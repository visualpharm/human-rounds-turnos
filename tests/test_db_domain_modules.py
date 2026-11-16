"""test_db_domain_modules.py — `core/db.py` is the connection, nothing else; every other name is reached through its owner.

`core/db.py` used to hold the whole persistence layer; the statements went to `core/db_<domain>.py` modules but stayed
reachable as `db.<name>` through a re-export, so the code, the tests and the domain modules themselves all went through
one facade (the domain functions even called each other as `db.<sibling>()`). The facade is gone (hospital TASK-57,
2026-10-30). This file pins the shape:

* `core/db.py` defines only the connection API (`CONNECTION_API`: the pool, `_connect`, the web timeouts, the schema
  status), re-exports nothing and imports no domain module;
* nobody reaches a name through `db` that another module owns: every name of the lower layers and of the domain modules is
  forbidden outright. Every way round is a hit: an alias (`from app.core import db as x`), `import app.core.db`,
  `from app.core.db import name`, `getattr`/`setattr`/`patch.object`, `patch("core.db.name")`, a `(db, "name")` pair, an object
  called `db`/`_db` or an attribute `.db` (the module handed to a function or kept in a context), importing the module by
  its string name. A computed name (`getattr(db, name)`) is forbidden outright. What stays is one ceiling of `CEILINGS`
  that only goes down: the module handed over as a value (it can reach only the connection API now, but the guard cannot
  see what the receiver does with it);
* the modules form layers: `db_values` < `db_wire` < `db_locks` < `db_schema` < `db`, no import (top of the file or
  inside a function) points back up, and the domain modules do not import each other in a circle;
* a module reaches a sibling THROUGH the module (`db_x.f()`), never `from app.core.db_x import f`, so a test patching
  `db_x.f` patches what every other module calls.

No database needed.
"""
import ast
import importlib
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1")

from app.core import db_values as _layer_probe  # noqa: E402

# the package of the persistence layer, wherever the repository layout keeps it
_CORE_PKG = _layer_probe.__package__

DOMAIN_MODULES = ("db_accounts", "db_ai", "db_analytics", "db_auth", "db_backup", "db_booking", "db_catalog",
                  "db_chat", "db_clinical", "db_hsi", "db_operators", "db_reports", "db_slots")
# the layers under `db`, bottom first; each imports only the ones before it
LOWER_LAYERS = ("db_values", "db_wire", "db_locks", "db_schema")

# What `core/db.py` keeps for good.
CONNECTION_API = frozenset("""
DbUnavailable DbTimeout _connect _db_url _get_pool _pools _pools_lock _atexit_registered _broken_urls _BROKEN_URL_TTL
_timeout_error _pool_size _pool_timeout POOL_SIZE_DEFAULT POOL_TIMEOUT_DEFAULT POOL_TIMEOUT_MAX
WEB_STATEMENT_TIMEOUT_DEFAULT WEB_LOCK_TIMEOUT_DEFAULT WEB_TIMEOUT_MAX _web_timeouts _timeout_ms enable_web_timeouts
disable_web_timeouts web_timeouts _set_timeouts _configure_web_connection without_timeouts close_pools _PooledConn
_diagnose_pool_timeout migrations_status SchemaRefused schema_refusal
DbConnectionLost _LOST_CLASSES _unavailable_error _unavailable_errors UNAVAILABLE_ERRORS
DbCommitUnknown _NOT_LOST_STATES
""".split())

# What still goes through `db` and may only shrink: `escape` = the module object stored or handed over as a value (a
# non-literal getattr/setattr/patch.object on it, `dynamic`, has no ceiling: it is zero and must stay zero).
CEILINGS = {
    "escape": 7,
}

# one name of each domain, for the check that every module is complete whatever was imported first
_SAMPLE_NAME = {
    "db_accounts": "create_user", "db_ai": "prompt_save", "db_analytics": "log_search", "db_auth": "passkey_create",
    "db_backup": "backup_journal_commit", "db_booking": "approve", "db_catalog": "catalog_upsert",
    "db_chat": "chat_history", "db_clinical": "interview_bind", "db_hsi": "hsi_outbox_claim",
    "db_operators": "operator_get", "db_reports": "reports_kpis", "db_slots": "hold_acquire",
}

