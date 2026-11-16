"""test_doctor_pool_golden.py — la regla «médicos del servicio» no cambia al unificarla (2026-10-25).

El pool de médicos de un servicio vivía repetido: el servidor le ofrece horarios al paciente
(`patient_web/site_*.py`), resuelve de quién es un turno (`booking_requests._resolve_slot_doctor`), sugiere y
asigna médico en la cola (`_suggested_doctor`, `_doctor_pool_by_service`) y arma la grilla de la
mesa (`admin_appointments._desk_slots_by_doctor`); cada sitio reescribía a mano el corte previo a
`schedule_policy.belongs` («no demo», «no fila-rol», «no deshabilitado», «con id»).

Este archivo congela, para TODOS los servicios del paquete de datos de ejemplo (`example-town`, el que
usa el demo) más una fila demo y una fila-rol en cada cluster+centro, lo que esos sitios devuelven HOY.
`tests/golden/doctor_pool.json` se grabó con el código previo a la unificación y la unificación lo dejó
sin tocar: si este test pasa sin regrabar el archivo, la regla no se movió.

  - Dos cortes, a propósito: la mesa asigna a PERSONAS (sin filas demo ni filas-rol); lo que se ofrece al
    paciente lleva también las filas-rol (enfermería, técnico: sus ventanas siguen alimentando los
    horarios), pero nunca las demo. Ver `schedule_policy.assignable` / `schedule_policy.offering`.
  - Si cambian los datos de `example-town` el archivo deja de corresponder a esos datos y el test se
    SALTEA (no falla): regrabarlo con `python3 tests/test_doctor_pool_golden.py --write` sólo tiene
    sentido sobre un código cuya regla ya se sabe correcta. La parte de «especificación» de abajo no
    depende de los datos y sigue corriendo siempre.

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_doctor_pool_golden.py
"""
import hashlib
import json
import os
import re
import sys
import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

from app.patient_web import catalog, site_availability, site_base, site_catalog, site_page_service
from app.modules.turnos import booking_requests, schedule_policy, slot_holds  # noqa: E402
from app.modules.turnos import booking_queue  # noqa: E402
from app.admin_panel import admin_appointments  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "doctor_pool.json")
NOW = datetime(2026, 10, 13, 8)   # a Tuesday morning; every window below is Monday–Friday
_FRANJAS = [{"dias": [1, 2, 3, 4, 5], "desde": "14:00", "hasta": "16:00", "cada_min": 20}]


def _extra_rows(services):
    """One demo row and one role-row per cluster+center, windows included: the two kinds
    the sites cut differently."""
    rows, seen = [], set()
    for s in services:
        key = (s.get("cluster"), s.get("centro"))
        if key in seen or not all(key):
            continue
        seen.add(key)
        base = {"especialidad": "Medicina general", "cluster": key[0], "centro": key[1],
                "centros": [{"centro": key[1], "franjas": deepcopy(_FRANJAS), "excepciones": []}],
                "agenda": {"franjas": deepcopy(_FRANJAS), "excepciones": []}}
        rows.append(dict(base, id="demo-%s-%s" % key, nombre="Demo %s %s" % key, demo=True))
        rows.append(dict(base, id="rol-%s-%s" % key, nombre="Rol %s %s" % key, rol=True))
    return rows


def _world():
    """(services, doctors) — the example pack as the patient site reads it, plus the extra rows."""
    services = [dict(s) for s in site_catalog._services() if isinstance(s, dict)]
    doctors = [dict(m) for m in site_catalog._doctors()] + _extra_rows(services)
    return services, doctors


def _patches(doctors):
    return (patch.object(site_catalog, "_doctors", side_effect=lambda localize=True: deepcopy(doctors)),
            patch.object(catalog, "doctors", side_effect=lambda *a, **k: deepcopy(doctors)),
            patch.object(site_base, "_now_art", return_value=NOW),
            patch.object(site_availability, "_slot_source_info", return_value={}),
            patch.object(site_availability, "_place_of", return_value={}),
            patch.object(slot_holds, "taken", return_value=set()),
            patch.object(schedule_policy, "_catalog_peers", side_effect=lambda: (deepcopy(doctors), {})))


def _who_sees(sid):
    """The doctors the service page lists under «Quién atiende», in order."""
    page = site_page_service._page_service(sid)
    html = page.decode("utf-8") if isinstance(page, bytes) else str(page or "")
    start = html.find('id="service-doctors"')
    if start < 0:
        return []
    block = html[start:html.find("</ul>", start)]
    return re.findall(r'href="/doctor/([^"]+)"', block)


