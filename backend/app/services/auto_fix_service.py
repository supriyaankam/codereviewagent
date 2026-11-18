"""
Auto-fix service — powered by Groq.

Handles two cases:
  CORRECTION:  source_language == target_language  → fix issues, keep same language
  CONVERSION:  source_language != target_language  → convert code to target language AND fix issues

SAFETY: Code is NEVER executed.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from groq import Groq, AuthenticationError, RateLimitError, APIError, APITimeoutError
from app.analyzers.language_analyzer import extract_statement_region
from app.services.team_standard_analyzer import redact_credential_values

logger = logging.getLogger(__name__)

DEFAULT_FIX_MODEL = "openai/gpt-oss-120b"
MAX_CODE_CHARS    = 14_000
DEFAULT_FIX_CONTEXT_CHARS = 14_000
GROQ_CONTEXT_WINDOW_TOKENS = 131_072
MAX_FIX_COMPLETION_TOKENS = 4_096
FIX_CONTEXT_SAFETY_MARGIN_TOKENS = 8_192


def _fix_context_limit() -> int:
    value = os.getenv("AUTOFIX_MAX_CONTEXT_CHARS", str(DEFAULT_FIX_CONTEXT_CHARS))
    try:
        limit = int(value)
    except ValueError as exc:
        raise RuntimeError("AUTOFIX_MAX_CONTEXT_CHARS must be a positive integer.") from exc
    if limit < 1_000 or limit > MAX_CODE_CHARS:
        raise RuntimeError(
            f"AUTOFIX_MAX_CONTEXT_CHARS must be between 1000 and {MAX_CODE_CHARS}."
        )
    return limit


def _line_offsets(code: str) -> list[int]:
    offsets = [0]
    for line in code.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _node_span(node: ast.AST, offsets: list[int]) -> tuple[int, int, int, int]:
    start_line = int(getattr(node, "lineno", 0))
    end_line = int(getattr(node, "end_lineno", start_line))
    return offsets[start_line - 1], offsets[end_line], start_line, end_line


def _format_context(lines: list[str], start_line: int, end_line: int) -> str:
    return "\n".join(
        f"L{number}: {lines[number - 1].rstrip()}"
        for number in range(start_line, end_line + 1)
    )


def _extract_fix_region(
    code: str,
    language: str,
    issue: dict[str, Any],
    maximum_chars: int,
) -> dict[str, Any]:
    """Choose a complete, line-aligned replacement region and bounded read-only context."""
    lines = code.splitlines(keepends=True)
    if not lines:
        raise ValueError("The selected source file is empty.")

    line = issue.get("line")
    try:
        target_line = int(line)
    except (TypeError, ValueError):
        target_line = 0
    end_line_value = issue.get("end_line", target_line)
    try:
        target_end_line = int(end_line_value)
    except (TypeError, ValueError):
        target_end_line = target_line

    if not 1 <= target_line <= len(lines):
        if len(code) <= maximum_chars:
            return {
                "start": 0,
                "end": len(code),
                "start_line": 1,
                "end_line": len(lines),
                "original_region": code,
                "context": "",
            }
        raise ValueError(
            "This large-file finding has no usable source line. Reanalyze the file to refresh its location."
        )
    target_end_line = min(max(target_line, target_end_line), len(lines))

    offsets = _line_offsets(code)
    scope_candidates: list[tuple[int, int, int, int]] = []
    statement_candidates: list[tuple[int, int, int, int]] = []
    python_source = language.strip().lower() in {"python", "py"}
    tree = None
    if python_source:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            tree = None

    if tree is not None:
        containing_scopes = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and getattr(node, "lineno", len(lines) + 1) <= target_line
            and getattr(node, "end_lineno", 0) >= target_line
        ]
        if containing_scopes:
            scope = min(
                containing_scopes,
                key=lambda node: getattr(node, "end_lineno", 0) - getattr(node, "lineno", 0),
            )
            scope_candidates.append(_node_span(scope, offsets))
            statement_candidates.extend(
                _node_span(node, offsets)
                for node in ast.walk(scope)
                if isinstance(node, ast.stmt)
                and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and getattr(node, "lineno", len(lines) + 1) <= target_line
                and getattr(node, "end_lineno", 0) >= target_line
            )
        else:
            statement_candidates.extend(
                _node_span(node, offsets)
                for node in tree.body
                if isinstance(node, ast.stmt)
                and getattr(node, "lineno", len(lines) + 1) <= target_line
                and getattr(node, "end_lineno", 0) >= target_line
            )
    elif not python_source:
        parser_region = extract_statement_region(language, code, target_line)
        if parser_region is not None:
            statement_candidates.append(parser_region)

    if not scope_candidates and not statement_candidates:
        statement_candidates.append((
            offsets[target_line - 1],
            offsets[target_end_line],
            target_line,
            target_end_line,
        ))

    selected_scope = next(
        (span for span in scope_candidates if span[1] - span[0] <= maximum_chars),
        None,
    )
    statement_candidates.sort(key=lambda span: (span[1] - span[0], span[2]))
    selected = selected_scope or next(
        (span for span in statement_candidates if span[1] - span[0] <= maximum_chars),
        None,
    )
    if selected is None:
        raise ValueError(
            "The finding's complete code region exceeds the configured AI context limit; "
            "a smaller syntax-safe region could not be identified."
        )
    start, end, start_line, selected_end_line = selected
    original_region = code[start:end]

    if tree is not None:
        import_nodes = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and not (start_line <= node.lineno <= selected_end_line)
        ]
        import_context = "\n".join(
            f"L{node.lineno}: {code[span[0]:span[1]].rstrip()}"
            for node in import_nodes
            for span in [_node_span(node, offsets)]
        )
    else:
        import_context = ""

    is_import_finding = any(
        "import" in str(issue.get(key, "")).lower()
        for key in ("title", "description", "category", "rule")
    )
    if import_context and len(original_region) + len(import_context) <= maximum_chars:
        context_parts = [f"Read-only imports:\n{import_context}"]
    elif is_import_finding and len(import_context) + len(original_region) > maximum_chars:
        raise ValueError(
            "The finding depends on import context that exceeds the configured AI context limit."
        )
    else:
        context_parts = []

    if python_source and tree is not None and selected_end_line - start_line > 1:
        surrounding_ranges = [(start_line - 8, start_line - 1), (selected_end_line + 1, selected_end_line + 8)]
    else:
        surrounding_ranges = [(max(1, target_line - 12), min(len(lines), target_end_line + 12))]

    for first, last in surrounding_ranges:
        first = max(1, first)
        last = min(len(lines), last)
        if first > last:
            continue
        block = _format_context(lines, first, last)
        candidate_context = "\n".join((*context_parts, f"Read-only surrounding context:\n{block}"))
        if len(original_region) + len(candidate_context) <= maximum_chars:
            context_parts.append(f"Read-only surrounding context:\n{block}")

    context = "\n".join(context_parts)
    return {
        "start": start,
        "end": end,
        "start_line": start_line,
        "end_line": selected_end_line,
        "original_region": original_region,
        "context": context,
    }


def auto_fix_finding(
    code: str,
    language: str,
    issue: dict[str, Any],
    filename: str,
    finding_id: str,
) -> dict[str, Any]:
    """Generate a replacement for one safe region and map it back to the full source."""
    limit = _fix_context_limit()
    try:
        region = _extract_fix_region(code, language, issue, limit)
    except ValueError as exc:
        return _fallback(
            code,
            str(exc),
            language,
            language,
            False,
        )

    bounded_issue = {
        key: issue[key][:800] if isinstance(issue.get(key), str) else issue[key]
        for key in (
            "title",
            "description",
            "explanation",
            "recommendation",
            "severity",
            "category",
            "source",
            "rule",
            "rule_id",
            "line",
            "end_line",
            "column",
        )
        if key in issue
    }
    bounded_issue["finding_id"] = finding_id
    bounded_issue["file_path"] = filename
    bounded_issue["selected_region_lines"] = (
        f"{region['start_line']}-{region['end_line']}"
    )
    proposal = auto_fix_code(
        code=region["original_region"],
        language=language,
        issues=[bounded_issue],
        filename=filename,
        context=region["context"],
        targeted_region=True,
    )
    replacement = proposal["fixed_code"]
    if proposal.get("ai_available"):
        original_region = region["original_region"]
        newline = "\r\n" if "\r\n" in original_region else "\n"
        replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
        if newline != "\n":
            replacement = replacement.replace("\n", newline)
        if original_region.endswith(("\n", "\r")) and not replacement.endswith(("\n", "\r")):
            replacement += newline
        elif not original_region.endswith(("\n", "\r")):
            replacement = replacement.rstrip("\r\n")

    full_proposal = code[:region["start"]] + replacement + code[region["end"]:]
    proposal.update(
        fixed_code=full_proposal,
        original_region=region["original_region"],
        replacement_region=replacement,
        patch_start=region["start"],
        patch_end=region["end"],
        patch_start_line=region["start_line"],
        patch_end_line=region["end_line"],
        context=region["context"],
        finding_id=finding_id,
    )
    return proposal


# ── Dynamic system prompt ─────────────────────────────────────────────────────

def _build_system_prompt(source_lang: str, target_lang: str, targeted_region: bool = False) -> str:
    is_conversion = source_lang.lower() != target_lang.lower()

    if is_conversion:
        header = (
            f"You are a code CONVERSION and correction assistant.\n"
            f"The original code is written in {source_lang}.\n"
            f"The user's SELECTED TARGET LANGUAGE is {target_lang}.\n\n"
            f"YOUR PRIMARY TASK: Convert the code FROM {source_lang} TO {target_lang} "
            f"and fix all detected issues.\n\n"
            f"CRITICAL LANGUAGE RULES:\n"
            f"- The output MUST be valid, idiomatic {target_lang} code.\n"
            f"- Do NOT return {source_lang} code.\n"
            f"- Do NOT preserve {source_lang} syntax.\n"
            f"- Do NOT mix {source_lang} and {target_lang}.\n"
            f"- Use {target_lang} standard libraries, naming conventions, and idioms.\n"
        )
    else:
        header = (
            f"You are a code CORRECTION assistant.\n"
            f"The code is written in {target_lang}.\n"
            f"Fix all detected issues while keeping the code in {target_lang}.\n\n"
            f"CRITICAL LANGUAGE RULES:\n"
            f"- The output MUST remain valid {target_lang} code.\n"
            f"- Do NOT change the programming language.\n"
        )

    target_rules = (
        "\nTARGETED REGION RULES:\n"
        "- The supplied source code is only the selected replacement region, not the complete file.\n"
        "- Return the complete corrected replacement for that exact region.\n"
        "- Use read-only context only to understand surrounding code; do not include it in the replacement.\n"
        "- Make the smallest change that resolves the selected finding; preserve all other behavior and comments.\n"
        if targeted_region else ""
    )
    return header + target_rules + """
