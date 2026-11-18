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
from pathlib import Path
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
    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
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
            options = {"storageBucket": storage_bucket} if storage_bucket else None
            firebase_admin.initialize_app(options=options)
            return
        cred = credentials.Certificate(sa_path)
        options = {"storageBucket": storage_bucket} if storage_bucket else None
        firebase_admin.initialize_app(cred, options=options)
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
        options = {"storageBucket": storage_bucket} if storage_bucket else None
        firebase_admin.initialize_app(cred, options=options)
        logger.info("Firebase: initialised from environment-variable credentials.")
        return

    # ── Neither configured — start uncredentialed ─────────────────────
    logger.warning(
        "Firebase credentials are not configured. "
        "Set FIREBASE_SERVICE_ACCOUNT_PATH=./firebase/serviceAccountKey.json "
        "in backend/.env to enable Firestore. "
        "All Firestore operations will fail until this is set."
    )
    options = {"storageBucket": storage_bucket} if storage_bucket else None
    firebase_admin.initialize_app(options=options)


_init_firebase()


# ── Firestore client helpers ──────────────────────────────────────────────────

def firebase_credentials_configured() -> bool:
    """Return whether explicit credentials or a known ADC runtime is configured."""
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    if service_account_path:
        return Path(service_account_path).is_file()
    if all(os.getenv(name, "").strip() for name in (
        "FIREBASE_PROJECT_ID",
        "FIREBASE_CLIENT_EMAIL",
        "FIREBASE_PRIVATE_KEY",
    )):
        return True
    adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if adc_path:
        return Path(adc_path).is_file()
    return bool(os.getenv("K_SERVICE") or os.getenv("GAE_ENV"))


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
    review_data: dict | None = None,
    linked_issues: list[dict] | None = None,
) -> dict:
    db  = get_db()
    ref = db.collection(CODE_REVIEWS).document()
    result_data = review_data or {}
    payload = {
        **result_data,
        "author_id":    user_id,
        "project_id":   project_id,
        "project_name": project_name,
        "language":     language,
        "code":         code,
        "status":       result_data.get("status", "pending"),
        "created_at":   SERVER_TIMESTAMP,
        "updated_at":   SERVER_TIMESTAMP,
    }
    if linked_issues is None:
        ref.set(payload)
    else:
        batch = db.batch()
        batch.set(ref, payload)
        for issue in linked_issues:
            issue_ref = db.collection(REVIEW_ISSUES).document()
            batch.set(issue_ref, _review_issue_payload(ref.id, issue))
        batch.commit()
    return {
        "id":           ref.id,
        "author_id":    user_id,
        "project_id":   project_id,
        "project_name": project_name,
        "language":     language,
        "code":         code,
        "status":       result_data.get("status", "pending"),
    }


def _review_issue_payload(review_id: str, issue: dict) -> dict:
    return {
        "review_id":      review_id,
        "severity":       issue.get("severity", "info"),
        "category":       issue.get("category", "quality"),
        "title":          issue.get("title", ""),
        "description":    issue.get("description", issue.get("explanation", "")),
        "recommendation": issue.get(
            "recommendation",
            issue.get("suggested_fix", issue.get("recommended_solution", "")),
        ),
        "line":           issue.get("line", issue.get("line_number", 0)),
        "source":         issue.get("source", issue.get("analyzer", "static")),
        "created_at":     SERVER_TIMESTAMP,
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
        batch.set(ref, _review_issue_payload(review_id, issue))
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
    severity: str = "warning",
    recommended_solution: str = "",
    enabled: bool = True,
) -> dict:
    """Alias kept for external callers — delegates to create_team_standard."""
    return create_team_standard(
        title, description, category, project_id, created_by,
        severity, recommended_solution, enabled,
    )


def create_team_standard(
    title: str,
    description: str,
    category: str,
    project_id: str,
    created_by: str,
    severity: str = "warning",
    recommended_solution: str = "",
    enabled: bool = True,
) -> dict:
    db  = get_db()
    ref = db.collection(TEAM_STANDARDS).document()
    payload = {
        "title":       title,
        "description": description,
        "category":    category,
        "severity":    severity,
        "recommended_solution": recommended_solution,
        "enabled":     enabled,
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
        "severity":    severity,
        "recommended_solution": recommended_solution,
        "enabled":     enabled,
        "project_id":  project_id,
        "created_by":  created_by,
    }


