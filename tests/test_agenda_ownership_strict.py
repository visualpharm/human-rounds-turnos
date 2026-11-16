"""Hotfix B 2026-10-14: what LOCKS a doctor's schedule and what a confirmed save
RESETS is the strict relation «this doctor is this service» (a franja tagged
with it, or the doctor's specialty IS the service at one of the doctor's
centers) — never «shares a cluster». The cluster relation (`belongs`) keeps
feeding offers and automatic assignment; it must not wipe colleagues.

Synthetic catalog only; no database.
"""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from app.admin_panel import admin_content
from app.modules.turnos import schedule_policy
from app.core import db_catalog

AGENDA = {"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 20}


def service(ident="psicomotricidad", nombre="Psicomotricidad", cluster="salud-mental", centro="caps-ostende", **extra):
    return dict({"id": ident, "nombre": nombre, "cluster": cluster, "centro": centro, "modo": "solicitud"}, **extra)


def doctor(ident, especialidad, cluster="salud-mental", centro="caps-ostende", franjas=None, **extra):
    franjas = franjas if franjas is not None else [{"dias": [2], "desde": "14:00", "hasta": "16:00", "cada_min": 20}]
    return dict({"id": ident, "nombre": ident.title(), "especialidad": especialidad, "cluster": cluster,
                 "centro": centro, "centros": [{"centro": centro, "franjas": franjas, "excepciones": []}]}, **extra)


PSICOMOTRICIST = doctor("lucia", "Psicomotricidad")
PSICOPEDAGOGUE = doctor("marta", "Psicopedagogía")
OCCUPATIONAL = doctor("rosa", "Terapia ocupacional")


class ServesIsStrict(unittest.TestCase):
    def test_specialty_that_is_the_service_serves_it(self):
        self.assertTrue(schedule_policy.serves(PSICOMOTRICIST, service()))

    def test_cluster_mates_of_other_specialties_do_not(self):
        for mate in (PSICOPEDAGOGUE, OCCUPATIONAL):
            self.assertFalse(schedule_policy.serves(mate, service()))
            # …yet the broad relation (offers, automatic assignment) keeps them.
            self.assertTrue(schedule_policy.belongs(mate, service()))

    def test_an_explicit_tag_serves_whatever_the_specialty(self):
        tagged = doctor("rosa", "Terapia ocupacional", franjas=[
            {"dias": [3], "desde": "08:00", "hasta": "09:00", "cada_min": 20, "servicio": "psicomotricidad"}])
        self.assertTrue(schedule_policy.serves(tagged, service()))

    def test_the_center_must_be_one_of_the_doctors(self):
        elsewhere = doctor("lucia", "Psicomotricidad", centro="hospital-pinamar")
        self.assertFalse(schedule_policy.serves(elsewhere, service()))

    def test_no_specialty_never_serves_without_a_tag(self):
        self.assertFalse(schedule_policy.serves(doctor("x", ""), service()))

    def test_runtime_and_lock_stay_on_the_pool_the_booking_engine_uses(self):
        # `managed_services` feeds the doctor's calendar, capacity and read-only
        # form: it must agree with `doctor_agendas` (booking), i.e. the pool.
        scheduled = service(agenda=dict(AGENDA))
        for member in (PSICOMOTRICIST, PSICOPEDAGOGUE):
            self.assertEqual([s["id"] for s in schedule_policy.managed_services(member, [scheduled])],
                             ["psicomotricidad"])
            self.assertEqual(schedule_policy.doctor_agendas(member, scheduled), [scheduled["agenda"]])
        other_center = doctor("x", "Psicomotricidad", centro="hospital-pinamar")
        self.assertEqual(schedule_policy.managed_services(other_center, [scheduled]), [])


class ResetIsScopedToTheServiceCenter(unittest.TestCase):
    def test_other_centers_keep_their_schedule(self):
        multi = doctor("lucia", "Psicomotricidad")
        multi["centros"].append({"centro": "caps-valeria", "franjas": [
            {"dias": [4], "desde": "10:00", "hasta": "12:00", "cada_min": 20}], "excepciones": [
            {"tipo": "cerrado", "fecha": "2026-11-02"}]})
        reset = schedule_policy.reset_doctor(multi, service())
        by_center = {c["centro"]: c for c in reset["centros"]}
        self.assertEqual(by_center["caps-ostende"]["franjas"], [])
        self.assertEqual(len(by_center["caps-valeria"]["franjas"]), 1)
        self.assertEqual(len(by_center["caps-valeria"]["excepciones"]), 1)

    def test_windows_tagged_for_another_service_of_the_same_center_stay(self):
        mixed = doctor("lucia", "Psicomotricidad", franjas=[
            {"dias": [1], "desde": "08:00", "hasta": "09:00", "cada_min": 20},
            {"dias": [3], "desde": "08:00", "hasta": "09:00", "cada_min": 20, "servicio": "psicopedagogia"},
            {"dias": [5], "desde": "08:00", "hasta": "09:00", "cada_min": 20, "servicio": "psicomotricidad"}])
        reset = schedule_policy.reset_doctor(mixed, service())
        kept = reset["centros"][0]["franjas"]
        self.assertEqual([f.get("servicio") for f in kept], ["psicopedagogia"])

    def test_closures_go_only_when_no_window_of_that_center_remains(self):
        with_closure = doctor("lucia", "Psicomotricidad")
        with_closure["centros"][0]["excepciones"] = [{"tipo": "cerrado", "fecha": "2026-11-02"}]
        self.assertEqual(schedule_policy.reset_doctor(with_closure, service())["centros"][0]["excepciones"], [])
        keeps_a_window = doctor("lucia", "Psicomotricidad", franjas=[
            {"dias": [3], "desde": "08:00", "hasta": "09:00", "cada_min": 20, "servicio": "psicopedagogia"}])
        keeps_a_window["centros"][0]["excepciones"] = [{"tipo": "cerrado", "fecha": "2026-11-02"}]
        self.assertEqual(len(schedule_policy.reset_doctor(keeps_a_window, service())["centros"][0]["excepciones"]), 1)

    def test_flat_agenda_goes_only_with_its_own_center(self):
        flat_here = dict(doctor("lucia", "Psicomotricidad"), agenda=dict(AGENDA))
        self.assertNotIn("agenda", schedule_policy.reset_doctor(flat_here, service()))
        flat_elsewhere = dict(doctor("lucia", "Psicomotricidad", centro="hospital-pinamar"), agenda=dict(AGENDA))
        flat_elsewhere["centros"].append({"centro": "caps-ostende", "franjas": [dict(AGENDA)], "excepciones": []})
        self.assertIn("agenda", schedule_policy.reset_doctor(flat_elsewhere, service()))

    def test_flat_agenda_windows_tagged_for_another_service_stay(self):
        flat = dict(doctor("lucia", "Psicomotricidad"), agenda={"franjas": [
            dict(AGENDA), dict(AGENDA, dias=[3], servicio="psicopedagogia")], "excepciones": []})
        reset = schedule_policy.reset_doctor(flat, service())
        self.assertEqual([f.get("servicio") for f in reset["agenda"]["franjas"]], ["psicopedagogia"])

    def test_without_a_service_everything_is_reset_like_before(self):
        reset = schedule_policy.reset_doctor(dict(PSICOMOTRICIST, agenda=dict(AGENDA)))
        self.assertNotIn("agenda", reset)
        self.assertEqual(reset["centros"][0]["franjas"], [])

    def test_the_input_catalog_is_never_mutated(self):
        original = doctor("lucia", "Psicomotricidad")
        schedule_policy.reset_doctor(original, service())
        self.assertTrue(original["centros"][0]["franjas"])


class DialogAndResetOnlyTouchTheServiceOwnDoctors(unittest.TestCase):
    def _save(self, stored, body, doctors, confirmed=False):
        body = dict(body, **({"confirmar_reinicio_agendas": True} if confirmed else {}))
        writes = []
        with patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext(None)), \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)), \
             patch.object(admin_content.catalog, "admin_list",
                          side_effect=lambda tipo: [dict(d) for d in doctors] if tipo == "medicos" else [dict(stored)]), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert",
                          side_effect=lambda tipo, id_, doc, **kw: writes.append((tipo, id_, doc))):
            result = admin_content.edit_item("servicios", stored["id"], body)
        return result, writes

    def test_first_schedule_lists_only_the_doctors_of_that_service(self):
        stored = service()
        with self.assertRaises(admin_content.ContentError) as caught:
            self._save(stored, dict(stored, agenda=dict(AGENDA)), [PSICOMOTRICIST, PSICOPEDAGOGUE, OCCUPATIONAL])
        self.assertEqual(caught.exception.payload["doctores"], ["Lucia"])
        self.assertEqual(caught.exception.payload["count"], 1)

    def test_a_service_with_no_doctor_of_its_own_saves_without_asking(self):
        stored = service(ident="patologia-mamaria", nombre="Patología mamaria", cluster="gineco")
        gynecologist = doctor("julia", "Ginecología", cluster="gineco", centro="caps-ostende")
        result, writes = self._save(stored, dict(stored, agenda=dict(AGENDA)), [gynecologist])
        self.assertTrue(result["ok"])
        self.assertEqual([(t, i) for t, i, _ in writes], [("servicios", "patologia-mamaria")])

    def test_confirmed_reset_wipes_only_the_service_center_of_its_own_doctors(self):
        stored = service()
        busy = doctor("lucia", "Psicomotricidad")
        busy["centros"].append({"centro": "caps-valeria", "franjas": [
            {"dias": [4], "desde": "10:00", "hasta": "12:00", "cada_min": 20}], "excepciones": []})
        result, writes = self._save(stored, dict(stored, agenda=dict(AGENDA)), [busy, PSICOPEDAGOGUE], confirmed=True)
        self.assertTrue(result["ok"])
        touched = {i for t, i, _ in writes if t == "medicos"}
        self.assertEqual(touched, {"lucia"})
        saved = next(doc for t, i, doc in writes if i == "lucia")
        by_center = {c["centro"]: c for c in saved["centros"]}
        self.assertEqual(by_center["caps-ostende"]["franjas"], [])
        self.assertEqual(len(by_center["caps-valeria"]["franjas"]), 1)

    def test_pool_neighbours_keep_their_own_windows_after_a_confirmed_reset(self):
        stored = service()
        result, writes = self._save(stored, dict(stored, agenda=dict(AGENDA)),
                                    [PSICOMOTRICIST, PSICOPEDAGOGUE, OCCUPATIONAL], confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual({i for t, i, _ in writes if t == "medicos"}, {"lucia"})
        # …and they are still in the pool the schedule applies to at runtime:
        scheduled = dict(stored, agenda=dict(AGENDA))
        for neighbour in (PSICOPEDAGOGUE, OCCUPATIONAL):
            self.assertEqual([s["id"] for s in schedule_policy.managed_services(neighbour, [scheduled])],
                             ["psicomotricidad"])

    def test_a_doctor_with_nothing_at_the_service_center_is_not_listed(self):
        stored = service()
        only_elsewhere = doctor("lucia", "Psicomotricidad", centro="caps-ostende", franjas=[])
        only_elsewhere["centros"].append({"centro": "caps-valeria", "franjas": [
            {"dias": [4], "desde": "10:00", "hasta": "12:00", "cada_min": 20}], "excepciones": []})
        result, writes = self._save(stored, dict(stored, agenda=dict(AGENDA)), [only_elsewhere])
        self.assertTrue(result["ok"])
        self.assertEqual([(t, i) for t, i, _ in writes], [("servicios", "psicomotricidad")])


if __name__ == "__main__":
    unittest.main()
