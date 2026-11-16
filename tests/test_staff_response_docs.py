"""test_staff_response_docs.py — the OpenAPI schema describes the success
answer of every staff operation of booking requests, patients and the
doctor's appointments (TASK-76, OpenAPI stage D, area b; the area and the
exclusions are `tests/_staff_response_models.py`).

  - every operation of the area documents its success answer (a model, or a
    204 without a body), or is a listed exclusion with its reason;
  - every exclusion is still an operation of the area, and undocumented;
  - every 2xx JSON answer the golden files pin for an operation of the area
    (`tests/golden/body_contract_*.json`) matches the model the schema names,
    and the model names every key of it.

The live answers of every operation are checked in
`tests/routes/test_staff_response_models.py`. DB-less.
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

import _response_models as shared  # noqa: E402
import _staff_response_models as rm  # noqa: E402


class TestStaffResponseDocs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = api_docs.build_schema(app_module.app)
        cls.area = rm.area_operations(cls.schema, app_module.app)

    def test_the_area_is_read_from_the_schema(self):
        self.assertEqual(len(self.area), 52)
        ops = {(method, path) for method, path, _o in self.area}
        for key in (("get", "/api/admin/requests"), ("post", "/api/admin/requests/{req_id}/approve"),
                    ("get", "/api/admin/patients"), ("get", "/api/admin/day-queue"),
                    ("get", "/api/admin/doctor-profile")):
            self.assertIn(key, ops)
        self.assertFalse(ops & rm.NOT_THIS_AREA)

    def test_every_operation_documents_its_success_answer(self):
        missing = [(method.upper(), path) for method, path, operation in self.area
                   if (method, path) not in rm.EXCLUDED and shared.success_answer(operation) is None]
        self.assertEqual(missing, [])

    def test_every_exclusion_is_an_undocumented_operation_of_the_area(self):
        area = {(method, path): operation for method, path, operation in self.area}
        for key, reason in rm.EXCLUDED.items():
            with self.subTest(operation=key):
                self.assertIn(key, area)
                self.assertTrue(reason)
                self.assertIsNone(shared.success_answer(area[key]))

    def test_every_documented_model_is_a_response_model(self):
        models = api_body.models()
        for method, path, operation in self.area:
            answer = shared.success_answer(operation)
            if answer and answer[1]:
                with self.subTest(operation=(method, path)):
                    self.assertTrue(issubclass(models[answer[1]], api_body.ResponseModel))

    def test_no_two_models_share_a_name(self):
        """A nested model lands in the schema's components under its class name
        (`api_body.component_schemas`, the `$defs` of each model): two different
        models of one name would silently leave one description for both."""
        seen = {}
        for name, model in sorted(api_body.models().items()):
            mode = "validation" if issubclass(model, api_body.BodyModel) else "serialization"
            schema = model.model_json_schema(ref_template="#/components/schemas/{model}", mode=mode)
            for nested, body in dict(schema.pop("$defs", {}), **{name: schema}).items():
                with self.subTest(model=nested):
                    self.assertEqual(seen.setdefault(nested, body), body)

    def test_the_pinned_answers_match_their_models(self):
        golden = shared.golden_by_template(app_module.app)
        checked = 0
        for method, path, operation in self.area:
            if (method, path) not in golden:
                continue
            answer = shared.success_answer(operation)
            for name, cases in golden[(method, path)]:
                for case, got in cases.items():
                    if not 200 <= got["status"] < 300:
                        continue
                    with self.subTest(golden=name, operation=(method, path), case=case):
                        # A pinned success the schema does not document is a gap too, not a skip.
                        self.assertIsNotNone(answer)
                        self.assertEqual(got["status"], int(answer[0]))
                        self.assertTrue((got["content-type"] or "").startswith("application/json"))
                        shared.check_answer(self, api_body.models()[answer[1]], json.loads(got["body"]))
                        checked += 1
        # 49 since the strict body boundary (TASK-76 item F) turned the pinned 2xx answers to mismatched bodies into 400s.
        self.assertGreater(checked, 45)


if __name__ == "__main__":
    unittest.main()
