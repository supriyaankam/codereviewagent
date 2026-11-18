import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import reviews as reviews_router
from app.schemas import CodeReviewResponse, ReviewAutoFixUpdate, ReviewSessionResponse


class ReviewRestoreTests(unittest.TestCase):
    def setUp(self):
        self.saved_review = {
            "id": "review-1",
            "author_id": "user-1",
            "project_id": "global",
            "project_name": "payments",
            "language": "python",
            "code": "password = 'placeholder'\n",
            "status": "completed",
            "overall_risk": "medium",
            "summary": "Review summary",
            "ai_issues": [{"title": "Finding", "severity": "warning", "line": 1}],
            "static_issues": [{"title": "Static finding", "severity": "info", "line": 2}],
            "static_summary": {"counts": {"total": 1}},
            "static_supported": True,
            "quality_score": {"score": 82, "categories": {"security": 75}},
            "ai_available": True,
            "ai_code_truncated": False,
            "feedback": [],
            "auto_fix": None,
            "created_at": "2026-04-01T10:00:00+00:00",
        }

    def test_latest_review_restores_complete_user_owned_session(self):
        feedback = [{"id": "feedback-1", "review_id": "review-1", "issue_id": "finding:1:password"}]
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_reviews", return_value=[
                {"id": "pending", "status": "pending"},
                self.saved_review,
            ]),
            patch.object(reviews_router, "get_issues_by_review", return_value=[]),
            patch.object(reviews_router, "get_feedback", return_value=feedback),
        ):
            result = reviews_router.get_latest_review_route(authenticated_user="user-1")

        restored = ReviewSessionResponse.model_validate(result)
        self.assertEqual(restored.review_id, "review-1")
        self.assertEqual(restored.code, self.saved_review["code"])
        self.assertEqual(restored.ai_issues, self.saved_review["ai_issues"])
        self.assertEqual(restored.static_issues, self.saved_review["static_issues"])
        self.assertEqual(restored.quality_score["score"], 82)
        self.assertEqual(restored.feedback, feedback)

    def test_latest_review_returns_empty_when_user_has_no_completed_review(self):
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_reviews", return_value=[]),
        ):
            result = reviews_router.get_latest_review_route(authenticated_user="user-1")
        self.assertIsNone(result)

    def test_history_review_is_owner_checked_and_includes_saved_feedback(self):
        feedback = [{"review_id": "review-1", "decision": "accepted"}]
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=self.saved_review),
            patch.object(reviews_router, "get_issues_by_review", return_value=[]),
            patch.object(reviews_router, "get_feedback", return_value=feedback),
        ):
            result = reviews_router.get_review_by_id_route(
                review_id="review-1",
                authenticated_user="user-1",
            )
        self.assertEqual(result["code"], self.saved_review["code"])
        self.assertEqual(result["feedback"], feedback)

        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=self.saved_review),
        ):
            with self.assertRaises(HTTPException) as error:
                reviews_router.get_review_by_id_route(
                    review_id="review-1",
                    authenticated_user="another-user",
                )
        self.assertEqual(error.exception.status_code, 404)

    def test_history_review_restores_legacy_findings_from_review_issues(self):
        legacy_review = {
            "id": "review-legacy",
            "author_id": "user-1",
            "project_id": "global",
            "project_name": "test",
            "language": "python",
            "code": "password = 'example'\n",
            "status": "completed",
            "overall_risk": "high",
            "summary": "The original AI summary.",
            "quality_score": {"score": 64, "categories": {"security": 40}},
            "ai_available": True,
            "static_supported": True,
            "issues_count": 2,
            "warning_count": 1,
        }
        issues = [
            {
                "id": "issue-ai-1",
                "source": "ai",
                "title": "Credential is hardcoded",
                "description": "A literal password is stored in source.",
                "recommendation": "Read the secret from an environment variable.",
                "line": 1,
                "severity": "critical",
                "category": "security",
            },
            {
                "id": "issue-static-1",
                "source": "bandit",
                "title": "Possible hardcoded password",
                "description": "A password literal was detected.",
                "recommendation": "Use a secret store.",
                "line": 1,
                "severity": "warning",
                "category": "security",
            },
        ]
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=legacy_review),
            patch.object(reviews_router, "get_issues_by_review", return_value=issues),
            patch.object(reviews_router, "get_feedback", return_value=[]),
        ):
            result = reviews_router.get_review_by_id_route(
                review_id="review-legacy",
                authenticated_user="user-1",
            )

        self.assertEqual(result["id"], "review-legacy")
        self.assertEqual(result["review_id"], "review-legacy")
        self.assertEqual(result["project_name"], "test")
        self.assertEqual(result["language"], "python")
        self.assertEqual(result["code"], legacy_review["code"])
        self.assertEqual(result["overall_risk"], "high")
        self.assertEqual(result["summary"], "The original AI summary.")
        self.assertEqual(result["quality_score"], legacy_review["quality_score"])
        self.assertEqual(result["issues_count"], 2)
        self.assertEqual(result["ai_issues"][0]["title"], issues[0]["title"])
        self.assertEqual(result["static_issues"][0]["title"], issues[1]["title"])
        self.assertEqual(result["ai_issues"][0]["explanation"], issues[0]["description"])
        self.assertEqual(result["ai_issues"][0]["recommendation"], issues[0]["recommendation"])
        self.assertEqual(result["static_issues"][0]["line"], 1)
        self.assertNotIn("ai_code_truncated", result)

    def test_history_restoration_uses_each_document_id_for_its_findings(self):
        reviews = {
            "review-modern-alpha": {
                "id": "review-modern-alpha",
                "author_id": "user-1",
                "project_name": "payments",
                "language": "python",
                "code": "charge()\n",
                "overall_risk": "high",
                "summary": "Modern review summary.",
                "quality_score": {"score": 91},
                "issues_count": 3,
                "ai_issues": [{
                    "title": "Inline AI finding",
                    "severity": "critical",
                    "line": 1,
                    "source": "ai",
                }],
                "static_issues": [{
                    "title": "Inline static finding",
                    "severity": "warning",
                    "line": 2,
                    "source": "static",
                }],
            },
            "review-legacy-beta": {
                "id": "review-legacy-beta",
                "author_id": "user-1",
                "project": "reports",
                "programming_language": "JavaScript",
                "source_code": "renderReport();\n",
                "risk_level": "medium",
                "ai_result": {
                    "summary": "Legacy nested summary.",
                    "available": True,
                },
                "qualityScore": 73,
            },
            "review-old-gamma": {
                "id": "review-old-gamma",
                "author_id": "user-1",
                "project_name": "worker",
                "language": "Go",
                "original_code": "runWorker()\n",
                "overall_risk": "low",
                "summary": "Older review summary.",
            },
        }
        issues_by_review = {
            "review-modern-alpha": [
                {
                    "source": "ai",
                    "title": "Inline AI finding",
                    "severity": "critical",
                    "line": 1,
                },
                {
                    "source": "bandit",
                    "title": "Alpha static linked finding",
                    "description": "Alpha finding details.",
                    "recommendation": "Apply the alpha fix.",
                    "severity": "warning",
                    "line": 4,
                },
            ],
            "review-legacy-beta": [
                {
                    "source": "ai",
                    "title": "Beta AI finding",
                    "description": "Beta AI explanation.",
                    "recommendation": "Apply the beta AI fix.",
                    "severity": "warning",
                    "line": 3,
                },
                {
                    "source": "eslint",
                    "title": "Beta static finding",
                    "description": "Beta static explanation.",
                    "recommendation": "Apply the beta static fix.",
                    "severity": "info",
                    "line": 5,
                },
            ],
            "review-old-gamma": [
                {
                    "source": "ai",
                    "title": "Gamma AI finding",
                    "description": "Gamma AI explanation.",
                    "severity": "critical",
                    "line": 2,
                },
                {
                    "source": "static",
                    "title": "Gamma static finding",
                    "description": "Gamma static explanation.",
                    "recommendation": "Apply the gamma fix.",
                    "severity": "warning",
                    "line": 6,
                },
            ],
        }
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(
                reviews_router,
                "get_code_review_by_id",
                side_effect=lambda review_id: reviews.get(review_id),
            ) as load_review,
            patch.object(
                reviews_router,
                "get_issues_by_review",
                side_effect=lambda review_id: issues_by_review[review_id],
            ) as load_issues,
            patch.object(reviews_router, "get_feedback", return_value=[]),
        ):
            restored_by_id = {}
            for review_id in reviews:
                with self.subTest(review_id=review_id):
                    restored_by_id[review_id] = reviews_router.get_review_by_id_route(
                        review_id=review_id,
                        authenticated_user="user-1",
                    )

        self.assertEqual(
            [call.args[0] for call in load_review.call_args_list],
            list(reviews),
        )
        self.assertEqual(
            [call.args[0] for call in load_issues.call_args_list],
            list(reviews),
        )
        modern = restored_by_id["review-modern-alpha"]
        self.assertEqual(modern["code"], "charge()\n")
        self.assertEqual(modern["project_name"], "payments")
        self.assertEqual(modern["language"], "python")
        self.assertEqual(modern["summary"], "Modern review summary.")
        self.assertEqual(modern["quality_score"], {"score": 91})
        self.assertEqual(
            [issue["title"] for issue in modern["ai_issues"]],
            ["Inline AI finding"],
        )
        self.assertEqual(
            [issue["title"] for issue in modern["static_issues"]],
            ["Inline static finding", "Alpha static linked finding"],
        )

        legacy = restored_by_id["review-legacy-beta"]
        self.assertEqual(legacy["code"], "renderReport();\n")
        self.assertEqual(legacy["project_name"], "reports")
        self.assertEqual(legacy["language"], "JavaScript")
        self.assertEqual(legacy["summary"], "Legacy nested summary.")
        self.assertEqual(legacy["quality_score"], {"score": 73})
        self.assertEqual([issue["title"] for issue in legacy["ai_issues"]], ["Beta AI finding"])
        self.assertEqual([issue["title"] for issue in legacy["static_issues"]], ["Beta static finding"])

        older = restored_by_id["review-old-gamma"]
        self.assertEqual(older["code"], "runWorker()\n")
        self.assertEqual(older["project_name"], "worker")
        self.assertEqual(older["language"], "Go")
        self.assertEqual(older["summary"], "Older review summary.")
        self.assertNotIn("quality_score", older)
        self.assertEqual([issue["title"] for issue in older["ai_issues"]], ["Gamma AI finding"])
        self.assertEqual([issue["title"] for issue in older["static_issues"]], ["Gamma static finding"])

    def test_history_response_does_not_default_missing_issue_counts_to_zero(self):
        review = CodeReviewResponse.model_validate({
            "id": "saved-review",
            "author_id": "user-1",
            "project_id": "global",
            "project_name": "legacy",
            "language": "python",
            "code": "pass\n",
            "status": "completed",
        })

        self.assertIsNone(review.issues_count)
        self.assertIsNone(review.critical_count)
        self.assertIsNone(review.warning_count)
        self.assertIsNone(review.info_count)

    def test_legacy_review_maps_nested_analysis_and_source_code_fields(self):
        legacy_review = {
            "id": "review-old",
            "author_id": "user-1",
            "project": "test",
            "programming_language": "Python",
            "source_code": "token = 'example'\n",
            "risk_level": "high",
            "ai_result": {
                "summary": "Original saved AI summary.",
                "available": True,
                "issues": [{
                    "title": "Credential in source",
                    "severity": "critical",
                    "line_number": 1,
                    "description": "A literal credential was found.",
                    "suggested_fix": "Use a secure environment variable.",
                    "source": "ai",
                }],
            },
            "static_result": {
                "supported": True,
                "issues": [{
                    "name": "Static password check",
                    "severity": "warning",
                    "line_number": 1,
                    "message": "A sensitive value is assigned directly.",
                    "recommended_solution": "Load it from a secret manager.",
                    "source": "bandit",
                }],
            },
            "quality_score": {"score": 61, "categories": {"security": 40}},
            "issue_count": 2,
            "warning_issues": 1,
            "auto_fix_result": {
                "fixed_code": "token = read_secret()\n",
                "accepted": True,
                "reanalysis": {"issues": []},
            },
            "status": "completed",
        }
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=legacy_review),
            patch.object(reviews_router, "get_issues_by_review", return_value=[]),
            patch.object(reviews_router, "get_feedback", return_value=[]),
        ):
            result = reviews_router.get_review_by_id_route(
                review_id="review-old",
                authenticated_user="user-1",
            )

        self.assertEqual(result["project_name"], "test")
        self.assertEqual(result["language"], "Python")
        self.assertEqual(result["code"], "token = 'example'\n")
        self.assertEqual(result["overall_risk"], "high")
        self.assertEqual(result["summary"], "Original saved AI summary.")
        self.assertEqual(result["quality_score"]["score"], 61)
        self.assertEqual(result["issues_count"], 2)
        self.assertEqual(result["warning_count"], 1)
        self.assertTrue(result["ai_available"])
        self.assertTrue(result["static_supported"])
        self.assertEqual(result["ai_issues"][0]["line"], 1)
        self.assertEqual(
            result["ai_issues"][0]["recommendation"],
            "Use a secure environment variable.",
        )
        self.assertEqual(result["static_issues"][0]["line"], 1)
        self.assertEqual(
            result["static_issues"][0]["recommendation"],
            "Load it from a secret manager.",
        )
        self.assertTrue(result["auto_fix"]["accepted"])

    def test_static_only_legacy_summary_is_not_reported_as_ai_summary(self):
        static_summary = {"counts": {"total": 1, "warning": 1}}
        legacy_review = {
            "id": "review-static-only",
            "author_id": "user-1",
            "project_name": "legacy",
            "language": "Python",
            "source_code": "value = 1\n",
            "summary": "AI review unavailable — timeout.",
            "ai_available": False,
            "ai_issues": [],
            "static_result": {
                "supported": True,
                "summary": static_summary,
                "issues": [{
                    "title": "Static finding",
                    "severity": "warning",
                    "line": 1,
                    "description": "Saved static detail.",
                    "recommendation": "Apply the static fix.",
                    "source": "ast",
                }],
            },
            "ai_failure_reason": "Groq request timed out.",
            "ai_failure_category": "timeout",
        }
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=legacy_review),
            patch.object(reviews_router, "get_issues_by_review", return_value=[]),
            patch.object(reviews_router, "get_feedback", return_value=[]),
        ):
            result = reviews_router.get_review_by_id_route(
                review_id="review-static-only",
                authenticated_user="user-1",
            )

        self.assertEqual(result["static_summary"], static_summary)
        self.assertFalse(result["ai_available"])
        self.assertNotIn("ai_summary", result)
        self.assertEqual(result["ai_failure_category"], "timeout")
        self.assertEqual(result["static_issues"][0]["title"], "Static finding")

    def test_committed_legacy_review_fields_are_returned_without_new_defaults(self):
        legacy_review = {
            "id": "review-committed-legacy",
            "author_id": "user-1",
            "project_id": "global",
            "project_name": "test",
            "language": "Python",
            "code": "password = 'original'\n",
            "status": "completed",
            "overall_risk": "high",
            "summary": "Original saved AI summary.",
            "issues_count": 2,
            "critical_count": 1,
            "warning_count": 1,
            "info_count": 0,
            "ai_issues": [{
                "title": "Hardcoded password",
                "severity": "critical",
                "line": 1,
                "explanation": "A literal password appears in source.",
                "recommendation": "Load it from an environment variable.",
                "source": "ai",
            }],
            "static_issues": [{
                "title": "Weak credential handling",
                "severity": "warning",
                "line": 1,
                "description": "A credential is assigned directly.",
                "recommendation": "Use a secrets manager.",
                "source": "bandit",
            }],
            "ai_available": True,
            "auto_fix": {"fixed_code": "password = read_secret()\n", "accepted": True},
        }
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=legacy_review),
            patch.object(reviews_router, "get_issues_by_review", return_value=[]),
            patch.object(reviews_router, "get_feedback", return_value=[]),
        ):
            result = reviews_router.get_review_by_id_route(
                review_id="review-committed-legacy",
                authenticated_user="user-1",
            )

        self.assertEqual(result["code"], legacy_review["code"])
        self.assertEqual(result["project_name"], "test")
        self.assertEqual(result["language"], "Python")
        self.assertEqual(result["overall_risk"], "high")
        self.assertEqual(result["summary"], "Original saved AI summary.")
        self.assertEqual(result["ai_issues"][0]["title"], "Hardcoded password")
        self.assertEqual(result["static_issues"][0]["title"], "Weak credential handling")
        self.assertEqual(result["issues_count"], 2)
        self.assertEqual(result["critical_count"], 1)
        self.assertEqual(result["warning_count"], 1)
        self.assertTrue(result["ai_available"])
        self.assertNotIn("quality_score", result)
        self.assertNotIn("static_supported", result)
        self.assertNotIn("static_metadata", result)
        self.assertEqual(result["auto_fix"], legacy_review["auto_fix"])

    def test_hbssynca_legacy_review_restores_five_separate_static_findings(self):
        source = "\n".join(f"value_{line} = {line}" for line in range(1, 49))
        legacy_review = {
            "id": "hbSSyNCA",
            "author_id": "user-1",
            "project_id": "global",
            "project_name": "test",
            "language": "Python",
            "code": source,
            "status": "completed",
            "overall_risk": "medium",
            "summary": "Original saved summary.",
            "issues_count": 5,
            "critical_count": 0,
            "warning_count": 5,
            "info_count": 0,
            "ai_available": True,
            "auto_fix": {"fixed_code": source, "accepted": False},
        }
        issues = [
            {
                "id": f"static-{index}",
                "review_id": "hbSSyNCA",
                "title": f"Static finding {index}",
                "description": f"Saved description {index}.",
                "recommendation": f"Saved suggested fix {index}.",
                "severity": "warning",
                "line": index * 3,
                "category": "security",
                "source": "static",
            }
            for index in range(1, 6)
        ]

        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=legacy_review),
            patch.object(reviews_router, "get_issues_by_review", return_value=[]),
            patch.object(reviews_router, "get_issues_by_review", return_value=issues) as load_issues,
            patch.object(reviews_router, "get_feedback", return_value=[]),
        ):
            result = reviews_router.get_review_by_id_route(
                review_id="hbSSyNCA",
                authenticated_user="user-1",
            )

        load_issues.assert_called_once_with("hbSSyNCA")
        self.assertEqual(result["code"], source)
        self.assertEqual(len(result["code"].splitlines()), 48)
        self.assertEqual(result["project_name"], "test")
        self.assertEqual(result["language"], "Python")
        self.assertEqual(result["summary"], "Original saved summary.")
        self.assertEqual(result["issues_count"], 5)
        self.assertNotIn("ai_issues", result)
        self.assertEqual(len(result["static_issues"]), 5)
        self.assertEqual(
            [issue["title"] for issue in result["static_issues"]],
            [issue["title"] for issue in issues],
        )
        self.assertEqual(
            [issue["explanation"] for issue in result["static_issues"]],
            [issue["description"] for issue in issues],
        )
        self.assertEqual(
            [issue["line"] for issue in result["static_issues"]],
            [3, 6, 9, 12, 15],
        )
        self.assertEqual(
            [issue["recommendation"] for issue in result["static_issues"]],
            [f"Saved suggested fix {index}." for index in range(1, 6)],
        )
        self.assertNotIn("quality_score", result)
        self.assertTrue(result["ai_available"])
        self.assertNotIn("static_supported", result)
        self.assertEqual(result["auto_fix"], legacy_review["auto_fix"])

    def test_review_detail_route_does_not_apply_response_model_defaults(self):
        route = next(
            route for route in reviews_router.router.routes
            if getattr(route, "path", None) == "/api/reviews/{review_id}"
            and "GET" in getattr(route, "methods", set())
        )
        self.assertIsNone(route.response_model)

    def test_autofix_state_is_saved_only_for_the_review_owner(self):
        proposal = ReviewAutoFixUpdate(
            original_code="value = 1\n",
            fixed_code="value = 2\n",
            changes=[{"issue": "Finding", "change": "Corrected the value."}],
            reanalysis={"issues": [], "supported": True},
            validation={"status": "passed"},
            accepted=True,
        )
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=self.saved_review),
            patch.object(reviews_router, "update_code_review") as update_review,
        ):
            result = reviews_router.update_review_autofix_route(
                review_id="review-1",
                body=proposal,
                authenticated_user="user-1",
            )
        self.assertTrue(result["saved"])
        self.assertEqual(update_review.call_args.args[1]["auto_fix"]["fixed_code"], "value = 2\n")
        self.assertTrue(update_review.call_args.args[1]["auto_fix"]["accepted"])

        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(reviews_router, "get_code_review_by_id", return_value=self.saved_review),
            patch.object(reviews_router, "update_code_review") as update_review,
        ):
            with self.assertRaises(HTTPException) as error:
                reviews_router.update_review_autofix_route(
                    review_id="review-1",
                    body=proposal,
                    authenticated_user="another-user",
                )
        self.assertEqual(error.exception.status_code, 404)
        update_review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
