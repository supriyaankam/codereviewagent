"""
POST /api/fix/code    — fix raw code submitted as JSON
POST /api/fix/upload  — fix one or more uploaded files (single file or ZIP)

Both endpoints:
  1. Run static analysis on each file
  2. Pass code + issues to the AI auto-fixer
  3. Return structured results + download-ready ZIP for file uploads
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel, Field, ValidationError
from typing import Optional

from app.auth import require_authenticated_user
from app.services.code_analyzer import analyze_code
from app.services.auto_fix_service import auto_fix_code, auto_fix_multiple
from app.services.firebase_service import firebase_available, get_team_standards_for_user
from app.services.upload_service import (
    MAX_FILE_BYTES,
    MAX_ZIP_BYTES,
    read_single_file,
    read_zip_archive,
)
from app.services import project_upload_service
from app.services.project_upload_service import ProjectUploadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fix", tags=["auto-fix"])


require_project_upload_user = require_authenticated_user


def _upload_error(exc: ProjectUploadError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _read_limited_json(request: Request, maximum_bytes: int) -> object:
    content = bytearray()
    async for request_chunk in request.stream():
        if len(content) + len(request_chunk) > maximum_bytes:
            limit = (
                f"{maximum_bytes // 1024} KiB"
                if maximum_bytes < 1024 * 1024
                else f"{maximum_bytes // (1024 * 1024)} MiB"
            )
            raise HTTPException(
                status_code=413,
                detail=f"Request metadata exceeds the {limit} limit.",
            )
        content.extend(request_chunk)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON.") from exc


class ProjectUploadFile(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)
    size: int = Field(..., ge=0, strict=True)


class ProjectUploadCreate(BaseModel):
    name: str = Field(default="project", max_length=200)
    files: list[ProjectUploadFile] = Field(..., min_length=1, max_length=project_upload_service.MAX_PROJECT_FILES)


class ProjectAnalyzeRequest(BaseModel):
    file_indices: list[int] = Field(..., min_length=1, max_length=project_upload_service.MAX_ANALYSIS_FILES)
    reset: bool = False
    selected_file_indices: list[int] | None = Field(
        default=None,
        min_length=1,
        max_length=project_upload_service.MAX_ANALYSIS_FILES,
    )


class ProjectFixSuggestionRequest(BaseModel):
    file_index: int = Field(..., ge=0)
    issue_index: int = Field(..., ge=0)


class ProjectFixApplyRequest(BaseModel):
    allow_validation_override: bool = False


@router.post("/project-uploads/archive", status_code=201)
async def upload_project_archive(
    file: UploadFile = File(...),
    user_id: str = Depends(require_project_upload_user),
):
    filename = file.filename or "project.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Choose a ZIP project archive.")
    if file.size is not None and file.size > MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"ZIP file exceeds the {MAX_ZIP_BYTES // (1024 * 1024)} MB upload limit.",
        )
    await file.seek(0)
    try:
        return await asyncio.to_thread(
            project_upload_service.create_upload_from_archive_file,
            user_id,
            filename,
            file.file,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.post("/project-uploads", status_code=201)
async def create_project_upload(
    request: Request,
    user_id: str = Depends(require_project_upload_user),
):
    payload = await _read_limited_json(request, 4 * 1024 * 1024)
    try:
        body = ProjectUploadCreate.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_input=False)) from exc
    try:
        return await asyncio.to_thread(
            project_upload_service.create_upload,
            user_id,
            body.name,
            [file.model_dump() for file in body.files],
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.get("/project-uploads/{upload_id}")
def get_project_upload(
    upload_id: str,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return project_upload_service.upload_status(upload_id, user_id)
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.put("/project-uploads/{upload_id}/files/{file_index}/chunks/{chunk_index}")
async def put_project_upload_chunk(
    upload_id: str,
    file_index: int,
    chunk_index: int,
    request: Request,
    chunk_sha256: str = Header(..., alias="X-Chunk-SHA256"),
    user_id: str = Depends(require_project_upload_user),
):
    data = bytearray()
    maximum_chunk_bytes = project_upload_service.chunk_bytes()
    async for request_chunk in request.stream():
        if len(data) + len(request_chunk) > maximum_chunk_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Chunk exceeds the configured {maximum_chunk_bytes // (1024 * 1024)} MB chunk limit.",
            )
        data.extend(request_chunk)
    try:
        return await asyncio.to_thread(
            project_upload_service.save_chunk,
            upload_id,
            user_id,
            file_index,
            chunk_index,
            bytes(data),
            chunk_sha256,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.post("/project-uploads/{upload_id}/complete")
def complete_project_upload(
    upload_id: str,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return project_upload_service.complete_upload(upload_id, user_id)
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.get("/project-uploads/{upload_id}/files")
def list_project_upload_files(
    upload_id: str,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return project_upload_service.project_files(upload_id, user_id)
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.post("/project-uploads/{upload_id}/analyze")
async def analyze_project_upload(
    upload_id: str,
    request: Request,
    user_id: str = Depends(require_project_upload_user),
):
    payload = await _read_limited_json(request, 64 * 1024)
    try:
        body = ProjectAnalyzeRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_input=False)) from exc
    try:
        return await asyncio.to_thread(
            project_upload_service.analyze_project_files,
            upload_id,
            user_id,
            body.file_indices,
            body.reset,
            body.selected_file_indices,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.get("/project-uploads/{upload_id}/quality-score")
async def project_quality_score(
    upload_id: str,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return await asyncio.to_thread(
            project_upload_service.project_quality_score,
            upload_id,
            user_id,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.post("/project-uploads/{upload_id}/fix-suggestions")
async def create_project_fix_suggestion(
    upload_id: str,
    body: ProjectFixSuggestionRequest,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return await asyncio.to_thread(
            project_upload_service.create_fix_suggestion,
            upload_id,
            user_id,
            body.file_index,
            body.issue_index,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.post("/project-uploads/{upload_id}/fix-suggestions/{proposal_id}/apply")
async def apply_project_fix_suggestion(
    upload_id: str,
    proposal_id: str,
    body: ProjectFixApplyRequest | None = None,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return await asyncio.to_thread(
            project_upload_service.apply_fix_suggestion,
            upload_id,
            user_id,
            proposal_id,
            body.allow_validation_override if body else False,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


@router.post("/project-uploads/{upload_id}/restore/{backup_id}")
async def restore_project_backup(
    upload_id: str,
    backup_id: str,
    user_id: str = Depends(require_project_upload_user),
):
    try:
        return await asyncio.to_thread(
            project_upload_service.restore_project_backup,
            upload_id,
            user_id,
            backup_id,
        )
    except ProjectUploadError as exc:
        raise _upload_error(exc) from exc


# ── Schemas ───────────────────────────────────────────────────────────────────

class CodeFixRequest(BaseModel):
    code:     str      = Field(..., min_length=1, max_length=14_000)
    language: str      = Field(..., min_length=1, max_length=60)
    filename: Optional[str] = Field(default="code.py")


class IssueItem(BaseModel):
    line:           int = 0
    severity:       str = ""
    category:       str = ""
    title:          str = ""
    description:    str = ""
    recommendation: str = ""


class ChangeItem(BaseModel):
    line:     int = 0
    original: str = ""
    fixed:    str = ""
    reason:   str = ""


class CodeFixResponse(BaseModel):
    filename:      str
    language:      str
    original_code: str
    fixed_code:    str
    issues:        list[IssueItem] = []
    changes:       list[ChangeItem] = []
    summary:       str = ""
    issues_found:  int = 0
    ai_available:  bool
    error:         Optional[str] = None


# ── POST /api/fix/code — JSON body ────────────────────────────────────────────

@router.post("/code", response_model=CodeFixResponse)
def fix_code_route(
    body: CodeFixRequest,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """
    Accept raw source code as JSON, run analysis, auto-fix, return fixed code.
    For Python: full static analysis (AST + Pylint + Bandit) + AI fix.
    For other languages: AI-only analysis and fix (static tools not available).
    """
    # Step 1: static analysis (Python only — returns empty for other languages)
    team_standards = []
    if firebase_available():
        try:
            team_standards = [
                standard for standard in get_team_standards_for_user(authenticated_user)
                if standard.get("enabled", True)
            ]
        except Exception:
            logger.exception("Could not load team standards for AutoFix analysis.")
    analysis = analyze_code(
        language=body.language,
        code=body.code,
        team_standards=team_standards,
    )
    issues   = analysis.get("issues", [])

    # Step 2: auto-fix (AI handles all languages regardless of static support)
    result = auto_fix_code(
        code=body.code,
        language=body.language,
        issues=issues,
        filename=body.filename or f"code.{body.language.lower()}",
    )

    return CodeFixResponse(
        filename=body.filename or f"code.{body.language.lower()}",
        language=body.language,
        original_code=body.code,
        fixed_code=result["fixed_code"],
        issues=[IssueItem(**{k: i.get(k, "") for k in IssueItem.model_fields}) for i in issues],
        changes=[ChangeItem(**c) for c in result.get("changes", [])],
        summary=result.get("summary", ""),
        issues_found=len(issues),
        ai_available=result["ai_available"],
        error=result.get("error"),
    )


# ── POST /api/fix/upload — multipart file upload ──────────────────────────────

@router.post("/upload")
async def fix_upload_route(
    file: UploadFile = File(..., description="Single source file (max 50 MiB) or ZIP archive (max 500 MiB)"),
    download: str    = Form(default="true",
                            description="'true' = return fixed ZIP; 'false' = return JSON results"),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """
    Upload a single source file or a ZIP project archive.
    Maximum upload size: 50 MiB per source file or 500 MiB per ZIP archive.
    """
    filename = file.filename or "upload"
    try:
        if filename.lower().endswith(".zip"):
            source_files = read_zip_archive(file.file)
        else:
            raw = await file.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Single file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB upload limit.",
                )
            sf = read_single_file(filename, raw)
            source_files = [sf]
    # ── 1. Parse upload ───────────────────────────────────────────────────
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # ── 2. Static analysis per file ───────────────────────────────────────
    files_for_fix = []
    team_standards = []
    if firebase_available():
        try:
            team_standards = [
                standard for standard in get_team_standards_for_user(authenticated_user)
                if standard.get("enabled", True)
            ]
        except Exception:
            logger.exception("Could not load team standards for uploaded AutoFix analysis.")
    for sf in source_files:
        analysis = analyze_code(
            language=sf.language,
            code=sf.content,
            team_standards=team_standards,
        )
        files_for_fix.append({
            "path":     sf.path,
            "language": sf.language,
            "content":  sf.content,
            "issues":   analysis.get("issues", []),
        })

    # ── 3. Auto-fix ───────────────────────────────────────────────────────
    results = auto_fix_multiple(files_for_fix)

    # ── 4a. Return downloadable ZIP ───────────────────────────────────────
    if download.lower() == "true":
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for res in results:
                # Write fixed source file
                fixed_path = _fixed_path(res["path"])
                zf.writestr(fixed_path, res["fixed_code"])

                # Write a companion change-log text file
                log_path  = fixed_path + ".fix_log.txt"
                log_lines = [
                    f"Auto-Fix Report: {res['path']}",
                    f"Language:        {res['language']}",
                    f"Issues found:    {len(res.get('issues', []))}",
                    f"AI available:    {res['ai_available']}",
                    "",
                    f"Summary: {res.get('summary', '')}",
                    "",
                    "── Changes ──────────────────────────────────────────",
                ]
                for ch in res.get("changes", []):
                    log_lines += [
                        f"Line {ch['line']}:",
                        f"  ORIGINAL: {ch['original']}",
                        f"  FIXED:    {ch['fixed']}",
                        f"  REASON:   {ch['reason']}",
                        "",
                    ]
                zf.writestr(log_path, "\n".join(log_lines))

        zip_buffer.seek(0)
        base = filename.replace(".zip", "")
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{base}_fixed.zip"',
                "X-Files-Fixed":       str(len(results)),
            },
        )

    # ── 4b. Return JSON results ───────────────────────────────────────────
    return [
        {
            "path":          r["path"],
            "language":      r["language"],
            "original_code": r["original_code"],
            "fixed_code":    r["fixed_code"],
            "issues":        r.get("issues", []),
            "changes":       r.get("changes", []),
            "summary":       r.get("summary", ""),
            "issues_found":  len(r.get("issues", [])),
            "ai_available":  r["ai_available"],
            "error":         r.get("error"),
        }
        for r in results
    ]


# ── Helper ────────────────────────────────────────────────────────────────────

def _fixed_path(original_path: str) -> str:
    """
    Preserve the original directory structure but prefix the filename
    with 'fixed_' so the original is easy to identify.
    """
    from pathlib import PurePosixPath
    p = PurePosixPath(original_path)
    return str(p.parent / f"fixed_{p.name}") if str(p.parent) != "." else f"fixed_{p.name}"
