"""test_remedies_steps.py — characterization of remedies.remedy().

test_remedies_i18n.py pins the TEXT each rejection cause produces; this file
pins how remedy() gets there: which helpers it calls, in which order and with
which arguments, what it returns when a step finds nothing, and that nothing a
helper raises ever escapes. Every collaborator that reaches the catalog or the
schedule is replaced by a recorder, so the tests need no database or pack.

Run:  python3 -m unittest discover -s tests -p test_remedies_steps.py
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.turnos import remedies  # noqa: E402

BASE = "https://x.ar"
ACTUAL = {"id": "cardio", "nombre": "Cardiología", "centro": "hospital"}
CLINICO = {"id": "mg", "nombre": "Medicina general", "centro": "caps"}
DESTINO = {"id": "traumato", "nombre": "Traumatología", "centro": "hospital"}
OTRO = {"id": "psiq-2", "nombre": "Psiquiatría", "centro": "diabetpin"}
TURNOS = [{"dia": "2026-08-04", "hora": "09:00", "cuando": "Martes 4 de agosto, 09:00", "medico": "Dra. Ruiz"}]

REUPLOAD = ["El documento subido no es una orden médica.", "La orden no se lee con claridad.",
            "La orden está a nombre de otra persona.", "El DNI de la orden no coincide con el de la cuenta."]
NEW_REFERRAL = ["La orden está vencida.", "Falta la firma o la matrícula del médico.",
                "La orden no tiene fecha.",
                "La orden fue emitida en otra jurisdicción y no corresponde a este centro."]
WRONG_SERVICE = "La orden no corresponde al servicio solicitado."
OTHER_CENTER = ["No hay turnos disponibles para este servicio por ahora.",
                "El servicio solicitado no se realiza en este centro."]


class _RemedyCase(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.row = {"servicio_id": "cardio", "centro_slug": "hospital"}
        self.stub("_base", BASE)
        self.stub("_service_by_id", ACTUAL)
        self.stub("_localized", lambda s, lang: s)
        self.stub("_center_name", "")
        self.stub("slots_for", TURNOS)
        self.stub("find_service", CLINICO)
        self.stub("suggested_service", {"id": "traumato"})
        self.stub("_mismo_servicio_otro_centro", OTRO)
        self.stub("_service_name", "Nombre de respaldo")
        self.wrap("_slot_actions")

    def stub(self, name, result):
        def fake(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if isinstance(result, Exception):
                raise result
            return result(*args, **kwargs) if callable(result) else result
        p = mock.patch.object(remedies, name, side_effect=fake)
        self.addCleanup(p.stop)
        p.start()

    def wrap(self, name):
        original = getattr(remedies, name)
        self.stub(name, original)

    def names(self):
        return [c[0] for c in self.calls]

    def call(self, name, nth=0):
        return [c for c in self.calls if c[0] == name][nth]


class TestEntry(_RemedyCase):
    def test_free_text_reason_returns_none_before_touching_anything(self):
        self.assertIsNone(remedies.remedy(self.row, "El paciente ya se atendió por guardia"))
        self.assertEqual(self.calls, [])

    def test_setup_resolves_the_base_then_the_requested_service(self):
        handler = object()
        remedies.remedy(self.row, REUPLOAD[0], handler)
        self.assertEqual(self.calls[0], ("_base", (handler,), {}))
        self.assertEqual(self.calls[1], ("_service_by_id", ("cardio",), {}))

    def test_missing_language_falls_back_to_the_active_one(self):
        for lang in (None, ""):
            with self.subTest(lang=lang):
                self.calls.clear()
                with mock.patch.object(remedies.i18n, "active", return_value="en") as active:
                    rem = remedies.remedy(self.row, REUPLOAD[0], lang=lang)
                active.assert_called_once_with()
                self.assertEqual(self.call("_localized")[1], (ACTUAL, "en"))
                self.assertEqual(rem["acciones"][0]["texto"], "Upload the referral")

    def test_explicit_language_never_asks_for_the_active_one(self):
        with mock.patch.object(remedies.i18n, "active", side_effect=AssertionError("asked")):
            rem = remedies.remedy(self.row, REUPLOAD[0], lang="en")
        self.assertEqual(rem["acciones"][0]["texto"], "Upload the referral")

    def test_anything_a_step_raises_comes_back_as_none(self):
        everywhere = REUPLOAD + NEW_REFERRAL + [WRONG_SERVICE] + OTHER_CENTER
        schedule = NEW_REFERRAL + [WRONG_SERVICE] + OTHER_CENTER
        reached = {
            "_base": everywhere, "_service_by_id": everywhere, "_localized": everywhere,
            "slots_for": schedule, "_slot_actions": schedule,
            "find_service": NEW_REFERRAL, "suggested_service": [WRONG_SERVICE],
            "_mismo_servicio_otro_centro": OTHER_CENTER,
        }
        for target, motivos in reached.items():
            for motivo in motivos:
                with self.subTest(target=target, motivo=motivo):
                    with mock.patch.object(remedies, target, side_effect=RuntimeError("boom")):
                        self.assertIsNone(remedies.remedy(self.row, motivo, lang="es"))

    def test_a_row_that_is_not_a_mapping_returns_none(self):
        self.assertIsNone(remedies.remedy(None, NEW_REFERRAL[0], lang="es"))
        self.assertEqual(self.names(), ["_base"])


class TestReupload(_RemedyCase):
    def test_only_the_localized_requested_service_is_used(self):
        for motivo in REUPLOAD:
            with self.subTest(motivo=motivo):
                self.calls.clear()
                rem = remedies.remedy(self.row, motivo, lang="es")
                self.assertEqual(self.names(), ["_base", "_service_by_id", "_localized"])
                self.assertEqual(self.call("_localized")[1], (ACTUAL, "es"))
                self.assertEqual(set(rem), {"titulo", "detalle", "acciones", "turnos"})
                self.assertEqual(rem["turnos"], [])
                self.assertEqual(rem["acciones"], [{
                    "texto": "Subir la orden", "url": f"{BASE}/upload-order?service=cardio"}])
                self.assertIn("Cardiología", rem["detalle"])

    def test_title_depends_on_the_cause(self):
        titles = {m: remedies.remedy(self.row, m, lang="es")["titulo"] for m in REUPLOAD}
        self.assertEqual(titles[REUPLOAD[0]], "Subí la orden médica y listo")
        self.assertEqual(titles[REUPLOAD[1]], "Subí una foto más nítida de la orden y listo")
        self.assertEqual(titles[REUPLOAD[2]], "Subí la orden que está a tu nombre y listo")
        self.assertEqual(titles[REUPLOAD[3]], titles[REUPLOAD[2]])

    def test_name_falls_back_to_the_booking_lookup_when_the_catalog_has_none(self):
        self.stub("_service_by_id", None)
        rem = remedies.remedy(self.row, REUPLOAD[0], lang="es")
        self.assertEqual(self.call("_service_name")[1], ("cardio",))
        self.assertIn("Nombre de respaldo", rem["detalle"])
        self.calls.clear()
        self.stub("_service_by_id", ACTUAL)
        self.stub("_localized", lambda s, lang: {"id": "cardio"})
        rem = remedies.remedy(self.row, REUPLOAD[0], lang="es")
        self.assertEqual(self.call("_service_name")[1], ("cardio",))
        self.assertIn("Nombre de respaldo", rem["detalle"])

    def test_catalog_name_wins_over_the_fallback(self):
        remedies.remedy(self.row, REUPLOAD[0], lang="es")
        self.assertNotIn("_service_name", self.names())


class TestNewReferral(_RemedyCase):
    def test_every_cause_offers_the_general_practitioner_in_this_order(self):
        for motivo in NEW_REFERRAL:
            with self.subTest(motivo=motivo):
                self.calls.clear()
                self.stub("_localized", lambda s, lang: {**s, "nombre": "Local " + s["nombre"]})
                rem = remedies.remedy(self.row, motivo, lang="en")
                self.assertEqual(self.names(), [
                    "_base", "_service_by_id", "find_service", "slots_for", "_localized",
                    "_slot_actions", "_center_name"])
                self.assertEqual(self.call("find_service")[1:], (("medicina general",), {"centro": "hospital"}))
                self.assertEqual(self.call("slots_for")[1:], ((CLINICO,), {"lang": "en"}))
                self.assertEqual(self.call("_localized")[1], (CLINICO, "en"))
                localized = {**CLINICO, "nombre": "Local Medicina general"}
                self.assertEqual(self.call("_slot_actions")[1], (BASE, localized, TURNOS, "en"))
                self.assertEqual(self.call("_center_name")[1], ("caps",))
                self.assertEqual(set(rem), {"titulo", "detalle", "acciones", "turnos", "servicio"})
                self.assertEqual(rem["titulo"], "Book a General practice appointment")
                self.assertIs(rem["turnos"], TURNOS)
                self.assertEqual(rem["acciones"], [{
                    "texto": "Martes 4 de agosto, 09:00",
                    "url": f"{BASE}/service/mg/book?slot=2026-08-04T09:00"}])
                self.assertEqual(rem["servicio"], {
                    "id": "mg", "nombre": "Medicina general", "centro": "caps", "centro_nombre": ""})

    def test_the_practitioner_is_searched_in_the_patients_center(self):
        remedies.remedy({"servicio_id": "cardio"}, NEW_REFERRAL[0], lang="es")
        self.assertEqual(self.call("find_service")[2], {"centro": None})

    def test_each_cause_has_its_own_explanation(self):
        detalle = {m: remedies.remedy(self.row, m, lang="es")["detalle"] for m in NEW_REFERRAL}
        self.assertEqual(len(set(detalle.values())), 4)
        self.assertIn("No hace falta orden para sacar ese turno", detalle[NEW_REFERRAL[0]])
        self.assertIn("la firma o la matrícula", detalle[NEW_REFERRAL[1]])
        self.assertIn("La orden no tiene fecha", detalle[NEW_REFERRAL[2]])
        self.assertIn("otra jurisdicción", detalle[NEW_REFERRAL[3]])

    def test_without_a_general_practitioner_there_is_no_remedy_and_no_schedule_lookup(self):
        self.stub("find_service", None)
        for motivo in NEW_REFERRAL:
            self.calls.clear()
            self.assertIsNone(remedies.remedy(self.row, motivo, lang="es"))
            self.assertNotIn("slots_for", self.names())

    def test_empty_agenda_still_gives_a_direct_link(self):
        self.stub("slots_for", [])
        rem = remedies.remedy(self.row, NEW_REFERRAL[0], lang="es")
        self.assertEqual(rem["turnos"], [])
        self.assertEqual(rem["acciones"], [{
            "texto": "Ver turnos de Medicina general", "url": f"{BASE}/service/mg/book"}])


class TestWrongService(_RemedyCase):
    def test_steps_and_arguments(self):
        self.stub("_center_name", "Hospital")
        self.stub("_localized", lambda s, lang: {**s, "nombre": "Local " + s["nombre"]})
        self.stub("_service_by_id", lambda sid: DESTINO if sid == "traumato" else ACTUAL)
        rem = remedies.remedy(self.row, WRONG_SERVICE, lang="en")
        self.assertEqual(self.names(), [
            "_base", "_service_by_id", "suggested_service", "_service_by_id", "slots_for",
            "_center_name", "_localized", "_slot_actions", "_center_name"])
        self.assertEqual(self.call("suggested_service")[1], (self.row,))
        self.assertEqual(self.call("_service_by_id", 1)[1], ("traumato",))
        self.assertEqual(self.call("slots_for")[1:], ((DESTINO,), {"lang": "en"}))
        self.assertEqual(self.call("_center_name", 0)[1], ("hospital",))
        self.assertEqual(self.call("_localized")[1], (DESTINO, "en"))
        localized = {**DESTINO, "nombre": "Local Traumatología"}
        self.assertEqual(self.call("_slot_actions")[1], (BASE, localized, TURNOS, "en"))
        self.assertEqual(self.call("_center_name", 1)[1], ("hospital",))
        self.assertEqual(set(rem), {"titulo", "detalle", "acciones", "turnos", "servicio"})
        self.assertEqual(rem["titulo"], "Your referral is for Local Traumatología")
        self.assertIn("Local Traumatología (Hospital)", rem["detalle"])
        self.assertIs(rem["turnos"], TURNOS)
        self.assertEqual(rem["acciones"][0]["url"], f"{BASE}/service/traumato/book?slot=2026-08-04T09:00")
        self.assertEqual(rem["servicio"], {
            "id": "traumato", "nombre": "Traumatología", "centro": "hospital", "centro_nombre": "Hospital"})

    def test_without_a_center_name_the_detail_omits_it(self):
        self.stub("_service_by_id", lambda sid: DESTINO if sid == "traumato" else ACTUAL)
        rem = remedies.remedy(self.row, WRONG_SERVICE, lang="es")
        self.assertEqual(rem["titulo"], "Tu orden es para Traumatología")
        self.assertEqual(rem["detalle"], "Sacá el turno en Traumatología y usá la misma orden: "
                                         "sirve tal cual está.")

    def test_no_suggestion_means_no_remedy(self):
        for suggestion in (None, {}):
            with self.subTest(suggestion=suggestion):
                self.calls.clear()
                self.stub("suggested_service", suggestion)
                self.assertIsNone(remedies.remedy(self.row, WRONG_SERVICE, lang="es"))
                self.assertEqual(self.names().count("_service_by_id"), 1)
                self.assertNotIn("slots_for", self.names())

    def test_suggestion_that_is_not_in_the_catalog_means_no_remedy(self):
        self.stub("_service_by_id", lambda sid: ACTUAL if sid == "cardio" else None)
        self.assertIsNone(remedies.remedy(self.row, WRONG_SERVICE, lang="es"))
        self.assertEqual(self.call("_service_by_id", 1)[1], ("traumato",))
        self.assertNotIn("slots_for", self.names())


class TestOtherCenter(_RemedyCase):
    def test_steps_and_arguments(self):
        for motivo in OTHER_CENTER:
            with self.subTest(motivo=motivo):
                self.calls.clear()
                self.stub("_center_name", "CAPS DiabetPin")
                self.stub("_localized", lambda s, lang: {**s, "nombre": "Local " + s["nombre"]})
                rem = remedies.remedy(self.row, motivo, lang="en")
                self.assertEqual(self.names(), [
                    "_base", "_service_by_id", "_mismo_servicio_otro_centro", "slots_for",
                    "_center_name", "_localized", "_slot_actions", "_center_name"])
                self.assertEqual(self.call("_mismo_servicio_otro_centro")[1], (ACTUAL, "cardio"))
                self.assertEqual(self.call("slots_for")[1:], ((OTRO,), {"lang": "en"}))
                self.assertEqual(self.call("_center_name", 0)[1], ("diabetpin",))
                self.assertEqual(self.call("_localized")[1], (OTRO, "en"))
                localized = {**OTRO, "nombre": "Local Psiquiatría"}
                self.assertEqual(self.call("_slot_actions")[1], (BASE, localized, TURNOS, "en"))
                self.assertEqual(set(rem), {"titulo", "detalle", "acciones", "turnos", "servicio"})
                self.assertEqual(rem["titulo"], "There are openings at CAPS DiabetPin")
                self.assertIn("Local Psiquiatría is also available at CAPS DiabetPin", rem["detalle"])
                self.assertIs(rem["turnos"], TURNOS)
                self.assertEqual(rem["acciones"][0]["url"],
                                 f"{BASE}/service/psiq-2/book?slot=2026-08-04T09:00")
                self.assertEqual(rem["servicio"], {
                    "id": "psiq-2", "nombre": "Psiquiatría", "centro": "diabetpin",
                    "centro_nombre": "CAPS DiabetPin"})

    def test_unnamed_center_reads_as_another_center(self):
        rem = remedies.remedy(self.row, OTHER_CENTER[0], lang="es")
        self.assertEqual(rem["titulo"], "Hay turnos en otro centro")
        self.assertIn("también se atiende en otro centro", rem["detalle"])

    def test_no_twin_service_means_no_remedy_and_no_schedule_lookup(self):
        self.stub("_mismo_servicio_otro_centro", None)
        self.assertIsNone(remedies.remedy(self.row, OTHER_CENTER[0], lang="es"))
        self.assertNotIn("slots_for", self.names())

    def test_twin_without_free_slots_means_no_remedy(self):
        self.stub("slots_for", [])
        self.assertIsNone(remedies.remedy(self.row, OTHER_CENTER[0], lang="es"))
        self.assertEqual(self.names(), ["_base", "_service_by_id", "_mismo_servicio_otro_centro",
                                        "slots_for"])


if __name__ == "__main__":
    unittest.main()
