"""Parser-backed syntax checks for source languages without dedicated analyzers."""

from __future__ import annotations

import ast
from tree_sitter import Node, Tree

from tree_sitter_language_pack import get_parser

from app.analyzers.base import Category, Issue, Severity


_PARSER_NAMES = {
    "python": "python",
    "py": "python",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "java": "java",
    "csharp": "csharp",
    "c#": "csharp",
    "go": "go",
    "ruby": "ruby",
    "php": "php",
    "cpp": "cpp",
    "c++": "cpp",
    "c": "c",
    "rust": "rust",
    "kotlin": "kotlin",
    "swift": "swift",
}
_IDENTIFIER_NODE_TYPES = {
    "identifier",
    "type_identifier",
    "property_identifier",
    "field_identifier",
    "namespace_identifier",
    "constant",
    "simple_identifier",
    "bound_identifier",
    "name",
}
_STRING_NODE_TYPES = {
    "string",
    "string_literal",
    "raw_string_literal",
    "interpreted_string_literal",
    "encapsed_string",
    "line_string_literal",
}
_ASSIGNMENT_NODE_TYPES = {
    "assignment",
    "assignment_expression",
    "init_declarator",
    "let_declaration",
    "property_declaration",
    "short_var_declaration",
    "variable_declarator",
    "var_spec",
}
_STATEMENT_NODE_TYPES = {
    "assignment",
    "expression_statement",
    "field_declaration",
    "function_definition",
    "function_item",
    "lexical_declaration",
    "local_variable_declaration",
    "method_declaration",
    "method_definition",
    "short_var_declaration",
    "variable_declaration",
    "declaration",
    "function_declaration",
}


def _parser_name(language: str) -> str | None:
    return _PARSER_NAMES.get(language.strip().lower())


def _parse_source(language: str, code: str) -> Tree | None:
    parser_name = _parser_name(language)
    if parser_name is None:
        return None

    parser_names = [parser_name]
    if parser_name == "typescript":
        parser_names.append("tsx")
    encoded = code.encode("utf-8")
    last_tree = None
    for candidate in parser_names:
        tree = get_parser(candidate).parse(encoded)
        if not tree.root_node.has_error:
            return tree
        last_tree = tree
    return last_tree


def source_identifiers(language: str, code: str) -> set[str] | None:
    """Extract identifier tokens from a parsed source tree, excluding comments and strings."""
    if language.strip().lower() in {"python", "py"}:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            pass
        else:
            identifiers = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    identifiers.add(node.id)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    identifiers.add(node.name)
                elif isinstance(node, ast.arg):
                    identifiers.add(node.arg)
                elif isinstance(node, ast.alias):
                    identifiers.add(node.asname or node.name.split(".")[0])
                elif isinstance(node, ast.Attribute):
                    identifiers.add(node.attr)
            return identifiers

    tree = _parse_source(language, code)
    if tree is None:
        return None

    identifiers: set[str] = set()
    nodes = [tree.root_node]
    while nodes:
        node = nodes.pop()
        if node.type in _IDENTIFIER_NODE_TYPES or node.type.endswith("_identifier"):
            identifiers.add(node.text.decode("utf-8"))
        nodes.extend(node.children)
    return identifiers