# no module of the persistence layer may grow back into a monolith
MAX_LINES = 1500

_GENERIC_RECEIVERS = ("db", "_db")
_DYNAMIC_CALLS = ("getattr", "setattr", "hasattr", "delattr")
# written in pieces so that this file does not trip its own guard
_FACADE = _CORE_PKG + "." + "db"


def _dotted_name(e):
    """`a.b.c` (a Name/Attribute chain) -> "a.b.c", else None."""
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        left = _dotted_name(e.value)
        return None if left is None else left + "." + e.attr
    return None


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _core(name):
    return os.path.join(ROOT, "app", "core", name + ".py")


def _defined(tree):
    """Names a module defines at its top level (not its imports)."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.Assign):
            out.extend(x.id for t in node.targets for x in ast.walk(t)
                       if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append(node.target.id)
    return [n for n in out if n != "__all__"]


def _owners():
    """name -> module, for every name a facade access can hit: the domains' names and the lower layers' names. A name two
    modules define (a private helper of each) is ambiguous: not listed."""
    seen = {}
    for mod in DOMAIN_MODULES + LOWER_LAYERS:
        for name in set(_defined(ast.parse(_read(_core(mod))))):
            seen.setdefault(name, []).append(mod)
    owners = {name: mods[0] for name, mods in seen.items() if len(mods) == 1}
    for name in CONNECTION_API:
        owners.pop(name, None)
    return owners


# ---- finding the accesses ---------------------------------------------------------------------------------

class Access:
    """`weak`: the name comes from a string (`hasattr(db, "x")`, `patch("db.x")`), so it counts only when it is a name
    that exists — a test may assert that a name is GONE."""

    def __init__(self, lineno, name, kind, weak=False):
        self.lineno, self.name, self.kind, self.weak = lineno, name, kind, weak

    def __repr__(self):
        return "%s:%s(%s)" % (self.lineno, self.name, self.kind)


def facade_accesses(source):
    """Every way `source` reaches the names of `core.db`. `kind` is 'name' (an attribute / literal name / import) or
    'dynamic' (the name is computed), 'escape' (the module stored or passed as a value) or 'import-by-string'."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    aliases = set()
    chain = False
    rebound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _CORE_PKG and node.level == 0:
            aliases.update(a.asname or a.name for a in node.names if a.name == "db")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == _FACADE:
                    if a.asname:
                        aliases.add(a.asname)
                    else:
                        chain = True
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            rebound.add(node.id)
        elif isinstance(node, ast.arg):
            rebound.add(node.arg)
    # a file that also assigns the name (`db = PerModuleDb(...)`) is not talking to the module through it
    aliases -= rebound

    def bound(e):
        if isinstance(e, ast.Name):
            return e.id in aliases
        return (chain and isinstance(e, ast.Attribute) and e.attr == "db"
                and _dotted_name(e.value) == _CORE_PKG)

    def facade_like(e):
        if bound(e):
            return True
        if isinstance(e, ast.Name):
            return e.id in _GENERIC_RECEIVERS
        return isinstance(e, ast.Attribute) and e.attr in _GENERIC_RECEIVERS

    found = []
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and facade_like(node.value):
            found.append((Access(node.lineno, node.attr, "name"), bound(node.value)))
        elif isinstance(node, ast.Tuple) and len(node.elts) == 2 and facade_like(node.elts[0]) \
                and isinstance(node.elts[1], ast.Constant) and isinstance(node.elts[1].value, str):
            # a table of (module, "name") pairs, e.g. what a test patches or watches
            found.append((Access(node.lineno, node.elts[1].value, "name", weak=True), bound(node.elts[0])))
        elif isinstance(node, ast.ImportFrom) and node.module == _FACADE and node.level == 0:
            for a in node.names:
                found.append((Access(node.lineno, a.name, "name"), True))
        elif isinstance(node, ast.Call):
            fn = node.func
            if len(node.args) > 1 and facade_like(node.args[0]):
                second = node.args[1]
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    found.append((Access(node.lineno, second.value, "name", weak=True), bound(node.args[0])))
                elif isinstance(fn, ast.Name) and fn.id in _DYNAMIC_CALLS or \
                        isinstance(fn, ast.Attribute) and fn.attr in ("object", "setattr", "delattr"):
                    found.append((Access(node.lineno, "?", "dynamic"), bound(node.args[0])))
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if bound(arg) and not (node.args and node.args[0] is arg and len(node.args) > 1
                                       and isinstance(node.args[1], ast.Constant)
                                       and isinstance(node.args[1].value, str)):
                    if not (isinstance(fn, ast.Name) and fn.id in _DYNAMIC_CALLS):
                        found.append((Access(node.lineno, "?", "escape"), True))
            # importing the module by its name
            if (isinstance(fn, ast.Attribute) and fn.attr == "import_module" or isinstance(fn, ast.Name)
                    and fn.id == "__import__") and node.args and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value == _FACADE:
                found.append((Access(node.lineno, "?", "import-by-string"), True))
        elif isinstance(node, ast.Assign) and bound(node.value):
            found.append((Access(node.lineno, "?", "escape"), True))
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == _FACADE:
            found.append((Access(node.lineno, "?", "import-by-string"), True))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith(_FACADE + ".") and v[len(_FACADE) + 1:].isidentifier():
                found.append((Access(node.lineno, v[len(_FACADE) + 1:], "name", weak=True), True))
            elif v.startswith("db.") and v[3:].isidentifier() and (aliases or chain):
                found.append((Access(node.lineno, v[3:], "name", weak=True), True))
    return found


