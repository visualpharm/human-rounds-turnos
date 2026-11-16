"""Hotfix 2026-10-14 (agenda creators, video «Dermatología»): saving a service
must not ask to reset doctors' schedules unless ITS schedule changes, and a
service must only claim the doctors that really belong to it.

Synthetic catalog only; no database.
"""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from app.admin_panel import admin_content
from app.modules.turnos import schedule_policy
from app.core import db_catalog

AGENDA = {"dias": [1, 3], "desde": "08:00", "hasta": "12:00", "cada_min": 20}


def service(**extra):
    base = {"id": "derma", "nombre": "Dermatología", "cluster": "otras", "centro": "hospital",
            "modo": "solicitud", "agenda": dict(AGENDA)}
    base.update(extra)
    return base


def doctor(ident, especialidad, cluster="otras", centro="hospital", **extra):
    base = {"id": ident, "nombre": ident.title(), "especialidad": especialidad, "cluster": cluster,
            "centro": centro,
            "centros": [{"centro": centro, "franjas": [
                {"dias": [2], "desde": "14:00", "hasta": "16:00", "cada_min": 20}]}]}
    base.update(extra)
    return base


DERMATOLOGIST = doctor("vidart", "Dermatología")
UROLOGIST = doctor("moreno", "Urología")
LUNG = doctor("bianchetti", "Neumonología")


class BelongsRespectsCatchAllClusters(unittest.TestCase):
    def test_catch_all_cluster_needs_the_specialty_to_match_the_service(self):
        derma = service()
        self.assertTrue(schedule_policy.belongs(DERMATOLOGIST, derma))
        self.assertFalse(schedule_policy.belongs(UROLOGIST, derma))
        self.assertFalse(schedule_policy.belongs(LUNG, derma))

    def test_themed_cluster_shares_specialty_by_cluster_and_center(self):
        cardio = {"id": "cardio", "nombre": "Cardiología adultos", "cluster": "cardiologia", "centro": "hospital",
                  "agenda": dict(AGENDA)}
        colleague = doctor("perez", "Cardiología", cluster="cardiologia")
        self.assertTrue(schedule_policy.belongs(colleague, cardio))
        elsewhere = doctor("garcia", "Cardiología", cluster="cardiologia", centro="caps-sur")
        self.assertFalse(schedule_policy.belongs(elsewhere, cardio))

    def test_an_explicit_tag_wins_even_in_a_catch_all_cluster(self):
        tagged = doctor("moreno", "Urología", centros=[{"centro": "hospital", "franjas": [
            {"dias": [1], "desde": "08:00", "hasta": "09:00", "cada_min": 20, "servicio": "derma"}]}])
        self.assertTrue(schedule_policy.belongs(tagged, service()))

    def test_doctor_without_specialty_never_matches_a_catch_all_service(self):
        self.assertFalse(schedule_policy.belongs(doctor("sin", ""), service()))

    def test_diagnostic_and_group_clusters_are_catch_all_too(self):
        for cluster in ("diagnostico", "grupos"):
            svc = service(id="eco", nombre="Ecografía", cluster=cluster)
            self.assertTrue(schedule_policy.belongs(doctor("a", "Ecografía", cluster=cluster), svc))
            self.assertFalse(schedule_policy.belongs(doctor("b", "Cardiología", cluster=cluster), svc))


