import unittest
from unittest.mock import patch

from app.services import auto_fix_service


class AutoFixTargetingTests(unittest.TestCase):
    def test_small_division_by_zero_finding_targets_its_complete_function(self):
        source = "def divide(value, divisor):\n    return value / divisor\n"
        issue = {
            "title": "Possible division by zero",
            "description": "The divisor can be zero.",
            "severity": "warning",
            "line": 2,
            "source": "pylint",
        }
        replacement = (
            "def divide(value, divisor):\n"
            "    if divisor == 0:\n"
            "        raise ValueError('divisor must not be zero')\n"
            "    return value / divisor\n"
        )

        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            return_value={
                "fixed_code": replacement,
                "changes": [{"issue": issue["title"], "change": "Check divisor."}],
                "summary": "Guarded the divisor.",
                "ai_available": True,
            },
        ) as fix:
            result = auto_fix_service.auto_fix_finding(
                source, "python", issue, "src/math_ops.py", "finding-1"
            )

        self.assertEqual(fix.call_args.kwargs["code"], source)
        self.assertTrue(fix.call_args.kwargs["targeted_region"])
        self.assertEqual(fix.call_args.kwargs["issues"][0]["finding_id"], "finding-1")
        self.assertEqual(result["fixed_code"], replacement)
        self.assertEqual(result["original_region"], source)

    def test_large_source_extracts_only_the_function_near_beginning_or_end(self):
        beginning = "def beginning(value):\n    return value / 0\n"
        ending = "def ending(value):\n    return value / 0\n"
        source = beginning + ("# unrelated filler\n" * 1_000) + ending
        lines = source.splitlines()

        for title, target_line, expected_region in (
            ("beginning bug", 2, beginning),
            ("ending bug", len(lines), ending),
        ):
            issue = {
                "title": title,
                "severity": "error",
                "line": target_line,
                "source": "pylint",
            }
            with self.subTest(finding=title):
                with patch(
                    "app.services.auto_fix_service.auto_fix_code",
                    side_effect=lambda **kwargs: {
                        "fixed_code": kwargs["code"],
                        "changes": [],
                        "summary": "No generated changes.",
                        "ai_available": True,
                    },
                ) as fix:
                    result = auto_fix_service.auto_fix_finding(
                        source, "python", issue, "src/large.py", title
                    )

                self.assertGreater(len(source), auto_fix_service.MAX_CODE_CHARS)
                self.assertEqual(fix.call_args.kwargs["code"], expected_region)
                self.assertNotIn("# unrelated filler", fix.call_args.kwargs["code"])
                self.assertEqual(result["fixed_code"], source)
                self.assertEqual(result["patch_start_line"], target_line - 1)

    def test_oversized_function_reduces_to_complete_containing_statement(self):
        source = (
            "def large_function(value):\n"
            "    target = value / 0\n"
            + "".join(f"    value += {index}\n" for index in range(2_000))
            + "    return target\n"
        )
        issue_line = 2
        issue = {
            "title": "Possible division by zero",
            "severity": "error",
            "line": issue_line,
        }

        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            side_effect=lambda **kwargs: {
                "fixed_code": kwargs["code"],
                "changes": [],
                "summary": "",
                "ai_available": True,
            },
        ) as fix:
            result = auto_fix_service.auto_fix_finding(
                source, "python", issue, "large.py", "large-finding"
            )

        submitted_region = fix.call_args.kwargs["code"]
        self.assertEqual(submitted_region, "    target = value / 0\n")
        self.assertLessEqual(len(submitted_region) + len(fix.call_args.kwargs["context"]), 14_000)
        self.assertEqual(result["fixed_code"], source)

    def test_multiple_independent_findings_send_only_the_selected_function(self):
        source = (
            "def first(value):\n    return value / 0\n\n"
            "def second(value):\n    return value / 0\n"
        )
        selected_issue = {
            "title": "Second division-by-zero finding",
            "severity": "error",
            "line": 5,
            "source": "pylint",
        }
        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            side_effect=lambda **kwargs: {
                "fixed_code": kwargs["code"],
                "changes": [],
                "summary": "",
                "ai_available": True,
            },
        ) as fix:
            auto_fix_service.auto_fix_finding(
                source, "python", selected_issue, "two_findings.py", "second-finding"
            )

        self.assertEqual(
            fix.call_args.kwargs["code"],
            "def second(value):\n    return value / 0\n",
        )
        self.assertEqual(
            [issue["title"] for issue in fix.call_args.kwargs["issues"]],
            ["Second division-by-zero finding"],
        )

    def test_large_finding_without_location_returns_actionable_error(self):
        source = "value = 1\n" * 2_000
        with patch("app.services.auto_fix_service.auto_fix_code") as fix:
            result = auto_fix_service.auto_fix_finding(
                source,
                "python",
                {"title": "File-level concern", "severity": "warning", "line": 0},
                "large.py",
                "file-level",
            )

        self.assertFalse(result["ai_available"])
        self.assertIn("no usable source line", result["error"])
        fix.assert_not_called()


if __name__ == "__main__":
    unittest.main()