# The file that may look at `core.db` itself: this guard.
_EXEMPT = ("test_db_domain_modules.py",)


def _python_files():
    skip = {".git", "node_modules", ".claude", "ci-artifacts", "__pycache__", "playwright-report", "test-results"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".py") and not (f in _EXEMPT and os.path.basename(base) in ("test", "tests")):
                yield os.path.join(base, f)


def _classify(accesses, owners):
    """-> (hits by group {group: [(lineno, name)]}, problems [(lineno, text)])"""
    hits = {}
    problems = []
    for acc, is_bound in accesses:
        if acc.kind == "import-by-string":
            problems.append((acc.lineno, "imports core.db by its string name"))
        elif acc.kind == "dynamic":
            hits.setdefault("dynamic", []).append((acc.lineno, "?"))
        elif acc.kind == "escape":
            hits.setdefault("escape", []).append((acc.lineno, "?"))
        elif acc.name in CONNECTION_API:
            continue
        elif acc.name in owners:
            hits.setdefault(owners[acc.name], []).append((acc.lineno, acc.name))
        elif is_bound and not acc.weak:
            problems.append((acc.lineno, "db.%s is not part of the connection API" % acc.name))
    return hits, problems


def _scan():
    owners = _owners()
    totals = {}
    problems = []
    for path in _python_files():
        hits, probs = _classify(facade_accesses(_read(path)), owners)
        rel = os.path.relpath(path, ROOT)
        for group, rows in hits.items():
            totals.setdefault(group, []).extend("%s:%d %s" % (rel, ln, nm) for ln, nm in rows)
        problems.extend("%s:%d %s" % (rel, ln, text) for ln, text in probs)
    return totals, problems


# ---- the facade is not used -------------------------------------------------------------------------------

