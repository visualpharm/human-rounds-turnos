"""test_intake_agent.py — motor de la entrevista previa
(modules/intake/interview_agent.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_intake_agent.py"

Con TURNOS_FAKE_AI=1 el "modelo" es la secuencia determinística de
_fake_answer (ver interview_agent.py): no depende de la calidad de un
modelo real, sólo de que el LOOP (historial -> pregunta -> [fin] -> status
'done') funcione.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.modules.intake import interview_agent  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakeagent",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestFakeSinBase(unittest.TestCase):
    """_fake_answer/_strip_fin son puras: no tocan la base."""

    def test_primera_pregunta_con_historial_vacio(self):
        self.assertEqual(interview_agent._fake_answer([]),
                         interview_agent._FAKE_PREGUNTAS[0])

    def test_avanza_una_pregunta_por_turno_asistente(self):
        historial = [{"role": "user", "content": "x"},
                     {"role": "assistant", "content": "y"}]
        self.assertEqual(interview_agent._fake_answer(historial),
                         interview_agent._FAKE_PREGUNTAS[1])

    def test_cierra_con_fin_despues_de_agotar_las_preguntas(self):
        historial = []
        for _ in interview_agent._FAKE_PREGUNTAS:
            historial += [{"role": "user", "content": "x"},
                          {"role": "assistant", "content": "y"}]
        respuesta = interview_agent._fake_answer(historial)
        self.assertEqual(respuesta, interview_agent._FAKE_CIERRE)
        self.assertIn("[fin]", respuesta)

    def test_strip_fin_saca_el_marcador(self):
        self.assertEqual(interview_agent._strip_fin("Listo, gracias. [fin]"),
                         "Listo, gracias.")
        self.assertEqual(interview_agent._strip_fin("Sin marcador."),
                         "Sin marcador.")

    def test_strip_fin_tolerante_a_espacios_y_mayusculas(self):
        self.assertEqual(interview_agent._strip_fin("Listo.[ FIN ]"), "Listo.")

    # ---- "Actualizar entrevista" (2026-08-01): reabrir un chat 'done' -----

    def test_reabierta_sin_texto_devuelve_la_pregunta_fija_de_actualizacion(self):
        # Historial largo (como después de un cierre real) no importa: el
        # PRIMER llamado tras un reopen (texto vacío) siempre da la misma
        # pregunta fija, sin mirar cuántos turnos ya había.
        historial = [{"role": "assistant", "content": "algo"}] * 5
        self.assertEqual(interview_agent._fake_answer(historial, "", reabierta=True),
                         interview_agent._FAKE_REABIERTA)

    def test_reabierta_no_afecta_turnos_posteriores(self):
        # Sólo el turno de siembra (texto vacío) usa la pregunta fija — el
        # siguiente turno, ya con texto, vuelve a la secuencia normal.
        self.assertEqual(interview_agent._fake_answer([], "una respuesta", reabierta=True),
                         interview_agent._FAKE_PREGUNTAS[0])

    def test_reabierta_no_pisa_el_escape_hatch_de_markers(self):
        respuesta = interview_agent._fake_answer([], "MARKERS:```codigo```", reabierta=True)
        self.assertEqual(respuesta, "```codigo```")


class TestOpcionesTocables(unittest.TestCase):
    """_parse_opciones (2026-08-01, quick-reply options) — pura, sin base."""

    def test_extrae_las_opciones_y_las_saca_del_texto(self):
        texto, opciones = interview_agent._parse_opciones(
            "¿Tenés fiebre?\n[opciones: Sí | No | No sé]")
        self.assertEqual(texto, "¿Tenés fiebre?")
        self.assertEqual(opciones, ["Sí", "No", "No sé"])

    def test_sin_marcador_no_cambia_nada(self):
        texto, opciones = interview_agent._parse_opciones("Contame más.")
        self.assertEqual(texto, "Contame más.")
        self.assertEqual(opciones, [])

    def test_tolerante_a_espacios(self):
        texto, opciones = interview_agent._parse_opciones(
            "Listo.  [ opciones :  Uno  |  Dos  ]  ")
        self.assertEqual(texto, "Listo.")
        self.assertEqual(opciones, ["Uno", "Dos"])

    def test_tope_de_cuatro_opciones(self):
        texto, opciones = interview_agent._parse_opciones(
            "[opciones: A | B | C | D | E]")
        self.assertEqual(opciones, ["A", "B", "C", "D"])


class TestFiltrarSalidaGuardrail(unittest.TestCase):
    """_filtrar_salida — el backstop regex del guardrail legal (2026-08-01):
    una respuesta con pinta de código nunca llega al paciente tal cual."""

    def test_bloque_de_codigo_se_reemplaza_por_el_redirect(self):
        salida = interview_agent._filtrar_salida("Claro, acá tenés:\n```rm -rf /```")
        self.assertEqual(salida, interview_agent._REDIRECT_ESTANDAR)

    def test_script_tag_se_reemplaza_por_el_redirect(self):
        salida = interview_agent._filtrar_salida("<script>alert(1)</script>")
        self.assertEqual(salida, interview_agent._REDIRECT_ESTANDAR)

    def test_texto_normal_pasa_intacto(self):
        salida = interview_agent._filtrar_salida("¿Desde cuándo tenés dolor?")
        self.assertEqual(salida, "¿Desde cuándo tenés dolor?")

    def test_vacio_no_rompe(self):
        self.assertEqual(interview_agent._filtrar_salida(""), "")
        self.assertEqual(interview_agent._filtrar_salida(None), "")


class TestSinContenidoClinico(unittest.TestCase):
    """sin_contenido_clinico / _es_redirect / _pregunta_apertura (2026-08-02)
    — puras, sin base. Backstop que evita cerrar una entrevista vacía por
    charla off-topic temprana (ver TestOffTopicTempranoConBase para el loop
    completo con MARKERS)."""

    def test_historial_vacio_o_solo_apertura_es_sin_contenido(self):
        self.assertTrue(interview_agent.sin_contenido_clinico([]))
        self.assertTrue(interview_agent.sin_contenido_clinico(
            [{"role": "assistant", "content": "¿Qué te trae hoy?"}]))

    def test_todo_redirect_es_sin_contenido(self):
        historial = [
            {"role": "assistant", "content": "¿Qué te trae hoy?"},
            {"role": "user", "content": "¿qué remedio tomo?"},
            {"role": "assistant", "content": interview_agent._REDIRECT_ESTANDAR},
        ]
        self.assertTrue(interview_agent.sin_contenido_clinico(historial))

    def test_una_respuesta_real_ya_no_es_sin_contenido(self):
        historial = [
            {"role": "assistant", "content": "¿Qué te trae hoy?"},
            {"role": "user", "content": "Me duele la cabeza hace dos días."},
            {"role": "assistant", "content": "¿Algo más?"},
        ]
        self.assertFalse(interview_agent.sin_contenido_clinico(historial))

    def test_pregunta_apertura_devuelve_el_primer_mensaje_del_asistente_limpio(self):
        historial = [{"role": "assistant", "content": "¿Qué te trae? [opciones: A | B]"}]
        self.assertEqual(interview_agent._pregunta_apertura(historial), "¿Qué te trae?")

    def test_pregunta_apertura_vacia_sin_historial(self):
        self.assertEqual(interview_agent._pregunta_apertura([]), "")


class TestBucleConBase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        self.enterContext(_env.patched_env(TURNOS_FAKE_AI="1", INTAKE_MAX_TURNS=None))

    def _interview(self, **over):
        return db_clinical.interview_create(entry_mode="post_booking", matched=True, **over)

    def test_dialogue_metadata_survives_real_storage_and_stays_in_its_interview(self):
        from app.modules.intake import dialogue
        first, second = self._interview(), self._interview()
        interview_agent.run_turn(first, '')
        with mock.patch.object(interview_agent, '_fake_answer', return_value='¿Desde cuándo? [pregunta: onset]'):
            interview_agent.run_turn(first, 'Me duele la rodilla derecha.')
            repeated = interview_agent.run_turn(first, 'Desde ayer.')
            interview_agent.run_turn(second, '')
            independent = interview_agent.run_turn(second, 'Vengo por dolor de hombro.')
        self.assertNotIn('Desde cuándo', repeated['texto'])
        self.assertIn('Desde cuándo', independent['texto'])
        history = db_chat.chat_history('interview', first['session_key'], 200)
        self.assertEqual(history[-1]['intake_topics'], ['additional'])
        self.assertEqual(dialogue.state(history)['answered']['onset']['quote'], 'Desde ayer.')
        with mock.patch.object(interview_agent, '_fake_answer', return_value='Gracias por la corrección.'):
            interview_agent.run_turn(first, 'Me equivoqué: es la izquierda, desde anteayer.')
        history = db_chat.chat_history('interview', first['session_key'], 200)
        self.assertIn('izquierda', dialogue.state(history)['corrections'][-1]['quote'])
        interview_agent.run_turn(first, 'Prefiero terminar.')
        self.assertEqual(db_clinical.interview_get(first['id'])['triage'], 'AMARILLO')
        self.assertEqual(db_clinical.interview_get(second['id'])['status'], 'active')

    def test_urgent_outcome_survives_real_storage_and_reanalysis(self):
        from app.modules.intake import analysis
        interview = self._interview()
        interview_agent.run_turn(interview, '')
        result = interview_agent.run_turn(interview, 'Después del golpe tengo los dedos azules y no siento la mano.')
        self.assertTrue(result['fin'])
        history = db_chat.chat_history('interview', interview['session_key'], 200)
        self.assertEqual(history[-1]['intake_outcome'], 'urgent')
        self.assertEqual(db_clinical.interview_get(interview['id'])['triage'], 'ROJO')
        analysis.analyze_interview(interview)
        self.assertEqual(db_clinical.interview_get(interview['id'])['triage'], 'ROJO')

    def test_imagen_se_persiste_como_mensaje_multiparte(self):
        # 2026-08-01: una foto adjunta arma el mensaje de usuario como lista
        # OpenAI-style (texto + image_url), no como string — así llega tal
        # cual a la API cuando haya un modelo real.
        interview = self._interview()
        interview_agent.run_turn(interview, "")
        imagen = {"mime": "image/png", "data_uri": "data:image/png;base64,QUFB"}
        interview_agent.run_turn(interview, "una mancha rara", imagen=imagen)
        historial = db_chat.chat_history("interview", interview["session_key"], 10)
        mensajes_usuario = [m for m in historial if m.get("role") == "user"]
        self.assertEqual(len(mensajes_usuario), 1)
        contenido = mensajes_usuario[0]["content"]
        self.assertIsInstance(contenido, list)
        self.assertEqual(contenido[0], {"type": "text", "text": "una mancha rara"})
        self.assertEqual(contenido[1]["type"], "image_url")
        self.assertEqual(contenido[1]["image_url"]["url"], imagen["data_uri"])

    def test_primer_turno_sin_texto_siembra_la_primera_pregunta(self):
        # interview_patient.py llama con texto vacío en la primera visita
        # (la caja nunca arranca vacía para el paciente).
        interview = self._interview()
        resultado = interview_agent.run_turn(interview, "")
        self.assertEqual(resultado["texto"], interview_agent._APERTURA)
        self.assertFalse(resultado["fin"])
        historial = db_chat.chat_history("interview", interview["session_key"], 10)
        roles = [m.get("role") for m in historial]
        self.assertEqual(roles, ["assistant"])  # nada de usuario todavía

    def test_secuencia_completa_llega_a_fin_y_nunca_expone_el_marcador(self):
        interview = self._interview()
        texto = ""
        respuestas = []
        fin = False
        for _ in range(len(interview_agent._FAKE_PREGUNTAS) + 2):
            resultado = interview_agent.run_turn(interview, texto)
            respuestas.append(resultado["texto"])
            fin = resultado["fin"]
            texto = "una respuesta cualquiera"
            if fin:
                break
        self.assertTrue(fin)
        for r in respuestas:
            self.assertNotIn("[fin]", r)

    def test_status_pasa_a_done_al_cerrar(self):
        interview = self._interview()
        texto = ""
        for _ in range(len(interview_agent._FAKE_PREGUNTAS) + 2):
            resultado = interview_agent.run_turn(interview, texto)
            texto = "algo"
            if resultado["fin"]:
                break
        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["status"], "done")

    def test_status_sigue_activo_mientras_no_cierra(self):
        interview = self._interview()
        interview_agent.run_turn(interview, "")
        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["status"], "active")

    def test_tope_de_turnos_cierra_la_entrevista(self):
        _env.set_env(self, INTAKE_MAX_TURNS="2")
        interview = self._interview()
        r1 = interview_agent.run_turn(interview, "primero")
        self.assertFalse(r1["fin"])
        r2 = interview_agent.run_turn(interview, "segundo")
        self.assertFalse(r2["fin"])
        r3 = interview_agent.run_turn(interview, "tercero")
        self.assertTrue(r3["fin"])
        self.assertNotIn("[fin]", r3["texto"])
        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["status"], "done")

    # ---- "Actualizar entrevista" (2026-08-01): reabrir con base real ------

    def test_run_turn_reabierta_siembra_la_pregunta_fija_y_conserva_el_historial(self):
        interview = self._interview()
        interview_agent.run_turn(interview, "")
        interview_agent.run_turn(interview, "me duele la cabeza")
        db_clinical.interview_set_status(interview["id"], "done")

        resultado = interview_agent.run_turn(interview, "", reabierta=True)
        self.assertEqual(resultado["texto"], interview_agent._FAKE_REABIERTA)
        self.assertFalse(resultado["fin"])

        historial = db_chat.chat_history("interview", interview["session_key"], 20)
        textos_usuario = [m["content"] for m in historial if m.get("role") == "user"]
        self.assertIn("me duele la cabeza", textos_usuario)  # nada se borró
        self.assertEqual(historial[-1]["content"], interview_agent._FAKE_REABIERTA)

    def test_doctor_id_ausente_no_rompe_el_prompt(self):
        # doctor_id=None (solicitud sin médico asignado todavía): el
        # sistema de prompt tiene que armar igual sin reventar.
        interview = self._interview(doctor_id=None)
        prompt = interview_agent._system_prompt(interview)
        self.assertIn("pre-consult clinical interviewer", prompt)

    def test_entrevista_de_verdad_entry_mode_qr_arma_contexto_corto(self):
        # bug del 911 (2026-08-01): una entrevista real (no un dict a mano)
        # con entry_mode='qr' tiene que traer la directiva corta y NUNCA
        # mandar a la guardia — ver test_prompts.py para el detalle del
        # contenido, acá sólo confirmamos que el wiring real (db row ->
        # _system_prompt) selecciona el contexto correcto.
        interview = db_clinical.interview_create(entry_mode="qr", matched=False)
        prompt = interview_agent._system_prompt(interview)
        self.assertIn("4 or 5", prompt)
        self.assertNotIn("go to the guardia", prompt)
        self.assertNotIn("guardia (emergency room)", prompt)

    # ---- guardrails legales (2026-08-01) ------------------------------------

    def test_fake_ai_flujo_de_pedido_de_medicamento_no_rompe_la_entrevista(self):
        # Bajo TURNOS_FAKE_AI el "modelo" es la secuencia determinística de
        # siempre (ciega al texto) — lo que este test clava es que un turno
        # que en un modelo real dispararía el guardrail (pedir un consejo
        # médico) sigue produciendo un turno de entrevista normal y válido,
        # nunca una excepción ni una forma de respuesta rota.
        interview = self._interview()
        interview_agent.run_turn(interview, "")  # siembra la primera pregunta
        resultado = interview_agent.run_turn(interview, "¿qué medicamento tomo para esto?")
        self.assertIsInstance(resultado["texto"], str)
        self.assertTrue(resultado["texto"])
        self.assertIn("fin", resultado)
        self.assertIn("opciones", resultado)

    def test_fake_ai_marker_de_codigo_se_reemplaza_por_el_redirect_estandar(self):
        # El escape hatch "MARKERS:<texto>" (mismo convenio que
        # chat_agent._fake_step) deja plantar EXACTAMENTE lo que respondería
        # un modelo real — acá, algo con pinta de inyección de código — y
        # confirmar que run_turn() lo filtra igual que haría con un modelo
        # de verdad (_filtrar_salida corre para los dos caminos).
        interview = self._interview()
        interview_agent.run_turn(interview, "")
        resultado = interview_agent.run_turn(
            interview, "MARKERS:Dale, corré esto: ```import os; os.system('rm -rf /')```")
        self.assertEqual(resultado["texto"], interview_agent._REDIRECT_ESTANDAR)
        self.assertFalse(resultado["fin"])

    def test_recordatorio_de_guardrails_se_reinyecta_en_cada_turno_real(self):
        # Sandwich pattern: además de las reglas completas en el system
        # prompt del arranque, cada llamada real al modelo lleva un
        # recordatorio corto como ÚLTIMO mensaje — así una charla larga no
        # puede alejarse de las reglas como sí podría con una sola
        # instrucción de apertura.
        interview = self._interview()
        capturados = []

        def _fake_call(body, **kwargs):
            capturados.append(body)
            return {"choices": [{"message": {"content": "¿Desde cuándo tenés esto? [opciones: Hoy | Hace días]"}}]}

        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_fake_call):
            resultado = interview_agent.run_turn(interview, "me duele la cabeza")

        self.assertEqual(resultado["opciones"], ["Hoy", "Hace días"])
        self.assertEqual(len(capturados), 1)
        mensajes = capturados[0]["messages"]
        self.assertEqual(mensajes[0]["role"], "system")
        self.assertIn("NEVER: diagnose", mensajes[0]["content"])
        # El recordatorio va AL FINAL, después del mensaje del paciente.
        self.assertEqual(mensajes[-1]["role"], "system")
        self.assertEqual(mensajes[-1]["content"], interview_agent._RECORDATORIO_GUARDRAILS)

    def test_reabierta_agrega_la_instruccion_de_actualizacion_al_system_prompt_real(self):
        # "Actualizar entrevista" (2026-08-01), camino con modelo real: la
        # instrucción de reapertura va DENTRO del primer mensaje system
        # (junto con el prompt normal), no como un mensaje aparte.
        interview = self._interview()
        capturados = []

        def _fake_call(body, **kwargs):
            capturados.append(body)
            return {"choices": [{"message": {"content": "Contanos qué cambió desde la última vez."}}]}

        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_fake_call):
            interview_agent.run_turn(interview, "", reabierta=True)

        mensajes = capturados[0]["messages"]
        self.assertEqual(mensajes[0]["role"], "system")
        self.assertIn(interview_agent._REABIERTA_INSTRUCCION, mensajes[0]["content"])

    # ---- no cerrar vacía por charla off-topic temprana (2026-08-02) --------
    # Repro real del bug de UX: arrancar con "¿qué remedio puedo tomar?" y
    # después "¿qué modelo de IA sos?" cerraba la entrevista sin ningún dato
    # clínico — el guardrail de "ofrecer terminar después de 2 redirects"
    # disparaba desde el arranque. TURNOS_FAKE_AI es ciego al texto, así que
    # para simular lo que respondería un modelo real ante cada turno se usa
    # el escape hatch "MARKERS:<texto>" (mismo convenio que
    # TestFiltrarSalidaGuardrail / test_fake_ai_marker_de_codigo_...).

    def test_dos_turnos_off_topic_seguidos_no_cierran_la_entrevista_vacia(self):
        interview = self._interview()
        apertura = interview_agent.run_turn(interview, "")["texto"]

        # Turno 1 fuera de tema: el modelo real redirige, sin intentar cerrar.
        r1 = interview_agent.run_turn(
            interview, "MARKERS:" + interview_agent._REDIRECT_ESTANDAR)
        self.assertFalse(r1["fin"])

        # Turno 2 fuera de tema: el bug real — el modelo ya intenta cerrar
        # sin haber capturado ningún dato clínico. El backstop de código lo
        # tiene que ignorar: nunca cierra, y vuelve a preguntar la apertura.
        cierre_prematuro = (interview_agent._REDIRECT_FRASE +
                            " Si preferís, lo dejamos acá y el equipo lo ve "
                            "en la consulta. [fin]")
        r2 = interview_agent.run_turn(interview, "MARKERS:" + cierre_prematuro)
        self.assertFalse(r2["fin"])
        self.assertNotIn("[fin]", r2["texto"])
        self.assertIn(apertura[apertura.rfind('¿'):], r2["texto"])
        self.assertNotIn('inteligencia artificial', r2["texto"])
        self.assertEqual(db_clinical.interview_get(interview["id"])["status"], "active")

    def test_al_tercer_turno_off_topic_seguido_se_permite_cerrar_y_el_triage_no_es_verde(self):
        interview = self._interview()
        interview_agent.run_turn(interview, "")
        cierre_prematuro = (interview_agent._REDIRECT_FRASE +
                            " Si preferís, lo dejamos acá y el equipo lo ve "
                            "en la consulta. [fin]")
        interview_agent.run_turn(  # turno 1: redirige normal
            interview, "MARKERS:" + interview_agent._REDIRECT_ESTANDAR)
        r2 = interview_agent.run_turn(  # turno 2: bloqueado por el backstop
            interview, "MARKERS:" + cierre_prematuro)
        self.assertFalse(r2["fin"])
        r3 = interview_agent.run_turn(  # turno 3 seguido fuera de tema
            interview, "MARKERS:" + cierre_prematuro)
        self.assertTrue(r3["fin"])

        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["status"], "done")
        # Cero dato clínico real: el triage nunca puede sonar a "todo bien".
        self.assertEqual(actualizada["triage"], "AMARILLO")


if __name__ == "__main__":
    unittest.main()
