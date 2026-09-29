"""
Hindsight memory service.

Stores and retrieves code review experiences from Firestore so the AI
reviewer can learn from past decisions without blindly repeating them.

Design principles:
  - Current code is ALWAYS analysed independently first.
  - Memory is provided as *contextual knowledge*, not as authoritative answers.
  - If Firestore is not configured the service degrades gracefully — every
    function returns an empty result or a no-op rather than raising.

Collections used (all inside Firestore):
  hindsight_experiences   — one doc per review issue + developer decision
  hindsight_feedback      — one doc per explicit accept / reject feedback

Memory schema (hindsight_experiences):
  review_id        : str   — parent review Firestore ID
  user_id          : str   — Firebase UID
  project_id       : str   — project context
  language         : str
  issue_title      : str   — the issue title from the AI review
  issue_category   : str
  issue_severity   : str
  explanation      : str   — AI explanation of why it was an issue
  recommendation   : str   — AI recommendation
  developer_decision : str — "accepted" | "rejected" | "pending"
  developer_reason : str   — free-text note from the developer
  team_preference  : str   — derived preference stored after decision
  arch_preference  : str   — architectural preference if applicable
  code_snippet     : str   — short context snippet (≤500 chars)
  created_at       : timestamp

Memory schema (hindsight_feedback):
  experience_id    : str   — FK → hindsight_experiences doc
  user_id          : str
  review_id        : str
  decision         : str   — "accepted" | "rejected"
  note             : str
  accepted_recommendation : str
  rejected_recommendation : str
  created_at       : timestamp
"""

from __future__ import annotations

import logging
from typing import Any

from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP  # type: ignore

logger = logging.getLogger(__name__)

# ── Collection names ──────────────────────────────────────────────────────────

_EXPERIENCES = "hindsight_experiences"
_FEEDBACK    = "hindsight_feedback"

# ── Max memories returned to the AI ──────────────────────────────────────────

_MAX_RELEVANT = 8


# ── Firestore client (lazy, graceful) ────────────────────────────────────────

def _db():
    """
    Return a Firestore client.
    Returns None (without raising) when Firebase is not configured.
    """
    try:
        from app.services.firebase_service import get_db  # local import avoids circular
        return get_db()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Memory service: Firestore unavailable — %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Experience storage
# ─────────────────────────────────────────────────────────────────────────────

def store_review_experience(
    *,
    review_id: str,
    user_id: str,
    project_id: str,
    language: str,
    issue_title: str,
    issue_category: str,
    issue_severity: str,
    explanation: str,
    recommendation: str,
    code_snippet: str = "",
    developer_decision: str = "pending",
    developer_reason: str = "",
    team_preference: str = "",
    arch_preference: str = "",
) -> str | None:
    """
    Persist a single review issue as a Hindsight experience.

    Returns the new document ID, or None if Firestore is unavailable.
    """
    db = _db()
    if db is None:
        return None

    try:
        ref = db.collection(_EXPERIENCES).document()
        ref.set({
            "review_id":          review_id,
            "user_id":            user_id,
            "project_id":         project_id,
            "language":           language.lower(),
            "issue_title":        issue_title[:300],
            "issue_category":     issue_category,
            "issue_severity":     issue_severity,
            "explanation":        explanation[:1000],
            "recommendation":     recommendation[:1000],
            "code_snippet":       code_snippet[:500],
            "developer_decision": developer_decision,
            "developer_reason":   developer_reason[:500],
            "team_preference":    team_preference[:500],
            "arch_preference":    arch_preference[:500],
            "created_at":         SERVER_TIMESTAMP,
        })
        return ref.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory: could not store experience — %s", exc)
        return None


def store_experiences_from_review(
    review_id: str,
    user_id: str,
    project_id: str,
    language: str,
    ai_issues: list[dict],
    code: str = "",
) -> int:
    """
    Bulk-store all AI issues from a completed review as experiences.
    Returns the count of successfully stored experiences.
    """
    stored = 0
    # Use a short snippet of the code as context (first 500 chars)
    snippet = code[:500] if code else ""

    for issue in ai_issues:
        exp_id = store_review_experience(
            review_id=review_id,
            user_id=user_id,
            project_id=project_id,
            language=language,
            issue_title=issue.get("title", ""),
            issue_category=issue.get("category", "quality"),
            issue_severity=issue.get("severity", "info"),
            explanation=issue.get("explanation", issue.get("description", "")),
            recommendation=issue.get("recommendation", ""),
            code_snippet=snippet,
        )
        if exp_id:
            stored += 1

    if stored:
        logger.info("Memory: stored %d experiences for review %s", stored, review_id)
    return stored


