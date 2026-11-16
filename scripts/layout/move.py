#!/usr/bin/env python3
"""move.py — one mechanical stage of the repository layout move (scripts/layout/plan.py).

    python3 scripts/layout/move.py --stage root|app|tests [--report FILE] [--dry-run]

Run from the repository root of a clean checkout. The stage:

1. moves its entries with `git mv` (directories whole) and deletes / creates what the plan says;
2. rewrites what names the moved code, using a table built from the tree BEFORE the move:
   - Python (libcst, formatting kept): `from core import db` -> `from app.core import db`;
     `import connectors` -> `from app import connectors` (only `from ... import ...` forms are
     written, so no module ever gains a global name `app`); string targets of `mock.patch`,
     `import_module`, `__import__`, `find_spec`, `route_enabled`, `sys.modules[...]`; path literals
     right after a root-like base in `os.path.join(ROOT, ...)`, `Path(ROOT, ...)`, `ROOT / "..."`;
   - module manifests (`module.json`): dotted `module` / `after` names, never asset paths (those
     are public URL names);
   - JavaScript in tests, scripts and tooling: literals of `join(ROOT, ...)` / `resolve(ROOT, ...)`
     and `../` literals of test files;
   - living markdown: relative links, and paths inside `code` spans;
   - CI, shell, YAML, ignore and ratchet files: path tokens;
3. reports every place it left alone that still looks like it names a moved path or module —
   each one is read by a person before the stage is committed.

Never rewritten: procrastinate task names (plan.FROZEN_STRINGS), historical records
(plan.HISTORICAL_PREFIXES), third-party code, HTML/CSS and app JavaScript (URL space).
Exit 0 on success, 1 when the tree is not clean or the stage was already applied.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan  # noqa: E402

try:  # libcst is a CI-only dependency (scripts/requirements-ci.txt), never imported by the app
    import libcst as cst
    from libcst.metadata import MetadataWrapper, PositionProvider
except ImportError:  # pragma: no cover - main() explains it
    cst = None

ROOT_NAMES = frozenset({
    "ROOT", "_ROOT", "REPO", "_REPO", "REPO_ROOT", "_REPO_ROOT", "PROJECT_ROOT", "_PROJECT_ROOT",
    "APP_ROOT", "ROOT_DIR", "REPO_DIR", "BASE_DIR", "_DIR",
})
STRING_TARGET_CALLS = frozenset({"patch", "import_module", "__import__", "find_spec",
                                 "route_enabled", "multiple"})
SYS_MODULES_METHODS = frozenset({"get", "pop", "setdefault"})
JOIN_CALLS = frozenset({"join", "Path", "PurePath", "PosixPath"})
DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
# Where a string naming a real module is a reference to it even outside the explicit contexts
# (golden lists of route modules, `"module:name"` helper references, census tables).
IMPLICIT_REFERENCE_PREFIXES = ("test/", "tests/", "scripts/")
# Helpers of tests and scripts that read a repository file named relative to the root
# (`read("admin-direct.css")`, `leer("tokens.css")`, `open("admin-app.js")`): their first literal
# is a disk path, a moved file's new place (module assets included).
READER_CALLS = frozenset({"read", "leer", "_read", "_leer", "source", "runtime", "at", "open",
                          "readFileSync", "readFile", "read_text_at"})
DISK_RELATIVE_JS_PREFIXES = ("test/", "tests/", "stories/", ".storybook/")
JS_READER_RE = re.compile(r"\b(%s)\(\s*(['\"])([^'\"\n]+)\2" % "|".join(sorted(READER_CALLS)))


@dataclass
class Report:
    changed: dict = field(default_factory=dict)   # new path -> number of rewrites
    notes: list = field(default_factory=list)      # (path, line, kind, text)

    def note(self, path, line, kind, text):
        self.notes.append((path, str(line), kind, text))

    def count(self, path, n):
        self.changed[path] = self.changed.get(path, 0) + n

    def render(self):
        lines = ["files rewritten: %d, rewrites: %d, notes: %d" % (
            len(self.changed), sum(self.changed.values()), len(self.notes))]
        for path in sorted(self.changed):
            lines.append("  rewrote %s %d" % (path, self.changed[path]))
        for path, line, kind, text in sorted(self.notes):
            lines.append("  NOTE %s:%s %s %s" % (path, line, kind, text))
        return "\n".join(lines) + "\n"


class OldTree:
    """The tree before the stage: tracked files, their directories, the importable modules."""

    def __init__(self, files, root="."):
        self.root = root
        self.files = frozenset(files)
        dirs = set()
        modules = {}
        for f in files:
            parts = f.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
            if f.endswith(".py") and not plan.is_skipped(f):
                mod = f[:-3]
                if mod.endswith("/__init__"):
                    mod = mod[: -len("/__init__")]
                modules[mod.replace("/", ".")] = f
        self.dirs = frozenset(dirs)
        self.modules = frozenset(modules)
        self._module_files = modules
        self._names = {}

    def top_level_names(self, module):
        """Names a module binds at its top level (defs, classes, assignments, imports)."""
        if module not in self._names:
            names = set()
            try:
                tree = ast.parse(Path(self.root, self._module_files[module]).read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                tree = None
            for node in tree.body if tree else ():
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        for sub in ast.walk(t):
                            if isinstance(sub, ast.Name):
                                names.add(sub.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        names.add((a.asname or a.name).split(".", 1)[0])
            self._names[module] = frozenset(names)
        return self._names[module]

    def names_real_object(self, name):
        """True when `name` is a module, or a module followed by a name it defines."""
        prefix = self.module_prefix(name)
        if prefix is None:
            return False
        rest = name[len(prefix) + 1:]
        if not rest:
            return True
        return rest.split(".", 1)[0] in self.top_level_names(prefix)

    def exists(self, path):
        path = path.rstrip("/")
        return path in self.files or path in self.dirs

    def module_prefix(self, name):
        """Longest dotted prefix of `name` that is a module of the old tree, or None."""
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.modules:
                return cand
        return None


def _glob_free_prefix(token):
    """`test/*.test.mjs` -> `test`; a token without glob characters comes back whole."""
    cut = len(token)
    for ch in "*?[{":
        i = token.find(ch)
        if i != -1:
            cut = min(cut, i)
    return token[:cut].rstrip("/")


class Mover:
    """The rename rules of one stage, decided against the old tree."""

    def __init__(self, stage, old_tree, report):
        self.stage = stage
        self.old = old_tree
        self.report = report
        self.renames = plan.PYTHON_RENAMES[stage]
        self.moves = plan.stage_moves(stage)
        self.root_assets = frozenset(plan.ROOT_WEB_ASSETS) if stage == "root" else frozenset()

    def new_dotted(self, name, strict):
        """Rewritten dotted name or None. `strict` (outside the explicit call contexts): only a
        name with a module of two or more components, or a root module, as its prefix counts."""
        if name in plan.FROZEN_STRINGS:
            return None
        if name.split(".", 1)[0] not in self.renames:
            return None
        prefix = self.old.module_prefix(name)
        if prefix is None:
            return None
        if strict and not ("." in prefix or prefix in plan.ROOT_MODULES) \
                or strict and not self.old.names_real_object(name):
            return None
        return plan.map_dotted(self.stage, name)

    def new_path(self, path):
        """Rewritten repository-relative path (globs and a trailing slash kept), or None."""
        if not path or path.startswith(("/", ".")) or "\\" in path:
            return None
        stem = _glob_free_prefix(path)
        if not stem or not self.old.exists(stem):
            return None
        mapped = plan.map_path(self.stage, stem)
        if mapped is None:
            return None
        return mapped + path[len(stem):]

    def rewrite_code_text(self, text):
        """Import statements inside the text of a string that holds code (a subprocess script, a
        source a test parses): `from core import x`, `from core.db import y`, `import core.{x}`,
        `import connectors`, and a quoted `'core.'` prefix. Returns (text, rewrites)."""
        if not self.renames:
            return text, 0
        heads = "|".join(re.escape(h) for h in sorted(self.renames, key=len, reverse=True))
        n = 0

        def new_of(head):
            return plan.map_dotted(self.stage, head)

        def from_sub(m):
            nonlocal n
            n += 1
            return m.group(1) + new_of(m.group(2)) + m.group(3) + m.group(4)

        def dotted_sub(m):
            nonlocal n
            n += 1
            return m.group(1) + new_of(m.group(2)) + m.group(3)

        def bare_sub(m):
            nonlocal n
            parent, _, last = new_of(m.group(2)).rpartition(".")
            alias = m.group(3) or ("" if last == m.group(2) else " as " + m.group(2))
            n += 1
            return "from %s import %s%s" % (parent, last, alias)

        def prefix_sub(m):
            nonlocal n
            n += 1
            return m.group(1) + new_of(m.group(2)) + "." + m.group(3)

        def list_sub(m):
            # `import core.tz, core.format, worker` -> one statement per name, each then rewritten
            # below like any other (`import a; import b` binds exactly what `import a, b` did)
            names = [e.strip() for e in m.group(3).split(",")]
            if not any(re.match(r"(%s)\b" % heads, e) for e in names):
                return m.group(0)
            return m.group(1) + m.group(2) + "; ".join("import " + e for e in names)

        element = r"[\w.{}%]+(?:\s+as\s+\w+)?"
        text = re.sub(r"(^|\n|;|\\n|\\t|[\"'])([ \t]*)import\s+(" + element + r"(?:\s*,\s*" + element + r")+)",
                      list_sub, text)
        # a keyword starts the text, follows a non-word character, or follows an escaped
        # newline/tab of the literal's source (`"...\nimport x"`)
        kw = r"(?:(?<=\\n)|(?<=\\t)|(?<![\w.]))"
        text = re.sub(r"(%sfrom\s+)(%s)((?:\.[\w{}%%]+)*)(\s+import\b)" % (kw, heads), from_sub, text)
        text = re.sub(r"(%simport\s+)(%s)((?:\.[\w{}%%]+)+)" % (kw, heads), dotted_sub, text)
        text = re.sub(r"%s(import\s+)(%s)\b(?![.\w])((?:\s+as\s+\w+)?)" % (kw, heads), bare_sub, text)
        text = re.sub(r"(['\"])(%s)\.(['\"])" % heads, prefix_sub, text)
        return text, n

    def is_code_path(self, path):
        """A path a test or script reads from disk, never a public asset name: a Python file, a
        directory, or anything outside `modules/` (module assets keep their `modules/<id>/<file>`
        URL names, so those are left to a person)."""
        stem = _glob_free_prefix(path)
        return stem.endswith(".py") or stem in self.old.dirs or not stem.startswith("modules/")

    def rewrite_parts(self, parts):
        """Literals that follow a root-like base -> the new literals, or None when nothing moves.

        Every old entry of the plan is a single path component, so only the first literal can
        name it: `["core", "db.py"]` -> `["app", "core", "db.py"]`, `["core/db.py"]` ->
        `["app/core/db.py"]`, `["test", "x"]` -> `["tests", "x"]`."""
        if not parts:
            return None
        first = parts[0].strip("/")
        head = first.split("/", 1)[0]
        for old, new in self.moves:
            if head != old:
                continue
            if not self.old.exists(head):
                return None
            if "/" in first:
                return [new + first[len(head):]] + list(parts[1:])
            return new.split("/") + list(parts[1:])
        return None


# ---------------------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------------------
def _dotted(expr):
    """`a.b.c` (a Name/Attribute chain) -> "a.b.c", else None."""
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute):
        left = _dotted(expr.value)
        return None if left is None else left + "." + expr.attr.value
    return None


def _attr_chain(dotted):
    parts = dotted.split(".")
    node = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


def _plain_string(node):
    """The value of a simple, escape-free, non-f, non-bytes string literal, else None."""
    if not isinstance(node, cst.SimpleString):
        return None
    prefix = node.prefix.lower()
    if "f" in prefix or "b" in prefix or "\\" in node.value:
        return None
    value = node.evaluated_value
    return value if isinstance(value, str) else None


def _with_value(node, new):
    return node.with_changes(value=node.prefix + node.quote + new + node.quote)


def _flatten_div(node):
    if isinstance(node, cst.BinaryOperation) and isinstance(node.operator, cst.Divide):
        return _flatten_div(node.left) + [node.right]
    return [node]


class PyRewriter(cst.CSTTransformer if cst else object):
    """Imports, explicit string targets and root-based path compositions of one module."""

    def __init__(self, mover, path, positions):
        super().__init__()
        self.m = mover
        self.path = path
        self.positions = positions
        self.n = 0
        self.decided = set()   # id() of string nodes whose fate a context already decided
        self.div_depth = 0
        self.local_roots = set()

    def _line(self, node):
        pos = self.positions.get(node)
        return pos.start.line if pos else "?"

    def _note(self, node, kind, text):
        self.m.report.note(self.path, self._line(node), kind, text)

    # imports -----------------------------------------------------------------------------
    def leave_ImportFrom(self, original, updated):
        if updated.relative or updated.module is None:
            return updated
        name = _dotted(updated.module)
        if name is None or name.split(".", 1)[0] not in self.m.renames:
            return updated
        new = plan.map_dotted(self.m.stage, name)
        if new is None or new == name:
            return updated
        self.n += 1
        return updated.with_changes(module=_attr_chain(new))

    def leave_Import(self, original, updated):
        names = updated.names
        if not any((_dotted(a.name) or "").split(".", 1)[0] in self.m.renames for a in names):
            return updated
        if len(names) != 1:
            self._note(original, "import-multi", "several names in one import")
            return updated
        alias = names[0]
        name = _dotted(alias.name)
        new = plan.map_dotted(self.m.stage, name)
        if new is None:
            return updated
        if "." in name and alias.asname is None:
            self._note(original, "import-dotted", "`import %s` binds the old top name" % name)
            return updated
        parent, _, last = new.rpartition(".")
        asname = alias.asname
        if asname is not None and isinstance(asname.name, cst.Name) and asname.name.value == last:
            asname = None  # `import a.b.c as c` -> `from app.a.b import c`
        elif asname is None and last != name:
            # `import app` -> `from app import main as app`: the local name stays `app`
            asname = cst.AsName(name=cst.Name(name))
        self.n += 1
        return cst.ImportFrom(module=_attr_chain(parent),
                              names=[cst.ImportAlias(name=cst.Name(last), asname=asname)])

    # explicit string contexts ------------------------------------------------------------
    def _context(self, call):
        """Which arguments of `call` are decided here: ('dotted', [nodes]) / ('join', ...)."""
        func = _dotted(call.func) or ""
        last = func.rsplit(".", 1)[-1]
        args = call.args
        if last in STRING_TARGET_CALLS and args:
            return "dotted", [args[0].value]
        if last in SYS_MODULES_METHODS and func.endswith("sys.modules." + last) and args:
            return "dotted", [args[0].value]
        if func.endswith("patch.dict") and len(args) >= 2 and _dotted(args[0].value) == "sys.modules" \
                and isinstance(args[1].value, cst.Dict):
            return "dotted", [el.key for el in args[1].value.elements
                              if isinstance(el, cst.DictElement)]
        if last in JOIN_CALLS and len(args) >= 2 and self._base_ok(args[0].value):
            run = []
            for a in args[1:]:
                if _plain_string(a.value) is None:
                    break
                run.append(a.value)
            return "join", run
        if last in READER_CALLS and args and self.path.startswith(IMPLICIT_REFERENCE_PREFIXES) \
                and _plain_string(args[0].value) is not None:
            return "reader", [args[0].value]
        return None, []

    def visit_Call(self, node):
        _kind, nodes = self._context(node)
        self.decided.update(id(n) for n in nodes)

    def visit_Subscript(self, node):
        if _dotted(node.value) == "sys.modules" and len(node.slice) == 1 \
                and isinstance(node.slice[0].slice, cst.Index):
            self.decided.add(id(node.slice[0].slice.value))

    def _rewrite_dotted_node(self, node):
        value = _plain_string(node)
        if value is None:
            return node
        target = value.split(":", 1)[0]
        new = self.m.new_dotted(target, strict=False)
        if new is None:
            return node
        self.n += 1
        return _with_value(node, new + value[len(target):])

    def leave_Call(self, original, updated):
        kind, _nodes = self._context(original)
        if kind is None:
            return updated
        args = list(updated.args)
        if kind == "dotted":
            func = _dotted(updated.func) or ""
            if func.endswith("patch.dict"):
                d = args[1].value
                elements = [el.with_changes(key=self._rewrite_dotted_node(el.key))
                            if isinstance(el, cst.DictElement) else el for el in d.elements]
                args[1] = args[1].with_changes(value=d.with_changes(elements=elements))
            else:
                args[0] = args[0].with_changes(value=self._rewrite_dotted_node(args[0].value))
            return updated.with_changes(args=args)
        if kind == "reader":
            value = _plain_string(args[0].value)
            new = self.m.new_path(value)
            if new is None or new == value:
                return updated
            self.n += 1
            args[0] = args[0].with_changes(value=_with_value(args[0].value, new))
            return updated.with_changes(args=args)
        run = [_plain_string(a.value) for a in args[1:1 + len(_nodes)]]
        new_run = self.m.rewrite_parts(run)
        if new_run is None or new_run == run:
            return updated
        quote = args[1].value.quote
        tail_comma = args[len(run)].comma
        new_args = [args[0]]
        for i, comp in enumerate(new_run):
            is_last = i == len(new_run) - 1
            comma = tail_comma if is_last else cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
            new_args.append(cst.Arg(value=cst.SimpleString(quote + comp + quote), comma=comma))
        new_args.extend(args[1 + len(run):])
        self.n += 1
        return updated.with_changes(args=new_args)

    def leave_Subscript(self, original, updated):
        if _dotted(updated.value) != "sys.modules" or len(updated.slice) != 1:
            return updated
        el = updated.slice[0]
        if not isinstance(el.slice, cst.Index):
            return updated
        new = self._rewrite_dotted_node(el.slice.value)
        if new is el.slice.value:
            return updated
        return updated.with_changes(slice=[el.with_changes(slice=el.slice.with_changes(value=new))])

    # ROOT / "core" / "x.py" ----------------------------------------------------------------
    def _climbs_to_root(self, expr):
        """`os.path.dirname(...os.path.abspath(__file__))` or `Path(__file__)....parents[k]` that
        lands exactly on the repository root from this file's place."""
        depth = self.path.count("/")
        dirnames = 0
        node = expr
        while isinstance(node, cst.Call) and (_dotted(node.func) or "").endswith("dirname") \
                and node.args:
            dirnames += 1
            node = node.args[0].value
        if dirnames:
            if isinstance(node, cst.Call) and (_dotted(node.func) or "").rsplit(".", 1)[-1] in (
                    "abspath", "realpath") and node.args:
                node = node.args[0].value
            return isinstance(node, cst.Name) and node.value == "__file__" and dirnames == depth + 1
        if isinstance(expr, cst.Subscript) and isinstance(expr.value, cst.Attribute) \
                and expr.value.attr.value == "parents" and len(expr.slice) == 1:
            index = expr.slice[0].slice
            uses_file = "__file__" in cst.Module(body=[]).code_for_node(expr.value)
            if isinstance(index, cst.Index) and isinstance(index.value, cst.Integer) and uses_file:
                return int(index.value.value) == depth
        return False

    def _base_ok(self, expr):
        """`ROOT`, `module.ROOT`, `Path(ROOT)`, or a climb from `__file__` that ends on the root."""
        if self._climbs_to_root(expr):
            return True
        if isinstance(expr, cst.Call) and len(expr.args) == 1 \
                and (_dotted(expr.func) or "").rsplit(".", 1)[-1] in JOIN_CALLS:
            expr = expr.args[0].value
            if self._climbs_to_root(expr):
                return True
        name = _dotted(expr)
        return name is not None and (name.rsplit(".", 1)[-1] in ROOT_NAMES or name in self.local_roots)

    def visit_Assign(self, node):
        # `root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`: a local name
        # for the repository root is a root-like base from then on
        if len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name) \
                and self._climbs_to_root(node.value):
            self.local_roots.add(node.targets[0].target.value)

    def visit_BinaryOperation(self, node):
        if isinstance(node.operator, cst.Divide):
            self.div_depth += 1
            if self.div_depth == 1:
                chain = _flatten_div(node)
                if self._base_ok(chain[0]):
                    for part in chain[1:]:
                        if _plain_string(part) is None:
                            break
                        self.decided.add(id(part))

    def leave_BinaryOperation(self, original, updated):
        if not isinstance(original.operator, cst.Divide):
            return updated
        self.div_depth -= 1
        if self.div_depth:
            return updated
        chain = _flatten_div(updated)
        if len(chain) < 2 or not self._base_ok(chain[0]):
            return updated
        run = []
        for part in chain[1:]:
            value = _plain_string(part)
            if value is None:
                break
            run.append(value)
        new_run = self.m.rewrite_parts(run)
        if new_run is None or new_run == run:
            return updated
        quote = chain[1].quote
        node = chain[0]
        for comp in new_run:
            node = cst.BinaryOperation(left=node, operator=updated.operator,
                                       right=cst.SimpleString(quote + comp + quote))
        for rest in chain[1 + len(run):]:
            node = cst.BinaryOperation(left=node, operator=updated.operator, right=rest)
        self.n += 1
        # `(ROOT / "x").read_text()`: the chain's own parentheses stay
        return node.with_changes(lpar=updated.lpar, rpar=updated.rpar)

    def leave_FormattedStringText(self, original, updated):
        """The literal text of an f-string (a subprocess script with `{ROOT!r}` holes)."""
        if self.path.startswith(IMPLICIT_REFERENCE_PREFIXES) and "import" in updated.value:
            raw, k = self.m.rewrite_code_text(updated.value)
            if k:
                self.n += k
                return updated.with_changes(value=raw)
        return updated

    # what was left alone -------------------------------------------------------------------
    def leave_SimpleString(self, original, updated):
        """Outside the explicit contexts: in tests and scripts a string that names a real module
        (or a name a module defines) is a reference to it and is rewritten; in application code
        it is only reported."""
        if id(original) in self.decided:
            return updated
        if self.path.startswith(IMPLICIT_REFERENCE_PREFIXES) and "import" in original.value:
            # code a test or script runs or parses: `"from core import db\n"`, `"import core.{x}"`
            raw, k = self.m.rewrite_code_text(original.value)
            if k:
                self.n += k
                return updated.with_changes(value=raw)
        value = _plain_string(original)
        if value is None or "\n" in value or " " in value.strip():
            return updated
        target = value.split(":", 1)[0]
        if DOTTED_RE.match(target) and self.m.new_dotted(target, strict=True):
            if self.path.startswith(IMPLICIT_REFERENCE_PREFIXES):
                self.n += 1
                return _with_value(updated, self.m.new_dotted(target, strict=True) + value[len(target):])
            self._note(original, "dotted", value)
        elif "/" in value and self.m.new_path(_file_of_ref(value.strip())):
            # a moved package's bare directory (`"modules/"`) is also the prefix of the module
            # assets' public names (`name.startswith("modules/")`): a person decides
            file = _file_of_ref(value.strip())
            bare_package_dir = self.m.stage == "app" and file.strip("/") in self.m.renames
            if self.path.startswith(IMPLICIT_REFERENCE_PREFIXES) and self.m.is_code_path(file) \
                    and not bare_package_dir:
                self.n += 1
                return _with_value(updated, value.replace(file, self.m.new_path(file), 1))
            self._note(original, "path", value)
        elif value in self.m.root_assets:
            self._note(original, "root-asset-name", value)
        return updated


