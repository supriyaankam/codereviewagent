"""
GET /api/dashboard

Returns real statistics from Firestore for the authenticated user.
Never returns hardcoded counts — Firestore is the single source of truth.
"""

import logging
from fastapi import APIRouter, HTTPException, Query
from app.services.firebase_service import get_dashboard_stats, firebase_available

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(user_id: str = Query(..., description="Firebase UID of the logged-in user")):
    """
    Return real dashboard statistics for a user.

    Reads from Firestore:
      - code_reviews  → total reviews + issue counts
      - feedback      → accepted / rejected counts

    Returns:
      {
        "total_reviews":   int,
        "total_issues":    int,
        "critical_issues": int,
        "warning_issues":  int,
        "info_issues":     int,
        "accepted_count":  int,
        "rejected_count":  int,
        "recent_reviews":  [ {id, project_name, language, status, overall_risk,
                               issues_count, created_at, summary} ]
      }
    """
    if not firebase_available():
        return {
            "total_reviews":   0,
            "total_issues":    0,
            "critical_issues": 0,
            "warning_issues":  0,
            "info_issues":     0,
            "accepted_count":  0,
            "rejected_count":  0,
            "recent_reviews":  [],
            "firebase_configured": False,
            "message": "Firebase is not configured. Add credentials to enable statistics.",
        }

    try:
        stats = get_dashboard_stats(user_id)
        stats["firebase_configured"] = True
        return stats
    except Exception as exc:
        logger.error("[dashboard] failed for user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
