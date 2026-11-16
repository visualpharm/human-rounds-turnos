"""Cuántos turnos puede tener a la vez una misma persona (booking_limits.py).

El responsable del producto, 2026-08-01: uno para casi todas las especialidades, diez para
kinesiología. La regla resuelve de lo más específico a lo más general —
médico, servicio, sitio — y el test fija ese orden, que es lo único que
importa cuando alguien agrega un cuarto nivel.
"""

import unittest

from app.modules.turnos import booking_limits
from app.patient_web import catalog
from app.core import config
import _env  # noqa: E402


class TestLimitResolution(unittest.TestCase):
    def setUp(self):
        # test_booking_requests.py levanta MAX_ACTIVE_REQUESTS en el entorno
        # del proceso para sus propios fixtures, y `discover` corre todo en el
        # mismo proceso: acá se prueba el DEFAULT, así que la variable se saca
        # y se devuelve tal cual estaba.
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
        _env.set_env(self, MAX_ACTIVE_REQUESTS=None)
        config._reset_cache_para_tests()

    def test_sin_overrides_el_limite_es_uno(self):
        self.assertEqual(booking_limits.limit_for(None, None),
                         booking_limits.DEFAULT_LIMIT)

    def test_el_servicio_pisa_al_sitio(self):
        kinesio = {"id": "kinesiologia", "turnos_activos_max": 10}
        self.assertEqual(booking_limits.limit_for(kinesio, None), 10)

    def test_el_medico_pisa_al_servicio(self):
        kinesio = {"turnos_activos_max": 10}
        medico = {"turnos_activos_max": 3}
        self.assertEqual(booking_limits.limit_for(kinesio, medico), 3)

    def test_un_override_vacio_hereda_en_vez_de_bloquear(self):
        # El formulario manda "" cuando el operador borra el campo: eso es
        # "seguí la regla del sitio", nunca "cero turnos".
        for vacio in ("", None, 0, "  ", "no es un número"):
            self.assertEqual(
                booking_limits.limit_for({"turnos_activos_max": vacio}, None),
                booking_limits.DEFAULT_LIMIT,
                f"{vacio!r} tendría que heredar")

    def test_el_maximo_esta_acotado(self):
        # Un tipeo en el panel no puede dejar el límite en 10.000.
        self.assertEqual(
            booking_limits.limit_for({"turnos_activos_max": 99999}, None),
            booking_limits.MAX_LIMIT)


class TestCatalogValidation(unittest.TestCase):
    """El catálogo también se carga por import, no sólo por el formulario:
    la validación vive en catalog.py, no en el JS."""

    def test_un_servicio_acepta_el_numero_y_descarta_el_vacio(self):
        doc, err = catalog._normalizar_turnos_max({"turnos_activos_max": "10"})
        self.assertIsNone(err)
        self.assertEqual(doc["turnos_activos_max"], 10)

        doc, err = catalog._normalizar_turnos_max({"turnos_activos_max": ""})
        self.assertIsNone(err)
        self.assertNotIn("turnos_activos_max", doc)

    def test_un_valor_imposible_es_un_error_del_formulario(self):
        for malo in ("cero", "0", "-4", 999):
            doc, err = catalog._normalizar_turnos_max({"turnos_activos_max": malo})
            self.assertIsNotNone(err, f"{malo!r} tendría que rechazarse")
            self.assertIsNone(doc)

    def test_un_medico_tiene_minimo_2(self):
        # El mínimo para el médico es 2: un máximo de 1 significa "sin límite
        # explícito, hereda del servicio". El formulario lo valida con
        # min="2"; este test verifica que el catálogo también rechace 1.
        doc, err = catalog._normalizar_turnos_max(
            {"turnos_activos_max": "1"}, minimo=2)
        self.assertIsNotNone(err)
        self.assertIsNone(doc)
        self.assertIn("va de 2", err)

    def test_un_medico_acepta_2_a_60(self):
        # El médico valida el rango 2–60 (minimo=2)
        for n in (2, 10, 60):
            doc, err = catalog._normalizar_turnos_max(
                {"turnos_activos_max": str(n)}, minimo=2)
            self.assertIsNone(err, f"{n} tendría que aceptarse")
            self.assertEqual(doc["turnos_activos_max"], n)

    def test_un_medico_rechaza_61(self):
        # Fuera del rango
        doc, err = catalog._normalizar_turnos_max(
            {"turnos_activos_max": "61"}, minimo=2)
        self.assertIsNotNone(err)
        self.assertIsNone(doc)


if __name__ == "__main__":
    unittest.main()