def rewrite_python(mover, path, source):
    wrapper = MetadataWrapper(cst.parse_module(source), unsafe_skip_copy=True)
    positions = wrapper.resolve(PositionProvider)
    py = PyRewriter(mover, path, positions)
    out = wrapper.module.visit(py)
    return out.code, py.n


# ---------------------------------------------------------------------------------------
# other text files
# ---------------------------------------------------------------------------------------
JS_JOIN_RE = re.compile(
    r"\b(?:join|resolve)\(\s*([A-Za-z_$][A-Za-z0-9_$.]*)\s*,"
    r"((?:\s*(['\"])[^'\"\n]*\3\s*,?)+)")
JS_LIT_RE = re.compile(r"(['\"])([^'\"\n]*)\1")
# a string or template literal of a JavaScript test (Python it hands to a subprocess)
# (DOTALL: a backslash ending a line of the code — Python's own continuation — escapes the newline)
JS_CODE_LITERAL_RE = re.compile(r"`(?:[^`\\]|\\.)*`|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'", re.S)
MD_LINK_RE = re.compile(r"(\]\()([^)\s#]+)((?:#[^)\s]*)?\))")
TOKEN_RE = re.compile(r"(?<![\w./@:-])([A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.*?\[\]{}-]*)+)")
MANIFEST_STR_RE = re.compile(r"\"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\"")
CODE_SPAN_RE = re.compile(r"(`+)([^`\n]+)\1")