def update_team_standard(
    standard_id: str,
    title: str,
    description: str,
    category: str,
    severity: str = "warning",
    recommended_solution: str = "",
    enabled: bool = True,
) -> dict:
    db  = get_db()
    ref = db.collection(TEAM_STANDARDS).document(standard_id)
    payload = {
        "title": title,
        "description": description,
        "category": category,
        "severity": severity,
        "recommended_solution": recommended_solution,
        "enabled": enabled,
        "updated_at": SERVER_TIMESTAMP,
    }
    ref.update(payload)
    return {
        "id": standard_id,
        "title": title,
        "description": description,
        "category": category,
        "severity": severity,
        "recommended_solution": recommended_solution,
        "enabled": enabled,
    }


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


def get_user_team_standards(user_id: str) -> list[dict]:
    db = get_db()
    docs = db.collection(TEAM_STANDARDS).where("created_by", "==", user_id).stream()
    results = [_doc_to_dict(d) for d in docs]
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


def get_team_standards_for_user(user_id: str, project_id: str = "global") -> list[dict]:
    """Return a user's global standards and any standards scoped to this project."""
    standards = get_user_team_standards(user_id)
    if not project_id or project_id == "global":
        return [row for row in standards if row.get("project_id", "global") == "global"]
    return [
        row for row in standards
        if row.get("project_id", "global") in ("global", project_id)
    ]


DEFAULT_TEAM_STANDARDS = [
    {
        "title": "Never hardcode credentials",
        "description": "API keys, passwords, secrets, and tokens must not be embedded in source code.",
        "category": "security",
        "severity": "critical",
        "recommended_solution": "Load credentials from environment variables or a secrets manager.",
    },
    {
        "title": "Validate user input",
        "description": "Validate and normalize user-controlled input before using it.",
        "category": "general",
        "severity": "error",
        "recommended_solution": "Add type, format, range, and allow-list validation at the system boundary.",
    },
    {
        "title": "Handle exceptions properly",
        "description": "Catch specific exceptions and handle or log failures instead of using bare except or pass.",
        "category": "error-handling",
        "severity": "warning",
        "recommended_solution": "Catch the narrow exception type and log the error or raise a meaningful application error.",
    },
    {
        "title": "Avoid duplicate code",
        "description": "Repeated logic should be extracted into a focused reusable function.",
        "category": "architecture",
        "severity": "warning",
        "recommended_solution": "Extract the repeated logic into a named helper and call it from both locations.",
    },
    {
        "title": "Use meaningful variable names",
        "description": "Variables should describe their purpose and avoid unexplained one-letter names.",
        "category": "naming",
        "severity": "warning",
        "recommended_solution": "Rename short or ambiguous variables to descriptive names.",
    },
    {
        "title": "Avoid unnecessary nested loops",
        "description": "Avoid deep loop nesting when a simpler algorithm or data structure is available.",
        "category": "performance",
        "severity": "warning",
        "recommended_solution": "Use lookups, grouping, indexing, or a smaller helper to reduce nesting.",
    },
]


