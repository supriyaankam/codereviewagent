"""
Python static analyzer — AST pass + Pylint pass.

SAFETY: submitted code is NEVER executed.
  - ast.parse()  only parses; it does not eval or exec.
  - Pylint runs via its Python API in-process against a tempfile; the file
    is never imported or run.
"""

import ast
import io
import json
import re
import sys
import tempfile
import os
import textwrap
from contextlib import redirect_stdout
from typing import Any

from pylint.lint import Run as PylintRun
from pylint.reporters.json_reporter import JSONReporter

from app.analyzers.base import Issue, Severity, Category


# ── Pylint message-id → (severity, category) mapping ────────────────────────

_PYLINT_MAP: dict[str, tuple[Severity, Category]] = {
    # Errors
    "E0001": (Severity.critical, Category.syntax),
    "E0100": (Severity.error,    Category.quality),
    "E0101": (Severity.error,    Category.quality),
    "E0102": (Severity.error,    Category.quality),
    "E0103": (Severity.error,    Category.quality),
    "E0104": (Severity.error,    Category.quality),
    "E0105": (Severity.error,    Category.quality),
    "E0106": (Severity.error,    Category.quality),
    "E0107": (Severity.error,    Category.quality),
    "E0108": (Severity.error,    Category.naming),
    "E0110": (Severity.error,    Category.quality),
    "E0111": (Severity.error,    Category.quality),
    "E0112": (Severity.error,    Category.quality),
    "E0113": (Severity.error,    Category.quality),
    "E0114": (Severity.error,    Category.quality),
    "E0115": (Severity.error,    Category.quality),
    "E0116": (Severity.error,    Category.quality),
    "E0117": (Severity.error,    Category.quality),
    "E0118": (Severity.error,    Category.quality),
    "E0119": (Severity.error,    Category.quality),
    "E0401": (Severity.error,    Category.quality),
    "E1101": (Severity.error,    Category.quality),
    # Warnings — unused
    "W0611": (Severity.warning,  Category.unused),
    "W0612": (Severity.warning,  Category.unused),
    "W0613": (Severity.warning,  Category.unused),
    "W0614": (Severity.warning,  Category.unused),
    "W0621": (Severity.warning,  Category.unused),
    "W0622": (Severity.warning,  Category.naming),
    # Warnings — general
    "W0101": (Severity.warning,  Category.quality),
    "W0102": (Severity.warning,  Category.quality),
    "W0104": (Severity.warning,  Category.quality),
    "W0105": (Severity.warning,  Category.quality),
    "W0107": (Severity.warning,  Category.quality),
    "W0108": (Severity.warning,  Category.quality),
    "W0109": (Severity.warning,  Category.quality),
    "W0110": (Severity.warning,  Category.quality),
    "W0120": (Severity.warning,  Category.quality),
    "W0122": (Severity.critical, Category.security),   # use of exec
    "W0123": (Severity.critical, Category.security),   # use of eval
    "W0150": (Severity.warning,  Category.quality),
    "W0199": (Severity.warning,  Category.quality),
    "W0301": (Severity.info,     Category.quality),
    "W0401": (Severity.warning,  Category.quality),
    "W0404": (Severity.warning,  Category.quality),
    "W0406": (Severity.warning,  Category.quality),
    "W0410": (Severity.warning,  Category.quality),
    # Naming
    "C0103": (Severity.info,     Category.naming),
    "C0104": (Severity.info,     Category.naming),
    "C0105": (Severity.info,     Category.naming),
    "C0106": (Severity.info,     Category.naming),
    # Documentation / style
    "C0114": (Severity.info,     Category.quality),
    "C0115": (Severity.info,     Category.quality),
    "C0116": (Severity.info,     Category.quality),
    "C0301": (Severity.info,     Category.quality),
    "C0302": (Severity.info,     Category.complexity),
    "C0303": (Severity.info,     Category.quality),
    "C0304": (Severity.info,     Category.quality),
    "C0305": (Severity.info,     Category.quality),
    "C0321": (Severity.info,     Category.quality),
    "C0325": (Severity.info,     Category.quality),
    "C0326": (Severity.info,     Category.quality),
    "C0327": (Severity.info,     Category.quality),
    "C0328": (Severity.info,     Category.quality),
    # Refactoring / complexity
    "R0201": (Severity.info,     Category.quality),
    "R0205": (Severity.info,     Category.quality),
    "R0801": (Severity.warning,  Category.quality),
    "R0901": (Severity.warning,  Category.complexity),
    "R0902": (Severity.warning,  Category.complexity),
    "R0903": (Severity.info,     Category.complexity),
    "R0904": (Severity.warning,  Category.complexity),
    "R0911": (Severity.warning,  Category.complexity),
    "R0912": (Severity.warning,  Category.complexity),
    "R0913": (Severity.warning,  Category.complexity),
    "R0914": (Severity.warning,  Category.complexity),
    "R0915": (Severity.warning,  Category.complexity),
    "R0916": (Severity.warning,  Category.complexity),
}

