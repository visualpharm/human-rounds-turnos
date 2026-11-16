"""_response_models.py — which operations of the public site and the
patient's account must document their success answer (TASK-76, OpenAPI
stage D, area a), and the check that an answer is the one its model
describes. Shared by `tests/test_patient_response_docs.py` (the schema and the
golden answers, DB-less) and `tests/routes/test_patient_response_models.py`
(every operation answered by the live app).

It also holds the helpers every area uses (b `tests/_staff_response_models.py`,
c `tests/_insight_area.py`, d `tests/_settings_area.py`): the documented success
answer of an operation, the golden answers by route template, and the check
that an answer is the one its model describes.

The area is read from the built schema (`routers/api_docs.build_schema`),
never from the source text: every `/api/...` operation in the groups of the
public site, the patient's account and the `turnos` module, except the staff
ones under `/api/admin/` (those are the panel's).

Not named test_*.py: unittest discovery must not collect it.
"""

import json
import os

_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

AREA_TAGS =("Patient site", "Patient accounts", "Appointments")

# Operations of the area whose success answer is not a JSON document a model
# could describe, and why.
EXCLUDED = {
    ("get", "/api/appointment/{sid}.ics"): "a calendar file (text/calendar), not JSON",
    ("get", "/api/account/photo"): "the photo itself (image bytes; `?probe=1` answers whether there is one)",
    ("get", "/api/orden-preview/{token}"): "the referral the patient uploaded, as it came (image or PDF bytes)",
    ("get", "/api/analytics/events"): "the anonymous event stream as NDJSON (application/x-ndjson), one event per line",
    ("get", "/api/analytics/schedule"): "the public schedule snapshot as NDJSON (application/x-ndjson)",
}


def area_operations(schema):
    """[(method, path, operation)] of the area, in schema order."""
    out = []
    for path, item in schema["paths"].items():
        if not path.startswith("/api/") or path.startswith("/api/admin/"):
            continue
        for method, operation in item.items():
            if operation.get("tags", [None])[0] in AREA_TAGS:
                out.append((method, path, operation))
    return out


def success_answer(operation):
    """(status, model name or None) of the documented success answer: the 2xx
    with a JSON schema, or a 204 (no body); None when there is none."""
    for code, answer in sorted(operation.get("responses", {}).items()):
        if not code.startswith("2"):
            continue
        if code == "204":
            return code, None
        schema = (answer.get("content", {}).get("application/json") or {}).get("schema") or {}
        ref = schema.get("$ref", "")
        if ref:
            return code, ref.rsplit("/", 1)[-1]
    return None


def golden_answers():
    """{(method, path): {case: answer}} of every route the body-contract golden
    files pin (`tests/routes/_body_contract.py`), with the file it is in."""
    out = {}
    for name in sorted(os.listdir(_GOLDEN_DIR)):
        if not name.startswith("body_contract_") or not name.endswith(".json"):
            continue
        with open(os.path.join(_GOLDEN_DIR, name), encoding="utf-8") as fh:
            for key, cases in json.load(fh).items():
                method, path = key.split(" ", 1)
                out[(method.lower(), path)] = (name, cases)
    return out


def golden_operations():
    """The (method, path) of the operations with pinned answers that include a success."""
    return {key for key, (_name, cases) in golden_answers().items()
            if any(200 <= got["status"] < 300 for got in cases.values())}


def golden_by_template(app):
    """{(method, path template): [(file, {case: answer})]} of every route the
    body-contract golden files pin (`tests/routes/_body_contract.py`). The
    files name the path they asked (`/api/admin/requests/1/approve`); the
    template is the route's that matches it."""
    from app.routers import api_docs
    routes = api_docs._route_index(app)
    out = {}
    for name in sorted(os.listdir(_GOLDEN_DIR)):
        if not name.startswith("body_contract_") or not name.endswith(".json"):
            continue
        with open(os.path.join(_GOLDEN_DIR, name), encoding="utf-8") as fh:
            for key, cases in json.load(fh).items():
                method, path = key.split(" ", 1)
                method = method.lower()
                for (template, m), route in routes.items():
                    if m == method and (template == path or route.path_regex.match(path)):
                        out.setdefault((method, template), []).append((name, cases))
                        break
    return out


def pinned_by_template(app):
    """The (method, path template) of the operations with a pinned success."""
    return {key for key, pinned in golden_by_template(app).items()
            if any(200 <= got["status"] < 300 for _name, cases in pinned for got in cases.values())}


def is_answer_model(model):
    """A documented answer is a `ResponseModel` (an object), or a `RootModel`
    for an answer that is a list or a map keyed by data."""
    from pydantic import RootModel
    from app.core import api_body
    return issubclass(model, (api_body.ResponseModel, RootModel))


def undeclared_keys(model, value):
    """Top-level keys of a JSON object answer its model does not name. A model
    allows fields it does not name (`core.api_body.ResponseModel`), so this
    is what keeps the envelope honest: `{"servicios": [...]}` is described by
    a model with `servicios`, not by one whose fields are all optional. A
    field with an alias (`_meta`) names the key by its alias."""
    if not isinstance(value, dict):
        return []
    names = {field.alias or name for name, field in model.model_fields.items()}
    return sorted(k for k in value if k not in names)


def check_answer(testcase, model, value):
    """The answer matches the model, and the model names every key of it (by
    its alias when it has one). A `RootModel` validates the whole value (a map
    keyed by data has no names to check); a list of objects checks the keys of
    each item against the item's model."""
    from pydantic import RootModel
    model.model_validate(value)
    if issubclass(model, RootModel):
        item = getattr(model.model_fields["root"].annotation, "__args__", (None,))[0]
        if isinstance(value, list) and isinstance(item, type) and is_answer_model(item):
            for row in value:
                testcase.assertEqual(undeclared_keys(item, row), [], item.__name__)
        return
    testcase.assertEqual(undeclared_keys(model, value), [], model.__name__)
