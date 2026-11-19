"""Bounded evaluation of numeric Tree-sitter expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import (
    Decimal,
    DecimalException,
    Inexact,
    Rounded,
    localcontext,
)
from enum import Enum

from tree_sitter import Node


MAX_EXPRESSION_DEPTH = 32
MAX_NUMERIC_DIGITS = 128

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
_PARENTHESIS_TYPES = {
    "expression_list",
    "parenthesized",
    "parenthesized_expression",
    "parenthesized_statements",
    "tuple_expression",
}
_BINARY_OPERATORS = {"+", "-", "*", "/", "//", "%"}
_ALIASES = {
    "c#": "csharp",
    "c++": "cpp",
    "js": "javascript",
    "py": "python",
    "ts": "typescript",
}
_FIXED_WIDTH_INTEGER_LANGUAGES = {
    "c",
    "cpp",
    "csharp",
    "go",
    "java",
    "kotlin",
    "rust",
    "swift",
}


class NumericKind(str, Enum):
    INTEGER = "integer"
    REAL = "real"
    BIG_INTEGER = "big_integer"


@dataclass(frozen=True)
class Constant:
    value: int | Decimal
    numeric_kind: NumericKind
    exact: bool = True


@dataclass(frozen=True)
class KnownZero:
    numeric_kind: NumericKind


@dataclass(frozen=True)
class KnownNonzero:
    numeric_kind: NumericKind


@dataclass(frozen=True)
class Unknown:
    pass


AbstractValue = Constant | KnownZero | KnownNonzero | Unknown
UNKNOWN = Unknown()


def is_known_zero(value: AbstractValue) -> bool:
    return isinstance(value, KnownZero) or (
        isinstance(value, Constant) and value.value == 0
    )


def _known_constant(
    value: int | Decimal,
    numeric_kind: NumericKind,
    max_digits: int = MAX_NUMERIC_DIGITS,
    *,
    exact: bool = True,
) -> AbstractValue:
    if value == 0:
        return KnownZero(numeric_kind)
    if not _is_bounded_number(value, max_digits):
        return UNKNOWN
    return Constant(value, numeric_kind, exact)


def _is_bounded_number(value: int | Decimal, max_digits: int = MAX_NUMERIC_DIGITS) -> bool:
    if isinstance(value, int):
        return len(str(abs(value))) <= max_digits
    if not value.is_finite():
        return False
    digits = value.as_tuple().digits
    return (
        len(digits) <= max_digits
        and abs(value.as_tuple().exponent) <= max_digits
        and abs(value.adjusted()) <= max_digits
    )


def _literal_suffix(language: str) -> tuple[re.Pattern[str] | None, str]:
    suffixes = {
        "c": (r"(?i)(u(?:ll?|l?l?)?|ll?u?)$", "integer"),
        "cpp": (r"(?i)(u(?:ll?|l?l?)?|ll?u?|f)$", "language"),
        "csharp": (r"(?i)(ul|lu|u|l|f|d|m)$", "language"),
        "java": (r"(?i)(l|f|d)$", "language"),
        "kotlin": (r"(?i)(ul|lu|u|l|f)$", "language"),
        "rust": (r"(?i)(?:i(?:8|16|32|64|128|size)|u(?:8|16|32|64|128|size)|f(?:32|64))$", "language"),
    }
    if language in {"javascript", "typescript"}:
        return re.compile(r"n$"), "big_integer"
    suffix = suffixes.get(language)
    if suffix is None:
        return None, ""
    return re.compile(suffix[0]), suffix[1]


def _parse_numeric_literal(
    raw: str,
    language: str,
    max_digits: int,
) -> tuple[int | Decimal, NumericKind] | None:
    raw = raw.replace("_", "")
    suffix_pattern, suffix_mode = _literal_suffix(language)
    suffix = ""
    if suffix_pattern is not None:
        match = suffix_pattern.search(raw)
        is_hexadecimal_integer = re.fullmatch(r"0[xX][0-9a-fA-F]+", raw) is not None
        if match and not (
            is_hexadecimal_integer
            and match.group(0).lower() in {"f", "d", "m", "f32", "f64"}
        ):
            suffix = match.group(0)
            raw = raw[:match.start()]
    elif re.search(r"[a-zA-Z]$", raw):
        return None

    if not raw or len(raw) > max_digits + 16:
        return None
    if sum(character.isdigit() for character in raw) > max_digits:
        return None

    lower = raw.lower()
    if suffix_mode == "big_integer" and suffix:
        if any(character in lower for character in ".ep"):
            return None
        numeric_kind = NumericKind.BIG_INTEGER
    elif suffix_mode == "big_integer":
        numeric_kind = NumericKind.REAL
    elif suffix_mode == "language" and suffix and suffix.lower() in {"f", "d", "m"}:
        numeric_kind = NumericKind.REAL
    elif any(character in lower for character in ".e"):
        numeric_kind = NumericKind.REAL
    else:
        numeric_kind = NumericKind.INTEGER

    try:
        if lower.startswith(("0x", "0b", "0o")):
            if numeric_kind == NumericKind.REAL:
                return None
            value: int | Decimal = int(raw, 0)
        elif numeric_kind in {NumericKind.INTEGER, NumericKind.BIG_INTEGER}:
            value = int(raw, 10)
        else:
            value = Decimal(raw)
    except (ValueError, DecimalException):
        return None

    if isinstance(value, int) and language in _FIXED_WIDTH_INTEGER_LANGUAGES:
        if not -127 <= value <= 127:
            return None
    if isinstance(value, Decimal) and (
        not value.is_finite()
        or abs(value.adjusted()) > 30
        or abs(value.as_tuple().exponent) > 30
    ):
        return None
    if not _is_bounded_number(value, max_digits):
        return None
    return value, numeric_kind


def _node_operator(node: Node) -> str | None:
    operators = [
        child.text.decode("utf-8")
        for child in node.children
        if not child.is_named and child.text.decode("utf-8") in _BINARY_OPERATORS
    ]
    return operators[0] if len(operators) == 1 else None


def _numeric_parts(
    value: AbstractValue,
) -> tuple[int | Decimal, NumericKind] | None:
    if isinstance(value, Constant):
        return value.value, value.numeric_kind
    if isinstance(value, KnownZero):
        return 0, value.numeric_kind
    return None


def _value_is_exact(value: AbstractValue) -> bool:
    return not isinstance(value, Constant) or value.exact


def _decimal(value: int | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(value)


def _is_integral_number(value: int | Decimal) -> bool:
    return isinstance(value, int) or value == value.to_integral_value()


def _exact_binary_float(value: Decimal) -> bool:
    if value == 0:
        return True
    if abs(value.adjusted()) > 30:
        return False
    numerator, denominator = value.as_integer_ratio()
    if denominator & (denominator - 1):
        return False
    significand = abs(numerator)
    while significand % 2 == 0:
        significand //= 2
    return significand.bit_length() <= 24


def _integer_result_is_safe(language: str, value: int) -> bool:
    if language in _FIXED_WIDTH_INTEGER_LANGUAGES:
        return -127 <= value <= 127
    return len(str(abs(value))) <= MAX_NUMERIC_DIGITS


def _truncating_quotient(numerator: int, denominator: int) -> int:
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def _binary_result(
    operator: str,
    left: AbstractValue,
    right: AbstractValue,
    language: str,
    max_digits: int,
) -> AbstractValue:
    left_parts = _numeric_parts(left)
    right_parts = _numeric_parts(right)
    if left_parts is None or right_parts is None:
        return UNKNOWN

    left_value, left_kind = left_parts
    right_value, right_kind = right_parts
    if (
        NumericKind.BIG_INTEGER in {left_kind, right_kind}
        and left_kind != right_kind
    ):
        return UNKNOWN

    kind = (
        NumericKind.BIG_INTEGER
        if left_kind == right_kind == NumericKind.BIG_INTEGER
        else NumericKind.REAL
        if NumericKind.REAL in {left_kind, right_kind}
        else NumericKind.INTEGER
    )

    if operator in {"+", "-", "*"}:
        if kind == NumericKind.REAL:
            left_decimal = _decimal(left_value)
            right_decimal = _decimal(right_value)
            if operator == "-" and left_decimal == right_decimal:
                return KnownZero(kind)
            if operator == "+" and (left_decimal == 0 or right_decimal == 0):
                preserved = left if right_decimal == 0 else right
                return _known_constant(
                    left_decimal if right_decimal == 0 else right_decimal,
                    kind,
                    max_digits,
                    exact=_value_is_exact(preserved),
                )
            if operator == "-" and right_decimal == 0:
                return _known_constant(
                    left_decimal,
                    kind,
                    max_digits,
                    exact=_value_is_exact(left),
                )
            if operator == "-" and left_decimal == 0:
                return _known_constant(
                    -right_decimal,
                    kind,
                    max_digits,
                    exact=_value_is_exact(right),
                )
            if operator == "*" and (left_decimal == 0 or right_decimal == 0):
                return KnownZero(kind)
            if not (
                _exact_binary_float(left_decimal)
                and _exact_binary_float(right_decimal)
            ):
                return UNKNOWN
            with localcontext() as context:
                context.prec = max_digits * 2 + 16
                try:
                    result = {
                        "+": lambda: left_decimal + right_decimal,
                        "-": lambda: left_decimal - right_decimal,
                        "*": lambda: left_decimal * right_decimal,
                    }[operator]()
                except DecimalException:
                    return UNKNOWN
        else:
            left_integer, right_integer = int(left_value), int(right_value)
            result = {
                "+": lambda: left_integer + right_integer,
                "-": lambda: left_integer - right_integer,
                "*": lambda: left_integer * right_integer,
            }[operator]()
            if not _integer_result_is_safe(language, result):
                return UNKNOWN
        return _known_constant(result, kind, max_digits)

    if operator in {"/", "//", "%"}:
        if right_value == 0:
            return UNKNOWN
        if operator == "//" and language != "python":
            return UNKNOWN
        if operator == "%" and (
            left_kind == NumericKind.REAL or right_kind == NumericKind.REAL
        ):
            if (
                language not in {"javascript", "typescript"}
                or not _is_integral_number(left_value)
                or not _is_integral_number(right_value)
                or not _exact_binary_float(_decimal(left_value))
                or not _exact_binary_float(_decimal(right_value))
            ):
                return UNKNOWN

        if operator == "%":
            numerator, denominator = int(left_value), int(right_value)
            if language in {"python", "ruby"}:
                result = numerator % denominator
            else:
                quotient = _truncating_quotient(numerator, denominator)
                result = numerator - quotient * denominator
            return _known_constant(result, kind, max_digits)

        if operator == "//":
            if left_kind != NumericKind.INTEGER or right_kind != NumericKind.INTEGER:
                return UNKNOWN
            return _known_constant(
                int(left_value) // int(right_value),
                NumericKind.INTEGER,
                max_digits,
            )

        if left_value == 0:
            result_kind = (
                NumericKind.REAL
                if language in {"javascript", "typescript", "php", "python"}
                or NumericKind.REAL in {left_kind, right_kind}
                else kind
            )
            return KnownZero(result_kind)

        if (
            language in {"rust", "swift"}
            and left_kind == right_kind == NumericKind.INTEGER
        ):
            return UNKNOWN

        if kind == NumericKind.BIG_INTEGER or (
            kind != NumericKind.REAL
            and language not in {"python", "javascript", "typescript", "php"}
        ):
            result = (
                int(left_value) // int(right_value)
                if language == "ruby" and kind != NumericKind.BIG_INTEGER
                else _truncating_quotient(int(left_value), int(right_value))
            )
            if not _integer_result_is_safe(language, result):
                return UNKNOWN
            return _known_constant(result, kind, max_digits)

        left_decimal = _decimal(left_value)
        right_decimal = _decimal(right_value)
        if not (
            _value_is_exact(left)
            and _value_is_exact(right)
            and _exact_binary_float(left_decimal)
            and _exact_binary_float(right_decimal)
        ):
            return UNKNOWN
        with localcontext() as context:
            context.prec = max_digits * 2 + 16
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            try:
                result = left_decimal / right_decimal
            except DecimalException:
                with localcontext() as approximate_context:
                    approximate_context.prec = max_digits * 2 + 16
                    approximate_context.traps[Inexact] = False
                    approximate_context.traps[Rounded] = False
                    try:
                        approximate = left_decimal / right_decimal
                    except DecimalException:
                        return UNKNOWN
                if (
                    approximate != 0
                    and approximate.is_finite()
                    and -30 <= approximate.adjusted() <= 30
                ):
                    return KnownNonzero(NumericKind.REAL)
                return UNKNOWN
        if not _exact_binary_float(result):
            return UNKNOWN
        return _known_constant(result, NumericKind.REAL, max_digits)

    return UNKNOWN


def evaluate_numeric_expression(
    node: Node | None,
    language: str,
    *,
    max_depth: int = MAX_EXPRESSION_DEPTH,
    max_digits: int = MAX_NUMERIC_DIGITS,
) -> AbstractValue:
    """Evaluate a small numeric expression without executing source code."""
    normalized_language = _ALIASES.get(language.strip().lower(), language.strip().lower())

    def evaluate(current: Node | None, depth: int) -> AbstractValue:
        if current is None or depth > max_depth:
            return UNKNOWN

        if current.type in _PARENTHESIS_TYPES:
            if len(current.named_children) != 1:
                return UNKNOWN
            return evaluate(current.named_children[0], depth + 1)

        if current.type in _NUMERIC_LITERAL_TYPES:
            parsed = _parse_numeric_literal(
                current.text.decode("utf-8"),
                normalized_language,
                max_digits,
            )
            if parsed is None:
                return UNKNOWN
            value, numeric_kind = parsed
            exact = (
                numeric_kind != NumericKind.REAL
                or _exact_binary_float(_decimal(value))
            )
            return _known_constant(
                value,
                numeric_kind,
                max_digits,
                exact=exact,
            )

        operands = current.named_children
        operator = _node_operator(current)
        if len(operands) == 1 and operator in {"+", "-"}:
            operand = evaluate(operands[0], depth + 1)
            parts = _numeric_parts(operand)
            if parts is None:
                if isinstance(operand, KnownNonzero):
                    return operand
                return UNKNOWN
            value, numeric_kind = parts
            return _known_constant(
                value if operator == "+" else -value,
                numeric_kind,
                max_digits,
                exact=_value_is_exact(operand),
            )

        if len(operands) == 2 and operator in _BINARY_OPERATORS:
            return _binary_result(
                operator,
                evaluate(operands[0], depth + 1),
                evaluate(operands[1], depth + 1),
                normalized_language,
                max_digits,
            )
        return UNKNOWN

    if max_depth < 0 or max_digits < 1:
        return UNKNOWN
    return evaluate(node, 0)
