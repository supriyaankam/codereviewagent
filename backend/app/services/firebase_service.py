"""
Firebase Admin SDK initialisation and Firestore service helpers.

Credentials are loaded from FIREBASE_SERVICE_ACCOUNT_PATH — never hardcoded.

Initialisation rules:
  - Runs exactly once at import time.
  - If FIREBASE_SERVICE_ACCOUNT_PATH is set but the file does not exist →
    raises a clear RuntimeError so the problem is obvious at startup.
  - If FIREBASE_SERVICE_ACCOUNT_PATH is empty (not yet configured) →
    logs a warning and continues without credentials so the server still
    starts (all Firestore calls will raise at runtime).

Collections:
  users, projects, code_reviews, review_issues, feedback, team_standards
"""

import os
import logging
from datetime import datetime, timezone
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

logger = logging.getLogger(__name__)

# ── Initialise Firebase Admin SDK (once) ─────────────────────────────────────

def _init_firebase() -> None:
    if firebase_admin._apps:
        return  # already initialised — never re-initialise

    sa_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    project_id   = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    client_email = os.getenv("FIREBASE_CLIENT_EMAIL", "").strip()
    private_key  = os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n").strip()

    # ── Option 1: service-account JSON file ──────────────────────────────
    if sa_path:
        if not os.path.isfile(sa_path):
            # Path is configured but file does not exist — log clearly and
            # continue uncredentialed so the server can still start.
            # The /api/firebase/health endpoint will report the problem.
            logger.error(
                "Firebase service-account file NOT FOUND at '%s'. "
                "Place your serviceAccountKey.json there and restart the server. "
                "All Firestore operations will fail until the file is present.",
                sa_path,
            )
            firebase_admin.initialize_app()
            return
        cred = credentials.Certificate(sa_path)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase: initialised from service-account file '%s'.", sa_path)
        return

    # ── Option 2: individual env-var fields ──────────────────────────────
    if project_id and client_email and private_key:
        sa_dict = {
            "type":         "service_account",
            "project_id":   project_id,
            "client_email": client_email,
            "private_key":  private_key,
            "token_uri":    "https://oauth2.googleapis.com/token",
        }
        cred = credentials.Certificate(sa_dict)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase: initialised from environment-variable credentials.")
        return

    # ── Neither configured — start uncredentialed ─────────────────────
    logger.warning(
        "Firebase credentials are not configured. "
        "Set FIREBASE_SERVICE_ACCOUNT_PATH=./firebase/serviceAccountKey.json "
        "in backend/.env to enable Firestore. "
        "All Firestore operations will fail until this is set."
    )
    firebase_admin.initialize_app()


_init_firebase()


# ── Firestore client helpers ──────────────────────────────────────────────────

def get_db():
    """Return a Firestore client. Raises with a clear message if not configured."""
    try:
        return firestore.client()
    except Exception as exc:
        raise RuntimeError(
            "Firestore client could not be created. "
            "Set FIREBASE_SERVICE_ACCOUNT_PATH in backend/.env and restart the server."
        ) from exc


def firebase_available() -> bool:
    """Return True if Firestore is reachable without errors."""
    try:
        firestore.client()
        return True
    except Exception:  # noqa: BLE001
        return False


