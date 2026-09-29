"""
Code analyzer orchestrator.

Runs all analysis passes in sequence and returns a unified, de-duplicated
result set.  Submitted code is NEVER executed.
"""

from __future__ import annotations

import logging

from app.analyzers.base import Issue, Severity, Category
from app.analyzers.python_analyzer import (
    check_syntax,
    run_ast_analysis,
    run_pylint_analysis,
)
from app.analyzers.security_analyzer import (
    run_bandit_analysis,
    run_secret_detection,
)

logger = logging.getLogger(__name__)

# ── Severity ordering for sorting ────────────────────────────────────────────

_SEVERITY_ORDER: dict[str, int] = {
    Severity.critical: 0,
    Severity.error:    1,
    Severity.warning:  2,
    Severity.info:     3,
}


def _build_summary(issues: list[Issue]) -> dict:
    summary: dict[str, int] = {
        "total":    len(issues),
        "critical": 0,
        "error":    0,
        "warning":  0,
        "info":     0,
    }
    by_category: dict[str, int] = {}
    by_source:   dict[str, int] = {}

    for issue in issues:
        summary[issue.severity.value] = summary.get(issue.severity.value, 0) + 1
        by_category[issue.category.value] = by_category.get(issue.category.value, 0) + 1
        by_source[issue.source]           = by_source.get(issue.source, 0) + 1

    return {"counts": summary, "by_category": by_category, "by_source": by_source}


def _deduplicate(issues: list[Issue]) -> list[Issue]:
    """
    Remove duplicate issues that share the same (title, line) pair.
    Keeps the highest-severity copy.
    """
    seen: dict[tuple, Issue] = {}
    for issue in issues:
        key = (issue.title, issue.line)
        if key not in seen:
            seen[key] = issue
        else:
            existing = seen[key]
            if _SEVERITY_ORDER.get(issue.severity, 99) < _SEVERITY_ORDER.get(existing.severity, 99):
                seen[key] = issue
    return list(seen.values())


def analyze_code(language: str, code: str) -> dict:
    """
    Run all applicable analysis passes for the given language.
    Code is NEVER executed — only parsed and inspected statically.

    Returns:
        {
            "language":  str,
            "supported": bool,
            "has_syntax_error": bool,
            "issues": [
                {
                    "category", "severity", "title", "description",
                    "recommendation", "line", "column", "source"
                }
            ],
            "summary": {
                "counts":      {total, critical, error, warning, info},
                "by_category": {...},
                "by_source":   {...},
            }
        }
    """
    lang = language.strip().lower()

    logger.info("[analyzer] language=%s  code_length=%d chars", lang, len(code))

    if lang != "python":
        logger.info("[analyzer] language '%s' not supported by static tools — returning empty", lang)
        return {
            "language":        lang,
            "issues":          [],
            "summary":         {"counts": {"total": 0}, "by_category": {}, "by_source": {}},
            "supported":       False,
            "has_syntax_error": False,
            "message":         (
                f"Static analysis tools (AST/Pylint/Bandit) only support Python. "
                f"Language '{lang}' will be reviewed by AI only."
            ),
        }

    issues: list[Issue] = []

    # ── Pass 1: syntax check ──────────────────────────────────────────────
    logger.info("[analyzer] pass=syntax  started")
    syntax_issues = check_syntax(code)
    has_syntax_error = len(syntax_issues) > 0
    issues.extend(syntax_issues)
    logger.info("[analyzer] pass=syntax  issues=%d  has_error=%s", len(syntax_issues), has_syntax_error)

    if not has_syntax_error:
        # ── Pass 2: AST structural analysis ──────────────────────────────
        logger.info("[analyzer] pass=ast  started")
        ast_issues = run_ast_analysis(code)
        issues.extend(ast_issues)
        logger.info("[analyzer] pass=ast  issues=%d", len(ast_issues))

        # ── Pass 3: Pylint quality + naming + unused ──────────────────────
        logger.info("[analyzer] pass=pylint  started")
        pylint_issues = run_pylint_analysis(code)
        issues.extend(pylint_issues)
        logger.info("[analyzer] pass=pylint  issues=%d", len(pylint_issues))
    else:
        logger.info("[analyzer] skipping AST + Pylint passes due to syntax error")

    # ── Pass 4: Bandit security (runs even with syntax errors) ───────────
    logger.info("[analyzer] pass=bandit  started")
    bandit_issues = run_bandit_analysis(code)
    issues.extend(bandit_issues)
    logger.info("[analyzer] pass=bandit  issues=%d", len(bandit_issues))

    # ── Pass 5: regex secret detection ───────────────────────────────────
    logger.info("[analyzer] pass=secrets  started")
    secret_issues = run_secret_detection(code)
    issues.extend(secret_issues)
    logger.info("[analyzer] pass=secrets  issues=%d", len(secret_issues))

    # ── Post-process ──────────────────────────────────────────────────────
    issues = _deduplicate(issues)
    issues.sort(key=lambda i: (_SEVERITY_ORDER.get(i.severity, 99), i.line))

    logger.info("[analyzer] TOTAL issues after dedup+sort: %d", len(issues))

    return {
        "language":        "python",
        "issues":          [i.to_dict() for i in issues],
        "summary":         _build_summary(issues),
        "supported":       True,
        "has_syntax_error": has_syntax_error,
    }
