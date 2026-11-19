"""Scope-aware, straight-line numeric value tracking for Tree-sitter languages."""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from app.analyzers.abstract_value import (
    AbstractValue,
    UNKNOWN,
    evaluate_numeric_expression,
    is_known_zero,
)
from app.analyzers.base import Category, Issue, Severity
from app.analyzers.language_analyzer import _parse_source, canonical_language


@dataclass(frozen=True)
class _LanguageAdapter:
    identifier_types: frozenset[str]
    function_types: frozenset[str]
    block_types: frozenset[str]
    declaration_types: frozenset[str]
    assignment_types: frozenset[str]
    control_types: frozenset[str]
    call_types: frozenset[str]
    function_scoped_assignments: bool = False


_COMMON_CONTROLS = frozenset({
    "catch_clause",
    "conditional_expression",
    "do_statement",
    "do_while_statement",
    "for_expression",
    "for_in_statement",
    "for_statement",
    "guard_statement",
    "if_expression",
    "if_statement",
    "loop_expression",
    "match_expression",
    "match_statement",
    "repeat_statement",
    "repeat_while_statement",
    "switch_expression",
    "switch_statement",
    "try_expression",
    "try_statement",
    "when_expression",
    "while_expression",
    "while_statement",
})
_SIDE_EFFECT_READ_TYPES = frozenset({
    "element_access_expression",
    "field_expression",
    "member_access_expression",
    "member_expression",
    "object_access_expression",
    "selector_expression",
    "subscript_expression",
})
_COMMON_CALLS = frozenset({
    "call",
    "call_expression",
    "command",
    "command_call",
    "function_call_expression",
    "macro_invocation",
    "member_call_expression",
    "method_call_expression",
    "method_invocation",
    "new_expression",
    "object_creation_expression",
    "scoped_call_expression",
})
_ASSIGNMENT_OPERATORS = frozenset({
    "=",
    ":=",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "<<=",
    ">>=",
    "++",
    "--",
})
_UPDATE_TYPES = frozenset({
    "augmented_assignment",
    "augmented_assignment_expression",
    "compound_assignment",
    "compound_assignment_expr",
    "operator_assignment",
    "postfix_unary_expression",
    "prefix_unary_expression",
    "update_expression",
})
_TARGET_WRAPPER_TYPES = frozenset({
    "directly_assignable_expression",
    "expression_list",
    "left_hand_side_expression",
    "parenthesized_expression",
    "pattern",
    "variable_declaration",
})
_DECLARATION_CONTAINERS = frozenset({
    "declaration",
    "declaration_list",
    "lexical_declaration",
    "local_declaration_statement",
    "local_variable_declaration",
    "var_declaration",
    "variable_declaration",
})
_STRUCTURAL_CONTAINERS = frozenset({
    "block",
    "body_statement",
    "class_body",
    "class_declaration",
    "class_definition",
    "class_declaration_list",
    "compilation_unit",
    "compound_statement",
    "function_body",
    "interface_declaration",
    "module",
    "namespace_declaration",
    "program",
    "source_file",
    "statement_block",
    "statement_list",
    "statements",
    "struct_declaration",
})


def _adapter(
    identifiers: set[str],
    functions: set[str],
    blocks: set[str],
    declarations: set[str],
    assignments: set[str],
    calls: set[str] | None = None,
    function_scoped_assignments: bool = False,
) -> _LanguageAdapter:
    return _LanguageAdapter(
        frozenset(identifiers),
        frozenset(functions),
        frozenset(blocks),
        frozenset(declarations),
        frozenset(assignments | _UPDATE_TYPES),
        _COMMON_CONTROLS,
        frozenset(calls or _COMMON_CALLS),
        function_scoped_assignments,
    )