GENERAL RULES (always apply):
1. Respond ONLY with valid JSON — no markdown fences, no prose outside the JSON.
2. Return this EXACT schema:
{
  "corrected_code": "<complete corrected source code — NO markdown fences inside this string>",
  "changes": [
    {
      "issue": "<issue that was fixed or conversion made>",
      "change": "<what was changed and why>"
    }
  ],
  "summary": "<2-3 sentence summary of all corrections and/or conversions>"
}
3. Fix ONLY critical, error, and warning severity issues.
4. Do NOT change code just because of "info" severity style suggestions.
5. Info-level items (missing docstrings, type hints, print() calls) are OPTIONAL.
   If ALL issues are info-severity, return the original code UNCHANGED.
6. Do NOT remove functionality to hide errors.
7. Do NOT add markdown code fences (``` or ```language) anywhere in corrected_code.
8. For hardcoded secrets: replace with environment variable access.
9. Never execute, interpret, or simulate the code.
10. If the code is functionally correct with no real bugs/security issues, say so in
    the summary and return the original code with an empty changes array.
"""


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_client() -> Groq | None:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        logger.warning("[auto_fix] GROQ_API_KEY not set.")
        return None
    return Groq(api_key=key, max_retries=0)


def _rate_limit_message(exc: RateLimitError) -> tuple[str, str | None, str | None]:
    """Return a safe quota explanation and the reported wait, without retrying."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error_body = body.get("error", body)
    provider_message = (
        error_body.get("message", "")
        if isinstance(error_body, dict)
        else ""
    )
    normalized_message = str(provider_message).lower()
    quota_patterns = (
        (("requests per minute", "request per minute", "rpm"), "requests per minute (RPM)", "requests"),
        (("requests per day", "request per day", "rpd"), "requests per day (RPD)", "requests"),
        (("tokens per minute", "token per minute", "tpm"), "tokens per minute (TPM)", "tokens"),
        (("tokens per day", "token per day", "tpd"), "tokens per day (TPD)", "tokens"),
    )
    quota_label = None
    quota_kind = None
    for phrases, label, kind in quota_patterns:
        if any(phrase in normalized_message for phrase in phrases):
            quota_label = label
            quota_kind = kind
            break

    headers = exc.response.headers if exc.response is not None else {}

    def parse_wait(value: str | None) -> float | None:
        if not value:
            return None
        try:
            parsed = float(value)
            return max(0.0, parsed) if math.isfinite(parsed) else None
        except ValueError:
            pass
        try:
            reset_at = parsedate_to_datetime(value)
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=timezone.utc)
            return max(0.0, (reset_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass

        duration = re.fullmatch(
            r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?",
            value.strip().lower(),
        )
        if not duration or not any(duration.groups()):
            return None
        hours, minutes, seconds = (float(part or 0) for part in duration.groups())
        return hours * 3600 + minutes * 60 + seconds

    reset_headers = (
        [headers.get("x-ratelimit-reset-requests")]
        if quota_kind == "requests"
        else [headers.get("x-ratelimit-reset-tokens")]
        if quota_kind == "tokens"
        else [
            headers.get("x-ratelimit-reset-requests"),
            headers.get("x-ratelimit-reset-tokens"),
        ]
    )
    reset_headers.append(headers.get("x-ratelimit-reset"))
    waits = [
        wait for wait in (
            parse_wait(headers.get("retry-after")),
            *(parse_wait(value) for value in reset_headers),
        )
        if wait is not None
    ]
    wait_message = None
    if waits:
        wait_seconds = math.ceil(max(waits))
        wait_message = f"Retry in about {wait_seconds} second{'s' if wait_seconds != 1 else ''}."

    message = "Groq rate limit reached"
    if quota_label:
        message += f" ({quota_label})"
    if wait_message:
        message += f". {wait_message}"
    else:
        message += ". Groq did not provide a retry time; check your quota reset before trying again."
    return message + " No automatic retry was made.", wait_message, quota_label


# ── Main entry point ──────────────────────────────────────────────────────────

def auto_fix_code(
    code: str,
    language: str,              # TARGET language (user's selection)
    issues: list[dict],
    filename: str = "code.py",
    source_language: str = "",  # actual language of the submitted code (may differ)
    model: str = DEFAULT_FIX_MODEL,
    *,
    context: str = "",
    targeted_region: bool = False,
) -> dict[str, Any]:
    """
    language        = target language the user SELECTED
    source_language = language the submitted code is ACTUALLY written in
                      (leave empty when same as target)

    When they differ → conversion task (e.g. Python → C++)
    When same        → correction task (fix issues, same language)
    """
    target_lang = language.strip() or "Python"
    src_lang    = source_language.strip() or target_lang
    is_conv     = src_lang.lower() != target_lang.lower()

    logger.info("[auto_fix] source=%s  target=%s  conversion=%s  issues=%d",
                src_lang, target_lang, is_conv, len(issues))

    client = _get_client()
    if client is None:
        return _fallback(code, "GROQ_API_KEY is not configured.", src_lang, target_lang, is_conv)

    request_context_chars = len(code) + len(context)
    if len(code) > MAX_CODE_CHARS or request_context_chars > _fix_context_limit():
        return _fallback(
            code,
            "The selected source and context exceed the configured AI fix request limit. "
            "No partial correction was generated.",
            src_lang,
            target_lang,
            is_conv,
        )
    code_for_prompt = redact_credential_values(code)

    # Separate real issues from optional style tips
    real_issues = [i for i in issues if i.get("severity") not in ("info",)]
    info_issues = [i for i in issues if i.get("severity") == "info"]
    has_real_issues = len(real_issues) > 0

    if not issues:
        issues_text = (
            f"No specific issues listed. "
            + (f"Convert to {target_lang} and fix any obvious critical/error/warning problems." if is_conv
               else f"Fix any obvious bugs or security issues. Do NOT change working code.")
        )
    elif not has_real_issues:
        # Only info/style issues — code is functionally correct
        issues_text = (
            f"IMPORTANT: All {len(info_issues)} issue(s) listed are INFO severity (optional style tips only).\n"
            f"The code is functionally correct. Return the original code UNCHANGED.\n"
            f"Do NOT modify working code for style reasons.\n"
            f"Issues (for reference only, do NOT fix):\n{json.dumps(info_issues, indent=2)}"
        )
    else:
        issues_text = (
            f"Fix ONLY these {len(real_issues)} real issue(s) (critical/error/warning):\n"
            f"{json.dumps(real_issues, indent=2)}\n\n"
            f"NOTE: There are also {len(info_issues)} info-level style suggestion(s) — "
            f"ignore them unless they are directly related to a real bug."
        )

    task_line = (
        f"Convert this {src_lang} code to {target_lang} and fix real issues. "
        f"Output MUST be valid {target_lang}."
        if is_conv else
        (f"Fix the real bugs/security issues in this {target_lang} code. Output MUST remain valid {target_lang}."
         if has_real_issues else
         f"The {target_lang} code below is functionally correct. Return it unchanged.")
    )

    user_prompt = f"""\
SOURCE LANGUAGE: {src_lang}
TARGET LANGUAGE: {target_lang}
FILE: {filename}

TASK: {task_line}

ISSUES TO FIX:
{issues_text}

SOURCE CODE:
{code_for_prompt}

READ-ONLY CONTEXT:
{context or "(none)"}

Return the JSON object. corrected_code MUST be valid {target_lang}.
"""

    try:
        system_prompt = _build_system_prompt(src_lang, target_lang, targeted_region)
        prompt_bytes = len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))
        prompt_budget = (
            GROQ_CONTEXT_WINDOW_TOKENS
            - MAX_FIX_COMPLETION_TOKENS
            - FIX_CONTEXT_SAFETY_MARGIN_TOKENS
        )
        if prompt_bytes > prompt_budget:
            return _fallback(
                code,
                "The fix prompt plus reserved completion tokens exceeds the model's safe context budget.",
                src_lang,
                target_lang,
                is_conv,
            )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=MAX_FIX_COMPLETION_TOKENS,
        )
        raw = resp.choices[0].message.content or ""
        logger.info("[auto_fix] response  chars=%d", len(raw))
        result = _parse(raw, code)
        result.update(source_language=src_lang, target_language=target_lang, is_conversion=is_conv)
        return result

    except AuthenticationError:
        return _fallback(code, "Groq authentication failed.", src_lang, target_lang, is_conv)
    except RateLimitError as exc:
        message, wait_message, quota_label = _rate_limit_message(exc)
        logger.warning(
            "[auto_fix] Groq rate limit  status=%s quota=%s retry_guidance=%s",
            exc.status_code,
            quota_label or "unspecified",
            wait_message or "unavailable",
        )
        return _fallback(code, message, src_lang, target_lang, is_conv)
    except APITimeoutError:
        return _fallback(code, "Groq request timed out.", src_lang, target_lang, is_conv)
    except APIError as exc:
        return _fallback(code, f"Groq API error: {exc}", src_lang, target_lang, is_conv)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[auto_fix] unexpected: %s", exc)
        return _fallback(code, "Unexpected error during auto-fix.", src_lang, target_lang, is_conv)