def rewrite_js(mover, path, text):
    n = 0

    def join_sub(m):
        nonlocal n
        if m.group(1).rsplit(".", 1)[-1] not in ROOT_NAMES:
            return m.group(0)
        lits = [lm.group(2) for lm in JS_LIT_RE.finditer(m.group(2))]
        new_run = mover.rewrite_parts(lits)
        if new_run is None or new_run == lits:
            return m.group(0)
        quote = JS_LIT_RE.search(m.group(2)).group(1)
        head = m.group(0)[: m.start(2) - m.start(0)]
        trailing = "," if m.group(2).rstrip().endswith(",") else ""
        n += 1
        return head + " " + ", ".join(quote + c + quote for c in new_run) + trailing

    text = JS_JOIN_RE.sub(join_sub, text)

    def reader_sub(m):
        nonlocal n
        new = mover.new_path(m.group(3))
        if new is None or new == m.group(3):
            return m.group(0)
        n += 1
        return "%s(%s%s%s" % (m.group(1), m.group(2), new, m.group(2))

    if path.startswith(IMPLICIT_REFERENCE_PREFIXES):
        text = JS_READER_RE.sub(reader_sub, text)
    if path.startswith(DISK_RELATIVE_JS_PREFIXES):
        # `import css from "../admin-direct.css?raw"` of a story, `"../core/x.py"` of a test: a
        # path on disk relative to the file (the app's own JavaScript is URL space, never here)
        text, k = _rewrite_relative_literals(mover, path, text)
        n += k
    if path.startswith(("test/", "tests/")):

        def code_sub(m):
            nonlocal n
            if "import" not in m.group(0):
                return m.group(0)
            new, k = mover.rewrite_code_text(m.group(0))
            n += k
            return new

        text = JS_CODE_LITERAL_RE.sub(code_sub, text)
    if path.startswith(DISK_RELATIVE_JS_PREFIXES + IMPLICIT_REFERENCE_PREFIXES):
        # a path literal of a JavaScript test, story or script is a file on disk (their public
        # URLs start with `/`): a moved one follows the file; a bare root web asset name may
        # be a public name, so a person decides on those
        def literal_sub(m):
            nonlocal n
            value = m.group(2)
            if DOTTED_RE.match(value):
                # a manifest's module name a test compares (`"modules.intake.x_screen"`); an i18n
                # key that looks dotted names no module and stays (strict)
                dotted = mover.new_dotted(value, strict=True)
                if dotted:
                    n += 1
                    return m.group(1) + dotted + m.group(1)
            new = mover.new_path(value)
            if new and "/" in value:
                n += 1
                return m.group(1) + new + m.group(1)
            if new or (mover.root_assets and value in mover.root_assets):
                mover.report.note(path, text.count("\n", 0, m.start()) + 1, "js-path", value)
            return m.group(0)

        text = JS_LIT_RE.sub(literal_sub, text)
    return text, n


