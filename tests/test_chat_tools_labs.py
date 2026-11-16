"""test_chat_tools_labs.py — las tools autenticadas del chat que hablan de
DATOS del paciente: my_lab_results (nueva), whoami con `sexo` del
dependiente y my_requests con `estado_texto` ya localizado.

Tres bugs reales, tres pruebas directas contra las funciones de tool (sin
HTTP, sin modelo, sin Postgres — las fuentes de datos se patchean en el
borde chat_store / ai.lab_results, igual que test_chat_tools_i18n.py
patchea el catálogo):
  - my_lab_results: existía la lectura en la web (ai.lab_results.informes_de)
    pero el chat respondía que no podía ver los resultados. Ahora existe la
    tool, es SOLO para sesiones iniciadas (nunca en ANONYMOUS_TOOLS), acepta
    el DNI de un dependiente VINCULADO (y rechaza cualquier otro), y un
    informe sensible cruza el cable como {"fecha", "sensible": true} y nada
    más — la redacción es del servidor.
  - whoami: el bot decía "your son" de una hija porque los dependientes no
    llevaban `sexo` (db_slots.list_dependents lo devuelve, wire "M"/"F"/"X").
  - my_requests: en una conversación en ruso el bot citaba «pendiente» en
    crudo. Ahora cada fila lleva `estado_texto` con la MISMA etiqueta que
    pinta la cuenta (account.mine.requests.status_*), localizada al idioma
    activo.

Correr: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_chat*.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no leer el .env real del checkout (idem test_chat_tools_i18n.py)

from app.core import i18n  # noqa: E402
from app.modules.chat import chat_agent  # noqa: E402
from app.modules.chat import chat_store  # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402

USUARIO = {"dni": "30999888", "nombre": "Delia", "apellido": "Suárez"}

_DEPENDENTES = [
    {"dni": "55111222", "nombre": "Lucía", "apellido": "Suárez",
     "nacimiento": "2018-04-10", "sexo": "F", "vinculo": "hija"},
    {"dni": "55222333", "nombre": "Tomás", "apellido": "Suárez",
     "nacimiento": "2015-09-02", "sexo": "M", "vinculo": "hijo"},
]

_INFORMES = [
    {"fecha": "2026-09-01", "tipo_estudio": "Análisis de sangre completo",
     "resumen": "los valores se ven dentro de rango", "tono": "ok",
     "servicio": "Laboratorio", "items": []},
    {"fecha": "2026-08-20", "sensible": True},
    {"fecha": "2026-07-15", "tipo_estudio": "Perfil tiroideo",
     "resumen": "algo para revisar con el médico", "tono": "revisar",
     "servicio": "Laboratorio",
     "items": [{"nombre": "TSH", "valor": 6.1, "unidad": "mIU/L"}]},
]


class DisponibilidadPorIdentidad(unittest.TestCase):
    """my_lab_results existe para quien tiene sesión y para nadie más."""

    def test_anonimo_no_la_tiene(self):
        anon = set(chat_agent._available_tools(None))
        self.assertNotIn("my_lab_results", anon)
        self.assertNotIn("my_lab_results", set(chat_agent.ANONYMOUS_TOOLS))

    def test_logueado_la_tiene_y_esta_registrada(self):
        logueado = set(chat_agent._available_tools(dict(USUARIO)))
        self.assertIn("my_lab_results", logueado)
        self.assertEqual(logueado, set(chat_tools.TOOL_FNS.keys()))
        self.assertIn(
            "my_lab_results", [t["name"] for t in chat_tools.TOOLS])

    def test_sin_sesion_devuelve_error(self):
        out = chat_tools._tool_lab_results(None, {}, None)
        self.assertIn("error", out)

    def test_my_requests_dice_que_estado_texto_es_la_etiqueta(self):
        spec = next(t for t in chat_tools.TOOLS if t["name"] == "my_requests")
        self.assertIn("estado_texto", spec["description"])


class ResultadosDeLaboratorio(unittest.TestCase):
    def test_titular_ve_sus_informes(self):
        with patch.object(chat_store, "list_dependents",
                          return_value=list(_DEPENDENTES)), \
             patch("app.ai.lab_results.informes_de",
                   return_value=(list(_INFORMES), {}, [], 1)) as informes_de:
            out = chat_tools._tool_lab_results(dict(USUARIO), {}, None)
        informes_de.assert_called_once_with("30999888")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["informes"], _INFORMES)
        self.assertEqual(out["analisis_pendiente"], 1)

    def test_informe_sensible_sale_redactado(self):
        # La redacción ya la hizo lab_results (server-side): la tool no debe
        # re-inflar la fila — fecha y sensible, nada más.
        with patch.object(chat_store, "list_dependents",
                          return_value=list(_DEPENDENTES)), \
             patch("app.ai.lab_results.informes_de",
                   return_value=(list(_INFORMES), {}, [], 0)):
            out = chat_tools._tool_lab_results(dict(USUARIO), {}, None)
        sensible = next(f for f in out["informes"] if f.get("sensible"))
        self.assertEqual(set(sensible.keys()), {"fecha", "sensible"})
        self.assertNotIn("resumen", repr(sensible))

    def test_dni_de_dependiente_vinculado_se_acepta(self):
        with patch.object(chat_store, "list_dependents",
                          return_value=list(_DEPENDENTES)) as deps, \
             patch("app.ai.lab_results.informes_de",
                   return_value=(list(_INFORMES), {}, [], 0)) as informes_de:
            out = chat_tools._tool_lab_results(
                dict(USUARIO), {"patient_dni": "55111222"}, None)
        deps.assert_called_once_with("30999888")
        informes_de.assert_called_once_with("55111222")
        self.assertEqual(out["total"], 3)

    def test_dni_ajeno_devuelve_error_y_no_consulta_nada(self):
        with patch.object(chat_store, "list_dependents",
                          return_value=list(_DEPENDENTES)), \
             patch("app.ai.lab_results.informes_de",
                   return_value=(list(_INFORMES), {}, [], 0)) as informes_de:
            out = chat_tools._tool_lab_results(
                dict(USUARIO), {"patient_dni": "99999999"}, None)
        self.assertIn("error", out)
        informes_de.assert_not_called()

    def test_tope_de_diez_informes(self):
        muchos = [{"fecha": f"2026-01-{d:02d}", "tipo_estudio": "Análisis",
                   "resumen": "ok", "tono": "ok", "servicio": "Laboratorio",
                   "items": []} for d in range(1, 14)]
        with patch.object(chat_store, "list_dependents", return_value=[]), \
             patch("app.ai.lab_results.informes_de",
                   return_value=(muchos, {}, [], 0)):
            out = chat_tools._tool_lab_results(dict(USUARIO), {}, None)
        self.assertEqual(len(out["informes"]), 10)
        self.assertEqual(out["total"], 13)

    def test_el_enlace_del_resultado_apunta_a_laboratorio(self):
        links = chat_tools.enlaces_para(
            "resultados de laboratorio", "Acá están tus resultados.",
            [("my_lab_results", {"informes": [], "total": 0,
                                 "analisis_pendiente": 0})])
        self.assertIn({"label": "Laboratorio", "href": "/account#labs"}, links)


class GeneroDelDependente(unittest.TestCase):
    def test_whoami_incluye_sexo(self):
        with patch.object(chat_store, "list_dependents",
                          return_value=list(_DEPENDENTES)):
            out = chat_tools._tool_whoami(dict(USUARIO), {}, None)
        por_dni = {d["dni"]: d for d in out["dependientes"]}
        self.assertEqual(por_dni["55111222"]["sexo"], "F")
        self.assertEqual(por_dni["55222333"]["sexo"], "M")


class EstadoTraducido(unittest.TestCase):
    _ROWS = [
        {"id": 1, "servicio_id": "cardiologia-hospital",
         "centro_slug": "hospital", "estado": "pendiente", "turno_dt": None,
         "preferencia_horario": "mañana", "motivo_rechazo": None,
         "created": "2026-09-01T10:00:00", "medico_asignado": None,
         "medico_asignado_id": None, "dni": "30999888", "nombre": "Delia",
         "apellido": "Suárez", "ai_json": None},
        {"id": 2, "servicio_id": "cardiologia-hospital",
         "centro_slug": "hospital", "estado": "aprobada", "turno_dt": None,
         "preferencia_horario": None, "motivo_rechazo": None,
         "created": "2026-09-02T10:00:00", "medico_asignado": None,
         "medico_asignado_id": None, "dni": "30999888", "nombre": "Delia",
         "apellido": "Suárez", "ai_json": None},
    ]

    def _mine(self):
        with patch.object(chat_store, "list_by_dni",
                          return_value=[dict(r) for r in self._ROWS]), \
             patch("app.patient_web.catalog.services", return_value=[]):
            return chat_tools._tool_mine(dict(USUARIO), {}, None)

    def test_estado_texto_en_ingles_cuando_el_idioma_activo_es_en(self):
        _, token = i18n.bind_lang("en")
        try:
            out = self._mine()
        finally:
            i18n.unbind(token)
        textos = {r["id"]: r["estado_texto"] for r in out["solicitudes"]}
        self.assertEqual(textos[1], "The hospital is reviewing it")
        self.assertEqual(textos[2], "Approved")
        # El valor crudo sigue disponible para la lógica, no para mostrarlo.
        self.assertEqual(out["solicitudes"][0]["estado"], "pendiente")

    def test_estado_texto_en_espanol_por_defecto(self):
        _, token = i18n.bind_lang("es")
        try:
            out = self._mine()
        finally:
            i18n.unbind(token)
        self.assertEqual(out["solicitudes"][0]["estado_texto"],
                         "El hospital la revisa")

    def test_estado_desconocido_pasa_sin_inventar_etiqueta(self):
        rows = [dict(self._ROWS[0], estado="algo_nuevo")]
        with patch.object(chat_store, "list_by_dni", return_value=rows), \
             patch("app.patient_web.catalog.services", return_value=[]):
            out = chat_tools._tool_mine(dict(USUARIO), {}, None)
        self.assertEqual(out["solicitudes"][0]["estado_texto"], "algo_nuevo")


if __name__ == "__main__":
    unittest.main()
