"""test_insight_response_docs.py — the OpenAPI schema describes the success
answer of every operation of the reports, the system status, the AI, the
pre-visit interview and the chat (TASK-76, OpenAPI stage D, area c; the area
and the exclusions are `tests/_insight_area.py`).

  - every operation of the area documents its success answer (a model, or a
    204 without a body), or is a listed exclusion with its reason;
  - every exclusion is still an operation of the area, and undocumented;
  - every 2xx JSON answer the golden files pin for an operation of the area
    (`tests/golden/body_contract_*.json`) matches the model the schema names,
    and the model names every key of it.

The live answers of every operation are checked in the four
`tests/routes/test_*_response_models.py` files of the area. DB-less.
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

import _insight_area as area  # noqa: E402
import _response_models as rm  # noqa: E402


class TestInsightResponseDocs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = api_docs.build_schema(app_module.app)
        cls.area = area.area_operations(cls.schema)

    def test_the_area_is_read_from_the_schema(self):
        self.assertGreater(len(self.area), 70)
        parts = {part: {path for _m, path, _o in area.area_operations(self.schema, part)}
                 for part in area.PARTS}
        self.assertIn("/api/admin/reports/funnel", parts["reports"])
        self.assertIn("/api/admin/system/hsi", parts["reports"])
        self.assertIn("/api/admin/prompts/{clave}", parts["ai"])
        self.assertIn("/api/admin/usability", parts["ai"])
        self.assertIn("/api/admin/intake/alerts", parts["intake"])
        self.assertIn("/api/chat/history", parts["chat"])
        self.assertEqual(sum(len(area.area_operations(self.schema, part)) for part in area.PARTS),
                         len(self.area))
        # The staff panel's other screens are not this area's.
        self.assertNotIn("/api/admin/users", set().union(*parts.values()))

    def test_every_operation_documents_its_success_answer(self):
        missing = [(method.upper(), path) for method, path, operation in self.area
                   if (method, path) not in area.EXCLUDED and rm.success_answer(operation) is None]
        self.assertEqual(missing, [])

    def test_every_exclusion_is_an_undocumented_operation_of_the_area(self):
        ops = {(method, path): operation for method, path, operation in self.area}
        for key, reason in area.EXCLUDED.items():
            with self.subTest(operation=key):
                self.assertIn(key, ops)
                self.assertTrue(reason)
                self.assertIsNone(rm.success_answer(ops[key]))

    def test_a_success_other_than_200_replaces_it(self):
        ops = {(method, path): operation for method, path, operation in self.area}
        created = ops[("post", "/api/admin/usability/findings")]
        self.assertEqual(rm.success_answer(created), ("201", "UsabilityFindingCreatedOut"))
        self.assertNotIn("200", created["responses"])

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
        self.assertGreater(checked, 3)


if __name__ == "__main__":
    unittest.main()