def _rewrite_relative_literals(mover, path, text):
    """`"../core/x.py"` in a file under test/ -> the same target seen from the file's new place."""
    here_old = posixpath.dirname(path)
    here_new = posixpath.dirname(plan.map_path(mover.stage, path) or path)
    n = 0

    def sub(m):
        nonlocal n
        value = m.group(2)
        if not value.startswith("../"):
            return m.group(0)
        target = posixpath.normpath(posixpath.join(here_old, value))
        bare = target.split("?", 1)[0].split("#", 1)[0]  # `"../admin-app.js?raw"` of a story
        if not (mover.old.exists(bare) or mover.old.exists(posixpath.dirname(bare))):
            # not a path to anything (a string another file is searched for, as in a story's own
            # `"../static/x.mjs"` a test replaces): leave it as written
            return m.group(0)
        new_target = mover.new_path(target) or (target if here_new != here_old else None)
        if new_target is None:
            return m.group(0)
        rel = posixpath.relpath(new_target, here_new)
        if value.endswith("/") and not rel.endswith("/"):
            rel += "/"  # `new URL("../../patient_web/", ...)` stays a directory URL
        if rel == value:
            return m.group(0)
        n += 1
        return m.group(1) + rel + m.group(1)

    return JS_LIT_RE.sub(sub, text), n


