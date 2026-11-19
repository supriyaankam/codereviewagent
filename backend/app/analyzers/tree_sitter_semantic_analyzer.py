"""Conservative semantic checks shared by Tree-sitter-backed languages."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tree_sitter import Node

from app.analyzers.abstract_value import evaluate_numeric_expression, is_known_zero
from app.analyzers.base import Category, Issue, Severity
from app.analyzers.language_analyzer import _parse_source


_NUMERIC_LITERAL_TYPES = {
    "decimal_integer_literal",
    "decimal_floating_point_literal",
    "float",
    "float_literal",
    "int_literal",
    "integer",
    "integer_literal",
    "number",
    "number_literal",
    "real_literal",
}


def _unwrap_parentheses(node: Node | None) -> Node | None:
    while node is not None and node.type in {
        "parenthesized_expression",
        "parenthesized",
        "expression_list",
    }:
        if len(node.named_children) != 1:
            return None
        node = node.named_children[0]
    return node


def _is_numeric_zero(node: Node | None) -> bool:
    node = _unwrap_parentheses(node)
    if node is None:
        return False
    if node.type == "unary_expression":
        operator = next(
            (child.text for child in node.children if not child.is_named),
            b"",
        )
        return operator in {b"+", b"-"} and _is_numeric_zero(
            node.child_by_field_name("argument")
            or (node.named_children[-1] if node.named_children else None)
        )
    if node.type not in _NUMERIC_LITERAL_TYPES:
        return False

    raw = node.text.decode("utf-8").replace("_", "")
    if raw.lower().startswith(("0x", "0b", "0o")):
        raw = re.sub(r"(?i)[uUlL]+$", "", raw)
        try:
            return int(raw, 0) == 0
        except ValueError:
            return False

    raw = re.sub(r"(?i)[uUlLfFdDmMn]+$", "", raw)
    try:
        return Decimal(raw) == 0
    except InvalidOperation:
        return False


def _division_operator(node: Node, language: str) -> str | None:
    if not node.is_named or len(node.named_children) < 2:
        return None
    operators = {
        child.text.decode("utf-8")
        for child in node.children
        if not child.is_named and child.text in {b"/", b"//", b"%"}
    }
    if "//" in operators and language.strip().lower() not in {"python", "py"}:
        operators.remove("//")
    return next(iter(operators)) if len(operators) == 1 else None


def analyze_tree_sitter_semantics(language: str, code: str) -> list[Issue]:
    """Report syntax-tree patterns that are definite arithmetic errors."""
    tree = _parse_source(language, code)
    if tree is None or tree.root_node.has_error:
        return []

    issues = []
    nodes = [tree.root_node]
    while nodes:
        node = nodes.pop()
        operator = _division_operator(node, language)
        operands = node.named_children
        if operator is not None and len(operands) >= 2:
            divisor = operands[-1]
            literal_zero = _is_numeric_zero(divisor)
            expression_zero = is_known_zero(
                evaluate_numeric_expression(divisor, language)
            )
        else:
            literal_zero = expression_zero = False

        if literal_zero or expression_zero:
            operation = "remainder" if operator == "%" else "division"
            is_literal = literal_zero
            issues.append(Issue(
                category=Category.quality,
                severity=Severity.warning,
                title=(
                    "Division or remainder by zero literal"
                    if is_literal
                    else "Division or remainder by constant zero expression"
                ),
                description=(
                    f"This {operation} expression uses a numeric zero divisor."
                    if is_literal
                    else (
                        f"The divisor in this {operation} expression evaluates to zero "
                        "using bounded constant-expression evaluation."
                    )
                )
                + (
                    " Its result may be non-finite, fail at runtime, or be undefined "
                    "depending on the language and operand types."
                ),
                recommendation=(
                    "Validate that the divisor is nonzero before evaluating this expression."
                ),
                line=node.start_point.row + 1,
                column=node.start_point.column + 1,
                source="tree_sitter_semantic",
            ))
        nodes.extend(reversed(node.children))
    return issues
