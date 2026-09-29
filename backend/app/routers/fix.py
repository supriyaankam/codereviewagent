"""
POST /api/fix/code    — fix raw code submitted as JSON
POST /api/fix/upload  — fix one or more uploaded files (single file or ZIP)

Both endpoints:
  1. Run static analysis on each file
  2. Pass code + issues to the AI auto-fixer
  3. Return structured results + download-ready ZIP for file uploads
"""

from __future__ import annotations

import io
import zipfile
import logging

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.services.code_analyzer import analyze_code
from app.services.auto_fix_service import auto_fix_code, auto_fix_multiple
from app.services.upload_service import read_single_file, read_zip_archive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fix", tags=["auto-fix"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CodeFixRequest(BaseModel):
    code:     str      = Field(..., min_length=1)
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
def fix_code_route(body: CodeFixRequest):
    """
    Accept raw source code as JSON, run analysis, auto-fix, return fixed code.
    For Python: full static analysis (AST + Pylint + Bandit) + AI fix.
    For other languages: AI-only analysis and fix (static tools not available).
    """
    # Step 1: static analysis (Python only — returns empty for other languages)
    analysis = analyze_code(language=body.language, code=body.code)
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
    file: UploadFile = File(..., description="Single source file (.py, .js, etc.) or ZIP archive (max 100 MB)"),
    download: str    = Form(default="true",
                            description="'true' = return fixed ZIP; 'false' = return JSON results"),
):
    """
    Upload a single source file or a ZIP project archive.
    Maximum upload size: 100 MB.
    """
    raw = await file.read()
    filename = file.filename or "upload"

    # Guard: check raw upload size (100 MB hard cap)
    MAX_RAW_BYTES = 100 * 1024 * 1024
    if len(raw) > MAX_RAW_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large ({len(raw) // (1024*1024)} MB). Maximum allowed is 100 MB.",
        )

    # ── 1. Parse upload ───────────────────────────────────────────────────
    try:
        if filename.lower().endswith(".zip"):
            source_files = read_zip_archive(raw)
        else:
            sf = read_single_file(filename, raw)
            source_files = [sf]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # ── 2. Static analysis per file ───────────────────────────────────────
    files_for_fix = []
    for sf in source_files:
        analysis = analyze_code(language=sf.language, code=sf.content)
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