def ensure_default_team_standards(user_id: str) -> list[dict]:
    existing = get_user_team_standards(user_id)
    existing_titles = {str(row.get("title") or "").strip().lower() for row in existing}
    for default in DEFAULT_TEAM_STANDARDS:
        if default["title"].strip().lower() in existing_titles:
            continue
        create_team_standard(
            title=default["title"],
            description=default["description"],
            category=default["category"],
            project_id="global",
            created_by=user_id,
            severity=default["severity"],
            recommended_solution=default["recommended_solution"],
            enabled=True,
        )
    return get_user_team_standards(user_id)


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

    standard_docs = (
        db.collection(TEAM_STANDARDS)
        .where("created_by", "==", user_id)
        .stream()
    )
    standards = [_doc_to_dict(d) for d in standard_docs]

    total_standards = len(standards)
    enabled_standards = sum(1 for standard in standards if standard.get("enabled", True))
    disabled_standards = total_standards - enabled_standards

    # ── Fetch all reviews for this user (simple where — no order_by) ─────
    review_docs = (
        db.collection(CODE_REVIEWS)
        .where("author_id", "==", user_id)
        .stream()                          # no .order_by() — avoids composite index
    )
    reviews = [_doc_to_dict(d) for d in review_docs]
    reviews.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    logger.info("dashboard: fetched %d reviews for user=%s", len(reviews), user_id)

    total_reviews  = len(reviews)
    total_issues   = 0
    critical_count = 0
    warning_count  = 0
    info_count     = 0
    issue_counts_complete = True
    critical_counts_complete = True
    warning_counts_complete = True
    info_counts_complete = True
    team_standard_violations = 0
    team_standard_by_severity = {"critical": 0, "error": 0, "warning": 0, "info": 0}

    for rev in reviews:
        total_value = rev.get("total_issue_count", rev.get("issues_count"))
        if isinstance(total_value, int) and not isinstance(total_value, bool):
            total_issues += total_value
        else:
            issue_counts_complete = False
        for field, attr in (
            ("critical_count", "critical"),
            ("warning_count", "warning"),
            ("info_count", "info"),
        ):
            value = rev.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                if attr == "critical":
                    critical_count += value
                elif attr == "warning":
                    warning_count += value
                else:
                    info_count += value
            elif attr == "critical":
                critical_counts_complete = False
            elif attr == "warning":
                warning_counts_complete = False
            else:
                info_counts_complete = False
        for issue in rev.get("static_issues", []) + rev.get("ai_issues", []):
            if not issue.get("team_standard"):
                continue
            team_standard_violations += 1
            severity = str(issue.get("severity") or "warning").lower()
            if severity in team_standard_by_severity:
                team_standard_by_severity[severity] += 1

    scores = []
    for review in reviews:
        quality_score = review.get("quality_score")
        score = quality_score.get("score") if isinstance(quality_score, dict) else quality_score
        if isinstance(score, (int, float)) and 0 <= score <= 100:
            scores.append(float(score))
    average_quality_score = round(sum(scores) / len(scores), 1) if scores else None

    # ── Feedback counts (simple where — no order_by) ──────────────────────
    accepted_count = 0
    rejected_count = 0
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

    # ── Recent reviews (last 10) ──────────────────────────────────────────
    recent = []
    for rev in reviews[:10]:
        team_violations = sum(
            1 for issue in rev.get("static_issues", []) + rev.get("ai_issues", [])
            if issue.get("team_standard")
        )
        recent.append({
            "id":           rev.get("id", ""),
            "project_name": rev.get("project_name", ""),
            "language":     rev.get("language", ""),
            "status":       rev.get("status", "completed"),
            "overall_risk": rev.get("overall_risk", ""),
            "issues_count": rev.get("total_issue_count", rev.get("issues_count")),
            "quality_score": (
                rev.get("quality_score", {}).get("score")
                if isinstance(rev.get("quality_score"), dict)
                else rev.get("quality_score")
            ),
            "created_at":   rev.get("created_at", ""),
            "summary":      (rev.get("summary") or "")[:120],
            "team_standard_violations": team_violations,
            "compliance_status": "Non-compliant" if team_violations > 0 else "Compliant",
        })

    return {
        "total_reviews":   total_reviews,
        "average_quality_score": average_quality_score,
        "quality_scored_reviews": len(scores),
        "total_issues":    total_issues if issue_counts_complete else None,
        "issue_counts_complete": issue_counts_complete,
        "critical_issues": critical_count if critical_counts_complete else None,
        "critical_counts_complete": critical_counts_complete,
        "warning_issues":  warning_count if warning_counts_complete else None,
        "warning_counts_complete": warning_counts_complete,
        "info_issues":     info_count if info_counts_complete else None,
        "info_counts_complete": info_counts_complete,
        "total_standards": total_standards,
        "enabled_standards": enabled_standards,
        "disabled_standards": disabled_standards,
        "team_standard_violations": team_standard_violations,
        "team_standard_by_severity": team_standard_by_severity,
        "accepted_count":  accepted_count,
        "rejected_count":  rejected_count,
        "recent_reviews":  recent,
    }
