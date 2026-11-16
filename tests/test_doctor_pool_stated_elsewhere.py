"""A doctor whose windows at a center all name other services leaves the pool of
the rest of that cluster's services there — but only while someone stays.

La contraparte técnica 2026-10-23: Diego Medina (Cardiología) does only Ergometría, his one
hospital window is tagged with it, yet the cluster rule kept him in the pool of
Cardiología del Hospital, so Crear turno and «Médico» named him there. Where
EVERY pooled doctor is tagged elsewhere (Niño sano, Trabajo social…) the broad
pool stays: after MR 50 a doctor out of the pool is a 409, and the desk could
not assign anyone.

Synthetic catalog only; no database.
"""
import unittest
from unittest.mock import patch

from app.modules.turnos import schedule_policy

CARDIO = {"id": "cardiologia-hospital", "nombre": "Cardiología", "cluster": "cardiologia",
          "centro": "hospital-pinamar", "modo": "solicitud"}
ERGO = {"id": "ergometria-hospital", "nombre": "Ergometría", "cluster": "diagnostico",
        "centro": "hospital-pinamar", "modo": "solicitud_con_orden"}


def window(servicio=None, dias=(4, 5)):
    w = {"dias": list(dias), "desde": "08:00", "hasta": "11:00", "cada_min": 20}
    if servicio:
        w["servicio"] = servicio
    return w


def doctor(ident, franjas, centro="hospital-pinamar", **extra):
    return dict({"id": ident, "nombre": ident.title(), "especialidad": "Cardiología", "cluster": "cardiologia",
                 "centro": centro, "centros": [{"centro": centro, "franjas": franjas, "excepciones": []}]}, **extra)


MEDINA = doctor("medina", [window("ergometria-hospital")])
SANCHEZ = doctor("sanchez", [window("cardiologia-hospital", (4,))])
SARALEGUI = doctor("saralegui", [window("ecocardiograma-hospital", (5,))])
UNTAGGED = doctor("untagged", [window()])
NO_WINDOWS = doctor("nowindows", [])


class StatedElsewhereLeavesThePool(unittest.TestCase):
    def test_tagged_elsewhere_leaves_when_a_colleague_stays(self):
        peers = [MEDINA, SANCHEZ, SARALEGUI]
        self.assertFalse(schedule_policy.belongs(MEDINA, CARDIO, peers))
        self.assertFalse(schedule_policy.belongs(SARALEGUI, CARDIO, peers))
        self.assertTrue(schedule_policy.belongs(SANCHEZ, CARDIO, peers))

    def test_the_tagged_service_keeps_him(self):
        self.assertTrue(schedule_policy.belongs(MEDINA, ERGO, [MEDINA, SANCHEZ]))

    def test_an_untagged_window_or_no_window_keeps_the_cluster_rule(self):
        peers = [MEDINA, UNTAGGED, NO_WINDOWS]
        self.assertTrue(schedule_policy.belongs(UNTAGGED, CARDIO, peers))
        self.assertTrue(schedule_policy.belongs(NO_WINDOWS, CARDIO, peers))
        self.assertFalse(schedule_policy.belongs(MEDINA, CARDIO, peers))

    def test_a_pool_that_would_empty_keeps_everyone(self):
        peers = [MEDINA, SARALEGUI]
        self.assertTrue(schedule_policy.belongs(MEDINA, CARDIO, peers))
        self.assertTrue(schedule_policy.belongs(SARALEGUI, CARDIO, peers))

    def test_role_rows_and_disabled_doctors_do_not_count_as_someone(self):
        peers = [MEDINA, dict(SANCHEZ, rol=True), dict(UNTAGGED, _borrado=True)]
        self.assertTrue(schedule_policy.belongs(MEDINA, CARDIO, peers))

    def test_a_demo_doctor_or_a_row_without_id_is_not_someone_the_desk_can_assign(self):
        peers = [MEDINA, dict(SANCHEZ, demo=True), {k: v for k, v in UNTAGGED.items() if k != "id"}]
        self.assertTrue(schedule_policy.belongs(MEDINA, CARDIO, peers))

    def test_a_single_flat_window_is_offered_everywhere_so_its_tag_does_not_count(self):
        # site_availability._service_schedule never trims a single window by its tag
        flat = {"id": "flat", "nombre": "Flat", "especialidad": "Cardiología", "cluster": "cardiologia",
                "centro": "hospital-pinamar", "agenda": dict(window("ergometria-hospital"))}
        self.assertTrue(schedule_policy.belongs(flat, CARDIO, [flat, SANCHEZ]))
        listed = dict(flat, agenda={"franjas": [window("ergometria-hospital")], "excepciones": []})
        self.assertFalse(schedule_policy.belongs(listed, CARDIO, [listed, SANCHEZ]))

    def test_windows_at_another_center_do_not_count(self):
        ostende = doctor("o", [window("cardiologia-ostende")], centro="caps-ostende")
        ostende["centros"].append({"centro": "hospital-pinamar", "franjas": [window()], "excepciones": []})
        self.assertTrue(schedule_policy.belongs(ostende, CARDIO, [ostende, SANCHEZ]))

    def test_without_peers_the_catalog_decides(self):
        with patch.object(schedule_policy, "_catalog_peers", return_value=([MEDINA, SANCHEZ], {})):
            self.assertFalse(schedule_policy.belongs(MEDINA, CARDIO))
        with patch.object(schedule_policy, "_catalog_peers", return_value=([MEDINA], {})):
            self.assertTrue(schedule_policy.belongs(MEDINA, CARDIO))

    def test_a_service_with_its_own_schedule_keeps_its_whole_pool(self):
        # its takeover leaves its doctors exactly the windows tagged elsewhere
        scheduled = dict(CARDIO, agenda={"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 20})
        self.assertTrue(schedule_policy.belongs(SARALEGUI, scheduled, [SARALEGUI, SANCHEZ]))

    def test_no_catalog_keeps_the_broad_pool(self):
        with patch.object(schedule_policy, "_catalog_peers", side_effect=RuntimeError("down")):
            self.assertTrue(schedule_policy.belongs(MEDINA, CARDIO))


if __name__ == "__main__":
    unittest.main()
