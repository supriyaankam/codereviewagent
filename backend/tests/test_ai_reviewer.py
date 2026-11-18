import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.ai_reviewer import run_ai_review


def _completion(content, finish_reason="stop", model="openai/gpt-oss-120b"):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )],
    )


class AIReviewerResponseTests(unittest.TestCase):
    def setUp(self):
        self.valid_payload = {
            "overall_risk": "medium",
            "summary": "A maintainability issue was found.",
            "issues": [{
                "severity": "warning",
                "category": "quality",
                "title": "Repeated logic",
                "explanation": "The same operation is repeated.",
                "recommendation": "Extract a helper.",
                "line": 4,
                "source": "ai",
                "confidence": "high",
                "beginner_explanation": {
                    "problem": "Logic repeats.",
                    "cause": "The operation is written twice.",
                    "impact": "Changes can become inconsistent.",
                    "fix": "Extract a helper.",
                    "example": "",
                    "concept": "Reuse",
                },
            }],
        }
        self.static_issue = {
            "title": "Static syntax finding",
            "severity": "critical",
            "category": "syntax",
            "description": "Syntax is invalid.",
            "recommendation": "Correct the syntax.",
            "line": 1,
            "source": "syntax",
        }

    def _run(self, responses):
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(response) for response in responses
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            result = run_ai_review(
                code="value = 1\nsecond = 2\nthird = 3\ncall(value)\n",
                language="python",
                project_name="test-project",
                static_issues=[],
            )
        return result, client

    def test_valid_json_is_accepted_and_requests_json_mode(self):
        result, client = self._run([json.dumps(self.valid_payload)])

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["summary"], self.valid_payload["summary"])
        self.assertEqual(result["issues"][0]["title"], "Repeated logic")
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )
        client.chat.completions.create.assert_called_once()

    def test_markdown_wrapped_json_is_accepted(self):
        response = f"```json\n{json.dumps(self.valid_payload)}\n```"
        result, client = self._run([response])

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["issues"][0]["line"], 4)
        client.chat.completions.create.assert_called_once()

    def test_json_surrounded_by_explanatory_text_is_accepted(self):
        response = f"Review result:\n{json.dumps(self.valid_payload)}\nEnd of response."
        result, client = self._run([response])

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["issues"][0]["title"], "Repeated logic")
        client.chat.completions.create.assert_called_once()

    def test_malformed_response_retries_then_accepts_valid_json(self):
        result, client = self._run([
            "This is not JSON.",
            json.dumps(self.valid_payload),
        ])

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["issues"][0]["title"], "Repeated logic")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_malformed_responses_fall_back_only_to_static_findings(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion("not JSON <sensitive-response-fragment>"),
            _completion("still not JSON"),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            with self.assertLogs("app.services.ai_reviewer", level="INFO") as logs:
                result = run_ai_review(
                    code="bad(",
                    language="python",
                    project_name="test-project",
                    static_issues=[self.static_issue],
                )

        self.assertFalse(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["static_issues"]), 1)
        self.assertEqual(result["static_issues"][0]["title"], "Static syntax finding")
        self.assertEqual(result["failure_category"], "response_json")
        self.assertIn("invalid response after retry", result["fallback_reason"])
        self.assertEqual(client.chat.completions.create.call_count, 2)
        diagnostic_log = "\n".join(logs.output)
        self.assertIn("model=openai/gpt-oss-120b", diagnostic_log)
        self.assertIn("response_model=openai/gpt-oss-120b", diagnostic_log)
        self.assertIn("finish_reason=stop", diagnostic_log)
        self.assertIn("content_type=str", diagnostic_log)
        self.assertIn("attempt=1", diagnostic_log)
        self.assertIn("attempt=2", diagnostic_log)
        self.assertNotIn("<sensitive-response-fragment>", diagnostic_log)

    def test_empty_responses_retry_then_fall_back_to_static_findings(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(None),
            _completion(""),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            result = run_ai_review(
                code="bad(",
                language="python",
                project_name="test-project",
                static_issues=[self.static_issue],
            )

        self.assertFalse(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["static_issues"]), 1)
        self.assertIn("empty response", result["fallback_reason"])
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_ai_findings_matching_static_findings_are_not_duplicated(self):
        matching_payload = {
            "overall_risk": "critical",
            "summary": "A syntax error was found.",
            "issues": [{
                "severity": "critical",
                "category": "syntax",
                "title": self.static_issue["title"],
                "explanation": self.static_issue["description"],
                "recommendation": self.static_issue["recommendation"],
                "line": self.static_issue["line"],
                "source": "ai",
                "confidence": "high",
            }],
        }
        client = Mock()
        client.chat.completions.create.return_value = _completion(json.dumps(matching_payload))
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            result = run_ai_review(
                code="bad(",
                language="python",
                project_name="test-project",
                static_issues=[self.static_issue],
            )

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["static_issues"], [self.static_issue])
        self.assertEqual(result["overall_risk"], "critical")

    def test_invalid_issue_schema_is_rejected_not_coerced(self):
        invalid_payload = {
            **self.valid_payload,
            "issues": [{
                **self.valid_payload["issues"][0],
                "line": "four",
            }],
        }
        result, client = self._run([
            json.dumps(invalid_payload),
            json.dumps(self.valid_payload),
        ])

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["issues"][0]["line"], 4)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_findings_with_nonexistent_symbols_are_rejected_after_retry(self):
        invalid_payload = {
            **self.valid_payload,
            "issues": [{
                **self.valid_payload["issues"][0],
                "line": 1,
                "explanation": "The missing_helper() call is undefined.",
            }],
        }
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(json.dumps(invalid_payload)),
            _completion(json.dumps(invalid_payload)),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            result = run_ai_review(
                code="def calculate(value):\n    return value + 1\n",
                language="python",
                project_name="test-project",
                static_issues=[],
            )

        self.assertFalse(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["failure_category"], "response_ungrounded")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_findings_with_out_of_range_lines_are_rejected(self):
        invalid_payload = {
            **self.valid_payload,
            "issues": [{
                **self.valid_payload["issues"][0],
                "line": 99,
            }],
        }
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(json.dumps(invalid_payload)),
            _completion(json.dumps(invalid_payload)),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            result = run_ai_review(
                code="def calculate(value):\n    return value + 1\n",
                language="python",
                project_name="test-project",
                static_issues=[],
            )

        self.assertFalse(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["failure_category"], "response_ungrounded")

    def test_truncated_first_response_retries_with_compact_output_guidance(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion('{"overall_risk":"medium"', finish_reason="length"),
            _completion(json.dumps(self.valid_payload)),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            with self.assertLogs("app.services.ai_reviewer", level="WARNING") as logs:
                result = run_ai_review(
                    code=(
                        "first = 1\nsecond = 2\nthird = 3\n"
                        "def add(left, right): return left + right\n"
                    ),
                    language="python",
                    project_name="test-project",
                    static_issues=[],
                )

        self.assertTrue(result["ai_available"])
        self.assertEqual(client.chat.completions.create.call_count, 2)
        second_prompt = client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("previous response was truncated", second_prompt)
        self.assertIn("omit optional beginner_explanation", second_prompt)
        self.assertIn("reason=truncated", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
