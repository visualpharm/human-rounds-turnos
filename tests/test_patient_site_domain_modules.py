"""test_patient_site_domain_modules.py — the page and data code of the public site is split by domain, and it is wired directly.

The old `patient_web/patient_site.py` (7000+ lines) is gone. Its code lives in `patient_web/site_*.py`, one module per
domain, and every caller imports the module that OWNS what it uses: `from app.patient_web import site_catalog` and
`site_catalog._services()`. There is no facade and no re-export. This file pins that shape:

* nothing in the repository imports the old facade module or calls `<facade>.<name>`;
* the domain modules form layers: an import (at the top of a file or inside a function) never points back up, so
  there is no import cycle among them, and `site_base` imports none of them;
* a module reaches a sibling THROUGH the module (`site_x.f()`), never with `from app.patient_web.site_x import f`, so a
  test patching `site_x.f` patches what every other module calls;
* every global a function reads exists in its own module, a name is defined in exactly one module, any module can be
  imported first, and none grows back into a monolith.

No database needed.
"""
import ast
import builtins
import importlib
import os
import subprocess
import symtable
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1")

PKG_DIR = os.path.join(ROOT, "app", "patient_web")
BASE = "site_base"

# no module of the public site may grow back into a monolith (the audit's limit was ~1500 lines)
MAX_LINES = 1500

from app import patient_web as _pw_pkg  # noqa: E402

# the package of the public site, wherever the repository layout keeps it
_PW_PKG = _pw_pkg.__name__

# the facade that no longer exists; written in pieces so this file does not trip its own guard
_FACADE = "patient_" + "site"
_FACADE_MODULE = _PW_PKG + "." + _FACADE


def _site_modules():
    return sorted(f[:-3] for f in os.listdir(PKG_DIR) if f.startswith("site_") and f.endswith(".py"))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _module_source(name):
    path = os.path.join(PKG_DIR, name + ".py")
    return path, _read(path)


# ---- the guard against the facade -------------------------------------------------------------------------

def _facade_offences(source):
    """What in `source` names the removed facade: imports of it and uses of the module object.
    The route owner label `<facade>_router`, email domains and prose are different identifiers/strings."""
    found = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == _FACADE_MODULE or (node.level and node.module == _FACADE):
                found.append((node.lineno, "imports from the facade"))
            if node.module == _PW_PKG and any(a.name == _FACADE for a in node.names):
                found.append((node.lineno, "imports the facade module"))
        elif isinstance(node, ast.Import):
            if any(a.name == _FACADE_MODULE for a in node.names):
                found.append((node.lineno, "imports the facade module"))
        elif isinstance(node, ast.Name) and node.id == _FACADE:
            found.append((node.lineno, "uses the name %s" % _FACADE))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == _FACADE_MODULE:
            found.append((node.lineno, "names the facade module in a string"))
    return found


def _python_files():
    skip = {".git", "node_modules", ".claude", "ci-artifacts", "__pycache__", "playwright-report", "test-results"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


class TestNoFacade(unittest.TestCase):
    def test_the_facade_file_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(PKG_DIR, _FACADE + ".py")))

    def test_nothing_in_the_repository_imports_or_calls_the_facade(self):
        bad = []
        for path in _python_files():
            for lineno, what in _facade_offences(_read(path)):
                bad.append("%s:%d %s" % (os.path.relpath(path, ROOT), lineno, what))
        self.assertEqual(bad, [], "import the module that owns the name (patient_web/site_*.py), not a facade")

    def test_the_guard_goes_red_on_each_way_back_in(self):
        for source in (
                "from %s import %s\n" % (_PW_PKG, _FACADE),
                "from %s import catalog, %s as ps\n" % (_PW_PKG, _FACADE),
                "from %s.%s import _services\n" % (_PW_PKG, _FACADE),
                "import %s.%s as ps\n" % (_PW_PKG, _FACADE),
                "x = %s._services()\n" % _FACADE,
                "mod = __import__(%r)\n" % _FACADE_MODULE,
                "from .%s import f\n" % _FACADE,
        ):
            with self.subTest(source=source):
                self.assertTrue(_facade_offences(source), source)

    def test_the_guard_leaves_the_router_and_prose_alone(self):
        for source in (
                "from app.patient_web import %s_router\n" % _FACADE,
                "x = %s_router.router\n" % _FACADE,
                "email = 'op@%s.gob.ar'\n" % _FACADE,
                "label = '%s'  # the route owner label\n" % _FACADE,
        ):
            with self.subTest(source=source):
                self.assertEqual(_facade_offences(source), [], source)


# ---- layers: no import cycle ------------------------------------------------------------------------------

def _sibling_imports(source, known):
    """The `site_*` modules `source` imports, at the top of the file or inside any function."""
    out = set()
    prefix = _PW_PKG + "."

    def sibling(dotted):
        if dotted and dotted.startswith(prefix):
            head = dotted[len(prefix):].split(".", 1)[0]
            if head in known:
                return head
        return None

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module == _PW_PKG:
                out.update(a.name for a in node.names if a.name in known)
            elif sibling(node.module):
                out.add(sibling(node.module))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if sibling(a.name):
                    out.add(sibling(a.name))
    return out