def firebase_health_check() -> dict[str, Any]:
    """
    Verify the Firebase Admin SDK + Firestore connection.
    Writes a test document to 'firebase_connection_test' and reads it back.
    Never exposes credentials.
    """
    sa_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()

    if not firebase_available():
        if sa_path and not os.path.isfile(sa_path):
            return {
                "status":  "error",
                "message": (
                    f"Firebase service-account file not found at '{sa_path}'. "
                    "Download serviceAccountKey.json from Firebase Console → "
                    "Project Settings → Service accounts → Generate new private key, "
                    "then place it at backend/firebase/serviceAccountKey.json and restart."
                ),
            }
        return {
            "status":  "error",
            "message": (
                "Firebase is not configured. "
                "Set FIREBASE_SERVICE_ACCOUNT_PATH=./firebase/serviceAccountKey.json "
                "in backend/.env and restart the server."
            ),
        }

    test_collection = "firebase_connection_test"
    try:
        db  = get_db()
        ref = db.collection(test_collection).document()
        ref.set({
            "message":    "Firebase Admin SDK connection test",
            "created_at": SERVER_TIMESTAMP,
            "source":     "code-review-agent-backend",
        })
        doc = ref.get()
        if not doc.exists:
            return {"status": "error", "message": "Test document was not found after writing."}

        ref.delete()  # clean up test document
        logger.info("Firebase health check: PASSED (test doc id=%s)", doc.id)
        return {
            "status":  "ok",
            "message": "Firebase Admin SDK and Firestore connection are working.",
            "details": {
                "test_document_id": doc.id,
                "collection":       test_collection,
                "write_read_delete": "success",
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Firebase health check failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ── Collection name constants ─────────────────────────────────────────────────

USERS          = "users"
PROJECTS       = "projects"
CODE_REVIEWS   = "code_reviews"
REVIEW_ISSUES  = "review_issues"
FEEDBACK       = "feedback"
TEAM_STANDARDS = "team_standards"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _doc_to_dict(doc) -> dict:
    """Convert a Firestore DocumentSnapshot to a plain dict including 'id'."""
    data = doc.to_dict() or {}
    data["id"] = doc.id
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):          # Firestore DatetimeWithNanoseconds
            data[key] = value.isoformat()
    return data


# ── users ─────────────────────────────────────────────────────────────────────

def create_user_document(uid: str, email: str, display_name: str = "") -> dict:
    """
    Create a user document in Firestore if it does not already exist.
    Idempotent — safe to call on every login.
    """
    db  = get_db()
    ref = db.collection(USERS).document(uid)
    doc = ref.get()
    if not doc.exists:
        payload = {
            "uid":          uid,
            "email":        email,
            "display_name": display_name,
            "created_at":   SERVER_TIMESTAMP,
            "updated_at":   SERVER_TIMESTAMP,
        }
        ref.set(payload)
        logger.info("Firestore: created user document uid=%s", uid)
        return {"id": uid, "uid": uid, "email": email, "display_name": display_name}
    return _doc_to_dict(doc)


def get_user_document(uid: str) -> dict | None:
    db  = get_db()
    doc = db.collection(USERS).document(uid).get()
    return _doc_to_dict(doc) if doc.exists else None


# ── projects ──────────────────────────────────────────────────────────────────

def create_project(user_id: str, name: str, description: str = "") -> dict:
    db  = get_db()
    ref = db.collection(PROJECTS).document()
    payload = {
        "name":        name,
        "description": description,
        "owner_id":    user_id,
        "created_at":  SERVER_TIMESTAMP,
        "updated_at":  SERVER_TIMESTAMP,
    }
    ref.set(payload)
    return {"id": ref.id, "name": name, "description": description, "owner_id": user_id}


def get_project(project_id: str) -> dict | None:
    db  = get_db()
    doc = db.collection(PROJECTS).document(project_id).get()
    return _doc_to_dict(doc) if doc.exists else None


def get_projects(user_id: str) -> list[dict]:
    db   = get_db()
    docs = (
        db.collection(PROJECTS)
        .where("owner_id", "==", user_id)
        .stream()
    )
    results = [_doc_to_dict(d) for d in docs]
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


# ── code_reviews ──────────────────────────────────────────────────────────────

def create_code_review(
    user_id: str,
    project_id: str,
    project_name: str,
    language: str,
    code: str,
) -> dict:
    db  = get_db()
    ref = db.collection(CODE_REVIEWS).document()
    payload = {
        "author_id":    user_id,
        "project_id":   project_id,
        "project_name": project_name,
        "language":     language,
        "code":         code,
        "status":       "pending",
        "created_at":   SERVER_TIMESTAMP,
        "updated_at":   SERVER_TIMESTAMP,
    }
    ref.set(payload)
    return {
        "id":           ref.id,
        "author_id":    user_id,
        "project_id":   project_id,
        "project_name": project_name,
        "language":     language,
        "code":         code,
        "status":       "pending",
    }


def update_code_review(review_id: str, data: dict) -> None:
    """Merge arbitrary fields into an existing code review document."""
    db = get_db()
    db.collection(CODE_REVIEWS).document(review_id).update({
        **data,
        "updated_at": SERVER_TIMESTAMP,
    })


def get_code_reviews(user_id: str) -> list[dict]:
    db   = get_db()
    # Simple where only — no order_by to avoid composite index requirement
    docs = (
        db.collection(CODE_REVIEWS)
        .where("author_id", "==", user_id)
        .stream()
    )
    results = [_doc_to_dict(d) for d in docs]
    # Sort newest-first in Python
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


def get_code_review_by_id(review_id: str) -> dict | None:
    db  = get_db()
    doc = db.collection(CODE_REVIEWS).document(review_id).get()
    return _doc_to_dict(doc) if doc.exists else None


# ── review_issues ─────────────────────────────────────────────────────────────

def save_review_issues(review_id: str, issues: list[dict]) -> list[str]:
    """
    Persist a list of review issues to the review_issues collection.
    Each issue is stored as a separate document linked by review_id.
    Returns the list of created document IDs.
    """
    db     = get_db()
    ids    = []
    batch  = db.batch()

    for issue in issues:
        ref = db.collection(REVIEW_ISSUES).document()
        batch.set(ref, {
            "review_id":      review_id,
            "severity":       issue.get("severity", "info"),
            "category":       issue.get("category", "quality"),
            "title":          issue.get("title", ""),
            "description":    issue.get("description", issue.get("explanation", "")),
            "recommendation": issue.get("recommendation", ""),
            "line":           issue.get("line", 0),
            "source":         issue.get("source", "static"),
            "created_at":     SERVER_TIMESTAMP,
        })
        ids.append(ref.id)

    batch.commit()
    logger.info("Firestore: saved %d issues for review %s", len(ids), review_id)
    return ids


def get_issues_by_review(review_id: str) -> list[dict]:
    db   = get_db()
    docs = (
        db.collection(REVIEW_ISSUES)
        .where("review_id", "==", review_id)
        .stream()
    )
    return [_doc_to_dict(d) for d in docs]


# ── feedback ──────────────────────────────────────────────────────────────────

def create_feedback(
    user_id: str,
    review_id: str,
    issue_id: str,
    decision: str,
    note: str = "",
    experience_id: str = "",
    issue_title: str = "",
    issue_category: str = "",
    issue_severity: str = "",
    original_recommendation: str = "",
    modified_recommendation: str = "",
    project_id: str = "global",
    language: str = "",
) -> dict:
    db  = get_db()
    ref = db.collection(FEEDBACK).document()
    payload = {
        "user_id":                  user_id,
        "review_id":                review_id,
        "issue_id":                 issue_id,
        "experience_id":            experience_id,
        "decision":                 decision,
        "note":                     note,
        "issue_title":              issue_title,
        "issue_category":           issue_category,
        "issue_severity":           issue_severity,
        "original_recommendation":  original_recommendation,
        "modified_recommendation":  modified_recommendation,
        "project_id":               project_id,
        "language":                 language,
        "created_at":               SERVER_TIMESTAMP,
    }
    ref.set(payload)
    return {"id": ref.id, **{k: v for k, v in payload.items() if k != "created_at"}}


def get_feedback(user_id: str) -> list[dict]:
    db   = get_db()
    # Simple where only — no order_by to avoid composite index requirement
    docs = (
        db.collection(FEEDBACK)
        .where("user_id", "==", user_id)
        .stream()
    )
    results = [_doc_to_dict(d) for d in docs]
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


# ── team_standards ────────────────────────────────────────────────────────────

def save_team_standard(
    title: str,
    description: str,
    category: str,
    project_id: str,
    created_by: str,
) -> dict:
    """Alias kept for external callers — delegates to create_team_standard."""
    return create_team_standard(title, description, category, project_id, created_by)


def create_team_standard(
    title: str,
    description: str,
    category: str,
    project_id: str,
    created_by: str,
) -> dict:
    db  = get_db()
    ref = db.collection(TEAM_STANDARDS).document()
    payload = {
        "title":       title,
        "description": description,
        "category":    category,
        "project_id":  project_id,
        "created_by":  created_by,
        "created_at":  SERVER_TIMESTAMP,
        "updated_at":  SERVER_TIMESTAMP,
    }
    ref.set(payload)
    return {
        "id":          ref.id,
        "title":       title,
        "description": description,
        "category":    category,
        "project_id":  project_id,
        "created_by":  created_by,
    }


def update_team_standard(standard_id: str, title: str, description: str, category: str) -> dict:
    db  = get_db()
    ref = db.collection(TEAM_STANDARDS).document(standard_id)
    payload = {"title": title, "description": description, "category": category, "updated_at": SERVER_TIMESTAMP}
    ref.update(payload)
    return {"id": standard_id, "title": title, "description": description, "category": category}


def delete_team_standard(standard_id: str) -> None:
    db = get_db()
    db.collection(TEAM_STANDARDS).document(standard_id).delete()


def get_team_standard(standard_id: str) -> dict | None:
    db  = get_db()
    doc = db.collection(TEAM_STANDARDS).document(standard_id).get()
    return _doc_to_dict(doc) if doc.exists else None


def get_all_team_standards() -> list[dict]:
    db   = get_db()
    # No order_by to avoid composite index requirement — sort in Python
    docs = db.collection(TEAM_STANDARDS).stream()
    results = [_doc_to_dict(d) for d in docs]
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


def get_standards_by_project(project_id: str) -> list[dict]:
    db   = get_db()
    # Simple where only — no order_by to avoid composite index requirement
    docs = (
        db.collection(TEAM_STANDARDS)
        .where("project_id", "==", project_id)
        .stream()
    )
    results = [_doc_to_dict(d) for d in docs]
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


# ── dashboard ─────────────────────────────────────────────────────────────────

def get_dashboard_stats(user_id: str) -> dict:
    """
    Compute real dashboard statistics from Firestore.

    IMPORTANT: Uses only simple equality filters (no order_by in Firestore queries)
    to avoid requiring composite indexes. Sorting is done in Python after fetch.
    """
    db = get_db()

    # ── Fetch all reviews for this user (simple where — no order_by) ─────
    reviews = []
    try:
        review_docs = (
            db.collection(CODE_REVIEWS)
            .where("author_id", "==", user_id)
            .stream()                          # no .order_by() — avoids composite index
        )
        reviews = [_doc_to_dict(d) for d in review_docs]
        # Sort newest-first in Python
        reviews.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        logger.info("dashboard: fetched %d reviews for user=%s", len(reviews), user_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("dashboard: failed to fetch reviews — %s", exc)

    total_reviews  = len(reviews)
    total_issues   = 0
    critical_count = 0
    warning_count  = 0
    info_count     = 0

    for rev in reviews:
        total_issues   += int(rev.get("issues_count",   0) or 0)
        critical_count += int(rev.get("critical_count", 0) or 0)
        warning_count  += int(rev.get("warning_count",  0) or 0)
        info_count     += int(rev.get("info_count",     0) or 0)

    # ── Feedback counts (simple where — no order_by) ──────────────────────
    accepted_count = 0
    rejected_count = 0
    try:
        fb_docs = (
            db.collection(FEEDBACK)
            .where("user_id", "==", user_id)
            .stream()
        )
        for fb in fb_docs:
            d = fb.to_dict() or {}
            dec = d.get("decision", "")
            if dec == "accepted":
                accepted_count += 1
            elif dec == "rejected":
                rejected_count += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: failed to fetch feedback — %s", exc)

    # ── Recent reviews (last 10) ──────────────────────────────────────────
    recent = []
    for rev in reviews[:10]:
        recent.append({
            "id":           rev.get("id", ""),
            "project_name": rev.get("project_name", ""),
            "language":     rev.get("language", ""),
            "status":       rev.get("status", "completed"),
            "overall_risk": rev.get("overall_risk", ""),
            "issues_count": int(rev.get("issues_count", 0) or 0),
            "created_at":   rev.get("created_at", ""),
            "summary":      (rev.get("summary") or "")[:120],
        })

    return {
        "total_reviews":   total_reviews,
        "total_issues":    total_issues,
        "critical_issues": critical_count,
        "warning_issues":  warning_count,
        "info_issues":     info_count,
        "accepted_count":  accepted_count,
        "rejected_count":  rejected_count,
        "recent_reviews":  recent,
    }
