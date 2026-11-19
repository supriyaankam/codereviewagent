"""One-time, allowlist-based cleanup for legacy Project History documents."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from google.api_core.exceptions import Aborted

from app.services.team_standard_analyzer import redact_credential_values

PROJECTS = "projects"
ANALYSIS_HISTORY = "analysis_history"
SCORE_HISTORY = "score_history"
FILES = "files"
FINDINGS = "findings"

_DOCUMENT_KINDS = (
    "project",
    "project_file",
    "analysis",
    "analysis_file",
    "finding",
    "score",
)
_PROJECT_STATUSES = {"uploading", "uploaded", "analyzed", "failed"}
_ANALYSIS_STATUSES = {"complete", "in_progress", "failed"}
_FILE_STATUSES = {"pending", "stored"}
_PARTIAL_UPLOAD_MARKER_FIELDS = {
    "storage_backend",
    "archive_storage_path",
    "analysis_status",
    "latest_analysis_id",
    "latest_score_id",
    "latest_quality_score",
    "issue_count",
}
_SEVERITIES = {"critical", "error", "warning", "info"}
_CATEGORIES = {
    "correctness", "security", "syntax", "bug", "quality",
    "complexity", "naming", "unused", "style",
}
_SOURCES = {
    "ai", "ast", "bandit", "pylint", "secrets", "static",
    "syntax", "team_standard", "tree_sitter",
}
_CONFIDENCE = {"high", "medium", "low"}
_LANGUAGE = re.compile(r"[A-Za-z0-9#+.-]{1,60}")
_SHA256 = re.compile(r"[a-fA-F0-9]{64}")
_OMIT = object()

_SCORE_CATEGORIES = {"correctness", "security", "maintainability", "style"}
_SCORE_METRICS = {
    "analyzed_files", "nonblank_lines", "functions_measured",
    "max_cyclomatic_complexity",
}
_SCORE_COVERAGE = {"selected", "analyzed", "failed", "skipped"}
_REPORT_FIELD_NAMES = {
    "id", "name", "owner_id", "status", "analysis_status", "file_count",
    "total_bytes", "issue_count", "latest_quality_score", "created_at",
    "updated_at", "latest_analysis_id", "latest_score_id", "storage_backend",
    "archive_storage_path", "path", "size", "storage_path", "sha256",
    "analyzed_files", "selected_files", "skipped_files", "failed_files",
    "languages", "file_index", "language", "ai_available",
    "ai_code_truncated", "source_sha256", "line", "category", "title",
    "severity", "source", "confidence", "score", "categories", "metrics",
    "coverage", "analysis_id", "complexity_measurable", "nonblank_lines",
    "functions_measured", "max_cyclomatic_complexity", "correctness",
    "security", "maintainability", "style", "selected", "analyzed",
    "failed", "skipped", "description", "summary", "source_code", "code",
    "snippet", "content", "raw_code", "recommendation", "recommendations",
    "beginner_explanation", "example", "ai_failure_reason", "factors",
    "findings", "explanation", "reason", "suggestion", "fix", "patch",
    "prompt", "response", "GROQ_API_KEY", "API_KEY", "SECRET_KEY",
    "DATABASE_URL", "password", "token", "credentials",
}
_REPORT_FIELD_PATH_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _safe_choice(value: Any, allowed: set[str]) -> Any:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return _OMIT


def _safe_count(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return _OMIT


def _safe_number(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return _OMIT


def _safe_timestamp(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if not isinstance(value, float) or math.isfinite(value) else _OMIT
    return _OMIT


def _safe_identifier(value: Any) -> Any:
    if isinstance(value, str) and value and len(value) <= 256:
        return redact_credential_values(value)
    return _OMIT


def _safe_path(value: Any) -> Any:
    if isinstance(value, str):
        return redact_credential_values(value)[:1024]
    return _OMIT


def _copy_if_present(
    source: Mapping[str, Any],
    target: dict[str, Any],
    field: str,
    sanitizer: Callable[[Any], Any],
) -> None:
    if field not in source:
        return
    value = sanitizer(source[field])
    if value is not _OMIT:
        target[field] = value


def _safe_numeric_map(value: Any, allowed: set[str]) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: number
        for key in allowed
        if (number := _safe_number(value.get(key))) is not _OMIT
    }


def _safe_score_metrics(value: Any) -> dict[str, Any]:
    metrics = _safe_numeric_map(value, _SCORE_METRICS)
    if isinstance(value, Mapping) and isinstance(value.get("complexity_measurable"), bool):
        metrics["complexity_measurable"] = value["complexity_measurable"]
    return metrics


def _project_record_scope(data: Mapping[str, Any]) -> str:
    if "file_count" in data:
        if _safe_count(data.get("file_count")) is not _OMIT:
            return "eligible"
        return "ambiguous"
    status = data.get("status")
    if (
        any(field in data for field in _PARTIAL_UPLOAD_MARKER_FIELDS)
        or (isinstance(status, str) and status in _PROJECT_STATUSES)
    ):
        return "ambiguous"
    return "ordinary"


def sanitize_document(kind: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the current safe schema for a Project History document."""
    safe: dict[str, Any] = {}

    if kind == "project":
        _copy_if_present(data, safe, "id", _safe_identifier)
        _copy_if_present(data, safe, "name", _safe_path)
        _copy_if_present(data, safe, "owner_id", _safe_identifier)
        _copy_if_present(data, safe, "status", lambda value: _safe_choice(value, _PROJECT_STATUSES))
        _copy_if_present(data, safe, "analysis_status", lambda value: _safe_choice(value, _ANALYSIS_STATUSES))
        for field in ("file_count", "total_bytes", "issue_count"):
            _copy_if_present(data, safe, field, _safe_count)
        for field in ("latest_quality_score",):
            _copy_if_present(data, safe, field, _safe_number)
        for field in ("created_at", "updated_at"):
            _copy_if_present(data, safe, field, _safe_timestamp)
        for field in ("latest_analysis_id", "latest_score_id"):
            _copy_if_present(data, safe, field, _safe_identifier)
        _copy_if_present(
            data,
            safe,
            "storage_backend",
            lambda value: _safe_choice(value, {"firebase_storage", "local"}),
        )
        _copy_if_present(data, safe, "archive_storage_path", _safe_path)
        return safe

    if kind == "project_file":
        _copy_if_present(data, safe, "path", _safe_path)
        _copy_if_present(data, safe, "size", _safe_count)
        _copy_if_present(data, safe, "status", lambda value: _safe_choice(value, _FILE_STATUSES))
        _copy_if_present(data, safe, "storage_path", _safe_path)
        _copy_if_present(data, safe, "sha256", lambda value: value.lower() if isinstance(value, str) and _SHA256.fullmatch(value) else _OMIT)
        return safe

    if kind == "analysis":
        _copy_if_present(data, safe, "status", lambda value: _safe_choice(value, _ANALYSIS_STATUSES))
        for field in ("analyzed_files", "selected_files", "skipped_files", "failed_files", "issue_count"):
            _copy_if_present(data, safe, field, _safe_count)
        _copy_if_present(data, safe, "created_at", _safe_timestamp)
        languages = data.get("languages")
        if isinstance(languages, list):
            safe["languages"] = sorted({
                value for value in languages
                if isinstance(value, str) and _LANGUAGE.fullmatch(value)
            })[:100]
        return safe

    if kind == "analysis_file":
        _copy_if_present(data, safe, "file_index", _safe_count)
        _copy_if_present(data, safe, "path", _safe_path)
        language = data.get("language")
        if isinstance(language, str) and _LANGUAGE.fullmatch(language):
            safe["language"] = language
        for field in ("ai_available", "ai_code_truncated"):
            if isinstance(data.get(field), bool):
                safe[field] = data[field]
        _copy_if_present(data, safe, "source_sha256", lambda value: value.lower() if isinstance(value, str) and _SHA256.fullmatch(value) else _OMIT)
        return safe

    if kind == "finding":
        _copy_if_present(data, safe, "file_index", _safe_count)
        _copy_if_present(data, safe, "path", _safe_path)
        _copy_if_present(data, safe, "line", _safe_count)
        category = _safe_choice(data.get("category"), _CATEGORIES)
        if category is _OMIT:
            category = "quality"
        safe["category"] = category
        safe["title"] = f"{category.title()} finding"
        _copy_if_present(data, safe, "severity", lambda value: _safe_choice(value, _SEVERITIES))
        _copy_if_present(data, safe, "source", lambda value: _safe_choice(value, _SOURCES))
        _copy_if_present(data, safe, "confidence", lambda value: _safe_choice(value, _CONFIDENCE))
        return safe

    if kind == "score":
        _copy_if_present(data, safe, "score", _safe_number)
        if "categories" in data:
            safe["categories"] = _safe_numeric_map(data["categories"], _SCORE_CATEGORIES)
        if "metrics" in data:
            safe["metrics"] = _safe_score_metrics(data["metrics"])
        if "coverage" in data:
            safe["coverage"] = _safe_numeric_map(data["coverage"], _SCORE_COVERAGE)
        _copy_if_present(data, safe, "analysis_id", _safe_identifier)
        _copy_if_present(data, safe, "created_at", _safe_timestamp)
        return safe

    raise ValueError(f"Unknown Project History document kind: {kind}")


