"""Lo que la entrevista recuerda de una visita a la siguiente.

The product owner, 2026-08-01: "next time I have an appointment, the chat should already
know what medication I take and only ask me for updates". Los tests cuidan
las tres promesas de eso: que lo guardado vuelva al prompt de la próxima
entrevista, que lo nuevo pise a lo viejo sin borrar lo que nadie contradijo,
y que nada de esto pueda romper una entrevista (sin base, sin IA o con un
modelo que devuelve basura, la entrevista sigue como si el perfil no
existiera).
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.modules.intake import health_profile  # noqa: E402
import _env  # noqa: E402


class TestNormalizar(unittest.TestCase):
    def test_los_cuatro_campos_siempre_son_listas(self):
        p = health_profile.normalizar({"medicacion": "sertralina 50 mg"})
        self.assertEqual(p["medicacion"], ["sertralina 50 mg"])
        for campo in ("condiciones", "alergias", "antecedentes"):
            self.assertEqual(p[campo], [])

    def test_saca_repetidos_vacios_y_vinetas(self):
        p = health_profile.normalizar({"medicacion": ["- sertralina", "sertralina", "", None,
                                                      {"texto": "metilfenidato 20 mg"}]})
        self.assertEqual(p["medicacion"], ["sertralina", "metilfenidato 20 mg"])

    def test_una_basura_cualquiera_no_levanta(self):
        for basura in (None, "texto suelto", 42, [1, 2], {"medicacion": 7}):
            self.assertEqual(set(health_profile.normalizar(basura)), set(health_profile.CAMPOS))


class TestPromptBlock(unittest.TestCase):
    def test_sin_perfil_no_agrega_nada_al_prompt(self):
        with patch.object(health_profile, "leer", return_value={}):
            self.assertEqual(health_profile.prompt_block("38111222"), "")

    def test_sin_dni_tampoco(self):
        # QR de puerta sin identificar: no hay a quién recordarle nada.
        with patch.object(health_profile, "leer", return_value={}):
            self.assertEqual(health_profile.prompt_block(""), "")

    def test_con_perfil_le_pide_al_modelo_confirmar_no_repreguntar(self):
        perfil = {"datos": health_profile.normalizar(
            {"medicacion": ["sertralina 50 mg a la noche"],
             "alergias": ["alergia a la penicilina"]}), "updated": None}
        with patch.object(health_profile, "leer", return_value=perfil):
            bloque = health_profile.prompt_block("38111222")
        self.assertIn("sertralina 50 mg a la noche", bloque)
        self.assertIn("alergia a la penicilina", bloque)
        self.assertIn("Do not recite this list", bloque)
        self.assertIn("Current patient statements and corrections take priority", bloque)
        # Nunca se le presenta al paciente como un dato que el hospital tiene.
        self.assertIn("not a medical record", bloque)


class _FakeAI:
    """ai_client mínimo: devuelve el JSON que le pasemos."""

    def __init__(self, contenido):
        self.contenido = contenido
        self.llamadas = 0

    def primary_key(self):
        return "k"

    def backup_key(self):
        return ""

    def available(self):
        return True

    def call(self, body, timeout=None, headers_extra=None):
        self.llamadas += 1
        return {"choices": [{"message": {"content": self.contenido}}]}


class TestMerge(unittest.TestCase):
    def setUp(self):
        self.flags = patch.object(health_profile.config, "get", side_effect=lambda k, d=None: "0" if k == "TURNOS_FAKE_AI" else d)
        self.flags.start()
        self.addCleanup(self.flags.stop)

    def _correr(self, contenido, previo=None, guardado=None):
        fake = _FakeAI(contenido)
        guardado = guardado if guardado is not None else []
        previo = previo or {}

        def _leer(dni):
            return {"datos": health_profile.normalizar(previo)} if previo else {}

        def _save(dni, datos, iid=None):
            guardado.append((dni, datos, iid))

        with patch.object(health_profile, "ai_client", fake), \
             patch.object(health_profile, "leer", _leer), \
             patch("app.core.db_chat.chat_save_message"), \
             patch("app.core.db_clinical.health_profile_save", side_effect=_save):
            resultado = health_profile.merge_from_interview(
                {"id": 7, "dni": "38111222"}, ["P: ¿medicación? R: sertralina 50 mg"])
        return resultado, guardado, fake

    def test_devuelve_borrador_sin_guardar_lo_que_el_paciente_conto(self):
        resultado, guardado, _ = self._correr(json.dumps({
            "medicacion": ["sertralina 50 mg a la noche"],
            "condiciones": ["prediabetes"], "alergias": [], "antecedentes": [],
        }))
        self.assertEqual(resultado["medicacion"], ["sertralina 50 mg a la noche"])
        self.assertEqual(guardado, [], "requiere revisión humana antes de guardar")

    def test_sin_dni_no_llama_al_modelo_ni_guarda(self):
        fake = _FakeAI("{}")
        with patch.object(health_profile, "ai_client", fake):
            self.assertEqual(
                health_profile.merge_from_interview({"id": 1, "dni": ""}, ["algo"]), {})
        self.assertEqual(fake.llamadas, 0)

    def test_una_entrevista_que_no_aporta_nada_no_borra_lo_anterior(self):
        previo = {"medicacion": ["sertralina 50 mg a la noche"]}
        resultado, guardado, _ = self._correr("{}", previo=previo)
        self.assertEqual(resultado["medicacion"], ["sertralina 50 mg a la noche"])
        self.assertEqual(guardado, [], "no se puede pisar el perfil con nada")

    def test_una_respuesta_ilegible_del_modelo_no_levanta(self):
        resultado, guardado, _ = self._correr("no soy json", previo={})
        self.assertEqual(resultado, health_profile.normalizar({}))
        self.assertEqual(guardado, [])

    def test_si_el_modelo_explota_la_entrevista_sigue(self):
        class Explota(_FakeAI):
            def call(self, body, timeout=None, headers_extra=None):
                raise TimeoutError("boom")

        with patch.object(health_profile, "ai_client", Explota("{}")):
            self.assertEqual(
                health_profile.merge_from_interview({"id": 1, "dni": "38111222"}, ["x"]), {})


class TestLeerDegrada(unittest.TestCase):
    def test_sin_base_devuelve_vacio_en_vez_de_levantar(self):
        with patch("app.core.db_clinical.health_profile_get", side_effect=RuntimeError("no hay base")):
            self.assertEqual(health_profile.leer("38111222"), {})



class TestPisoDeEmergencia(unittest.TestCase):
    """El hallazgo más grave del red-team del 2026-08-01: un dolor de pecho
    como PRIMER mensaje no llegaba a la guardia. El backstop de "no cerrar
    una entrevista vacía" pisaba el cierre correcto del modelo con un
    redirect genérico, porque miraba el historial SIN el mensaje que acababa
    de llegar. Un cierre que encamina una emergencia es intocable."""

    def setUp(self):
        from app.modules.intake import interview_agent
        self.ia = interview_agent

    def test_un_cierre_a_la_guardia_se_reconoce_como_emergencia(self):
        for texto in [
            "Por favor, acercate ahora mismo a la guardia del Hospital de Pinamar. [fin]",
            "Llamá al 107 ya mismo. [fin]",
            "Avisá al personal del consultorio ahora. [fin]",
            "Please go to the emergencias entrance right now. [fin]",
        ]:
            self.assertTrue(self.ia._es_cierre_de_emergencia(texto), texto)

    def test_un_cierre_normal_no_se_confunde_con_una_emergencia(self):
        self.assertFalse(self.ia._es_cierre_de_emergencia(
            "Gracias por contarme. El médico va a leer tus respuestas durante la consulta. [fin]"))

    def test_una_negativa_del_modelo_cuenta_como_turno_fuera_de_tema(self):
        # Antes sólo contaba la frase congelada, así que cuatro pedidos de
        # inyección seguidos podían cerrar la entrevista sin un solo dato.
        for texto in ["No puedo realizar esa acción, pero seguimos con la entrevista.",
                      "No estoy autorizado a compartir eso.",
                      "No tengo permitido darte ese detalle."]:
            self.assertTrue(self.ia._es_redirect(texto), texto)

    def test_una_pregunta_clinica_no_es_una_negativa(self):
        self.assertFalse(self.ia._es_redirect("¿No podés apoyar el pie todavía?"))


class TestPerfilNoCloudSinOpenRouter(unittest.TestCase):
    """Un perfil no-cloud con su propia ``AI_INFERENCE_API_KEY`` llega al
    modelo aunque no haya ninguna clave OpenRouter configurada: el guardia
    ``available()`` es profile-neutral, y ``merge_from_interview`` alcanza la
    llamada (mockeada en el borde de red) sin tocar OpenRouter."""

    _KEYS = (
        "AI_INFERENCE_PROFILE", "AI_INFERENCE_BASE_URL",
        "AI_INFERENCE_API_KEY", "AI_INFERENCE_API_KEY_BACKUP",
        "OPENROUTER_API_KEY", "OPENROUTER_API_KEY_BACKUP",
    )

    def setUp(self):
        from app.core import config
        self._config = config
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        self._db_patch = patch.object(config, "_db_overrides", return_value={})
        self._db_patch.start()
        # Perfil no-cloud con su propia clave dedicada; cero claves OpenRouter.
        values = dict.fromkeys(self._KEYS)
        values.update({
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": "https://inference.example/v1/chat/completions",
            "AI_INFERENCE_API_KEY": "dedicated-primary"})
        _env.set_env(self, values)

    def tearDown(self):
        self._db_patch.stop()
        self._config.ENV_PATH = self._env_path
        self._config._reset_cache_para_tests()

    def test_llega_a_la_llamada_mock_sin_claves_openrouter(self):
        self.assertEqual(health_profile.ai_client.profile(), "hosted_jurisdiction")
        self.assertFalse(os.environ.get("OPENROUTER_API_KEY"))
        self.assertTrue(health_profile.ai_client.available())

        contenido = json.dumps({"medicacion": ["sertralina 50 mg a la noche"],
                                "condiciones": [], "alergias": [], "antecedentes": []})
        with patch.object(health_profile.ai_client, "_post",
                          return_value={"choices": [{"message": {"content": contenido}}]}) as post, \
             patch.object(health_profile, "leer", return_value={}), \
             patch("app.core.db_chat.chat_save_message"), \
             patch("app.core.db_clinical.health_profile_save", return_value=None):
            with _env.patched_env({"TURNOS_FAKE_AI": "0"}):
                resultado = health_profile.merge_from_interview(
                {"id": 1, "dni": "38111222"},
                ["P: ¿medicación? R: sertralina 50 mg a la noche"])

        post.assert_called_once()
        # Llegó al destino no-cloud con la clave dedicada, no a OpenRouter.
        _body, api_key, _timeout, _headers, base_url = post.call_args.args
        self.assertEqual(api_key, "dedicated-primary")
        self.assertEqual(base_url, "https://inference.example/v1/chat/completions")
        self.assertEqual(resultado["medicacion"], ["sertralina 50 mg a la noche"])


if __name__ == "__main__":
    unittest.main()
