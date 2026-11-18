"""
GET /api/dashboard

Returns real statistics from Firestore for the authenticated user.
Never returns hardcoded counts — Firestore is the single source of truth.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import require_authenticated_user
from app.services.firebase_service import get_dashboard_stats, firebase_available

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(
    user_id: str = Query(..., description="Firebase UID of the logged-in user"),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """
    Return real dashboard statistics for a user.

    Reads from Firestore:
      - code_reviews  → total reviews + issue counts
      - feedback      → accepted / rejected counts

    Returns:
      {
        "total_reviews":   int,
        "average_quality_score": float | null,
        "quality_scored_reviews": int,
        "total_issues":    int,
        "critical_issues": int,
        "warning_issues":  int,
        "info_issues":     int,
        "accepted_count":  int,
        "rejected_count":  int,
        "recent_reviews":  [ {id, project_name, language, status, overall_risk,
                               issues_count, quality_score, created_at, summary} ]
      }
    """
    if user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only access your own dashboard.")
    if not firebase_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Firestore is unavailable. Configure FIREBASE_SERVICE_ACCOUNT_PATH "
                "in backend/.env, or set FIREBASE_PROJECT_ID, FIREBASE_CLIENT_EMAIL, "
                "and FIREBASE_PRIVATE_KEY, then restart the backend."
            ),
        )

    try:
        stats = get_dashboard_stats(user_id)
        stats["firebase_configured"] = True
        return stats
    except Exception as exc:
        logger.exception("[dashboard] failed for user=%s", user_id)
        raise HTTPException(
            status_code=500,
            detail="Could not load dashboard data from Firestore. Please try again.",
        ) from exc
