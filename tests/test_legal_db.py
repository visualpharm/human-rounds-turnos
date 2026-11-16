"""El texto legal se edita desde el panel y eso es lo que se publica.

La parte con base de datos de `legal.py`: guardar crea el override, la página
pública lo sirve, "restaurar" borra la fila y vuelve el original del código.
Sin base, la página tiene que seguir sirviendo el original — un documento
legal no puede depender de que Postgres esté arriba.

Base dedicada (`sana_turnos_test_legal`), mismo patrón que test_translate.py.

Corré: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_legal_db.py"
"""

import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app.core import db_accounts  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import legal       # noqa: E402
from app.core import site_identity  # noqa: E402
from app.core import tz  # noqa: E402

import _dbtest     # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_legal",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))

HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestOverlay(unittest.TestCase):

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin Postgres")
        db_catalog.legal_delete("terminos")

    def tearDown(self):
        if HAVE_DB:
            db_catalog.legal_delete("terminos")

    def test_sin_fila_se_sirve_el_original_del_codigo(self):
        doc = legal.document("terminos")
        self.assertFalse(doc["editado"])
        self.assertEqual(doc["cuerpo"], legal.original("terminos")["cuerpo"])

    def test_guardar_pisa_el_original_y_queda_marcado_como_editado(self):
        db_catalog.legal_save("terminos", "## Nuevo\n\nLo que decidió el hospital.",
                      usuario="silvia")
        doc = legal.document("terminos")
        self.assertTrue(doc["editado"])
        self.assertIn("Lo que decidió el hospital.", doc["cuerpo"])
        self.assertEqual(doc["actualizado_por"], "silvia")

    def test_la_fecha_de_actualizacion_es_la_de_hoy_en_la_zona_del_hospital(self):
        # No la del reloj del proceso: una edición de las 22:30 en Pinamar no
        # puede publicarse fechada al día siguiente.
        db_catalog.legal_save("terminos", "Texto nuevo.", usuario="silvia")
        hoy = datetime.now(tz.tzinfo()).date().isoformat()
        self.assertEqual(legal.document("terminos")["actualizado"], hoy)

    def test_lo_editado_es_lo_que_se_publica_en_la_pagina(self):
        db_catalog.legal_save("terminos", "## Cláusula nueva\n\nRige desde hoy.")
        html = site_identity.render_page("terms.html")[0].decode("utf-8")
        self.assertIn("<h2>Cláusula nueva</h2>", html)
        self.assertIn("Rige desde hoy.", html)
        self.assertNotIn("Se ofrece", html)   # el original ya no está

    def test_restaurar_borra_la_fila_y_vuelve_el_original(self):
        db_catalog.legal_save("terminos", "Texto provisorio.")
        db_catalog.legal_delete("terminos")
        doc = legal.document("terminos")
        self.assertFalse(doc["editado"])
        self.assertEqual(doc["cuerpo"], legal.original("terminos")["cuerpo"])

    def test_el_placeholder_de_la_instalacion_sigue_resolviendo_en_texto_editado(self):
        db_catalog.legal_save("terminos", "Esto es {{sitio.brand}} y su hospital.")
        html = site_identity.render_page("terms.html")[0].decode("utf-8")
        self.assertNotIn("{{sitio.brand}}", html)
        self.assertIn(site_identity.get("brand"), html)

    def test_cada_documento_es_independiente(self):
        db_catalog.legal_save("terminos", "Sólo términos.")
        self.assertEqual(legal.document("privacidad")["cuerpo"],
                         legal.original("privacidad")["cuerpo"])


if __name__ == "__main__":
    unittest.main()
