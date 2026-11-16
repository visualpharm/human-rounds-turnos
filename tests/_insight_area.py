"""_insight_area.py — which operations of the reports, the system status, the
AI, the pre-visit interview and the chat must document their success answer
(TASK-76, OpenAPI stage D, area c), split into the parts the live tests
answer. Shared by `tests/test_insight_response_docs.py` (the schema and the
golden answers, DB-less) and the live files under `tests/routes/`
(`test_reports_response_models.py`, `test_ai_response_models.py`,
`test_intake_response_models.py`, `test_chat_response_models.py`); the
generic checks are `tests/_response_models.py`'s.

The area is read from the built schema (`routers/api_docs.build_schema`),
never from the source text: every `/api/...` operation in the groups «AI»,
«Pre-visit interview» and «Chat», and the staff panel's reports, system
status, AI register and usability study (by path).

Not named test_*.py: unittest discovery must not collect it.
"""

import json

import _response_models as rm

_TAG_PARTS = {"AI": "ai", "Pre-visit interview": "intake", "Chat": "chat"}

# Staff panel operations of the area, by path prefix.
_PANEL_PARTS = (
    ("/api/admin/reports", "reports"),
    ("/api/admin/system", "reports"),
    ("/api/admin/ai-register", "ai"),
    ("/api/admin/usability", "ai"),
)

PARTS = ("reports", "ai", "intake", "chat")

# Operations of the area whose success answer is not a JSON document a model
# could describe, and why.
EXCLUDED = {
    ("get", "/api/admin/reports/requests.csv"): "a CSV file (text/csv attachment), not JSON",
    ("get", "/api/admin/reports/cost-recovery.csv"): "a CSV file (text/csv attachment), not JSON",
    ("get", "/api/admin/reports/sumar.csv"): "a CSV file (text/csv attachment), not JSON",
    ("get", "/api/admin/reports/statistics.csv"): "a CSV file (text/csv attachment), not JSON",
    ("get", "/api/admin/intake/door/{code}"): "the door poster with its QR: an HTML page, or a PDF with `?format=pdf`",
    ("post", "/api/chat/stream"):"a server-sent event stream (text/event-stream), not one JSON document",
    ("post", "/api/chat/stream/"): "a server-sent event stream (text/event-stream), not one JSON document",
    ("get", "/api/whatsapp/webhook"): "Meta's subscription check: the challenge echoed as text/plain",
    ("get", "/api/whatsapp/webhook/"): "Meta's subscription check: the challenge echoed as text/plain",
}


def part_of(path, operation):
    """The part of the area an operation is in, or None outside the area."""
    if not path.startswith("/api/"):
        return None
    part = _TAG_PARTS.get(operation.get("tags", [None])[0])
    if part:
        return part
    if operation.get("tags", [None])[0] == "Staff panel":
        for prefix, name in _PANEL_PARTS:
            if path == prefix or path.startswith(prefix + "/"):
                return name
    return None


def area_operations(schema, part=None):
    """[(method, path, operation)] of the area (or of one part), in schema order."""
    out = []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            got = part_of(path, operation)
            if got and (part is None or got == part):
                out.append((method, path, operation))
    return out


class AnswerChecks:
    """Mixin for a live `RouteTestCase` of one part: `documented` is filled by
    `load_documented(part)`; `_answer` asks the app and checks the answer
    against the model the schema names."""

    part = None
    documented = {}

    @classmethod
    def load_documented(cls):
        from app import main as app_module
        from app.routers import api_docs
        schema = api_docs.build_schema(app_module.app)
        cls.documented = {(method, path): rm.success_answer(operation)
                          for method, path, operation in area_operations(schema, cls.part)}

    def _answer(self, method, template, path=None, headers=None, body=None):
        """Ask `method path` (`template` filled in) and check the answer against the
        documented model; returns the decoded body."""
        from app.core import api_body
        status, hdrs, raw = self._request(method.upper(), path or template, headers=headers, body=body)
        code, name = self.documented[(method, template)]
        self.assertEqual(status, int(code), f"{method.upper()} {path or template}: {raw[:300]!r}")
        if name is None:
            self.assertEqual(raw, b"")
            return None
        self.assertTrue(hdrs.get("Content-Type", "").startswith("application/json"),
                        hdrs.get("Content-Type"))
        value = json.loads(raw.decode("utf-8"))
        with self.subTest(operation=(method, path or template)):
            rm.check_answer(self, api_body.models()[name], value)
        return value

    def assert_every_operation_answered(self, answered):
        """Every documented operation of the part is answered by a test of the file
        (`answered`: {(method, template): test name}), or its success is pinned in a
        golden file (checked by test_insight_response_docs.py)."""
        described = {key for key in self.documented if key not in EXCLUDED}
        expected = described - rm.golden_operations()
        self.assertEqual(sorted(expected - set(answered)), [])
        self.assertEqual(sorted(set(answered) - described), [])