_PYLINT_SEVERITY_FALLBACK: dict[str, Severity] = {
    "E": Severity.error,
    "F": Severity.critical,
    "W": Severity.warning,
    "C": Severity.info,
    "R": Severity.info,
}

_PYLINT_CATEGORY_FALLBACK: dict[str, Category] = {
    "E": Category.quality,
    "F": Category.quality,
    "W": Category.quality,
    "C": Category.quality,
    "R": Category.complexity,
}


# ── AST helpers ───────────────────────────────────────────────────────────────

class _ScopeBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return


def _bound_names(nodes: list[ast.stmt]) -> set[str]:
    collector = _ScopeBindingCollector()
    for node in nodes:
        collector.visit(node)
    return collector.names


class _ASTVisitor(ast.NodeVisitor):
    """Single-pass AST walker that collects multiple issue types."""

    def __init__(self, tree: ast.Module) -> None:
        self.issues: list[Issue] = []
        self._module_names = _bound_names(tree.body)
        self._function_names: list[set[str]] = []
        self._class_names: list[set[str]] = []

    # ── dangerous built-in calls ────────────────────────────────────────────

    _DANGEROUS_CALLS = {
        "eval":       ("Use of eval()", "eval() executes arbitrary code strings.",
                       "Never use eval() on untrusted input. Consider safer alternatives like ast.literal_eval()."),
        "exec":       ("Use of exec()", "exec() executes arbitrary code strings.",
                       "Remove exec() calls. Refactor to explicit function calls."),
    }

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id

        shadowed = name in self._module_names or any(
            name in scope for scope in self._function_names
        )
        if not self._function_names:
            shadowed = shadowed or any(name in scope for scope in self._class_names)
        if name in self._DANGEROUS_CALLS and not shadowed:
            title, desc, rec = self._DANGEROUS_CALLS[name]
            self.issues.append(Issue(
                category=Category.security,
                severity=Severity.critical,
                title=title,
                description=desc,
                recommendation=rec,
                line=node.lineno,
                source="ast",
            ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for expression in (*node.decorator_list, *node.bases):
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._class_names.append(_bound_names(node.body))
        for statement in node.body:
            self.visit(statement)
        self._class_names.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        local_names = {
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        }
        if node.args.vararg:
            local_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            local_names.add(node.args.kwarg.arg)
        self._function_names.append(local_names)
        self.visit(node.body)
        self._function_names.pop()

    # ── bare except ─────────────────────────────────────────────────────────

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.type is None:
            self.issues.append(Issue(
                category=Category.quality,
                severity=Severity.warning,
                title="Bare except clause",
                description="A bare `except:` catches all exceptions including KeyboardInterrupt and SystemExit.",
                recommendation="Catch specific exception types, e.g. `except ValueError:`.",
                line=node.lineno,
                source="ast",
            ))
        self.generic_visit(node)

    # ── mutable default arguments ────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        for expression in node.decorator_list:
            self.visit(expression)
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            self.visit(node.args.kwarg.annotation)
        if node.returns:
            self.visit(node.returns)

        local_names = _bound_names(node.body)
        local_names.update(
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        )
        if node.args.vararg:
            local_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            local_names.add(node.args.kwarg.arg)
        self._function_names.append(local_names)
        self._check_mutable_defaults(node)
        self._check_function_complexity(node)
        for statement in node.body:
            self.visit(statement)
        self._function_names.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # same checks apply

    def _check_mutable_defaults(self, node: ast.FunctionDef) -> None:
        mutable_types = (ast.List, ast.Dict, ast.Set)
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None and isinstance(default, mutable_types):
                self.issues.append(Issue(
                    category=Category.quality,
                    severity=Severity.warning,
                    title="Mutable default argument",
                    description=f"Function `{node.name}` uses a mutable object as a default argument. "
                                "This object is shared across all calls.",
                    recommendation="Use `None` as the default and initialise inside the function body.",
                    line=node.lineno,
                    source="ast",
                ))
                break

    def _check_function_complexity(self, node: ast.FunctionDef) -> None:
        """Flag functions whose body is very long (> 60 logical lines)."""
        body_lines = (node.end_lineno or node.lineno) - node.lineno
        if body_lines > 60:
            self.issues.append(Issue(
                category=Category.complexity,
                severity=Severity.warning,
                title="Long function body",
                description=f"Function `{node.name}` spans {body_lines} lines.",
                recommendation="Break the function into smaller, single-purpose functions.",
                line=node.lineno,
                source="ast",
            ))

    # ── assert in production code ────────────────────────────────────────────

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802
        self.issues.append(Issue(
            category=Category.quality,
            severity=Severity.info,
            title="Use of assert statement",
            description="assert statements are disabled when Python is run with optimisations (-O flag).",
            recommendation="Use explicit if/raise patterns for runtime validation in production code.",
            line=node.lineno,
            source="ast",
        ))
        self.generic_visit(node)

    # ── global / nonlocal ────────────────────────────────────────────────────

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.issues.append(Issue(
            category=Category.quality,
            severity=Severity.info,
            title="Use of global statement",
            description=f"Global variables make code harder to reason about: {', '.join(node.names)}.",
            recommendation="Consider encapsulating state in a class or passing values explicitly.",
            line=node.lineno,
            source="ast",
        ))
        self.generic_visit(node)

# ── Syntax check ─────────────────────────────────────────────────────────────

def check_syntax(code: str) -> list[Issue]:
    """Return a single Issue if the code has a syntax error, else []."""
    try:
        ast.parse(code)
        return []
    except SyntaxError as exc:
        return [Issue(
            category=Category.syntax,
            severity=Severity.critical,
            title="Syntax error",
            description=f"{exc.msg} (line {exc.lineno}, col {exc.offset})",
            recommendation="Fix the syntax error before re-submitting for analysis.",
            line=exc.lineno or 0,
            source="syntax",
            column=exc.offset or 0,
        )]


# ── AST analysis ─────────────────────────────────────────────────────────────

def run_ast_analysis(code: str) -> list[Issue]:
    """Walk the AST and return structural issues. Never executes the code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []   # syntax errors are reported separately

    visitor = _ASTVisitor(tree)
    visitor.visit(tree)
    return visitor.issues


# ── Pylint analysis ───────────────────────────────────────────────────────────

def run_pylint_analysis(code: str) -> list[Issue]:
    """
    Run Pylint against a tempfile (never imported/executed).
    Returns a list of Issues parsed from Pylint's JSON output.
    """
    issues: list[Issue] = []

    # Write to a named tempfile — Pylint needs a real path
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="cra_lint_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        output_buf = io.StringIO()
        reporter = JSONReporter(output_buf)

        pylint_args = [
            tmp_path,
            "--output-format=json",
            "--score=no",
            # Disable messages we don't care about at this stage
            "--disable=C0411,C0412,C0413,C0414,C0415",
            "--disable=R0401",
            "--max-line-length=120",
            "--persistent=no",
        ]

        try:
            with redirect_stdout(output_buf):
                PylintRun(pylint_args, reporter=reporter, exit=False)
        except SystemExit:
            pass

        raw = output_buf.getvalue().strip()
        if not raw:
            return []

        messages: list[dict[str, Any]] = json.loads(raw)

        for msg in messages:
            msg_id: str = msg.get("message-id", "")
            msg_type: str = msg.get("type", "").upper()[:1]

            if msg_id in _PYLINT_MAP:
                severity, category = _PYLINT_MAP[msg_id]
            else:
                severity = _PYLINT_SEVERITY_FALLBACK.get(msg_type, Severity.info)
                category = _PYLINT_CATEGORY_FALLBACK.get(msg_type, Category.quality)

            issues.append(Issue(
                category=category,
                severity=severity,
                title=f"[{msg_id}] {msg.get('symbol', 'pylint')}",
                description=msg.get("message", ""),
                recommendation=_pylint_recommendation(msg_id, msg.get("symbol", "")),
                line=msg.get("line", 0),
                source="pylint",
                column=msg.get("column", 0),
            ))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return issues


def _pylint_recommendation(msg_id: str, symbol: str) -> str:
    """Return a human-friendly recommendation for common Pylint codes."""
    recs: dict[str, str] = {
        "C0103": "Rename to follow PEP 8 conventions (snake_case for variables/functions, PascalCase for classes).",
        "C0114": "Add a module-level docstring describing the purpose of this file.",
        "C0115": "Add a class-level docstring.",
        "C0116": "Add a docstring to this function/method.",
        "C0301": "Break the line to stay within the configured line-length limit.",
        "W0611": "Remove the unused import to keep the namespace clean.",
        "W0612": "Remove or use the variable. Prefix with _ if intentionally unused.",
        "W0613": "Remove or use the parameter. Prefix with _ if intentionally unused.",
        "W0621": "Rename to avoid shadowing an outer-scope variable.",
        "W0622": "Rename to avoid shadowing a Python built-in.",
        "W0102": "Use None as the default and initialise inside the function.",
        "W0122": "Remove exec() — it is a security risk and makes code hard to analyse.",
        "W0123": "Replace eval() with ast.literal_eval() or a safer alternative.",
        "R0912": "Reduce the number of branches by extracting helper functions or using early returns.",
        "R0913": "Reduce the number of parameters by grouping related arguments into a data class.",
        "R0914": "Reduce local variable count by extracting sub-functions.",
        "R0915": "Break the function into smaller, single-purpose functions.",
        "E0001": "Fix the syntax error shown in the description.",
        "E0401": "Ensure the module is installed and importable in the current environment.",
    }
    return recs.get(msg_id, f"Consult the Pylint documentation for `{symbol}` ({msg_id}).")
