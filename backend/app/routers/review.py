"""
POST /api/review

Full review pipeline (order matters):
  1. Static analysis          — AST + Pylint + Bandit + secrets
  2. Retrieve relevant memory — past experiences for this project/language
  3. Retrieve team standards  — project-specific + global fallback
  4. AI review                — LLM receives code + static + memory + standards
  5. Persist to Firestore     — review record (best-effort)
  6. Store Hindsight memories — one experience per AI issue (best-effort)
  7. Return structured result

Submitted code is NEVER executed.
Steps 5 & 6 are best-effort: failures are logged but never surface to the caller.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas import ReviewRequest, ReviewResponse
from app.services.code_analyzer import analyze_code
from app.services.ai_reviewer import run_ai_review
from app.services.firebase_service import (
    create_code_review,
    update_code_review,
    get_standards_by_project,
)
from app.services.memory_service import (
    retrieve_relevant_experiences,
    store_experiences_from_review,
    memory_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["review"])


@router.post("/review", response_model=ReviewResponse, status_code=201)
def full_review(body: ReviewRequest):
    """
    Submit code for a full review.

    Pipeline: static analysis → memory retrieval → team standards →
              AI review → Firestore persist → memory storage → response.
    """
    logger.info("[review] started  language=%s  code_length=%d  project=%s",
                body.language, len(body.code), body.project_name)

    # ── Step 1: Static analysis ───────────────────────────────────────────
    static_result = analyze_code(language=body.language, code=body.code)
    static_issues = static_result.get("issues", [])
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

    # ── Step 3: Fetch team standards (best-effort) ────────────────────────
    team_standards: list[dict] = []
    try:
        if body.project_id:
            team_standards = get_standards_by_project(body.project_id)
        if not team_standards:
            team_standards = get_standards_by_project("global")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Team standards fetch skipped: %s", exc)

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
    logger.info("[review] AI review complete  ai_issues=%d  overall_risk=%s  ai_available=%s",
                len(ai_result.get("issues", [])), ai_result.get("overall_risk"), ai_result.get("ai_available"))

    # ── Step 5: Persist review to Firestore (best-effort) ─────────────────
    review_id = "local"
    mem_status = memory_status()

    # Calculate flat issue counts for easy dashboard queries
    all_issues     = ai_result.get("issues", [])
    static_counts  = static_result.get("summary", {}).get("counts", {})
    ai_critical    = sum(1 for i in all_issues if i.get("severity") == "critical")
    ai_warning     = sum(1 for i in all_issues if i.get("severity") == "warning")
    ai_info        = sum(1 for i in all_issues if i.get("severity") == "info")
    # Use whichever is larger (AI promotes static, so avoid double-counting)
    issues_count   = max(len(all_issues), static_counts.get("total", 0))
    critical_count = max(ai_critical,  static_counts.get("critical", 0))
    warning_count  = max(ai_warning,   static_counts.get("warning", 0))
    info_count     = max(ai_info,      static_counts.get("info", 0))

    saved_to_db = False
    try:
        doc = create_code_review(
            user_id=body.user_id,
            project_id=body.project_id,
            project_name=body.project_name,
            language=body.language,
            code=body.code,
        )
        review_id = doc["id"]

        update_code_review(review_id, {
            "status":            "completed",
            "overall_risk":      ai_result["overall_risk"],
            "summary":           ai_result["summary"],
            # Flat counts for fast dashboard aggregation
            "issues_count":      issues_count,
            "critical_count":    critical_count,
            "warning_count":     warning_count,
            "info_count":        info_count,
            # Full issue arrays for detail view
            "ai_issues":         ai_result.get("issues", []),
            "static_issues":     static_result.get("issues", []),
            "ai_available":      ai_result.get("ai_available", False),
            "memory_used":       bool(memory_experiences),
            "memory_count":      len(memory_experiences),
        })
        saved_to_db = True
        logger.info("[review] saved to Firestore  review_id=%s  issues=%d", review_id, issues_count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[review] Firestore write skipped: %s", exc)

    # ── Step 6: Store Hindsight memories (best-effort) ────────────────────
    if mem_status["available"] and review_id != "local":
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
    static_summary = static_result.get("summary", {"counts": {}, "by_category": {}})

    return ReviewResponse(
        review_id=review_id,
        language=body.language,
        project_name=body.project_name,
        overall_risk=ai_result["overall_risk"],
        summary=ai_result["summary"],
        ai_issues=ai_result.get("issues", []),
        static_issues=static_result.get("issues", []),
        static_summary=static_summary,
        ai_available=ai_result.get("ai_available", False),
        fallback_reason=ai_result.get("fallback_reason"),
        saved_to_db=saved_to_db,
    )