# ─────────────────────────────────────────────────────────────────────────────
# Experience retrieval
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_relevant_experiences(
    *,
    user_id: str,
    project_id: str,
    language: str,
    limit: int = _MAX_RELEVANT,
) -> list[dict[str, Any]]:
    """
    Retrieve the most recent experiences for this user + project + language.

    Retrieval strategy (simple, no vector search required):
      1. Match on project_id + language — most relevant
      2. If fewer than `limit` results, supplement with user-level memories
         across all projects for the same language.

    Returns a list of plain dicts safe for JSON serialisation.
    The list is intentionally small (≤ _MAX_RELEVANT) to stay within LLM
    context budgets.
    """
    db = _db()
    if db is None:
        return []

    results: list[dict] = []

    try:
        # Primary: project + language match
        docs = (
            db.collection(_EXPERIENCES)
            .where("user_id",    "==", user_id)
            .where("project_id", "==", project_id)
            .where("language",   "==", language.lower())
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        results = [_exp_to_context(d) for d in docs]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory: primary experience query failed — %s", exc)

    # Supplement with broader user-level memories if needed
    if len(results) < limit:
        try:
            remaining = limit - len(results)
            existing_ids = {r["id"] for r in results}
            docs2 = (
                db.collection(_EXPERIENCES)
                .where("user_id",  "==", user_id)
                .where("language", "==", language.lower())
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(limit * 2)   # fetch more, filter duplicates client-side
                .stream()
            )
            for d in docs2:
                if d.id not in existing_ids and len(results) < limit:
                    results.append(_exp_to_context(d))
                    existing_ids.add(d.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Memory: supplemental experience query failed — %s", exc)

    return results[:limit]


def _exp_to_context(doc) -> dict[str, Any]:
    data = doc.to_dict() or {}
    # Convert timestamps
    for key in ("created_at",):
        val = data.get(key)
        if val and hasattr(val, "isoformat"):
            data[key] = val.isoformat()
    data["id"] = doc.id
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Feedback storage
# ─────────────────────────────────────────────────────────────────────────────

def store_feedback_memory(
    *,
    experience_id: str,
    user_id: str,
    review_id: str,
    decision: str,
    note: str = "",
    accepted_recommendation: str = "",
    rejected_recommendation: str = "",
) -> str | None:
    """
    Record explicit developer feedback (accept / reject) against an experience.

    Also updates the parent experience document's developer_decision field so
    future retrievals reflect the actual outcome.

    Returns the new feedback document ID, or None on failure.
    """
    db = _db()
    if db is None:
        return None

    try:
        ref = db.collection(_FEEDBACK).document()
        ref.set({
            "experience_id":          experience_id,
            "user_id":                user_id,
            "review_id":              review_id,
            "decision":               decision,
            "note":                   note[:500],
            "accepted_recommendation": accepted_recommendation[:1000],
            "rejected_recommendation": rejected_recommendation[:1000],
            "created_at":             SERVER_TIMESTAMP,
        })

        # Back-update the experience with the developer's final decision
        _update_experience_decision(
            experience_id=experience_id,
            decision=decision,
            developer_reason=note,
            accepted_recommendation=accepted_recommendation,
            rejected_recommendation=rejected_recommendation,
        )

        return ref.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory: could not store feedback — %s", exc)
        return None


def _update_experience_decision(
    experience_id: str,
    decision: str,
    developer_reason: str,
    accepted_recommendation: str,
    rejected_recommendation: str,
) -> None:
    db = _db()
    if db is None:
        return
    try:
        update_payload: dict[str, Any] = {
            "developer_decision": decision,
            "developer_reason":   developer_reason,
        }
        if decision == "accepted" and accepted_recommendation:
            update_payload["team_preference"] = accepted_recommendation
        if decision == "rejected" and rejected_recommendation:
            update_payload["arch_preference"] = rejected_recommendation

        db.collection(_EXPERIENCES).document(experience_id).update(update_payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Memory: could not back-update experience %s — %s", experience_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Feedback retrieval
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_relevant_feedback(
    *,
    user_id: str,
    project_id: str,
    limit: int = _MAX_RELEVANT,
) -> list[dict[str, Any]]:
    """
    Retrieve recent developer feedback for this user + project.
    Returns a list of plain dicts.
    """
    db = _db()
    if db is None:
        return []

    try:
        docs = (
            db.collection(_FEEDBACK)
            .where("user_id", "==", user_id)
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .stream()
        )
        results = []
        for d in docs:
            data = d.to_dict() or {}
            data["id"] = d.id
            if "created_at" in data and hasattr(data["created_at"], "isoformat"):
                data["created_at"] = data["created_at"].isoformat()
            results.append(data)
        return results
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory: feedback retrieval failed — %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Configuration status
# ─────────────────────────────────────────────────────────────────────────────

def memory_status() -> dict[str, Any]:
    """
    Return a dict describing whether Hindsight memory is available.
    Used by the health/status endpoint and the AI reviewer fallback.
    """
    db = _db()
    if db is None:
        return {
            "available": False,
            "reason": (
                "Firestore is not configured. Set FIREBASE_SERVICE_ACCOUNT_PATH "
                "or FIREBASE_PROJECT_ID + FIREBASE_CLIENT_EMAIL + FIREBASE_PRIVATE_KEY "
                "in the backend .env file to enable persistent memory."
            ),
        }
    return {"available": True, "reason": ""}
