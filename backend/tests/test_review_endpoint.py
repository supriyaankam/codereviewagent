import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import review as review_router
from app.routers import reviews as reviews_router
from app.schemas import ReviewRequest


class ReviewEndpointTests(unittest.TestCase):
    def _assert_review_rejected_before_analysis(
        self,
        body,
        expected_code,
        status_code,
        expected_detected_language=None,
    ):
        with (
            patch.object(review_router, "firebase_available") as firebase,
            patch.object(review_router, "analyze_code") as analyze,
            patch.object(review_router, "run_ai_review") as ai_review,
            patch.object(review_router, "calculate_quality_score") as score,
            patch.object(review_router, "create_code_review") as persist,
        ):
            with self.assertRaises(HTTPException) as error:
                review_router.full_review(body, authenticated_user="test-user")

        self.assertEqual(error.exception.status_code, status_code)
        self.assertEqual(error.exception.detail["code"], expected_code)
        self.assertEqual(error.exception.detail["selected_language"], body.language)
        if expected_detected_language is not None:
            self.assertEqual(
                error.exception.detail["detected_language"],
                expected_detected_language,
            )
        firebase.assert_not_called()
        analyze.assert_not_called()
        ai_review.assert_not_called()
        score.assert_not_called()
        persist.assert_not_called()

    def test_javascript_review_returns_detected_language(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="JavaScript",
            code='console.log("hello");',
        )
        static_result = {
            "language": "javascript",
            "issues": [],
            "summary": {"counts": {"total": 0}},
            "supported": True,
            "has_syntax_error": False,
        }
        ai_result = {
            "overall_risk": "info",
            "summary": "No findings reported.",
            "issues": [],
            "ai_available": True,
        }

        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value=static_result),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(review_router, "create_code_review", return_value={"id": "javascript-review"}),
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        self.assertEqual(response.detected_language, "javascript")
        self.assertEqual(response.language_validation_status, "matched")

    def test_javascript_selected_for_c_code_is_rejected_before_analysis(self):
        self._assert_review_rejected_before_analysis(
            ReviewRequest(
                user_id="test-user",
                project_name="demo",
                language="JavaScript",
                code="int main(void) { return _Generic(0, int: 1, default: 0); }",
            ),
            "language_mismatch",
            422,
            "c",
        )

    def test_python_selected_for_java_code_is_rejected_before_analysis(self):
        self._assert_review_rejected_before_analysis(
            ReviewRequest(
                user_id="test-user",
                project_name="demo",
                language="Python",
                code=(
                    'public class Main { public static void main(String[] args) { '
                    'System.out.println("hello"); } }'
                ),
            ),
            "language_mismatch",
            422,
            "java",
        )

    def test_ambiguous_typescript_requires_confirmation_before_analysis(self):
        self._assert_review_rejected_before_analysis(
            ReviewRequest(
                user_id="test-user",
                project_name="demo",
                language="JavaScript",
                code="const count: number = 1;",
            ),
            "language_ambiguous",
            409,
        )

    def test_confirmed_ambiguous_language_continues_review(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="JavaScript",
            code="const count: number = 1;",
            language_confirmed=True,
        )
        static_result = {
            "language": "javascript",
            "issues": [],
            "summary": {"counts": {"total": 0}},
            "supported": True,
            "has_syntax_error": False,
        }
        ai_result = {
            "overall_risk": "info",
            "summary": "No findings reported.",
            "issues": [],
            "ai_available": True,
        }

        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value=static_result) as analyze,
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result) as ai_review,
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(
                review_router,
                "create_code_review",
                return_value={"id": "confirmed-review"},
            ) as persist,
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        self.assertEqual(response.detected_language, "javascript")
        self.assertEqual(response.language_validation_status, "confirmed_ambiguous")
        self.assertTrue(response.saved_to_db)
        analyze.assert_called_once()
        ai_review.assert_called_once()
        persist.assert_called_once()

    def test_whitespace_only_code_is_rejected_before_analysis(self):
        self._assert_review_rejected_before_analysis(
            ReviewRequest(
                user_id="test-user",
                project_name="demo",
                language="JavaScript",
                code=" \n\t ",
            ),
            "empty_code",
            422,
        )

    def test_unsupported_language_is_rejected_before_analysis(self):
        self._assert_review_rejected_before_analysis(
            ReviewRequest(
                user_id="test-user",
                project_name="demo",
                language="Fortran",
                code="program hello\nend program hello",
            ),
            "unsupported_language",
            422,
        )

    def test_review_includes_enabled_team_standard_finding(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="python",
            code='api_key = "sk_live_FAKE_KEY_1234567890"\n',
        )
        standard = {
            "id": "credential-rule",
            "title": "Never hardcode credentials",
            "description": "Never hardcode API keys.",
            "category": "security",
            "severity": "critical",
            "recommended_solution": "Use an environment variable.",
            "enabled": True,
            "created_by": "test-user",
            "project_id": "global",
        }
        finding = {
            "category": "security",
            "severity": "critical",
            "title": "[Team Standard] Never hardcode credentials",
            "description": "This line assigns a literal credential value in source code.",
            "recommendation": "Use an environment variable.",
            "line": 1,
            "source": "team_standard",
            "column": 1,
            "team_standard": True,
            "standard_id": "credential-rule",
            "standard_name": "Never hardcode credentials",
        }
        static_result = {
            "issues": [finding],
            "summary": {"counts": {"total": 1, "critical": 1}, "by_category": {"security": 1}},
            "supported": True,
            "has_syntax_error": False,
        }
        ai_result = {
            "overall_risk": "critical",
            "summary": "A team policy was violated.",
            "issues": [],
            "ai_available": True,
        }

        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[standard]),
            patch.object(review_router, "analyze_code", return_value=static_result) as analyze,
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(review_router, "create_code_review", return_value={"id": "review"}),
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        self.assertTrue(response.static_issues[0].team_standard)
        self.assertEqual(response.static_issues[0].standard_name, "Never hardcode credentials")
        self.assertEqual(analyze.call_args.kwargs["team_standards"], [standard])

    def test_review_response_includes_deterministic_score(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="python",
            code='"""Module."""\n\ndef add(left, right):\n    """Add values."""\n    return left + right\n',
        )
        static_result = {
            "issues": [],
            "language": "python",
            "summary": {
                "counts": {"total": 0},
                "by_category": {},
                "by_source": {},
            },
            "supported": True,
            "has_syntax_error": False,
        }
        ai_result = {
            "overall_risk": "info",
            "summary": "No findings reported.",
            "issues": [],
            "ai_available": True,
        }

        with (
            patch.object(review_router, "analyze_code", return_value=static_result),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(review_router, "create_code_review", return_value={"id": "test-review"}) as persist_review,
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        self.assertEqual(response.quality_score["score"], 100)
        self.assertEqual(
            persist_review.call_args.kwargs["review_data"]["quality_score"],
            response.quality_score,
        )
        self.assertTrue(response.saved_to_db)
        self.assertEqual(response.review_id, "test-review")
        self.assertTrue(response.ai_available)
        self.assertTrue(response.static_supported)
        self.assertEqual(response.static_metadata, {
            "language": "python",
            "supported": True,
            "has_syntax_error": False,
            "summary": static_result["summary"],
        })
        self.assertFalse(response.ai_code_truncated)
        saved_review = persist_review.call_args.kwargs
        self.assertEqual(saved_review["code"], body.code)
        review_data = saved_review["review_data"]
        self.assertEqual(review_data["source_code"], body.code)
        self.assertEqual(review_data["user_id"], body.user_id)
        self.assertEqual(review_data["ai_summary"], ai_result["summary"])
        self.assertEqual(review_data["analysis_status"], "completed")
        self.assertEqual(review_data["total_issue_count"], 0)
        self.assertEqual(saved_review["review_data"]["static_summary"], static_result["summary"])
        self.assertTrue(saved_review["review_data"]["static_supported"])
        self.assertEqual(saved_review["review_data"]["static_metadata"], response.static_metadata)
        self.assertIn("completed_at", saved_review["review_data"])
        self.assertEqual(saved_review["review_data"]["feedback"], [])
        self.assertIsNone(saved_review["review_data"]["auto_fix"])

    def test_static_only_review_keeps_findings_out_of_ai_issues_and_counts_once(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="python",
            code="value = 1\n",
        )
        finding = {
            "title": "Static finding",
            "severity": "warning",
            "category": "quality",
            "description": "A static issue.",
            "recommendation": "Fix the issue.",
            "line": 1,
            "source": "ast",
        }
        static_result = {
            "issues": [finding],
            "summary": {"counts": {"total": 1, "warning": 1}},
            "supported": True,
            "language": "python",
        }
        ai_result = {
            "overall_risk": "medium",
            "summary": "AI review unavailable.",
            "issues": [],
            "static_issues": [finding],
            "ai_available": False,
            "fallback_reason": "Groq timed out.",
            "failure_category": "timeout",
        }
        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value=static_result),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(
                review_router,
                "create_code_review",
                return_value={"id": "static-only"},
            ) as persist,
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        review_data = persist.call_args.kwargs["review_data"]
        self.assertFalse(response.ai_available)
        self.assertEqual(response.ai_issues, [])
        self.assertEqual([issue.title for issue in response.static_issues], ["Static finding"])
        self.assertEqual(review_data["ai_issues"], [])
        self.assertEqual(review_data["static_issues"], [finding])
        self.assertEqual(review_data["analysis_status"], "partial")
        self.assertEqual(review_data["total_issue_count"], 1)
        self.assertEqual(review_data["warning_count"], 1)
        self.assertEqual(len(persist.call_args.kwargs["linked_issues"]), 1)

    def test_failed_static_and_ai_analysis_does_not_invent_score_or_counts(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="Python",
            code="value = 1\n",
        )
        ai_result = {
            "overall_risk": None,
            "summary": "AI review unavailable.",
            "issues": [],
            "static_issues": [],
            "ai_available": False,
            "fallback_reason": "Groq returned invalid JSON.",
            "failure_category": "response_json",
        }
        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", side_effect=RuntimeError("analyzer failed")),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(review_router, "calculate_quality_score") as calculate_score,
            patch.object(
                review_router,
                "create_code_review",
                return_value={"id": "failed-review"},
            ) as persist,
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        review_data = persist.call_args.kwargs["review_data"]
        self.assertEqual(response.analysis_status, "failed")
        self.assertIsNone(response.quality_score)
        calculate_score.assert_not_called()
        self.assertIsNone(response.overall_risk)
        self.assertIsNone(response.total_issue_count)
        self.assertIsNone(response.static_supported)
        self.assertIsNone(review_data["quality_score"])
        self.assertIsNone(review_data["total_issue_count"])
        self.assertEqual(review_data["static_analysis_error"], "analyzer_error")
        self.assertEqual(review_data["ai_failure_category"], "response_json")

    def test_auto_fix_reanalysis_uses_corrected_source_and_keeps_unresolved_findings(self):
        source = "def parse(value=[]):\n    return value\n"
        corrected = "def parse(value=[]):\n    return value\n# attempted fix did not resolve the finding\n"
        finding = {
            "title": "Mutable default argument",
            "severity": "warning",
            "category": "quality",
            "line": 1,
            "source": "ast",
        }
        body = reviews_router.ReviewFixRequest(
            code=source,
            language="python",
            source_language="python",
            issues=[finding],
        )
        with (
            patch.object(reviews_router, "auto_fix_finding", return_value={
                "fixed_code": corrected,
                "changes": [],
                "summary": "Attempted a correction.",
                "ai_available": True,
                "source_language": "python",
                "target_language": "python",
                "is_conversion": False,
            }),
            patch.object(reviews_router, "analyze_code", return_value={
                "issues": [finding],
                "summary": {"counts": {"total": 1, "warning": 1}},
                "supported": True,
                "has_syntax_error": False,
                "language": "python",
            }) as analyze,
        ):
            response = reviews_router.fix_review_route(
                body,
                _authenticated_user="test-user",
            )

        self.assertEqual(analyze.call_args.kwargs["code"], corrected)
        self.assertNotEqual(analyze.call_args.kwargs["code"], source)
        self.assertEqual(response.reanalysis["issues"], [finding])
        self.assertIn("still has selected finding", response.validation["message"])

    def test_review_persists_unsupported_analyzer_metadata_and_message(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="JavaScript",
            code="const value = 1;\n",
            language_confirmed=True,
        )
        static_result = {
            "language": "javascript",
            "issues": [],
            "summary": {
                "counts": {"total": 0},
                "by_category": {},
                "by_source": {},
            },
            "supported": False,
            "has_syntax_error": False,
            "message": "Static analyzer does not support JavaScript.",
        }
        ai_result = {
            "overall_risk": "info",
            "summary": "No findings reported.",
            "issues": [],
            "ai_available": True,
        }

        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value=static_result),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value=ai_result),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(review_router, "calculate_quality_score") as calculate_score,
            patch.object(
                review_router,
                "create_code_review",
                return_value={"id": "unsupported-review"},
            ) as persist_review,
        ):
            response = review_router.full_review(body, authenticated_user="test-user")

        expected_metadata = {
            "language": "javascript",
            "supported": False,
            "has_syntax_error": False,
            "summary": static_result["summary"],
            "message": static_result["message"],
        }
        self.assertEqual(response.static_metadata, expected_metadata)
        self.assertEqual(
            persist_review.call_args.kwargs["review_data"]["static_metadata"],
            expected_metadata,
        )
        self.assertFalse(response.static_supported)
        self.assertIsNone(response.quality_score)
        calculate_score.assert_not_called()

    def test_review_persistence_failure_returns_error_instead_of_success(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="python",
            code="value = 1\n",
        )
        with (
            patch.object(review_router, "firebase_available", return_value=True),
            patch.object(review_router, "get_team_standards_for_user", return_value=[]),
            patch.object(review_router, "analyze_code", return_value={
                "issues": [],
                "summary": {"counts": {"total": 0}},
                "supported": True,
                "has_syntax_error": False,
            }),
            patch.object(review_router, "retrieve_relevant_experiences", return_value=[]),
            patch.object(review_router, "run_ai_review", return_value={
                "overall_risk": "info",
                "summary": "No issues found.",
                "issues": [],
                "ai_available": True,
            }),
            patch.object(review_router, "memory_status", return_value={"available": False}),
            patch.object(
                review_router,
                "create_code_review",
                side_effect=RuntimeError("Firestore write unavailable"),
            ),
        ):
            with self.assertRaises(HTTPException) as error:
                review_router.full_review(body, authenticated_user="test-user")

        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("could not be saved to Firestore", error.exception.detail)

    def test_review_without_firestore_fails_before_analysis(self):
        body = ReviewRequest(
            user_id="test-user",
            project_name="demo",
            language="python",
            code="value = 1\n",
        )
        with (
            patch.object(review_router, "firebase_available", return_value=False),
            patch.object(review_router, "analyze_code") as analyze,
        ):
            with self.assertRaises(HTTPException) as error:
                review_router.full_review(body, authenticated_user="test-user")

        self.assertEqual(error.exception.status_code, 503)
        analyze.assert_not_called()

    def test_large_individual_finding_uses_targeted_fix_route(self):
        source = "def check(value):\n    return value / 0\n" + ("# context\n" * 2_000)
        issue = {
            "title": "Possible division by zero",
            "line": 2,
            "severity": "warning",
            "category": "correctness",
            "source": "pylint",
        }
        body = reviews_router.ReviewFixRequest(
            code=source,
            language="python",
            source_language="python",
            issues=[issue],
            filename="src/check.py",
        )
        fix_result = {
            "fixed_code": source.replace("return value / 0", "return value"),
            "changes": [],
            "summary": "Updated the selected function.",
            "ai_available": True,
            "source_language": "python",
            "target_language": "python",
            "is_conversion": False,
            "original_region": "def check(value):\n    return value / 0\n",
            "replacement_region": "def check(value):\n    return value\n",
            "patch_start_line": 1,
            "patch_end_line": 2,
        }

        with patch.object(reviews_router, "auto_fix_finding", return_value=fix_result) as targeted_fix:
            with patch.object(
                reviews_router,
                "analyze_code",
                return_value={"issues": [], "supported": True, "has_syntax_error": False},
            ):
                response = reviews_router.fix_review_route(body, _authenticated_user="user-1")

        self.assertEqual(targeted_fix.call_args.kwargs["code"], source)
        self.assertEqual(targeted_fix.call_args.kwargs["issue"], issue)
        self.assertEqual(response.original_region, fix_result["original_region"])
        self.assertEqual(response.proposed_region, fix_result["replacement_region"])
        self.assertEqual(response.line_range, {"start": 1, "end": 2})


if __name__ == "__main__":
    unittest.main()
