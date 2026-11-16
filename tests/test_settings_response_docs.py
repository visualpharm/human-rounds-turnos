"""test_settings_response_docs.py — the OpenAPI schema describes the success
answer of every operation of the staff panel's settings, content, users and
roles, staff sign-in, the platform and everything outside `/api/` (TASK-76,
OpenAPI stage D, area d; the area and the exclusions are
`tests/_settings_area.py`).

  - every operation of the area documents its success answer (a model, or a
    204 without a body), or is excluded with its reason;
  - every exclusion is still an operation of the area, and undocumented;
  - every 2xx answer the golden files pin for an operation of the area
    (`tests/golden/body_contract_*.json`) has the documented status, is JSON,
    matches the model the schema names, and the model names every key of it.

The live answers are checked in `tests/routes/test_settings_response_models.py`.
DB-less.
"""
import json
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
from app.core import api_body  # noqa: E402
from app.routers import api_docs  # noqa: E402

import _response_models as rm  # noqa: E402
import _settings_area as area  # noqa: E402


class TestSettingsResponseDocs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = api_docs.build_schema(app_module.app)
        cls.area = area.area_operations(cls.schema, app_module.app)
        cls.ops = {(method, path): operation for method, path, operation in cls.area}

    def test_the_area_is_read_from_the_schema(self):
        self.assertGreater(len(self.area), 200)
        self.assertIn(("get", "/api/admin/settings"), self.ops)
        self.assertIn(("get", "/api/admin/content/{tipo}"), self.ops)
        self.assertIn(("get", "/api/admin/theme"), self.ops)
        self.assertIn(("get", "/health"), self.ops)
        self.assertIn(("get", "/admin/settings"), self.ops)
        # Held by the other areas.
        self.assertNotIn(("get", "/api/services"), self.ops)
        self.assertNotIn(("get", "/api/admin/requests"), self.ops)
        self.assertNotIn(("get", "/api/admin/reports/patients"), self.ops)
        self.assertNotIn(("get", "/api/ai-status"), self.ops)

    def test_every_operation_documents_its_success_answer(self):
        missing = [(method.upper(), path) for method, path, operation in self.area
                   if area.excluded(method, path, operation) is None
                   and rm.success_answer(operation) is None]
        self.assertEqual(missing, [])

    def test_every_exclusion_is_an_undocumented_operation_of_the_area(self):
        for key, reason in area.EXCLUDED.items():
            with self.subTest(operation=key):
                self.assertIn(key, self.ops)
                self.assertTrue(reason)
                self.assertIsNone(rm.success_answer(self.ops[key]))
        legacy = [key for key, operation in self.ops.items()
                  if operation.get("tags", [None])[0] == area._LEGACY_TAG]
        self.assertGreater(len(legacy), 40)
        for key in legacy:
            with self.subTest(operation=key):
                self.assertIsNone(rm.success_answer(self.ops[key]))

    def test_every_api_operation_answers_json_or_is_excluded_under_api(self):
        """Outside /api/ an operation may be excluded by class; under /api/ only by name."""
        for (method, path), operation in self.ops.items():
            if path.startswith("/api/") and area.excluded(method, path, operation):
                with self.subTest(operation=(method, path)):
                    self.assertIn((method, path), area._API_EXCLUDED)

    def test_every_documented_model_is_a_response_model(self):
        """...or a `RootModel`, for a list or a map keyed by data."""
        models = api_body.models()
        for (method, path), operation in self.ops.items():
            answer = rm.success_answer(operation)
            if answer and answer[1]:
                with self.subTest(operation=(method, path)):
                    self.assertTrue(rm.is_answer_model(models[answer[1]]))

    def test_the_pinned_answers_match_their_models(self):
        golden = rm.golden_by_template(app_module.app)
        checked = 0
        for (method, path), operation in self.ops.items():
            answer = rm.success_answer(operation)
            if not answer or (method, path) not in golden:
                continue
            code, name = answer
            for file_name, cases in golden[(method, path)]:
                for case, got in cases.items():
                    if not 200 <= got["status"] < 300:
                        continue
                    with self.subTest(golden=file_name, operation=(method, path), case=case):
                        # A success the schema does not document is a gap too, not a skip.
                        self.assertEqual(got["status"], int(code))
                        if name is None:
                            self.assertEqual(got["body"], "")
                            continue
                        self.assertTrue((got["content-type"] or "").startswith("application/json"))
                        rm.check_answer(self, api_body.models()[name], json.loads(got["body"]))
                        checked += 1
        self.assertGreater(checked, 40)


if __name__ == "__main__":
    unittest.main()
