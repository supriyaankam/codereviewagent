"""
AI reviewer service — powered by Groq.

Sends static analysis results + source code to the Groq API and parses
a structured code review.

Security:
  - API key is read from GROQ_API_KEY environment variable only.
  - The key is NEVER logged or included in responses.
  - Submitted code is NEVER executed — only embedded in a prompt string.

Fallback:
  - If Groq is unavailable for any reason (no key, auth error, timeout,
    rate limit, bad response), static findings remain separate from AI findings.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from groq import Groq, AuthenticationError, RateLimitError, APIError, APITimeoutError
from app.analyzers.language_analyzer import source_identifiers
from app.services.team_standard_analyzer import redact_credential_values

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

RISK_LEVELS     = ("critical", "high", "medium", "low", "info")
DEFAULT_MODEL   = "openai/gpt-oss-120b"   # most capable model available on this Groq account
MAX_TOKENS      = 4096
TEMPERATURE     = 0.2
MAX_CODE_CHARS  = 12_000
MAX_RESPONSE_ATTEMPTS = 2
_ALLOWED_CATEGORIES = (
    "security", "quality", "naming", "unused", "complexity", "syntax", "style",
)
_ALLOWED_SOURCES = (
    "ast", "pylint", "bandit", "secrets", "syntax", "tree_sitter", "team_standard", "ai",
)
_ALLOWED_CONFIDENCE = ("high", "medium", "low")
_NON_SYMBOL_REFERENCES = {
    "a", "an", "and", "are", "as", "be", "by", "can", "class", "code", "does",
    "for", "from", "function", "if", "in", "input", "is", "it", "line", "may",
    "method", "must", "name", "not", "of", "on", "or", "output", "result",
    "return", "should", "that", "the", "this", "to", "use", "value", "variable",
    "when", "while", "will", "with", "you",
}


class _InvalidAIResponse(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert code reviewer embedded in a developer tool called Code Review Agent.
Your role is to analyse submitted source code and return a structured JSON review.

RULES:
1. Never execute, interpret, or simulate the submitted code.
2. Respond ONLY with valid JSON — no markdown fences, no prose outside the JSON object.
3. Be concise, precise, and actionable in every field.
4. Do not invent issues that are not supported by the code you can see.
   When naming a function, variable, class, or property in an issue title or explanation,
   quote its exact identifier in backticks.
5. Each issue must have a unique, specific title.
6. CRITICAL: If the STATIC ANALYSIS RESULTS section contains issues, you MUST include ALL
   of them in your response. You may add your own additional issues, but you must NOT omit,
   ignore, or override any confirmed static-analysis finding. Preserve original line numbers.
7. CRITICAL: If static analysis found issues, overall_risk must be at minimum "medium".
   Do NOT return overall_risk="info" when static issues exist.
8. For syntax errors: explain them in plain English and show the exact line and column.
9. The "source" field must reflect where the issue originated:
   ast | pylint | bandit | secrets | syntax | tree_sitter | team_standard | ai

SEVERITY GUIDELINES — use these carefully:
- "critical": security vulnerabilities, syntax errors, code that will crash at runtime
- "error":    logic bugs, incorrect behavior, broken functionality
- "warning":  potential runtime problems, bad practices that could cause bugs
- "info":     optional style suggestions only — missing docstrings, type hints, naming style

CONFIDENCE:
- "high": directly supported by the code or a reproducible static-analysis finding
- "medium": plausible concern that depends on missing context or assumptions
- "low": speculative; only include when clearly labeled as uncertain
Never present a medium/low-confidence AI-only suggestion as a confirmed bug.
Static-analysis findings must keep their original source and use high confidence.

CLEAN CODE RULE:
If the code is functionally correct, has no security issues, no bugs, and no crashes:
- Set overall_risk to "info"
- You MAY include "info" severity style suggestions but keep them minimal (max 2)
- Do NOT manufacture warnings or errors just to have something to report
- It is perfectly acceptable to return 0 issues if the code is genuinely clean
- A print() call that is the intended output of a script is NOT a debugging issue
- Missing docstrings and type hints are optional style suggestions only — never warnings

OUTPUT SCHEMA (strict — no extra keys, no markdown):
{
  "overall_risk": "critical|high|medium|low|info",
  "summary": "<2-4 sentences covering ALL issues found>",
  "issues": [
    {
      "severity":       "critical|error|warning|info",
      "category":       "security|quality|naming|unused|complexity|syntax|style",
      "title":          "<short descriptive title>",
      "explanation":    "<plain-English explanation of why this is a problem>",
      "recommendation": "<concrete fix, ideally with a short code example>",
      "line":           <integer, 0 if not line-specific>,
      "source":         "<ast|pylint|bandit|secrets|syntax|ai>",
      "confidence":     "high|medium|low",
      "beginner_explanation": {
        "problem": "<what is happening in simple English>",
        "cause": "<why it happens>",
        "impact": "<why the developer should care>",
        "fix": "<how to fix it in simple steps>",
        "example": "<short example when useful, otherwise empty>",
        "concept": "<relevant programming concept, otherwise empty>"
      }
    }
  ]
}
"""

