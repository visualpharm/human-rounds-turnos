"""The retired request/response transport, recognised by its SHAPE in production source.

Until 2026-08-27 every route could hide a `handle(handler, method)` implementation behind
`LegacyHandlerShim`. The shim, the dispatcher and the handlers are deleted; this module is what
makes bringing one back fail a test, without relying on any of their names: it walks every
production `.py` file's syntax tree and reports

* a definition, reference, `getattr`/`setattr` or class member named `handle`;
* an import (static, function-local, aliased, or `importlib`/`__import__` with a constant that
  folds to a name) of a retired module;
* a class, `type(...)` call, `SimpleNamespace` or object that rebuilds the old response writer
  (`send_response`/`send_header`/`end_headers`, `_send_json`, `wfile.write`) or a fabricated
  request (`command`/`path`/`headers`/`client_address`/`_body` plus readers).

Real stdlib servers (`BaseHTTPRequestHandler`), `urllib` handlers, dataclasses and Starlette
endpoints are not findings. Limit, as for the census it replaces: a handler written in a wholly
different shape is not detected, nor is arbitrary dead source.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

from _layout import in_app


ROOT = Path(__file__).resolve().parents[1]

# Modules that existed for the old transport and are deleted. Importing any of them, by any
# alias, anywhere in production source is a finding (`test_legacy_topology_files_stay_deleted`
# pins that the files themselves stay deleted).
RETIRED_MODULES = frozenset({
    "legacy_shim",
    "core.routing",
    "core.module_router",
    "services.legacy_http",
    "services.legacy_static",
    "routers.legacy_http_transport",
    "ops.legacy_web_server",
    "server",
})


class ShapeError(RuntimeError):
    """A production source file cannot be analysed."""


class Finding(NamedTuple):
    path: str
    line: int
    column: int
    code: str
    message: str

    def as_dict(self):
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
        }


_RESPONSE_WRITER_METHODS = frozenset({
    "send_response", "send_header", "end_headers", "_send_json",
})
_RESPONSE_STATE_FIELDS = frozenset({
    "status", "json", "body", "headers_out", "sent_headers", "wfile",
})
_REQUEST_FIELDS = frozenset({
    "command", "path", "headers", "client_address", "_body", "rfile",
})
_REQUEST_READERS = frozenset({"_read_json", "_read_body", "read_body", "rfile"})
_SURFACE_MEMBERS = (
    _RESPONSE_WRITER_METHODS | _RESPONSE_STATE_FIELDS
    | _REQUEST_FIELDS | _REQUEST_READERS | {"write"}
)
_IGNORED_SOURCE_DIRS = frozenset({
    ".git", ".claude", "__pycache__", "docs", "node_modules", "scripts", "tests",
})


def _finding(path, node, code: str, message: str) -> Finding:
    return Finding(
        path=str(path),
        line=getattr(node, "lineno", 1),
        column=getattr(node, "col_offset", 0) + 1,
        code=code,
        message=message,
    )


def _dotted_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _constant_string(node, bindings) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        left = _constant_string(node.left, bindings)
        right = _constant_string(node.right, bindings)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        values = []
        for value in node.values:
            candidate = (value.value if isinstance(value, ast.Constant)
                         and isinstance(value.value, str) else None)
            if candidate is None:
                return None
            values.append(candidate)
        return "".join(values)
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
            and isinstance(node.args[0], (ast.List, ast.Tuple))):
        separator = _constant_string(node.func.value, bindings)
        parts = [_constant_string(item, bindings) for item in node.args[0].elts]
        if separator is not None and all(item is not None for item in parts):
            return separator.join(parts)
    return None


def _member_names_from_dict(node, dict_bindings) -> set[str]:
    if isinstance(node, ast.Name):
        return set(dict_bindings.get(node.id, ()))
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _surface_findings(
    path, node, label: str, members, *, request_pair_is_clone=False,
) -> list[Finding]:
    members = set(members)
    findings = []
    classic_writer = {"send_response", "send_header", "end_headers"}
    if classic_writer <= members:
        findings.append(_finding(
            path,
            node,
            "legacy-response-writer",
            f"{label} implements the legacy response writer surface: "
            "send_response, send_header, end_headers",
        ))
    elif "_send_json" in members:
        state = ", ".join(sorted(members & _RESPONSE_STATE_FIELDS)) or "none"
        findings.append(_finding(
            path,
            node,
            "legacy-json-writer",
            f"{label} implements a legacy JSON response writer with state: {state}",
        ))
    elif {"wfile", "write"} <= members:
        findings.append(_finding(
            path,
            node,
            "legacy-wfile-writer",
            f"{label} implements the legacy wfile/write response surface",
        ))

    request_fields = members & _REQUEST_FIELDS
    readers = members & _REQUEST_READERS
    if (len(request_fields) >= 3
            or (len(request_fields) >= 2
                and (readers or request_pair_is_clone))):
        findings.append(_finding(
            path,
            node,
            "fabricated-request-clone",
            f"{label} fabricates a legacy request shape (fields: "
            f"{', '.join(sorted(request_fields))}; readers: "
            f"{', '.join(sorted(readers))})",
        ))
    return findings


class _ShapeVisitor(ast.NodeVisitor):
    """Find retired transport behavior without relying on its class names."""

    def __init__(self, path: str):
        self.path = path
        self.findings: list[Finding] = []
        self.string_bindings: dict[str, str] = {}
        self.dict_bindings: dict[str, set[str]] = {}
        self.class_nodes: dict[str, ast.ClassDef] = {}
        self.class_members: dict[str, set[str]] = {}
        self.object_members: dict[tuple[str, str], tuple[ast.AST, set[str]]] = {}
        self.scope = ["<module>"]

    def scan(self, tree) -> list[Finding]:
        self._collect_bindings(tree)
        self._collect_classes(tree)
        self.visit(tree)
        for (_scope, expression), (node, members) in self.object_members.items():
            self.findings.extend(_surface_findings(
                self.path, node, f"object {expression}", members,
                request_pair_is_clone=True,
            ))
        unique = {finding: None for finding in self.findings}
        return sorted(unique, key=lambda item: (
            item.path, item.line, item.column, item.code, item.message,
        ))

    def _collect_bindings(self, tree):
        assignments = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value

        # Resolve aliases to a fixed point so reordering declarations cannot
        # hide a dynamic import/member or a dictionary-backed ``type`` call.
        changed = True
        while changed:
            changed = False
            for name, value in assignments.items():
                string = _constant_string(value, self.string_bindings)
                if string is not None and self.string_bindings.get(name) != string:
                    self.string_bindings[name] = string
                    changed = True
                keys = _member_names_from_dict(value, self.dict_bindings)
                if keys and self.dict_bindings.get(name) != keys:
                    self.dict_bindings[name] = keys
                    changed = True

    @staticmethod
    def _own_class_members(node: ast.ClassDef) -> set[str]:
        members = set()
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members.add(statement.name)
                for child in ast.walk(statement):
                    if (isinstance(child, ast.Attribute)
                            and isinstance(child.value, ast.Name)
                            and child.value.id in {"self", "cls"}
                            and (isinstance(child.ctx, ast.Store)
                                 or child.attr in _SURFACE_MEMBERS)):
                        members.add(child.attr)
                    if (isinstance(child, ast.Attribute)
                            and child.attr == "write"
                            and isinstance(child.value, ast.Attribute)
                            and child.value.attr == "wfile"
                            and isinstance(child.value.value, ast.Name)
                            and child.value.value.id in {"self", "cls"}):
                        members.update({"wfile", "write"})
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (statement.targets if isinstance(statement, ast.Assign)
                           else [statement.target])
                for target in targets:
                    if isinstance(target, ast.Name):
                        members.add(target.id)
        return members

    def _collect_classes(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self.class_nodes[node.name] = node
                self.class_members[node.name] = self._own_class_members(node)

        # A renamed local subclass still implements its local base's behavior.
        changed = True
        while changed:
            changed = False
            for name, node in self.class_nodes.items():
                inherited = set().union(*(
                    self.class_members.get(_dotted_name(base), set())
                    for base in node.bases
                )) if node.bases else set()
                before = len(self.class_members[name])
                self.class_members[name].update(inherited)
                changed = changed or len(self.class_members[name]) != before

    def _record_object_member(self, base, member: str, node):
        expression = _dotted_name(base)
        if not expression:
            try:
                expression = ast.unparse(base)
            except (AttributeError, ValueError):
                expression = "<dynamic>"
        key = (".".join(self.scope), expression)
        first, members = self.object_members.setdefault(key, (node, set()))
        members.add(member)
        self.object_members[key] = (first, members)

    def visit_ClassDef(self, node):
        members = self.class_members.get(node.name, set())
        is_real_stdlib_server = any(
            _dotted_name(base).endswith("BaseHTTPRequestHandler")
            for base in node.bases
        )
        custom_constructor = any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "__init__"
            for item in node.body
        )
        if not is_real_stdlib_server:
            self.findings.extend(_surface_findings(
                self.path, node, f"class {node.name}", members,
                request_pair_is_clone=custom_constructor,
            ))
        self.scope.append(f"{node.name}@{node.lineno}")
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node):
        if node.name == "handle":
            self.findings.append(_finding(
                self.path,
                node,
                "legacy-handle-definition",
                "production code defines retired handle transport entrypoint",
            ))
        self.scope.append(f"{node.name}@{node.lineno}")
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load) and node.attr == "handle":
            self.findings.append(_finding(
                self.path,
                node,
                "legacy-handle-reference",
                "production code references retired .handle transport",
            ))
        elif not (isinstance(node.value, ast.Name)
                  and node.value.id in {"self", "cls"}):
            if isinstance(node.ctx, ast.Store) or node.attr in _SURFACE_MEMBERS:
                self._record_object_member(node.value, node.attr, node)
            if (node.attr == "write"
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "wfile"
                    and not (isinstance(node.value.value, ast.Name)
                             and node.value.value.id in {"self", "cls"})):
                self._record_object_member(node.value.value, "wfile", node)
                self._record_object_member(node.value.value, "write", node)
        if isinstance(node.ctx, ast.Store) and node.attr == "handle":
            self.findings.append(_finding(
                self.path,
                node,
                "legacy-handle-dynamic-surface",
                "production code assigns retired .handle transport surface",
            ))
        self.generic_visit(node)

    def visit_Call(self, node):
        function_name = _dotted_name(node.func)
        if function_name == "getattr" and len(node.args) >= 2:
            member = _constant_string(node.args[1], self.string_bindings)
            if member == "handle":
                self.findings.append(_finding(
                    self.path,
                    node,
                    "legacy-handle-dynamic-reference",
                    "production code dynamically resolves retired handle transport",
                ))
        elif function_name == "setattr" and len(node.args) >= 2:
            member = _constant_string(node.args[1], self.string_bindings)
            if member:
                self._record_object_member(node.args[0], member, node)
                if member == "handle":
                    self.findings.append(_finding(
                        self.path,
                        node,
                        "legacy-handle-dynamic-surface",
                        "production code dynamically assigns retired handle transport",
                    ))

        if function_name in {"importlib.import_module", "__import__"} and node.args:
            module_name = _constant_string(node.args[0], self.string_bindings)
            if in_app(module_name) in RETIRED_MODULES:
                self.findings.append(_finding(
                    self.path,
                    node,
                    "legacy-dynamic-import",
                    f"production code dynamically imports retired contract {module_name}",
                ))

        if function_name == "type" and len(node.args) >= 3:
            members = _member_names_from_dict(node.args[2], self.dict_bindings)
            self.findings.extend(_surface_findings(
                self.path, node, "dynamically constructed class", members,
                request_pair_is_clone=True,
            ))
            if "handle" in members:
                self.findings.append(_finding(
                    self.path,
                    node,
                    "legacy-handle-dynamic-surface",
                    "dynamically constructed class exposes retired handle transport",
                ))
        elif function_name.endswith("SimpleNamespace"):
            members = {keyword.arg for keyword in node.keywords if keyword.arg}
            self.findings.extend(_surface_findings(
                self.path, node, "dynamically constructed object", members,
                request_pair_is_clone=True,
            ))
            if "handle" in members:
                self.findings.append(_finding(
                    self.path,
                    node,
                    "legacy-handle-dynamic-surface",
                    "dynamically constructed object exposes retired handle transport",
                ))
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if in_app(alias.name) in RETIRED_MODULES:
                self.findings.append(_finding(
                    self.path,
                    node,
                    "legacy-contract-import",
                    f"production code imports retired contract {alias.name}",
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        imported_contracts = {
            qualified
            for alias in node.names
            for qualified in (module, f"{module}.{alias.name}" if module else alias.name)
            if in_app(qualified) in RETIRED_MODULES
        }
        for contract in sorted(imported_contracts):
            self.findings.append(_finding(
                self.path,
                node,
                "legacy-contract-import",
                f"production code imports retired contract {contract}",
            ))
        self.generic_visit(node)


def findings_from_source(source: str, path="<memory>") -> list[dict]:
    """Return stable diagnostics for one source unit (used by adversarial tests)."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ShapeError(f"cannot parse production source {path}: {exc}") from exc
    return [finding.as_dict() for finding in _ShapeVisitor(str(path)).scan(tree)]


def production_python_files(root=ROOT):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in _IGNORED_SOURCE_DIRS for part in relative.parts):
            continue
        yield path


def production_findings(production_files=None) -> list[dict]:
    files = (production_python_files() if production_files is None
             else production_files)
    findings = []
    for path in files:
        path = Path(path)
        display_path = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        findings.extend(findings_from_source(
            path.read_text(encoding="utf-8"), display_path,
        ))
    return sorted(findings, key=lambda item: (
        item["path"], item["line"], item["column"], item["code"], item["message"],
    ))