_ADAPTERS = {
    "python": _adapter(
        {"identifier"},
        {"function_definition", "lambda"},
        {"class_definition"},
        set(),
        {"assignment", "augmented_assignment", "named_expression"},
        function_scoped_assignments=True,
    ),
    "javascript": _adapter(
        {"identifier"},
        {
            "arrow_function", "function_declaration", "function_expression",
            "generator_function_declaration", "method_definition",
        },
        {"class_body", "statement_block"},
        {"variable_declarator"},
        {"assignment_expression"},
    ),
    "typescript": _adapter(
        {"identifier"},
        {
            "arrow_function", "function_declaration", "function_expression",
            "generator_function_declaration", "method_definition",
        },
        {"class_body", "statement_block"},
        {"variable_declarator"},
        {"assignment_expression"},
    ),
    "java": _adapter(
        {"identifier"},
        {"constructor_declaration", "lambda_expression", "method_declaration"},
        {"block", "class_body"},
        {"variable_declarator"},
        {"assignment_expression"},
    ),
    "csharp": _adapter(
        {"identifier"},
        {
            "anonymous_method_expression", "constructor_declaration",
            "lambda_expression", "local_function_statement", "method_declaration",
        },
        {"block", "declaration_list"},
        {"variable_declarator"},
        {"assignment_expression"},
    ),
    "c": _adapter(
        {"identifier"},
        {"function_definition"},
        {"compound_statement"},
        {"init_declarator"},
        {"assignment_expression"},
    ),
    "cpp": _adapter(
        {"identifier"},
        {"function_definition", "lambda_expression"},
        {"compound_statement", "field_declaration_list"},
        {"init_declarator"},
        {"assignment_expression"},
    ),
    "go": _adapter(
        {"identifier"},
        {"func_literal", "function_declaration", "method_declaration"},
        {"block"},
        {"short_var_declaration", "var_spec"},
        {"assignment_statement"},
    ),
    "rust": _adapter(
        {"identifier"},
        {"closure_expression", "function_item"},
        {"block"},
        {"let_declaration"},
        {"assignment_expression"},
    ),
    "ruby": _adapter(
        {"identifier"},
        {"method", "singleton_method"},
        {"class", "module"},
        set(),
        {"assignment", "operator_assignment"},
        function_scoped_assignments=True,
    ),
    "php": _adapter(
        {"variable_name"},
        {"anonymous_function", "arrow_function", "function_definition", "method_declaration"},
        {"class_declaration_list"},
        set(),
        {"assignment_expression", "augmented_assignment_expression"},
        function_scoped_assignments=True,
    ),
    "swift": _adapter(
        {"simple_identifier"},
        {"function_declaration", "initializer_declaration", "lambda_literal"},
        {"class_body", "function_body"},
        {"property_declaration"},
        {"assignment"},
    ),
    "kotlin": _adapter(
        {"simple_identifier"},
        {"anonymous_function", "function_declaration"},
        {"class_body", "function_body"},
        {"property_declaration"},
        {"assignment"},
    ),
}


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _unwrap_target(node: Node | None, adapter: _LanguageAdapter) -> Node | None:
    while (
        node is not None
        and node.type in _TARGET_WRAPPER_TYPES
        and len(node.named_children) == 1
    ):
        node = node.named_children[0]
    return node if node is not None and node.type in adapter.identifier_types else None


def _target_names(node: Node | None, adapter: _LanguageAdapter) -> list[str]:
    if node is None:
        return []
    return list(dict.fromkeys(
        candidate.text.decode("utf-8")
        for candidate in _walk(node)
        if candidate.type in adapter.identifier_types
    ))


def _node_operator(node: Node) -> str:
    operator = node.child_by_field_name("operator")
    if operator is not None:
        return operator.text.decode("utf-8")
    return next(
        (
            child.text.decode("utf-8")
            for child in node.children
            if not child.is_named
            and child.text.decode("utf-8") in _ASSIGNMENT_OPERATORS
        ),
        "",
    )


def _initializer(node: Node) -> Node | None:
    for field_name in ("value", "right", "initializer"):
        value = node.child_by_field_name(field_name)
        if value is not None:
            if value.type == "expression_list" and len(value.named_children) == 1:
                return value.named_children[0]
            return value

    for child in node.named_children:
        if child.type in {"equals_value_clause", "initializer_expression"}:
            value = child.child_by_field_name("value")
            if value is not None:
                return value
            if child.named_children:
                return child.named_children[-1]
    if node.type == "variable_declarator" and len(node.named_children) > 1:
        return node.named_children[-1]
    return None


def _declaration_target(node: Node, adapter: _LanguageAdapter) -> Node | None:
    name = node.child_by_field_name("name")
    if name is not None:
        simple = _unwrap_target(name, adapter)
        if simple is not None:
            return simple

    if node.type == "init_declarator":
        declarator = node.child_by_field_name("declarator")
        return _unwrap_target(declarator, adapter)

    value = _initializer(node)
    for child in node.named_children:
        if child is value:
            break
        simple = _unwrap_target(child, adapter)
        if simple is not None:
            return simple

    identifiers = _target_names(node, adapter)
    if len(identifiers) == 1:
        return next(
            candidate
            for candidate in _walk(node)
            if candidate.type in adapter.identifier_types
            and candidate.text.decode("utf-8") == identifiers[0]
        )
    return None