def rewrite_markdown(mover, path, text):
    """Relative links resolved from the document's old place and re-pointed from its new place;
    paths inside `code` spans rewritten; prose left as written."""
    here_old = posixpath.dirname(path)
    here_new = posixpath.dirname(plan.map_path(mover.stage, path) or path)
    n = 0

    def link(m):
        nonlocal n
        target = m.group(2)
        if "://" in target or target.startswith(("mailto:", "/", "#")):
            return m.group(0)
        old_abs = posixpath.normpath(posixpath.join(here_old, target))
        if old_abs.startswith(".."):
            return m.group(0)
        new_abs = mover.new_path(old_abs)
        if new_abs is None:
            if here_new == here_old or not mover.old.exists(old_abs):
                return m.group(0)
            new_abs = old_abs
        rel = posixpath.relpath(new_abs, here_new or ".")
        if rel == posixpath.normpath(target):
            return m.group(0)
        n += 1
        return m.group(1) + rel + m.group(3)

    def span(m):
        nonlocal n
        inner, k = _sub_tokens(mover, m.group(2))
        n += k
        return m.group(1) + inner + m.group(1)

    text = MD_LINK_RE.sub(link, text)
    text = CODE_SPAN_RE.sub(span, text)
    return text, n


def _sub_tokens(mover, text):
    n = 0

    def tok(m):
        nonlocal n
        new = mover.new_path(m.group(1))
        if new is None:
            return m.group(0)
        n += 1
        return new

    return TOKEN_RE.sub(tok, text), n


