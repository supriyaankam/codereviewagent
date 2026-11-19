import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.ai_reviewer import (
    _ALLOWED_SOURCES,
    _SYSTEM_PROMPT,
    _InvalidAIResponse,
    _parse_response,
    run_ai_review,
)


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

    def _run(self, responses, static_issues=None, code=None):
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(response) for response in responses
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            result = run_ai_review(
                code=code or "value = 1\nsecond = 2\nthird = 3\ncall(value)\n",
                language="python",
                project_name="test-project",
                static_issues=static_issues or [],
            )
        return result, client

    def _parse(self, payload):
        return _parse_response(
            json.dumps(payload),
            [],
            "value = 1\nsecond = 2\nthird = 3\ncall(value)\n",
            "python",
        )

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
        response = f"Here is the review:\n```json\n{json.dumps(self.valid_payload)}\n```\nEnd."
        result, client = self._run([response])

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["issues"][0]["line"], 4)
        client.chat.completions.create.assert_called_once()

    def test_schema_invalid_responses_retry_then_fall_back(self):
        invalid_payload = {
            **self.valid_payload,
            "issues": [{
                **self.valid_payload["issues"][0],
                "line": "four",
            }],
        }
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(json.dumps(invalid_payload)),
            _completion(json.dumps(invalid_payload)),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            with self.assertLogs("app.services.ai_reviewer", level="WARNING") as logs:
                result = run_ai_review(
                    code="value = 1\nsecond = 2\nthird = 3\ncall(value)\n",
                    language="python",
                    project_name="test-project",
                    static_issues=[],
                )

        self.assertFalse(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["failure_category"], "response_schema")
        self.assertIn("invalid response after retry", result["fallback_reason"])
        self.assertEqual(client.chat.completions.create.call_count, 2)
        diagnostic_log = "\n".join(logs.output)
        self.assertIn("reason=schema", diagnostic_log)
        self.assertIn("field=issues[0].line", diagnostic_log)
        self.assertIn("detail=type_or_range", diagnostic_log)
        self.assertIn("finish_reason=stop", diagnostic_log)

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

    def test_explicit_enum_aliases_and_case_whitespace_are_normalized(self):
        payload = deepcopy(self.valid_payload)
        payload["overall_risk"] = " MEDIUM "
        issue = payload["issues"][0]
        issue["severity"] = " HIGH "
        issue["category"] = " ERROR-HANDLING "
        issue["source"] = " TEAM_STANDARD "
        issue["confidence"] = " HIGH "
        issue.pop("explanation")
        issue["description"] = "The same operation is repeated."
        issue.pop("recommendation")
        issue["suggested_fix"] = "Extract a helper."
        issue["beginner_explanation"] = None

        result = self._parse(payload)

        self.assertEqual(result["issues"][0]["severity"], "error")
        self.assertEqual(result["issues"][0]["category"], "quality")
        self.assertEqual(result["issues"][0]["source"], "ai")
        self.assertEqual(result["issues"][0]["confidence"], "high")
        self.assertEqual(
            result["issues"][0]["explanation"],
            "The same operation is repeated.",
        )
        self.assertEqual(result["issues"][0]["recommendation"], "Extract a helper.")
        self.assertEqual(result["issues"][0]["beginner_explanation"], {
            "problem": "",
            "cause": "",
            "impact": "",
            "fix": "",
            "example": "",
            "concept": "",
        })

    def test_severity_aliases_preserve_low_findings_as_actionable(self):
        cases = (
            (" HIGH ", "error", "high"),
            (" MEDIUM ", "warning", "medium"),
            (" LOW ", "warning", "medium"),
            (" INFO ", "info", "info"),
        )
        for value, expected_severity, expected_risk in cases:
            with self.subTest(value=value):
                payload = deepcopy(self.valid_payload)
                payload["issues"][0]["severity"] = value
                result = self._parse(payload)
                self.assertEqual(result["issues"][0]["severity"], expected_severity)
                self.assertEqual(result["overall_risk"], expected_risk)

    def test_team_standard_categories_map_to_quality(self):
        for category in ("general", "error-handling", "architecture", "performance"):
            with self.subTest(category=category):
                payload = deepcopy(self.valid_payload)
                payload["issues"][0]["category"] = category
                result = self._parse(payload)
                self.assertEqual(result["issues"][0]["category"], "quality")

    def test_prompt_and_validator_share_all_source_values(self):
        source_schema = _SYSTEM_PROMPT.split('"source":', 1)[1].splitlines()[0]
        for source in _ALLOWED_SOURCES:
            with self.subTest(source=source):
                self.assertIn(source, source_schema)
                payload = deepcopy(self.valid_payload)
                payload["issues"][0]["source"] = source
                self._parse(payload)

    def test_missing_and_null_beginner_explanation_are_empty(self):
        for beginner in ("missing", None):
            with self.subTest(beginner=beginner):
                payload = deepcopy(self.valid_payload)
                if beginner == "missing":
                    payload["issues"][0].pop("beginner_explanation")
                else:
                    payload["issues"][0]["beginner_explanation"] = beginner
                result = self._parse(payload)
                self.assertEqual(
                    result["issues"][0]["beginner_explanation"],
                    {
                        "problem": "",
                        "cause": "",
                        "impact": "",
                        "fix": "",
                        "example": "",
                        "concept": "",
                    },
                )

    def test_invalid_beginner_explanation_shapes_are_rejected(self):
        cases = (
            (["not", "an", "object"], "issues[0].beginner_explanation", "type"),
            (
                {"problem": None},
                "issues[0].beginner_explanation.problem",
                "type",
            ),
            (
                {"concept": 42},
                "issues[0].beginner_explanation.concept",
                "type",
            ),
        )
        for beginner, field, detail in cases:
            with self.subTest(beginner=beginner):
                payload = deepcopy(self.valid_payload)
                payload["issues"][0]["beginner_explanation"] = beginner
                with self.assertRaises(_InvalidAIResponse) as raised:
                    self._parse(payload)
                self.assertEqual(raised.exception.reason, "schema")
                self.assertEqual(raised.exception.field, field)
                self.assertEqual(raised.exception.detail, detail)

    def test_schema_errors_identify_field_and_reason_without_coercion(self):
        cases = (
            ("overall_risk", lambda p: p.update(overall_risk=1), "overall_risk", "type"),
            ("summary type", lambda p: p.update(summary=1), "summary", "type"),
            ("summary empty", lambda p: p.update(summary="  "), "summary", "empty"),
            ("issues type", lambda p: p.update(issues={}), "issues", "type"),
            (
                "issue type",
                lambda p: p.update(issues=["bad issue"]),
                "issues[0]",
                "type",
            ),
            (
                "severity",
                lambda p: p["issues"][0].update(severity="urgent"),
                "issues[0].severity",
                "unsupported_value",
            ),
            (
                "category",
                lambda p: p["issues"][0].update(category="unapproved"),
                "issues[0].category",
                "unsupported_value",
            ),
            (
                "line",
                lambda p: p["issues"][0].update(line="4"),
                "issues[0].line",
                "type_or_range",
            ),
            (
                "source",
                lambda p: p["issues"][0].update(source="unapproved"),
                "issues[0].source",
                "unsupported_value",
            ),
            (
                "confidence",
                lambda p: p["issues"][0].update(confidence="certain"),
                "issues[0].confidence",
                "unsupported_value",
            ),
            (
                "title missing",
                lambda p: p["issues"][0].pop("title"),
                "issues[0].title",
                "missing",
            ),
            (
                "title type",
                lambda p: p["issues"][0].update(title=3),
                "issues[0].title",
                "type",
            ),
            (
                "explanation missing",
                lambda p: p["issues"][0].pop("explanation"),
                "issues[0].explanation",
                "missing",
            ),
            (
                "explanation empty",
                lambda p: p["issues"][0].update(explanation=" "),
                "issues[0].explanation",
                "empty",
            ),
            (
                "recommendation missing",
                lambda p: p["issues"][0].pop("recommendation"),
                "issues[0].recommendation",
                "missing",
            ),
        )
        for name, mutate, field, detail in cases:
            with self.subTest(name=name):
                payload = deepcopy(self.valid_payload)
                mutate(payload)
                with self.assertRaises(_InvalidAIResponse) as raised:
                    self._parse(payload)
                self.assertEqual(raised.exception.reason, "schema")
                self.assertEqual(raised.exception.field, field)
                self.assertEqual(raised.exception.detail, detail)

    def test_aliases_do_not_make_explanation_or_recommendation_optional(self):
        for field, alias, value in (
            ("explanation", "description", "An explanation."),
            ("recommendation", "fix", "A fix."),
        ):
            with self.subTest(field=field):
                payload = deepcopy(self.valid_payload)
                payload["issues"][0].pop(field)
                payload["issues"][0][alias] = value
                parsed = self._parse(payload)
                self.assertEqual(parsed["issues"][0][field], value)

                payload["issues"][0].pop(alias)
                with self.assertRaises(_InvalidAIResponse) as raised:
                    self._parse(payload)
                self.assertEqual(raised.exception.field, f"issues[0].{field}")
                self.assertEqual(raised.exception.detail, "missing")

    def test_invalid_canonical_text_field_does_not_fall_back_to_alias(self):
        payload = deepcopy(self.valid_payload)
        payload["issues"][0]["explanation"] = None
        payload["issues"][0]["description"] = "A valid alias."
        with self.assertRaises(_InvalidAIResponse) as raised:
            self._parse(payload)
        self.assertEqual(raised.exception.field, "issues[0].explanation")
        self.assertEqual(raised.exception.detail, "type")

    def test_invalid_explanation_and_recommendation_alias_types_are_rejected(self):
        cases = (
            ("explanation", "description", None),
            ("explanation", "description", []),
            ("recommendation", "suggested_fix", None),
            ("recommendation", "fix", {}),
        )
        for field, alias, value in cases:
            with self.subTest(field=field, alias=alias, value=value):
                payload = deepcopy(self.valid_payload)
                payload["issues"][0].pop(field)
                payload["issues"][0][alias] = value
                with self.assertRaises(_InvalidAIResponse) as raised:
                    self._parse(payload)
                self.assertEqual(raised.exception.reason, "schema")
                self.assertEqual(raised.exception.field, f"issues[0].{field}")
                self.assertEqual(raised.exception.detail, "type")

    def test_schema_diagnostics_do_not_log_response_source_or_drop_static_findings(self):
        response_sentinel = "response_value_must_not_be_logged"
        source_sentinel = "source_code_must_not_be_logged"
        invalid_payload = deepcopy(self.valid_payload)
        invalid_payload["issues"][0]["line"] = response_sentinel
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(json.dumps(invalid_payload)),
            _completion(json.dumps(invalid_payload)),
        ]
        with patch("app.services.ai_reviewer._get_client", return_value=client):
            with self.assertLogs("app.services.ai_reviewer", level="WARNING") as logs:
                result = run_ai_review(
                    code=f"{source_sentinel} = 1\nsecond = 2\nthird = 3\ncall(value)\n",
                    language="python",
                    project_name="test-project",
                    static_issues=[self.static_issue],
                )

        diagnostic_log = "\n".join(logs.output)
        self.assertIn("field=issues[0].line", diagnostic_log)
        self.assertIn("detail=type_or_range", diagnostic_log)
        self.assertNotIn(response_sentinel, diagnostic_log)
        self.assertNotIn(source_sentinel, diagnostic_log)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["static_issues"], [self.static_issue])
        self.assertEqual(result["failure_category"], "response_schema")
        retry_prompt = client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("issues[0].line", retry_prompt)
        self.assertNotIn(response_sentinel, retry_prompt)

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