def auto_fix_multiple(files: list[dict], model: str = DEFAULT_FIX_MODEL) -> list[dict[str, Any]]:
    results = []
    for f in files:
        r = auto_fix_code(
            code=f["content"], language=f["language"], issues=f.get("issues", []),
            filename=f["path"], source_language=f.get("source_language", ""), model=model,
        )
        results.append({"path": f["path"], "language": f["language"],
                         "original_code": f["content"], "issues": f.get("issues", []), **r})
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
    if t.endswith("```"):
        t = "\n".join(t.split("\n")[:-1])
    return t.strip()


def _parse(raw: str, original: str) -> dict[str, Any]:
    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("[auto_fix] response was not valid JSON; retaining original code")
        return {
            "fixed_code": original,
            "changes": [],
            "summary": "No correction was applied because the AI response could not be validated.",
            "ai_available": False,
            "error": "The AI returned an invalid response. The original code was preserved.",
        }

    if not isinstance(data, dict) or not isinstance(data.get("corrected_code"), str):
        logger.warning("[auto_fix] response schema invalid; retaining original code")
        return {
            "fixed_code": original,
            "changes": [],
            "summary": "No correction was applied because the AI response had an invalid format.",
            "ai_available": False,
            "error": "The AI response did not contain corrected_code. The original code was preserved.",
        }

    code = data["corrected_code"]
    if code.strip().startswith("```") and code.strip().endswith("```"):
        code = "\n".join(code.strip().splitlines()[1:-1])
    if not code.strip():
        return {
            "fixed_code": original,
            "changes": [],
            "summary": "No correction was applied because the proposed code was empty.",
            "ai_available": False,
            "error": "The AI returned empty corrected code. The original code was preserved.",
        }
    changes = [{"issue": str(c.get("issue", ""))[:300], "change": str(c.get("change", ""))[:500]}
               for c in data.get("changes", []) if isinstance(c, dict)]
    return {"fixed_code": code, "changes": changes,
            "summary": str(data.get("summary", ""))[:1000], "ai_available": True, "error": None}


def _fallback(code: str, reason: str, src: str, tgt: str, is_conv: bool) -> dict[str, Any]:
    return {"fixed_code": code, "changes": [], "summary": f"Auto-fix unavailable: {reason}",
            "ai_available": False, "error": reason,
            "source_language": src, "target_language": tgt, "is_conversion": is_conv}