# ── Groq client factory ───────────────────────────────────────────────────────

def _get_client() -> Groq | None:
    """Return a Groq client or None if the key is not configured."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        logger.warning("[groq] GROQ_API_KEY is not set — AI review will be skipped.")
        return None
    # Never log the actual key value
    logger.info("[groq] API key configured")
    return Groq(api_key=api_key)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(
    code: str,
    language: str,
    project_name: str,
    static_issues: list[dict],
    team_standards: list[dict] | None = None,
    memory_experiences: list[dict] | None = None,
) -> str:

    static_summary = (
        json.dumps(static_issues, indent=2) if static_issues
        else "No static analysis issues detected."
    )

    # Enforcement note so the model cannot ignore static findings
    if static_issues:
        enforcement = (
            f"\n⚠️  MANDATORY: Static analysis found {len(static_issues)} issue(s). "
            "You MUST include ALL of them in your response. "
            "Do NOT claim the code is clean.\n"
        )
    else:
        enforcement = ""

    # Team standards block
    if team_standards:
        lines = [
            f"  {i}. [{s.get('category','general').upper()}] "
            f"{s.get('title','')}: {s.get('description','')}"
            for i, s in enumerate(team_standards, 1)
        ]
        standards_block = (
            "=== TEAM STANDARDS ===\n"
            "Violations must be tagged \"[TEAM STANDARD]\" in the title.\n"
            + "\n".join(lines) + "\n"
        )
    else:
        standards_block = "=== TEAM STANDARDS ===\nNone defined.\n"

    # Memory / Hindsight block
    if memory_experiences:
        mem_lines = []
        for i, exp in enumerate(memory_experiences, 1):
            decision  = exp.get("developer_decision", "pending")
            title     = exp.get("issue_title", "")
            cat       = exp.get("issue_category", "")
            reason    = exp.get("developer_reason", "")
            pref      = exp.get("team_preference", "")
            entry     = f"  {i}. [{cat.upper()}] \"{title}\" — decision: {decision}"
            if reason: entry += f" (reason: {reason})"
            if decision == "accepted" and pref: entry += f" | accepted fix: {pref}"
            mem_lines.append(entry)
        memory_block = (
            "=== TEAM MEMORY (context only — do NOT copy blindly) ===\n"
            + "\n".join(mem_lines) + "\n"
        )
    else:
        memory_block = "=== TEAM MEMORY ===\nNo previous experiences found.\n"

    # Truncate large code
    code_snippet = (
        code if len(code) <= MAX_CODE_CHARS
        else code[:MAX_CODE_CHARS] + f"\n\n... [truncated — {len(code)-MAX_CODE_CHARS} chars omitted]"
    )

    return f"""\
PROJECT: {project_name}
LANGUAGE: {language}

{memory_block}
{standards_block}
=== STATIC ANALYSIS RESULTS ==={enforcement}
{static_summary}

=== SOURCE CODE ===
{code_snippet}

