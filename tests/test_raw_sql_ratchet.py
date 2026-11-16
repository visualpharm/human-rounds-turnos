"""test_raw_sql_ratchet.py — hand-written SQL does not come back (SQLAlchemy Core, stage 7).

Every statement of the application reaches the database through `core/sqlcore.py` (`run`, `prepare`,
`run_many`, `run_ddl`, `run_async`). A `.execute(` / `.executemany(` call anywhere else in the
shipped Python is SQL written by hand: it escapes the schema model, the `col == None` and
compiled-SQL checks of the converted code, and the one place that adapts parameters.

The allowance below is the whole list of files that may still call `.execute(` and how many times:
  * `core/sqlcore.py` — the executor itself (one `execute` per way of running a statement);
  * `tools/msd_pull.py` — reads the SQLite cookie store of the developer's Chrome, not our database.
A file that is not listed may not call it at all, so a NEW file with raw SQL is red at once; a listed
file whose count drops must lower its number here (the ratchet only turns one way). Tests and the
Alembic revisions are outside the scan: a revision is SQL by nature and a test may seed or inspect
a database with plain SQL.

Fix a red result by writing the statement with Core (`docs/architecture/sqlalchemy-core.md`), not by
raising a number.
"""
import ast
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ALLOWED = {
    "app/core/sqlcore.py": 5,
    "scripts/tools/msd_pull.py": 1,
}
_SKIP_DIRS = {"tests", "alembic", "node_modules", "vendor", "static", "stories",
              "__pycache__"}


def count_raw_executes(root):
    """{relative path: number of `.execute(` / `.executemany(` calls} for the shipped Python of `root`."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        top = "" if rel_dir == "." else rel_dir.split(os.sep)[0]
        # the tests and the revisions live at the top level; a hidden directory is tooling (.claude, .git)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not (top == "" and d in _SKIP_DIRS)
                       and d not in ("node_modules", "__pycache__")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), path)
            except (SyntaxError, UnicodeDecodeError):
                continue
            n = sum(1 for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("execute", "executemany"))
            if n:
                found[os.path.relpath(path, root).replace(os.sep, "/")] = n
    return found


def violations(found, allowed=None):
    allowed = _ALLOWED if allowed is None else allowed
    out = []
    for path, n in sorted(found.items()):
        if path not in allowed:
            out.append("%s: %d hand-written `.execute(` call(s); write the statement with Core "
                       "(sqlcore.run / sqlcore.prepare), see docs/architecture/sqlalchemy-core.md" % (path, n))
        elif n > allowed[path]:
            out.append("%s: %d `.execute(` calls, the allowance is %d" % (path, n, allowed[path]))
    for path, n in sorted(allowed.items()):
        if found.get(path, 0) < n:
            out.append("%s: only %d `.execute(` call(s) left, lower its allowance in test_raw_sql_ratchet.py "
                       "(it is %d)" % (path, found.get(path, 0), n))
    return out


class TestRatchet(unittest.TestCase):
    def test_no_hand_written_sql_outside_the_allowance(self):
        self.assertEqual([], violations(count_raw_executes(ROOT)))


class TestTheScan(unittest.TestCase):
    """The guard itself: a new file with raw SQL is red, the allowance only turns one way."""

    def _tree(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, body in files.items():
            path = os.path.join(tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        return tmp.name

    def test_a_new_file_with_raw_sql_is_red(self):
        root = self._tree({"scripts/nuevo.py": "def f(cur):\n    cur.execute('SELECT 1')\n"})
        found = count_raw_executes(root)
        self.assertEqual({"scripts/nuevo.py": 1}, found)
        self.assertEqual(1, len(violations(found, {})))
        self.assertIn("scripts/nuevo.py", violations(found, {})[0])

    def test_executemany_and_a_connection_execute_count_too(self):
        root = self._tree({"core/otro.py": "def f(conn, cur):\n    conn.execute('x')\n    cur.executemany('y', [])\n"})
        self.assertEqual({"core/otro.py": 2}, count_raw_executes(root))

    def test_growing_inside_an_allowed_file_is_red(self):
        found = {"app/core/sqlcore.py": 3}
        self.assertEqual(1, len(violations(found, {"app/core/sqlcore.py": 2})))

    def test_shrinking_below_the_allowance_asks_to_lower_it(self):
        found = {"app/core/sqlcore.py": 1}
        out = violations(found, {"app/core/sqlcore.py": 2})
        self.assertEqual(1, len(out))
        self.assertIn("lower its allowance", out[0])

    def test_tests_revisions_and_hidden_directories_are_not_scanned(self):
        root = self._tree({
            "tests/test_x.py": "def f(cur):\n    cur.execute('SELECT 1')\n",
            "alembic/versions/0001_x.py": "def upgrade(op):\n    op.execute('x')\n",
            ".claude/worktrees/w/core/x.py": "def f(c):\n    c.execute('x')\n",
            "core/limpio.py": "def f():\n    return 1\n",
            "docs/example/test/inner.py": "def f(c):\n    c.execute('x')\n",
        })
        # only a top-level tests/ is skipped: the same name deeper in the tree is shipped code
        self.assertEqual({"docs/example/test/inner.py": 1}, count_raw_executes(root))

    def test_the_real_tree_has_no_file_outside_the_allowance(self):
        found = count_raw_executes(ROOT)
        self.assertEqual(set(_ALLOWED), set(found), "the allowance lists exactly the files that call `.execute(`")


if __name__ == "__main__":
    unittest.main()
