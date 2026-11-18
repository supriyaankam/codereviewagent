import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from pydantic import ValidationError
from groq import APITimeoutError
import httpx

from app.schemas import AnalyzeRequest, ReviewRequest
from app.services.auto_fix_service import _parse
from app.services.ai_reviewer import run_ai_review
from app.services.code_analyzer import analyze_code
from app.services.quality_score import calculate_quality_score
from app.analyzers.python_analyzer import check_syntax, run_ast_analysis


class ReviewSafetyTests(unittest.TestCase):
    def test_identical_source_has_identical_static_findings_and_score_three_times(self):
        source = "def greet(name=[]):\n    return name\n"
        runs = []
        for _ in range(3):
            with redirect_stdout(StringIO()):
                analysis = analyze_code("python", source)
            runs.append((
                analysis["issues"],
                calculate_quality_score(source, "python", [], analysis["issues"]),
            ))

        self.assertEqual(runs[0][0], runs[1][0])
        self.assertEqual(runs[1][0], runs[2][0])
        self.assertEqual(
            [run[1]["score"] for run in runs],
            [runs[0][1]["score"]] * 3,
        )
        self.assertEqual(
            [run[1]["factors"] for run in runs],
            [runs[0][1]["factors"]] * 3,
        )

    def test_valid_and_multiple_fixes_remove_resolved_static_findings(self):
        source = "def parse(value=[]):\n    return eval(value)\n"
        fixed_source = (
            "import ast\n\n"
            "def parse(value=None):\n"
            "    if value is None:\n"
            "        value = []\n"
            "    return ast.literal_eval(value)\n"
        )
        with redirect_stdout(StringIO()):
            before = analyze_code("python", source)
            after = analyze_code("python", fixed_source)

        before_titles = [issue["title"] for issue in before["issues"]]
        after_titles = [issue["title"] for issue in after["issues"]]
        self.assertIn("Mutable default argument", before_titles)
        self.assertIn("Use of eval()", before_titles)
        self.assertNotIn("Mutable default argument", after_titles)
        self.assertNotIn("Use of eval()", after_titles)
        identities = [
            (issue["title"], issue["line"], issue["source"])
            for issue in after["issues"]
        ]
        self.assertEqual(len(identities), len(set(identities)))

    def test_ineffective_fix_keeps_the_unresolved_static_finding(self):
        source = "def parse(value=[]):\n    return value\n"
        ineffective_fixed_source = source
        with redirect_stdout(StringIO()):
            before = analyze_code("python", source)
            after = analyze_code("python", ineffective_fixed_source)

        finding = next(
            issue for issue in before["issues"]
            if issue["title"] == "Mutable default argument"
        )
        remaining = [
            issue for issue in after["issues"]
            if issue["title"] == finding["title"]
        ]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["line"], finding["line"])

    def test_empty_and_oversized_submissions_are_rejected(self):
        with self.assertRaises(ValidationError):
            AnalyzeRequest(language="python", code="")
        with self.assertRaises(ValidationError):
            ReviewRequest(
                user_id="test",
                project_name="demo",
                language="python",
                code="x" * 100_001,
            )

    def test_python_syntax_error_reports_its_line(self):
        issues = check_syntax("def broken(:\n    pass\n")

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 1)
        self.assertEqual(issues[0].severity.value, "critical")

    def test_common_mutable_default_is_detected_without_execution(self):
        issues = run_ast_analysis("def append(value, items=[]):\n    items.append(value)\n    return items\n")

        self.assertIn("Mutable default argument", [issue.title for issue in issues])

    def test_clean_code_has_no_ast_findings(self):
        issues = run_ast_analysis('def add(left, right):\n    """Add two values."""\n    return left + right\n')

        self.assertEqual(issues, [])

    def test_malformed_fix_response_preserves_original_source(self):
        original = "def add(a, b):\n    return a + b\n"

        result = _parse("not valid json", original)

        self.assertEqual(result["fixed_code"], original)
        self.assertFalse(result["ai_available"])
        self.assertTrue(result["error"])

    def test_valid_fix_response_returns_proposed_code(self):
        original = "def add(a, b):\n    return a + b\n"
        proposed = "def add(a, b):\n    return a + b\n"
        raw = json.dumps({
            "corrected_code": proposed,
            "changes": [],
            "summary": "No changes needed.",
        })
        result = _parse(raw, original)

        self.assertEqual(result["fixed_code"], proposed)
        self.assertTrue(result["ai_available"])

    def test_ai_timeout_falls_back_to_static_findings(self):
        request = httpx.Request("POST", "https://api.groq.com")
        fake_client = Mock()
        fake_client.chat.completions.create.side_effect = APITimeoutError(request=request)
        static_issue = {
            "title": "Syntax error",
            "severity": "critical",
            "category": "syntax",
            "description": "Unexpected token",
            "recommendation": "Fix the token",
            "line": 1,
            "source": "syntax",
        }

        with patch("app.services.ai_reviewer._get_client", return_value=fake_client):
            result = run_ai_review("bad(", "python", "demo", [static_issue])

        self.assertFalse(result["ai_available"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["static_issues"], [static_issue])
        self.assertIn("timed out", result["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