class TestNoFacadeUse(unittest.TestCase):
    def test_nothing_reaches_a_name_through_db_that_another_module_owns(self):
        totals, problems = _scan()
        self.assertEqual(problems, [], "this name is not part of core/db.py's API; import the module that owns it")
        errors = []
        for group in sorted(set(totals) | set(CEILINGS)):
            rows = totals.get(group, [])
            ceiling = CEILINGS.get(group, 0)
            if len(rows) > ceiling:
                errors.append("%s: %d accesses through db, at most %d allowed; reach the owner module instead:\n    %s"
                              % (group, len(rows), ceiling, "\n    ".join(sorted(rows)[:25])))
            elif len(rows) < ceiling:
                errors.append("%s: only %d accesses through db are left (ceiling %d): lower CEILINGS[%r] to %d"
                              % (group, len(rows), ceiling, group, len(rows)))
        self.assertEqual(errors, [])

    def test_the_guard_goes_red_on_each_way_to_reach_the_facade(self):
        facade = _FACADE
        owners = {"approve": "db_booking", "_wire": "db_wire"}
        for source, expect in (
                ("from app.core import db\nx = db.approve(1)\n", "db_booking"),
                ("from app.core import db as store\nx = store.approve(1)\n", "db_booking"),
                ("import %s as store\nstore.approve(1)\n" % facade, "db_booking"),
                ("import %s\n%s.approve(1)\n" % (facade, facade), "db_booking"),
                ("from %s import approve\n" % facade, "db_booking"),
                ("from app.core import db\nx = getattr(db, 'approve')\n", "db_booking"),
                ("from app.core import db\npatch.object(db, 'approve')\n", "db_booking"),
                ("from app.core import db\nself._spy(db, 'approve', 1)\n", "db_booking"),
                ("x = patch('%s.approve')\n" % facade, "db_booking"),
                ("from app.core import db\nx = patch('db.approve')\n", "db_booking"),
                ("def f(db):\n    return db.approve(1)\n", "db_booking"),
                ("def f(ctx):\n    return ctx.db.approve(1)\n", "db_booking"),
                ("from app.core import db\nx = getattr(db, name)\n", "dynamic"),
                ("from app.core import db\nsetattr(db, name, 1)\n", "dynamic"),
                ("from app.core import db\nself.db = db\n", "escape"),
                ("from app.core import db\nrun(db, 1)\n", "escape"),
        ):
            with self.subTest(source=source):
                hits, problems = _classify(facade_accesses(source), owners)
                self.assertIn(expect, hits, (source, hits, problems))
        hits, problems = _classify(facade_accesses("import importlib\nm = importlib.import_module('%s')\n" % facade), owners)
        self.assertTrue(problems)
        hits, problems = _classify(facade_accesses("from app.core import db\nx = db.not_a_db_name(1)\n"), owners)
        self.assertEqual(len(problems), 1)
        hits, problems = _classify(facade_accesses("from app.core import db\nx = db.os\n"), owners)
        self.assertEqual(len(problems), 1)

    def test_the_guard_lets_the_connection_api_and_unrelated_objects_through(self):
        owners = {"approve": "db_booking"}
        for source in (
                "from app.core import db\nwith db._connect() as conn:\n    pass\n",
                "from app.core import db\ntry:\n    pass\nexcept db.DbUnavailable:\n    pass\n",
                "from app.core import db\npatch.object(db, '_connect')\n",
                "from app.core import db\ndb.close_pools()\n",
                "from app.core import db_booking\ndb_booking.approve(1)\n",
                "db = make()\ndb.set_up()\n",
                "def f(db):\n    return db.test_url\n",
        ):
            with self.subTest(source=source):
                hits, problems = _classify(facade_accesses(source), owners)
                self.assertEqual((hits, problems), ({}, []), source)


# ---- what core/db.py defines ------------------------------------------------------------------------------

class TestWhatDbDefines(unittest.TestCase):
    def test_db_defines_only_the_connection_api(self):
        extra = sorted(set(_defined(ast.parse(_read(_core("db"))))) - CONNECTION_API)
        self.assertEqual(extra, [], "core/db.py is the connection; put these in the module that owns them")

    def test_db_defines_every_name_of_the_connection_api(self):
        gone = sorted(CONNECTION_API - set(_defined(ast.parse(_read(_core("db"))))))
        self.assertEqual(gone, [], "not in core/db.py any more: delete it from CONNECTION_API")

    def test_a_name_is_defined_in_exactly_one_module_of_the_layer(self):
        seen = {}
        dup = []
        for mod in ("db",) + LOWER_LAYERS + DOMAIN_MODULES:
            for name in set(_defined(ast.parse(_read(_core(mod))))):
                # a private helper of two domains is two different helpers; a public name, or one `db` also defines,
                # must have one owner
                if name in seen and (not name.startswith("_") or "db" in (seen[name], mod)):
                    dup.append("%s is defined in %s and %s" % (name, seen[name], mod))
                seen.setdefault(name, mod)
        self.assertEqual(dup, [])

    def test_no_module_of_the_layer_is_bigger_than_the_limit(self):
        big = []
        for mod in ("db",) + LOWER_LAYERS + DOMAIN_MODULES:
            lines = _read(_core(mod)).count("\n")
            if lines > MAX_LINES:
                big.append("%s: %d lines" % (mod, lines))
        self.assertEqual(big, [], "split it by domain instead of growing it (limit %d lines)" % MAX_LINES)


# ---- layers: no import cycle ------------------------------------------------------------------------------

