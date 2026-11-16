"""test_dependency_pins.py — the libraries the code calls directly are pinned, and
the private SQLAlchemy API `core/sqlcore.py` stands on is checked, not assumed.

`core/sqlcore.py` compiles a Core statement itself and runs it on the psycopg
cursor of the pool (no `Engine`, no `Session` — README, «SQLAlchemy Core on the
psycopg cursor»). To do the same steps as SQLAlchemy's own execution it uses
three private members — `ClauseElement._compile_w_cache`,
`SQLCompiler._process_parameters_for_postcompile`, `SQLCompiler._bind_processors`
— and one private keyword, `construct_params(_no_postcompile=True)`. Private
means a minor release may rename or reshape them without notice, so:

  - `requirements.txt` pins SQLAlchemy exactly, and that pin must be the
    release `sqlcore.SQLALCHEMY_VERIFIED` names: bumping the pin without
    re-checking the bridge is red here, before any query runs;
  - every private name `sqlcore.py` touches is in `PRIVATE_API` below: a new
    one is red until it is listed (and checked);
  - each listed member still exists on the INSTALLED SQLAlchemy with the shape
    the bridge relies on: a renamed method, a dropped keyword or a changed
    result is red here with the member's name, not as a wrong query later.

The installed version itself is not compared with the pin: a developer's
machine may carry another release, and what matters there is that the API the
bridge calls is still the same — the checks below prove exactly that.

Pydantic is imported by the application (`core/api_body.py`, every
`*/api_models.py`), so it is pinned too, not left to whatever FastAPI's range
resolves to on the day an image is built.
"""
import ast
import inspect
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.sql.compiler import ExpandedState, SQLCompiler  # noqa: E402
from sqlalchemy.sql.elements import ClauseElement  # noqa: E402

from app.core import sqlcore  # noqa: E402
from app.core.schema_model import waitlist  # noqa: E402

# Every private name core/sqlcore.py touches: the members, and the private
# keyword of the public `construct_params`.
PRIVATE_API = {
    "_compile_w_cache",
    "_process_parameters_for_postcompile",
    "_bind_processors",
    "_no_postcompile",
}

_PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s#]+)", re.MULTILINE)


def _pins():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        return {name.lower(): version for name, version in _PIN.findall(f.read())}


def _sqlcore_source():
    with open(os.path.join(ROOT, "app", "core", "sqlcore.py"), encoding="utf-8") as f:
        return f.read()


class TestPins(unittest.TestCase):
    def test_the_libraries_the_code_imports_are_pinned_exactly(self):
        pins = _pins()
        for name in ("fastapi", "pydantic", "sqlalchemy", "alembic", "psycopg"):
            with self.subTest(package=name):
                self.assertIn(name, pins, "%s is imported by the app but not pinned in requirements.txt" % name)

    def test_the_sqlalchemy_pin_is_the_release_the_bridge_was_verified_on(self):
        self.assertEqual(
            _pins()["sqlalchemy"], sqlcore.SQLALCHEMY_VERIFIED,
            "requirements.txt pins another SQLAlchemy than the one core/sqlcore.py was checked "
            "against: re-run this file on the new release, then move SQLALCHEMY_VERIFIED with the pin")


class TestPrivateApiInventory(unittest.TestCase):
    def test_every_private_name_the_bridge_uses_is_listed(self):
        tree = ast.parse(_sqlcore_source())
        used, own = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                if isinstance(node.value, ast.Name) and node.value.id == "self":
                    own.add(node.attr)  # the module's own classes keep their state
                else:
                    used.add(node.attr)
            elif isinstance(node, ast.keyword) and (node.arg or "").startswith("_"):
                used.add(node.arg)
        self.assertEqual(used - own, PRIVATE_API)


class TestPrivateApiShape(unittest.TestCase):
    """The installed SQLAlchemy still has what the bridge calls, in the shape it calls it."""

    def test_compile_w_cache_takes_the_keywords_and_returns_three_values(self):
        params = inspect.signature(ClauseElement._compile_w_cache).parameters
        for name in ("dialect", "compiled_cache", "column_keys"):
            self.assertIn(name, params)
        stmt = sa.select(waitlist.c.id).where(waitlist.c.id == 7)
        result = stmt._compile_w_cache(dialect=sqlcore._DIALECT, compiled_cache={}, column_keys=[])
        self.assertEqual(len(result), 3)
        compiled, extracted, _stats = result
        self.assertIsInstance(compiled, SQLCompiler)
        self.assertEqual([p.value for p in extracted], [7])

    def test_construct_params_takes_the_private_no_postcompile_keyword(self):
        params = inspect.signature(SQLCompiler.construct_params).parameters
        for name in ("params", "extracted_parameters", "escape_names", "_no_postcompile"):
            self.assertIn(name, params)

    def test_postcompile_expands_in_lists_into_statement_parameters_and_processors(self):
        params = inspect.signature(SQLCompiler._process_parameters_for_postcompile).parameters
        self.assertIn("parameters", params)
        compiled = sa.select(waitlist.c.id).where(waitlist.c.id.in_([1, 2])).compile(
            dialect=sqlcore._DIALECT, column_keys=[])
        state = compiled._process_parameters_for_postcompile(
            compiled.construct_params(escape_names=True, _no_postcompile=True))
        self.assertIsInstance(state, ExpandedState)
        self.assertEqual(state.parameters, {"id_1_1": 1, "id_1_2": 2})
        self.assertIn("%(id_1_1)s, %(id_1_2)s", state.statement)
        self.assertIsInstance(state.processors, dict)

    def test_bind_processors_is_a_mapping_of_callables(self):
        compiled = sa.insert(waitlist).values(id=1).compile(dialect=sqlcore._DIALECT, column_keys=[])
        processors = compiled._bind_processors
        self.assertIsInstance(processors, dict)
        self.assertTrue(all(callable(fn) for fn in processors.values()))

    def test_the_bridge_still_compiles_what_it_compiled_on_the_verified_release(self):
        sql, params = sqlcore.compile_statement(
            sa.select(waitlist.c.id).where(waitlist.c.id.in_([3, 4]), waitlist.c.id != 5))
        self.assertEqual(params, {"id_1_1": 3, "id_1_2": 4, "id_2": 5})
        self.assertIn("IN (%(id_1_1)s, %(id_1_2)s)", sql)
        self.assertIn("!= %(id_2)s", sql)


if __name__ == "__main__":
    unittest.main()