def rewrite_root_anchored(mover, path, text):
    """`.gitattributes` / `.gitignore` / `.dockerignore`: `/design-qa.md`, `/test/e2e/*-tmp.mjs`,
    and unanchored `test/fixtures/...` lines."""
    n = 0

    def anchored(m):
        nonlocal n
        token = m.group(2)
        new = mover.new_path(token)
        if new is None and plan.map_path(mover.stage, _glob_free_prefix(token)) \
                and mover.old.exists(_glob_free_prefix(token)):
            new = plan.map_path(mover.stage, _glob_free_prefix(token)) + token[len(_glob_free_prefix(token)):]
        if new is None:
            return m.group(0)
        n += 1
        return m.group(1) + new

    text = re.sub(r"(?m)^(!?/)([^\s#]+)", anchored, text)
    lines = []
    for line in text.split("\n"):
        if line.startswith(("/", "!/", "#")):
            lines.append(line)
            continue
        new_line, k = _sub_tokens(mover, line)
        n += k
        lines.append(new_line)
    return "\n".join(lines), n


def _manifest_module_names(text):
    """The strings a module manifest uses as module names: `module`, `entrypoint` and `after` values (any
    depth). Everything else — `label_key: "ai.register.title"` above all — is not a module."""
    try:
        data = json.loads(text)
    except ValueError:
        return frozenset()
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("module", "entrypoint") and isinstance(value, str):
                    found.add(value)
                elif key == "after":
                    if isinstance(value, str):
                        found.add(value)
                    elif isinstance(value, dict):
                        found.update(v for v in value.values() if isinstance(v, str))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return frozenset(found)


