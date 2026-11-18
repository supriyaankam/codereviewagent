import unittest

from app.services.quality_score import calculate_quality_score


class QualityScoreTests(unittest.TestCase):
    def test_clean_python_starts_at_one_hundred(self):
        result = calculate_quality_score("def add(a, b):\n    return a + b\n", "python", [], [])

        self.assertEqual(result["score"], 100)
        self.assertTrue(result["metrics"]["complexity_measurable"])
        self.assertEqual(result["metrics"]["max_cyclomatic_complexity"], 1)

    def test_duplicate_static_and_ai_finding_is_only_counted_once(self):
        finding = {
            "title": "Mutable default argument",
            "line": 2,
            "severity": "warning",
            "category": "quality",
            "source": "ast",
        }
        ai_finding = {**finding, "source": "ai", "confidence": "high"}
        result = calculate_quality_score("def f(x=[]):\n    return x\n", "python", [ai_finding], [finding])

        self.assertEqual(result["score"], 95)

    def test_ai_output_variations_do_not_change_static_score_or_factors(self):
        code = "def f(value=[]):\n    return value\n"
        static_finding = {
            "title": "Mutable default argument",
            "line": 1,
            "severity": "warning",
            "category": "quality",
            "source": "ast",
        }
        ai_variants = [
            [{
                "title": "Possible shared mutable state",
                "line": 1,
                "severity": "critical",
                "category": "security",
                "source": "ai",
                "confidence": "high",
            }],
            [{
                "title": "Potentially unsafe default",
                "line": 1,
                "severity": "info",
                "category": "style",
                "source": "ai",
                "confidence": "high",
            }],
            [],
        ]

        results = [
            calculate_quality_score(code, "python", ai_findings, [static_finding])
            for ai_findings in ai_variants
        ]
        self.assertEqual({result["score"] for result in results}, {95})
        self.assertEqual(
            [result["factors"] for result in results],
            [results[0]["factors"]] * 3,
        )
        self.assertIn("AI findings are excluded", results[0]["method"]["notes"])

    def test_duplicate_static_findings_are_deduplicated_in_stable_order(self):
        first = {
            "title": "Mutable default argument",
            "line": 1,
            "severity": "warning",
            "category": "quality",
            "source": "ast",
        }
        duplicate = {
            **first,
            "title": "[Pylint] Mutable default argument",
            "severity": "error",
            "source": "pylint",
        }

        result = calculate_quality_score(
            "def f(value=[]):\n    return value\n",
            "python",
            [],
            [first, duplicate],
        )

        self.assertEqual(result["score"], 88)
        self.assertEqual(len(result["factors"]), 1)

    def test_uncertain_ai_only_findings_do_not_reduce_score(self):
        finding = {
            "title": "Possible issue",
            "line": 1,
            "severity": "critical",
            "category": "security",
            "source": "ai",
            "confidence": "medium",
        }
        result = calculate_quality_score("x = 1\n", "javascript", [finding], [])

        self.assertEqual(result["score"], 100)
        self.assertFalse(result["metrics"]["complexity_measurable"])

    def test_ai_cannot_claim_static_source_to_bypass_confidence_filter(self):
        finding = {
            "title": "Unverified concern",
            "line": 1,
            "severity": "critical",
            "category": "security",
            "source": "unrecognized-ai-source",
            "confidence": "medium",
        }
        result = calculate_quality_score("x = 1\n", "javascript", [finding], [])

        self.assertEqual(result["score"], 100)

    def test_python_complexity_metric_reduces_maintainability(self):
        branches = "\n".join(f"    if value == {n}:\n        return {n}" for n in range(11))
        code = f"def choose(value):\n{branches}\n    return None\n"
        result = calculate_quality_score(code, "python", [], [])

        self.assertEqual(result["metrics"]["max_cyclomatic_complexity"], 12)
        self.assertEqual(result["categories"]["maintainability"], 96)
        self.assertEqual(result["score"], 96)

    def test_complexity_finding_is_not_penalized_twice(self):
        branches = "\n".join(f"    if value == {n}:\n        return {n}" for n in range(11))
        code = f"def choose(value):\n{branches}\n    return None\n"
        issue = {
            "title": "Too many branches",
            "line": 1,
            "severity": "warning",
            "category": "complexity",
            "source": "pylint",
        }
        result = calculate_quality_score(code, "python", [], [issue])

        self.assertEqual(result["score"], 96)


if __name__ == "__main__":
    unittest.main()
