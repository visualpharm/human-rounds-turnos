"""test_intake_analysis.py — fase 3 de la entrevista previa
(modules/intake/redflags.py + modules/intake/analysis.py).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_intake_analysis.py"
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
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.modules.intake import redflags, analysis, interview_agent  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakeanalysis",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


# =============================================================================
# redflags.py — puro, sin base
# =============================================================================

class TestTriageOrder(unittest.TestCase):
    def test_orden_de_mas_a_menos_urgente(self):
        self.assertEqual(redflags.TRIAGE_ORDER, ["ROJO", "NARANJA", "AMARILLO", "VERDE"])

    def test_more_urgent_elige_el_mas_urgente(self):
        self.assertEqual(redflags.more_urgent("ROJO", "VERDE"), "ROJO")
        self.assertEqual(redflags.more_urgent("VERDE", "ROJO"), "ROJO")
        self.assertEqual(redflags.more_urgent("NARANJA", "AMARILLO"), "NARANJA")
        self.assertEqual(redflags.more_urgent("AMARILLO", "AMARILLO"), "AMARILLO")

    def test_more_urgent_nunca_baja_de_nivel(self):
        # Reglas SOLO escalan: el resultado nunca es menos urgente que el
        # más urgente de los dos valores de entrada.
        for a in redflags.TRIAGE_ORDER:
            for b in redflags.TRIAGE_ORDER:
                resultado = redflags.more_urgent(a, b)
                ia, ib = redflags.TRIAGE_ORDER.index(a), redflags.TRIAGE_ORDER.index(b)
                self.assertEqual(redflags.TRIAGE_ORDER.index(resultado), min(ia, ib))

    def test_more_urgent_tolera_none_y_basura(self):
        self.assertEqual(redflags.more_urgent(None, "AMARILLO"), "AMARILLO")
        self.assertEqual(redflags.more_urgent("AMARILLO", None), "AMARILLO")
        self.assertIsNone(redflags.more_urgent(None, None))
        self.assertEqual(redflags.more_urgent("cualquier_cosa", "NARANJA"), "NARANJA")
        self.assertEqual(redflags.more_urgent("NARANJA", "cualquier_cosa"), "NARANJA")


class TestRedFlagsScanner(unittest.TestCase):
    """Cada regla dispara con su frase canónica, en versión CON tilde y SIN
    tilde (el escaneo tiene que ser insensible a acentos — el paciente
    dicta o tipea sin preocuparse por eso)."""

    def _codes(self, texto):
        r = redflags.evaluate(texto)
        return r["triage"], [f["code"] for f in r["flags"]]

    def test_dolor_pecho_en_reposo_rojo(self):
        triage, codes = self._codes("Siento un dolor en el tórax estando en reposo.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("dolor_pecho_reposo", codes)
        triage, codes = self._codes("Tengo dolor en el pecho, sin hacer nada, hace un rato.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("dolor_pecho_reposo", codes)

    def test_dolor_pecho_opresivo_rojo(self):
        triage, codes = self._codes("Tengo opresión en el pecho desde ayer.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("dolor_pecho_opresivo", codes)
        triage, codes = self._codes("El pecho me aprieta como si tuviera un peso.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("dolor_pecho_opresivo", codes)

    def test_falta_de_aire_en_reposo_rojo(self):
        triage, codes = self._codes("Me ahogo aún estando quieto.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("falta_aire_reposo", codes)
        triage, codes = self._codes("Tengo dificultad para respirar, incluso en reposo.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("falta_aire_reposo", codes)

    def test_sincope_naranja(self):
        triage, codes = self._codes("Tuve un síncope hace un rato.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("sincope", codes)
        triage, codes = self._codes("Me desmaye sin razón esta mañana.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("sincope", codes)

    def test_sangrado_abundante_naranja(self):
        triage, codes = self._codes("Tengo un sangrado abundante que no para.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("sangrado_abundante", codes)
        triage, codes = self._codes("No para de sangrar desde hace una hora.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("sangrado_abundante", codes)

    def test_signos_de_acv_rojo(self):
        triage, codes = self._codes("Se me durmió la cara de golpe.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("signos_acv", codes)
        triage, codes = self._codes("Arrastro las palabras al hablar.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("signos_acv", codes)

    def test_ideacion_suicida_rojo(self):
        triage, codes = self._codes("No quiero vivir más, la verdad.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("ideacion_suicida", codes)
        triage, codes = self._codes("Quiero matarme, no doy mas.")
        self.assertEqual(triage, "ROJO")
        self.assertIn("ideacion_suicida", codes)

    def test_fiebre_con_rigidez_de_nuca_naranja(self):
        triage, codes = self._codes("Tengo fiebre y no puedo doblar el cuello.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("fiebre_rigidez_nuca", codes)
        triage, codes = self._codes("Tengo fiebre alta y rigidez de nuca desde ayer.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("fiebre_rigidez_nuca", codes)

    def test_dolor_abdominal_severo_naranja(self):
        triage, codes = self._codes("Tengo un dolor abdominal muy fuerte desde anoche.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("dolor_abdominal_severo", codes)
        triage, codes = self._codes("Me agarró un dolor de panza insoportable.")
        self.assertEqual(triage, "NARANJA")
        self.assertIn("dolor_abdominal_severo", codes)

    def test_texto_benigno_no_dispara_nada(self):
        for texto in ("Vengo por un control de rutina, me siento bien.",
                      "Me duele un poco la cabeza desde ayer.",
                      "Tengo dolor de panza pero es leve, ya mejoró."):
            triage, codes = self._codes(texto)
            self.assertIsNone(triage, texto)
            self.assertEqual(codes, [], texto)

    def test_texto_vacio_no_dispara_nada(self):
        r = redflags.evaluate([])
        self.assertIsNone(r["triage"])
        self.assertEqual(r["flags"], [])
        r = redflags.evaluate(["   ", ""])
        self.assertIsNone(r["triage"])

    def test_dos_banderas_el_triage_es_el_mas_urgente(self):
        # síncope (NARANJA) + dolor opresivo en el pecho (ROJO) en el mismo
        # texto -> el resultado combinado es ROJO, no el último que matcheó.
        r = redflags.evaluate("Me desmaye. Después sentí opresión en el pecho.")
        self.assertEqual(r["triage"], "ROJO")
        codes = {f["code"] for f in r["flags"]}
        self.assertIn("sincope", codes)
        self.assertIn("dolor_pecho_opresivo", codes)

    def test_because_cita_la_oracion_que_disparo(self):
        r = redflags.evaluate("Vengo por un control. Tengo opresión en el pecho hace rato.")
        flag = next(f for f in r["flags"] if f["code"] == "dolor_pecho_opresivo")
        self.assertIn("opresi", flag["because"].lower())

    def test_exams_for_devuelve_estudios_de_las_reglas_disparadas(self):
        r = redflags.evaluate("Tengo dolor en el pecho, en reposo, hace una hora.")
        exams = redflags.exams_for(r["flags"])
        self.assertIn("ecg", exams)

    def test_exams_for_vacio_sin_flags(self):
        self.assertEqual(redflags.exams_for([]), [])


# =============================================================================
# analysis._coerce — puro, sin base
# =============================================================================

class TestCoerce(unittest.TestCase):
    def setUp(self):
        self.exam_ids = {"ecg", "rx-torax"}
        self.dx_ids = {"hta", "iam"}

    def test_triage_valido_pasa(self):
        out = analysis._coerce({"triage": "rojo"}, self.exam_ids, self.dx_ids)
        self.assertEqual(out["triage"], "ROJO")

    def test_triage_invalido_se_descarta(self):
        out = analysis._coerce({"triage": "no-se"}, self.exam_ids, self.dx_ids)
        self.assertIsNone(out["triage"])
        out = analysis._coerce({}, self.exam_ids, self.dx_ids)
        self.assertIsNone(out["triage"])

    def test_ids_desconocidos_de_exams_y_diferencial_se_descartan(self):
        out = analysis._coerce({
            "suggested_exams": ["ecg", "estudio-inventado", "rx-torax"],
            "differential": [{"id": "hta", "prob": 0.5}, {"id": "diagnostico-inventado", "prob": 0.9}],
        }, self.exam_ids, self.dx_ids)
        self.assertEqual(out["suggested_exams"], ["ecg", "rx-torax"])
        self.assertEqual([d["id"] for d in out["differential"]], ["hta"])

    def test_probabilidades_se_clampean_0_1(self):
        out = analysis._coerce({
            "differential": [{"id": "hta", "prob": 5}, {"id": "iam", "prob": -3}],
        }, self.exam_ids, self.dx_ids)
        # prob -3 clampea a 0 -> se descarta (prob<=0 no aporta nada al
        # diferencial); prob 5 clampea a 1.
        ids_probs = {d["id"]: d["prob"] for d in out["differential"]}
        self.assertEqual(ids_probs.get("hta"), 1.0)
        self.assertNotIn("iam", ids_probs)

    def test_differential_ordenado_por_probabilidad_descendente(self):
        out = analysis._coerce({
            "differential": [{"id": "hta", "prob": 0.2}, {"id": "iam", "prob": 0.8}],
        }, self.exam_ids, self.dx_ids)
        self.assertEqual([d["id"] for d in out["differential"]], ["iam", "hta"])

    def test_brief_se_ordena_por_prioridad_y_se_capea_a_6(self):
        raw = [{"priority": i, "point": f"punto {i}"} for i in range(10, 0, -1)]
        out = analysis._coerce({"brief": raw}, self.exam_ids, self.dx_ids)
        self.assertEqual(len(out["brief"]), 6)
        prioridades = [b["priority"] for b in out["brief"]]
        self.assertEqual(prioridades, sorted(prioridades))

    def test_brief_sin_prioridad_usa_el_indice(self):
        out = analysis._coerce({"brief": [{"point": "a"}, {"point": "b"}]},
                               self.exam_ids, self.dx_ids)
        self.assertEqual([b["priority"] for b in out["brief"]], [1, 2])

    def test_brief_con_puntos_vacios_se_descarta(self):
        out = analysis._coerce({"brief": [{"priority": 1, "point": "  "},
                                         {"priority": 2, "point": "real"}]},
                               self.exam_ids, self.dx_ids)
        self.assertEqual(len(out["brief"]), 1)
        self.assertEqual(out["brief"][0]["point"], "real")

    def test_one_liner_se_recorta_a_140(self):
        out = analysis._coerce({"one_liner": "x" * 300}, self.exam_ids, self.dx_ids)
        self.assertEqual(len(out["one_liner"]), 140)

    def test_valores_no_dict_no_rompen(self):
        out = analysis._coerce(None, self.exam_ids, self.dx_ids)
        self.assertEqual(out, {"one_liner": "", "triage": None, "brief": [],
                               "differential": [], "suggested_exams": []})
        out = analysis._coerce({"brief": "no es una lista", "differential": 5,
                                "suggested_exams": {"no": "es lista"}},
                               self.exam_ids, self.dx_ids)
        self.assertEqual(out["brief"], [])
        self.assertEqual(out["differential"], [])
        self.assertEqual(out["suggested_exams"], [])


# =============================================================================
# analysis.analyze_interview — camino determinístico (TURNOS_FAKE_AI=1)
# =============================================================================

class AnalyzeInterviewTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")

    def _interview(self):
        return db_clinical.interview_create(entry_mode="post_booking", matched=True)

    def _seed(self, session_key, mensajes):
        for m in mensajes:
            db_chat.chat_save_message("interview", session_key, m)

    def test_sin_mensajes_de_usuario_da_amarillo_sin_llamar_ia(self):
        # 2026-08-02: VERDE + "sin síntomas relatados" sonaba a "está bien"
        # cuando en realidad no se sabe nada — no debe leerse como una
        # entrevista completada normalmente (ver analysis.py docstring).
        interview = self._interview()
        resultado = analysis.analyze_interview(interview)
        self.assertEqual(resultado["triage"], "AMARILLO")
        self.assertNotIn("Sin síntomas relatados", resultado["summary"])
        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["triage"], "AMARILLO")

    def test_sin_mensajes_pero_con_solo_asistente_tambien_da_amarillo(self):
        interview = self._interview()
        self._seed(interview["session_key"],
                   [{"role": "assistant", "content": "¿Qué te trae hoy a la consulta?"}])
        resultado = analysis.analyze_interview(interview)
        self.assertEqual(resultado["triage"], "AMARILLO")

    def test_fake_sin_banderas_usa_la_primer_respuesta_como_resumen(self):
        interview = self._interview()
        self._seed(interview["session_key"], [
            {"role": "assistant", "content": "¿Qué te trae hoy?"},
            {"role": "user", "content": "Vengo por un control de rutina, nada en particular."},
        ])
        resultado = analysis.analyze_interview(interview)
        self.assertEqual(resultado["ai_model"], "fake")
        self.assertEqual(resultado["triage"], "AMARILLO")
        self.assertTrue(resultado["summary"].startswith("Vengo por un control"))
        self.assertEqual(resultado["differential"], [])

    def test_fake_con_bandera_roja_escala_el_triage_y_arma_el_brief(self):
        interview = self._interview()
        self._seed(interview["session_key"], [
            {"role": "assistant", "content": "¿Qué te trae hoy?"},
            {"role": "user", "content": "Tengo un dolor en el pecho, en reposo, hace una hora."},
        ])
        resultado = analysis.analyze_interview(interview)
        self.assertEqual(resultado["ai_model"], "fake")
        self.assertEqual(resultado["triage"], "ROJO")
        self.assertTrue(resultado["red_flags"])
        self.assertIn("ecg", resultado["suggested_exams"])
        self.assertTrue(resultado["brief"])
        self.assertEqual(resultado["red_flags"][0]["code"], "dolor_pecho_reposo")

        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["triage"], "ROJO")
        self.assertEqual(actualizada["ai_model"], "fake")
        self.assertTrue(actualizada["red_flags"])
        self.assertIsNotNone(actualizada["analyzed_at"])

    def test_seed_ficticio_puede_pedir_una_lectura_real_aunque_fake_este_activo(self):
        interview = self._interview()
        self._seed(interview["session_key"], [
            {"role": "assistant", "content": "¿Qué te trae hoy?"},
            {"role": "user", "content": "Quiero controlar mi glucemia y mi presión."},
        ])
        response = {
            "choices": [{"message": {"content": json.dumps({
                "one_liner": "Control de glucemia y presión.",
                "triage": "AMARILLO",
                "brief": [{"priority": 1, "point": "Revisar adherencia y mediciones."}],
                "differential": [],
                "suggested_exams": ["glucemia"],
            })}}],
            "usage": {"cost": 0.001},
        }
        with mock.patch.object(analysis.ai_client, "primary_key", return_value="demo-key"), \
                mock.patch.object(analysis.ai_client, "available", return_value=True), \
                mock.patch.object(analysis, "_call_openrouter", return_value=response) as call:
            result = analysis.analyze_interview(
                interview, allow_network_when_fake=True)

        call.assert_called_once()
        self.assertEqual(result["ai_model"], analysis.DEFAULT_MODEL)
        self.assertEqual(result["brief"][0]["point"], "Revisar adherencia y mediciones.")
        self.assertEqual(result["suggested_exams"], ["glucemia"])

    def test_sin_contenido_clinico_pero_con_bandera_roja_escala_igual(self):
        # 2026-08-02: aunque la charla se haya ido del tema (sin_contenido_
        # clinico=True), el piso de seguridad de banderas rojas nunca se
        # salta — si el paciente dejó algo que las reglas reconocen, eso
        # escala el triage igual.
        interview = self._interview()
        self._seed(interview["session_key"], [
            {"role": "assistant", "content": "¿Qué te trae hoy a la consulta?"},
            {"role": "user", "content": "¿Qué modelo de IA sos? Ya no quiero vivir más."},
            {"role": "assistant", "content": interview_agent._REDIRECT_ESTANDAR},
        ])
        resultado = analysis.analyze_interview(interview)
        self.assertEqual(resultado["triage"], "ROJO")
        self.assertTrue(resultado["red_flags"])

    def test_interview_inexistente_no_rompe(self):
        # analyze_interview nunca lanza, incluso con un dict incompleto.
        resultado = analysis.analyze_interview({"id": 999999999, "session_key": "no-existe"})
        self.assertEqual(resultado["triage"], "AMARILLO")

    def test_none_no_rompe(self):
        self.assertIsNone(analysis.analyze_interview(None))


class InterviewLoopClosesWithAnalysisTestCase(unittest.TestCase):
    """El loop de fase 2 (interview_agent.run_turn) hasta [fin] deja el
    análisis de fase 3 guardado en la fila — la integración completa, no
    sólo analyze_interview() llamada a mano."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")

    def test_entrevista_fake_completa_termina_con_analisis_guardado(self):
        interview = db_clinical.interview_create(entry_mode="post_booking", matched=True)
        texto = ""
        fin = False
        for _ in range(len(interview_agent._FAKE_PREGUNTAS) + 2):
            resultado = interview_agent.run_turn(interview, texto)
            fin = resultado["fin"]
            texto = "Tengo dolor en el pecho hace un rato, en reposo."
            if fin:
                break
        self.assertTrue(fin)
        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["status"], "done")
        self.assertIsNotNone(actualizada["triage"])
        self.assertIsNotNone(actualizada["analyzed_at"])
        self.assertEqual(actualizada["ai_model"], "fake")


class TestAnalysisPersist(unittest.TestCase):
    """Pure test for analysis._persist() — no database needed."""

    def test_persist_records_triage_from_interview_not_from_analysis(self):
        """_persist should record the interview's triage, not the analysis triage."""
        interview = {"id": 1, "triage": "ROJO"}
        analysis_data = {
            "triage": "VERDE",
            "summary": "",
            "brief": [],
            "differential": [],
            "suggested_exams": [],
            "red_flags": [],
            "ai_model": "x"
        }

        with mock.patch.object(db_clinical, "interview_update_analysis") as mock_update:
            analysis._persist(interview, analysis_data)

            # Verify that interview_update_analysis was called with the interview's triage (ROJO)
            # not the analysis's triage (VERDE)
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            # The triage argument should be the 2nd positional arg (after interview_id)
            self.assertEqual(call_args[0][1], "ROJO")


if __name__ == "__main__":
    unittest.main()