def _find_cycle(graph):
    """A list of modules that import each other in a circle, or None."""
    state = {}
    stack = []

    def visit(node):
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def _real_graph():
    known = set(_site_modules())
    return {name: _sibling_imports(_module_source(name)[1], known) for name in known}


class TestLayers(unittest.TestCase):
    def test_the_site_modules_do_not_import_each_other_in_a_circle(self):
        cycle = _find_cycle(_real_graph())
        self.assertIsNone(cycle, "import cycle among the site modules: %s — move the shared piece down a layer"
                          % " -> ".join(cycle or ()))

    def test_site_base_imports_no_sibling(self):
        self.assertEqual(_real_graph()[BASE], set())

    def test_the_cycle_check_goes_red_on_a_deliberate_cycle(self):
        known = {"site_a", "site_b", "site_c"}
        sources = {
            "site_a": "from app.patient_web import site_b\n",
            "site_b": "def f():\n    from app.patient_web import site_c  # a lazy import is still an edge\n",
            "site_c": "import app.patient_web.site_a\n",
        }
        graph = {name: _sibling_imports(src, known) for name, src in sources.items()}
        self.assertEqual(_find_cycle(graph), ["site_a", "site_b", "site_c", "site_a"])
        sources["site_c"] = "x = 1\n"
        graph = {name: _sibling_imports(src, known) for name, src in sources.items()}
        self.assertIsNone(_find_cycle(graph))

    def test_a_module_reaches_a_sibling_through_the_module_not_by_name(self):
        # `from patient_web.site_x import f` binds f at import time: a test patching `site_x.f` would no longer
        # reach this module. `from patient_web import site_x` + `site_x.f()` looks it up at call time.
        bad = []
        for name in _site_modules():
            _path, text = _module_source(name)
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(_PW_PKG + ".site_"):
                    bad.append("%s:%d from %s import ..." % (name, node.lineno, node.module))
        self.assertEqual(bad, [])


# ---- the modules themselves -------------------------------------------------------------------------------

class TestOwnership(unittest.TestCase):
    def test_a_name_is_defined_in_exactly_one_module(self):
        seen = {}
        dup = []
        for name in _site_modules():
            _path, text = _module_source(name)
            for node in ast.parse(text).body:
                defined = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined = [node.name]
                elif isinstance(node, ast.Assign):
                    defined = [t.id for t in node.targets if isinstance(t, ast.Name)]
                for d in defined:
                    if d in seen:
                        dup.append("%s is defined in %s and %s" % (d, seen[d], name))
                    seen[d] = name
        self.assertEqual(dup, [])

    def test_any_module_can_be_imported_first(self):
        for first in _site_modules():
            done = subprocess.run([sys.executable, "-I", "-c",
                                   "import sys; sys.path.insert(0, %r); import %s.%s" % (ROOT, _PW_PKG, first)],
                                  capture_output=True, text=True, timeout=120,
                                  env={"PATH": os.environ.get("PATH", ""), "TURNOS_FAKE_AI": "1"})
            self.assertEqual(done.returncode, 0, "importing %s first: %s" % (first, done.stderr[-800:]))

    def test_a_patch_on_the_owner_reaches_the_other_modules(self):
        from app.patient_web import site_news, site_shell

        with patch.object(site_shell, "_shell", return_value="PATCHED-SHELL") as shell:
            self.assertEqual(site_news._page_news(), "PATCHED-SHELL")
        self.assertTrue(shell.called)

    def test_every_global_a_function_reads_exists_in_its_module(self):
        # A name that lives in another module and is used bare fails only when that line runs; this finds it
        # without running anything.
        problems = []
        for name in _site_modules():
            module = importlib.import_module("%s.%s" % (_PW_PKG, name))
            path, text = _module_source(name)

            def walk(table):
                for sym in table.get_symbols():
                    if sym.is_global() and sym.is_referenced() and not hasattr(builtins, sym.get_name()) \
                            and not hasattr(module, sym.get_name()):
                        problems.append("%s: %s reads the unknown global %s" % (name, table.get_name(), sym.get_name()))
                for child in table.get_children():
                    walk(child)

            walk(symtable.symtable(text, path, "exec"))
        self.assertEqual(problems, [])


class TestNoMonolith(unittest.TestCase):
    def test_no_module_of_the_patient_site_is_bigger_than_the_limit(self):
        big = []
        for name in _site_modules():
            _path, text = _module_source(name)
            if text.count("\n") > MAX_LINES:
                big.append("%s: %d lines" % (name, text.count("\n")))
        self.assertEqual(big, [], "split it by domain instead of growing it (limit %d lines)" % MAX_LINES)


if __name__ == "__main__":
    unittest.main()
