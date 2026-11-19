"""
POST /api/review

Full review pipeline (order matters):
  1. Static analysis          — AST + Pylint + Bandit + secrets
  2. Retrieve relevant memory — past experiences for this project/language
  3. Retrieve team standards  — project-specific + global fallback
  4. AI review                — LLM receives code + static + memory + standards
  5. Persist to Firestore     — completed review and linked findings atomically
  6. Store Hindsight memories — one experience per AI issue (best-effort)
  7. Return structured result

Submitted code is NEVER executed.
Firestore persistence is required before success is returned. Hindsight memory
storage is best-effort and does not affect the saved review.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_authenticated_user
from app.analyzers.language_analyzer import canonical_language, detect_source_language
from app.schemas import ReviewRequest, ReviewResponse
from app.services.code_analyzer import analyze_code
from app.services.ai_reviewer import MAX_CODE_CHARS, run_ai_review
from app.services.quality_score import calculate_quality_score
from app.services.firebase_service import (
    SERVER_TIMESTAMP,
    create_code_review,
    firebase_available,
    get_team_standards_for_user,
)
from app.services.memory_service import (
    retrieve_relevant_experiences,
    store_experiences_from_review,
    memory_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["review"])


@router.post("/review", response_model=ReviewResponse, status_code=201)
def full_review(
    body: ReviewRequest,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """
    Submit code for a full review.

    Pipeline: static analysis → memory retrieval → team standards →
              AI review → Firestore persist → memory storage → response.
    """
    if body.user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only submit reviews for your own account.")
    if not body.code.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "empty_code",
                "message": "Code must contain at least one non-whitespace character.",
                "selected_language": body.language,
                "detected_language": None,
                "candidates": [],
            },
        )

    selected_language = canonical_language(body.language)
    is_other = body.language.strip().lower() == "other"
    detection = detect_source_language(body.code)
    detected_language = detection["detected_language"]
    if selected_language is None and not is_other:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_language",
                "message": f"'{body.language}' is not a supported language selection.",
                "selected_language": body.language,
                "detected_language": detected_language,
                "candidates": detection["candidates"],
            },
        )
    if detected_language and selected_language != detected_language:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "language_mismatch",
                "message": (
                    f"Selected language is {body.language}, but the source strongly "
                    f"matches {detected_language}."
                ),
                "selected_language": body.language,
                "detected_language": detected_language,
                "candidates": detection["candidates"],
            },
        )
    if detection["status"] == "ambiguous" and not (
        body.language_confirmed and selected_language in detection["candidates"]
    ):
        candidate_names = ", ".join(detection["candidates"])
        raise HTTPException(
            status_code=409,
            detail={
                "code": "language_ambiguous",
                "message": (
                    f"Language could not be determined confidently. The source is "
                    f"compatible with {candidate_names}; confirm the selected language to continue."
                ),
                "selected_language": body.language,
                "detected_language": None,
                "candidates": detection["candidates"],
            },
        )

    language_validation_status = (
        "other"
        if is_other
        else "confirmed_ambiguous"
        if detection["status"] == "ambiguous"
        else "matched"
        if detected_language
        else "undetermined"
    )
    if detection["status"] == "ambiguous" and body.language_confirmed:
        detected_language = selected_language

    if not firebase_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Firestore is unavailable, so this review cannot be saved. "
                "Configure FIREBASE_SERVICE_ACCOUNT_PATH in backend/.env or set "
                "FIREBASE_PROJECT_ID, FIREBASE_CLIENT_EMAIL, and FIREBASE_PRIVATE_KEY."
            ),
        )
    logger.info("[review] started  language=%s  code_length=%d  project=%s",
                body.language, len(body.code), body.project_name)

    # ── Step 1: Fetch enabled team standards ─────────────────────────────
    team_standards: list[dict] = []
    if firebase_available():
        try:
            team_standards = get_team_standards_for_user(
                body.user_id,
                body.project_id or "global",
            )
            team_standards = [standard for standard in team_standards if standard.get("enabled", True)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Team standards fetch skipped: %s", exc)

    # ── Step 2: Static analysis ───────────────────────────────────────────
    try:
        static_result = analyze_code(
            language=body.language,
            code=body.code,
            team_standards=team_standards,
        )
        static_analysis_succeeded = True
    except Exception:  # noqa: BLE001
        logger.exception("[review] static analysis failed")
        static_result = {
            "language": body.language,
            "issues": [],
            "message": "Static analysis failed.",
            "failure_category": "analyzer_error",
        }
        static_analysis_succeeded = False
    static_issues = static_result.get("issues", [])
    static_metadata = {
        key: static_result[key]
        for key in (
            "language", "supported", "has_syntax_error", "summary", "message",
            "failure_category", "capabilities",
        )
        if key in static_result
    }
    logger.info("[review] static analysis complete  issues=%d  has_syntax_error=%s",
                len(static_issues), static_result.get("has_syntax_error", False))

    # ── Step 2: Retrieve relevant Hindsight memories (best-effort) ───────
    memory_experiences: list[dict] = []
    try:
        memory_experiences = retrieve_relevant_experiences(
            user_id=body.user_id,
            project_id=body.project_id or "global",
            language=body.language,
        )
        if memory_experiences:
            logger.info(
                "Memory: retrieved %d experiences for project=%s language=%s",
                len(memory_experiences), body.project_id, body.language,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory retrieval skipped: %s", exc)

    # ── Step 4: AI review ────────────────────────────────────────────────
    logger.info("[review] starting AI review  static_issues=%d", len(static_issues))
    ai_result = run_ai_review(
        code=body.code,
        language=body.language,
        project_name=body.project_name,
        static_issues=static_issues,
        team_standards=team_standards or None,
        memory_experiences=memory_experiences or None,
    )
    analysis_succeeded = (
        ai_result.get("ai_available", False)
        or (static_analysis_succeeded and static_result.get("supported", False))
    )
    quality_score = (
        calculate_quality_score(
            code=body.code,
            language=body.language,
            ai_issues=ai_result.get("issues", []),
            static_issues=static_issues,
        )
        if (
            analysis_succeeded
            and static_analysis_succeeded
            and static_result.get("supported", False)
            and not is_other
        )
        else None
    )
    logger.info("[review] AI review complete  ai_issues=%d  overall_risk=%s  ai_available=%s",
                len(ai_result.get("issues", [])), ai_result.get("overall_risk"), ai_result.get("ai_available"))

    # ── Step 5: Atomically persist the completed review and its findings ───
    mem_status = memory_status()

    # Counts are based on distinct persisted findings, not analyzer-reported maxima.
    combined_findings = {}
    for issue in [*static_issues, *ai_result.get("issues", [])]:
        key = (
            str(issue.get("title", "")).strip().lower(),
            int(issue.get("line", issue.get("line_number", 0)) or 0),
        )
        combined_findings.setdefault(key, issue)
    findings = list(combined_findings.values())
    counts_available = analysis_succeeded
    issues_count = len(findings) if counts_available else None
    critical_count = sum(
        str(issue.get("severity", "")).lower() == "critical" for issue in findings
    ) if counts_available else None
    warning_count = sum(
        str(issue.get("severity", "")).lower() == "warning" for issue in findings
    ) if counts_available else None
    info_count = sum(
        str(issue.get("severity", "")).lower() == "info" for issue in findings
    ) if counts_available else None
    error_count = sum(
        str(issue.get("severity", "")).lower() == "error" for issue in findings
    ) if counts_available else None
    if findings:
        severity_order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
        risk_by_severity = {
            "critical": "critical",
            "error": "high",
            "warning": "medium",
            "info": "info",
        }
        highest_severity = min(
            (str(issue.get("severity", "info")).lower() for issue in findings),
            key=lambda severity: severity_order.get(severity, 4),
        )
        overall_risk = risk_by_severity.get(highest_severity, "info")
    elif counts_available:
        overall_risk = "info"
    else:
        overall_risk = None
    analysis_status = (
        "completed"
        if ai_result.get("ai_available", False)
        and static_analysis_succeeded
        and static_result.get("supported", False)
        else "partial" if analysis_succeeded else "failed"
    )
    static_summary = static_result.get("summary")

    try:
        doc = create_code_review(
            user_id=body.user_id,
            project_id=body.project_id,
            project_name=body.project_name,
            language=body.language,
            code=body.code,
            review_data={
                "status":            "completed",
                "analysis_status":   analysis_status,
                "user_id":           body.user_id,
                "source_code":       body.code,
                "overall_risk":      overall_risk,
                "summary":           ai_result["summary"],
                "ai_summary":        ai_result["summary"] if ai_result.get("ai_available") else None,
                "ai_failure_reason": ai_result.get("fallback_reason"),
                "ai_failure_category": ai_result.get("failure_category"),
                "total_issue_count": issues_count,
                "issues_count":      issues_count,
                "critical_count":    critical_count,
                "warning_count":     warning_count,
                "info_count":        info_count,
                "error_count":       error_count,
                "ai_issues":         ai_result.get("issues", []),
                "static_issues":     static_result.get("issues", []),
                "static_summary":    static_summary,
                "static_supported":  (
                    static_result.get("supported", False)
                    if static_analysis_succeeded else None
                ),
                "static_metadata":   static_metadata,
                "static_analyzer_metadata": static_metadata,
                "detected_language": detected_language,
                "language_validation_status": language_validation_status,
                "quality_score":     quality_score,
                "ai_available":      ai_result.get("ai_available", False),
                "static_analysis_error": static_result.get("failure_category"),
                "ai_code_truncated": ai_result.get("ai_available", False) and len(body.code) > MAX_CODE_CHARS,
                "fallback_reason":   ai_result.get("fallback_reason"),
                "completed_at":      SERVER_TIMESTAMP,
                "feedback":          [],
                "auto_fix":          None,
                "memory_used":       bool(memory_experiences),
                "memory_count":      len(memory_experiences),
            },
            linked_issues=[
                *static_result.get("issues", []),
                *[
                    {**issue, "source": "ai"}
                    for issue in ai_result.get("issues", [])
                ],
            ],
        )
        review_id = doc["id"]
        logger.info("[review] saved to Firestore  review_id=%s  issues=%d", review_id, issues_count)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[review] Firestore persistence failed")
        raise HTTPException(
            status_code=503,
            detail="The review was analyzed but could not be saved to Firestore. Please retry.",
        ) from exc

    # ── Step 6: Store Hindsight memories (best-effort) ────────────────────
    if mem_status["available"]:
        try:
            n = store_experiences_from_review(
                review_id=review_id,
                user_id=body.user_id,
                project_id=body.project_id or "global",
                language=body.language,
                ai_issues=ai_result.get("issues", []),
                code=body.code,
            )
            logger.info("Memory: stored %d new experiences for review %s", n, review_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Memory storage skipped: %s", exc)

    # ── Step 7: Build and return response ─────────────────────────────────
    return ReviewResponse(
        review_id=review_id,
        language=body.language,
        detected_language=detected_language,
        language_validation_status=language_validation_status,
        project_name=body.project_name,
        overall_risk=overall_risk,
        summary=ai_result["summary"],
        ai_summary=ai_result["summary"] if ai_result.get("ai_available") else None,
        ai_issues=ai_result.get("issues", []),
        static_issues=static_result.get("issues", []),
        static_summary=static_summary,
        static_supported=(
            static_result.get("supported", False)
            if static_analysis_succeeded else None
        ),
        static_metadata=static_metadata,
        quality_score=quality_score,
        ai_code_truncated=ai_result.get("ai_available", False) and len(body.code) > MAX_CODE_CHARS,
        ai_available=ai_result.get("ai_available", False),
        fallback_reason=ai_result.get("fallback_reason"),
        ai_failure_category=ai_result.get("failure_category"),
        static_analysis_error=static_result.get("failure_category"),
        total_issue_count=issues_count,
        critical_count=critical_count,
        warning_count=warning_count,
        info_count=info_count,
        error_count=error_count,
        analysis_status=analysis_status,
        saved_to_db=True,
    )