def literal_assignments(language: str, code: str) -> list[tuple[str, str, int]] | None:
    """Extract identifier-to-string-literal assignments from parsed source."""
    normalized_language = language.strip().lower()
    if normalized_language in {"python", "py"}:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        assignments = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name):
                        assignments.append((child.id, repr(value.value), child.lineno))
                    elif isinstance(child, ast.Attribute):
                        assignments.append((child.attr, repr(value.value), child.lineno))
        return assignments

    tree = _parse_source(language, code)
    if tree is None:
        return None

    assignments = []
    nodes = [tree.root_node]
    while nodes:
        node = nodes.pop()
        if node.type in _ASSIGNMENT_NODE_TYPES:
            name_node = next(
                (
                    node.child_by_field_name(field)
                    for field in ("left", "name", "declarator", "pattern")
                    if node.child_by_field_name(field) is not None
                ),
                None,
            )
            value_node = next(
                (
                    node.child_by_field_name(field)
                    for field in ("right", "value")
                    if node.child_by_field_name(field) is not None
                ),
                None,
            )
            literal = _unwrap_string_literal(value_node) if value_node else None
            if literal is None:
                literal = next(
                    (
                        _unwrap_string_literal(child)
                        for child in node.named_children
                        if _unwrap_string_literal(child) is not None
                    ),
                    None,
                )
            names = _identifier_names(name_node) if name_node else _identifier_names(node)
            if literal is not None:
                for name in names:
                    assignments.append((
                        name,
                        literal.text.decode("utf-8"),
                        literal.start_point.row + 1,
                    ))
        nodes.extend(node.children)
    return assignments


def literal_strings(language: str, code: str) -> list[tuple[str, int]] | None:
    """Extract string literal source tokens, excluding comments and non-code text."""
    if language.strip().lower() in {"python", "py"}:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        return [
            (repr(node.value), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]

    tree = _parse_source(language, code)
    if tree is None:
        return None
    result = []
    nodes = [tree.root_node]
    while nodes:
        node = nodes.pop()
        if node.type in _STRING_NODE_TYPES:
            result.append((node.text.decode("utf-8"), node.start_point.row + 1))
            continue
        nodes.extend(node.children)
    return result


def _identifier_names(node) -> list[str]:
    if node is None:
        return []
    names = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _IDENTIFIER_NODE_TYPES or current.type.endswith("_identifier"):
            names.append(current.text.decode("utf-8").lstrip("$"))
        stack.extend(reversed(current.children))
    return names


def _unwrap_string_literal(node: Node | None) -> Node | None:
    if node is None:
        return None
    if node.type in _STRING_NODE_TYPES:
        return node
    if node.type in {"expression_list", "parenthesized_expression"}:
        children = node.named_children
        if len(children) == 1:
            return _unwrap_string_literal(children[0])
    return None


def extract_statement_region(
    language: str,
    code: str,
    target_line: int,
) -> tuple[int, int, int, int] | None:
    """Return the smallest parser-recognized statement covering a source line."""
    tree = _parse_source(language, code)
    if tree is None:
        return None

    target_row = target_line - 1
    candidates: list[tuple[int, int, int, int, int]] = []
    encoded = code.encode("utf-8")
    nodes = [tree.root_node]
    while nodes:
        node = nodes.pop()
        start_row = node.start_point.row
        end_row = node.end_point.row
        if (
            node.is_named
            and (
                node.type in _STATEMENT_NODE_TYPES
                or node.type.endswith("_statement")
                or node.type.endswith("_declaration")
            )
            and start_row <= target_row <= end_row
        ):
            start = len(encoded[:node.start_byte].decode("utf-8"))
            end = len(encoded[:node.end_byte].decode("utf-8"))
            candidates.append((end - start, start, end, start_row + 1, end_row + 1))
        nodes.extend(node.children)

    if not candidates:
        return None
    _, start, end, start_line, end_line = min(candidates)
    return start, end, start_line, end_line


def check_language_syntax(language: str, code: str) -> list[Issue] | None:
    """Return parser findings, or None when this language has no bundled grammar."""
    tree = _parse_source(language, code)
    if tree is None:
        return None

    if not tree.root_node.has_error:
        return []

    nodes = [tree.root_node]
    while nodes:
        node = nodes.pop()
        if node.type == "ERROR" or node.is_missing:
            row, column = node.start_point
            title = "Syntax error: missing token" if node.is_missing else "Syntax error"
            return [Issue(
                category=Category.syntax,
                severity=Severity.critical,
                title=title,
                description=f"The {language} parser found invalid syntax near this location.",
                recommendation="Correct the syntax before submitting the code for review.",
                line=row + 1,
                source="tree_sitter",
                column=column + 1,
            )]
        nodes.extend(reversed(node.children))

    return []
