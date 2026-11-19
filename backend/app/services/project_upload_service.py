"""Disk-backed, resumable project uploads."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator

from app.services.upload_service import EXTENSION_MAP, should_skip_path
from app.services import project_cloud_service

logger = logging.getLogger(__name__)

DEFAULT_MAX_PROJECT_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
MIN_CONFIGURED_CHUNK_BYTES = 64 * 1024
MAX_CONFIGURED_CHUNK_BYTES = 16 * 1024 * 1024
MAX_PROJECT_FILES = 10_000
MAX_ANALYSIS_FILES = 200
MAX_ANALYSIS_REQUEST_FILES = 20
MAX_ANALYSIS_BYTES = 64 * 1024 * 1024
MAX_ANALYSIS_FILE_BYTES = 20 * 1024 * 1024
MAX_CONFIGURED_ANALYSIS_BYTES = 128 * 1024 * 1024
MAX_CONFIGURED_ANALYSIS_FILE_BYTES = 20 * 1024 * 1024
_FILE_LOCKS: dict[tuple[str, int], tuple[Any, int]] = {}
_FILE_LOCKS_GUARD = threading.Lock()


@contextmanager
def _serialize_file_operation(session_dir: Path, file_index: int) -> Iterator[None]:
    key = (str(session_dir), file_index)
    with _FILE_LOCKS_GUARD:
        entry = _FILE_LOCKS.get(key)
        lock, users = entry if entry is not None else (threading.Lock(), 0)
        _FILE_LOCKS[key] = (lock, users + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _FILE_LOCKS_GUARD:
            current_lock, users = _FILE_LOCKS[key]
            if users == 1:
                del _FILE_LOCKS[key]
            else:
                _FILE_LOCKS[key] = (current_lock, users - 1)


class ProjectUploadError(ValueError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _positive_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


def max_project_bytes() -> int:
    return _positive_int("MAX_PROJECT_UPLOAD_BYTES", DEFAULT_MAX_PROJECT_BYTES)


def chunk_bytes() -> int:
    value = _positive_int("PROJECT_UPLOAD_CHUNK_BYTES", DEFAULT_CHUNK_BYTES)
    if value < MIN_CONFIGURED_CHUNK_BYTES:
        raise RuntimeError(
            f"PROJECT_UPLOAD_CHUNK_BYTES cannot be less than {MIN_CONFIGURED_CHUNK_BYTES}."
        )
    if value > MAX_CONFIGURED_CHUNK_BYTES:
        raise RuntimeError(
            f"PROJECT_UPLOAD_CHUNK_BYTES cannot exceed {MAX_CONFIGURED_CHUNK_BYTES}."
        )
    return value


def upload_ttl_seconds() -> int:
    return _positive_int("PROJECT_UPLOAD_TTL_HOURS", 24) * 60 * 60


def analysis_limits() -> dict[str, int]:
    maximum_files = _positive_int("PROJECT_MAX_ANALYSIS_FILES", MAX_ANALYSIS_FILES)
    maximum_request_files = _positive_int(
        "PROJECT_MAX_ANALYSIS_REQUEST_FILES",
        MAX_ANALYSIS_REQUEST_FILES,
    )
    maximum_batch_bytes = _positive_int("PROJECT_MAX_ANALYSIS_BATCH_BYTES", MAX_ANALYSIS_BYTES)
    maximum_file_bytes = _positive_int("PROJECT_MAX_ANALYSIS_FILE_BYTES", MAX_ANALYSIS_FILE_BYTES)
    if maximum_files > MAX_ANALYSIS_FILES:
        raise RuntimeError(f"PROJECT_MAX_ANALYSIS_FILES cannot exceed {MAX_ANALYSIS_FILES}.")
    if maximum_request_files > MAX_ANALYSIS_REQUEST_FILES:
        raise RuntimeError(
            "PROJECT_MAX_ANALYSIS_REQUEST_FILES cannot exceed "
            f"{MAX_ANALYSIS_REQUEST_FILES}."
        )
    if maximum_batch_bytes > MAX_CONFIGURED_ANALYSIS_BYTES:
        raise RuntimeError(
            f"PROJECT_MAX_ANALYSIS_BATCH_BYTES cannot exceed {MAX_CONFIGURED_ANALYSIS_BYTES}."
        )
    if maximum_file_bytes > MAX_CONFIGURED_ANALYSIS_FILE_BYTES:
        raise RuntimeError(
            f"PROJECT_MAX_ANALYSIS_FILE_BYTES cannot exceed {MAX_CONFIGURED_ANALYSIS_FILE_BYTES}."
        )
    return {
        "maximum_files": maximum_files,
        "maximum_request_files": min(maximum_files, maximum_request_files),
        "maximum_batch_bytes": maximum_batch_bytes,
        "maximum_file_bytes": maximum_file_bytes,
    }


def additional_skip_dirs() -> set[str]:
    configured = os.getenv("PROJECT_UPLOAD_SKIP_DIRS", "")
    return {
        name.strip()
        for name in configured.split(",")
        if name.strip() and "/" not in name and "\\" not in name
    }


def storage_root() -> Path:
    configured = os.getenv("PROJECT_UPLOAD_TEMP_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "code-review-agent-uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _session_dir(upload_id: str) -> Path:
    try:
        normalized_id = str(uuid.UUID(upload_id))
    except ValueError as exc:
        raise ProjectUploadError("Upload session was not found.", 404) from exc
    return storage_root() / normalized_id


def _manifest_path(session_dir: Path) -> Path:
    return session_dir / "manifest.json"


def _state_path(session_dir: Path, name: str) -> Path:
    return session_dir / name


def _read_state(session_dir: Path, name: str) -> dict[str, Any]:
    path = _state_path(session_dir, name)
    try:
        with path.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read project state at %s: %s", path, exc)
        raise ProjectUploadError("Project analysis state is unavailable.", 500) from exc
    if not isinstance(state, dict):
        raise ProjectUploadError("Project analysis state is unavailable.", 500)
    return state


def _write_state(session_dir: Path, name: str, state: dict[str, Any]) -> None:
    path = _state_path(session_dir, name)
    temporary_path = session_dir / f"{name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary_path.open("w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        logger.error("Could not persist project state at %s: %s", path, exc)
        raise ProjectUploadError("Could not save project analysis state.", 507) from exc


def _read_manifest(session_dir: Path) -> dict[str, Any]:
    try:
        with _manifest_path(session_dir).open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except FileNotFoundError as exc:
        raise ProjectUploadError("Upload session was not found.", 404) from exc
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read project upload metadata at %s: %s", session_dir, exc)
        raise ProjectUploadError("Upload session metadata is unavailable.", 500) from exc
    if not isinstance(manifest, dict):
        raise ProjectUploadError("Upload session metadata is unavailable.", 500)
    return manifest


def _write_manifest(session_dir: Path, manifest: dict[str, Any]) -> None:
    temporary_path = session_dir / "manifest.tmp"
    with temporary_path.open("w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file)
        manifest_file.flush()
        os.fsync(manifest_file.fileno())
    os.replace(temporary_path, _manifest_path(session_dir))


def _session_for_user(upload_id: str, user_id: str) -> tuple[Path, dict[str, Any]]:
    session_dir = _session_dir(upload_id)
    manifest = _read_manifest(session_dir)
    if manifest.get("owner_id") != user_id:
        raise ProjectUploadError("Upload session was not found.", 404)
    if manifest.get("expires_at", 0) <= time.time():
        shutil.rmtree(session_dir)
        raise ProjectUploadError("Upload session expired. Start a new upload.", 410)
    return session_dir, manifest


def _safe_relative_path(raw_path: str) -> str:
    if not raw_path or len(raw_path) > 1024 or "\\" in raw_path:
        raise ProjectUploadError("A project file contains an invalid relative path.")
    if any(ord(char) < 32 for char in raw_path) or re.match(r"^[A-Za-z]:", raw_path):
        raise ProjectUploadError("A project file contains an invalid relative path.")
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or not path.parts
        or raw_path.endswith("/")
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ProjectUploadError("A project file contains an invalid relative path.")
    if any(
        part.endswith((" ", "."))
        or any(char in '<>:"|?*' for char in part)
        or part.upper().split(".")[0] in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }
        for part in path.parts
    ):
        raise ProjectUploadError("A project file contains an invalid relative path.")
    return path.as_posix()


def cleanup_expired_uploads() -> int:
    root = storage_root()
    now = time.time()
    ttl = upload_ttl_seconds()
    removed = 0
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        try:
            manifest = _read_manifest(session_dir)
            expired = manifest.get("expires_at", 0) <= now or now - session_dir.stat().st_mtime > ttl
        except ProjectUploadError as exc:
            if exc.status_code == 404:
                continue
            logger.warning("Skipping cleanup for project upload %s: %s", session_dir.name, exc.detail)
            continue
        except OSError as exc:
            logger.warning("Could not inspect project upload %s: %s", session_dir.name, exc)
            continue
        if expired:
            try:
                shutil.rmtree(session_dir)
                removed += 1
            except OSError as exc:
                logger.warning("Could not remove expired project upload %s: %s", session_dir.name, exc)
    return removed


def create_upload(owner_id: str, name: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    if not files:
        raise ProjectUploadError("Select at least one file to upload.")
    if len(files) > MAX_PROJECT_FILES:
        raise ProjectUploadError(f"Projects may contain at most {MAX_PROJECT_FILES:,} files.", 413)

    max_bytes = max_project_bytes()
    normalized_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_directories: set[str] = set()
    total_bytes = 0
    for file_info in files:
        path = _safe_relative_path(str(file_info.get("path", "")))
        folded_path = path.casefold()
        parts = PurePosixPath(path).parts
        parent_paths = {
            "/".join(parts[:part_count]).casefold()
            for part_count in range(1, len(parts))
        }
        if folded_path in seen_paths or folded_path in seen_directories:
            raise ProjectUploadError(f"Conflicting project path: {path}")
        if parent_paths & seen_paths:
            raise ProjectUploadError(f"Conflicting project path: {path}")
        seen_paths.add(folded_path)
        seen_directories.update(parent_paths)
        size = file_info.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ProjectUploadError(f"Invalid file size for '{path}'.")
        total_bytes += size
        if total_bytes > max_bytes:
            raise ProjectUploadError(
                f"Project exceeds the configured {max_bytes // (1024 * 1024)} MB upload limit.",
                413,
            )
        normalized_files.append({"path": path, "size": size})

    try:
        cleanup_expired_uploads()
        root = storage_root()
        free_bytes = shutil.disk_usage(root).free
    except OSError as exc:
        logger.error("Could not check project upload storage space: %s", exc)
        raise ProjectUploadError("Temporary upload storage is unavailable.", 503) from exc
    if free_bytes < total_bytes:
        raise ProjectUploadError("Not enough temporary disk space for this project upload.", 507)

    upload_id = str(uuid.uuid4())
    session_dir = root / upload_id
    project_dir = session_dir / "project"
    chunks_dir = session_dir / "chunks"
    try:
        session_dir.mkdir(mode=0o700)
        project_dir.mkdir()
        chunks_dir.mkdir()
    except OSError as exc:
        logger.error("Could not create project upload storage: %s", exc)
        shutil.rmtree(session_dir, ignore_errors=True)
        raise ProjectUploadError("Could not create temporary upload storage.", 507) from exc
    now = time.time()
    manifest = {
        "owner_id": owner_id,
        "name": Path(name or "project").name[:200],
        "created_at": now,
        "expires_at": now + upload_ttl_seconds(),
        "complete": False,
        "files": normalized_files,
    }
    try:
        _write_manifest(session_dir, manifest)
        for file_info in normalized_files:
            if file_info["size"] == 0:
                destination = project_dir.joinpath(*PurePosixPath(file_info["path"]).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.touch()
        manifest["firebase_metadata_persisted"] = project_cloud_service.record_upload_created(
            upload_id,
            owner_id,
            manifest,
        )
        _write_manifest(session_dir, manifest)
    except OSError as exc:
        logger.error("Could not initialize project upload metadata: %s", exc)
        shutil.rmtree(session_dir, ignore_errors=True)
        raise ProjectUploadError("Could not initialize temporary upload storage.", 507) from exc
    return upload_status(upload_id, owner_id)


def create_upload_from_archive(owner_id: str, name: str, archive_bytes: bytes) -> dict[str, Any]:
    from app.services.upload_service import read_zip_archive

    archive_skipped_files: list[dict[str, str]] = []
    try:
        source_files = read_zip_archive(
            archive_bytes,
            archive_skipped_files,
            additional_skip_dirs(),
        )
    except (
        ValueError,
        OSError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
    ) as exc:
        raise ProjectUploadError(str(exc), 422) from exc

    status = create_upload(
        owner_id,
        Path(name).stem or "project",
        [
            {"path": source.path, "size": len(source.content.encode("utf-8"))}
            for source in source_files
        ],
    )
    upload_id = status["upload_id"]
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    try:
        for source, file_info in zip(source_files, manifest["files"]):
            destination = session_dir / "project" / Path(*PurePosixPath(file_info["path"]).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.content.encode("utf-8"))
        manifest["archive_skipped_files"] = archive_skipped_files
        stored_in_cloud = project_cloud_service.upload_project_files(
            upload_id,
            owner_id,
            session_dir,
            manifest,
        )
        manifest["complete"] = True
        manifest["storage_backend"] = "firebase_storage" if stored_in_cloud else "local"
        _write_manifest(session_dir, manifest)
        project_cloud_service.record_upload_complete(upload_id, stored_in_cloud)
    except OSError as exc:
        logger.error("Could not extract project archive into temporary storage: %s", exc)
        shutil.rmtree(session_dir, ignore_errors=True)
        raise ProjectUploadError("Could not store the extracted project archive.", 507) from exc
    except Exception as exc:
        logger.exception("Could not complete cloud storage for project archive %s.", upload_id)
        raise ProjectUploadError(
            "The archive was extracted locally, but Firebase Cloud Storage could not store it. "
            "Retry after checking the Firebase Storage bucket configuration.",
            503,
        ) from exc
    return project_files(upload_id, owner_id)


def create_upload_from_archive_file(
    owner_id: str,
    name: str,
    archive_file: BinaryIO,
) -> dict[str, Any]:
    from app.services.upload_service import (
        MAX_FILE_IN_ZIP,
        MAX_TOTAL_BYTES,
        MAX_ZIP_BYTES,
        zip_source_entries,
    )

    upload_id = ""
    session_dir: Path | None = None
    archive_skipped_files: list[dict[str, str]] = []
    try:
        archive_file.seek(0, os.SEEK_END)
        archive_size = archive_file.tell()
        if archive_size > MAX_ZIP_BYTES:
            raise ProjectUploadError(
                f"ZIP file exceeds the {MAX_ZIP_BYTES // (1024 * 1024)} MB upload limit.",
                413,
            )
        archive_file.seek(0)
        if not zipfile.is_zipfile(archive_file):
            raise ProjectUploadError("Uploaded file is not a valid ZIP archive.", 422)
        archive_file.seek(0)

        with zipfile.ZipFile(archive_file) as archive:
            try:
                entries = zip_source_entries(
                    archive,
                    archive_skipped_files,
                    additional_skip_dirs(),
                )
            except ValueError as exc:
                raise ProjectUploadError(str(exc), 422) from exc
            status = create_upload(
                owner_id,
                Path(name).stem or "project",
                [{"path": path, "size": info.file_size} for info, path, _ in entries],
            )
            upload_id = status["upload_id"]
            session_dir, manifest = _session_for_user(upload_id, owner_id)
            manifest["archive_skipped_files"] = archive_skipped_files
            _write_manifest(session_dir, manifest)
            total_written = 0
            for index, (info, path, _) in enumerate(entries):
                destination = session_dir / "project" / Path(*PurePosixPath(path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(info, "r") as source, destination.open("wb") as target:
                    while block := source.read(1024 * 1024):
                        written += len(block)
                        total_written += len(block)
                        if written > MAX_FILE_IN_ZIP or total_written > MAX_TOTAL_BYTES:
                            raise ProjectUploadError(
                                "ZIP archive expanded beyond the validated source-file limits.",
                                413,
                            )
                        target.write(block)
                if written != manifest["files"][index]["size"]:
                    raise ProjectUploadError(
                        f"ZIP entry size did not match its header: {path}.",
                        422,
                    )

        archive_file.seek(0)
        try:
            archive_storage_path = project_cloud_service.upload_project_archive(
                upload_id,
                owner_id,
                archive_file,
            )
            stored_in_cloud = project_cloud_service.upload_project_files(
                upload_id,
                owner_id,
                session_dir,
                manifest,
            )
        except Exception as exc:
            raise ProjectUploadError(
                "The archive could not be stored in Firebase. Check the Storage bucket configuration and retry.",
                503,
            ) from exc
        manifest["complete"] = True
        manifest["storage_backend"] = "firebase_storage" if stored_in_cloud else "local"
        if archive_storage_path:
            manifest["archive_storage_path"] = archive_storage_path
        _write_manifest(session_dir, manifest)
        manifest["firebase_metadata_persisted"] = project_cloud_service.record_upload_complete(
            upload_id,
            stored_in_cloud,
        )
        _write_manifest(session_dir, manifest)
    except ProjectUploadError:
        if upload_id:
            project_cloud_service.record_upload_failure(upload_id)
            try:
                project_cloud_service.delete_project_objects(upload_id, owner_id)
            except Exception:
                logger.exception("Could not remove partial cloud objects for project %s.", upload_id)
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise
    except OSError as exc:
        if upload_id:
            project_cloud_service.record_upload_failure(upload_id)
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        logger.exception("Could not store project archive in temporary upload storage.")
        raise ProjectUploadError(
            "Could not store the extracted project archive. Check temporary storage capacity.",
            507,
        ) from exc
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
    ) as exc:
        if upload_id:
            project_cloud_service.record_upload_failure(upload_id)
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        logger.exception("Could not process project archive.")
        raise ProjectUploadError("Could not safely read the project ZIP archive.", 422) from exc
    except Exception as exc:
        if upload_id:
            project_cloud_service.record_upload_failure(upload_id)
            try:
                project_cloud_service.delete_project_objects(upload_id, owner_id)
            except Exception:
                logger.exception("Could not remove partial cloud objects for project %s.", upload_id)
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        logger.exception("Could not store project archive in Firebase Cloud Storage.")
        raise ProjectUploadError(
            "The archive could not be stored in Firebase. Check the Storage bucket configuration and retry.",
            503,
        ) from exc

    return project_files(upload_id, owner_id)


def _file_is_complete(session_dir: Path, index: int, file_info: dict[str, Any]) -> bool:
    destination = session_dir / "project" / Path(*PurePosixPath(file_info["path"]).parts)
    try:
        return destination.is_file() and destination.stat().st_size == file_info["size"]
    except OSError:
        return False


def _received_chunks(session_dir: Path, index: int, size: int) -> list[int]:
    if size == 0:
        return []
    count = (size + chunk_bytes() - 1) // chunk_bytes()
    directory = session_dir / "chunks" / str(index)
    return [
        chunk_index
        for chunk_index in range(count)
        if (directory / f"{chunk_index}.part").is_file()
    ]


def upload_status(upload_id: str, owner_id: str) -> dict[str, Any]:
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    file_statuses = []
    for index, file_info in enumerate(manifest["files"]):
        completed = _file_is_complete(session_dir, index, file_info)
        file_statuses.append({
            "index": index,
            "path": file_info["path"],
            "size": file_info["size"],
            "complete": completed,
            "received_chunks": [] if completed else _received_chunks(session_dir, index, file_info["size"]),
        })
    complete = all(file_info["complete"] for file_info in file_statuses)
    return {
        "upload_id": str(uuid.UUID(upload_id)),
        "name": manifest["name"],
        "storage_backend": manifest.get("storage_backend", "local"),
        "metadata_persisted": manifest.get("firebase_metadata_persisted", False),
        "total_bytes": sum(file_info["size"] for file_info in manifest["files"]),
        "complete": complete,
        "chunk_bytes": chunk_bytes(),
        "files": file_statuses,
    }


def _assemble_file(session_dir: Path, index: int, file_info: dict[str, Any]) -> None:
    size = file_info["size"]
    destination = session_dir / "project" / Path(*PurePosixPath(file_info["path"]).parts)
    chunk_directory = session_dir / "chunks" / str(index)
    chunk_size = chunk_bytes()
    count = (size + chunk_size - 1) // chunk_size
    if count == 0:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch(exist_ok=True)
        return
    try:
        free_bytes = shutil.disk_usage(session_dir).free
    except OSError as exc:
        logger.error("Could not check disk space while assembling project file: %s", exc)
        raise ProjectUploadError("Temporary upload storage is unavailable.", 503) from exc
    if free_bytes < size:
        raise ProjectUploadError("Not enough temporary disk space to assemble this file.", 507)

    temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("wb") as target:
            for chunk_index in range(count):
                part_path = chunk_directory / f"{chunk_index}.part"
                expected_size = min(chunk_size, size - chunk_index * chunk_size)
                if not part_path.is_file() or part_path.stat().st_size != expected_size:
                    raise ProjectUploadError("Upload is missing or has an invalid chunk.", 409)
                with part_path.open("rb") as part:
                    shutil.copyfileobj(part, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if temporary_path.stat().st_size != size:
            raise ProjectUploadError("Assembled file size does not match the upload manifest.", 409)
        os.replace(temporary_path, destination)
    except OSError as exc:
        logger.error("Could not assemble project upload file %s: %s", file_info["path"], exc)
        raise ProjectUploadError("Could not assemble the uploaded file. Check temporary storage.", 507) from exc
    finally:
        temporary_path.unlink(missing_ok=True)
    shutil.rmtree(chunk_directory, ignore_errors=False)


def save_chunk(
    upload_id: str,
    owner_id: str,
    file_index: int,
    chunk_index: int,
    data: bytes,
    expected_sha256: str,
) -> dict[str, Any]:
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    if file_index < 0 or file_index >= len(manifest["files"]):
        raise ProjectUploadError("Project file was not found.", 404)
    with _serialize_file_operation(session_dir, file_index):
        return _save_chunk_locked(
            upload_id,
            owner_id,
            file_index,
            chunk_index,
            data,
            expected_sha256,
        )


def _save_chunk_locked(
    upload_id: str,
    owner_id: str,
    file_index: int,
    chunk_index: int,
    data: bytes,
    expected_sha256: str,
) -> dict[str, Any]:
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    if file_index < 0 or file_index >= len(manifest["files"]):
        raise ProjectUploadError("Project file was not found.", 404)
    file_info = manifest["files"][file_index]
    if _file_is_complete(session_dir, file_index, file_info):
        raise ProjectUploadError("This project file is already complete.", 409)
    current_chunk_bytes = chunk_bytes()
    chunk_count = (file_info["size"] + current_chunk_bytes - 1) // current_chunk_bytes
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise ProjectUploadError("Chunk index is outside the expected range.")
    expected_size = min(current_chunk_bytes, file_info["size"] - chunk_index * current_chunk_bytes)
    if len(data) != expected_size:
        raise ProjectUploadError("Chunk size does not match the expected size.", 400)
    digest = hashlib.sha256(data).hexdigest()
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256) or digest.lower() != expected_sha256.lower():
        raise ProjectUploadError("Chunk checksum verification failed.", 400)

    chunk_directory = session_dir / "chunks" / str(file_index)
    chunk_path = chunk_directory / f"{chunk_index}.part"
    try:
        chunk_directory.mkdir(parents=True, exist_ok=True)
        with chunk_path.open("xb") as chunk_file:
            chunk_file.write(data)
            chunk_file.flush()
            os.fsync(chunk_file.fileno())
    except FileExistsError:
        try:
            existing_digest = hashlib.sha256(chunk_path.read_bytes()).hexdigest()
        except OSError as exc:
            logger.error("Could not verify an existing project upload chunk: %s", exc)
            raise ProjectUploadError("Could not verify the previously received chunk.", 500) from exc
        if existing_digest != digest:
            raise ProjectUploadError("A different chunk already exists at this index.", 409)
        if len(_received_chunks(session_dir, file_index, file_info["size"])) == chunk_count:
            _assemble_file(session_dir, file_index, file_info)
        return {
            "received": True,
            "duplicate": True,
            "file_complete": _file_is_complete(session_dir, file_index, file_info),
        }
    except OSError as exc:
        chunk_path.unlink(missing_ok=True)
        logger.error("Could not write project upload chunk: %s", exc)
        raise ProjectUploadError("Could not save this upload chunk. Check temporary storage.", 507) from exc

    if len(_received_chunks(session_dir, file_index, file_info["size"])) == chunk_count:
        _assemble_file(session_dir, file_index, file_info)
    complete = _file_is_complete(session_dir, file_index, file_info)
    return {"received": True, "duplicate": False, "file_complete": complete}


def complete_upload(upload_id: str, owner_id: str) -> dict[str, Any]:
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    for index, file_info in enumerate(manifest["files"]):
        if not _file_is_complete(session_dir, index, file_info):
            chunk_count = (file_info["size"] + chunk_bytes() - 1) // chunk_bytes()
            received_chunks = _received_chunks(session_dir, index, file_info["size"])
            if len(received_chunks) != chunk_count:
                raise ProjectUploadError(
                    "Upload is incomplete; one or more chunks are missing.",
                    409,
                )
            _assemble_file(session_dir, index, file_info)
            if not _file_is_complete(session_dir, index, file_info):
                raise ProjectUploadError("Uploaded file could not be finalized.", 409)
    manifest["complete"] = True
    try:
        stored_in_cloud = project_cloud_service.upload_project_files(
            upload_id,
            owner_id,
            session_dir,
            manifest,
        )
        manifest["storage_backend"] = "firebase_storage" if stored_in_cloud else "local"
        _write_manifest(session_dir, manifest)
    except OSError as exc:
        logger.error("Could not mark project upload complete: %s", exc)
        raise ProjectUploadError("Could not finalize project upload metadata.", 507) from exc
    except Exception as exc:
        logger.exception("Could not store uploaded project %s in Firebase Storage.", upload_id)
        raise ProjectUploadError(
            "Project files were uploaded locally, but Firebase Cloud Storage could not store them. "
            "Retry after checking the Firebase Storage bucket configuration.",
            503,
        ) from exc
    manifest["firebase_metadata_persisted"] = project_cloud_service.record_upload_complete(
        upload_id,
        stored_in_cloud,
    )
    try:
        _write_manifest(session_dir, manifest)
    except OSError as exc:
        logger.error("Could not persist project upload status: %s", exc)
        raise ProjectUploadError("Could not finalize project upload metadata.", 507) from exc
    return upload_status(upload_id, owner_id)


def _source_skip_reason(session_dir: Path, index: int, file_info: dict[str, Any]) -> str | None:
    path = PurePosixPath(file_info["path"])
    if should_skip_path(path, additional_skip_dirs()):
        return "Generated, dependency, or hidden paths are skipped."
    if path.suffix.lower() not in EXTENSION_MAP:
        return "Unsupported source file type."
    max_file_bytes = analysis_limits()["maximum_file_bytes"]
    if file_info["size"] > max_file_bytes:
        return f"File exceeds the {max_file_bytes // (1024 * 1024)} MB analysis-file limit."
    disk_path = session_dir / "project" / Path(*path.parts)
    try:
        with disk_path.open("rb") as source_file:
            preview = source_file.read(8192)
    except OSError:
        return "Uploaded file is unavailable."
    if b"\x00" in preview:
        return "Binary files are skipped."
    return None


def project_files(upload_id: str, owner_id: str) -> dict[str, Any]:
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    if not manifest.get("complete"):
        raise ProjectUploadError("Finish uploading before listing project files.", 409)
    files = []
    for index, file_info in enumerate(manifest["files"]):
        reason = _source_skip_reason(session_dir, index, file_info)
        files.append({
            "index": index,
            "path": file_info["path"],
            "size": file_info["size"],
            "language": EXTENSION_MAP.get(PurePosixPath(file_info["path"]).suffix.lower()),
            "selectable": reason is None,
            "skip_reason": reason,
        })
    return {
        "upload_id": str(uuid.UUID(upload_id)),
        "name": manifest["name"],
        "storage_backend": manifest.get("storage_backend", "local"),
        "metadata_persisted": manifest.get("firebase_metadata_persisted", False),
        "total_files": len(files),
        "selectable_files": sum(file_info["selectable"] for file_info in files),
        "excluded_files": manifest.get("archive_skipped_files", []),
        "analysis_limits": analysis_limits(),
        "files": files,
    }


def analyze_project_files(
    upload_id: str,
    owner_id: str,
    file_indices: list[int],
    reset: bool = False,
    selected_file_indices: list[int] | None = None,
) -> dict[str, Any]:
    from app.services.ai_reviewer import run_ai_review
    from app.services.code_analyzer import analyze_code
    from app.services.firebase_service import firebase_available, get_team_standards_for_user

    session_dir, manifest = _session_for_user(upload_id, owner_id)
    if not manifest.get("complete"):
        raise ProjectUploadError("Finish uploading before analyzing project files.", 409)
    if not file_indices:
        raise ProjectUploadError("Select at least one supported source file.")
    limits = analysis_limits()
    if (
        len(file_indices) > limits["maximum_request_files"]
        or len(set(file_indices)) != len(file_indices)
    ):
        raise ProjectUploadError(
            f"Analyze between 1 and {limits['maximum_request_files']} files per request."
        )

    analysis_state: dict[str, Any]
    with _serialize_file_operation(session_dir, -1):
        analysis_state = _read_state(session_dir, "analysis.json")
        if reset:
            if not selected_file_indices:
                raise ProjectUploadError("Select project files before starting analysis.")
            if (
                len(selected_file_indices) > limits["maximum_files"]
                or len(set(selected_file_indices)) != len(selected_file_indices)
                or not set(file_indices).issubset(selected_file_indices)
            ):
                raise ProjectUploadError("The analysis selection is invalid.")
            for index in selected_file_indices:
                if index < 0 or index >= len(manifest["files"]):
                    raise ProjectUploadError("A selected project file does not exist.")
            analysis_state = {
                "run_id": str(uuid.uuid4()),
                "selected_indices": sorted(selected_file_indices),
                "processed_indices": [],
                "results": {},
                "errors": [],
                "skipped_files": list(manifest.get("archive_skipped_files", [])),
            }
        elif not analysis_state:
            raise ProjectUploadError("Start a new analysis before submitting another batch.", 409)
        else:
            analysis_state.setdefault("run_id", str(uuid.uuid4()))

        selected_indices = set(analysis_state.get("selected_indices", []))
        if not set(file_indices).issubset(selected_indices):
            raise ProjectUploadError("This file is not part of the current analysis selection.")

    results: list[dict[str, Any]] = []
    skipped_files = []
    errors = []
    selected_bytes = 0
    team_standards = []
    if firebase_available():
        try:
            team_standards = [
                standard
                for standard in get_team_standards_for_user(owner_id, manifest.get("name", "global"))
                if standard.get("enabled", True)
            ]
        except Exception:
            logger.exception("Could not load team standards for project analysis.")
    standards_digest = hashlib.sha256(
        json.dumps(team_standards, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    analysis_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cached_result in analysis_state.get("results", {}).values():
        cache_key = (
            str(cached_result.get("language", "")),
            str(cached_result.get("source_sha256", "")),
            str(cached_result.get("standards_sha256", "")),
        )
        if all(cache_key):
            analysis_cache.setdefault(cache_key, cached_result)

    for index in file_indices:
        file_info = manifest["files"][index]
        reason = _source_skip_reason(session_dir, index, file_info)
        if reason:
            skipped_files.append({"path": file_info["path"], "reason": reason})
            continue
        selected_bytes += file_info["size"]
        if selected_bytes > limits["maximum_batch_bytes"]:
            raise ProjectUploadError(
                "Selected files exceed the "
                f"{limits['maximum_batch_bytes'] // (1024 * 1024)} MB analysis-batch limit."
            )
        path = PurePosixPath(file_info["path"])
        disk_path = session_dir / "project" / Path(*path.parts)
        try:
            with disk_path.open("rb") as source_file:
                raw = source_file.read(limits["maximum_file_bytes"] + 1)
        except OSError as exc:
            logger.error("Could not read selected project file %s: %s", file_info["path"], exc)
            errors.append({"path": file_info["path"], "error": "Uploaded file could not be read."})
            continue
        if len(raw) > limits["maximum_file_bytes"]:
            skipped_files.append({
                "path": file_info["path"],
                "reason": (
                    "File exceeds the "
                    f"{limits['maximum_file_bytes'] // (1024 * 1024)} MB analysis-file limit."
                ),
            })
            continue
        if b"\x00" in raw:
            skipped_files.append({"path": file_info["path"], "reason": "Binary files are skipped."})
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("latin-1")
        language = EXTENSION_MAP[path.suffix.lower()]
        source_digest = hashlib.sha256(raw).hexdigest()
        cache_key = (language, source_digest, standards_digest)
        cached_result = analysis_cache.get(cache_key)
        if cached_result is not None:
            results.append({
                **cached_result,
                "file_index": index,
                "path": file_info["path"],
                "source_sha256": source_digest,
                "duplicate_of": (
                    cached_result.get("duplicate_of")
                    or cached_result.get("path", file_info["path"])
                ),
                "analysis_reused": True,
            })
            continue
        try:
            static_result = analyze_code(
                language=language,
                code=content,
                team_standards=team_standards,
            )
            static_issues = static_result.get("issues", [])
            static_issues_for_ai = [
                {
                    **issue,
                    "title": str(issue.get("title", ""))[:200],
                    "description": str(issue.get("description", ""))[:500],
                    "recommendation": str(issue.get("recommendation", ""))[:500],
                }
                for issue in static_issues[:40]
            ]
            ai_result = run_ai_review(
                code=content,
                language=language,
                project_name=manifest["name"],
                static_issues=static_issues_for_ai,
            )
            ai_issues = ai_result.get("issues", [])
            combined: dict[tuple[str, int], dict[str, Any]] = {}
            for issue in [*static_issues, *ai_issues]:
                key = (
                    str(issue.get("title", "")).strip().lower(),
                    int(issue.get("line") or 0),
                )
                current = combined.get(key)
                if current is None or (
                    current.get("source") == "ai"
                    and issue.get("source") != "ai"
                ):
                    combined[key] = issue
            result = {
                "file_index": index,
                "path": file_info["path"],
                "language": language,
                "issues": list(combined.values()),
                "static_issues": static_issues,
                "ai_issues": ai_issues,
                "ai_available": ai_result.get("ai_available", False),
                "ai_failure_reason": ai_result.get("fallback_reason"),
                "ai_failure_category": ai_result.get("failure_category"),
                "ai_code_truncated": len(content) > 12_000,
                "summary": ai_result.get("summary", ""),
                "source_sha256": source_digest,
                "standards_sha256": standards_digest,
                "analysis_reused": False,
            }
            results.append(result)
            analysis_cache.setdefault(cache_key, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not analyze project file %s", file_info["path"])
            errors.append({"path": file_info["path"], "error": "Analysis failed for this file."})

    with _serialize_file_operation(session_dir, -1):
        latest_state = _read_state(session_dir, "analysis.json")
        if reset:
            latest_state = analysis_state
        processed = set(latest_state.get("processed_indices", []))
        existing_results = latest_state.get("results", {})
        existing_errors = latest_state.get("errors", [])
        existing_skipped = latest_state.get("skipped_files", [])
        for result in results:
            key = str(result["file_index"])
            existing_results[key] = result
            processed.add(result["file_index"])
        for error in errors:
            index = next(
                (file_index for file_index in file_indices
                 if manifest["files"][file_index]["path"] == error["path"]),
                None,
            )
            if index is not None:
                processed.add(index)
        for skipped in skipped_files:
            index = next(
                (file_index for file_index in file_indices
                 if manifest["files"][file_index]["path"] == skipped["path"]),
                None,
            )
            if index is not None:
                processed.add(index)
        latest_state.update(
            processed_indices=sorted(processed),
            results=existing_results,
            errors=[item for item in existing_errors if item["path"] not in {e["path"] for e in errors}]
            + errors,
            skipped_files=[
                item for item in existing_skipped
                if item["path"] not in {skipped["path"] for skipped in skipped_files}
            ] + skipped_files,
        )
        selected_indices = latest_state.get("selected_indices", [])
        latest_state["complete"] = set(selected_indices).issubset(processed)
        _write_state(session_dir, "analysis.json", latest_state)

    combined_results = [
        latest_state["results"][str(index)]
        for index in sorted(latest_state.get("results", {}), key=int)
    ]
    summary = {
            "selected": len(selected_indices),
            "analyzed": len(combined_results),
            "skipped": len(latest_state.get("skipped_files", [])),
            "not_selected": max(0, len(manifest["files"]) - len(selected_indices)),
            "skipped_files": latest_state.get("skipped_files", []),
            "errors": latest_state.get("errors", []),
            "complete": latest_state["complete"],
            "languages": sorted({result["language"] for result in combined_results}),
            "reused": sum(bool(result.get("analysis_reused")) for result in combined_results),
            "duplicate_files": sum(bool(result.get("duplicate_of")) for result in combined_results),
            "ai_calls_limited_to_selected_files": True,
            "maximum_files_per_batch": limits["maximum_request_files"],
            "maximum_source_bytes_per_batch": limits["maximum_batch_bytes"],
        }
    summary["history_persisted"] = project_cloud_service.record_analysis(
        upload_id,
        latest_state["run_id"],
        combined_results,
        summary,
    )
    return {
        "results": results,
        "summary": summary,
    }


def project_quality_score(upload_id: str, owner_id: str) -> dict[str, Any]:
    from app.services.quality_score import calculate_project_quality_score

    session_dir, manifest = _session_for_user(upload_id, owner_id)
    with _serialize_file_operation(session_dir, -1):
        analysis_state = _read_state(session_dir, "analysis.json")
    if not analysis_state or not analysis_state.get("complete"):
        raise ProjectUploadError("Complete project analysis before checking its quality score.", 409)

    def scored_files() -> Iterator[dict[str, Any]]:
        for key, result in analysis_state.get("results", {}).items():
            file_index = int(key)
            file_info = manifest["files"][file_index]
            path = PurePosixPath(file_info["path"])
            source_path = session_dir / "project" / Path(*path.parts)
            try:
                raw = source_path.read_bytes()
            except OSError as exc:
                logger.error("Could not load analyzed file %s for scoring: %s", file_info["path"], exc)
                raise ProjectUploadError("An analyzed project file is no longer available.", 409) from exc
            if hashlib.sha256(raw).hexdigest() != result["source_sha256"]:
                raise ProjectUploadError("Project files changed; reanalyze before checking the score.", 409)
            try:
                code = raw.decode("utf-8")
            except UnicodeDecodeError:
                code = raw.decode("latin-1")
            yield {
                "path": file_info["path"],
                "language": result["language"],
                "code": code,
                "static_issues": result["static_issues"],
                "ai_issues": result["ai_issues"],
            }

    if not analysis_state.get("results"):
        raise ProjectUploadError("No project files completed analysis, so a score is unavailable.", 409)

    score = calculate_project_quality_score(scored_files())
    score["coverage"] = {
        "selected": len(analysis_state.get("selected_indices", [])),
        "analyzed": score["metrics"]["analyzed_files"],
        "failed": len(analysis_state.get("errors", [])),
        "skipped": len(analysis_state.get("skipped_files", [])),
    }
    persisted = project_cloud_service.record_quality_score(
        upload_id,
        score,
        analysis_state.get("run_id"),
    )
    return {
        **score,
        "history_persisted": persisted,
    }


def source_files_available(upload_id: str, owner_id: str) -> bool:
    try:
        session_dir, manifest = _session_for_user(upload_id, owner_id)
    except ProjectUploadError as exc:
        if exc.status_code in (404, 410):
            return False
        raise
    return all(
        _file_is_complete(session_dir, index, file_info)
        for index, file_info in enumerate(manifest.get("files", []))
    )


def create_fix_suggestion(
    upload_id: str,
    owner_id: str,
    file_index: int,
    issue_index: int,
) -> dict[str, Any]:
    from app.services.auto_fix_service import auto_fix_finding
    from app.services.code_analyzer import analyze_code
    from app.services.firebase_service import firebase_available, get_team_standards_for_user

    session_dir, manifest = _session_for_user(upload_id, owner_id)
    if not manifest.get("complete"):
        raise ProjectUploadError("Finish uploading before generating a fix.", 409)
    with _serialize_file_operation(session_dir, -1):
        analysis_state = _read_state(session_dir, "analysis.json")
    stored = analysis_state.get("results", {}).get(str(file_index))
    if not stored:
        raise ProjectUploadError("Analyze this project file before requesting a fix.", 409)
    issues = stored.get("issues", [])
    if issue_index < 0 or issue_index >= len(issues):
        raise ProjectUploadError("The selected finding was not found.", 404)

    file_info = manifest["files"][file_index]
    path = PurePosixPath(file_info["path"])
    source_path = session_dir / "project" / Path(*path.parts)
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        logger.error("Could not read project source for fix proposal %s: %s", file_info["path"], exc)
        raise ProjectUploadError("The selected project file is unavailable.", 409) from exc
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if source_sha256 != stored["source_sha256"]:
        raise ProjectUploadError("Project files changed; reanalyze before generating a fix.", 409)
    try:
        code = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        code = raw.decode("latin-1")
        encoding = "latin-1"

    issue = issues[issue_index]
    finding_id = str(issue.get("finding_id") or issue.get("id") or f"{file_index}:{issue_index}")
    proposal = auto_fix_finding(
        code=code,
        language=stored["language"],
        issue=issue,
        filename=file_info["path"],
        finding_id=finding_id,
    )
    if not proposal.get("ai_available"):
        raise ProjectUploadError(
            proposal.get("error") or "AI fix generation is unavailable. The original file was not changed.",
            503,
        )
    proposed_code = proposal["fixed_code"]
    original_region = proposal["original_region"]
    replacement_region = proposal["replacement_region"]
    patch_start = proposal["patch_start"]
    patch_end = proposal["patch_end"]
    if (
        code[patch_start:patch_end] != original_region
        or code[:patch_start] + replacement_region + code[patch_end:] != proposed_code
    ):
        raise ProjectUploadError(
            "The generated fix could not be mapped safely to the original source. "
            "The uploaded file was not changed.",
            422,
        )
    team_standards = []
    if firebase_available():
        try:
            team_standards = [
                standard
                for standard in get_team_standards_for_user(owner_id, manifest.get("name", "global"))
                if standard.get("enabled", True)
            ]
        except Exception:
            logger.exception("Could not load team standards when validating a fix proposal.")
    try:
        if team_standards:
            before_check = analyze_code(stored["language"], code, team_standards=team_standards)
            after_check = analyze_code(stored["language"], proposed_code, team_standards=team_standards)
        else:
            before_check = analyze_code(stored["language"], code)
            after_check = analyze_code(stored["language"], proposed_code)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not validate a proposed fix for %s", file_info["path"])
        raise ProjectUploadError(
            "Could not validate this fix proposal. The uploaded source was not changed.",
            503,
        ) from exc
    static_source = str(issue.get("source", "")).strip().lower() not in {"", "ai"}
    validation_supported = bool(after_check.get("supported") and static_source)
    before_severe = sum(
        issue.get("severity") in {"critical", "error"}
        for issue in before_check.get("issues", [])
    )
    after_severe = sum(
        issue.get("severity") in {"critical", "error"}
        for issue in after_check.get("issues", [])
    )
    target_issue_remains = any(
        str(candidate.get("title", "")).strip().lower()
        == str(issue.get("title", "")).strip().lower()
        and candidate.get("severity") == issue.get("severity")
        for candidate in after_check.get("issues", [])
    )
    validation_passed = (
        after_severe <= before_severe
        and not target_issue_remains
    ) if validation_supported else None
    validation = {
        "supported": validation_supported,
        "passed": validation_passed,
        "message": (
            "The selected static finding was resolved without increasing critical or error findings."
            if validation_supported and validation_passed
            else "The selected finding remains or static checks found additional critical or error findings."
            if validation_supported
            else "The selected finding cannot be independently confirmed by static checks; review the proposal carefully."
        ),
    }

    proposal_id = str(uuid.uuid4())
    with _serialize_file_operation(session_dir, -1):
        proposals = _read_state(session_dir, "fix-suggestions.json")
        proposals[proposal_id] = {
            "file_index": file_index,
            "path": file_info["path"],
            "original_size": len(raw),
            "source_sha256": source_sha256,
            "proposed_code": proposed_code,
            "original_region": original_region,
            "replacement_region": replacement_region,
            "patch_start": patch_start,
            "patch_end": patch_end,
            "patch_start_line": proposal["patch_start_line"],
            "patch_end_line": proposal["patch_end_line"],
            "encoding": encoding,
            "finding_id": finding_id,
            "validation": validation,
            "changed": proposed_code != code,
        }
        _write_state(session_dir, "fix-suggestions.json", proposals)
    return {
        "proposal_id": proposal_id,
        "path": file_info["path"],
        "language": stored["language"],
        "issue": issue,
        "finding_id": finding_id,
        "original_code": original_region,
        "proposed_code": replacement_region,
        "line_range": {
            "start": proposal["patch_start_line"],
            "end": proposal["patch_end_line"],
        },
        "summary": proposal.get("summary", ""),
        "validation": validation,
        "changed": proposed_code != code,
    }


def apply_fix_suggestion(
    upload_id: str,
    owner_id: str,
    proposal_id: str,
    allow_validation_override: bool = False,
) -> dict[str, Any]:
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    with _serialize_file_operation(session_dir, -1):
        proposals = _read_state(session_dir, "fix-suggestions.json")
        proposal = proposals.get(proposal_id)
        if not proposal:
            raise ProjectUploadError("Fix proposal was not found.", 404)
        if proposal.get("applied"):
            raise ProjectUploadError("This fix proposal has already been applied.", 409)
        if not proposal.get("proposed_code") or not proposal.get("changed"):
            raise ProjectUploadError("This proposal contains no file changes.", 409)
        validation = proposal.get("validation", {})
        if (
            (validation.get("supported") and not validation.get("passed"))
            or not validation.get("supported")
        ) and not allow_validation_override:
            raise ProjectUploadError(
                "This proposal needs explicit approval because static validation did not pass.",
                409,
            )

        relative = PurePosixPath(proposal["path"])
        target = session_dir / "project" / Path(*relative.parts)
        try:
            original = target.read_bytes()
        except OSError as exc:
            logger.error("Could not read source before applying fix %s: %s", proposal["path"], exc)
            raise ProjectUploadError("The selected project file is unavailable.", 409) from exc
        if hashlib.sha256(original).hexdigest() != proposal["source_sha256"]:
            raise ProjectUploadError("Project file changed since this proposal; reanalyze it.", 409)
        try:
            original_text = original.decode(proposal.get("encoding", "utf-8"))
            patch_start = int(proposal["patch_start"])
            patch_end = int(proposal["patch_end"])
            original_region = proposal["original_region"]
            replacement_region = proposal["replacement_region"]
            if (
                patch_start < 0
                or patch_end < patch_start
                or patch_end > len(original_text)
                or original_text[patch_start:patch_end] != original_region
            ):
                raise ValueError("The original replacement region no longer matches.")
            proposed_text = (
                original_text[:patch_start]
                + replacement_region
                + original_text[patch_end:]
            )
            if proposed_text != proposal["proposed_code"]:
                raise ValueError("The stored replacement does not match the validated patch.")
            proposed_bytes = proposed_text.encode(proposal.get("encoding", "utf-8"))
        except UnicodeError as exc:
            raise ProjectUploadError(
                "The proposed correction cannot be represented in the source file's encoding. "
                "The original file was preserved.",
                422,
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectUploadError(
                "The proposed patch no longer maps safely to the expected source region. "
                "The original file was preserved.",
                409,
            ) from exc
        file_limit = analysis_limits()["maximum_file_bytes"]
        if len(proposed_bytes) > file_limit:
            raise ProjectUploadError(
                f"The suggested file exceeds the {file_limit // (1024 * 1024)} MB project analysis limit.",
                413,
            )
        current_project_bytes = sum(item["size"] for item in manifest["files"])
        if current_project_bytes - len(original) + len(proposed_bytes) > max_project_bytes():
            raise ProjectUploadError("The suggested fix exceeds the configured project upload limit.", 413)

        history_dir = session_dir / "history"
        backup_id = f"{uuid.uuid4()}"
        backup_path = history_dir / f"{backup_id}.bak"
        temporary_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        replaced = False
        try:
            history_dir.mkdir(exist_ok=True)
            shutil.copy2(target, backup_path)
            with temporary_path.open("wb") as target_file:
                target_file.write(proposed_bytes)
                target_file.flush()
                os.fsync(target_file.fileno())
            os.replace(temporary_path, target)
            replaced = True
            manifest["files"][proposal["file_index"]]["size"] = len(proposed_bytes)
            _write_manifest(session_dir, manifest)
            proposal["applied"] = True
            proposal["backup_id"] = backup_id
            proposals[proposal_id] = proposal
            _write_state(session_dir, "fix-suggestions.json", proposals)
            _write_state(session_dir, "analysis.json", {})
        except (OSError, ProjectUploadError) as exc:
            temporary_path.unlink(missing_ok=True)
            if replaced:
                rollback_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.rollback")
                try:
                    rollback_path.write_bytes(original)
                    os.replace(rollback_path, target)
                    manifest["files"][proposal["file_index"]]["size"] = len(original)
                    _write_manifest(session_dir, manifest)
                    proposals[proposal_id].pop("applied", None)
                    proposals[proposal_id].pop("backup_id", None)
                    _write_state(session_dir, "fix-suggestions.json", proposals)
                    backup_path.unlink(missing_ok=True)
                except (OSError, ProjectUploadError):
                    rollback_path.unlink(missing_ok=True)
                    logger.exception("Could not roll back an incomplete project fix for %s", proposal["path"])
            logger.error("Could not safely apply project fix to %s: %s", proposal["path"], exc)
            raise ProjectUploadError("Could not apply the fix. The original file was preserved.", 507) from exc

    return {
        "path": proposal["path"],
        "size": len(proposed_bytes),
        "backup_id": backup_id,
        "updated_code": proposed_text,
        "message": "Fix applied to the uploaded project copy; the original is available in change history.",
    }


def restore_project_backup(upload_id: str, owner_id: str, backup_id: str) -> dict[str, Any]:
    try:
        normalized_backup_id = str(uuid.UUID(backup_id))
    except ValueError as exc:
        raise ProjectUploadError("Change-history backup was not found.", 404) from exc
    session_dir, manifest = _session_for_user(upload_id, owner_id)
    with _serialize_file_operation(session_dir, -1):
        proposals = _read_state(session_dir, "fix-suggestions.json")
        proposal = next(
            (
                item for item in proposals.values()
                if item.get("backup_id") == normalized_backup_id and item.get("applied")
            ),
            None,
        )
        if not proposal:
            raise ProjectUploadError("Change-history backup was not found.", 404)
        relative = PurePosixPath(proposal["path"])
        target = session_dir / "project" / Path(*relative.parts)
        backup_path = session_dir / "history" / f"{normalized_backup_id}.bak"
        try:
            backup = backup_path.read_bytes()
            temporary_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            with temporary_path.open("wb") as target_file:
                target_file.write(backup)
                target_file.flush()
                os.fsync(target_file.fileno())
            os.replace(temporary_path, target)
            backup_path.unlink()
        except OSError as exc:
            logger.error("Could not restore project backup for %s: %s", proposal["path"], exc)
            raise ProjectUploadError("Could not restore the project backup.", 507) from exc
        manifest["files"][proposal["file_index"]]["size"] = proposal["original_size"]
        _write_manifest(session_dir, manifest)
        _write_state(session_dir, "analysis.json", {})
    return {
        "path": proposal["path"],
        "size": proposal["original_size"],
        "message": "Original uploaded source restored.",
    }
