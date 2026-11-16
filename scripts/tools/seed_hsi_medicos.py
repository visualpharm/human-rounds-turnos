#!/usr/bin/env python3
"""
seed_hsi_medicos.py — mirror the REAL bookable Sana professionals into demo HSI.

scripts/tools/seed_hsi_demo.py gives the demo HSI one generic professional per
specialty; that unblocks specialty-only pushes but every approval that names a
médico (the admin queue suggests one by default) still conflicted with
"no HSI diary matches professional" — the real Pinamar names simply did not
exist there. This script closes that gap: every medico with a real `agenda` in
webdata/medicos.json gets a person + professional + user + role + ONE diary
whose weekly windows mirror the Sana franjas 1:1 (same days, same start/end,
same cada_min), so the exact slots the wizard offers exist and align in HSI.

Idempotent like the demo seed: license MP-SANA-<medico_id> is the key, the
diary is skip-if-exists. Honors TURNOS_WEBDATA_DIR via sitedata.py.

Run (after scripts/tools/seed_hsi_demo.py):
    cd <workspace>/sana-turnos
    python3 scripts/tools/seed_hsi_medicos.py
"""

import os
import sys
import zlib
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import sitedata  # noqa: E402
from app.connectors.hsi.hsi_client import HsiClient, HsiError  # noqa: E402
from seed_hsi_demo import (  # noqa: E402
    INSTITUTION_ID, OFFICE_SQL, PRO_SQL, PROF_MEDICO, _psql,
)

# Sana `especialidad` -> HSI clinical_specialty id (v2.71.2 catalog; type-2
# "Especialidad" preferred where duplicated — same criterion as seed_hsi_demo).
ESPECIALIDAD_HSI = {
    "Medicina general": 91,
    "Demanda espontánea": 91,
    "Clínica médica": 128,
    "Cardiología": 131,
    "Kinesiología": 39,
    "Ginecología": 157,
    "Cirugía": 58,
    "Traumatología": 161,          # Ortopedia y traumatología
    "Pediatría": 165,
    "Obstetricia": 159,
    "Odontología": 64,
    "Nutrición": 145,
    "Terapia ocupacional": 34,
    "Diabetología": 296,
    "Neurología": 144,
    "Neurología infantil": 174,
    "Psicopedagogía": 53,
    "Otorrinolaringología": 162,
    "Gastroenterología": 137,
    "Endocrinología": 134,
    "Oftalmología": 160,
    "Urología": 164,
    "Neumología": 143,             # HSI: Neumonología
    "Dermatología": 133,
    "Lic. Psicomotricidad": 327,   # psicomotricista
    "Psiquiatría": 178,
    "Infectología": 141,
    "Infectología infantil": 170,
    "Fonoaudiología": 79,
}


def _bookables():
    import json
    with open(sitedata.path("medicos.json"), encoding="utf-8") as f:
        data = json.load(f)
    meds = data.get("medicos") if isinstance(data, dict) else data
    from app.modules.turnos import schedule_policy
    return [m for m in meds if schedule_policy.assignable(m) and m.get("agenda")]


def _dni_de(medico_id):
    # Deterministic per catalog id so re-runs never mint a second person.
    return "91%06d" % (zlib.crc32(medico_id.encode("utf-8")) % 1000000)


def _sql_txt(value):
    return str(value).replace("'", "''")


def _windows_de(agenda):
    """Sana franjas -> HSI (dayWeekId, from, to) windows. JS days 0=Sun..6=Sat
    map to HSI 1=Mon..7=Sun. Same-day overlapping/touching franjas get merged:
    HSI rejects "horarios de atención superpuestos" within one diary."""
    por_dia, cada = {}, 20
    for franja in agenda.get("franjas") or []:
        cada = int(franja.get("cada_min") or cada)
        for js_day in franja.get("dias") or []:
            hsi_day = (int(js_day) + 6) % 7 + 1
            por_dia.setdefault(hsi_day, []).append(
                (str(franja.get("desde")), str(franja.get("hasta"))))
    windows = []
    for hsi_day, tramos in sorted(por_dia.items()):
        tramos.sort()
        merged = [list(tramos[0])]
        for desde, hasta in tramos[1:]:
            if desde <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hasta)
            else:
                merged.append([desde, hasta])
        windows.extend((hsi_day, d + ":00", h + ":00") for d, h in merged)
    return windows, cada