def _report_field_path(path: tuple[str, ...]) -> str:
    safe_parts = [
        part if part in _REPORT_FIELD_NAMES and _REPORT_FIELD_PATH_PART.fullmatch(part)
        else "other"
        for part in path
    ]
    return ".".join(safe_parts)


def _field_differences(
    original: Mapping[str, Any],
    safe: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    removed: Counter[str] = Counter()
    modified: Counter[str] = Counter()
    added: Counter[str] = Counter()
    for field, value in original.items():
        field_path = (*path, str(field))
        if field not in safe:
            removed[_report_field_path(field_path)] += 1
        elif isinstance(value, Mapping) and isinstance(safe[field], Mapping):
            child_removed, child_modified, child_added = _field_differences(
                value, safe[field], field_path
            )
            removed.update(child_removed)
            modified.update(child_modified)
            added.update(child_added)
        elif value != safe[field]:
            modified[_report_field_path(field_path)] += 1
    for field in safe.keys() - original.keys():
        added[_report_field_path((*path, str(field)))] += 1
    return removed, modified, added


def _replace_document_if_unchanged(
    db: Any,
    document: Any,
    safe: Mapping[str, Any],
) -> bool:
    expected_update_time = getattr(document, "update_time", None)
    if expected_update_time is None:
        raise RuntimeError(
            "Cannot safely sanitize a document without its Firestore update time."
        )

    transaction = db.transaction()
    current = document.reference.get(transaction=transaction)
    if not current.exists or current.update_time != expected_update_time:
        transaction.rollback()
        return False

    transaction.set(document.reference, dict(safe))
    try:
        transaction.commit()
    except Aborted:
        return False
    return True


def _initial_stats() -> dict[str, Any]:
    return {
        "eligible_history_uploads": 0,
        "ordinary_projects_skipped": 0,
        "ambiguous_history_records_skipped": 0,
        "write_conflicts": {kind: 0 for kind in _DOCUMENT_KINDS},
        "documents_scanned": {kind: 0 for kind in _DOCUMENT_KINDS},
        "documents_changed": {kind: 0 for kind in _DOCUMENT_KINDS},
        "documents_written": {kind: 0 for kind in _DOCUMENT_KINDS},
        "fields_removed": {kind: 0 for kind in _DOCUMENT_KINDS},
        "fields_modified": {kind: 0 for kind in _DOCUMENT_KINDS},
        "fields_added": {kind: 0 for kind in _DOCUMENT_KINDS},
        "removed_fields_by_name": {kind: Counter() for kind in _DOCUMENT_KINDS},
        "modified_fields_by_name": {kind: Counter() for kind in _DOCUMENT_KINDS},
        "added_fields_by_name": {kind: Counter() for kind in _DOCUMENT_KINDS},
    }


def _clean_collection(
    collection_ref: Any,
    kind: str,
    stats: dict[str, Any],
    apply: bool,
    db: Any,
) -> bool:
    conflicted = False
    for document in collection_ref.stream():
        original = document.to_dict()
        if not isinstance(original, Mapping):
            continue
        safe = sanitize_document(kind, original)
        stats["documents_scanned"][kind] += 1
        removed, modified, added = _field_differences(original, safe)
        if original == safe:
            continue
        stats["documents_changed"][kind] += 1
        stats["fields_removed"][kind] += sum(removed.values())
        stats["fields_modified"][kind] += sum(modified.values())
        stats["fields_added"][kind] += sum(added.values())
        stats["removed_fields_by_name"][kind].update(removed)
        stats["modified_fields_by_name"][kind].update(modified)
        stats["added_fields_by_name"][kind].update(added)
        if apply:
            if not _replace_document_if_unchanged(db, document, safe):
                stats["write_conflicts"][kind] += 1
                conflicted = True
                continue
            stats["documents_written"][kind] += 1
    return conflicted


def migrate_project_history(db: Any, apply: bool = False) -> dict[str, Any]:
    """Sanitize known Project History collections; dry-run unless explicitly applied."""
    stats = _initial_stats()
    for project in db.collection(PROJECTS).stream():
        project_data = project.to_dict() or {}
        if not isinstance(project_data, Mapping):
            stats["ambiguous_history_records_skipped"] += 1
            continue
        scope = _project_record_scope(project_data)
        if scope == "ordinary":
            stats["ordinary_projects_skipped"] += 1
            continue
        if scope == "ambiguous":
            stats["ambiguous_history_records_skipped"] += 1
            continue

        stats["eligible_history_uploads"] += 1
        project_ref = project.reference
        if _clean_collection(
            _SingleDocumentCollection(project), "project", stats, apply, db
        ):
            continue
        _clean_collection(project_ref.collection(FILES), "project_file", stats, apply, db)
        for analysis in project_ref.collection(ANALYSIS_HISTORY).stream():
            analysis_ref = analysis.reference
            if _clean_collection(
                _SingleDocumentCollection(analysis), "analysis", stats, apply, db
            ):
                continue
            _clean_collection(
                analysis_ref.collection(FILES), "analysis_file", stats, apply, db
            )
            _clean_collection(
                analysis_ref.collection(FINDINGS), "finding", stats, apply, db
            )
        _clean_collection(
            project_ref.collection(SCORE_HISTORY), "score", stats, apply, db
        )
    return stats


class _SingleDocumentCollection:
    """Adapt a project/analysis snapshot to the common collection sanitizer."""

    def __init__(self, document: Any) -> None:
        self._document = document

    def stream(self) -> tuple[Any, ...]:
        return (self._document,)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove non-allowlisted fields from existing Project History documents."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write sanitized documents. Without this flag, only report proposed changes.",
    )
    return parser


