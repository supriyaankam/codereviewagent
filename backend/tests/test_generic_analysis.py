import unittest

from app.analyzers.language_analyzer import check_language_syntax
from app.analyzers.python_analyzer import run_ast_analysis, run_pylint_analysis
from app.analyzers.security_analyzer import run_secret_detection
from app.services.team_standard_analyzer import analyze_team_standards
from app.services.auto_fix_service import _extract_fix_region
from app.services.code_analyzer import analyze_code
from app.routers.reviews import _fix_validation


class GenericAnalysisTests(unittest.TestCase):
    def test_parser_backed_syntax_support_covers_uploaded_languages(self):
        examples = {
            "javascript": "function add(a, b) { return a + b; }",
            "typescript": "function add(a: number, b: number): number { return a + b; }",
            "java": "class Main { int add(int a, int b) { return a + b; } }",
            "csharp": "class Main { int Add(int a, int b) { return a + b; } }",
            "go": "package main\nfunc add(a int, b int) int { return a + b }",
            "ruby": "def add(a, b)\n  a + b\nend",
            "php": "<?php function add($a, $b) { return $a + $b; }",
            "cpp": "int add(int a, int b) { return a + b; }",
            "c": "int add(int a, int b) { return a + b; }",
            "rust": "fn add(a: i32, b: i32) -> i32 { a + b }",
            "kotlin": "fun add(a: Int, b: Int): Int = a + b",
            "swift": "func add(_ a: Int, _ b: Int) -> Int { a + b }",
        }

        for language, code in examples.items():
            with self.subTest(language=language):
                result = analyze_code(language, code)
                self.assertTrue(result["supported"])
                self.assertFalse(result["has_syntax_error"])
                self.assertEqual(result["issues"], [])
                self.assertEqual(
                    result["capabilities"],
                    ["syntax", "secrets", "team_standards"],
                )

    def test_supported_non_python_languages_report_syntax_errors(self):
        for language, code in (
            ("javascript", "function broken( {"),
            ("typescript", "const total: number = ;"),
            ("java", "class Main {"),
            ("cpp", "int main( {"),
            ("go", "func main( {"),
        ):
            with self.subTest(language=language):
                result = analyze_code(language, code)
                self.assertTrue(result["supported"])
                self.assertTrue(result["has_syntax_error"])
                self.assertEqual(result["issues"][0]["source"], "tree_sitter")
                self.assertGreaterEqual(result["issues"][0]["line"], 1)

    def test_typescript_parser_accepts_tsx_uploaded_as_typescript(self):
        result = analyze_code("typescript", "const App = () => <div />;")

        self.assertTrue(result["supported"])
        self.assertFalse(result["has_syntax_error"])

    def test_python_builtins_and_environment_variables_are_not_false_positives(self):
        source = (
            "import os\n"
            "def convert(values):\n"
            "    return [int(value) for value in values if value]\n"
            "token = os.getenv('API_TOKEN')\n"
            "print(len(convert(['1'])))\n"
        )

        result = analyze_code("python", source)
        titles = [issue["title"] for issue in result["issues"]]

        self.assertFalse(any("print() call" in title for title in titles))
        self.assertFalse(any("secret" in title.lower() for title in titles))
        self.assertFalse(any("undefined-variable" in title for title in titles))

    def test_custom_eval_method_is_not_mistaken_for_python_builtin(self):
        source = (
            "class Calculator:\n"
            "    def eval(self, value):\n"
            "        return value\n"
            "    def run(self, value):\n"
            "        return self.eval(value)\n"
        )

        issues = run_ast_analysis(source)

        self.assertNotIn("Use of eval()", [issue.title for issue in issues])

    def test_user_defined_eval_function_is_not_mistaken_for_python_builtin(self):
        source = (
            "def eval(value):\n"
            "    return value\n"
            "def convert(value):\n"
            "    return eval(value)\n"
        )
        issues = run_ast_analysis(source)
        analysis = analyze_code("python", source)

        self.assertNotIn("Use of eval()", [issue.title for issue in issues])
        self.assertFalse(any(
            issue["category"] == "security"
            and ("eval" in issue["title"].lower() or "blacklist" in issue["title"].lower())
            for issue in analysis["issues"]
        ))

    def test_builtin_eval_call_is_still_reported(self):
        issues = run_ast_analysis("def convert(value):\n    return eval(value)\n")

        self.assertIn("Use of eval()", [issue.title for issue in issues])

    def test_python_undefined_names_are_reported_by_pylint(self):
        issues = run_pylint_analysis(
            "def calculate(value):\n"
            "    return missing_helper(value)\n"
        )

        self.assertTrue(any("undefined-variable" in issue.title for issue in issues))

    def test_comments_do_not_create_python_secret_findings(self):
        code = '# password = "looksLikeASecret123"\nvalue = 1\n'

        self.assertEqual(run_secret_detection(code), [])

    def test_python_assignment_to_secret_name_is_detected(self):
        code = 'api_key = "sk_live_FAKE_KEY_1234567890"\n'
        issues = run_secret_detection(code)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].title, "Hardcoded API key")
        self.assertNotIn("FAKE_KEY", issues[0].description)

    def test_environment_value_read_is_not_reported_as_literal_secret(self):
        code = 'api_key = os.getenv("API_KEY")\n'

        self.assertEqual(run_secret_detection(code), [])

    def test_secret_literals_are_detected_from_syntax_trees_across_languages(self):
        examples = {
            "javascript": 'const apiKey = "sk_live_FAKE_KEY_1234567890";',
            "java": 'class Main { String apiKey = "sk_live_FAKE_KEY_1234567890"; }',
            "csharp": 'class Main { string apiKey = "sk_live_FAKE_KEY_1234567890"; }',
            "cpp": 'const char* apiKey = "sk_live_FAKE_KEY_1234567890";',
            "go": 'package main\nvar apiKey = "sk_live_FAKE_KEY_1234567890"',
            "ruby": 'api_key = "sk_live_FAKE_KEY_1234567890"',
            "php": '<?php $apiKey = "sk_live_FAKE_KEY_1234567890";',
            "rust": 'let api_key = "sk_live_FAKE_KEY_1234567890";',
            "kotlin": 'val apiKey = "sk_live_FAKE_KEY_1234567890"',
            "swift": 'let apiKey = "sk_live_FAKE_KEY_1234567890"',
        }

        for language, code in examples.items():
            with self.subTest(language=language):
                findings = run_secret_detection(code, language)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].title, "Hardcoded API key")
                self.assertNotIn("FAKE_KEY", findings[0].description)

    def test_comments_are_not_treated_as_hardcoded_credentials(self):
        standard = [{
            "title": "Never hardcode credentials",
            "description": "Never hardcode API keys or passwords.",
            "severity": "critical",
            "enabled": True,
        }]
        examples = {
            "python": '# password = "looksLikeASecret123"\nvalue = 1',
            "javascript": '// password = "looksLikeASecret123"\nconst value = 1;',
            "java": '// password = "looksLikeASecret123"\nclass Main {}',
        }

        for language, code in examples.items():
            with self.subTest(language=language):
                self.assertEqual(analyze_team_standards(language, code, standard), [])

    def test_environment_retrieval_is_not_reported_across_languages(self):
        examples = {
            "javascript": 'const apiKey = process.env.API_KEY;',
            "java": 'String apiKey = System.getenv("API_KEY");',
            "csharp": 'var apiKey = Environment.GetEnvironmentVariable("API_KEY");',
            "go": 'package main\nvar apiKey = os.Getenv("API_KEY")',
            "ruby": 'api_key = ENV.fetch("API_KEY")',
            "php": '$apiKey = getenv("API_KEY");',
            "rust": 'let api_key = std::env::var("API_KEY");',
            "kotlin": 'val apiKey = System.getenv("API_KEY")',
            "swift": 'let apiKey = ProcessInfo.processInfo.environment["API_KEY"]',
        }

        for language, code in examples.items():
            with self.subTest(language=language):
                self.assertEqual(run_secret_detection(code, language), [])

    def test_auto_fix_validation_never_claims_an_unresolved_finding_was_fixed(self):
        selected = {
            "title": "[E0602] undefined-variable",
            "category": "quality",
            "source": "pylint",
        }

        validation = _fix_validation(
            [selected],
            {
                "supported": True,
                "has_syntax_error": False,
                "issues": [selected],
            },
            "python",
        )

        self.assertEqual(validation["status"], "unresolved")
        self.assertIn("still has selected", validation["message"])

    def test_ai_finding_is_only_partially_verified_by_static_reanalysis(self):
        validation = _fix_validation(
            [{"title": "Possible logic issue", "source": "ai"}],
            {"supported": True, "has_syntax_error": False, "issues": []},
            "python",
        )

        self.assertEqual(validation["status"], "partial")
        self.assertIn("could not be independently checked", validation["message"])

    def test_non_python_auto_fix_targets_a_complete_statement(self):
        code = (
            "function calculate(value) {\n"
            "  const result = value / 0;\n"
            "  return result;\n"
            "}\n"
        )
        issue = {"line": 2}

        region = _extract_fix_region(code, "javascript", issue, 14_000)

        self.assertEqual(region["original_region"], "const result = value / 0;")
        self.assertEqual(region["start_line"], 2)
        self.assertEqual(region["end_line"], 2)


if __name__ == "__main__":
    unittest.main()
