"""Persist project metadata, analysis history, and source files in Firebase."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from firebase_admin import storage
from google.api_core.exceptions import PreconditionFailed

from app.services.firebase_service import (
    SERVER_TIMESTAMP,
    firebase_credentials_configured,
    get_db,
)
from app.services.team_standard_analyzer import redact_credential_values

logger = logging.getLogger(__name__)

PROJECTS = "projects"
ANALYSIS_HISTORY = "analysis_history"
SCORE_HISTORY = "score_history"
FILES = "files"

_SAFE_SEVERITIES = {"critical", "error", "warning", "info"}
_SAFE_CATEGORIES = {
    "correctness", "security", "syntax", "bug", "quality",
    "complexity", "naming", "unused", "style",
}
_SAFE_SOURCES = {
    "ai", "ast", "bandit", "pylint", "secrets", "static",
    "syntax", "team_standard", "tree_sitter",
}
_SAFE_CONFIDENCE = {"high", "medium", "low"}
_SAFE_PROJECT_STATUSES = {"uploading", "uploaded", "analyzed", "failed"}
_SAFE_ANALYSIS_STATUSES = {"complete", "in_progress", "failed"}


def storage_configured() -> bool:
    return bool(os.getenv("FIREBASE_STORAGE_BUCKET", "").strip())


def _project_ref(upload_id: str):
    return get_db().collection(PROJECTS).document(upload_id)


def _redact_history_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_credential_values(value)
    if isinstance(value, dict):
        return {key: _redact_history_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_history_value(item) for item in value]
    return value


def _safe_choice(value: Any, choices: set[str], default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    return normalized if normalized in choices else default


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _safe_timestamp(value: Any) -> Any:
    if isinstance(value, (datetime, int, float)) and not isinstance(value, bool):
        return value
    return None


def _safe_sha256(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value):
        return value.lower()
    return ""


def _safe_numeric_map(value: Any, keys: set[str]) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    return {
        key: number
        for key in keys
        if (number := _safe_number(value.get(key))) is not None
    }


def _safe_score_history_item(document: Any) -> dict[str, Any]:
    data = document.to_dict() or {}
    categories = _safe_numeric_map(
        data.get("categories"),
        {"correctness", "security", "maintainability", "style"},
    )
    metrics = _safe_numeric_map(
        data.get("metrics"),
        {
            "analyzed_files", "nonblank_lines", "functions_measured",
            "max_cyclomatic_complexity",
        },
    )
    if isinstance(data.get("metrics"), dict) and isinstance(
        data["metrics"].get("complexity_measurable"), bool
    ):
        metrics["complexity_measurable"] = data["metrics"]["complexity_measurable"]
    return {
        "id": document.id,
        "score": _safe_number(data.get("score")),
        "categories": categories,
        "metrics": metrics,
        "coverage": _safe_numeric_map(
            data.get("coverage"), {"selected", "analyzed", "failed", "skipped"}
        ),
        "analysis_id": data.get("analysis_id")
        if isinstance(data.get("analysis_id"), str) else None,
        "created_at": _safe_timestamp(data.get("created_at")),
    }


def _safe_file_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return redact_credential_values(value)[:1024]


def _safe_project_history_item(document: Any) -> dict[str, Any]:
    data = document.to_dict() or {}
    return {
        "id": document.id,
        "name": _safe_file_path(data.get("name")) or "Unnamed project",
        "file_count": _safe_count(data.get("file_count")),
        "total_bytes": _safe_count(data.get("total_bytes")),
        "status": _safe_choice(data.get("status"), _SAFE_PROJECT_STATUSES, "uploaded"),
        "analysis_status": _safe_choice(
            data.get("analysis_status"), _SAFE_ANALYSIS_STATUSES, "in_progress"
        ) if data.get("analysis_status") else None,
        "issue_count": _safe_count(data.get("issue_count")),
        "latest_quality_score": _safe_number(data.get("latest_quality_score")),
        "created_at": _safe_timestamp(data.get("created_at")),
        "updated_at": _safe_timestamp(data.get("updated_at")),
    }


def _safe_analysis_history_item(document: Any) -> dict[str, Any]:
    data = document.to_dict() or {}
    return {
        "id": document.id,
        "status": _safe_choice(data.get("status"), _SAFE_ANALYSIS_STATUSES, "in_progress"),
        "analyzed_files": _safe_count(data.get("analyzed_files")),
        "selected_files": _safe_count(data.get("selected_files")),
        "skipped_files": _safe_count(data.get("skipped_files")),
        "failed_files": _safe_count(data.get("failed_files")),
        "issue_count": _safe_count(data.get("issue_count")),
        "created_at": _safe_timestamp(data.get("created_at")),
    }


def _timestamp_sort_key(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0


def _page_documents(documents: list[Any], page_size: int, cursor: str | None) -> dict[str, Any]:
    documents.sort(
        key=lambda doc: (
            _timestamp_sort_key((doc.to_dict() or {}).get("created_at")),
            doc.id,
        ),
        reverse=True,
    )
    start = 0
    if cursor:
        cursor_index = next(
            (index for index, document in enumerate(documents) if document.id == cursor),
            None,
        )
        if cursor_index is None:
            raise ValueError("Invalid history cursor.")
        start = cursor_index + 1
    page = documents[start:start + page_size + 1]
    has_more = len(page) > page_size
    page = page[:page_size]
    return {
        "documents": page,
        "next_cursor": page[-1].id if has_more and page else None,
    }


def _record_write(operation: str, callback) -> bool:
    if not firebase_credentials_configured():
        logger.warning(
            "Could not persist project %s: Firebase Admin credentials are not configured.",
            operation,
        )
        return False
    try:
        callback()
        return True
    except Exception:
        logger.exception("Could not persist project %s to Firebase.", operation)
        return False


def record_upload_created(
    upload_id: str,
    owner_id: str,
    manifest: dict[str, Any],
) -> bool:
    def write() -> None:
        ref = _project_ref(upload_id)
        ref.set({
            "id": upload_id,
            "name": _redact_history_value(manifest["name"]),
            "description": "",
            "owner_id": owner_id,
            "status": "uploading",
            "file_count": len(manifest["files"]),
            "total_bytes": sum(item["size"] for item in manifest["files"]),
            "storage_backend": "firebase_storage" if storage_configured() else "local",
            "created_at": manifest["created_at"],
            "updated_at": manifest["created_at"],
        }, merge=True)
        batch = get_db().batch()
        operations = 0
        for item in manifest["files"]:
            file_ref = ref.collection(FILES).document(
                hashlib.sha256(item["path"].encode("utf-8")).hexdigest()
            )
            batch.set(file_ref, {
                "path": _redact_history_value(item["path"]),
                "size": item["size"],
                "status": "pending",
            }, merge=True)
            operations += 1
            if operations == 400:
                batch.commit()
                batch = get_db().batch()
                operations = 0
        if operations:
            batch.commit()

    return _record_write("upload metadata", write)


def upload_project_files(
    upload_id: str,
    owner_id: str,
    session_dir: Path,
    manifest: dict[str, Any],
) -> bool:
    """Stream assembled files to Cloud Storage; return False when no bucket is configured."""
    if not storage_configured():
        return False

    bucket = storage.bucket()
    db = get_db()
    project_ref = db.collection(PROJECTS).document(upload_id)
    for item in manifest["files"]:
        relative_path = item["path"]
        source_path = session_dir / "project" / Path(*relative_path.split("/"))
        digest = hashlib.sha256()
        with source_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        checksum = digest.hexdigest()
        object_name = f"users/{owner_id}/projects/{upload_id}/files/{relative_path}"
        blob = bucket.blob(object_name)
        blob.chunk_size = 8 * 1024 * 1024
        blob.metadata = {"sha256": checksum}
        if blob.exists():
            blob.reload()
            if blob.size != item["size"] or (blob.metadata or {}).get("sha256") != checksum:
                generation = blob.generation
                blob.metadata = {"sha256": checksum}
                try:
                    blob.upload_from_filename(
                        str(source_path),
                        if_generation_match=generation,
                        checksum="auto",
                    )
                except PreconditionFailed:
                    blob.reload()
                    if blob.size != item["size"] or (blob.metadata or {}).get("sha256") != checksum:
                        raise
        else:
            try:
                blob.upload_from_filename(
                    str(source_path),
                    if_generation_match=0,
                    checksum="auto",
                )
            except PreconditionFailed:
                blob.reload()
                if blob.size != item["size"] or (blob.metadata or {}).get("sha256") != checksum:
                    raise

        file_ref = project_ref.collection(FILES).document(
            hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
        )
        file_ref.set({
            "path": relative_path,
            "size": item["size"],
            "status": "stored",
            "storage_path": object_name,
            "sha256": checksum,
        }, merge=True)

    project_ref.set({
        "status": "uploaded",
        "storage_backend": "firebase_storage",
    }, merge=True)
    return True


def upload_project_archive(
    upload_id: str,
    owner_id: str,
    archive_file: BinaryIO,
) -> str | None:
    """Stream a ZIP archive to Cloud Storage without buffering it in memory."""
    if not storage_configured():
        return None

    archive_file.seek(0)
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: archive_file.read(1024 * 1024), b""):
        size += len(block)
        digest.update(block)
    checksum = digest.hexdigest()
    archive_file.seek(0)

    object_name = f"users/{owner_id}/projects/{upload_id}/archive.zip"
    blob = storage.bucket().blob(object_name)
    blob.chunk_size = 8 * 1024 * 1024
    blob.metadata = {"sha256": checksum}
    if blob.exists():
        blob.reload()
        if blob.size != size or (blob.metadata or {}).get("sha256") != checksum:
            generation = blob.generation
            blob.metadata = {"sha256": checksum}
            try:
                blob.upload_from_file(
                    archive_file,
                    rewind=True,
                    size=size,
                    content_type="application/zip",
                    if_generation_match=generation,
                    checksum="auto",
                )
            except PreconditionFailed:
                blob.reload()
                if blob.size != size or (blob.metadata or {}).get("sha256") != checksum:
                    raise
    else:
        try:
            blob.upload_from_file(
                archive_file,
                rewind=True,
                size=size,
                content_type="application/zip",
                if_generation_match=0,
                checksum="auto",
            )
        except PreconditionFailed:
            blob.reload()
            if blob.size != size or (blob.metadata or {}).get("sha256") != checksum:
                raise

    _project_ref(upload_id).set({
        "archive_storage_path": object_name,
    }, merge=True)
    return object_name


def delete_project_objects(upload_id: str, owner_id: str) -> None:
    if not storage_configured():
        return
    prefix = f"users/{owner_id}/projects/{upload_id}/"
    for blob in storage.bucket().list_blobs(prefix=prefix):
        blob.delete()


def record_upload_complete(upload_id: str, storage_enabled: bool) -> bool:
    return _record_write(
        "upload status",
        lambda: _project_ref(upload_id).set({
            "status": "uploaded",
            "storage_backend": "firebase_storage" if storage_enabled else "local",
        }, merge=True),
    )


def record_upload_failure(upload_id: str) -> bool:
    return _record_write(
        "upload failure status",
        lambda: _project_ref(upload_id).set({
            "status": "failed",
            "updated_at": SERVER_TIMESTAMP,
        }, merge=True),
    )


def record_analysis(
    upload_id: str,
    analysis_id: str,
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> bool:
    def write() -> None:
        ref = _project_ref(upload_id).collection(ANALYSIS_HISTORY).document(analysis_id)
        issue_count = sum(len(result.get("issues", [])) for result in results)
        ref.set({
            "status": "complete" if summary.get("complete") else "in_progress",
            "analyzed_files": summary.get("analyzed", 0),
            "selected_files": summary.get("selected", 0),
            "skipped_files": summary.get("skipped", 0),
            "failed_files": len(summary.get("errors", [])),
            "issue_count": issue_count,
            "languages": sorted({
                result["language"]
                for result in results
                if isinstance(result.get("language"), str)
                and re.fullmatch(r"[A-Za-z0-9#+.-]{1,60}", result["language"])
            }),
            "created_at": SERVER_TIMESTAMP,
        }, merge=True)

        findings_ref = ref.collection("findings")
        files_ref = ref.collection("files")
        existing = list(findings_ref.stream())
        existing_files = list(files_ref.stream())
        batch = get_db().batch()
        desired_ids: set[str] = set()
        desired_file_ids: set[str] = set()
        operations = 0

        def commit_if_full() -> None:
            nonlocal batch, operations
            if operations >= 400:
                batch.commit()
                batch = get_db().batch()
                operations = 0

        for result in results:
            file_index = result["file_index"]
            file_id = str(file_index)
            desired_file_ids.add(file_id)
            batch.set(files_ref.document(file_id), {
                "file_index": file_index,
                "path": _safe_file_path(result.get("path")),
                "language": result.get("language", "")[:60]
                if isinstance(result.get("language"), str)
                and re.fullmatch(r"[A-Za-z0-9#+.-]{1,60}", result.get("language")) else "",
                "ai_available": result.get("ai_available") is True,
                "ai_code_truncated": result.get("ai_code_truncated") is True,
                "source_sha256": _safe_sha256(result.get("source_sha256")),
            })
            operations += 1
            commit_if_full()
            for index, issue in enumerate(result.get("issues", [])):
                finding_id = f"{file_index}-{index}"
                desired_ids.add(finding_id)
                category = _safe_choice(issue.get("category"), _SAFE_CATEGORIES, "quality")
                line = issue.get("line", 0)
                batch.set(findings_ref.document(finding_id), {
                    "file_index": file_index,
                    "path": _safe_file_path(result.get("path")),
                    "line": line if isinstance(line, int) and not isinstance(line, bool) and line >= 0 else 0,
                    "severity": _safe_choice(issue.get("severity"), _SAFE_SEVERITIES, "info"),
                    "category": category,
                    "title": f"{category.title()} finding",
                    "source": _safe_choice(issue.get("source"), _SAFE_SOURCES, "static"),
                    "confidence": _safe_choice(issue.get("confidence"), _SAFE_CONFIDENCE, "")
                    or None,
                })
                operations += 1
                commit_if_full()

        for finding in existing:
            if finding.id not in desired_ids:
                batch.delete(finding.reference)
                operations += 1
                commit_if_full()
        for file_result in existing_files:
            if file_result.id not in desired_file_ids:
                batch.delete(file_result.reference)
                operations += 1
                commit_if_full()
        if operations:
            batch.commit()

        _project_ref(upload_id).set({
            "status": "analyzed" if summary.get("complete") else "uploaded",
            "latest_analysis_id": analysis_id,
            "analysis_status": "complete" if summary.get("complete") else "in_progress",
            "issue_count": issue_count,
            "updated_at": SERVER_TIMESTAMP,
        }, merge=True)

    return _record_write("analysis history", write)


def list_project_history(
    owner_id: str,
    page_size: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    documents = [
        document
        for document in get_db().collection(PROJECTS).where("owner_id", "==", owner_id).stream()
        if "file_count" in (document.to_dict() or {})
    ]
    page = _page_documents(documents, page_size, cursor)
    return {
        "items": [_safe_project_history_item(document) for document in page["documents"]],
        "next_cursor": page["next_cursor"],
    }


def _owned_project_ref(upload_id: str, owner_id: str):
    project_ref = _project_ref(upload_id)
    project = project_ref.get()
    if not project.exists or (project.to_dict() or {}).get("owner_id") != owner_id:
        return None, None
    return project_ref, project.to_dict()


def list_analysis_history(
    upload_id: str,
    owner_id: str,
    page_size: int = 20,
    cursor: str | None = None,
) -> dict[str, Any] | None:
    project_ref, _ = _owned_project_ref(upload_id, owner_id)
    if project_ref is None:
        return None
    documents = list(project_ref.collection(ANALYSIS_HISTORY).stream())
    page = _page_documents(documents, page_size, cursor)
    return {
        "items": [_safe_analysis_history_item(document) for document in page["documents"]],
        "next_cursor": page["next_cursor"],
    }


def get_analysis_history(
    upload_id: str,
    analysis_id: str,
    owner_id: str,
) -> dict[str, Any] | None:
    project_ref, project = _owned_project_ref(upload_id, owner_id)
    if project_ref is None:
        return None
    analysis_ref = project_ref.collection(ANALYSIS_HISTORY).document(analysis_id)
    analysis = analysis_ref.get()
    if not analysis.exists:
        return None

    files = []
    for document in analysis_ref.collection("files").stream():
        data = document.to_dict() or {}
        files.append({
            "id": document.id,
            "file_index": _safe_count(data.get("file_index")),
            "path": _safe_file_path(data.get("path")),
            "language": data.get("language", "")[:60]
            if isinstance(data.get("language"), str)
            and re.fullmatch(r"[A-Za-z0-9#+.-]{1,60}", data.get("language")) else "",
            "ai_available": data.get("ai_available") is True,
            "ai_code_truncated": data.get("ai_code_truncated") is True,
            "source_sha256": _safe_sha256(data.get("source_sha256")),
        })
    findings = []
    for document in analysis_ref.collection("findings").stream():
        data = document.to_dict() or {}
        category = _safe_choice(data.get("category"), _SAFE_CATEGORIES, "quality")
        line = data.get("line", 0)
        findings.append({
            "id": document.id,
            "file_index": _safe_count(data.get("file_index")),
            "path": _safe_file_path(data.get("path")),
            "line": line if isinstance(line, int) and not isinstance(line, bool) and line >= 0 else 0,
            "severity": _safe_choice(data.get("severity"), _SAFE_SEVERITIES, "info"),
            "category": category,
            "title": f"{category.title()} finding",
            "source": _safe_choice(data.get("source"), _SAFE_SOURCES, "static"),
            "confidence": _safe_choice(data.get("confidence"), _SAFE_CONFIDENCE, "") or None,
        })
    scores = [
        _safe_score_history_item(document)
        for document in project_ref.collection(SCORE_HISTORY).stream()
    ]
    matching_scores = [
        score for score in scores
        if score.get("analysis_id") in (None, analysis_id)
    ]
    return {
        **_safe_analysis_history_item(analysis),
        "id": analysis.id,
        "project": {
            "id": upload_id,
            "name": _safe_file_path(project.get("name")) or "Unnamed project",
            "storage_backend": _safe_choice(
                project.get("storage_backend"), {"firebase_storage", "local"}, "local"
            ),
        },
        "files": sorted(files, key=lambda item: item.get("file_index", 0)),
        "findings": sorted(
            findings,
            key=lambda item: (
                item.get("path", ""),
                item.get("line", 0),
                item.get("id", ""),
            ),
        ),
        "quality_scores": sorted(
            matching_scores,
            key=lambda item: _timestamp_sort_key(item.get("created_at")),
            reverse=True,
        ),
    }


def project_source_storage(upload_id: str, owner_id: str) -> str:
    project_ref, project = _owned_project_ref(upload_id, owner_id)
    if project_ref is None:
        return "unavailable"
    if project.get("storage_backend") == "firebase_storage":
        files = list(project_ref.collection(FILES).stream())
        if files and all((document.to_dict() or {}).get("storage_path") for document in files):
            return "cloud"
    return "local"


def record_quality_score(
    upload_id: str,
    score: dict[str, Any],
    analysis_id: str | None = None,
) -> bool:
    def write() -> None:
        db = get_db()
        project_ref = db.collection(PROJECTS).document(upload_id)
        score_id = uuid.uuid4().hex
        metrics = _safe_numeric_map(
            score.get("metrics"),
            {
                "analyzed_files", "nonblank_lines", "functions_measured",
                "max_cyclomatic_complexity",
            },
        )
        if isinstance(score.get("metrics"), dict) and isinstance(
            score["metrics"].get("complexity_measurable"), bool
        ):
            metrics["complexity_measurable"] = score["metrics"]["complexity_measurable"]
        score_data = {
            "score": _safe_number(score.get("score")),
            "categories": _safe_numeric_map(
                score.get("categories"),
                {"correctness", "security", "maintainability", "style"},
            ),
            "metrics": metrics,
            "coverage": _safe_numeric_map(
                score.get("coverage"), {"selected", "analyzed", "failed", "skipped"}
            ),
            "analysis_id": analysis_id if isinstance(analysis_id, str) else None,
            "created_at": SERVER_TIMESTAMP,
        }
        project_ref.collection(SCORE_HISTORY).document(score_id).set(score_data)
        project_ref.set({
            "latest_quality_score": score_data["score"],
            "latest_score_id": score_id,
            "updated_at": SERVER_TIMESTAMP,
        }, merge=True)

    return _record_write("quality score", write)
