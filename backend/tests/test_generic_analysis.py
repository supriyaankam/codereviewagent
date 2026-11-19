import unittest

from app.analyzers.language_analyzer import (
    canonical_language,
    check_language_syntax,
    detect_source_language,
)
from app.analyzers.python_analyzer import run_ast_analysis, run_pylint_analysis
from app.analyzers.security_analyzer import run_secret_detection
from app.services.team_standard_analyzer import analyze_team_standards
from app.services.auto_fix_service import _extract_fix_region
from app.services.code_analyzer import analyze_code
from app.services.quality_score import calculate_quality_score
from app.routers.reviews import _fix_validation


class GenericAnalysisTests(unittest.TestCase):
    def test_c_cpp_and_csharp_have_distinct_canonical_names(self):
        self.assertEqual(canonical_language("C"), "c")
        self.assertEqual(canonical_language("C++"), "cpp")
        self.assertEqual(canonical_language("C#"), "csharp")

    def test_language_detection_uses_positive_language_signals(self):
        examples = (
            ("console.log(\"hello\");", "javascript"),
            ("int main(void) { return _Generic(0, int: 1, default: 0); }", "c"),
            (
                'public class Main { public static void main(String[] args) { '
                'System.out.println("hello"); } }',
                "java",
            ),
            ("def add(left, right):\n    return left + right", "python"),
        )

        for code, language in examples:
            detected = detect_source_language(code)
            self.assertEqual(detected["status"], "detected")
            self.assertEqual(detected["detected_language"], language)

    def test_shared_typescript_and_cpp_syntax_is_reported_as_ambiguous(self):
        typescript = detect_source_language("const count: number = 1;")
        cpp = detect_source_language("int main() { return 0; }")

        self.assertEqual(typescript["status"], "ambiguous")
        self.assertEqual(typescript["candidates"], ["javascript", "typescript"])
        self.assertEqual(cpp["status"], "ambiguous")
        self.assertEqual(cpp["candidates"], ["c", "cpp"])

    def test_empty_code_has_no_detected_language(self):
        result = detect_source_language(" \n\t ")

        self.assertEqual(result["status"], "empty")
        self.assertIsNone(result["detected_language"])

    def test_syntax_error_without_language_signals_remains_undetermined(self):
        result = detect_source_language("function broken( {")

        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["detected_language"])

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
                    ["syntax", "semantic", "secrets", "team_standards"],
                )

    def test_shared_tree_sitter_zero_divisor_rule_covers_every_supported_parser(self):
        examples = {
            "python": ("def calculate(value):\n    return value / 0\n", 2),
            "javascript": ("function calculate(value) {\n  return value / 0;\n}\n", 2),
            "typescript": (
                "function calculate(value: number): number {\n  return value / 0;\n}\n",
                2,
            ),
            "java": (
                "class Demo {\n  double calculate(double value) {\n    return value / 0;\n  }\n}\n",
                3,
            ),
            "csharp": (
                "class Demo {\n  double Calculate(double value) {\n    return value / 0;\n  }\n}\n",
                3,
            ),
            "c": ("double calculate(double value) {\n  return value / 0;\n}\n", 2),
            "cpp": ("double calculate(double value) {\n  return value / 0;\n}\n", 2),
            "go": ("package main\nfunc calculate(value int) int {\n  return value / 0\n}\n", 3),
            "rust": ("fn calculate(value: i32) -> i32 {\n  value / 0\n}\n", 2),
            "ruby": ("def calculate(value)\n  value / 0\nend\n", 2),
            "php": ("<?php\n$result = $value / 0;\n", 2),
            "swift": ("func calculate(_ value: Int) -> Int {\n  value / 0\n}\n", 2),
            "kotlin": ("fun calculate(value: Int): Int {\n  value / 0\n}\n", 2),
        }

        for language, (source, expected_line) in examples.items():
            with self.subTest(language=language):
                result = analyze_code(language, source)
                findings = [
                    issue for issue in result["issues"]
                    if issue["title"] == "Division or remainder by zero literal"
                ]

                self.assertTrue(result["supported"])
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["line"], expected_line)
                self.assertEqual(findings[0]["severity"], "warning")
                self.assertEqual(findings[0]["source"], "tree_sitter_semantic")
                self.assertTrue(findings[0]["recommendation"])

    def test_constant_zero_expressions_are_detected_for_every_supported_parser(self):
        examples = {
            "python": "def f(value):\n    return value / (2 - 2)\n",
            "javascript": "function f(value) { return value / (2 - 2); }",
            "typescript": "function f(value: number) { return value / (2 - 2); }",
            "java": "class A { int f(int value) { return value / (2 - 2); } }",
            "csharp": "class A { int F(int value) { return value / (2 - 2); } }",
            "c": "int f(int value) { return value / (2 - 2); }",
            "cpp": "int f(int value) { return value / (2 - 2); }",
            "go": "package main\nfunc f(value int) int { return value / (2 - 2) }",
            "rust": "fn f(value: i32) -> i32 { value / (2 - 2) }",
            "ruby": "def f(value)\n  value / (2 - 2)\nend",
            "php": "<?php function f($value) { return $value / (2 - 2); }",
            "swift": "func f(_ value: Int) -> Int { return value / (2 - 2) }",
            "kotlin": "fun f(value: Int): Int { return value / (2 - 2) }",
        }

        for language, source in examples.items():
            with self.subTest(language=language):
                result = analyze_code(language, source)
                findings = [
                    issue for issue in result["issues"]
                    if issue["title"] == "Division or remainder by constant zero expression"
                ]

                self.assertTrue(result["supported"])
                self.assertFalse(result["has_syntax_error"])
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["source"], "tree_sitter_semantic")
                self.assertGreaterEqual(findings[0]["line"], 1)

    def test_nonzero_constant_divisor_expressions_are_not_reported(self):
        examples = {
            "python": "def f(value):\n    return value / (10 - 2) + value / (2 + 3)\n",
            "javascript": "function f(value) { return value / (10 - 2) + value / (2 + 3); }",
            "typescript": "function f(value: number) { return value / (10 - 2) + value / (2 + 3); }",
            "java": "class A { int f(int value) { return value / (10 - 2) + value / (2 + 3); } }",
            "csharp": "class A { int F(int value) { return value / (10 - 2) + value / (2 + 3); } }",
            "c": "int f(int value) { return value / (10 - 2) + value / (2 + 3); }",
            "cpp": "int f(int value) { return value / (10 - 2) + value / (2 + 3); }",
            "go": "package main\nfunc f(value int) int { return value / (10 - 2) + value / (2 + 3) }",
            "rust": "fn f(value: i32) -> i32 { value / (10 - 2) + value / (2 + 3) }",
            "ruby": "def f(value)\n  value / (10 - 2) + value / (2 + 3)\nend",
            "php": "<?php function f($value) { return $value / (10 - 2) + $value / (2 + 3); }",
            "swift": "func f(_ value: Int) -> Int { return value / (10 - 2) + value / (2 + 3) }",
            "kotlin": "fun f(value: Int): Int { return value / (10 - 2) + value / (2 + 3) }",
        }

        for language, source in examples.items():
            with self.subTest(language=language):
                result = analyze_code(language, source)
                self.assertFalse(any(
                    issue["title"] == "Division or remainder by constant zero expression"
                    for issue in result["issues"]
                ))

    def test_python_floor_division_by_zero_remains_detected(self):
        result = analyze_code(
            "python",
            "def divide(value):\n    return value // 0\n",
        )
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Division or remainder by zero literal"
        ]

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 2)

    def test_shared_tree_sitter_zero_divisor_rule_avoids_nonzero_and_nonliteral_cases(self):
        examples = {
            "python": (
                "def calculate(value, divisor):\n"
                "    message = 'value / 0'\n"
                "    # value % 0\n"
                "    return value / divisor + value % 2\n"
            ),
            "javascript": (
                "function calculate(value, divisor) {\n"
                "  const message = 'value / 0';\n"
                "  // value % 0\n"
                "  return value / divisor + value % 2;\n"
                "}\n"
            ),
            "java": (
                "class Demo {\n"
                "  double calculate(double value, double divisor) {\n"
                "    return value / divisor + value % 2;\n"
                "  }\n"
                "}\n"
            ),
        }

        for language, source in examples.items():
            with self.subTest(language=language):
                result = analyze_code(language, source)
                self.assertFalse(any(
                    issue["title"] == "Division or remainder by zero literal"
                    for issue in result["issues"]
                ))

    def test_shared_tree_sitter_zero_divisor_rule_recognizes_floating_literals(self):
        examples = {
            "java": "class Demo { double f(double x) { return x / 0.0; } }",
            "csharp": "class Demo { double F(double x) { return x / 0.0; } }",
            "go": "package main\nfunc f(x float64) float64 { return x / 0.0 }",
            "rust": "fn f(x: f64) -> f64 { x / 0.0 }",
            "swift": "func f(_ x: Double) -> Double { x / 0.0 }",
            "kotlin": "fun f(x: Double): Double { x / 0.0 }",
        }

        for language, source in examples.items():
            with self.subTest(language=language):
                result = analyze_code(language, source)
                self.assertEqual(
                    sum(
                        issue["title"] == "Division or remainder by zero literal"
                        for issue in result["issues"]
                    ),
                    1,
                )

    def test_shared_tree_sitter_zero_divisor_finding_reaches_quality_score(self):
        source = "function calculate(numerator) { return numerator / 0; }\n"
        result = analyze_code("javascript", source)
        score = calculate_quality_score(source, "javascript", [], result["issues"])

        self.assertEqual(score["score"], 95)

    def test_constant_zero_expression_reaches_existing_quality_score(self):
        source = "function calculate(numerator) { return numerator / (2 - 2); }\n"
        result = analyze_code("javascript", source)
        score = calculate_quality_score(source, "javascript", [], result["issues"])

        self.assertEqual(score["score"], 95)

    def test_shared_tree_sitter_rule_skips_invalid_syntax_and_unsupported_other(self):
        invalid = analyze_code("javascript", "function calculate(value) { return value / ; }")
        unsupported = analyze_code("Other", "value / 0")

        self.assertTrue(invalid["has_syntax_error"])
        self.assertFalse(any(
            issue["title"] == "Division or remainder by zero literal"
            for issue in invalid["issues"]
        ))
        self.assertFalse(unsupported["supported"])
        self.assertFalse(any(
            issue["title"] == "Division or remainder by zero literal"
            for issue in unsupported["issues"]
        ))

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

    def test_javascript_undefined_names_respect_imports_scopes_and_properties(self):
        source = (
            'import { readFile as read, sep } from "node:fs";\n'
            "const model = { value: 1 };\n"
            "function calculate({ input }, [offset]) {\n"
            "  try {\n"
            "    const local = input + offset + model.value;\n"
            "    return read(local) + sep.length + Math.max(local, 0) + missingHelper();\n"
            "  } catch (error) {\n"
            "    return error.message;\n"
            "  }\n"
            "}\n"
            "console.log(window.location, process.env.NODE_ENV, calculate(model, [1]));\n"
            "typeof maybeGlobal;\n"
        )

        result = analyze_code("javascript", source)
        undefined = [
            issue for issue in result["issues"]
            if issue["title"].startswith("Likely undefined variable:")
        ]

        self.assertEqual(
            [(issue["title"], issue["line"]) for issue in undefined],
            [("Likely undefined variable: missingHelper", 6)],
        )
        self.assertEqual(undefined[0]["severity"], "error")
        self.assertIn("Declare or import", undefined[0]["recommendation"])

    def test_javascript_assignment_in_if_and_while_conditions_is_reported(self):
        source = (
            "let ready = false;\n"
            "let active = false;\n"
            "function isReady() { return true; }\n"
            "function hasNext() { return false; }\n"
            "if (ready = isReady()) { active = true; }\n"
            "while (active = hasNext()) { break; }\n"
            "if (ready === true) { active = false; }\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Assignment used as a condition"
        ]

        self.assertEqual([issue["line"] for issue in findings], [5, 6])
        self.assertTrue(all(issue["severity"] == "warning" for issue in findings))
        self.assertTrue(all(issue["recommendation"] for issue in findings))

    def test_javascript_loop_boundary_check_only_flags_direct_inclusive_length_bound(self):
        source = (
            "const values = [1, 2, 3];\n"
            "for (let i = 0; i <= values.length; i++) console.log(values[i]);\n"
            "for (let j = 0; j < values.length; j++) console.log(values[j]);\n"
            "for (let k = 0; k <= values.length - 1; k++) console.log(values[k]);\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Loop may access past the end of an array"
        ]

        self.assertEqual([issue["line"] for issue in findings], [2])
        self.assertIn("values.length", findings[0]["description"])
        self.assertIn("values.length", findings[0]["recommendation"])

    def test_javascript_accumulator_check_requires_zero_init_and_matching_array_loop(self):
        source = (
            "const values = [1, 2, 3];\n"
            "var total = 0;\n"
            "for (let i = 0; i < values.length; i++) total = values[i];\n"
            "let last = 0;\n"
            "for (let j = 0; j < values.length; j++) last = values[j];\n"
            "let sum = 0;\n"
            "for (let k = 0; k < values.length; k++) sum += values[k];\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"].startswith("Possible overwritten accumulator:")
        ]

        self.assertEqual(
            [(issue["title"], issue["line"]) for issue in findings],
            [("Possible overwritten accumulator: total", 3)],
        )
        self.assertIn("total += values[i]", findings[0]["recommendation"])

    def test_javascript_direct_null_property_access_is_reported_but_optional_is_safe(self):
        source = (
            "const model = { value: 1 };\n"
            "const safe = null?.value;\n"
            "const value = model.value;\n"
            "const invalid = null.value; const alsoInvalid = null['value'];\n"
            "const thirdInvalid = null['value'];\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Property access on null"
        ]

        self.assertEqual([issue["line"] for issue in findings], [4, 5])
        self.assertTrue(all(issue["severity"] == "error" for issue in findings))
        self.assertTrue(all(issue["recommendation"] for issue in findings))

    def test_javascript_string_indexes_and_char_at_report_only_visible_out_of_range_indexes(self):
        source = (
            'const text = "abc";\n'
            "text[3];\n"
            "text.charAt(4);\n"
            '"xy".charAt(2);\n'
            "text[2];\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "String index may be out of range"
        ]

        self.assertEqual([issue["line"] for issue in findings], [2, 3, 4])
        self.assertTrue(all(issue["severity"] == "warning" for issue in findings))
        self.assertTrue(all(issue["recommendation"] for issue in findings))
        self.assertEqual(
            calculate_quality_score(source, "javascript", [], result["issues"])["score"],
            85,
        )

    def test_javascript_string_index_check_ignores_valid_dynamic_and_escaped_strings(self):
        source = (
            'const text = "abc";\n'
            "text[2];\n"
            "text.charAt(2);\n"
            'let dynamic = "abc";\n'
            "dynamic[9];\n"
            'const escaped = "a\\\\nb";\n'
            "escaped[20];\n"
            '"😀"[1];\n'
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "String index may be out of range"
        ]

        self.assertEqual(findings, [])

    def test_javascript_average_division_warns_without_empty_array_guard(self):
        source = (
            "function average(values) {\n"
            "  return values.reduce((sum, value) => sum + value, 0) / values.length;\n"
            "}\n"
            "function averageGuarded(values) {\n"
            "  if (values.length === 0) return 0;\n"
            "  return values.reduce((sum, value) => sum + value, 0) / values.length;\n"
            "}\n"
            "function averageStored(values) {\n"
            "  const sum = values.reduce((accumulator, value) => accumulator + value, 0);\n"
            "  return sum / values.length;\n"
            "}\n"
            "function averagePositive(values) {\n"
            "  if (values.length > 0) return values.reduce((sum, value) => sum + value, 0) / values.length;\n"
            "}\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Average may divide by zero for an empty array"
        ]

        self.assertEqual([issue["line"] for issue in findings], [2, 10])
        self.assertTrue(all(issue["severity"] == "warning" for issue in findings))
        self.assertEqual(
            calculate_quality_score(source, "javascript", [], result["issues"])["score"],
            90,
        )

    def test_javascript_average_division_respects_else_empty_array_guard(self):
        source = (
            "function average(values) {\n"
            "  if (values.length === 0) {\n"
            "    return 0;\n"
            "  } else {\n"
            "    return values.reduce((sum, value) => sum + value, 0) / values.length;\n"
            "  }\n"
            "}\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Average may divide by zero for an empty array"
        ]

        self.assertEqual(findings, [])

    def test_javascript_insufficient_balance_handler_before_deduction_is_reported(self):
        source = (
            "function withdraw(balance, amount) {\n"
            "  if (balance < amount) {\n"
            '    console.log("Insufficient balance");\n'
            "  }\n"
            "  balance -= amount;\n"
            "}\n"
            "function charge(account, amount, reportInsufficientFunds) {\n"
            "  if (account.balance < amount) reportInsufficientFunds();\n"
            "  account.balance = account.balance - amount;\n"
            "}\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Insufficient balance may not stop the deduction"
        ]

        self.assertEqual([issue["line"] for issue in findings], [5, 9])
        self.assertTrue(all(issue["severity"] == "warning" for issue in findings))
        self.assertTrue(all(issue["recommendation"] for issue in findings))
        self.assertEqual(
            calculate_quality_score(source, "javascript", [], result["issues"])["score"],
            90,
        )

    def test_javascript_insufficient_balance_check_ignores_return_else_and_followup_guard(self):
        source = (
            "function withdraw(balance, amount) {\n"
            "  if (balance < amount) {\n"
            '    console.log("Insufficient balance");\n'
            "    return false;\n"
            "  }\n"
            "  balance -= amount;\n"
            "}\n"
            "function withdrawElse(balance, amount) {\n"
            "  if (balance < amount) {\n"
            '    console.log("Insufficient balance");\n'
            "  } else {\n"
            "    balance -= amount;\n"
            "  }\n"
            "}\n"
            "function withdrawGuarded(balance, amount) {\n"
            "  if (balance < amount) console.log('Insufficient balance');\n"
            "  if (balance < amount) return false;\n"
            "  balance -= amount;\n"
            "}\n"
        )

        result = analyze_code("javascript", source)
        findings = [
            issue for issue in result["issues"]
            if issue["title"] == "Insufficient balance may not stop the deduction"
        ]

        self.assertEqual(findings, [])

    def test_javascript_semantic_findings_reach_existing_quality_score(self):
        source = "function calculate(value) { return value + missingHelper(); }\n"
        analysis = analyze_code("javascript", source)
        score = calculate_quality_score(
            source,
            "javascript",
            ai_issues=[],
            static_issues=analysis["issues"],
        )

        self.assertTrue(any(
            issue["title"] == "Likely undefined variable: missingHelper"
            and issue["source"] == "javascript_semantic"
            for issue in analysis["issues"]
        ))
        self.assertEqual(score["score"], 88)

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
