from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from app.auth import require_authenticated_user
from app.schemas import (
    CodeReviewCreate,
    CodeReviewResponse,
    ReviewAutoFixUpdate,
)
from app.services.firebase_service import (
    create_code_review,
    get_code_reviews,
    get_code_review_by_id,
    get_issues_by_review,
    get_feedback,
    update_code_review,
    firebase_available,
)
from app.services.auto_fix_service import auto_fix_code, auto_fix_finding
from app.services.code_analyzer import analyze_code
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])

_NO_FIREBASE = HTTPException(
    status_code=503,
    detail="Firebase is not configured. Add credentials to backend/.env to enable this feature.",
)


# ── Fix request / response schemas ───────────────────────────────────────────

class ReviewFixRequest(BaseModel):
    code:            str = Field(..., min_length=1, max_length=100_000, description="Original source code to fix")
    language:        str = Field(..., min_length=1, max_length=60,
                                 description="TARGET language selected by the user")
    source_language: str = Field(default="",
                                 description="Actual language of the submitted code (empty = same as language)")
    issues:          list[dict] = Field(default_factory=list, max_length=100, description="Issues detected by previous review")
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
    validation:      dict = Field(default_factory=dict)
    original_region: Optional[str] = None
    proposed_region: Optional[str] = None
    line_range:      Optional[dict] = None


_REANALYZABLE_SOURCES = {
    "ast", "pylint", "bandit", "secrets", "syntax", "tree_sitter", "team_standard",
}


def _finding_identity(issue: dict) -> tuple[str, str]:
    title = re.sub(r"^\[[^\]]+\]\s*", "", str(issue.get("title") or "")).strip().lower()
    category = str(issue.get("category") or "").strip().lower()
    return title, category


def _fix_validation(selected_issues: list[dict], analysis: dict, language: str) -> dict:
    if not analysis.get("supported"):
        return {
            "status": "unavailable",
            "message": f"No static validator is configured for {language}; the proposal was not verified.",
        }
    if analysis.get("has_syntax_error"):
        return {
            "status": "failed",
            "message": f"The proposed {language} code contains a syntax error.",
        }

    sources = {
        str(issue.get("source") or "").strip().lower()
        for issue in selected_issues
    }
    if not selected_issues or not sources.issubset(_REANALYZABLE_SOURCES):
        return {
            "status": "partial",
            "message": (
                f"{language} syntax validation passed, but the selected finding could not "
                "be independently checked by the available analyzers."
            ),
        }

    remaining = {
        _finding_identity(issue)
        for issue in analysis.get("issues", [])
        if isinstance(issue, dict)
    }
    unresolved = [
        issue for issue in selected_issues
        if _finding_identity(issue) in remaining
    ]
    if unresolved:
        titles = ", ".join(str(issue.get("title") or "selected finding") for issue in unresolved)
        return {
            "status": "unresolved",
            "message": f"The updated code still has selected finding(s): {titles}.",
        }

    parser_only_languages = {
        "javascript", "js", "typescript", "ts", "java", "csharp", "c#",
        "go", "ruby", "php", "cpp", "c++", "c", "rust", "kotlin", "swift",
    }
    if language.strip().lower() in parser_only_languages and not sources.issubset(
        {"tree_sitter", "team_standard"}
    ):
        return {
            "status": "partial",
            "message": (
                f"{language} syntax validation passed, but no semantic analyzer is configured "
                "to verify the selected finding."
            ),
        }
    return {
        "status": "passed",
        "message": f"The selected static finding is no longer reported for the updated {language} code.",
    }