def _record():
    services, doctors = _world()
    patches = _patches(doctors)
    for p in patches:
        p.start()
    try:
        out = {"belongs": {}, "pool_by_service": {}, "suggested": {}, "resolved": {}, "offers": {},
               "next_best": {}, "next_opening": {}, "desk_grid": {}, "service_page_doctors": {}}
        with patch.object(booking_requests, "_services", side_effect=lambda: {s["id"]: s for s in services}), \
                patch.object(booking_requests, "_service_full",
                             side_effect=lambda sid: next((s for s in services if s["id"] == sid), None)):
            out["pool_by_service"] = booking_queue._doctor_pool_by_service()
            for s in services:
                sid = s["id"]
                out["belongs"][sid] = [m["id"] for m in doctors if schedule_policy.belongs(m, s)]
                offers = site_availability._appointments_for_service(s)
                out["offers"][sid] = offers
                out["next_best"][sid] = site_availability._next_appointment_best(s, ocupados=set())
                out["next_opening"][sid] = site_availability._next_opening(s)
                out["desk_grid"][sid] = admin_appointments._desk_slots_by_doctor(s)
                out["suggested"][sid] = booking_queue._suggested_doctor(
                    {"servicio_id": sid, "preferencia_horario": ""}, {})
                out["service_page_doctors"][sid] = _who_sees(sid)
                firsts = [(t.get("dia"), t.get("hora")) for b in (offers or {}).get("medicos") or []
                          for t in b.get("turnos") or []][:3]
                out["resolved"][sid] = [[d, h, booking_requests._resolve_slot_doctor(sid, d, h)]
                                        for d, h in firsts]
    finally:
        for p in reversed(patches):
            p.stop()
    out = json.loads(json.dumps(out, sort_keys=True, ensure_ascii=False, default=str))
    digest = hashlib.sha256(json.dumps([services, doctors], sort_keys=True, ensure_ascii=False,
                                       default=str).encode()).hexdigest()
    return {"inputs_sha256": digest, "services": len(services), "doctors": len(doctors), "outputs": out}


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class DoctorPoolGolden(unittest.TestCase):
    def test_every_site_returns_what_it_returned_before_the_unification(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            want = json.load(fh)
        got = _record()
        if got["inputs_sha256"] != want["inputs_sha256"]:
            self.skipTest("the example-town data changed since the golden was recorded")
        self.assertGreater(want["services"], 50)
        for site, per_service in want["outputs"].items():
            for sid, expected in per_service.items():
                self.assertEqual(got["outputs"][site][sid], expected, "%s / %s" % (site, sid))
        self.assertEqual(got["outputs"]["pool_by_service"], want["outputs"]["pool_by_service"])


class DoctorPoolSpecification(unittest.TestCase):
    """The rule itself, independent of the example data."""

    SERVICE = {"id": "svc", "nombre": "Consultorio", "modo": "solicitud", "cluster": "clin", "centro": "hosp"}

    @staticmethod
    def _doc(did, desde, centro="hosp", **extra):
        franjas = [{"dias": [1, 2, 3, 4, 5], "desde": desde, "hasta": "%02d:00" % (int(desde[:2]) + 1),
                    "cada_min": 20}]
        return dict({"id": did, "nombre": "Nombre " + did, "especialidad": "Medicina general", "cluster": "clin",
                     "centro": centro, "centros": [{"centro": centro, "franjas": franjas, "excepciones": []}],
                     "agenda": {"franjas": franjas, "excepciones": []}}, **extra)

    def _run(self, doctors):
        service = dict(self.SERVICE)
        patches = _patches(doctors)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(booking_requests, "_services", side_effect=lambda: {"svc": service}),
                  patch.object(booking_requests, "_service_full", side_effect=lambda sid: service)):
            p.start()
            self.addCleanup(p.stop)
        offers = site_availability._appointments_for_service(service) or {}
        hours = sorted({t["hora"][:2] for b in offers.get("medicos") or [] for t in b.get("turnos") or []})
        return {
            "pool": booking_queue._doctor_pool_by_service()["svc"],
            "suggested": (booking_queue._suggested_doctor({"servicio_id": "svc", "preferencia_horario": ""}, {})
                          or {}).get("nombre"),
            "offered_hours": hours,
            "desk": sorted(b["medico_id"] for b in
                           admin_appointments._desk_slots_by_doctor(service).get("medicos") or []),
        }

    def test_two_cuts_people_for_the_desk_role_rows_too_for_the_offers(self):
        got = self._run([self._doc("ana", "08:00"), self._doc("dem", "10:00", demo=True),
                         self._doc("enf", "12:00", rol=True)])
        # the desk assigns PEOPLE: no demo row, no role-row
        self.assertEqual(got["pool"], ["ana"])
        self.assertEqual(got["suggested"], "Nombre ana")
        # what is OFFERED never includes a demo row's windows, but a role-row's still feed it
        self.assertEqual(got["offered_hours"], ["08", "12"])
        # the desk's grid names every doctor that feeds the offers, demo rows excepted
        self.assertEqual(got["desk"], ["ana", "enf"])

    def test_the_suggestion_and_the_offers_never_needed_an_id_but_the_desk_list_does(self):
        row = self._doc("ana", "08:00")
        row.pop("id")
        got = self._run([row])
        self.assertEqual(got["suggested"], "Nombre ana")
        self.assertEqual(got["offered_hours"], ["08"])
        self.assertEqual(got["pool"], [])
        self.assertEqual(got["desk"], [])    # the desk's grid links the doctor by id

    def test_a_doctor_of_another_center_or_cluster_is_not_in_the_pool(self):
        got = self._run([self._doc("ana", "08:00"), self._doc("otro-centro", "09:00", centro="otro"),
                         self._doc("otro-cluster", "10:00", cluster="otra")])
        self.assertEqual(got["pool"], ["ana"])
        self.assertEqual(got["offered_hours"], ["08"])


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
