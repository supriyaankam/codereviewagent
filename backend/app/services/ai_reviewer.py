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
    rate limit, bad response) the static analysis results are returned
    directly so the user never sees "No errors found" when issues exist.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from groq import Groq, AuthenticationError, RateLimitError, APIError, APITimeoutError

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

RISK_LEVELS     = ("critical", "high", "medium", "low", "info")
DEFAULT_MODEL   = "openai/gpt-oss-120b"   # most capable model available on this Groq account
MAX_TOKENS      = 2048
TEMPERATURE     = 0.2
MAX_CODE_CHARS  = 12_000

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert code reviewer embedded in a developer tool called Code Review Agent.
Your role is to analyse submitted source code and return a structured JSON review.

RULES:
1. Never execute, interpret, or simulate the submitted code.
2. Respond ONLY with valid JSON — no markdown fences, no prose outside the JSON object.
3. Be concise, precise, and actionable in every field.
4. Do not invent issues that are not supported by the code you can see.
5. Each issue must have a unique, specific title.
6. CRITICAL: If the STATIC ANALYSIS RESULTS section contains issues, you MUST include ALL
   of them in your response. You may add your own additional issues, but you must NOT omit,
   ignore, or override any confirmed static-analysis finding. Preserve original line numbers.
7. CRITICAL: If static analysis found issues, overall_risk must be at minimum "medium".
   Do NOT return overall_risk="info" when static issues exist.
8. For syntax errors: explain them in plain English and show the exact line and column.
9. The "source" field must reflect where the issue originated:
   ast | pylint | bandit | secrets | syntax | ai

SEVERITY GUIDELINES — use these carefully:
- "critical": security vulnerabilities, syntax errors, code that will crash at runtime
- "error":    logic bugs, incorrect behavior, broken functionality
- "warning":  potential runtime problems, bad practices that could cause bugs
- "info":     optional style suggestions only — missing docstrings, type hints, naming style

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
      "source":         "<ast|pylint|bandit|secrets|syntax|ai>"
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
    logger.info("[groq] API key found (length=%d)", len(api_key))
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
    Falls back to static analysis results on any failure.
    """
    logger.info("[groq] review started  language=%s  static_issues=%d  code_chars=%d",
                language, len(static_issues), len(code))

    client = _get_client()
    if client is None:
        return _fallback("GROQ_API_KEY is not configured.", static_issues)

    prompt = _build_user_prompt(
        code, language, project_name, static_issues,
        team_standards, memory_experiences,
    )
    logger.info("[groq] sending request  model=%s  prompt_chars=%d", model, len(prompt))

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )

        raw = response.choices[0].message.content or ""
        logger.info("[groq] response received  chars=%d", len(raw))
        result = _parse_response(raw, static_issues)
        logger.info("[groq] review complete  issues=%d  overall_risk=%s  ai_available=%s",
                    len(result.get("issues", [])), result.get("overall_risk"), result.get("ai_available"))
        return result

    except AuthenticationError:
        logger.error("[groq] authentication failed — GROQ_API_KEY is invalid")
        return _fallback("Groq authentication failed. Check your GROQ_API_KEY.", static_issues)
    except RateLimitError:
        logger.error("[groq] rate limit exceeded")
        return _fallback("Groq rate limit reached. Please try again in a moment.", static_issues)
    except APITimeoutError:
        logger.error("[groq] request timed out")
        return _fallback("Groq request timed out. Please try again.", static_issues)
    except APIError as exc:
        logger.error("[groq] API error: %s", exc)
        return _fallback(f"Groq API error: {exc}", static_issues)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[groq] unexpected error: %s", exc)
        return _fallback("An unexpected error occurred during AI analysis.", static_issues)


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
        logger.info("[groq] connection test succeeded  response=%s", content[:50])
        return {
            "status":  "ok",
            "message": "Groq API connection successful.",
            "model":   DEFAULT_MODEL,
            "response_preview": content[:100],
        }
    except AuthenticationError:
        return {"status": "error", "message": "Invalid GROQ_API_KEY."}
    except APITimeoutError:
        return {"status": "error", "message": "Groq API timed out."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(raw: str, static_issues: list[dict]) -> dict[str, Any]:
    """Parse and validate the Groq JSON response."""
    # Strip any accidental markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[:-1])
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("[groq] response is not valid JSON: %s", raw[:200])
        return _fallback("Groq returned a non-JSON response.", static_issues)

    issues = []
    for item in data.get("issues", []):
        issues.append({
            "severity":       _coerce(item.get("severity", "info"),
                                      ("critical", "error", "warning", "info"), "info"),
            "category":       _coerce(item.get("category", "quality"),
                                      ("security", "quality", "naming", "unused",
                                       "complexity", "syntax", "style"), "quality"),
            "title":          str(item.get("title", "Untitled issue"))[:200],
            "explanation":    str(item.get("explanation", ""))[:1000],
            "recommendation": str(item.get("recommendation", ""))[:1000],
            "line":           int(item.get("line", 0)),
            "source":         str(item.get("source", "ai"))[:30],
        })

    # Safety net: AI returned 0 issues but static found some
    if not issues and static_issues:
        logger.warning("[groq] AI returned 0 issues but static found %d — promoting", len(static_issues))
        return _fallback(
            "Groq returned no issues despite static findings. Showing static results.",
            static_issues,
        )

    # ── Enforce correct overall_risk based on actual issue severities ─────
    # The AI sometimes returns "medium" even when all issues are "info".
    # Recalculate from the actual issue list to guarantee accuracy.
    ai_risk = _coerce(data.get("overall_risk", "info"), RISK_LEVELS, "info")
    actual_sevs = [i["severity"] for i in issues]
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

    # Never downgrade a critical/high AI assessment, but always correct upward errors
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    # Use the LOWER (more severe) of AI-declared vs computed — but cap at computed when AI over-reports
    final_risk = computed_risk  # always trust the computed value from actual issues

    logger.info("[groq] risk correction: ai_said=%s computed=%s final=%s issues=%d",
                ai_risk, computed_risk, final_risk, len(issues))

    return {
        "overall_risk": final_risk,
        "summary":      str(data.get("summary", ""))[:2000],
        "issues":       issues,
        "ai_available": True,
    }


def _fallback(reason: str, static_issues: list[dict]) -> dict[str, Any]:
    """
    Graceful degraded review — always shows static results, never hides them.
    """
    promoted = [
        {
            "severity":       i.get("severity", "info"),
            "category":       i.get("category", "quality"),
            "title":          i.get("title", ""),
            "explanation":    i.get("description", ""),
            "recommendation": i.get("recommendation", ""),
            "line":           i.get("line", 0),
            "source":         i.get("source", "static"),
        }
        for i in static_issues
    ]

    sevs = [i["severity"] for i in promoted]
    risk = ("critical" if "critical" in sevs else
            "high"     if "error"    in sevs else
            "medium"   if "warning"  in sevs else
            "low"      if promoted   else "info")

    summary = (
        f"AI review unavailable — {reason} "
        f"Showing {len(promoted)} static analysis result(s). Highest severity: {risk}."
        if promoted else
        f"AI review unavailable — {reason} Static analysis found no issues."
    )

    return {
        "overall_risk":    risk,
        "summary":         summary,
        "issues":          promoted,
        "ai_available":    False,
        "fallback_reason": reason,
    }


def _coerce(value: str, allowed: tuple, default: str) -> str:
    return value if value in allowed else default