@router.post("", response_model=CodeReviewResponse, status_code=201)
def create_review_route(
    body: CodeReviewCreate,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Submit a new code review record."""
    if body.user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only submit reviews for your own account.")
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
def get_reviews_route(
    user_id: str = Query(..., description="Firebase UID"),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return code reviews belonging to the authenticated user."""
    if user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only access your own review history.")
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        return get_code_reviews(user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _first_saved_value(record: dict, *field_names: str):
    for field_name in field_names:
        if field_name in record and record[field_name] is not None:
            return record[field_name]
    return None


def _saved_value(records: list[dict], *field_names: str):
    for record in records:
        if isinstance(record, dict):
            value = _first_saved_value(record, *field_names)
            if value is not None:
                return value
    return None


def _normalize_saved_issue(issue: dict) -> dict:
    normalized = dict(issue)
    aliases = {
        "title": ("title", "name", "rule_name"),
        "description": ("description", "explanation", "message"),
        "recommendation": (
            "recommendation", "suggested_fix", "recommended_solution", "fix",
        ),
        "line": ("line", "line_number", "lineNumber"),
        "severity": ("severity", "risk_level"),
        "category": ("category", "type"),
        "source": ("source", "analyzer", "tool"),
    }
    for target, names in aliases.items():
        value = _first_saved_value(issue, *names)
        if value is not None:
            normalized[target] = value
    if "explanation" not in normalized and normalized.get("description"):
        normalized["explanation"] = normalized["description"]
    try:
        if "line" in normalized:
            normalized["line"] = int(normalized["line"] or 0)
    except (TypeError, ValueError):
        normalized.pop("line", None)
    return normalized


def _merge_saved_issues(*groups: list[dict]) -> list[dict]:
    merged = []
    seen = set()
    identity_fields = (
        "title", "description", "recommendation", "line",
        "severity", "category",
    )
    for group in groups:
        if not isinstance(group, list):
            continue
        for issue in group:
            if not isinstance(issue, dict):
                continue
            normalized = _normalize_saved_issue(issue)
            identity = tuple(str(normalized.get(field, "")).strip() for field in identity_fields)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(normalized)
    return merged


def _session_response(review: dict, authenticated_user: str) -> dict:
    review = dict(review)
    review_id = review.get("id")
    ai_result = review.get("ai_result", {})
    static_result = review.get("static_result", review.get("static_analysis", {}))
    metadata = review.get("analysis_metadata", review.get("analysis", {}))
    ai_result = ai_result if isinstance(ai_result, dict) else {}
    static_result = static_result if isinstance(static_result, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    ai_records = [review, ai_result, metadata]
    static_records = [review, static_result, metadata]
    saved_static_metadata = _saved_value(
        [review, static_result, metadata],
        "static_analyzer_metadata",
        "static_metadata",
        "analyzer_metadata",
        "metadata",
    )

    aliases = {
        "project_name": ([review], ("project_name", "projectName", "project")),
        "language": ([review], ("language", "programming_language", "programmingLanguage")),
        "code": ([review], ("code", "source_code", "submitted_code", "original_code")),
        "overall_risk": ([review], ("overall_risk", "risk_level", "risk")),
        "ai_summary": (ai_records, ("ai_summary", "review_summary")),
        "summary": (ai_records, ("ai_summary", "summary", "review_summary")),
        "quality_score": (
            [review, metadata], ("quality_score", "code_quality_score", "qualityScore", "quality_metrics"),
        ),
        "issues": ([review], ("issues", "findings", "review_findings")),
        "ai_available": (ai_records, ("ai_available", "ai_analysis_available", "available")),
        "static_supported": (
            [review, static_result, saved_static_metadata if isinstance(saved_static_metadata, dict) else {}],
            ("static_supported", "static_analysis_supported", "analyzer_supported", "supported"),
        ),
        "static_metadata": (
            [review, static_result, metadata],
            ("static_analyzer_metadata", "static_metadata", "analyzer_metadata"),
        ),
        "static_summary": (
            [review, static_result, metadata],
            ("static_summary",),
        ),
        "issues_count": (
            [review],
            ("total_issue_count", "issues_count", "issue_count", "total_issues"),
        ),
        "ai_failure_reason": (
            [review, ai_result, metadata],
            ("ai_failure_reason", "fallback_reason"),
        ),
        "ai_failure_category": (
            [review, ai_result, metadata],
            ("ai_failure_category", "failure_category"),
        ),
        "analysis_status": ([review, metadata], ("analysis_status", "status")),
        "critical_count": ([review], ("critical_count", "critical_issues")),
        "warning_count": ([review], ("warning_count", "warning_issues")),
        "info_count": ([review], ("info_count", "info_issues")),
        "auto_fix": ([review], ("auto_fix", "auto_fix_result", "autofix", "autofix_result")),
    }
    for target, (records, names) in aliases.items():
        value = _saved_value(records, *names)
        if value is not None:
            review[target] = value
    if "static_summary" not in review:
        nested_static_summary = _saved_value([static_result], "summary")
        if nested_static_summary is not None:
            review["static_summary"] = nested_static_summary
    if "ai_summary" not in review and review.get("ai_available") is not False:
        legacy_summary = _saved_value(ai_records, "summary", "review_summary")
        if legacy_summary is not None:
            review["ai_summary"] = legacy_summary
    if "static_metadata" not in review:
        legacy_static_metadata = {}
        for key in (
            "static_language",
            "static_supported",
            "has_syntax_error",
            "static_summary",
            "static_message",
        ):
            if key in review and review[key] is not None:
                metadata_key = {
                    "static_supported": "supported",
                    "static_language": "language",
                    "static_summary": "summary",
                    "static_message": "message",
                }.get(key, key)
                legacy_static_metadata[metadata_key] = review[key]
        if legacy_static_metadata:
            review["static_metadata"] = legacy_static_metadata
    if "ai_issues" not in review:
        value = _saved_value([review], "ai_findings", "aiIssues")
        if value is None:
            value = _saved_value([ai_result], "issues", "findings")
        if value is not None:
            review["ai_issues"] = value
    if "static_issues" not in review:
        value = _saved_value([review], "static_findings", "staticIssues")
        if value is None:
            value = _saved_value([static_result], "static_issues", "static_findings", "issues", "findings")
        if value is not None:
            review["static_issues"] = value

    if isinstance(review.get("issues"), list):
        classified = {"ai": [], "static": []}
        for issue in review["issues"]:
            if not isinstance(issue, dict):
                continue
            normalized = _normalize_saved_issue(issue)
            source = str(normalized.get("source", "")).strip().lower()
            issue_type = str(issue.get("analysis_type", issue.get("finding_type", ""))).strip().lower()
            if source in {"ai", "llm", "groq", "openai"} or issue_type == "ai":
                classified["ai"].append(normalized)
            else:
                classified["static"].append(normalized)
        if "ai_issues" not in review and classified["ai"]:
            review["ai_issues"] = classified["ai"]
        if "static_issues" not in review and classified["static"]:
            review["static_issues"] = classified["static"]

    if "quality_score" in review and not isinstance(review["quality_score"], dict):
        review["quality_score"] = {"score": review["quality_score"]}
    if isinstance(review.get("ai_issues"), list):
        review["ai_issues"] = [
            _normalize_saved_issue(issue)
            for issue in review["ai_issues"]
            if isinstance(issue, dict)
        ]
    if isinstance(review.get("static_issues"), list):
        review["static_issues"] = [
            _normalize_saved_issue(issue)
            for issue in review["static_issues"]
            if isinstance(issue, dict)
        ]

    stored_issues = get_issues_by_review(review_id) if review_id else []
    linked_ai_issues = []
    linked_static_issues = []
    for issue in stored_issues:
        normalized = _normalize_saved_issue(issue)
        source = str(normalized.get("source", "")).strip().lower()
        if source in {"ai", "llm", "groq", "openai"}:
            linked_ai_issues.append(normalized)
        else:
            linked_static_issues.append(normalized)

    if "ai_issues" in review or linked_ai_issues:
        review["ai_issues"] = _merge_saved_issues(
            review.get("ai_issues", []),
            linked_ai_issues,
        )
    if "static_issues" in review or linked_static_issues:
        review["static_issues"] = _merge_saved_issues(
            review.get("static_issues", []),
            linked_static_issues,
        )

    feedback = [
        item for item in get_feedback(authenticated_user)
        if item.get("review_id") == review_id
    ]
    return {**review, "review_id": review_id, "feedback": feedback}


@router.get("/latest")
def get_latest_review_route(
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return the most recently completed review belonging to the caller."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        reviews = get_code_reviews(authenticated_user)
        latest = next((review for review in reviews if review.get("status") == "completed"), None)
        return _session_response(latest, authenticated_user) if latest else None
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not load the saved review.") from exc


@router.get("/{review_id}")
def get_review_by_id_route(
    review_id: str,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return a single code review by its Firestore document ID."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        review = get_code_review_by_id(review_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    if review.get("author_id", review.get("authorId")) != authenticated_user:
        raise HTTPException(status_code=404, detail="Review not found.")
    try:
        return _session_response(review, authenticated_user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not load the saved review.") from exc


@router.patch("/{review_id}/autofix")
def update_review_autofix_route(
    review_id: str,
    body: ReviewAutoFixUpdate,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Persist an Auto-Fix proposal and whether the user accepted it."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        review = get_code_review_by_id(review_id)
        if review is None or review.get("author_id", review.get("authorId")) != authenticated_user:
            raise HTTPException(status_code=404, detail="Review not found.")
        update_code_review(review_id, {"auto_fix": body.model_dump()})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not save the Auto-Fix state.") from exc
    return {"saved": True}


# ── POST /api/reviews/fix ─────────────────────────────────────────────────────

@router.post("/fix", response_model=ReviewFixResponse)
def fix_review_route(
    body: ReviewFixRequest,
    _authenticated_user: str = Depends(require_authenticated_user),
):
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
    targeted_finding = (
        len(body.issues) == 1
        and (body.source_language or body.language).strip().lower() == body.language.strip().lower()
    )
    if targeted_finding:
        finding = body.issues[0]
        fix_result = auto_fix_finding(
            code=body.code,
            language=body.language,
            issue=finding,
            filename=body.filename or f"code.{body.language.lower()}",
            finding_id=str(finding.get("finding_id") or finding.get("id") or "0"),
        )
    else:
        fix_result = auto_fix_code(
            code=body.code,
            language=body.language,
            issues=body.issues,
            filename=body.filename or f"code.{body.language.lower()}",
            source_language=body.source_language or "",
        )

    # Step 2: Re-analyze the corrected code using the TARGET language
    reanalysis = None
    validation = {"status": "unavailable", "message": "No static validator is available for this language."}
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
            "message":          reanalysis_raw.get("message"),
            "capabilities":     reanalysis_raw.get("capabilities", []),
        }
        if fix_result.get("ai_available"):
            validation = _fix_validation(body.issues, reanalysis_raw, body.language)
        else:
            validation = {
                "status": "unavailable",
                "message": fix_result.get("error")
                or "No AI-generated proposal was produced; the original code was preserved.",
            }
        logger.info("[reviews/fix] re-analysis  language=%s  remaining=%d",
                    body.language, len(reanalysis["issues"]))
    except Exception as exc:  # noqa: BLE001
        validation = {"status": "unavailable", "message": "Static validation failed to run."}
        logger.exception("[reviews/fix] re-analysis failed")

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
        validation=validation,
        original_region=fix_result.get("original_region"),
        proposed_region=fix_result.get("replacement_region"),
        line_range=(
            {
                "start": fix_result["patch_start_line"],
                "end": fix_result["patch_end_line"],
            }
            if "patch_start_line" in fix_result
            else None
        ),
    )