def _declaration_kind(node: Node, language: str) -> tuple[bool, bool]:
    """Return (function_scoped, immutable) for the declaration."""
    if language in {"javascript", "typescript"}:
        ancestor = node.parent
        while ancestor is not None and ancestor.type not in {
            "lexical_declaration",
            "variable_declaration",
        }:
            ancestor = ancestor.parent
        if ancestor is not None:
            kind = ancestor.type
            if kind == "variable_declaration":
                return True, False
            return False, ancestor.text.decode("utf-8").lstrip().startswith("const ")

    if node.type == "let_declaration" and language == "rust":
        mutable = any(child.type == "mutable_specifier" for child in node.named_children)
        return False, not mutable
    if node.type == "property_declaration" and language == "swift":
        binding = next(
            (child.text.decode("utf-8") for child in node.named_children
             if child.type == "value_binding_pattern"),
            "",
        )
        return False, binding == "let"
    if node.type == "property_declaration" and language == "kotlin":
        binding = next(
            (child.text.decode("utf-8") for child in node.named_children
             if child.type == "binding_pattern_kind"),
            "",
        )
        return False, binding == "val"
    return False, False


@dataclass
class _Write:
    names: list[str]
    value: Node | None
    declaration: bool
    supported: bool
    function_scoped: bool = False
    immutable: bool = False


def _extract_write(node: Node, language: str, adapter: _LanguageAdapter) -> _Write | None:
    if node.type in adapter.declaration_types:
        target = _declaration_target(node, adapter)
        names = _target_names(target, adapter) if target is not None else _target_names(node, adapter)
        function_scoped, immutable = _declaration_kind(node, language)
        if node.type == "short_var_declaration":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            target = _unwrap_target(left, adapter)
            names = _target_names(left, adapter)
            value = (
                right.named_children[0]
                if right is not None and len(right.named_children) == 1
                else right
            )
            supported = target is not None and len(names) == 1
        else:
            value = _initializer(node)
            if node.type == "property_declaration" and language == "kotlin":
                target = next(
                    (
                        candidate
                        for child in node.named_children
                        if child.type == "variable_declaration"
                        for candidate in _walk(child)
                        if candidate.type in adapter.identifier_types
                    ),
                    None,
                )
                names = _target_names(target, adapter)
                value = next(
                    (
                        child for child in node.named_children
                        if child is not target
                        and child.type not in {"binding_pattern_kind", "variable_declaration"}
                    ),
                    None,
                )
            supported = target is not None and len(names) == 1
        if node.type == "var_spec" and value is not None and value.type in {
            "type_identifier",
            "type",
        }:
            value = None
        return _Write(names, value, True, supported, function_scoped, immutable)

    if node.type in adapter.assignment_types:
        left = node.child_by_field_name("left")
        if left is None and node.named_children:
            left = node.named_children[0]
        right = node.child_by_field_name("right")
        if right is None and len(node.named_children) >= 2:
            right = node.named_children[-1]
        operator = _node_operator(node)
        target = _unwrap_target(left, adapter)
        names = _target_names(left, adapter)
        is_declaration = node.type == "short_var_declaration"
        supported = (
            target is not None
            and len(names) == 1
            and operator in {"=", ":="}
        )
        return _Write(names, right, is_declaration, supported)

    if node.type in _UPDATE_TYPES:
        target = node.child_by_field_name("argument") or node.child_by_field_name("left")
        if target is None and node.named_children:
            target = node.named_children[0]
        return _Write(_target_names(target, adapter), None, False, False)
    return None


@dataclass
class _Scope:
    parent: _Scope | None
    is_function: bool = False
    values: dict[str, AbstractValue] = field(default_factory=dict)
    immutable: set[str] = field(default_factory=set)

    def nearest_function(self) -> _Scope | None:
        current: _Scope | None = self
        while current is not None:
            if current.is_function:
                return current
            current = current.parent
        return None

    def owner(self, name: str) -> _Scope | None:
        current: _Scope | None = self
        while current is not None:
            if name in current.values:
                return current
            if current.is_function:
                return None
            current = current.parent
        return None

    def lookup(self, name: str) -> AbstractValue:
        owner = self.owner(name)
        return owner.values[name] if owner is not None else UNKNOWN

    def bind(self, name: str, value: AbstractValue = UNKNOWN, immutable: bool = False) -> None:
        self.values[name] = value
        if immutable:
            self.immutable.add(name)
        else:
            self.immutable.discard(name)

    def assign(self, name: str, value: AbstractValue) -> None:
        owner = self.owner(name) or self
        owner.values[name] = UNKNOWN if name in owner.immutable else value

    def invalidate(self, names: list[str]) -> None:
        for name in names:
            owner = self.owner(name)
            if owner is not None:
                owner.values[name] = UNKNOWN

    def invalidate_visible(self) -> None:
        current: _Scope | None = self
        while current is not None:
            for name in current.values:
                current.values[name] = UNKNOWN
            if current.is_function:
                break
            current = current.parent