def _print_report(stats: Mapping[str, Any], apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN"
    scanned = sum(stats["documents_scanned"].values())
    changed = sum(stats["documents_changed"].values())
    removed = sum(stats["fields_removed"].values())
    modified = sum(stats["fields_modified"].values())
    added = sum(stats["fields_added"].values())
    written = sum(stats["documents_written"].values())
    conflicts = sum(stats["write_conflicts"].values())
    print(
        f"Project History cleanup [{mode}]: {scanned} documents scanned, "
        f"{changed} documents requiring changes, {removed} fields removed, "
        f"{modified} fields modified/normalized, {added} fields added, "
        f"{written} documents written."
    )
    print(f"Eligible Project History uploads: {stats['eligible_history_uploads']}.")
    print(f"Ordinary projects skipped: {stats['ordinary_projects_skipped']}.")
    print(
        "Ambiguous or partial history records skipped: "
        f"{stats['ambiguous_history_records_skipped']}."
    )
    print(f"Write conflicts skipped: {conflicts}.")
    collection_names = {
        "project": "projects",
        "project_file": "projects/{project}/files",
        "analysis": "projects/{project}/analysis_history",
        "analysis_file": "projects/{project}/analysis_history/{analysis}/files",
        "finding": "projects/{project}/analysis_history/{analysis}/findings",
        "score": "projects/{project}/score_history",
    }
    for kind in _DOCUMENT_KINDS:
        print(
            f"{collection_names[kind]}: "
            f"{stats['documents_scanned'][kind]} scanned, "
            f"{stats['documents_changed'][kind]} needing changes."
        )

    for label, stats_key in (
        ("Removed", "removed_fields_by_name"),
        ("Modified/normalized", "modified_fields_by_name"),
        ("Added", "added_fields_by_name"),
    ):
        print(f"{label} fields by collection:")
        has_fields = False
        for kind in _DOCUMENT_KINDS:
            field_counts = stats[stats_key][kind]
            if not field_counts:
                continue
            has_fields = True
            counts = ", ".join(
                f"{field}={count}" for field, count in sorted(field_counts.items())
            )
            print(f"  {collection_names[kind]}: {counts}")
        if not has_fields:
            print("  none")

    print(
        "Finding title policy: stored titles that differ from the category-based "
        "title are normalized; stored title text is never shown in this report."
    )
    print("Anonymized finding example (illustrative only; not a stored document):")
    before = {
        "path": "src/[file].py",
        "line": 12,
        "severity": "warning",
        "category": "security",
        "title": "[legacy free-form title]",
        "description": "[not shown]",
    }
    after = {
        "path": "src/[file].py",
        "line": 12,
        "severity": "warning",
        "category": "security",
        "title": "Security finding",
    }
    print(f"  before: {json.dumps(before, sort_keys=True)}")
    print(f"  after:  {json.dumps(after, sort_keys=True)}")


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv()
    from app.services.firebase_service import get_db

    stats = migrate_project_history(get_db(), apply=args.apply)
    _print_report(stats, apply=args.apply)


if __name__ == "__main__":
    main()
