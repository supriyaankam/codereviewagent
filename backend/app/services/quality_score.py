"""Deterministic code quality scoring based on review findings and Python metrics."""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable


_SEVERITY_PENALTIES = {
    "critical": 20,
    "error": 12,
    "warning": 5,
    "info": 1,
}
_CATEGORY_ALIASES = {
    "security": "security",
    "syntax": "correctness",
    "bug": "correctness",
    "correctness": "correctness",
    "quality": "maintainability",
    "complexity": "maintainability",
    "naming": "maintainability",
    "unused": "maintainability",
    "style": "style",
}
def _finding_key(issue: dict[str, Any]) -> tuple[str, int]:
    title = re.sub(r"^\[[^\]]+\]\s*", "", str(issue.get("title", ""))).strip().lower()
    return title, int(issue.get("line") or 0)


def _unique_findings(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    severity_rank = {"critical": 0, "error": 1, "warning": 2, "info": 3}

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        key = _finding_key(issue)
        current = unique.get(key)
        if current is None:
            unique[key] = issue
        elif (
            severity_rank.get(str(issue.get("severity", "info")).lower(), 3),
            str(issue.get("category", "")).lower(),
            str(issue.get("source", "")).lower(),
            str(issue.get("title", "")).strip().lower(),
        ) < (
            severity_rank.get(str(current.get("severity", "info")).lower(), 3),
            str(current.get("category", "")).lower(),
            str(current.get("source", "")).lower(),
            str(current.get("title", "")).strip().lower(),
        ):
            unique[key] = issue
    return [unique[key] for key in sorted(unique)]


class _ComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.complexities: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.complexities.append(_cyclomatic_complexity(node))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def _cyclomatic_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    decisions = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.IfExp,
        ast.ExceptHandler,
        ast.comprehension,
        ast.Match,
    )
    count = 1 + sum(isinstance(child, decisions) for child in ast.walk(node))
    count += sum(
        max(0, len(child.values) - 1)
        for child in ast.walk(node)
        if isinstance(child, ast.BoolOp)
    )
    return count


def _python_metrics(code: str, language: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "nonblank_lines": sum(bool(line.strip()) for line in code.splitlines()),
        "functions_measured": None,
        "max_cyclomatic_complexity": None,
        "complexity_measurable": False,
    }
    if language.strip().lower() not in {"python", "py"}:
        return metrics

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return metrics

    visitor = _ComplexityVisitor()
    visitor.visit(tree)
    metrics.update(
        functions_measured=len(visitor.complexities),
        max_cyclomatic_complexity=max(visitor.complexities, default=1),
        complexity_measurable=True,
    )
    return metrics


