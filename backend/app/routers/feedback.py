"""
POST /api/feedback  — record developer feedback on a single AI review issue
GET  /api/feedback  — retrieve all feedback for a user

Feedback flow:
  1. Persist to Firebase `feedback` collection
  2. If an experience_id is provided, update the Hindsight experience with
     the developer's decision so future reviews can use the context.
  3. If no experience_id but enough context is present, store a new experience.

This handler never raises on memory failures — the Firestore write is the
source of truth; memory is best-effort.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_authenticated_user
from app.schemas import FeedbackCreate, FeedbackResponse
from app.services.firebase_service import (
    create_feedback,
    get_feedback,
    get_code_review_by_id,
)
from app.services.memory_service import store_feedback_memory, store_review_experience

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse, status_code=201)
def create_feedback_route(
    body: FeedbackCreate,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """
    Record a developer's Accept / Reject / Modify decision on an AI issue.

    Stores the decision in Firestore and propagates it to the Hindsight
    memory layer so future reviews benefit from the team's preferences.
    """
    if body.user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only submit feedback for your own account.")
    review = get_code_review_by_id(body.review_id)
    if review is None or review.get("author_id", review.get("authorId")) != authenticated_user:
        raise HTTPException(status_code=404, detail="Review not found.")
    # ── 1. Persist to Firestore ───────────────────────────────────────────
    try:
        doc = create_feedback(
            user_id=body.user_id,
            review_id=body.review_id,
            issue_id=body.issue_id,
            decision=body.decision,
            note=body.note or "",
            experience_id=body.experience_id or "",
            issue_title=body.issue_title or "",
            issue_category=body.issue_category or "",
            issue_severity=body.issue_severity or "",
            original_recommendation=body.original_recommendation or "",
            modified_recommendation=body.modified_recommendation or "",
            project_id=body.project_id or "global",
            language=body.language or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # ── 2. Update Hindsight memory (best-effort) ──────────────────────────
    memory_stored = False
    decision = body.decision  # "accepted" | "rejected" | "modified"

    # Determine what to pass to memory based on the decision
    accepted_rec  = ""
    rejected_rec  = ""

    if decision == "accepted":
        accepted_rec = body.original_recommendation or ""
    elif decision == "rejected":
        rejected_rec = body.original_recommendation or ""
    elif decision == "modified":
        # A modified decision means the developer accepted a variant
        accepted_rec = body.modified_recommendation or body.original_recommendation or ""

    try:
        if body.experience_id:
            # Update the existing Hindsight experience
            mem_id = store_feedback_memory(
                experience_id=body.experience_id,
                user_id=body.user_id,
                review_id=body.review_id,
                decision=decision,
                note=body.note or "",
                accepted_recommendation=accepted_rec,
                rejected_recommendation=rejected_rec,
            )
            memory_stored = mem_id is not None
        elif body.issue_title:
            # No pre-existing experience — create one from the feedback itself
            # so future reviews still learn from this decision
            exp_id = store_review_experience(
                review_id=body.review_id,
                user_id=body.user_id,
                project_id=body.project_id or "global",
                language=body.language or "unknown",
                issue_title=body.issue_title,
                issue_category=body.issue_category or "quality",
                issue_severity=body.issue_severity or "info",
                explanation=body.note or "",
                recommendation=body.original_recommendation or "",
                developer_decision=decision,
                developer_reason=body.note or "",
                team_preference=accepted_rec,
                arch_preference=rejected_rec,
            )
            if exp_id:
                # Also record the feedback doc against the new experience
                store_feedback_memory(
                    experience_id=exp_id,
                    user_id=body.user_id,
                    review_id=body.review_id,
                    decision=decision,
                    note=body.note or "",
                    accepted_recommendation=accepted_rec,
                    rejected_recommendation=rejected_rec,
                )
            memory_stored = exp_id is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory update skipped after feedback: %s", exc)

    return FeedbackResponse(
        id=doc["id"],
        user_id=doc["user_id"],
        review_id=doc["review_id"],
        issue_id=doc.get("issue_id", ""),
        experience_id=doc.get("experience_id", ""),
        decision=doc["decision"],
        note=doc.get("note", ""),
        issue_title=doc.get("issue_title", ""),
        memory_stored=memory_stored,
    )


@router.get("", response_model=list[FeedbackResponse])
def get_feedback_route(
    user_id: str = Query(..., description="Firebase UID"),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return all feedback submitted by a user, newest first."""
    if user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only access your own feedback.")
    try:
        raw = get_feedback(user_id)
        return [
            FeedbackResponse(
                id=f["id"],
                user_id=f.get("user_id", ""),
                review_id=f.get("review_id", ""),
                issue_id=f.get("issue_id", ""),
                experience_id=f.get("experience_id", ""),
                decision=f.get("decision", ""),
                note=f.get("note", ""),
                issue_title=f.get("issue_title", ""),
                memory_stored=False,
            )
            for f in raw
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
