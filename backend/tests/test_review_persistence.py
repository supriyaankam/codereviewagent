import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import review as review_router
from app.routers import reviews as reviews_router
from app.schemas import ReviewRequest
from app.schemas import ReviewSessionResponse
from app.services import firebase_service


class _Document:
    def __init__(self, document_id):
        self.id = document_id
        self.data = None
        self.exists = False

    def set(self, data):
        self.data = dict(data)
        self.exists = True

    def to_dict(self):
        return dict(self.data or {})

    def get(self):
        return self


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
            document_id = f"document-{self.next_id}"
            self.next_id += 1
        return self.documents.setdefault(document_id, _Document(document_id))


class _Batch:
    def __init__(self, database):
        self.database = database
        self.pending = []

    def set(self, document, payload):
        self.pending.append((document, payload))

    def commit(self):
        if self.database.fail_batch:
            raise RuntimeError("Firestore batch commit failed")
        for document, payload in self.pending:
            document.set(payload)


class _Database:
    def __init__(self, fail_batch=False):
        self.collections = {}
        self.fail_batch = fail_batch

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())

    def batch(self):
        return _Batch(self)


class ReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.database = _Database()
        self.static_result = {
            "language": "python",
            "issues": [{
                "category": "security",
                "severity": "warning",
                "title": "Static finding",
                "description": "Static details.",
                "recommendation": "Use a secure implementation.",
                "line": 4,
                "source": "bandit",
            }],
            "summary": {
                "counts": {"total": 1, "warning": 1},
                "by_category": {"security": 1},
                "by_source": {"bandit": 1},
            },
            "supported": True,
            "has_syntax_error": False,
        }
        self.ai_result = {
            "overall_risk": "high",
            "summary": "Persisted AI summary.",
            "issues": [{
                "category": "security",
                "severity": "critical",
                "title": "AI finding",
                "explanation": "AI details.",
                "recommendation": "Read the secret from the environment.",
                "line": 2,
                "source": "ai",
            }],
            "ai_available": True,
        }

    def _create_and_restore(self, database=None):
        database = database or self.database
        body = ReviewRequest(
            user_id="user-1",
            project_id="project-1",
            project_name="payments",
            language="python",
            code="value = 1\npassword = 'placeholder'\ncharge()\n# finding\n",
        )
        with (
            patch.object(firebase_service, "get_db", return_value=database),
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value=self.static_result),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=self.ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(reviews_router, "firebase_available", return_value=True),
        ):
            created = review_router.full_review(body, authenticated_user="user-1")
            restored = reviews_router.get_review_by_id_route(
                review_id=created.review_id,
                authenticated_user="user-1",
            )
        return created, restored

    def test_review_save_and_detail_restore_include_linked_findings(self):
        created, restored = self._create_and_restore()
        review_id = created.review_id
        parent = self.database.collection(firebase_service.CODE_REVIEWS).document(review_id).to_dict()
        with patch.object(firebase_service, "get_db", return_value=self.database):
            linked_issues = firebase_service.get_issues_by_review(review_id)

        self.assertTrue(created.saved_to_db)
        self.assertEqual(parent["status"], "completed")
        self.assertEqual(parent["quality_score"], created.quality_score)
        self.assertEqual(parent["ai_issues"], self.ai_result["issues"])
        self.assertEqual(parent["static_issues"], self.static_result["issues"])
        self.assertEqual(parent["user_id"], "user-1")
        self.assertEqual(parent["source_code"], parent["code"])
        self.assertEqual(parent["ai_summary"], self.ai_result["summary"])
        self.assertEqual(parent["analysis_status"], "completed")
        self.assertEqual(parent["total_issue_count"], 2)
        self.assertTrue(parent["ai_available"])
        self.assertTrue(parent["static_supported"])
        self.assertEqual(parent["static_metadata"], {
            "language": "python",
            "supported": True,
            "has_syntax_error": False,
            "summary": self.static_result["summary"],
        })
        self.assertEqual(len(linked_issues), 2)
        self.assertEqual({issue["review_id"] for issue in linked_issues}, {review_id})

        self.assertEqual(restored["review_id"], review_id)
        self.assertEqual(restored["code"], parent["code"])
        self.assertEqual(restored["project_name"], "payments")
        self.assertEqual(restored["language"], "python")
        self.assertEqual(restored["summary"], "Persisted AI summary.")
        self.assertEqual(restored["quality_score"], created.quality_score)
        self.assertTrue(restored["ai_available"])
        self.assertTrue(restored["static_supported"])
        self.assertEqual(restored["static_metadata"], parent["static_metadata"])
        serializable_response = {
            **restored,
            "created_at": None,
            "updated_at": None,
            "completed_at": None,
        }
        session_response = ReviewSessionResponse.model_validate(serializable_response)
        self.assertEqual(session_response.static_metadata, parent["static_metadata"])
        self.assertEqual(
            [issue["title"] for issue in restored["ai_issues"]],
            ["AI finding"],
        )
        self.assertEqual(
            [issue["title"] for issue in restored["static_issues"]],
            ["Static finding"],
        )

    def test_five_independent_review_ids_restore_their_own_data_and_linked_findings(self):
        severities = ["critical", "error", "warning", "info", "critical"]
        created_ids = []
        scores = set()
        for index, severity in enumerate(severities, start=1):
            code = f"value_{index} = {index}\n"
            static_finding = {
                "category": "security",
                "severity": severity,
                "title": f"Static finding {index}",
                "description": f"Static details for review {index}.",
                "recommendation": f"Static fix {index}.",
                "line": index,
                "source": "bandit",
            }
            ai_finding = {
                "category": "correctness",
                "severity": severity,
                "title": f"AI finding {index}",
                "explanation": f"AI details for review {index}.",
                "recommendation": f"AI fix {index}.",
                "line": index + 10,
                "source": "ai",
            }
            static_result = {
                "language": "python",
                "issues": [static_finding],
                "summary": {
                    "counts": {"total": 1, severity: 1},
                    "by_category": {"security": 1},
                    "by_source": {"bandit": 1},
                },
                "supported": True,
            }
            ai_available = index % 2 == 1
            ai_result = {
                "overall_risk": "critical" if severity == "critical" else "medium",
                "summary": (
                    f"AI summary for review {index}."
                    if ai_available else f"AI unavailable for review {index}."
                ),
                "issues": [ai_finding] if ai_available else [],
                "static_issues": [] if ai_available else [static_finding],
                "ai_available": ai_available,
                "fallback_reason": None if ai_available else "Groq timed out.",
                "failure_category": None if ai_available else "timeout",
            }
            body = ReviewRequest(
                user_id="user-1",
                project_id=f"project-{index}",
                project_name=f"project-{index}",
                language="python",
                code=code,
            )
            with (
                patch.object(firebase_service, "get_db", return_value=self.database),
                patch.object(review_router, "firebase_available", return_value=True),
                patch.object(review_router, "get_team_standards_for_user", return_value=[]),
                patch.object(review_router, "analyze_code", return_value=static_result),
                patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
                patch.object(review_router, "run_ai_review", return_value=ai_result),
                patch.object(review_router, "memory_status", return_value={"available": False}),
                patch.object(reviews_router, "firebase_available", return_value=True),
            ):
                created = review_router.full_review(body, authenticated_user="user-1")
                restored = reviews_router.get_review_by_id_route(
                    review_id=created.review_id,
                    authenticated_user="user-1",
                )

            created_ids.append(created.review_id)
            scores.add(created.quality_score["score"])
            self.assertEqual(restored["code"], code)
            self.assertEqual(restored["project_name"], f"project-{index}")
            self.assertEqual(restored["ai_available"], ai_available)
            self.assertEqual(
                [issue["title"] for issue in restored["static_issues"]],
                [f"Static finding {index}"],
            )
            self.assertEqual(
                [issue["title"] for issue in restored["ai_issues"]],
                [f"AI finding {index}"] if ai_available else [],
            )
            if ai_available:
                self.assertEqual(restored["ai_summary"], f"AI summary for review {index}.")
            else:
                self.assertIsNone(restored.get("ai_summary"))
                self.assertEqual(restored["ai_failure_category"], "timeout")

        self.assertEqual(len(created_ids), 5)
        self.assertEqual(len(set(created_ids)), 5)
        self.assertGreater(len(scores), 1)

    def test_failed_linked_finding_write_does_not_create_completed_review(self):
        database = _Database(fail_batch=True)
        body = ReviewRequest(
            user_id="user-1",
            project_id="project-1",
            project_name="payments",
            language="python",
            code="value = 1\n",
        )
        with (
            patch.object(firebase_service, "get_db", return_value=database),
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value=self.static_result),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=self.ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
        ):
            with self.assertRaises(HTTPException) as error:
                review_router.full_review(body, authenticated_user="user-1")

        self.assertEqual(error.exception.status_code, 503)
        self.assertTrue(all(
            not document.exists
            for document in database.collection(firebase_service.CODE_REVIEWS).documents.values()
        ))
        self.assertTrue(all(
            not document.exists
            for document in database.collection(firebase_service.REVIEW_ISSUES).documents.values()
        ))


if __name__ == "__main__":
    unittest.main()