def calculate_quality_score(
    code: str,
    language: str,
    ai_issues: list[dict[str, Any]],
    static_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a reproducible 0-100 score, category scores, factors, and metrics.

    Only unique static findings affect finding penalties. AI findings are excluded
    because model output can vary for identical source. Python complexity is
    computed directly from source and is independent of analyzer wording.
    """
    metrics = _python_metrics(code, language)
    findings = _unique_findings(static_issues)
    category_penalties: defaultdict[str, int] = defaultdict(int)
    factors: list[str] = []
    max_complexity = metrics["max_cyclomatic_complexity"]

    for issue in findings:
        severity = str(issue.get("severity", "info")).lower()
        penalty = _SEVERITY_PENALTIES.get(severity, 0)
        category = _CATEGORY_ALIASES.get(str(issue.get("category", "")).lower(), "maintainability")
        if (
            category == "maintainability"
            and str(issue.get("category", "")).lower() == "complexity"
            and max_complexity is not None
            and max_complexity > 10
        ):
            continue
        category_penalties[category] += penalty
        if penalty:
            factors.append(
                f"{issue.get('title', 'Finding')}: -{penalty} points ({severity})."
            )

    complexity_penalty = 0
    if max_complexity is not None and max_complexity > 10:
        complexity_penalty = min(30, (max_complexity - 10) * 2)
        category_penalties["maintainability"] += complexity_penalty
        factors.append(
            f"Maximum measured function complexity is {max_complexity} "
            f"(threshold 10): -{complexity_penalty} points."
        )

    category_scores = {
        name: max(0, 100 - min(100, category_penalties[name]))
        for name in ("correctness", "security", "maintainability", "style")
    }
    score = max(0, 100 - sum(category_penalties.values()))

    if not factors:
        factors.append("No confirmed findings reduced the score.")
    if not metrics["complexity_measurable"]:
        factors.append("Cyclomatic complexity was not measured for this language or invalid Python syntax.")

    return {
        "score": score,
        "categories": category_scores,
        "factors": factors,
        "metrics": metrics,
        "method": {
            "finding_penalties": _SEVERITY_PENALTIES,
            "complexity_threshold": 10,
            "complexity_points_per_step": 2,
            "complexity_penalty_cap": 30,
            "notes": (
                "Finding penalties use unique static findings only; AI findings are excluded so "
                "model wording or confidence variations cannot change the score. Duplicate static "
                "findings sharing a normalized title and line count once. Penalties are critical "
                "20, error 12, warning 5, and info 1 points, plus a Python complexity penalty of "
                "2 points per complexity level above 10, capped at 30. The score is 100 minus "
                "penalties, floored at 0. Python function complexity is measured from the AST; "
                "other languages report complexity as unavailable."
            ),
        },
    }


def calculate_project_quality_score(files: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-file scores without collapsing findings from different files."""
    file_scores = []
    category_penalties: defaultdict[str, int] = defaultdict(int)
    factors: list[str] = []
    total_penalty = 0
    nonblank_lines = 0
    max_complexity: int | None = None
    functions_measured = 0
    complexity_measurable = False
    analyzed_files = 0
    unique_sources: dict[tuple[str, str], str] = {}

    for file in files:
        analyzed_files += 1
        result = calculate_quality_score(
            code=file["code"],
            language=file["language"],
            ai_issues=file.get("ai_issues", []),
            static_issues=file.get("static_issues", []),
        )
        path = str(file["path"])
        language = str(file["language"])
        source_digest = hashlib.sha256(file["code"].encode("utf-8")).hexdigest()
        source_key = (language.strip().lower(), source_digest)
        duplicate_of = unique_sources.get(source_key)
        if duplicate_of is None:
            unique_sources[source_key] = path
        file_score = {
            "path": path,
            "language": language,
            "score": result["score"],
        }
        if duplicate_of is not None:
            file_score["duplicate_of"] = duplicate_of
        file_scores.append(file_score)
        metrics = result["metrics"]
        nonblank_lines += metrics["nonblank_lines"]
        if metrics["complexity_measurable"]:
            complexity_measurable = True
            functions_measured += metrics["functions_measured"] or 0
            measured = metrics["max_cyclomatic_complexity"]
            max_complexity = max(max_complexity or 0, measured or 0)
        if duplicate_of is not None:
            continue
        total_penalty += 100 - result["score"]
        for category, category_score in result["categories"].items():
            category_penalties[category] += 100 - category_score
        factors.extend(f"{path}: {factor}" for factor in result["factors"])

    categories = {
        name: max(0, 100 - min(100, category_penalties[name]))
        for name in ("correctness", "security", "maintainability", "style")
    }
    if not factors:
        factors.append("No confirmed findings reduced the score.")

    return {
        "score": max(0, 100 - min(100, total_penalty)),
        "categories": categories,
        "factors": factors,
        "metrics": {
            "analyzed_files": analyzed_files,
            "nonblank_lines": nonblank_lines,
            "functions_measured": functions_measured if complexity_measurable else None,
            "max_cyclomatic_complexity": max_complexity,
            "complexity_measurable": complexity_measurable,
        },
        "files": file_scores,
        "method": {
            "finding_penalties": _SEVERITY_PENALTIES,
            "complexity_threshold": 10,
            "complexity_points_per_step": 2,
            "complexity_penalty_cap_per_file": 30,
            "notes": (
                "The project score starts at 100 and subtracts each file's unique, confirmed "
                "finding penalties and measured Python complexity penalties. Findings are "
                "deduplicated within each file; identical source files in the same language "
                "contribute only once to the project total. AI findings below high confidence "
                "are excluded."
            ),
        },
    }