def _sibling_imports(source, known):
    """The `core/db*.py` modules `source` imports, at the top of the file or inside any function."""
    out = set()
    prefix = _CORE_PKG + "."

    def sibling(dotted):
        if dotted and dotted.startswith(prefix):
            head = dotted[len(prefix):].split(".", 1)[0]
            if head in known:
                return head
        return None

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module == _CORE_PKG:
                out.update(a.name for a in node.names if a.name in known)
            elif sibling(node.module):
                out.add(sibling(node.module))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if sibling(a.name):
                    out.add(sibling(a.name))
    return out


def _find_cycle(graph):
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
    known = set(("db",) + LOWER_LAYERS + DOMAIN_MODULES)
    return {name: _sibling_imports(_read(_core(name)), known) for name in known}


class TestLayers(unittest.TestCase):
    def test_the_lower_layers_import_only_the_layers_below_them(self):
        graph = _real_graph()
        bad = []
        for i, mod in enumerate(LOWER_LAYERS):
            extra = graph[mod] - set(LOWER_LAYERS[:i])
            if extra:
                bad.append("%s imports %s" % (mod, sorted(extra)))
        self.assertEqual(bad, [])

    def test_db_imports_only_the_lower_layers(self):
        graph = _real_graph()
        self.assertEqual(graph["db"] - set(LOWER_LAYERS), set(), "core/db.py imports no domain module")

    def test_the_domain_modules_do_not_import_each_other_in_a_circle(self):
        graph = {m: deps - {"db"} for m, deps in _real_graph().items() if m != "db"}
        cycle = _find_cycle(graph)
        self.assertIsNone(cycle, "import cycle among the db modules: %s — move the shared piece down a layer"
                          % " -> ".join(cycle or ()))

    def test_the_cycle_check_goes_red_on_a_deliberate_cycle(self):
        known = {"db_a", "db_b", "db_c"}
        sources = {
            "db_a": "from app.core import db_b\n",
            "db_b": "def f():\n    from app.core import db_c  # a lazy import is still an edge\n",
            "db_c": "import app.core.db_a\n",
        }
        graph = {name: _sibling_imports(src, known) for name, src in sources.items()}
        self.assertEqual(_find_cycle(graph), ["db_a", "db_b", "db_c", "db_a"])
        sources["db_c"] = "x = 1\n"
        graph = {name: _sibling_imports(src, known) for name, src in sources.items()}
        self.assertIsNone(_find_cycle(graph))

    def test_a_module_reaches_a_sibling_through_the_module_not_by_name(self):
        # `from core.db_x import f` binds f at import time: a test patching `db_x.f` would no longer reach this
        # module. `from core import db_x` + `db_x.f()` looks it up at call time.
        bad = []
        for path in _python_files():
            for node in ast.walk(ast.parse(_read(path))):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(_CORE_PKG + ".db"):
                    bad.append("%s:%d from %s import ..." % (os.path.relpath(path, ROOT), node.lineno, node.module))
        self.assertEqual(bad, [])


# ---- the modules themselves -------------------------------------------------------------------------------

def _unbound_db_modules(source, filename="<src>"):
    """The `db_*` module names a function of `source` reads as a global that the file never binds (no module-level import
    or assignment): a NameError that only shows when that line runs — typically a script a browser test starts, or a
    helper that used to get the module as a parameter."""
    import symtable
    modules = set(DOMAIN_MODULES) | set(LOWER_LAYERS)
    try:
        tree = ast.parse(source)
        top = symtable.symtable(source, filename, "exec")
    except SyntaxError:
        return []
    bound = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom):
                bound.update(a.asname or a.name for a in sub.names)
            elif isinstance(sub, ast.Import):
                bound.update((a.asname or a.name).split(".")[0] for a in sub.names)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                bound.add(sub.id)
    missing = []

    def walk(table):
        for sym in table.get_symbols():
            if sym.is_global() and sym.is_referenced() and sym.get_name() in modules and sym.get_name() not in bound:
                missing.append("%s reads %s" % (table.get_name(), sym.get_name()))
        for child in table.get_children():
            walk(child)

    walk(top)
    return missing