MANIFEST_FIELD_RE = re.compile(r"(\"(?:module|entrypoint|after)\"\s*:\s*)(\"[^\"]*\"|\{[^{}]*\})")


def rewrite_manifest(mover, path, text):
    """Module names of a manifest: only the values of `module`, `entrypoint` and `after` (an `after` may map
    methods to names). A translation key that looks like a module (`label_key`) never changes."""
    n = 0
    names = _manifest_module_names(text)

    def name_sub(m):
        nonlocal n
        if m.group(1) not in names:
            return m.group(0)
        new = mover.new_dotted(m.group(1), strict=False)
        if new is None:
            return m.group(0)
        n += 1
        return '"%s"' % new

    def field_sub(m):
        return m.group(1) + MANIFEST_STR_RE.sub(name_sub, m.group(2))

    return MANIFEST_FIELD_RE.sub(field_sub, text), n


FILE_REF_RE = re.compile(r"^([^\s:]+\.[A-Za-z0-9]+):[A-Za-z_][A-Za-z0-9_.]*$")


def _file_of_ref(value):
    """`"admin_panel/x_router.py:native_slots"` (a file and a function in it) -> the file; else the value."""
    m = FILE_REF_RE.match(value)
    return m.group(1) if m else value


JSON_REF_RE = re.compile(r"\"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)((?::[A-Za-z_][A-Za-z0-9_.]*)?)\"")


def rewrite_json_refs(mover, path, text):
    """Golden / ratchet JSON of tests and scripts: `"core.db"`, `"core.db:connect"` that name a
    real module (strictly: an i18n key such as `"ai.title"` is left as it is)."""
    n = 0

    def sub(m):
        nonlocal n
        new = mover.new_dotted(m.group(1), strict=True)
        if new is None:
            return m.group(0)
        n += 1
        return '"%s%s"' % (new, m.group(2))

    return JSON_REF_RE.sub(sub, text), n


def rewrite_text_file(mover, old_path, text):
    text, n = _rewrite_by_kind(mover, old_path, text)
    if mover.stage == "tests":
        text, k = _sub_test_dir(mover, old_path, text)
        n += k
    return text, n


SERVED_TREES = ("static/", "templates/", "vendor/", "webdata/")


def _served(path):
    """A file the site serves as it is: the asset and template trees, and in the code package
    everything but its Python, documents and manifests (module assets, module templates)."""
    if path.startswith(SERVED_TREES):
        return True
    top = path.split("/", 1)[0]
    in_code = top == "app" or top in plan.PACKAGES
    return in_code and "/" in path and not path.endswith((".py", ".md", ".json"))


