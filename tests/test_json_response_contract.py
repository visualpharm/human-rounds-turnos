"""test_json_response_contract.py — todas las respuestas JSON nativas hablan igual (2026-10-25).

Cada router nativo tenía su propia `_json_response` (26 copias en 22 archivos) y las familias
`_native_support`/`native_json` otras cuatro: todas arman el mismo cuerpo
(`json.dumps(obj, ensure_ascii=False)` en UTF-8), `Content-Type: application/json; charset=utf-8`,
`Cache-Control: no-store`, y las que reciben el pedido comprimen con `core.gzip_bridge`. Hoy las construye
`core/http_json.py`; este archivo fija, para cada una, el estado, los encabezados y el cuerpo que salían
ANTES de juntarlas (`tests/golden/json_response.json`, grabado con el código previo y no tocado por el
cambio). No se juntó la de `modules/chat/chat_api_router.py`: su `Content-Type` es `application/json`
sin charset, distinto a propósito (se fija aparte, abajo).

Regrabar: `python3 tests/test_json_response_contract.py --write` (sólo sobre código cuya salida ya se
sabe correcta).

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_json_response_contract.py
"""
import gzip
import hashlib
import importlib
import json
import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "json_response.json")

# (module, attribute, form): "plain" = f(obj, status), "gzip" = f(request, obj, status),
# "background" = f(obj, status, background=...)
TARGETS = [
    ("app.admin_panel.reports_router", "_json_response", "gzip"),
    ("app.auth.panel_login_router", "_json_response", "gzip"),
    ("app.core.app_modules_router", "_json_response", "gzip"),
    ("app.modules.turnos.appointment_confirm_router", "_json_response", "gzip"),
    ("app.modules.turnos.appointment_offer_router", "_json_response", "gzip"),
    ("app.modules.turnos.booking_requests_router", "_json_response", "gzip"),
    ("app.modules.turnos.calendar_feed_router", "_json_response", "gzip"),
    ("app.modules.turnos.doctor_appointments_router", "_json_response", "gzip"),
    ("app.modules.turnos.preparation_router", "_json_response", "gzip-200"),
    ("app.modules.turnos.searches_router", "_json_response", "gzip"),
    ("app.modules.turnos.slot_holds_router", "_json_response", "gzip"),
    ("app.ops.demo_installation_router", "_json_response", "gzip"),
    ("app.patient_web.avatar_sprite_router", "_json_response", "gzip"),
    ("app.patient_web.branding_router", "_json_response", "gzip"),
    ("app.patient_web.patient_site_router", "_json_response", "gzip-200"),
    ("app.admin_panel.native_json", "json_response", "gzip"),
    ("app.modules.chat._native_support", "json_response", "gzip"),
    ("app.ai.ai_costs_router", "_json_response", "plain"),
    ("app.ai.ai_health_router", "_json_response", "plain"),
    ("app.ai.lab_results_router", "_json_response", "plain"),
    ("app.ai.chart_summary_router", "_json_response", "plain"),
    ("app.ai.prompts_router", "_json_response", "plain"),
    ("app.connectors.puco.puco_router", "_json_response", "plain"),
    ("app.patient_web.doctor_photos_router", "_json_response", "plain"),
    ("app.patient_web.legal_router", "_json_response", "plain"),
    ("app.modules.chat.whatsapp_chat_router", "_json_response", "plain"),
    ("app.admin_panel._native_support", "json_response", "plain"),
    ("app.modules.intake._native_support", "json_response", "plain"),
    ("app.auth.password_reset_router", "_json_response", "background"),
]

_SMALL = {"ok": True}
_ACCENTS = {"nombre": "Ñandú Pérez", "texto": "¿Cómo está? — «bien»", "n": [1, 2.5, None]}
_BIG = {"items": [{"id": i, "texto": "Consultorio de clínica médica, planta baja " * 2} for i in range(60)]}


def _request(accept):
    return SimpleNamespace(headers={"Accept-Encoding": accept} if accept else {})


def _describe(response):
    headers = sorted([k.decode("latin-1"), v.decode("latin-1")] for k, v in response.raw_headers)
    body = response.body
    if response.headers.get("content-encoding") == "gzip":
        body = gzip.decompress(body)
    text = body.decode("utf-8")
    if len(text) > 400:     # keep the file small: the digest pins the big bodies
        text = "sha256:%s len:%d" % (hashlib.sha256(body).hexdigest(), len(body))
    return {"status": response.status_code, "headers": headers, "body": text,
            "background": response.background is not None}


def _calls(form):
    cases = []
    for label, obj in (("small", _SMALL), ("accents", _ACCENTS), ("big", _BIG)):
        for status in (200, 404):
            if form == "gzip-200":      # the two that always answer 200 take no status
                if status == 200:
                    for accept in (None, "gzip, deflate", "identity"):
                        cases.append(("%s/200/%s" % (label, accept or "-"), (_request(accept), obj), {}))
            elif form == "gzip":
                for accept in (None, "gzip, deflate", "identity"):
                    cases.append(("%s/%s/%s" % (label, status, accept or "-"), (_request(accept), obj, status), {}))
            else:
                cases.append(("%s/%s" % (label, status), (obj, status), {}))
    if form == "background":
        cases.append(("background", (_SMALL, 200), {"background": object()}))
    return cases


def _record():
    out = {}
    for module, attr, form in TARGETS:
        fn = getattr(importlib.import_module(module), attr)
        out["%s.%s" % (module, attr)] = {
            label: _describe(fn(*args, **kwargs)) for label, args, kwargs in _calls(form)}
    return out


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class JsonResponseContract(unittest.TestCase):
    def test_every_native_json_response_is_what_it_was_before_they_were_joined(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            want = json.load(fh)
        got = _record()
        self.assertEqual(sorted(got), sorted(want))
        for name in sorted(want):
            for label in sorted(want[name]):
                self.assertEqual(got[name][label], want[name][label], "%s / %s" % (name, label))

    def test_the_contract_itself(self):
        got = _record()
        for name, cases in got.items():
            for label, resp in cases.items():
                headers = dict(resp["headers"])
                self.assertEqual(headers["content-type"], "application/json; charset=utf-8", name)
                self.assertEqual(headers["cache-control"], "no-store", name)
                self.assertEqual(int(headers["content-length"]) > 0, True, name)
        small = got["app.ai.ai_costs_router._json_response"]["accents/200"]
        self.assertIn("Ñandú", small["body"])   # ensure_ascii=False
        big = got["app.admin_panel.reports_router._json_response"]["big/200/gzip, deflate"]
        self.assertEqual(dict(big["headers"])["content-encoding"], "gzip")
        self.assertEqual(dict(big["headers"])["vary"], "Accept-Encoding")

    def test_the_chat_widget_keeps_its_own_content_type(self):
        from app.modules.chat import chat_api_router
        response = chat_api_router._json_response({"ok": True}, 200)
        self.assertEqual(dict((k.decode(), v.decode()) for k, v in response.raw_headers)["content-type"],
                         "application/json")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