class TestEveryUseOfAModuleIsImported(unittest.TestCase):
    def test_no_function_reads_a_db_module_its_file_never_imports(self):
        bad = []
        for path in _python_files():
            for what in _unbound_db_modules(_read(path), path):
                bad.append("%s: %s" % (os.path.relpath(path, ROOT), what))
        self.assertEqual(bad, [], "import the db_* module (inside the function if the file avoids an import-time cost)")

    def test_the_check_goes_red_on_a_missing_import_and_not_on_a_present_one(self):
        self.assertEqual(_unbound_db_modules("def f():\n    return db_booking.approve(1)\n"), ["f reads db_booking"])
        for source in ("from app.core import db_booking\ndef f():\n    return db_booking.approve(1)\n",
                       "def f():\n    from app.core import db_booking\n    return db_booking.approve(1)\n",
                       "def f(db_booking):\n    return db_booking.approve(1)\n"):
            self.assertEqual(_unbound_db_modules(source), [], source)


class TestModules(unittest.TestCase):
    def test_any_module_can_be_imported_first(self):
        # whatever is imported first, the whole layer ends up complete (a name of every module is there)
        script = ("import sys; sys.path.insert(0, %r); import app.core.{first}; "
                  "from app.core import db, db_values, db_wire, db_locks, db_schema; "
                  "from app.core import {domains}; "
                  "assert callable(db_values._coerce_dt) and callable(db_wire._wire) and callable(db_locks._lock_key) "
                  "and callable(db_schema._ensure_schema) and callable(db._connect); "
                  "mine = [n for m, n in %r.items() if not hasattr(sys.modules['app.core.' + m], n)]; assert not mine, mine; "
                  "assert not any(hasattr(db, n) for n in %r.values()), 'a domain name is back on db'; "
                  "assert db_values._ART_SQL and db_values._ART")
        for first in ("db",) + LOWER_LAYERS + DOMAIN_MODULES:
            done = subprocess.run([sys.executable, "-I", "-c",
                                   (script.format(first=first, domains=", ".join(DOMAIN_MODULES))
                                    % (ROOT, _SAMPLE_NAME, _SAMPLE_NAME))],
                                  capture_output=True, text=True, timeout=120,
                                  env={"PATH": os.environ.get("PATH", ""), "TURNOS_FAKE_AI": "1"})
            self.assertEqual(done.returncode, 0, "importing %s first: %s" % (first, done.stderr[-800:]))

    def test_every_global_a_function_reads_exists_in_its_module(self):
        import builtins
        import symtable
        problems = []
        for name in LOWER_LAYERS + ("db",):
            module = importlib.import_module("%s.%s" % (_CORE_PKG, name))
            text = _read(_core(name))

            def walk(table):
                for sym in table.get_symbols():
                    if sym.is_global() and sym.is_referenced() and not hasattr(builtins, sym.get_name()) \
                            and not hasattr(module, sym.get_name()):
                        problems.append("%s: %s reads the unknown global %s" % (name, table.get_name(), sym.get_name()))
                for child in table.get_children():
                    walk(child)

            walk(symtable.symtable(text, _core(name), "exec"))
        self.assertEqual(problems, [])


class TestPlumbingIsLookedUpAtCallTime(unittest.TestCase):
    def test_a_patch_on_db_reaches_the_domain_functions(self):
        from app.core import db

        owners = _owners()

        def owned(name):
            return getattr(importlib.import_module("%s.%s" % (_CORE_PKG, owners[name])), name)

        class Boom(Exception):
            pass

        with patch.object(db, "_connect", side_effect=Boom):
            for name, args in (("prompts_all", ()), ("catalog_list", ("centros",)), ("backup_journal_map", ()),
                               ("claim_daily_alert", ("k", "2026-01-01")), ("chat_history", ("web", "k")),
                               ("hsi_metrics", ()), ("interview_get", (1,)), ("red_alert_count", ()),
                               ("list_approved_day", ("2030-01-01",)), ("count_appointments_of", ("servicios", "x")),
                               ("reports_kpis", ())):
                with self.subTest(function=name):
                    with self.assertRaises(Boom):
                        owned(name)(*args)
            # the analytics writers swallow the failure — but only after reaching the patched `_connect`
            self.assertEqual(owned("top_searches")(), [])
            self.assertIsNone(owned("log_search")("q", 1))


if __name__ == "__main__":
    unittest.main()