def _sub_test_dir(mover, path, text):
    """The tests stage renames one whole directory, so a `test/...` token can only mean it: its
    paths in comments, docstrings, prose and globs (`test/test_*.py`) follow it too. A nested
    project with a `test/` of its own (a browser extension) keeps its relative names, and a file the
    site serves keeps every byte (a comment of `human-rounds.css` is in its fingerprint and its URL)."""
    nested = [d[: -len("/test")] for d in mover.old.dirs if d.endswith("/test")]
    if any(path.startswith(d + "/") for d in nested) or _served(path):
        return text, 0
    n = 0

    def tok(m):
        nonlocal n
        token = m.group(1)
        if not token.startswith("test/"):
            return m.group(0)
        # a path a nested project has under its own test/ and ours does not (`test/x.html`, read relative
        # to that project from one of our tests) is not ours; a placeholder (`test/test_x.py`) or a glob is
        plain = token.rstrip(".,;:")
        if not mover.old.exists(plain) and any(mover.old.exists(d + "/" + plain) for d in nested):
            return m.group(0)
        n += 1
        return "tests/" + token[len("test/"):]

    def start(m):
        nonlocal n
        n += 1
        return m.group(1) + "tests"

    text = TOKEN_RE.sub(tok, text)
    # `unittest discover -s test` names the directory without a slash
    text = re.sub(r"((?:\s|^)(?:-s|--start-directory)[ =])test(?![\w./-])", start, text)
    return text, n


def _rewrite_by_kind(mover, old_path, text):
    name = old_path.rsplit("/", 1)[-1]
    if name.endswith(".py"):
        return rewrite_python(mover, old_path, text)
    if name == "module.json":
        return rewrite_manifest(mover, old_path, text)
    if name.endswith((".mjs", ".js", ".cjs")):
        if old_path.startswith((".storybook/", "stories/", "scripts/", "test/", "tests/", ".claude/")) \
                or "/" not in old_path or "/test/" in old_path:
            return rewrite_js(mover, old_path, text)
        return text, 0
    if name.endswith(".md"):
        return rewrite_markdown(mover, old_path, text)
    if name.endswith((".html", ".css", ".tpl", ".txt")):
        return text, 0
    if name in plan.ROOT_ANCHORED_FILES:
        return rewrite_root_anchored(mover, old_path, text)
    if name.endswith(".json"):
        if old_path.startswith(("test/", "tests/", "scripts/", ".claude/")) or "/" not in old_path:
            text, n = rewrite_json_refs(mover, old_path, text)
            text, k = _sub_tokens(mover, text)
            return text, n + k
        return text, 0
    return _sub_tokens(mover, text)


# ---------------------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------------------
def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout


def tracked(root):
    return [p for p in _git(root, "ls-files", "-z").split("\0") if p]


def preflight(root, stage, require_clean=True):
    """None when the stage can run here, else the reason it cannot."""
    if require_clean and _git(root, "status", "--porcelain", "--untracked-files=no").strip():
        return "the working tree has uncommitted changes"
    old = OldTree(tracked(root), root)
    if not any(old.exists(src) for src, _dst in plan.stage_moves(stage)):
        return "stage %r is already applied (none of its sources exist)" % stage
    return None


def plan_rewrites(root, stage):
    """(OldTree, {old path: new text}, Report) — what the stage would write, nothing moved yet."""
    report = Report()
    files = tracked(root)
    old = OldTree(files, root)
    mover = Mover(stage, old, report)
    rewritten = {}
    for f in files:
        if plan.is_skipped(f) or plan.is_historical(f) or not plan.is_text(f):
            continue
        try:
            text = Path(root, f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        try:
            out, n = rewrite_text_file(mover, f, text)
        except cst.ParserSyntaxError as e:
            report.note(f, getattr(e, "raw_line", "?"), "unparsed", "libcst cannot parse it")
            continue
        except Exception as e:  # noqa: BLE001 - one bad file must not hide the others
            report.note(f, "?", "error", "%s: %s" % (type(e).__name__, str(e)[:200]))
            continue
        if n and out != text:
            rewritten[f] = out
            report.count(plan.map_path(stage, f) or f, n)
    return old, rewritten, report


def run_stage(root, stage, dry_run=False):
    """Apply one stage in `root`; return its Report."""
    old, rewritten, report = plan_rewrites(root, stage)
    if dry_run:
        return report
    for src, dst in plan.stage_moves(stage):
        if not old.exists(src):
            report.note(src, "-", "missing", "nothing to move")
            continue
        parent = posixpath.dirname(dst)
        if parent:
            os.makedirs(os.path.join(root, parent), exist_ok=True)
        _git(root, "mv", src, dst)
    for gone in plan.DELETES[stage]:
        if gone in old.files:
            _git(root, "rm", "-q", gone)
    for created, content in plan.CREATES[stage].items():
        Path(root, created).write_text(content, encoding="utf-8")
        _git(root, "add", created)
    for f, out in rewritten.items():
        target = plan.map_path(stage, f) or f
        Path(root, target).write_text(out, encoding="utf-8")
        _git(root, "add", target)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="One mechanical stage of the layout move.")
    parser.add_argument("--stage", required=True, choices=plan.STAGES)
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", help="write the full report here (default: stdout)")
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = parser.parse_args(argv)
    if cst is None:
        print("move.py needs libcst: pip install -r scripts/requirements-ci.txt", file=sys.stderr)
        return 2
    root = os.path.abspath(args.root)
    problem = preflight(root, args.stage, require_clean=not args.dry_run)
    if problem:
        print("move.py: " + problem, file=sys.stderr)
        return 1
    text = run_stage(root, args.stage, dry_run=args.dry_run).render()
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
        print(text.splitlines()[0])
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
