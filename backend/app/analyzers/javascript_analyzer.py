"""Conservative semantic checks for JavaScript source."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from app.analyzers.base import Category, Issue, Severity
from app.analyzers.language_analyzer import _parse_source


_FUNCTION_SCOPE_TYPES = {
    "arrow_function",
    "function_declaration",
    "function_expression",
    "generator_function",
    "generator_function_declaration",
    "method_definition",
}
_BLOCK_SCOPE_TYPES = {
    "catch_clause",
    "class_body",
    "for_in_statement",
    "for_of_statement",
    "for_statement",
    "statement_block",
    "switch_body",
}
_SCOPE_NODE_TYPES = _FUNCTION_SCOPE_TYPES | _BLOCK_SCOPE_TYPES | {
    "class_declaration",
    "class_expression",
}
_FUNCTION_TYPES = _FUNCTION_SCOPE_TYPES
_BINDING_CONTAINERS = {
    "array_pattern",
    "formal_parameters",
    "object_pattern",
    "rest_pattern",
}
_BUILTIN_GLOBALS = {
    "AggregateError",
    "AbortController",
    "Audio",
    "Array",
    "ArrayBuffer",
    "Atomics",
    "BigInt",
    "BigInt64Array",
    "BigUint64Array",
    "Blob",
    "Boolean",
    "Buffer",
    "CustomEvent",
    "DataView",
    "Date",
    "DOMParser",
    "Element",
    "Error",
    "EvalError",
    "Event",
    "EventTarget",
    "File",
    "FileList",
    "FileReader",
    "FinalizationRegistry",
    "Float32Array",
    "Float64Array",
    "FormData",
    "Function",
    "Headers",
    "HTMLElement",
    "Infinity",
    "Int8Array",
    "Int16Array",
    "Int32Array",
    "IntersectionObserver",
    "Intl",
    "Image",
    "JSON",
    "KeyboardEvent",
    "Map",
    "Math",
    "MouseEvent",
    "MutationObserver",
    "NaN",
    "Node",
    "NodeList",
    "Number",
    "Object",
    "Performance",
    "Promise",
    "Proxy",
    "RangeError",
    "ReferenceError",
    "Reflect",
    "RegExp",
    "ResizeObserver",
    "Set",
    "SharedArrayBuffer",
    "String",
    "Symbol",
    "SyntaxError",
    "TextDecoder",
    "TextEncoder",
    "TypeError",
    "Uint8Array",
    "Uint8ClampedArray",
    "Uint16Array",
    "Uint32Array",
    "URIError",
    "URL",
    "URLSearchParams",
    "WeakMap",
    "WeakRef",
    "WeakSet",
    "WebAssembly",
    "WebSocket",
    "Worker",
    "XMLHttpRequest",
    "__dirname",
    "__filename",
    "alert",
    "arguments",
    "atob",
    "btoa",
    "clearImmediate",
    "clearInterval",
    "clearTimeout",
    "confirm",
    "console",
    "crypto",
    "decodeURI",
    "decodeURIComponent",
    "document",
    "event",
    "encodeURI",
    "encodeURIComponent",
    "escape",
    "eval",
    "exports",
    "fetch",
    "global",
    "globalThis",
    "history",
    "isFinite",
    "isNaN",
    "localStorage",
    "location",
    "module",
    "navigator",
    "parseFloat",
    "parseInt",
    "performance",
    "process",
    "prompt",
    "queueMicrotask",
    "requestAnimationFrame",
    "cancelAnimationFrame",
    "require",
    "setImmediate",
    "setInterval",
    "setTimeout",
    "sessionStorage",
    "structuredClone",
    "undefined",
    "unescape",
    "window",
}
_AGGREGATE_NAMES = {"accumulator", "subtotal", "sum", "total"}


@dataclass
class _Scope:
    parent: _Scope | None
    function_scope: bool = False
    names: set[str] = field(default_factory=set)

    def nearest_function(self) -> _Scope:
        scope = self
        while not scope.function_scope and scope.parent is not None:
            scope = scope.parent
        return scope

    def resolves(self, name: str) -> bool:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.names:
                return True
            scope = scope.parent
        return False


def _key(node: Node) -> tuple[str, int, int]:
    return node.type, node.start_byte, node.end_byte


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8") if node is not None else ""


def _binding_nodes(node: Node | None) -> list[Node]:
    if node is None:
        return []
    if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
        return [node]
    if node.type == "pair_pattern":
        return _binding_nodes(node.child_by_field_name("value"))
    if node.type in {"assignment_pattern", "object_assignment_pattern"}:
        return _binding_nodes(node.child_by_field_name("left"))
    if node.type in _BINDING_CONTAINERS:
        return [
            binding
            for child in node.named_children
            for binding in _binding_nodes(child)
        ]
    return []


def _declare(scope: _Scope, node: Node | None, binding_nodes: set[tuple[str, int, int]]) -> None:
    for binding in _binding_nodes(node):
        scope.names.add(_text(binding))
        binding_nodes.add(_key(binding))


def _import_bindings(node: Node, scope: _Scope, binding_nodes: set[tuple[str, int, int]]) -> None:
    clause = next((child for child in node.named_children if child.type == "import_clause"), None)
    if clause is None:
        return
    for child in clause.named_children:
        if child.type == "identifier":
            _declare(scope, child, binding_nodes)
        elif child.type == "named_imports":
            for specifier in child.named_children:
                if specifier.type != "import_specifier":
                    continue
                imported_name = specifier.child_by_field_name("name")
                local_name = (
                    specifier.child_by_field_name("alias")
                    or imported_name
                )
                _declare(scope, local_name, binding_nodes)
                if imported_name is not None and local_name is not imported_name:
                    binding_nodes.add(_key(imported_name))
        elif child.type == "namespace_import":
            identifiers = [
                candidate
                for candidate in child.named_children
                if candidate.type == "identifier"
            ]
            if identifiers:
                _declare(scope, identifiers[-1], binding_nodes)


def _build_scopes(
    root: Node,
) -> tuple[_Scope, dict[tuple[str, int, int], _Scope], set[tuple[str, int, int]]]:
    root_scope = _Scope(parent=None, function_scope=True, names=set(_BUILTIN_GLOBALS))
    node_scopes: dict[tuple[str, int, int], _Scope] = {}
    binding_nodes: set[tuple[str, int, int]] = set()

    def visit(node: Node, inherited_scope: _Scope) -> None:
        scope = inherited_scope
        if node.type in _SCOPE_NODE_TYPES:
            scope = _Scope(
                parent=inherited_scope,
                function_scope=node.type in _FUNCTION_SCOPE_TYPES,
            )
            if node.type in _FUNCTION_SCOPE_TYPES and node.type != "arrow_function":
                scope.names.add("arguments")
        node_scopes[_key(node)] = scope

        if node.type in {"function_declaration", "class_declaration"}:
            _declare(inherited_scope, node.child_by_field_name("name"), binding_nodes)
        elif node.type in {"function_expression", "class_expression"}:
            _declare(scope, node.child_by_field_name("name"), binding_nodes)
        elif node.type == "variable_declarator":
            kind = _declaration_kind(node)
            target_scope = scope.nearest_function() if kind == "var" else scope
            _declare(target_scope, node.child_by_field_name("name"), binding_nodes)
        elif node.type in _FUNCTION_TYPES:
            _declare(scope, node.child_by_field_name("parameters"), binding_nodes)
        elif node.type == "catch_clause":
            _declare(scope, node.child_by_field_name("parameter"), binding_nodes)
        elif node.type == "import_statement":
            _import_bindings(node, scope, binding_nodes)
        elif node.type == "export_specifier":
            # The alias is an exported name, not a local variable reference.
            alias = node.child_by_field_name("alias")
            if alias is not None:
                binding_nodes.add(_key(alias))
            elif node.parent and node.parent.parent:
                export_statement = node.parent.parent
                if export_statement.type == "export_statement" and export_statement.child_by_field_name("source"):
                    binding_nodes.add(_key(node.child_by_field_name("name")))

        if node.type in _FUNCTION_TYPES:
            _declare(scope, node.child_by_field_name("parameters"), binding_nodes)

        for child in node.named_children:
            visit(child, scope)

    visit(root, root_scope)
    return root_scope, node_scopes, binding_nodes


def _ancestors(node: Node):
    current: Node | None = node
    while current is not None:
        yield current
        current = current.parent


def _descendants(node: Node, *, skip_functions: bool = False):
    stack = list(reversed(node.named_children))
    while stack:
        current = stack.pop()
        if skip_functions and current.type in _FUNCTION_TYPES:
            continue
        yield current
        stack.extend(reversed(current.named_children))


def _issue(
    *,
    title: str,
    description: str,
    recommendation: str,
    line: int,
    severity: Severity = Severity.warning,
) -> Issue:
    return Issue(
        category=Category.quality,
        severity=severity,
        title=title,
        description=description,
        recommendation=recommendation,
        line=line,
        source="javascript_semantic",
    )


def _check_undefined_names(
    root: Node,
    node_scopes: dict[tuple[str, int, int], _Scope],
    binding_nodes: set[tuple[str, int, int]],
) -> list[Issue]:
    issues = []
    for node in _descendants(root):
        if node.type not in {"identifier", "shorthand_property_identifier"}:
            continue
        if _key(node) in binding_nodes:
            continue
        if any(ancestor.type == "with_statement" for ancestor in _ancestors(node)):
            continue
        scope = node_scopes.get(_key(node))
        name = _text(node)
        if scope is None or scope.resolves(name):
            continue
        if node.parent and node.parent.type == "unary_expression":
            operator = next(
                (child for child in node.parent.children if not child.is_named),
                None,
            )
            if _text(operator) == "typeof":
                continue
        issues.append(_issue(
            title=f"Likely undefined variable: {name}",
            description=(
                f"'{name}' is referenced without a visible declaration, import, parameter, "
                "or recognized JavaScript global."
            ),
            recommendation=f"Declare or import '{name}', or correct the identifier name.",
            line=node.start_point.row + 1,
            severity=Severity.error,
        ))
    return issues


def _assignment_operator(node: Node) -> str:
    field_operator = node.child_by_field_name("operator")
    if field_operator is not None:
        return _text(field_operator)
    return next(
        (
            _text(child)
            for child in node.children
            if not child.is_named and _text(child) in {"=", "+=", "-=", "*=", "/=", "%="}
        ),
        "",
    )


def _check_assignment_conditions(root: Node) -> list[Issue]:
    issues = []
    for statement in _descendants(root):
        if statement.type not in {"if_statement", "while_statement", "do_statement"}:
            continue
        condition = statement.child_by_field_name("condition")
        if condition is None:
            continue
        assignment = next(
            (
                node
                for node in _descendants(condition)
                if node.type == "assignment_expression"
                and _assignment_operator(node) == "="
            ),
            None,
        )
        if assignment is not None:
            issues.append(_issue(
                title="Assignment used as a condition",
                description=(
                    "This condition assigns a value instead of comparing values, so its "
                    "result may control the branch unexpectedly."
                ),
                recommendation=(
                    "Use an explicit comparison operator, or parenthesize and document the "
                    "assignment if it is intentional."
                ),
                line=assignment.start_point.row + 1,
            ))
    return issues


def _loop_index_and_array(loop: Node) -> tuple[str, str] | None:
    condition = loop.child_by_field_name("condition")
    update = loop.child_by_field_name("increment")
    if condition is None or update is None or condition.type != "binary_expression":
        return None
    if _text(condition.child_by_field_name("operator")) not in {"<", "<="}:
        return None
    index = condition.child_by_field_name("left")
    bound = condition.child_by_field_name("right")
    if index is None or index.type != "identifier" or bound is None:
        return None
    if bound.type != "member_expression":
        return None
    if _text(bound.child_by_field_name("property")) != "length":
        return None
    array = bound.child_by_field_name("object")
    if array is None or array.type != "identifier":
        return None
    if update.type != "update_expression":
        return None
    update_index = update.child_by_field_name("argument")
    if update_index is None or _text(update_index) != _text(index):
        return None
    if _text(update.child_by_field_name("operator")) != "++":
        return None
    return _text(index), _text(array)


def _check_loop_boundaries(root: Node) -> list[Issue]:
    issues = []
    for loop in _descendants(root):
        if loop.type != "for_statement":
            continue
        condition = loop.child_by_field_name("condition")
        loop_values = _loop_index_and_array(loop)
        if (
            loop_values is None
            or condition is None
            or _text(condition.child_by_field_name("operator")) != "<="
        ):
            continue
        index, array = loop_values
        issues.append(_issue(
            title="Loop may access past the end of an array",
            description=(
                f"The loop uses '{index} <= {array}.length' while incrementing '{index}', "
                "which permits an iteration at index array.length."
            ),
            recommendation=(
                f"Use '{index} < {array}.length' for zero-based array iteration, or adjust "
                "the bound if the extra iteration is intentional."
            ),
            line=condition.start_point.row + 1,
        ))
    return issues


def _declaration_kind(node: Node) -> str:
    declaration = node.parent
    while declaration is not None and declaration.type not in {
        "lexical_declaration",
        "variable_declaration",
    }:
        declaration = declaration.parent
    if declaration is None:
        return ""
    if declaration.type == "variable_declaration":
        return "var"
    return _text(declaration.child_by_field_name("kind"))


def _is_zero_literal(node: Node | None) -> bool:
    return node is not None and node.type == "number" and _text(node).replace("_", "") in {
        "0",
        "0.0",
    }


def _has_prior_zero_aggregate(
    name: str,
    loop: Node,
    loop_scope: _Scope,
    zero_aggregates: list[tuple[str, _Scope, int]],
) -> bool:
    resolving_scope: _Scope | None = loop_scope
    while resolving_scope is not None and name not in resolving_scope.names:
        resolving_scope = resolving_scope.parent
    if resolving_scope is None:
        return False

    return any(
        aggregate_name == name
        and aggregate_scope is resolving_scope
        and declaration_start < loop.start_byte
        for aggregate_name, aggregate_scope, declaration_start in zero_aggregates
    )


def _check_accumulation_assignments(
    root: Node,
    node_scopes: dict[tuple[str, int, int], _Scope],
) -> list[Issue]:
    issues = []
    zero_aggregates: list[tuple[str, _Scope, int]] = []
    for declaration in _descendants(root):
        if declaration.type != "variable_declarator":
            continue
        binding = declaration.child_by_field_name("name")
        value = declaration.child_by_field_name("value")
        declaration_kind = _declaration_kind(declaration)
        scope = node_scopes.get(_key(declaration))
        if (
            binding is None
            or binding.type != "identifier"
            or _text(binding).lower() not in _AGGREGATE_NAMES
            or declaration_kind not in {"let", "var"}
            or not _is_zero_literal(value)
            or scope is None
        ):
            continue
        if declaration_kind == "var":
            scope = scope.nearest_function()
        zero_aggregates.append((_text(binding), scope, declaration.start_byte))

    for loop in _descendants(root):
        if loop.type != "for_statement":
            continue
        loop_values = _loop_index_and_array(loop)
        body = loop.child_by_field_name("body")
        if loop_values is None or body is None:
            continue
        index, array = loop_values
        loop_scope = node_scopes.get(_key(loop))
        if loop_scope is None:
            continue
        for assignment in _descendants(body, skip_functions=True):
            if (
                assignment.type != "assignment_expression"
                or _assignment_operator(assignment) != "="
            ):
                continue
            target = assignment.child_by_field_name("left")
            value = assignment.child_by_field_name("right")
            if (
                target is None
                or target.type != "identifier"
                or _text(target).lower() not in _AGGREGATE_NAMES
                or value is None
                or value.type != "subscript_expression"
            ):
                continue
            subscript_array = value.child_by_field_name("object")
            subscript_index = value.child_by_field_name("index")
            name = _text(target)
            if (
                subscript_array is None
                or subscript_array.type != "identifier"
                or _text(subscript_array) != array
                or subscript_index is None
                or subscript_index.type != "identifier"
                or _text(subscript_index) != index
                or not _has_prior_zero_aggregate(
                    name,
                    loop,
                    loop_scope,
                    zero_aggregates,
                )
            ):
                continue
            issues.append(_issue(
                title=f"Possible overwritten accumulator: {name}",
                description=(
                    f"'{name}' is initialized to zero and then overwritten with "
                    f"{array}[{index}] inside the loop instead of retaining the prior total."
                ),
                recommendation=(
                    f"If '{name}' is intended to accumulate values, use "
                    f"'{name} += {array}[{index}]'."
                ),
                line=assignment.start_point.row + 1,
            ))
    return issues


def _check_null_property_access(root: Node) -> list[Issue]:
    issues = []
    for access in _descendants(root):
        if access.type not in {"member_expression", "subscript_expression"}:
            continue
        if access.child_by_field_name("optional_chain") is not None:
            continue
        object_node = access.child_by_field_name("object")
        if object_node is None or object_node.type != "null":
            continue
        issues.append(_issue(
            title="Property access on null",
            description=(
                "This access uses a null literal as the object and will throw a "
                "TypeError when evaluated."
            ),
            recommendation=(
                "Check that the value is non-null before accessing its property, or use "
                "optional chaining if that matches the intended behavior."
            ),
            line=access.start_point.row + 1,
            severity=Severity.error,
        ))
    return issues


def _unwrap_parentheses(node: Node | None) -> Node | None:
    while node is not None and node.type == "parenthesized_expression":
        if not node.named_children:
            return None
        node = node.named_children[0]
    return node


def _constant_integer(node: Node | None) -> int | None:
    node = _unwrap_parentheses(node)
    if node is None:
        return None
    if node.type == "unary_expression":
        argument = node.child_by_field_name("argument")
        operator = next(
            (child for child in node.children if not child.is_named),
            None,
        )
        value = _constant_integer(argument)
        if value is not None and _text(operator) in {"-", "+"}:
            return -value if _text(operator) == "-" else value
        return None
    if node.type != "number":
        return None
    raw = _text(node).replace("_", "")
    try:
        if raw.lower().startswith(("0x", "0b", "0o")):
            return int(raw, 0)
        if raw.isdigit():
            return int(raw)
    except ValueError:
        pass
    return None


def _string_literal_length(node: Node | None) -> int | None:
    node = _unwrap_parentheses(node)
    if node is None or node.type != "string":
        return None
    raw = _text(node)
    if len(raw) < 2 or raw[0] not in {"'", '"'} or raw[-1] != raw[0]:
        return None
    content = raw[1:-1]
    if "\\" in content or "\n" in content or "\r" in content:
        return None
    try:
        return len(content.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return None


def _known_const_string_lengths(
    root: Node,
    node_scopes: dict[tuple[str, int, int], _Scope],
) -> list[tuple[str, _Scope, int, int]]:
    known = []
    for declaration in _descendants(root):
        if (
            declaration.type != "variable_declarator"
            or _declaration_kind(declaration) != "const"
        ):
            continue
        name = declaration.child_by_field_name("name")
        length = _string_literal_length(declaration.child_by_field_name("value"))
        scope = node_scopes.get(_key(declaration))
        if name is not None and name.type == "identifier" and length is not None and scope:
            known.append((_text(name), scope, declaration.start_byte, length))
    return known


def _visible_string_length(
    expression: Node | None,
    node_scopes: dict[tuple[str, int, int], _Scope],
    known_strings: list[tuple[str, _Scope, int, int]],
) -> int | None:
    expression = _unwrap_parentheses(expression)
    direct_length = _string_literal_length(expression)
    if direct_length is not None:
        return direct_length
    if expression is None or expression.type != "identifier":
        return None
    scope: _Scope | None = node_scopes.get(_key(expression))
    name = _text(expression)
    while scope is not None and name not in scope.names:
        scope = scope.parent
    if scope is None:
        return None
    return next(
        (
            length
            for known_name, known_scope, declaration_start, length in known_strings
            if known_name == name
            and known_scope is scope
            and declaration_start < expression.start_byte
        ),
        None,
    )


def _string_index_issue(index_node: Node, length: int, index: int) -> Issue:
    return _issue(
        title="String index may be out of range",
        description=(
            f"Index {index} is outside the visible string's valid range "
            f"0 through {length - 1}."
        ),
        recommendation=(
            "Check the string length before indexing or calling charAt, and use an index "
            "less than the string's UTF-16 length."
        ),
        line=index_node.start_point.row + 1,
    )


def _check_string_indexes(
    root: Node,
    node_scopes: dict[tuple[str, int, int], _Scope],
) -> list[Issue]:
    known_strings = _known_const_string_lengths(root, node_scopes)
    issues = []
    for node in _descendants(root):
        if node.type == "subscript_expression":
            index_node = node.child_by_field_name("index")
            length = _visible_string_length(
                node.child_by_field_name("object"),
                node_scopes,
                known_strings,
            )
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if (
                function is None
                or function.type != "member_expression"
                or _text(function.child_by_field_name("property")) != "charAt"
                or arguments is None
                or len(arguments.named_children) != 1
            ):
                continue
            index_node = arguments.named_children[0]
            length = _visible_string_length(
                function.child_by_field_name("object"),
                node_scopes,
                known_strings,
            )
        else:
            continue

        index = _constant_integer(index_node)
        if length is not None and index is not None and (index < 0 or index >= length):
            issues.append(_string_index_issue(index_node, length, index))
    return issues


def _is_array_length(node: Node | None, array_name: str) -> bool:
    node = _unwrap_parentheses(node)
    if node is None or node.type != "member_expression":
        return False
    if node.child_by_field_name("optional_chain") is not None:
        return False
    object_node = node.child_by_field_name("object")
    return (
        object_node is not None
        and object_node.type == "identifier"
        and _text(object_node) == array_name
        and _text(node.child_by_field_name("property")) == "length"
    )


def _is_array_sum_reduce(node: Node | None, array_name: str) -> bool:
    node = _unwrap_parentheses(node)
    if node is None or node.type != "call_expression":
        return False
    function = node.child_by_field_name("function")
    if function is None or function.type != "member_expression":
        return False
    receiver = function.child_by_field_name("object")
    if (
        receiver is None
        or receiver.type != "identifier"
        or _text(receiver) != array_name
        or _text(function.child_by_field_name("property")) != "reduce"
    ):
        return False
    arguments = node.child_by_field_name("arguments")
    if arguments is None or not arguments.named_children:
        return False
    callback = arguments.named_children[0]
    if callback.type not in {"arrow_function", "function_expression"}:
        return False
    parameters = _binding_nodes(callback.child_by_field_name("parameters"))
    if len(parameters) < 2 or any(parameter.type != "identifier" for parameter in parameters[:2]):
        return False
    accumulator, item = (_text(parameter) for parameter in parameters[:2])
    body = callback.child_by_field_name("body")
    if body is None:
        return False
    return any(
        expression.type == "binary_expression"
        and _text(expression.child_by_field_name("operator")) == "+"
        and {
            _text(expression.child_by_field_name("left")),
            _text(expression.child_by_field_name("right")),
        } == {accumulator, item}
        for expression in (body, *_descendants(body))
    )


def _is_array_sum_expression(
    node: Node | None,
    array_name: str,
    root: Node,
    node_scopes: dict[tuple[str, int, int], _Scope],
) -> bool:
    node = _unwrap_parentheses(node)
    if _is_array_sum_reduce(node, array_name):
        return True
    if node is None or node.type != "identifier":
        return False
    name = _text(node)
    scope: _Scope | None = node_scopes.get(_key(node))
    while scope is not None and name not in scope.names:
        scope = scope.parent
    if scope is None:
        return False
    return any(
        declaration.type == "variable_declarator"
        and _declaration_kind(declaration) == "const"
        and declaration.start_byte < node.start_byte
        and node_scopes.get(_key(declaration)) is scope
        and declaration.child_by_field_name("name") is not None
        and _text(declaration.child_by_field_name("name")) == name
        and _is_array_sum_reduce(
            declaration.child_by_field_name("value"),
            array_name,
        )
        for declaration in _descendants(root)
    )


def _length_guard_status(condition: Node | None, array_name: str) -> str | None:
    condition = _unwrap_parentheses(condition)
    if condition is None:
        return None
    if condition.type == "unary_expression":
        argument = condition.child_by_field_name("argument")
        operator = next(
            (child for child in condition.children if not child.is_named),
            None,
        )
        if _text(operator) == "!" and _is_array_length(argument, array_name):
            return "empty"
        return None
    if condition.type != "binary_expression":
        return None

    left = _unwrap_parentheses(condition.child_by_field_name("left"))
    right = _unwrap_parentheses(condition.child_by_field_name("right"))
    operator = _text(condition.child_by_field_name("operator"))
    if not _is_array_length(left, array_name):
        return None
    value = _constant_integer(right)
    if value is None:
        return None
    if operator in {"==", "==="} and value == 0:
        return "empty"
    if operator == "<" and value == 1:
        return "empty"
    if operator == "<=" and value == 0:
        return "empty"
    if operator in {"!=", "!==", ">"} and value == 0:
        return "nonempty"
    if operator == ">=" and value == 1:
        return "nonempty"
    return None


def _direct_statements(branch: Node) -> list[Node]:
    return list(branch.named_children) if branch.type == "statement_block" else [branch]


def _is_stopping_statement(statement: Node) -> bool:
    if statement.type in {"return_statement", "throw_statement"}:
        return True
    if statement.type != "expression_statement":
        return False
    expression = statement.named_children[0] if statement.named_children else None
    if expression is None or expression.type != "call_expression":
        return False
    function = expression.child_by_field_name("function")
    return _text(function) in {"process.exit", "Deno.exit"}


def _branch_stops(branch: Node) -> bool:
    return any(_is_stopping_statement(statement) for statement in _direct_statements(branch))


def _branch_stops_before(branch: Node, target: Node) -> bool:
    for statement in _direct_statements(branch):
        if statement.start_byte >= target.start_byte:
            break
        if _is_stopping_statement(statement):
            return True
    return False


def _node_contains(ancestor: Node, target: Node) -> bool:
    current: Node | None = target
    while current is not None:
        if _key(current) == _key(ancestor):
            return True
        current = current.parent
    return False


def _has_empty_array_guard(
    expression: Node,
    array_name: str,
    node_scopes: dict[tuple[str, int, int], _Scope],
    root: Node,
) -> bool:
    for ancestor in _ancestors(expression):
        if ancestor.type != "if_statement":
            continue
        status = _length_guard_status(
            ancestor.child_by_field_name("condition"),
            array_name,
        )
        consequence = ancestor.child_by_field_name("consequence")
        alternative = ancestor.child_by_field_name("alternative")
        if (
            status == "nonempty"
            and consequence is not None
            and _node_contains(consequence, expression)
        ):
            return True
        if (
            status == "empty"
            and alternative is not None
            and _node_contains(alternative, expression)
        ):
            return True
        if (
            status == "empty"
            and consequence is not None
            and _node_contains(consequence, expression)
            and _branch_stops_before(consequence, expression)
        ):
            return True

    expression_scope = node_scopes.get(_key(expression))
    if expression_scope is None:
        return False
    for guard in _descendants(root):
        if (
            guard.type != "if_statement"
            or guard.end_byte > expression.start_byte
            or node_scopes.get(_key(guard)) is not expression_scope
            or _length_guard_status(
                guard.child_by_field_name("condition"),
                array_name,
            ) != "empty"
        ):
            continue
        consequence = guard.child_by_field_name("consequence")
        if consequence is not None and _branch_stops(consequence):
            return True
    return False


def _check_average_division(
    root: Node,
    node_scopes: dict[tuple[str, int, int], _Scope],
) -> list[Issue]:
    issues = []
    for division in _descendants(root):
        if (
            division.type != "binary_expression"
            or _text(division.child_by_field_name("operator")) != "/"
        ):
            continue
        denominator = _unwrap_parentheses(division.child_by_field_name("right"))
        if denominator is None or denominator.type != "member_expression":
            continue
        array = denominator.child_by_field_name("object")
        if (
            array is None
            or array.type != "identifier"
            or not _is_array_length(denominator, _text(array))
            or not _is_array_sum_expression(
                division.child_by_field_name("left"),
                _text(array),
                root,
                node_scopes,
            )
            or _has_empty_array_guard(
                division,
                _text(array),
                node_scopes,
                root,
            )
        ):
            continue
        issues.append(_issue(
            title="Average may divide by zero for an empty array",
            description=(
                f"The sum of '{_text(array)}' is divided by its length without an evident "
                "guard for the empty-array case."
            ),
            recommendation=(
                f"Handle '{_text(array)}.length === 0' before computing the average, or only "
                "evaluate this expression when the array is nonempty."
            ),
            line=division.start_point.row + 1,
        ))
    return issues


def _has_balance_name(node: Node | None) -> bool:
    if node is None:
        return False
    candidates = (node, *_descendants(node))
    return any(
        candidate.type in {"identifier", "property_identifier"}
        and _text(candidate).lower().endswith("balance")
        for candidate in candidates
    )


def _insufficient_balance_operands(condition: Node | None) -> tuple[Node, Node] | None:
    condition = _unwrap_parentheses(condition)
    if condition is None or condition.type != "binary_expression":
        return None
    operator = _text(condition.child_by_field_name("operator"))
    left = _unwrap_parentheses(condition.child_by_field_name("left"))
    right = _unwrap_parentheses(condition.child_by_field_name("right"))
    if left is None or right is None:
        return None
    if operator in {"<", "<="} and _has_balance_name(left) and not _has_balance_name(right):
        balance, amount = left, right
    elif operator in {">", ">="} and _has_balance_name(right) and not _has_balance_name(left):
        balance, amount = right, left
    else:
        return None
    if not any(node.type == "identifier" for node in (amount, *_descendants(amount))):
        return None
    return balance, amount


_INSUFFICIENT_MESSAGE = re.compile(
    r"insufficient[\W_]*(?:balance|funds?)|(?:balance|funds?)[\W_]*insufficient",
    re.IGNORECASE,
)


def _handles_insufficient_balance(branch: Node) -> bool:
    return bool(_INSUFFICIENT_MESSAGE.search(_text(branch)))


def _balance_deduction(
    assignment: Node,
    balance: Node,
    amount: Node,
) -> bool:
    target = assignment.child_by_field_name("left")
    value = assignment.child_by_field_name("right")
    if target is None or value is None or _text(target) != _text(balance):
        return False
    operator = _assignment_operator(assignment)
    if assignment.type == "augmented_assignment_expression":
        return operator == "-=" and _text(value) == _text(amount)
    if (
        assignment.type == "assignment_expression"
        and operator == "="
        and value.type == "binary_expression"
        and _text(value.child_by_field_name("operator")) == "-"
    ):
        return (
            _text(value.child_by_field_name("left")) == _text(balance)
            and _text(value.child_by_field_name("right")) == _text(amount)
        )
    return False


def _direct_assignment(statement: Node) -> Node | None:
    if statement.type in {"assignment_expression", "augmented_assignment_expression"}:
        return statement
    if statement.type != "expression_statement" or not statement.named_children:
        return None
    expression = statement.named_children[0]
    return (
        expression
        if expression.type in {"assignment_expression", "augmented_assignment_expression"}
        else None
    )


def _same_insufficient_guard_stops(
    statement: Node,
    balance: Node,
    amount: Node,
) -> bool:
    if statement.type != "if_statement":
        return False
    operands = _insufficient_balance_operands(statement.child_by_field_name("condition"))
    if (
        operands is None
        or _text(operands[0]) != _text(balance)
        or _text(operands[1]) != _text(amount)
    ):
        return False
    consequence = statement.child_by_field_name("consequence")
    return consequence is not None and _branch_stops(consequence)


def _check_insufficient_balance_deductions(root: Node) -> list[Issue]:
    issues = []
    reported: set[tuple[int, int]] = set()

    def report(assignment: Node, balance: Node) -> None:
        key = (assignment.start_byte, assignment.end_byte)
        if key in reported:
            return
        reported.add(key)
        issues.append(_issue(
            title="Insufficient balance may not stop the deduction",
            description=(
                "The insufficient-balance path handles the error but execution can still reach "
                "a deduction from the same balance."
            ),
            recommendation=(
                "Return or throw from the insufficient-balance branch, or place the deduction "
                "inside the corresponding valid else branch."
            ),
            line=assignment.start_point.row + 1,
        ))

    for statement in _descendants(root):
        if statement.type != "if_statement":
            continue
        operands = _insufficient_balance_operands(statement.child_by_field_name("condition"))
        consequence = statement.child_by_field_name("consequence")
        if (
            operands is None
            or consequence is None
            or not _handles_insufficient_balance(consequence)
        ):
            continue
        balance, amount = operands

        for branch_statement in _direct_statements(consequence):
            assignment = _direct_assignment(branch_statement)
            if (
                assignment is not None
                and _balance_deduction(assignment, balance, amount)
                and not _branch_stops_before(consequence, assignment)
            ):
                report(assignment, balance)

        if _branch_stops(consequence):
            continue
        parent = statement.parent
        if parent is None or parent.type not in {"program", "statement_block"}:
            continue
        siblings = list(parent.named_children)
        try:
            statement_index = next(
                index for index, sibling in enumerate(siblings)
                if _key(sibling) == _key(statement)
            )
        except StopIteration:
            continue
        for sibling in siblings[statement_index + 1:]:
            if _is_stopping_statement(sibling):
                break
            if _same_insufficient_guard_stops(sibling, balance, amount):
                break
            assignment = _direct_assignment(sibling)
            if assignment is not None and _balance_deduction(assignment, balance, amount):
                report(assignment, balance)

    return issues


def analyze_javascript(code: str) -> list[Issue]:
    """Return conservative semantic findings for syntactically valid JavaScript."""
    tree = _parse_source("javascript", code)
    if tree is None or tree.root_node.has_error:
        return []

    root = tree.root_node
    _, node_scopes, binding_nodes = _build_scopes(root)
    issues = [
        *_check_undefined_names(root, node_scopes, binding_nodes),
        *_check_assignment_conditions(root),
        *_check_loop_boundaries(root),
        *_check_accumulation_assignments(root, node_scopes),
        *_check_null_property_access(root),
        *_check_string_indexes(root, node_scopes),
        *_check_average_division(root, node_scopes),
        *_check_insufficient_balance_deductions(root),
    ]
    return issues
