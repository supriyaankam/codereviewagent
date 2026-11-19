import unittest

from app.analyzers.tree_sitter_value_tracker import (
    analyze_tree_sitter_value_tracking,
)
from app.services.code_analyzer import analyze_code


class TreeSitterValueTrackerTests(unittest.TestCase):
    ZERO_DIVISOR_EXAMPLES = {
        "python": (
            "def calculate(value):\n"
            "    divisor = 0\n"
            "    return value / divisor\n"
        ),
        "javascript": (
            "function calculate(value) {\n"
            "  let divisor = 0;\n"
            "  return value / divisor;\n"
            "}\n"
        ),
        "typescript": (
            "function calculate(value: number): number {\n"
            "  let divisor: number = 0;\n"
            "  return value / divisor;\n"
            "}\n"
        ),
        "java": (
            "class Demo {\n"
            "  int calculate(int value) {\n"
            "    int divisor = 0;\n"
            "    return value / divisor;\n"
            "  }\n"
            "}\n"
        ),
        "csharp": (
            "class Demo {\n"
            "  int Calculate(int value) {\n"
            "    int divisor = 0;\n"
            "    return value / divisor;\n"
            "  }\n"
            "}\n"
        ),
        "c": (
            "int calculate(int value) {\n"
            "  int divisor = 0;\n"
            "  return value / divisor;\n"
            "}\n"
        ),
        "cpp": (
            "int calculate(int value) {\n"
            "  int divisor = 0;\n"
            "  return value / divisor;\n"
            "}\n"
        ),
        "go": (
            "package main\n"
            "func calculate(value int) int {\n"
            "  var divisor int = 0\n"
            "  return value / divisor\n"
            "}\n"
        ),
        "rust": (
            "fn calculate(value: i32) -> i32 {\n"
            "  let divisor = 0;\n"
            "  value / divisor\n"
            "}\n"
        ),
        "ruby": (
            "def calculate(value)\n"
            "  divisor = 0\n"
            "  value / divisor\n"
            "end\n"
        ),
        "php": (
            "<?php\n"
            "function calculate($value) {\n"
            "  $divisor = 0;\n"
            "  return $value / $divisor;\n"
            "}\n"
        ),
        "swift": (
            "func calculate(_ value: Int) -> Int {\n"
            "  let divisor = 0\n"
            "  return value / divisor\n"
            "}\n"
        ),
        "kotlin": (
            "fun calculate(value: Int): Int {\n"
            "  val divisor = 0\n"
            "  return value / divisor\n"
            "}\n"
        ),
    }

    def test_initialized_zero_is_reported_for_every_parser_backed_language(self):
        for language, code in self.ZERO_DIVISOR_EXAMPLES.items():
            with self.subTest(language=language):
                findings = analyze_tree_sitter_value_tracking(language, code)
                self.assertEqual(len(findings), 1)
                expected_line = 4 if language in {
                    "java", "csharp", "go", "php"
                } else 3
                self.assertEqual(findings[0].line, expected_line)
                self.assertEqual(findings[0].source, "tree_sitter_value_tracking")

    def test_nonzero_initializers_do_not_report(self):
        examples = {
            language: code.replace("= 0", "= 2").replace("=0", "=2")
            for language, code in self.ZERO_DIVISOR_EXAMPLES.items()
        }
        for language, code in examples.items():
            with self.subTest(language=language):
                self.assertEqual(analyze_tree_sitter_value_tracking(language, code), [])

    def test_reassignment_to_zero_is_reported_and_reassignment_away_from_zero_is_not(self):
        examples = {
            "python": (
                "def f(value):\n"
                "  divisor = 4\n"
                "  divisor = 0\n"
                "  return value / divisor\n",
                "def f(value):\n"
                "  divisor = 0\n"
                "  divisor = 4\n"
                "  return value / divisor\n",
            ),
            "javascript": (
                "function f(value) { let divisor = 4; divisor = 0; return value / divisor; }",
                "function f(value) { let divisor = 0; divisor = 4; return value / divisor; }",
            ),
            "typescript": (
                "function f(value: number) { let divisor = 4; divisor = 0; return value / divisor; }",
                "function f(value: number) { let divisor = 0; divisor = 4; return value / divisor; }",
            ),
            "java": (
                "class D { int f(int value) { int divisor = 4; divisor = 0; return value / divisor; } }",
                "class D { int f(int value) { int divisor = 0; divisor = 4; return value / divisor; } }",
            ),
            "csharp": (
                "class D { int F(int value) { int divisor = 4; divisor = 0; return value / divisor; } }",
                "class D { int F(int value) { int divisor = 0; divisor = 4; return value / divisor; } }",
            ),
            "c": (
                "int f(int value) { int divisor = 4; divisor = 0; return value / divisor; }",
                "int f(int value) { int divisor = 0; divisor = 4; return value / divisor; }",
            ),
            "cpp": (
                "int f(int value) { int divisor = 4; divisor = 0; return value / divisor; }",
                "int f(int value) { int divisor = 0; divisor = 4; return value / divisor; }",
            ),
            "go": (
                "package main\nfunc f(value int) int { divisor := 4; divisor = 0; return value / divisor }",
                "package main\nfunc f(value int) int { divisor := 0; divisor = 4; return value / divisor }",
            ),
            "rust": (
                "fn f(value: i32) -> i32 { let mut divisor = 4; divisor = 0; value / divisor }",
                "fn f(value: i32) -> i32 { let mut divisor = 0; divisor = 4; value / divisor }",
            ),
            "ruby": (
                "def f(value)\n divisor = 4\n divisor = 0\n value / divisor\nend",
                "def f(value)\n divisor = 0\n divisor = 4\n value / divisor\nend",
            ),
            "php": (
                "<?php function f($value) { $divisor = 4; $divisor = 0; return $value / $divisor; }",
                "<?php function f($value) { $divisor = 0; $divisor = 4; return $value / $divisor; }",
            ),
            "swift": (
                "func f(_ value: Int) -> Int { var divisor = 4; divisor = 0; return value / divisor }",
                "func f(_ value: Int) -> Int { var divisor = 0; divisor = 4; return value / divisor }",
            ),
            "kotlin": (
                "fun f(value: Int): Int { var divisor = 4; divisor = 0; return value / divisor }",
                "fun f(value: Int): Int { var divisor = 0; divisor = 4; return value / divisor }",
            ),
        }
        for language, (to_zero, from_zero) in examples.items():
            with self.subTest(language=language, reassignment="to zero"):
                self.assertEqual(len(analyze_tree_sitter_value_tracking(language, to_zero)), 1)
            with self.subTest(language=language, reassignment="from zero"):
                self.assertEqual(analyze_tree_sitter_value_tracking(language, from_zero), [])

    def test_shadowed_variable_uses_nearest_lexical_binding(self):
        examples = {
            "javascript": (
                "function f(value) { let divisor = 0; { let divisor = 2; return value / divisor; } }"
            ),
            "typescript": (
                "function f(value: number) { let divisor = 0; { let divisor = 2; return value / divisor; } }"
            ),
            "java": (
                "class D { int f(int value) { int divisor = 0; { int divisor = 2; return value / divisor; } } }"
            ),
            "csharp": (
                "class D { int F(int value) { int divisor = 0; { int divisor = 2; return value / divisor; } } }"
            ),
            "c": "int f(int value) { int divisor = 0; { int divisor = 2; return value / divisor; } }",
            "cpp": "int f(int value) { int divisor = 0; { int divisor = 2; return value / divisor; } }",
            "go": "package main\nfunc f(value int) int { divisor := 0; { divisor := 2; return value / divisor } }",
            "rust": "fn f(value: i32) -> i32 { let divisor = 0; { let divisor = 2; value / divisor } }",
            "swift": "func f(_ value: Int) -> Int { let divisor = 0; do { let divisor = 2; return value / divisor } }",
            "kotlin": "fun f(value: Int): Int { val divisor = 0; run { val divisor = 2; value / divisor } }",
        }
        for language, code in examples.items():
            with self.subTest(language=language):
                self.assertEqual(analyze_tree_sitter_value_tracking(language, code), [])

    def test_calls_and_control_flow_invalidate_values_conservatively(self):
        examples = {
            "python": "def f(value):\n divisor = 0\n refresh()\n return value / divisor\n",
            "javascript": "function f(value) { let divisor = 0; refresh(); return value / divisor; }",
            "java": "class D { int f(int value) { int divisor = 0; refresh(); return value / divisor; } }",
            "go": "package main\nfunc f(value int) int { divisor := 0; refresh(); return value / divisor }",
            "rust": "fn f(value: i32) -> i32 { let divisor = 0; refresh(); value / divisor }",
        }
        for language, code in examples.items():
            with self.subTest(language=language):
                self.assertEqual(analyze_tree_sitter_value_tracking(language, code), [])

        conditional = (
            "function f(value, flag) {\n"
            "  let divisor = 0;\n"
            "  if (flag) divisor = 1;\n"
            "  return value / divisor;\n"
            "}\n"
        )
        self.assertEqual(analyze_tree_sitter_value_tracking("javascript", conditional), [])

    def test_separate_function_scopes_do_not_leak_values(self):
        code = (
            "function first() { let divisor = 0; }\n"
            "function second(value) { return value / divisor; }\n"
        )
        self.assertEqual(analyze_tree_sitter_value_tracking("javascript", code), [])

    def test_multiple_assignments_and_independent_variables(self):
        code = (
            "function f(value) {\n"
            "  let divisor = 3;\n"
            "  divisor = 2;\n"
            "  divisor = 0;\n"
            "  const safeDivisor = 5;\n"
            "  const first = value / safeDivisor;\n"
            "  return value / divisor;\n"
            "}\n"
        )
        findings = analyze_tree_sitter_value_tracking("javascript", code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, 7)

    def test_function_call_and_unsupported_writes_invalidate_values(self):
        examples = (
            "function f(value) { let divisor = 0; divisor = getValue(); return value / divisor; }",
            "function f(value) { let divisor = 0; divisor += 1; return value / divisor; }",
            "function f(value, values) { let divisor = 0; divisor = values[0]; return value / divisor; }",
        )
        for code in examples:
            with self.subTest(code=code):
                self.assertEqual(analyze_tree_sitter_value_tracking("javascript", code), [])

    def test_shared_tracker_is_reached_through_analyze_code(self):
        result = analyze_code(
            "javascript",
            "function f(value) {\n  let divisor = 0;\n  return value / divisor;\n}\n",
        )
        findings = [
            issue for issue in result["issues"]
            if issue["source"] == "tree_sitter_value_tracking"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 3)

    def test_unsupported_language_and_empty_source_return_no_findings(self):
        self.assertEqual(analyze_tree_sitter_value_tracking("other", "x / 0"), [])
        self.assertEqual(analyze_tree_sitter_value_tracking("python", "  \n"), [])


if __name__ == "__main__":
    unittest.main()
