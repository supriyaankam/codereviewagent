import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import dashboard as dashboard_router
from app.services import firebase_service


class _Document:
    def __init__(self, document_id, data=None):
        self.id = document_id
        self.data = data
        self.exists = data is not None

    def set(self, data):
        self.data = dict(data)
        self.exists = True

    def update(self, data):
        self.data.update(data)

    def to_dict(self):
        return dict(self.data or {})


class _Query:
    def __init__(self, collection, field=None, expected=None):
        self.collection = collection
        self.field = field
        self.expected = expected

    def where(self, field, _operator, expected):
        return _Query(self.collection, field, expected)

    def stream(self):
        documents = self.collection.documents.values()
        if self.field is not None:
            documents = (
                document for document in documents
                if document.data and document.data.get(self.field) == self.expected
            )
        return list(documents)


class _Collection(_Query):
    def __init__(self):
        super().__init__(self)
        self.documents = {}
        self.next_id = 1

    def document(self, document_id=None):
        if document_id is None:
            document_id = f"review-{self.next_id}"
            self.next_id += 1
        return self.documents.setdefault(document_id, _Document(document_id))


class _Database:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


class DashboardPersistenceTests(unittest.TestCase):
    def test_dashboard_returns_explicit_error_when_firestore_is_not_configured(self):
        with patch.object(dashboard_router, "firebase_available", return_value=False):
            with self.assertRaises(HTTPException) as error:
                dashboard_router.get_dashboard(
                    user_id="user-1",
                    authenticated_user="user-1",
                )

        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("FIREBASE_SERVICE_ACCOUNT_PATH", error.exception.detail)
        self.assertIn("backend/.env", error.exception.detail)

    def test_dashboard_rejects_another_users_data(self):
        with self.assertRaises(HTTPException) as error:
            dashboard_router.get_dashboard(
                user_id="user-2",
                authenticated_user="user-1",
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_saved_review_and_quality_score_are_available_after_dashboard_reload(self):
        database = _Database()
        issue = {
            "title": "Potential issue",
            "severity": "warning",
            "team_standard": False,
        }

        with patch.object(firebase_service, "get_db", return_value=database):
            review = firebase_service.create_code_review(
                user_id="user-1",
                project_id="project-1",
                project_name="Example project",
                language="python",
                code="value = 1\n",
                review_data={
                    "status": "completed",
                    "issues_count": 1,
                    "warning_count": 1,
                    "quality_score": {"score": 87, "grade": "B"},
                    "static_issues": [issue],
                    "ai_issues": [],
                    "static_summary": {"counts": {"total": 1}},
                    "static_supported": True,
                    "ai_code_truncated": False,
                    "fallback_reason": None,
                    "feedback": [],
                    "auto_fix": None,
                },
            )

            restored_reviews = firebase_service.get_code_reviews("user-1")
            dashboard = firebase_service.get_dashboard_stats("user-1")
            other_user_dashboard = firebase_service.get_dashboard_stats("user-2")

        self.assertEqual(dashboard["total_reviews"], 1)
        self.assertEqual(dashboard["average_quality_score"], 87.0)
        self.assertEqual(dashboard["quality_scored_reviews"], 1)
        self.assertEqual(dashboard["total_issues"], 1)
        self.assertEqual(len(dashboard["recent_reviews"]), 1)
        self.assertEqual(dashboard["recent_reviews"][0]["id"], review["id"])
        self.assertEqual(dashboard["recent_reviews"][0]["status"], "completed")
        self.assertEqual(dashboard["recent_reviews"][0]["quality_score"], 87)
        self.assertEqual(restored_reviews[0]["code"], "value = 1\n")
        self.assertEqual(restored_reviews[0]["static_summary"], {"counts": {"total": 1}})
        self.assertEqual(restored_reviews[0]["quality_score"], {"score": 87, "grade": "B"})
        self.assertIsNone(restored_reviews[0]["auto_fix"])
        self.assertEqual(other_user_dashboard["total_reviews"], 0)
        self.assertEqual(other_user_dashboard["recent_reviews"], [])

    def test_dashboard_read_errors_are_not_reported_as_empty_results(self):
        database = _Database()

        with patch.object(firebase_service, "get_db", return_value=database):
            with patch.object(_Query, "stream", side_effect=RuntimeError("Firestore read failed")):
                with self.assertRaisesRegex(RuntimeError, "Firestore read failed"):
                    firebase_service.get_dashboard_stats("user-1")

    def test_dashboard_keeps_legacy_missing_issue_counts_unavailable(self):
        database = _Database()
        with patch.object(firebase_service, "get_db", return_value=database):
            firebase_service.create_code_review(
                user_id="user-1",
                project_id="project-legacy",
                project_name="Legacy project",
                language="python",
                code="value = 1\n",
                review_data={"status": "completed"},
            )
            dashboard = firebase_service.get_dashboard_stats("user-1")

        self.assertIsNone(dashboard["total_issues"])
        self.assertFalse(dashboard["issue_counts_complete"])
        self.assertIsNone(dashboard["critical_issues"])
        self.assertFalse(dashboard["critical_counts_complete"])
        self.assertIsNone(dashboard["recent_reviews"][0]["issues_count"])


if __name__ == "__main__":
    unittest.main()
