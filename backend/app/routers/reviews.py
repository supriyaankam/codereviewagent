from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from app.schemas import CodeReviewCreate, CodeReviewResponse
from app.services.firebase_service import (
    create_code_review,
    get_code_reviews,
    get_code_review_by_id,
    firebase_available,
)
from app.services.auto_fix_service import auto_fix_code
from app.services.code_analyzer import analyze_code
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])

_NO_FIREBASE = HTTPException(
    status_code=503,
    detail="Firebase is not configured. Add credentials to backend/.env to enable this feature.",
)


# ── Fix request / response schemas ───────────────────────────────────────────

class ReviewFixRequest(BaseModel):
    code:            str = Field(..., min_length=1, description="Original source code to fix")
    language:        str = Field(..., min_length=1, max_length=60,
                                 description="TARGET language selected by the user")
    source_language: str = Field(default="",
                                 description="Actual language of the submitted code (empty = same as language)")
    issues:          list[dict] = Field(default=[], description="Issues detected by previous review")
    filename:        Optional[str] = Field(default="code.py")


class FixChange(BaseModel):
    issue:  str = ""
    change: str = ""


class ReviewFixResponse(BaseModel):
    fixed_code:      str
    changes:         list[FixChange] = []
    summary:         str = ""
    ai_available:    bool
    error:           Optional[str] = None
    source_language: str = ""
    target_language: str = ""
    is_conversion:   bool = False
    reanalysis:      Optional[dict] = None


@router.post("", response_model=CodeReviewResponse, status_code=201)
def create_review_route(body: CodeReviewCreate):
    """Submit a new code review record."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        return create_code_review(
            user_id=body.user_id,
            project_id=body.project_id,
            project_name=body.project_name,
            language=body.language,
            code=body.code,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("", response_model=list[CodeReviewResponse])
def get_reviews_route(user_id: str = Query(..., description="Firebase UID")):
    """Return all code reviews submitted by a user — empty list when Firebase not configured."""
    if not firebase_available():
        return []
    try:
        return get_code_reviews(user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{review_id}", response_model=CodeReviewResponse)
def get_review_by_id_route(review_id: str):
    """Return a single code review by its Firestore document ID."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        review = get_code_review_by_id(review_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return review


# ── POST /api/reviews/fix ─────────────────────────────────────────────────────

@router.post("/fix", response_model=ReviewFixResponse)
def fix_review_route(body: ReviewFixRequest):
    """
    Auto-fix a piece of code based on previously detected issues.

    Workflow:
      1. Send code + issues to Groq → get corrected code
      2. Run static re-analysis on the corrected code
      3. Return corrected code + changes made + re-analysis results

    Code is NEVER executed.
    """
    logger.info("[reviews/fix] fix request  target_lang=%s  source_lang=%s  issues=%d  code_chars=%d",
                body.language, body.source_language or "(same)", len(body.issues), len(body.code))

    # Step 1: AI auto-fix / conversion using Groq
    fix_result = auto_fix_code(
        code=body.code,
        language=body.language,
        issues=body.issues,
        filename=body.filename or f"code.{body.language.lower()}",
        source_language=body.source_language or "",
    )

    # Step 2: Re-analyze the corrected code using the TARGET language
    reanalysis = None
    try:
        reanalysis_raw = analyze_code(
            language=body.language,          # always use TARGET language for re-analysis
            code=fix_result["fixed_code"],
        )
        reanalysis = {
            "issues":           reanalysis_raw.get("issues", []),
            "summary":          reanalysis_raw.get("summary", {}),
            "has_syntax_error": reanalysis_raw.get("has_syntax_error", False),
            "supported":        reanalysis_raw.get("supported", False),
            "language":         body.language,
        }
        logger.info("[reviews/fix] re-analysis  language=%s  remaining=%d",
                    body.language, len(reanalysis["issues"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reviews/fix] re-analysis skipped: %s", exc)

    return ReviewFixResponse(
        fixed_code=fix_result["fixed_code"],
        changes=[FixChange(**c) for c in fix_result.get("changes", [])],
        summary=fix_result.get("summary", ""),
        ai_available=fix_result["ai_available"],
        error=fix_result.get("error"),
        source_language=fix_result.get("source_language", body.source_language or body.language),
        target_language=fix_result.get("target_language", body.language),
        is_conversion=fix_result.get("is_conversion", False),
        reanalysis=reanalysis,
    )