class SameScheduleIsRecognisedWhateverTheEditorAddsOrReorders(unittest.TestCase):
    """The editor re-sends a stored schedule with its own defaults filled in
    (`cupos`), single windows as `franjas`, lists in another order…"""

    def same(self, a, b):
        return schedule_policy.agenda_unchanged(a, b)

    def test_default_quotas_the_form_fills_in_are_not_a_change(self):
        stored = dict(AGENDA)
        sent = dict(AGENDA, cupos={"web": 1, "reservado": 0, "sobreturnos": 0})
        self.assertTrue(self.same(sent, stored))

    def test_a_single_window_equals_the_same_window_as_franjas(self):
        self.assertTrue(self.same({"franjas": [dict(AGENDA)], "excepciones": []}, dict(AGENDA)))

    def test_order_of_windows_days_and_closures_is_not_a_change(self):
        a = {"franjas": [dict(AGENDA, dias=[3, 1]), dict(AGENDA, desde="14:00", hasta="18:00", dias=[2])],
             "excepciones": [{"tipo": "cerrado", "fecha": "2026-11-01"}, {"tipo": "cerrado", "fecha": "2026-11-02"}]}
        b = {"franjas": [dict(AGENDA, desde="14:00", hasta="18:00", dias=[2]), dict(AGENDA, dias=[1, 3])],
             "excepciones": [{"tipo": "cerrado", "fecha": "2026-11-02"}, {"tipo": "cerrado", "fecha": "2026-11-01"}]}
        self.assertTrue(self.same(a, b))

    def test_absent_empty_and_null_are_the_same_thing(self):
        self.assertTrue(self.same({"franjas": [], "excepciones": []}, None))
        self.assertTrue(self.same(dict(AGENDA, excepciones=None, cupos={}), dict(AGENDA)))

    def test_every_real_difference_is_a_change(self):
        base = dict(AGENDA)
        for changed in (dict(base, dias=[1, 2, 3]), dict(base, desde="09:00"), dict(base, hasta="13:00"),
                        dict(base, cada_min=30), dict(base, cupos={"web": 2}),
                        dict(base, cupos={"overbooking_percent": 20}),
                        dict(base, excepciones=[{"tipo": "cerrado", "fecha": "2026-11-01"}]),
                        dict(base, servicio="other"), None):
            with self.subTest(changed=changed):
                self.assertFalse(self.same(changed, base))

    def test_the_booking_window_is_not_part_of_the_schedule(self):
        self.assertTrue(self.same(dict(AGENDA, dias_anticipacion=15, apertura_turnos="08:00"), dict(AGENDA)))

    def test_zero_overbooking_the_editor_writes_back_equals_none(self):
        # The hours editor writes `overbooking_percent: 0` back when its toggle
        # is off (collectCupos); the engine reads an absent value as 0. A window
        # saved that way is the same schedule as one that never had the key.
        stored = dict(AGENDA)
        for sent_cupos in ({"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 0},
                           {"overbooking_percent": None}, {"overbooking_percent": "0"}, {"web": "1"}):
            with self.subTest(cupos=sent_cupos):
                self.assertTrue(self.same(dict(AGENDA, cupos=sent_cupos), stored))
        for real in ({"overbooking_percent": 20}, {"web": 0}, {"sobreturnos": 2}, {"reservado": 1}):
            with self.subTest(real=real):
                self.assertFalse(self.same(dict(AGENDA, cupos=real), stored))

    def test_windows_merged_or_split_by_day_are_the_same_schedule(self):
        two = {"franjas": [dict(AGENDA, dias=[2]), dict(AGENDA, dias=[4])], "excepciones": []}
        one = {"franjas": [dict(AGENDA, dias=[2, 4])], "excepciones": []}
        self.assertTrue(self.same(two, one))
        self.assertTrue(self.same(one, two))
        self.assertTrue(self.same({"franjas": [dict(AGENDA, dias=[2]), dict(AGENDA, dias=[2, 4])]}, one))
        self.assertFalse(self.same({"franjas": [dict(AGENDA, dias=[2]), dict(AGENDA, dias=[4], hasta="13:00")]}, one))

    def test_the_real_prod_shape_of_a_two_window_service(self):
        stored = {"franjas": [{"dias": [2], "desde": "12:30", "hasta": "14:00", "cada_min": 20},
                              {"dias": [4], "desde": "12:30", "hasta": "14:00", "cada_min": 20}], "excepciones": []}
        sent = {"franjas": [{"dias": [2], "desde": "12:30", "hasta": "14:00", "cada_min": 20,
                             "cupos": {"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 0}},
                            {"dias": [4], "desde": "12:30", "hasta": "14:00", "cada_min": 20}], "excepciones": []}
        self.assertTrue(self.same(sent, stored))


class ServiceSaveOnlyAsksWhenItsScheduleChanges(unittest.TestCase):
    def _edit(self, stored, body, doctors, confirmed=False):
        body = dict(body)
        if confirmed:
            body["confirmar_reinicio_agendas"] = True
        writes = []
        with patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext(None)), \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)), \
             patch.object(admin_content.catalog, "admin_list",
                          side_effect=lambda tipo: [dict(d) for d in doctors] if tipo == "medicos" else [dict(stored)]), \
             patch.object(admin_content.catalog, "doctors", return_value=[dict(d) for d in doctors]), \
             patch.object(admin_content.catalog, "services", return_value=[dict(stored)]), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert",
                          side_effect=lambda tipo, id_, doc, **kw: writes.append((tipo, id_, doc))):
            result = admin_content.edit_item("servicios", stored["id"], body)
        return result, writes

    def test_changing_only_the_booking_window_never_asks_and_never_resets_doctors(self):
        stored = service()
        body = dict(stored, dias_anticipacion=15, apertura_turnos="08:00")
        result, writes = self._edit(stored, body, [DERMATOLOGIST, UROLOGIST])
        self.assertTrue(result["ok"])
        self.assertEqual([(t, i) for t, i, _ in writes], [("servicios", "derma")])
        self.assertEqual(writes[0][2]["dias_anticipacion"], 15)

    def test_the_form_resending_the_schedule_with_default_quotas_does_not_ask(self):
        stored = service()
        sent = dict(stored, agenda=dict(AGENDA, cupos={"web": 1, "reservado": 0, "sobreturnos": 0}),
                    dias_anticipacion=15)
        result, writes = self._edit(stored, sent, [DERMATOLOGIST])
        self.assertTrue(result["ok"])
        self.assertEqual([(t, i) for t, i, _ in writes], [("servicios", "derma")])

    def test_changing_the_specialty_group_alone_does_not_ask_either(self):
        stored = service()
        body = dict(stored, cluster="otras")
        result, writes = self._edit(stored, body, [DERMATOLOGIST])
        self.assertTrue(result["ok"])
        self.assertEqual([(t, i) for t, i, _ in writes], [("servicios", "derma")])

    def test_a_real_schedule_change_asks_and_names_only_the_doctors_of_that_service(self):
        stored = service()
        body = dict(stored, agenda=dict(AGENDA, desde="09:00"))
        with self.assertRaises(admin_content.ContentError) as caught:
            self._edit(stored, body, [DERMATOLOGIST, UROLOGIST, LUNG])
        self.assertEqual(caught.exception.status, 409)
        payload = caught.exception.payload
        self.assertEqual(payload["error"], "agenda_doctores_se_reinician")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["doctores"], ["Vidart"])

    def test_confirmed_schedule_change_resets_only_the_doctors_of_that_service(self):
        stored = service()
        body = dict(stored, agenda=dict(AGENDA, desde="09:00"))
        result, writes = self._edit(stored, body, [DERMATOLOGIST, UROLOGIST], confirmed=True)
        self.assertTrue(result["ok"])
        reset = {i for t, i, _ in writes if t == "medicos"}
        self.assertEqual(reset, {"vidart"})
        cleared = next(doc for t, i, doc in writes if i == "vidart")
        self.assertEqual(cleared["centros"][0]["franjas"], [])

    def test_first_schedule_of_a_service_asks_before_taking_over_its_doctors(self):
        stored = service()
        stored.pop("agenda")
        body = dict(stored, agenda=dict(AGENDA))
        with self.assertRaises(admin_content.ContentError) as caught:
            self._edit(stored, body, [DERMATOLOGIST, UROLOGIST])
        self.assertEqual(caught.exception.payload["doctores"], ["Vidart"])

    def test_service_without_a_schedule_never_asks(self):
        stored = service()
        stored.pop("agenda")
        result, writes = self._edit(stored, dict(stored, dias_anticipacion=7), [DERMATOLOGIST])
        self.assertTrue(result["ok"])
        self.assertEqual([(t, i) for t, i, _ in writes], [("servicios", "derma")])


if __name__ == "__main__":
    unittest.main()
