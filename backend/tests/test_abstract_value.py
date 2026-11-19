import unittest
from decimal import Decimal

from app.analyzers.abstract_value import (
    MAX_EXPRESSION_DEPTH,
    MAX_NUMERIC_DIGITS,
    Constant,
    KnownNonzero,
    KnownZero,
    NumericKind,
    Unknown,
    evaluate_numeric_expression,
)
from app.analyzers.language_analyzer import _parse_source


def _expression_node(expression, language="python"):
    code = f"def calculate(): return {expression}"
    tree = _parse_source(language, code)
    if tree is None or tree.root_node.has_error:
        raise AssertionError(f"Could not parse {language} expression: {expression}")
    start = len("def calculate(): return ")
    candidates = [
        node
        for node in _walk(tree.root_node)
        if node.start_byte == start and node.end_byte == len(code)
    ]
    if not candidates:
        raise AssertionError(f"Could not locate expression: {expression}")
    return candidates[0]


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class AbstractValueTests(unittest.TestCase):
    def evaluate(self, expression, language="python", **kwargs):
        return evaluate_numeric_expression(
            _expression_node(expression, language),
            language,
            **kwargs,
        )

    def test_numeric_zero_expressions(self):
        for expression in ("2 - 2", "5 - 5", "2 * 0", "-0", "((0))"):
            with self.subTest(expression=expression):
                self.assertIsInstance(self.evaluate(expression), KnownZero)

    def test_nonzero_constants(self):
        for expression, expected in (("10 - 2", 8), ("2 + 3", 5), ("-(2 + 3)", -5)):
            with self.subTest(expression=expression):
                result = self.evaluate(expression)
                self.assertIsInstance(result, Constant)
                self.assertEqual(result.value, expected)

    def test_unsupported_syntax_returns_unknown(self):
        for expression in ("get_zero()", "value", "2 ** 3", '"0"'):
            with self.subTest(expression=expression):
                self.assertIsInstance(self.evaluate(expression), Unknown)

    def test_depth_and_numeric_size_are_bounded(self):
        nested = "(" * (MAX_EXPRESSION_DEPTH + 4) + "1" + ")" * (MAX_EXPRESSION_DEPTH + 4)
        oversized = "9" * (MAX_NUMERIC_DIGITS + 1)

        self.assertIsInstance(
            self.evaluate(nested, max_depth=MAX_EXPRESSION_DEPTH),
            Unknown,
        )
        self.assertIsInstance(self.evaluate(oversized), Unknown)

    def test_division_and_remainder_are_evaluated_only_when_safe(self):
        division = self.evaluate("8 / 2")
        remainder = self.evaluate("5 % 2")
        floor_division = self.evaluate("5 // 2")

        self.assertIsInstance(division, Constant)
        self.assertEqual(division.value, 4)
        self.assertIsInstance(remainder, Constant)
        self.assertEqual(remainder.value, 1)
        self.assertIsInstance(floor_division, Constant)
        self.assertEqual(floor_division.value, 2)
        self.assertIsInstance(self.evaluate("1 / 3"), KnownNonzero)
        self.assertIsInstance(self.evaluate("1 / 0"), Unknown)

    def test_javascript_numbers_and_hexadecimal_suffixes_are_parsed_safely(self):
        js_node = _expression_node("2 + 3")
        js_result = evaluate_numeric_expression(js_node, "javascript")
        js_remainder_node = _expression_node("5 % 2")
        js_remainder = evaluate_numeric_expression(js_remainder_node, "javascript")
        java_hex_node = _expression_node("0x1d")
        java_hex = evaluate_numeric_expression(java_hex_node, "java")
        cpp_hex_node = _expression_node("0x1f")
        cpp_hex = evaluate_numeric_expression(cpp_hex_node, "cpp")

        self.assertIsInstance(js_result, Constant)
        self.assertEqual(js_result.value, Decimal("5"))
        self.assertIsInstance(js_remainder, Constant)
        self.assertEqual(js_remainder.value, 1)
        self.assertIsInstance(java_hex, Constant)
        self.assertEqual(java_hex.value, 29)
        self.assertIsInstance(cpp_hex, Constant)
        self.assertEqual(cpp_hex.value, 31)

    def test_integer_division_uses_conservative_language_semantics(self):
        node = _expression_node("-5 / 2")
        java = evaluate_numeric_expression(node, "java")
        ruby = evaluate_numeric_expression(node, "ruby")
        swift = evaluate_numeric_expression(node, "swift")

        self.assertIsInstance(java, Constant)
        self.assertEqual(java.value, -2)
        self.assertIsInstance(ruby, Constant)
        self.assertEqual(ruby.value, -3)
        self.assertIsInstance(swift, Unknown)

    def test_division_semantics_differ_by_language(self):
        node = _expression_node("1 / 3")

        self.assertIsInstance(evaluate_numeric_expression(node, "python"), KnownNonzero)
        self.assertIsInstance(evaluate_numeric_expression(node, "javascript"), KnownNonzero)
        self.assertIsInstance(evaluate_numeric_expression(node, "java"), KnownZero)
        self.assertIsInstance(evaluate_numeric_expression(node, "rust"), Unknown)

    def test_inexact_real_values_are_not_reused_as_exact_constants(self):
        rounded_expression = _expression_node(
            "1 / (0.1 + 0.0) - 10",
        )
        evaluator_result = evaluate_numeric_expression(
            rounded_expression,
            "javascript",
        )
        same_literal_subtraction = self.evaluate("0.1 - 0.1", "python")

        self.assertIsInstance(evaluator_result, Unknown)
        self.assertIsInstance(same_literal_subtraction, KnownZero)

    def test_known_nonzero_representation_is_available(self):
        self.assertEqual(KnownNonzero(NumericKind.INTEGER).numeric_kind, NumericKind.INTEGER)


if __name__ == "__main__":
    unittest.main()
