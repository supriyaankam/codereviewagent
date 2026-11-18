"""Persist project metadata, analysis history, and source files in Firebase."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from firebase_admin import storage
from google.api_core.exceptions import PreconditionFailed

from app.services.firebase_service import (
    SERVER_TIMESTAMP,
    firebase_credentials_configured,
    get_db,
)

logger = logging.getLogger(__name__)

PROJECTS = "projects"
ANALYSIS_HISTORY = "analysis_history"
SCORE_HISTORY = "score_history"
FILES = "files"


def storage_configured() -> bool:
    return bool(os.getenv("FIREBASE_STORAGE_BUCKET", "").strip())


def _project_ref(upload_id: str):
    return get_db().collection(PROJECTS).document(upload_id)


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
            "name": manifest["name"],
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
                "path": item["path"],
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
            "languages": summary.get("languages", []),
            "created_at": SERVER_TIMESTAMP,
        }, merge=True)

        findings_ref = ref.collection("findings")
        existing = list(findings_ref.stream())
        batch = get_db().batch()
        desired_ids: set[str] = set()
        operations = 0

        def commit_if_full() -> None:
            nonlocal batch, operations
            if operations >= 400:
                batch.commit()
                batch = get_db().batch()
                operations = 0

        for result in results:
            for index, issue in enumerate(result.get("issues", [])):
                finding_id = f"{result['file_index']}-{index}"
                desired_ids.add(finding_id)
                batch.set(findings_ref.document(finding_id), {
                    "path": result["path"],
                    "line": issue.get("line", 0),
                    "severity": issue.get("severity", "info"),
                    "category": issue.get("category", "quality"),
                    "title": issue.get("title", ""),
                    "source": issue.get("source", ""),
                })
                operations += 1
                commit_if_full()

        for finding in existing:
            if finding.id not in desired_ids:
                batch.delete(finding.reference)
                operations += 1
                commit_if_full()
        if operations:
            batch.commit()

        _project_ref(upload_id).set({
            "status": "analyzed" if summary.get("complete") else "uploaded",
            "latest_analysis_id": analysis_id,
            "updated_at": SERVER_TIMESTAMP,
        }, merge=True)

    return _record_write("analysis history", write)


def record_quality_score(upload_id: str, score: dict[str, Any]) -> bool:
    def write() -> None:
        db = get_db()
        project_ref = db.collection(PROJECTS).document(upload_id)
        score_id = uuid.uuid4().hex
        project_ref.collection(SCORE_HISTORY).document(score_id).set({
            "score": score.get("score"),
            "categories": score.get("categories", {}),
            "metrics": score.get("metrics", {}),
            "coverage": score.get("coverage", {}),
            "created_at": SERVER_TIMESTAMP,
        })
        project_ref.set({
            "latest_quality_score": score.get("score"),
            "latest_score_id": score_id,
            "updated_at": SERVER_TIMESTAMP,
        }, merge=True)

    return _record_write("quality score", write)