Instructions:
1. Analyse the code independently on its own merits.
2. Check against team standards — label violations "[TEAM STANDARD]".
3. You MUST include ALL static analysis issues listed above.
4. Add any additional issues the static tools may have missed.
Return only the JSON object described in the system prompt.
"""


# ── Main entry point ──────────────────────────────────────────────────────────

def run_ai_review(
    code: str,
    language: str,
    project_name: str,
    static_issues: list[dict],
    team_standards: list[dict] | None = None,
    memory_experiences: list[dict] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """
    Call the Groq API and return a structured review dict.

    Always returns a safe dict — never raises.
    Returns static findings separately when AI analysis fails.
    """
    logger.info("[groq] review started  language=%s  static_issues=%d  code_chars=%d",
                language, len(static_issues), len(code))
    # Never send literal credential values to the AI provider.
    code = redact_credential_values(code)

    client = _get_client()
    if client is None:
        return _fallback(
            "GROQ_API_KEY is not configured.",
            static_issues,
            "not_configured",
        )

    prompt = _build_user_prompt(
        code, language, project_name, static_issues,
        team_standards, memory_experiences,
    )
    logger.info("[groq] sending request  model=%s  prompt_chars=%d", model, len(prompt))

    try:
        retry_prompt = prompt
        for attempt in range(1, MAX_RESPONSE_ATTEMPTS + 1):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": retry_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )
                choice = response.choices[0]
                message = choice.message
            except (IndexError, AttributeError, TypeError) as exc:
                logger.warning(
                    "[groq] invalid response  model=%s  reason=response_structure  "
                    "content_type=%s  attempt=%d",
                    model,
                    type(exc).__name__,
                    attempt,
                )
                if attempt < MAX_RESPONSE_ATTEMPTS:
                    retry_prompt = f"{prompt}\n\nReturn one complete JSON object matching the required schema."
                    continue
                return _fallback(
                    "Groq returned an unexpected response structure.",
                    static_issues,
                    "response_structure",
                )
            response_model = getattr(response, "model", None) or model
            finish_reason = getattr(choice, "finish_reason", None) or "unknown"
            response_content = getattr(message, "content", None)
            content_type = type(response_content).__name__
            raw = response_content if isinstance(response_content, str) else ""
            logger.info(
                "[groq] response received  model=%s  response_model=%s  "
                "finish_reason=%s  content_type=%s  chars=%d  attempt=%d",
                model,
                response_model,
                finish_reason,
                content_type,
                len(raw),
                attempt,
            )
            try:
                if not isinstance(response_content, str):
                    raise _InvalidAIResponse("content_type")
                if finish_reason == "length":
                    raise _InvalidAIResponse("truncated")
                result = _parse_response(
                    raw,
                    static_issues,
                    code[:MAX_CODE_CHARS],
                    language,
                )
            except _InvalidAIResponse as exc:
                logger.warning(
                    "[groq] invalid response  reason=%s  chars=%d  attempt=%d",
                    exc.reason,
                    len(raw),
                    attempt,
                )
                if attempt < MAX_RESPONSE_ATTEMPTS:
                    retry_guidance = (
                        "The previous response was truncated. Keep the summary under 2 sentences, "
                        "make each issue concise, and omit optional beginner_explanation fields."
                        if exc.reason == "truncated"
                        else "Your previous response did not conform to the required JSON schema. "
                        "Return one complete JSON object only. Ensure `overall_risk` and `summary` "
                        "are strings and `issues` is an array of fully valid issue objects."
                    )
                    retry_prompt = f"{prompt}\n\n{retry_guidance}"
                    continue
                return _fallback(
                    "Groq returned an empty response." if exc.reason == "empty"
                    else "Groq returned an invalid response after retry.",
                    static_issues,
                    f"response_{exc.reason}",
                )
            logger.info("[groq] review complete  issues=%d  overall_risk=%s  ai_available=%s",
                        len(result.get("issues", [])), result.get("overall_risk"), result.get("ai_available"))
            return result
        return _fallback(
            "Groq returned an invalid response after retry.",
            static_issues,
            "response_invalid",
        )

    except AuthenticationError:
        logger.error("[groq] authentication failed — GROQ_API_KEY is invalid")
        return _fallback(
            "Groq authentication failed. Check your GROQ_API_KEY.",
            static_issues,
            "authentication",
        )
    except RateLimitError:
        logger.error("[groq] rate limit exceeded")
        return _fallback(
            "Groq rate limit reached. Please try again in a moment.",
            static_issues,
            "rate_limited",
        )
    except APITimeoutError:
        logger.error("[groq] request timed out")
        return _fallback("Groq request timed out. Please try again.", static_issues, "timeout")
    except APIError as exc:
        logger.error("[groq] API request failed  error_type=%s", type(exc).__name__)
        return _fallback("Groq API request failed.", static_issues, "api_error")
    except Exception as exc:  # noqa: BLE001
        logger.error("[groq] unexpected review error  error_type=%s", type(exc).__name__)
        return _fallback(
            "An unexpected error occurred during AI analysis.",
            static_issues,
            "unexpected_error",
        )


# ── Connection test ───────────────────────────────────────────────────────────

def test_groq_connection() -> dict[str, Any]:
    """
    Send a minimal request to Groq to verify the API key and connection.
    Returns a status dict — never raises.
    Does NOT log or return the API key value.
    """
    client = _get_client()
    if client is None:
        return {"status": "error", "message": "GROQ_API_KEY is not set."}

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: {\"ok\": true}"}],
            temperature=0,
            max_tokens=20,
        )
        content = response.choices[0].message.content or ""
        logger.info("[groq] connection test succeeded  response_chars=%d", len(content))
        return {
            "status":  "ok",
            "message": "Groq API connection successful.",
            "model":   DEFAULT_MODEL,
        }
    except AuthenticationError:
        return {"status": "error", "message": "Invalid GROQ_API_KEY."}
    except APITimeoutError:
        return {"status": "error", "message": "Groq API timed out."}
    except Exception as exc:  # noqa: BLE001
        logger.error("[groq] connection test failed  error_type=%s", type(exc).__name__)
        return {"status": "error", "message": "Groq API connection failed."}


# ── Response parser ───────────────────────────────────────────────────────────

def _decode_json_object(raw: str) -> dict[str, Any]:
    if not raw.strip():
        raise _InvalidAIResponse("empty")

    decoder = json.JSONDecoder()
    candidates = [raw.strip()]
    lines = raw.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        if lines[-1].strip().startswith("```") and len(lines) > 2:
            candidates.insert(0, "\n".join(lines[1:-1]).strip())

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    # Accept a JSON object surrounded by explanatory text, but never parse fields
    # from that surrounding text as findings.
    for start, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise _InvalidAIResponse("json")


def _required_string(record: dict, field: str, max_length: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidAIResponse("schema")
    return value.strip()[:max_length]


def _parse_response(
    raw: str,
    static_issues: list[dict],
    code: str,
    language: str,
) -> dict[str, Any]:
    """Parse and strictly validate one complete Groq review object."""
    data = _decode_json_object(raw)
    risk = data.get("overall_risk")
    summary = data.get("summary")
    raw_issues = data.get("issues")
    if risk not in RISK_LEVELS or not isinstance(summary, str) or not summary.strip():
        raise _InvalidAIResponse("schema")
    if not isinstance(raw_issues, list):
        raise _InvalidAIResponse("schema")

    issues = []
    for item in raw_issues:
        if not isinstance(item, dict):
            raise _InvalidAIResponse("schema")
        severity = item.get("severity")
        category = item.get("category")
        line = item.get("line")
        if severity not in ("critical", "error", "warning", "info"):
            raise _InvalidAIResponse("schema")
        if category not in _ALLOWED_CATEGORIES:
            raise _InvalidAIResponse("schema")
        if isinstance(line, bool) or not isinstance(line, int) or line < 0:
            raise _InvalidAIResponse("schema")
        source = item.get("source", "ai")
        confidence = item.get("confidence", "medium")
        beginner = item.get("beginner_explanation", {})
        if source not in _ALLOWED_SOURCES or confidence not in _ALLOWED_CONFIDENCE:
            raise _InvalidAIResponse("schema")
        if not isinstance(beginner, dict):
            raise _InvalidAIResponse("schema")
        if any(
            key in beginner and not isinstance(beginner[key], str)
            for key in ("problem", "cause", "impact", "fix", "example", "concept")
        ):
            raise _InvalidAIResponse("schema")

        issues.append({
            "severity": severity,
            "category": category,
            "title": _required_string(item, "title", 200),
            "explanation": _required_string(item, "explanation", 1000),
            "recommendation": _required_string(item, "recommendation", 1000),
            "line": line,
            "source": source,
            "confidence": confidence,
            "beginner_explanation": {
                key: beginner.get(key, "")[:500]
                for key in ("problem", "cause", "impact", "fix", "example", "concept")
            },
        })

    static_keys = {_finding_key(issue) for issue in static_issues}
    identifiers = source_identifiers(language, code)
    if identifiers is not None and any(
        _finding_key(issue) not in static_keys
        and not _issue_references_source(issue, code, identifiers)
        for issue in issues
    ):
        raise _InvalidAIResponse("ungrounded")

    ai_issues = []
    for issue in issues:
        if _finding_key(issue) in static_keys:
            continue
        ai_issues.append({**issue, "source": "ai"})

    # ── Enforce correct overall_risk based on actual issue severities ─────
    # The AI sometimes returns "medium" even when all issues are "info".
    # Recalculate from the actual issue list to guarantee accuracy.
    ai_risk = risk
    actual_sevs = [
        str(issue.get("severity", "")).lower()
        for issue in [*static_issues, *ai_issues]
    ]
    if "critical" in actual_sevs:
        computed_risk = "critical"
    elif "error" in actual_sevs:
        computed_risk = "high"
    elif "warning" in actual_sevs:
        computed_risk = "medium"
    elif any(s in ("low", "info") for s in actual_sevs):
        computed_risk = "info"
    else:
        computed_risk = "info"   # no issues at all

    final_risk = computed_risk

    logger.info(
        "[groq] risk correction: ai_said=%s computed=%s final=%s ai_issues=%d static_issues=%d",
        ai_risk,
        computed_risk,
        final_risk,
        len(ai_issues),
        len(static_issues),
    )

    return {
        "overall_risk": final_risk,
        "summary":      summary.strip()[:2000],
        "issues":       ai_issues,
        "static_issues": static_issues,
        "ai_available": True,
        "fallback_reason": None,
        "failure_category": None,
    }


def _issue_references_source(
    issue: dict[str, Any],
    code: str,
    identifiers: set[str],
) -> bool:
    lines = code.splitlines()
    line = issue["line"]
    if line < 0 or line > len(lines):
        return False
    if line > 0 and not lines[line - 1].strip():
        return False

    identifier_pattern = re.compile(r"`([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\(\s*\))?`")
    call_pattern = re.compile(r"(?<![\w$])([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
    styled_identifier_pattern = re.compile(
        r"(?<![\w$])([A-Za-z_$][A-Za-z0-9_$]*_[A-Za-z0-9_$]+|"
        r"[a-z][A-Z][A-Za-z0-9_$]*)(?![\w$])"
    )
    for field in ("title", "explanation"):
        references = set(identifier_pattern.findall(issue[field]))
        references.update(call_pattern.findall(issue[field]))
        references.update(styled_identifier_pattern.findall(issue[field]))
        for reference in references:
            if (
                reference.lower() not in _NON_SYMBOL_REFERENCES
                and reference not in identifiers
            ):
                return False
    return True


def _finding_key(issue: dict[str, Any]) -> tuple[str, int]:
    title = str(issue.get("title") or "").strip().lower()
    line = issue.get("line", issue.get("line_number", 0))
    try:
        line_number = int(line or 0)
    except (TypeError, ValueError):
        line_number = 0
    return title, line_number


def _static_risk(static_issues: list[dict]) -> str | None:
    severities = {str(issue.get("severity") or "").lower() for issue in static_issues}
    if "critical" in severities:
        return "critical"
    if "error" in severities:
        return "high"
    if "warning" in severities:
        return "medium"
    if severities:
        return "info"
    return None


def _fallback(
    reason: str,
    static_issues: list[dict],
    failure_category: str = "unavailable",
) -> dict[str, Any]:
    """
    Graceful degraded review with static findings kept out of the AI issue list.
    """
    risk = _static_risk(static_issues)

    summary = (
        f"AI review unavailable — {reason} "
        f"Showing {len(static_issues)} static analysis result(s). Highest severity: {risk}."
        if static_issues else
        f"AI review unavailable — {reason} Static analysis found no issues."
    )

    return {
        "overall_risk":    risk,
        "summary":         summary,
        "issues":          [],
        "static_issues":   static_issues,
        "ai_available":    False,
        "fallback_reason": reason,
        "failure_category": failure_category,
    }
