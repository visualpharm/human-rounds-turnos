"""test_openapi_response_coverage.py — the whole OpenAPI schema describes
its success answers (TASK-76, OpenAPI stage D): every operation is in
exactly one of the four areas, and documents its success answer or is an
exclusion, with its reason, of the area that holds it.

The areas are the lists their own files keep: a `tests/_response_models.py`,
b `tests/_staff_response_models.py`, c `tests/_insight_area.py`, d
`tests/_settings_area.py` (which restates the boundaries of a, b and c; here
the restatement is compared with their own lists). All four lists are
required: an area without its file is an import error, not a smaller check.
DB-less.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import main as app_module  # noqa: E402
from app.routers import api_docs  # noqa: E402

import _insight_area as area_c  # noqa: E402
import _response_models as area_a  # noqa: E402
import _settings_area as area_d  # noqa: E402
import _staff_response_models as area_b  # noqa: E402


class TestOpenapiResponseCoverage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app = app_module.app
        cls.schema = api_docs.build_schema(app)
        index = api_docs._route_index(app)
        cls.restated = {}
        for path, item in cls.schema["paths"].items():
            for method, operation in item.items():
                name = area_d.other_area(path, operation, index[(path, method)].endpoint.__module__)
                cls.restated[(method, path)] = name or "d"
        cls.held = {
            "a": {(m, p): (o, area_a.EXCLUDED.get((m, p))) for m, p, o in area_a.area_operations(cls.schema)},
            "b": {(m, p): (o, area_b.EXCLUDED.get((m, p)))
                  for m, p, o in area_b.area_operations(cls.schema, app)},
            "c": {(m, p): (o, area_c.EXCLUDED.get((m, p))) for m, p, o in area_c.area_operations(cls.schema)},
            "d": {(m, p): (o, area_d.excluded(m, p, o))
                  for m, p, o in area_d.area_operations(cls.schema, app)},
        }

    def test_every_operation_is_in_exactly_one_area(self):
        for key, name in self.restated.items():
            with self.subTest(operation=key):
                holders = [a for a, held in self.held.items() if key in held]
                self.assertEqual(holders, [name])

    def test_the_restated_boundaries_are_the_areas_own(self):
        for name, held in self.held.items():
            restated = {key for key, area in self.restated.items() if area == name}
            with self.subTest(area=name):
                self.assertEqual(sorted(restated ^ set(held)), [])

    def test_every_operation_documents_its_success_answer_or_is_excluded(self):
        missing = sorted((name, method.upper(), path) for name, held in self.held.items()
                         for (method, path), (operation, reason) in held.items()
                         if not reason and area_a.success_answer(operation) is None)
        self.assertEqual(missing, [])

    def test_the_whole_schema_is_counted(self):
        """Every operation of the schema is in the count, and the four areas are the only ones."""
        self.assertEqual(len(self.restated), sum(len(item) for item in self.schema["paths"].values()))
        self.assertEqual(set(self.restated.values()) - {"a", "b", "c", "d"}, set())
        self.assertGreater(len(self.restated), 400)


if __name__ == "__main__":
    unittest.main()
