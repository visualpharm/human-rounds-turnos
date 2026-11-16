"""test_print_templates_contract.py - the two print documents render the same bytes after they share a base (2026-10-28).

Second pass of the architecture audit (section 6): `modules/intake/print_receta.html` and `print_orden.html`
are the same 77-line page but for the title, the page class, the document label and the block that lists the
prescription (`lineas`) or the studies and diagnoses (`estudios`, `diagnosticos`). Before the common part
moves to one base template, this renders both with a battery of payloads (every flag, every empty list,
markup that must be escaped, a replaced document) and records the digest and length of each answer.

The catalog is replaced by its Spanish fallback, so the answer depends on the templates and on nothing in
the locales. The recorded file is written with the templates that exist today and is not touched by the
change that shares them: `python3 tests/test_print_templates_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_print_templates_contract.py
"""
import hashlib
import html
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from markupsafe import Markup  # noqa: E402

from app.core import i18n, jinja_env  # noqa: E402
from app.modules.intake import printable_router  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "print_templates.json")

BASE = {
    "titulo": "Hospital Municipal", "fecha": "2026-10-28",
    "medico": {"nombre": "Dra. Ana Pérez", "especialidad": "Cardiología", "matricula": "MP 1234"},
    "paciente": {"nombre": "Juan González", "dni": "30123456"},
    "lineas": ["Enalapril 10 mg", "Aspirina 100 mg"],
    "estudios": ["Electrocardiograma", "Análisis de sangre"],
    "diagnosticos": ["Hipertensión arterial"],
    "notas": "En ayunas", "pie": "Vale 30 días",
}


def _case(**changes):
    payload = json.loads(json.dumps(BASE))
    for key, value in changes.items():
        if key in ("medico", "paciente") and isinstance(value, dict):
            payload[key].update(value)
        else:
            payload[key] = value
    return payload


CASES = {
    "full": (_case(), None),
    "borrador": (_case(borrador=True), None),
    "anulada": (_case(anulada=True), {"url": "/r/nuevo-token", "fecha": "2026-10-29"}),
    "anulada-no-link": (_case(anulada=True), {}),
    "empty-lists": (_case(lineas=[], estudios=[], diagnosticos=[]), None),
    "lists-missing": (_case(lineas=None, estudios="no es lista", diagnosticos=3), None),
    "no-notes-no-dni": (_case(notas="", paciente={"dni": None}), None),
    "no-doctor-details": (_case(medico={"especialidad": "", "matricula": None}), None),
    "only-speciality": (_case(medico={"matricula": ""}), None),
    "only-license": (_case(medico={"especialidad": None}), None),
    "escaped": (_case(titulo="<b>x</b>", lineas=["<script>alert(1)</script>", "a & b"], estudios=["\"q\" 'r'"],
                      diagnosticos=["<i>"], notas="<u>n</u>", pie="</div>"), None),
    "space": (_case(espacio=3), None),
    "blank-payload": ({}, None),
    "not-a-dict": (None, None),
}


def _t(key, default, **variables):
    return Markup(html.escape(default, quote=True))


def _plain_catalog():
    """The template global `t` answers its Spanish fallback, whatever the locales say."""
    return mock.patch.object(jinja_env.ENV, "globals", {**jinja_env.ENV.globals, "t": _t})


def _record():
    out = {}
    with _plain_catalog(), mock.patch.object(i18n, "active", lambda: "es"):
        for kind in ("receta", "orden"):
            for name, (payload, replacement) in sorted(CASES.items()):
                body = printable_router._render_printable(kind, payload, replacement)
                out["%s|%s" % (kind, name)] = {"sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "length": len(body)}
        out["not_found"] = {"sha256": hashlib.sha256(printable_router._render_not_found().encode("utf-8")).hexdigest()}
    return out


def _write():
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, indent=1)
        fh.write("\n")


class PrintTemplatesContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def test_every_case_is_recorded(self):
        self.assertEqual(sorted(self.got), sorted(self.want))

    def test_each_document_renders_the_same_bytes(self):
        for key in sorted(self.want):
            self.assertEqual(self.got[key], self.want[key], key)

    def test_the_two_documents_differ_only_where_they_must(self):
        """Guards the guard: the receta lists `lineas`, the orden `estudios` and `diagnosticos`."""
        with _plain_catalog(), mock.patch.object(i18n, "active", lambda: "es"):
            receta = printable_router._render_printable("receta", _case(), None)
            orden = printable_router._render_printable("orden", _case(), None)
        self.assertIn("Enalapril 10 mg", receta)
        self.assertNotIn("Electrocardiograma", receta)
        self.assertIn("Electrocardiograma", orden)
        self.assertIn("Hipertensión arterial", orden)
        self.assertNotIn("Enalapril 10 mg", orden)
        self.assertIn("hoja-receta", receta)
        self.assertIn("hoja-orden", orden)
        self.assertNotEqual(receta, orden)


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
