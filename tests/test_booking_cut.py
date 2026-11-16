"""The cut of booking_requests.py by action (TASK-60, 2026-11-06) stays a cut, not a facade.

booking_requests.py keeps what the booking modules share; each route's domain function lives in the module of
its action (`SPLIT`). Three rules keep a test's patch reaching the code it means to reach:

1. booking_requests.py imports none of the split modules: no facade, no re-export under the old names.
2. Nobody binds a name of these modules at import time (`from app.modules.turnos.booking_x import name`): callers
   reach `<module>.<name>` when they call, so a patch on the module that owns the name reaches every caller.
3. Every name lives in one of these modules only, and a test patches it in the module that defines it, not
   in one that merely imports something under that name.

Hermetic: source only, no database.
"""

import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "app.modules.turnos"
SPLIT = ("booking_create", "booking_referrals", "booking_queue", "booking_decisions", "booking_patient",
         "booking_capacity", "booking_emr", "panel_settings")
MODULES = ("booking_requests",) + SPLIT
SKIP_DIRS = {".git", "node_modules", ".claude", "__pycache__", "venv", ".venv"}


def _path(module):
    return os.path.join(ROOT, "app", "modules", "turnos", module + ".py")


def _tree(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _defined(module):
    """Top-level names the module defines itself (functions, classes, assignments), not its imports."""
    out = set()
    for node in _tree(_path(module)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                out.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
    return out


def _python_files(*dirs):
    for top in dirs:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, top)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _aliases(tree):
    """{local name: module} for every import of one of MODULES in the file (any scope)."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == PKG and node.level == 0:
            for a in node.names:
                if a.name in MODULES:
                    out[a.asname or a.name] = a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname and a.name.startswith(PKG + ".") and a.name.rsplit(".", 1)[1] in MODULES:
                    out[a.asname] = a.name.rsplit(".", 1)[1]
    return out


def _patch_targets(tree):
    """(module, name, line) of every patch a test file aims at one of MODULES: patch.object(m, "x"),
    a (m, "x", ...) entry of a patch table, setattr(m, "x", ...), `m.x = ...` and patch("modules.turnos.m.x")."""
    aliases = _aliases(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if re.search(r"(^|\.)(patch|patch\.object|setattr)$", func) and len(node.args) >= 2:
                a0, a1 = node.args[0], node.args[1]
                if isinstance(a0, ast.Name) and a0.id in aliases and isinstance(a1, ast.Constant) \
                        and isinstance(a1.value, str):
                    out.append((aliases[a0.id], a1.value, node.lineno))
            if re.search(r"(^|\.)patch$", func) and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                m = re.fullmatch(r"%s\.(%s)\.([A-Za-z_]\w*)" % (re.escape(PKG), "|".join(MODULES)),
                                 node.args[0].value)
                if m:
                    out.append((m.group(1), m.group(2), node.lineno))
        elif isinstance(node, ast.Tuple) and len(node.elts) >= 3:
            a0, a1 = node.elts[0], node.elts[1]
            if isinstance(a0, ast.Name) and a0.id in aliases and isinstance(a1, ast.Constant) \
                    and isinstance(a1.value, str):
                out.append((aliases[a0.id], a1.value, node.lineno))
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) \
                and isinstance(node.value, ast.Name) and node.value.id in aliases:
            out.append((aliases[node.value.id], node.attr, node.lineno))
    return out


class TestNoFacade(unittest.TestCase):
    def test_booking_requests_imports_no_split_module(self):
        offenders = []
        for node in ast.walk(_tree(_path("booking_requests"))):
            if isinstance(node, ast.ImportFrom) and node.module == PKG:
                offenders += [a.name for a in node.names if a.name in SPLIT]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(PKG + "."):
                if node.module.rsplit(".", 1)[1] in SPLIT:
                    offenders.append(node.module)
            elif isinstance(node, ast.Import):
                offenders += [a.name for a in node.names if a.name.rsplit(".", 1)[-1] in SPLIT]
        self.assertEqual(offenders, [], "booking_requests.py must not import the modules cut out of it")

    def test_every_name_lives_in_one_module(self):
        seen = {}
        clashes = []
        for module in MODULES:
            for name in _defined(module):
                if name.startswith("__"):
                    continue
                if name in seen:
                    clashes.append((name, seen[name], module))
                seen[name] = module
        self.assertEqual(clashes, [])


class TestNamesAreReachedAtCallTime(unittest.TestCase):
    def test_nobody_imports_a_name_out_of_these_modules(self):
        offenders = []
        for path in _python_files("."):
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "") in \
                        {PKG + "." + m for m in MODULES}:
                    offenders.append("%s:%d" % (os.path.relpath(path, ROOT), node.lineno))
        self.assertEqual(offenders, [], "import the module and call <module>.<name>, never the name itself")

    def test_split_modules_reach_booking_requests_as_a_module(self):
        for module in SPLIT:
            with open(_path(module), encoding="utf-8") as f:
                src = f.read()
            if "bkr." in src:
                self.assertIn("from app.modules.turnos import booking_requests as bkr\n", src, module)


class TestPatchesAimAtTheOwner(unittest.TestCase):
    def test_every_patch_on_these_modules_names_something_the_module_defines(self):
        defined = {m: _defined(m) for m in MODULES}
        owner = {}
        for m in MODULES:
            for name in defined[m]:
                owner[name] = m
        wrong = []
        for path in _python_files("tests"):
            for module, name, line in _patch_targets(_tree(path)):
                if name not in defined[module]:
                    where = owner.get(name, "no module of the cut")
                    wrong.append("%s:%d patches %s.%s (it lives in %s)" % (
                        os.path.relpath(path, ROOT), line, module, name, where))
        self.assertEqual(wrong, [])

    def test_the_census_sees_the_patches(self):
        # The census above is only as good as its parser: it must find the patch table of the create steps
        # and the string targets, or an empty result would mean nothing.
        found = set()
        for path in _python_files("tests"):
            found.update((m, n) for m, n, _ in _patch_targets(_tree(path)))
        for target in (("booking_create", "_check_catalog_refs"), ("booking_requests", "_get_user"),
                       ("booking_create", "create_request"), ("booking_queue", "_suggested_doctor"),
                       ("booking_requests", "REFERRALS_DIR")):
            self.assertIn(target, found)


if __name__ == "__main__":
    unittest.main()
