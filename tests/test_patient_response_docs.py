"""test_patient_response_docs.py — the OpenAPI schema describes the success
answer of every operation of the public site and the patient's account
(TASK-76, OpenAPI stage D, area a; the area and the exclusions are
`tests/_response_models.py`).

  - every operation of the area documents its success answer (a model, or a
    204 without a body), or is a listed exclusion with its reason;
  - every exclusion is still an operation of the area, and undocumented;
  - every 2xx JSON answer the golden files pin for an operation of the area
    (`tests/golden/body_contract_*.json`) matches the model the schema names,
    and the model names every key of it.

The live answers of every operation are checked in
`tests/routes/test_patient_response_models.py`. DB-less.
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


class TestPatientResponseDocs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = api_docs.build_schema(app_module.app)
        cls.area = rm.area_operations(cls.schema)

    def test_the_area_is_read_from_the_schema(self):
        self.assertGreater(len(self.area), 40)
        paths = {path for _m, path, _o in self.area}
        self.assertIn("/api/services", paths)
        self.assertIn("/api/account/me", paths)
        self.assertIn("/api/requests/{req_id}", paths)
        self.assertFalse(any(path.startswith("/api/admin/") for path in paths))

    def test_every_operation_documents_its_success_answer(self):
        missing = [(method.upper(), path) for method, path, operation in self.area
                   if (method, path) not in rm.EXCLUDED and rm.success_answer(operation) is None]
        self.assertEqual(missing, [])

    def test_every_exclusion_is_an_undocumented_operation_of_the_area(self):
        area = {(method, path): operation for method, path, operation in self.area}
        for key, reason in rm.EXCLUDED.items():
            with self.subTest(operation=key):
                self.assertIn(key, area)
                self.assertTrue(reason)
                self.assertIsNone(rm.success_answer(area[key]))

    def test_a_success_other_than_200_replaces_it(self):
        ops = {(method, path): operation for method, path, operation in self.area}
        created = ops[("post", "/api/account/test-identities/random")]["responses"]
        self.assertEqual(rm.success_answer(ops[("post", "/api/account/test-identities/random")]),
                         ("201", "TestIdentityOut"))
        self.assertNotIn("200", created)
        done = ops[("post", "/api/search-log")]["responses"]
        self.assertEqual(done["204"], {"description": "Done; no body"})
        self.assertNotIn("200", done)

    def test_every_documented_model_is_a_response_model(self):
        models = api_body.models()
        for method, path, operation in self.area:
            answer = rm.success_answer(operation)
            if answer and answer[1]:
                with self.subTest(operation=(method, path)):
                    self.assertTrue(issubclass(models[answer[1]], api_body.ResponseModel))

    def test_the_pinned_answers_match_their_models(self):
        golden = rm.golden_answers()
        checked = 0
        for method, path, operation in self.area:
            answer = rm.success_answer(operation)
            if not answer or not answer[1] or (method, path) not in golden:
                continue
            status, model = int(answer[0]), api_body.models()[answer[1]]
            name, cases = golden[(method, path)]
            for case, got in cases.items():
                if not 200 <= got["status"] < 300:
                    continue
                with self.subTest(golden=name, operation=(method, path), case=case):
                    # A success the schema does not document is a gap too, not a skip.
                    self.assertEqual(got["status"], status)
                    self.assertTrue((got["content-type"] or "").startswith("application/json"))
                    rm.check_answer(self, model, json.loads(got["body"]))
                    checked += 1
        # 80 since the strict body boundary (TASK-76 item F) turned the pinned 2xx answers to mismatched bodies into 400s.
        self.assertGreater(checked, 75)


if __name__ == "__main__":
    unittest.main()
