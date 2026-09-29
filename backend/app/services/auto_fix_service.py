"""
Auto-fix service — powered by Groq.

Handles two cases:
  CORRECTION:  source_language == target_language  → fix issues, keep same language
  CONVERSION:  source_language != target_language  → convert code to target language AND fix issues

SAFETY: Code is NEVER executed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from groq import Groq, AuthenticationError, RateLimitError, APIError, APITimeoutError

logger = logging.getLogger(__name__)

DEFAULT_FIX_MODEL = "openai/gpt-oss-120b"
MAX_CODE_CHARS    = 14_000


# ── Dynamic system prompt ─────────────────────────────────────────────────────

def _build_system_prompt(source_lang: str, target_lang: str) -> str:
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

    return header + """
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
    return Groq(api_key=key)


# ── Main entry point ──────────────────────────────────────────────────────────

def auto_fix_code(
    code: str,
    language: str,              # TARGET language (user's selection)
    issues: list[dict],
    filename: str = "code.py",
    source_language: str = "",  # actual language of the submitted code (may differ)
    model: str = DEFAULT_FIX_MODEL,
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

    truncated       = len(code) > MAX_CODE_CHARS
    code_for_prompt = code[:MAX_CODE_CHARS] if truncated else code

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
{"[NOTE: truncated — fix/convert only the visible portion]" if truncated else ""}

Return the JSON object. corrected_code MUST be valid {target_lang}.
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _build_system_prompt(src_lang, target_lang)},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content or ""
        logger.info("[auto_fix] response  chars=%d", len(raw))
        result = _parse(raw, code)
        result.update(source_language=src_lang, target_language=target_lang, is_conversion=is_conv)
        return result

    except AuthenticationError:
        return _fallback(code, "Groq authentication failed.", src_lang, target_lang, is_conv)
    except RateLimitError:
        return _fallback(code, "Groq rate limit reached.", src_lang, target_lang, is_conv)
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
        logger.warning("[auto_fix] non-JSON response — using raw as code")
        code = _strip_fences(cleaned) or original
        return {"fixed_code": code, "changes": [], "summary": "Code corrected.", "ai_available": True, "error": None}

    code = _strip_fences(data.get("corrected_code", "").strip()) or original
    changes = [{"issue": str(c.get("issue", ""))[:300], "change": str(c.get("change", ""))[:500]}
               for c in data.get("changes", [])]
    return {"fixed_code": code, "changes": changes,
            "summary": str(data.get("summary", ""))[:1000], "ai_available": True, "error": None}


def _fallback(code: str, reason: str, src: str, tgt: str, is_conv: bool) -> dict[str, Any]:
    return {"fixed_code": code, "changes": [], "summary": f"Auto-fix unavailable: {reason}",
            "ai_available": False, "error": reason,
            "source_language": src, "target_language": tgt, "is_conversion": is_conv}