def seed_db(medicos):
    parts = ["\\set ON_ERROR_STOP on", "BEGIN;"]
    for m in medicos:
        tokens = str(m["nombre"]).split()
        first = _sql_txt(tokens[0].upper())
        last = _sql_txt(" ".join(tokens[1:]).upper() or tokens[0].upper())
        parts.append(OFFICE_SQL.format(inst=INSTITUTION_ID,
                                       office=_sql_txt(m["_office"])))
        parts.append(PRO_SQL.format(
            inst=INSTITUTION_ID, lic=m["_lic"], first=first, last=last,
            dni=_dni_de(m["id"]), esp=m["_esp"], prof=PROF_MEDICO,
            user="%s@sana.local" % m["id"], role=3,
            office=_sql_txt(m["_office"]), alias="", overturn=0,
            desde="", hasta=""))
    parts.append("COMMIT;")
    _psql("\n".join(parts))
    values = ",\n    ".join("('%s','%s')" % (m["_lic"], _sql_txt(m["_office"]))
                            for m in medicos)
    rows = _psql(r"""
SELECT hp.id, hp.license_number,
       (SELECT id FROM doctors_office WHERE institution_id={inst} AND description=off.descr) AS office_id
FROM (VALUES
    {values}
) AS off(lic, descr)
JOIN healthcare_professional hp ON hp.license_number = off.lic
ORDER BY hp.id;
""".format(inst=INSTITUTION_ID, values=values), tuples=True)
    ids = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        hp_id, lic, office_id = line.split("|")
        ids[lic] = {"hp_id": int(hp_id), "office_id": int(office_id)}
    return ids


def seed_diaries(medicos, ids):
    client = HsiClient()
    client.login()
    today = date.today()
    end = today + timedelta(weeks=13)
    created = skipped = 0
    for m in medicos:
        info = ids[m["_lic"]]
        if client._diaries_for_professional(info["hp_id"]):
            skipped += 1
            continue
        windows, cada = _windows_de(m["agenda"])
        if not windows:
            print("   [sin franjas] %s" % m["nombre"])
            continue
        diary_id = client.create_diary(
            healthcare_professional_id=info["hp_id"],
            doctors_office_id=info["office_id"],
            clinical_specialty_id=m["_esp"],
            start_date=today.isoformat(),
            end_date=end.isoformat(),
            appointment_duration=cada,
            alias="Sana %s" % m["nombre"],
            windows=windows,
        )
        created += 1
        print("   [created] diary #%s %s (%s, %d ventanas, cada %d')"
              % (diary_id, m["nombre"], m["especialidad"], len(windows), cada))
    print("== %d diaries created, %d already existed ==" % (created, skipped))


def main():
    medicos = _bookables()
    plan, sin_mapa = [], []
    for m in medicos:
        esp_id = ESPECIALIDAD_HSI.get(m.get("especialidad"))
        if not esp_id:
            sin_mapa.append("%s (%s)" % (m["nombre"], m.get("especialidad")))
            continue
        m["_esp"] = esp_id
        m["_lic"] = "MP-SANA-%s" % m["id"]
        m["_office"] = "Consultorio %s DEMO" % m["nombre"]
        plan.append(m)
    print("== mirroring %d real professionals into HSI (%d sin mapa de especialidad) =="
          % (len(plan), len(sin_mapa)))
    for linea in sin_mapa:
        print("   [sin mapa] %s" % linea)
    ids = seed_db(plan)
    try:
        seed_diaries(plan, ids)
    except HsiError as e:
        raise SystemExit("HSI REST seed failed: %s" % e)
    print("Done.")


if __name__ == "__main__":
    main()
