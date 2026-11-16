"""test_intake_consult_compose.py — el gate de IA de
modules/intake/consult_compose.py (fase 8).

Lo que se prueba: `compose()` es determinístico por default (instalación de
fábrica no llama a la red) y el pase de IA opcional se prende ÚNICAMENTE
cuando el hospital editó "entrevista_receta"/"entrevista_orden" en el panel
(`prompts.is_edited`) — nunca por tener una API key configurada sola. Una
falla del pase (red, JSON roto) deja parada la versión determinística.

Correr:  python3 -W error::ResourceWarning -m unittest discover -s tests \
         -p "test_intake_consult_compose.py"

No usa base de datos: `prompts._overrides` se parchea directo, mismo truco
que test_prompts.py, así que corre sin Postgres.
"""

import os
import sys
import json
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.ai import prompts  # noqa: E402
from app.modules.intake import consult_compose  # noqa: E402

_DOCTOR = {"nombre": "Dra. Fernández", "matricula": "MP1234", "especialidad": "Cardiología"}
_PACIENTE = {"nombre": "Juan Pérez", "dni": "30123456"}
_MEDICACION = [{"nombre": "Ibuprofeno", "dosis": "400 mg", "frecuencia": "cada 8 h", "duracion": "5 días"}]
_ESTUDIOS = [{"label": "Hemograma"}]
_DIAGNOSTICOS = [{"label": "Hipertensión arterial esencial"}]


def _override(clave, texto_en, editado):
    return {clave: {"clave": clave, "texto_en": texto_en, "texto_es": None,
                     "editado": editado}}


class _SinRed(unittest.TestCase):
    """Base común: sin TURNOS_FAKE_AI (para no pisar el propio gate que se
    está probando) y con una key "presente" pero sin pegarle nunca a la red
    salvo que el propio test lo espere."""

    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI=None)
        self._real_overrides = prompts._overrides
        prompts._overrides = lambda: {}
        prompts.invalidate_cache()
        self._key_patch = mock.patch("app.ai.ai_client.primary_key", return_value="fake-key-123")
        self._key_patch.start()
        self._avail_patch = mock.patch("app.ai.ai_client.available", return_value=True)
        self._avail_patch.start()

    def tearDown(self):
        self._avail_patch.stop()
        self._key_patch.stop()
        prompts._overrides = self._real_overrides
        prompts.invalidate_cache()

    def _compose(self):
        return consult_compose.compose(_DOCTOR, _PACIENTE, _DIAGNOSTICOS,
                                       _ESTUDIOS, _MEDICACION, "notas de control")


class SinEdicionTestCase(_SinRed):
    """Instalación de fábrica: ningún prompt de entrevista_receta/orden
    editado -> compose() nunca llama a ai_client.call, aunque haya API key."""

    def test_no_llama_a_ai_client_con_ambos_sin_editar(self):
        with mock.patch("app.ai.ai_client.call", side_effect=AssertionError(
                "no debía llamar a la red sin edición del panel")):
            receta, orden = self._compose()
        self.assertIn("Ibuprofeno", receta["lineas"][0])
        self.assertIn("Hipertensión arterial esencial", orden["diagnosticos"][0])

    def test_modifier_edited_es_false_para_las_dos_claves(self):
        self.assertFalse(consult_compose._modifier_edited("receta"))
        self.assertFalse(consult_compose._modifier_edited("orden"))


class EdicionTestCase(_SinRed):
    """El hospital editó una instrucción en el panel: el pase corre y la
    lleva en el pedido al modelo."""

    def test_receta_editada_llama_a_ai_client_con_el_modifier_en_el_pedido(self):
        prompts._overrides = lambda: _override(
            "entrevista_receta", "Usar la sustancia activa en vez del nombre comercial.", True)
        prompts.invalidate_cache()

        captured = {}

        def fake_call(body, timeout=30, headers_extra=None):
            captured["body"] = body
            content = json.dumps({"lineas": ["Ibuprofeno — 400 mg — cada 8 h — 5 días"],
                                  "espacio_superior_cm": 0})
            return {"choices": [{"message": {"content": content}}], "usage": {}}

        with mock.patch("app.ai.ai_client.call", side_effect=fake_call) as mocked:
            receta, orden = self._compose()

        self.assertTrue(mocked.called)
        prompt_text = captured["body"]["messages"][0]["content"]
        self.assertIn("Usar la sustancia activa en vez del nombre comercial.", prompt_text)
        self.assertIn("Ibuprofeno", receta["lineas"][0])

    def test_editar_solo_receta_deja_orden_deterministica_sin_llamar(self):
        prompts._overrides = lambda: _override("entrevista_receta", "Usar sustancia activa.", True)
        prompts.invalidate_cache()

        llamadas = []

        def fake_call(body, timeout=30, headers_extra=None):
            llamadas.append(headers_extra.get("X-Title", ""))
            content = json.dumps({"lineas": ["Ibuprofeno — 400 mg — cada 8 h — 5 días"]})
            return {"choices": [{"message": {"content": content}}], "usage": {}}

        with mock.patch("app.ai.ai_client.call", side_effect=fake_call):
            self._compose()

        self.assertEqual(len(llamadas), 1)
        self.assertIn("receta", llamadas[0])

    def test_falla_de_red_deja_parada_la_version_deterministica(self):
        prompts._overrides = lambda: _override("entrevista_orden", "Dejar notas verbatim.", True)
        prompts.invalidate_cache()

        with mock.patch("app.ai.ai_client.call", side_effect=TimeoutError("boom")):
            receta, orden = self._compose()

        # el pase falló, pero compose() nunca lanza: queda la composición
        # determinística de siempre.
        self.assertIn("Hipertensión arterial esencial", orden["diagnosticos"][0])
        self.assertIn("Hemograma", orden["estudios"])

    def test_json_roto_deja_parada_la_version_deterministica(self):
        prompts._overrides = lambda: _override("entrevista_orden", "Dejar notas verbatim.", True)
        prompts.invalidate_cache()

        with mock.patch("app.ai.ai_client.call",
                        return_value={"choices": [{"message": {"content": "esto no es json"}}],
                                     "usage": {}}):
            receta, orden = self._compose()

        self.assertIn("Hemograma", orden["estudios"])

    def test_modifier_edited_es_true_solo_para_la_clave_editada(self):
        prompts._overrides = lambda: _override("entrevista_orden", "algo", True)
        prompts.invalidate_cache()
        self.assertTrue(consult_compose._modifier_edited("orden"))
        self.assertFalse(consult_compose._modifier_edited("receta"))


if __name__ == "__main__":
    unittest.main()