class _ValueTracker:
    def __init__(self, language: str, adapter: _LanguageAdapter) -> None:
        self.language = language
        self.adapter = adapter
        self.issues: list[Issue] = []
        self.reported: set[tuple[int, int]] = set()

    def analyze(self, root: Node) -> list[Issue]:
        root_scope = _Scope(parent=None)
        self._predeclare(root, root_scope)
        self._process_children(root, root_scope)
        return self.issues

    def _parameter_names(self, function: Node) -> list[str]:
        body = function.child_by_field_name("body")
        names: list[str] = []

        def visit(node: Node) -> None:
            if node is body:
                return
            if "parameter" in node.type.lower():
                names.extend(_target_names(node, self.adapter))
                return
            if node.type in self.adapter.function_types and node is not function:
                return
            for child in node.named_children:
                visit(child)

        visit(function)
        return list(dict.fromkeys(names))

    def _predeclare(self, node: Node, scope: _Scope) -> None:
        def visit(current: Node, is_root: bool = False) -> None:
            if not is_root and (
                current.type in self.adapter.function_types
                or current.type in self.adapter.block_types
                or current.type in self.adapter.control_types
            ):
                return
            write = _extract_write(current, self.language, self.adapter)
            if write is not None:
                should_predeclare = write.declaration or (
                    self.adapter.function_scoped_assignments
                    and current.type in self.adapter.assignment_types
                )
                target_scope = (
                    scope.nearest_function()
                    if write.function_scoped
                    else scope
                )
                if should_predeclare and target_scope is scope:
                    for name in write.names:
                        scope.bind(name, UNKNOWN, write.immutable)
            for child in current.named_children:
                visit(child)

        if scope.is_function:
            for name in self._parameter_names(node):
                scope.bind(name)
        visit(node, is_root=True)

    def _process_function(self, node: Node) -> None:
        function_scope = _Scope(parent=None, is_function=True)
        self._predeclare(node, function_scope)
        self._process_children(node, function_scope)

    def _process_block(self, node: Node, parent_scope: _Scope) -> None:
        block_scope = _Scope(parent=parent_scope)
        self._predeclare(node, block_scope)
        self._process_children(node, block_scope)

    def _contains_call(self, node: Node) -> bool:
        def visit(candidate: Node, is_root: bool = False) -> bool:
            if (
                not is_root
                and candidate.type in self.adapter.function_types
            ):
                return False
            if candidate.type in self.adapter.call_types:
                return True
            return any(visit(child) for child in candidate.named_children)

        return visit(node, is_root=True)

    def _contains_side_effect(self, node: Node) -> bool:
        def visit(candidate: Node, is_root: bool = False) -> bool:
            if (
                not is_root
                and candidate.type in self.adapter.function_types
            ):
                return False
            if (
                candidate.type in self.adapter.call_types
                or candidate.type in _SIDE_EFFECT_READ_TYPES
            ):
                return True
            return any(visit(child) for child in candidate.named_children)

        return visit(node, is_root=True)

    def _contains_write(self, node: Node) -> bool:
        def visit(candidate: Node, is_root: bool = False) -> bool:
            if (
                not is_root
                and candidate.type in self.adapter.function_types
            ):
                return False
            if (
                not is_root
                and _extract_write(candidate, self.language, self.adapter) is not None
            ):
                return True
            return any(visit(child) for child in candidate.named_children)

        return visit(node, is_root=True)

    def _has_side_effect_inside_division(self, node: Node) -> bool:
        def visit(candidate: Node, is_root: bool = False) -> bool:
            if not is_root and candidate.type in self.adapter.function_types:
                return False
            if not is_root and (
                candidate.type in self.adapter.call_types
                or candidate.type in _SIDE_EFFECT_READ_TYPES
                or _extract_write(candidate, self.language, self.adapter) is not None
            ):
                return True
            return any(visit(child) for child in candidate.named_children)

        return visit(node, is_root=True)

    def _simple_expression_name(self, node: Node | None) -> str | None:
        while (
            node is not None
            and node.type in {
                "expression_list",
                "parenthesized_expression",
                "parenthesized_statements",
                "tuple_expression",
            }
            and len(node.named_children) == 1
        ):
            node = node.named_children[0]
        if node is not None and node.type in self.adapter.identifier_types:
            return node.text.decode("utf-8")
        return None

    def _division_operator(self, node: Node) -> str | None:
        if len(node.named_children) < 2:
            return None
        operators = {
            child.text.decode("utf-8")
            for child in node.children
            if not child.is_named and child.text in {b"/", b"//", b"%"}
        }
        if self.language != "python":
            operators.discard("//")
        return next(iter(operators)) if len(operators) == 1 else None

    def _check_division(self, node: Node, scope: _Scope) -> None:
        operator = self._division_operator(node)
        if operator is None:
            return
        if self._has_side_effect_inside_division(node):
            scope.invalidate_visible()
            return
        divisor = node.child_by_field_name("right")
        if divisor is None:
            divisor = node.named_children[-1]
        name = self._simple_expression_name(divisor)
        if name is None or not is_known_zero(scope.lookup(name)):
            return
        key = (node.start_byte, node.end_byte)
        if key in self.reported:
            return
        self.reported.add(key)
        operation = "remainder" if operator == "%" else "division"
        self.issues.append(Issue(
            category=Category.quality,
            severity=Severity.warning,
            title="Division or remainder by variable known to be zero",
            description=(
                f"The divisor variable '{name}' is definitely zero at this "
                f"{operation} expression according to straight-line assignments."
            ),
            recommendation="Assign a nonzero value or guard the operation before using this divisor.",
            line=node.start_point.row + 1,
            column=node.start_point.column + 1,
            source="tree_sitter_value_tracking",
        ))

    def _process_write(self, node: Node, scope: _Scope) -> None:
        write = _extract_write(node, self.language, self.adapter)
        if write is None:
            return
        if not write.supported or len(write.names) != 1:
            if write.names:
                scope.invalidate(write.names)
            else:
                scope.invalidate_visible()
            return

        name = write.names[0]
        if write.value is not None and self._contains_side_effect(write.value):
            scope.invalidate_visible()
            value = UNKNOWN
        elif write.value is not None and self._contains_write(write.value):
            scope.invalidate_visible()
            value = UNKNOWN
        elif self._simple_expression_name(write.value) is not None:
            scope.invalidate_visible()
            value = UNKNOWN
        elif write.value is None:
            value = UNKNOWN
        else:
            self._process_node(write.value, scope)
            value = evaluate_numeric_expression(write.value, self.language)

        target_scope = (
            scope.nearest_function()
            if write.function_scoped
            else scope
        )
        if target_scope is None:
            target_scope = scope
        if write.declaration:
            target_scope.bind(name, value, write.immutable)
        else:
            target_scope.assign(name, value)

    def _process_children(self, node: Node, scope: _Scope) -> None:
        for child in node.named_children:
            self._process_node(child, scope)

    def _process_node(self, node: Node, scope: _Scope) -> None:
        if node.type in self.adapter.function_types:
            self._process_function(node)
            return

        if node.type in self.adapter.control_types:
            scope.invalidate_visible()
            return

        if node.type in self.adapter.block_types:
            self._process_block(node, scope)
            return

        if _extract_write(node, self.language, self.adapter) is not None:
            self._process_write(node, scope)
            return

        if self._division_operator(node) is not None:
            self._check_division(node, scope)
            if self._has_side_effect_inside_division(node):
                return

        if self._contains_side_effect(node):
            scope.invalidate_visible()

        nested_writes = [
            candidate
            for candidate in _walk(node)
            if candidate is not node
            and _extract_write(candidate, self.language, self.adapter) is not None
        ]
        if (
            nested_writes
            and node.type not in _DECLARATION_CONTAINERS
            and node.type not in _STRUCTURAL_CONTAINERS
        ):
            direct_expression_assignment = (
                node.type == "expression_statement"
                and len(node.named_children) == 1
                and _extract_write(
                    node.named_children[0],
                    self.language,
                    self.adapter,
                ) is not None
            )
            if not direct_expression_assignment:
                scope.invalidate_visible()
                return

        for child in node.named_children:
            self._process_node(child, scope)


def analyze_tree_sitter_value_tracking(language: str, code: str) -> list[Issue]:
    """Find divisions by variables known to be zero in straight-line code."""
    normalized = canonical_language(language)
    adapter = _ADAPTERS.get(normalized or "")
    if adapter is None:
        return []
    tree = _parse_source(normalized, code)
    if tree is None or tree.root_node.has_error:
        return []
    return _ValueTracker(normalized, adapter).analyze(tree.root_node)
