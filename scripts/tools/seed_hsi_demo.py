#!/usr/bin/env python3
"""
seed_hsi_demo.py — idempotent seeding of the HSI demo prerequisites needed to
book turnos, for the Pinamar Turnos <-> HSI integration.

What it creates (all keyed/guarded so re-running is safe), per ROSTER entry:
  DB layer (via `docker exec <container> psql`, no pip deps):
    * 1 sector "Consultorios Externos DEMO" (Ambulatorio)
    * consultorios (doctors_office): one per professional — HSI validates
      diary overlap per office, so a shared office 400s on the second diary
    * 1 healthcare professional per specialty, each linked to a person + a
      profession + its clinical specialty, AND to a user account with a
      clinical role at the institution — HSI only lists a professional as
      bookable when person->user_person->user_role ties them to the institution
      (discovered the hard way; see docs/HSI-API-NOTES.md).
  REST layer (via hsi_client, exactly as the HSI UI does it):
    * 1 diary per professional for the next ~13 weeks. The two legacy entries
      keep weekday mornings (Mon-Fri 08:00-12:00); the wide roster runs
      Mon-Fri 08:00-17:00 so afternoon approvals also integrate.

The ROSTER covers the specialties the Sana catalog actually pushes
(solicitud/solicitud_con_orden services): before 2026-07-21 only Cardiología
and Kinesiología existed, so almost every approval conflicted with
"no diary matches" (see docs/solutions/hsi/retry-boton-outbox-reset.md).
Practice→specialty naming gaps (Ecografía→Diagnóstico por imágenes,
Laboratorio→Bioquímica clínica, Neumología→Neumonología…) are translated in
hsi_client._match_diary, not here.

Run:
    cd <workspace>/sana-turnos
    python3 scripts/tools/seed_hsi_demo.py

Env (defaults shown): HSI_URL=http://localhost:8280  HSI_EMAIL=admin@example.com
    HSI_PASS=SanaAdmin2026  HSI_INSTITUTION_ID=1
    HSI_DB_CONTAINER=postgresql  HSI_DB_NAME=hsi-db  HSI_DB_USER=postgres
"""

import os
import sys
import subprocess
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.connectors.hsi.hsi_client import HsiClient, HsiError  # noqa: E402

DB_CONTAINER = os.environ.get("HSI_DB_CONTAINER", "postgresql")
DB_NAME = os.environ.get("HSI_DB_NAME", "hsi-db")
DB_USER = os.environ.get("HSI_DB_USER", "postgres")
INSTITUTION_ID = int(os.environ.get("HSI_INSTITUTION_ID", "1"))

CARDIO_OVERTURN_COUNT = 2
# professional_specialty (profession) ids:
PROF_MEDICO = 83
PROF_KINESIOLOGO = 67

# One demo professional + diary per specialty the Sana catalog pushes.
# clinical_specialty ids verified against the live HSI v2.71.2 catalog
# (SELECT id,name,clinical_specialty_type_id FROM clinical_specialty) —
# where a name exists as both type 1 "Servicio" and type 2 "Especialidad",
# the type-2 id is used (same criterion as the original Cardiología=131).
# Fields: license (unique key), first/last (person), dni, clinical_specialty_id,
#         profession_id, office description, alias, overturn, from/to time.
ROSTER = [
    # Legacy pair — keep license/office/hours EXACTLY as first shipped so a
    # previously-seeded HSI stays untouched (diary create is skip-if-exists).
    # The cardio last name is fictional since 2026-11-16; an HSI seeded
    # before keeps its old person (person create is skip-if-exists by DNI).
    dict(lic="MP-DEMO-CARDIO", first="ALEJANDRO", last="BERISSO", dni="90000001",
         esp=131, prof=PROF_MEDICO, office="Consultorio Cardiología DEMO",
         alias="DEMO Cardiología", overturn=CARDIO_OVERTURN_COUNT,
         desde="08:00:00", hasta="12:00:00"),
    dict(lic="MP-DEMO-KINE", first="JOSE", last="KINEDEMO", dni="90000002",
         esp=39, prof=PROF_KINESIOLOGO, office="Consultorio Kinesiología DEMO",
         alias="DEMO Kinesiología", overturn=0,
         desde="08:00:00", hasta="12:00:00"),
    # Wide roster (2026-07-21) — Mon-Fri 08:00-17:00 in the shared office.
    dict(lic="MP-DEMO-CLINICA", first="MARTA", last="CLINIDEMO", dni="90000003", esp=128, alias="DEMO Clínica médica"),
    dict(lic="MP-DEMO-GENERAL", first="RAUL", last="GENERDEMO", dni="90000004", esp=91, alias="DEMO Medicina general"),
    dict(lic="MP-DEMO-PEDIATRIA", first="LAURA", last="PEDIADEMO", dni="90000005", esp=165, alias="DEMO Pediatría"),
    dict(lic="MP-DEMO-GINECO", first="SILVIA", last="GINECODEMO", dni="90000006", esp=157, alias="DEMO Ginecología"),
    dict(lic="MP-DEMO-OBSTETRICIA", first="NORA", last="OBSTEDEMO", dni="90000007", esp=159, alias="DEMO Obstetricia"),
    dict(lic="MP-DEMO-DERMATOLOGIA", first="HUGO", last="DERMADEMO", dni="90000008", esp=133, alias="DEMO Dermatología"),
    dict(lic="MP-DEMO-DIABETOLOGIA", first="ELSA", last="DIABEDEMO", dni="90000009", esp=296, alias="DEMO Diabetología"),
    dict(lic="MP-DEMO-ENDOCRINO", first="PABLO", last="ENDOCDEMO", dni="90000010", esp=134, alias="DEMO Endocrinología"),
    dict(lic="MP-DEMO-GASTRO", first="IRMA", last="GASTRODEMO", dni="90000011", esp=137, alias="DEMO Gastroenterología"),
    dict(lic="MP-DEMO-GERIATRIA", first="OSCAR", last="GERIADEMO", dni="90000012", esp=139, alias="DEMO Geriatría"),
    dict(lic="MP-DEMO-INFECTO", first="ALICIA", last="INFECTODEMO", dni="90000013", esp=141, alias="DEMO Infectología"),
    dict(lic="MP-DEMO-NEUMO", first="JORGE", last="NEUMODEMO", dni="90000014", esp=143, alias="DEMO Neumonología"),
    dict(lic="MP-DEMO-NEURO", first="CARLA", last="NEURODEMO", dni="90000015", esp=144, alias="DEMO Neurología"),
    dict(lic="MP-DEMO-NUTRICION", first="DIEGO", last="NUTRIDEMO", dni="90000016", esp=145, alias="DEMO Nutrición"),
    dict(lic="MP-DEMO-OFTALMO", first="MIRTA", last="OFTALDEMO", dni="90000017", esp=160, alias="DEMO Oftalmología"),
    dict(lic="MP-DEMO-ORL", first="FELIX", last="ORLDEMO", dni="90000018", esp=162, alias="DEMO Otorrinolaringología"),
    dict(lic="MP-DEMO-PSICOLOGIA", first="SONIA", last="PSICODEMO", dni="90000019", esp=46, alias="DEMO Psicología"),
    dict(lic="MP-DEMO-PSIQUIATRIA", first="BRUNO", last="PSIQUIDEMO", dni="90000020", esp=178, alias="DEMO Psiquiatría"),
    dict(lic="MP-DEMO-TRAUMATO", first="GLADYS", last="TRAUMADEMO", dni="90000021", esp=161, alias="DEMO Ortopedia y traumatología"),
    dict(lic="MP-DEMO-UROLOGIA", first="NESTOR", last="URODEMO", dni="90000022", esp=164, alias="DEMO Urología"),
    dict(lic="MP-DEMO-CIRUGIA", first="TERESA", last="CIRUDEMO", dni="90000023", esp=58, alias="DEMO Cirugía"),
    dict(lic="MP-DEMO-NEONATO", first="FABIAN", last="NEONATODEMO", dni="90000024", esp=172, alias="DEMO Neonatología"),
    dict(lic="MP-DEMO-FONO", first="ESTELA", last="FONODEMO", dni="90000025", esp=79, alias="DEMO Fonoaudiología"),
    dict(lic="MP-DEMO-IMAGENES", first="RAMON", last="IMAGENDEMO", dni="90000026", esp=182, alias="DEMO Diagnóstico por imágenes"),
    dict(lic="MP-DEMO-LABORATORIO", first="BEATRIZ", last="LABDEMO", dni="90000027", esp=88, alias="DEMO Bioquímica clínica"),
    dict(lic="MP-DEMO-TOCUPACIONAL", first="WALTER", last="TODEMO", dni="90000028", esp=34, alias="DEMO Terapia ocupacional"),
    dict(lic="MP-DEMO-PSICOPEDAGOGIA", first="ANDREA", last="PSPDEMO", dni="90000029", esp=53, alias="DEMO Psicopedagogía"),
]
for _r in ROSTER:
    _r.setdefault("prof", PROF_MEDICO)
    # HSI validates diary overlap PER doctors_office: two overlapping diaries
    # in one shared office are a 400. One consultorio per professional.
    _r.setdefault("office", "Consultorio %s DEMO" % _r["alias"].replace("DEMO ", ""))
    _r.setdefault("overturn", 0)
    _r.setdefault("desde", "08:00:00")
    _r.setdefault("hasta", "17:00:00")


PRELUDE_SQL = r"""
-- Sector (Ambulatorio) --------------------------------------------------------
INSERT INTO sector(institution_id, description, sector_type_id, created_by, created_on, updated_by, updated_on, deleted)
SELECT {inst}, 'Consultorios Externos DEMO', 1, -1, now(), -1, now(), false
WHERE NOT EXISTS (SELECT 1 FROM sector WHERE institution_id={inst} AND description='Consultorios Externos DEMO');

-- Ensure the admin (user 1) can CREATE diaries: role 9 = Administrador agenda.
-- (The sibling setup already grants role 5 = Administrativo, needed to book turnos.)
INSERT INTO user_role(role_id, user_id, institution_id, deleted, created_on, updated_on, created_by, updated_by)
SELECT 9, 1, {inst}, false, now(), now(), -1, -1
WHERE NOT EXISTS (SELECT 1 FROM user_role WHERE user_id=1 AND role_id=9 AND institution_id={inst});
INSERT INTO user_role(role_id, user_id, institution_id, deleted, created_on, updated_on, created_by, updated_by)
SELECT 5, 1, {inst}, false, now(), now(), -1, -1
WHERE NOT EXISTS (SELECT 1 FROM user_role WHERE user_id=1 AND role_id=5 AND institution_id={inst});
"""

OFFICE_SQL = r"""
INSERT INTO doctors_office(institution_id, description, opening_time, closing_time, sector_id, created_by, created_on, updated_by, updated_on, deleted, enabled)
SELECT {inst}, '{office}', '07:00', '20:00',
       (SELECT id FROM sector WHERE institution_id={inst} AND description='Consultorios Externos DEMO'),
       -1, now(), -1, now(), false, true
WHERE NOT EXISTS (SELECT 1 FROM doctors_office WHERE institution_id={inst} AND description='{office}');
"""

# One professional = person + healthcare_professional + profession +
# clinical specialty + user + user_person + clinical user_role.
# role 3 = Especialista Medico, 4 = Profesional de la salud.
PRO_SQL = r"""
INSERT INTO person(first_name, last_name, identification_type_id, identification_number, gender_id, birth_date)
SELECT '{first}', '{last}', 1, '{dni}', 2, DATE '1980-01-01'
WHERE NOT EXISTS (SELECT 1 FROM person WHERE identification_type_id=1 AND identification_number='{dni}');

INSERT INTO healthcare_professional(license_number, person_id, deleted, created_on, updated_on, created_by, updated_by)
SELECT '{lic}', (SELECT id FROM person WHERE identification_number='{dni}' AND identification_type_id=1), false, now(), now(), -1, -1
WHERE NOT EXISTS (SELECT 1 FROM healthcare_professional WHERE license_number='{lic}');

INSERT INTO professional_professions(healthcare_professional_id, professional_specialty_id, created_by, created_on, updated_by, updated_on, deleted)
SELECT (SELECT id FROM healthcare_professional WHERE license_number='{lic}'), {prof}, -1, now(), -1, now(), false
WHERE NOT EXISTS (SELECT 1 FROM professional_professions pp WHERE pp.healthcare_professional_id=(SELECT id FROM healthcare_professional WHERE license_number='{lic}') AND pp.professional_specialty_id={prof});

INSERT INTO healthcare_professional_specialty(clinical_specialty_id, professional_profession_id, deleted, created_on, updated_on, created_by, updated_by)
SELECT {esp}, (SELECT pp.id FROM professional_professions pp WHERE pp.healthcare_professional_id=(SELECT id FROM healthcare_professional WHERE license_number='{lic}') AND pp.professional_specialty_id={prof}), false, now(), now(), -1, -1
WHERE NOT EXISTS (SELECT 1 FROM healthcare_professional_specialty hps WHERE hps.clinical_specialty_id={esp} AND hps.professional_profession_id=(SELECT pp.id FROM professional_professions pp WHERE pp.healthcare_professional_id=(SELECT id FROM healthcare_professional WHERE license_number='{lic}') AND pp.professional_specialty_id={prof}));

INSERT INTO users(username, enable, deleted, two_factor_authentication_enabled, created_on, updated_on, created_by, updated_by)
SELECT '{user}', true, false, false, now(), now(), -1, -1
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username='{user}');

INSERT INTO user_person(user_id, person_id)
SELECT (SELECT id FROM users WHERE username='{user}'), (SELECT id FROM person WHERE identification_number='{dni}' AND identification_type_id=1)
WHERE NOT EXISTS (SELECT 1 FROM user_person WHERE user_id=(SELECT id FROM users WHERE username='{user}'));

INSERT INTO user_role(role_id, user_id, institution_id, deleted, created_on, updated_on, created_by, updated_by)
SELECT {role}, (SELECT id FROM users WHERE username='{user}'), {inst}, false, now(), now(), -1, -1
WHERE NOT EXISTS (SELECT 1 FROM user_role WHERE user_id=(SELECT id FROM users WHERE username='{user}') AND role_id={role} AND institution_id={inst});
"""


def _user_de(rec):
    return "%s.demo@sana.local" % rec["lic"].replace("MP-DEMO-", "").lower()


def _role_de(rec):
    return 3 if rec["prof"] == PROF_MEDICO else 4


def _seed_sql():
    parts = ["\\set ON_ERROR_STOP on", "BEGIN;", PRELUDE_SQL.format(inst=INSTITUTION_ID)]
    for office in dict.fromkeys(r["office"] for r in ROSTER):
        parts.append(OFFICE_SQL.format(inst=INSTITUTION_ID, office=office))
    for rec in ROSTER:
        parts.append(PRO_SQL.format(inst=INSTITUTION_ID, user=_user_de(rec),
                                    role=_role_de(rec), **rec))
    parts.append("COMMIT;")
    return "\n".join(parts)


def _query_ids_sql():
    values = ",\n    ".join("('%s','%s')" % (r["lic"], r["office"]) for r in ROSTER)
    return r"""
SELECT hp.id, hp.license_number,
       (SELECT id FROM doctors_office WHERE institution_id={inst} AND description=off.descr) AS office_id
FROM (VALUES
    %s
) AS off(lic, descr)
JOIN healthcare_professional hp ON hp.license_number = off.lic
ORDER BY hp.id;
""".format(inst=INSTITUTION_ID) % values


def _psql(sql, tuples=False):
    cmd = ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    if tuples:
        cmd += ["-t", "-A", "-F", "|"]
    p = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("psql failed (%s):\n%s" % (p.returncode, p.stderr.strip()))
    return p.stdout.strip()


def seed_db():
    print("== DB seed (sector, consultorios, %s professionals, users, roles) ==" % len(ROSTER))
    _psql(_seed_sql())
    rows = _psql(_query_ids_sql(), tuples=True)
    ids = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        hp_id, lic, office_id = line.split("|")
        ids[lic] = {"hp_id": int(hp_id), "office_id": int(office_id)}
    print("   professionals+offices: %s entries" % len(ids))
    return ids


def seed_diaries(ids):
    print("== REST seed (diaries, Mon-Fri; legacy pair 08-12, roster 08-17) ==")
    client = HsiClient()
    client.login()
    today = date.today()
    end = today + timedelta(weeks=13)
    for rec in ROSTER:
        info = ids[rec["lic"]]
        existing = client._diaries_for_professional(info["hp_id"])
        if existing:
            d = existing[0]
            print("   [skip] %s already has diary #%s (%s)"
                  % (rec["lic"], d["id"], d.get("clinicalSpecialtyName")))
            continue
        diary_id = client.create_diary(
            healthcare_professional_id=info["hp_id"],
            doctors_office_id=info["office_id"],
            clinical_specialty_id=rec["esp"],
            start_date=today.isoformat(),
            end_date=end.isoformat(),
            week_days=(1, 2, 3, 4, 5),
            from_time=rec["desde"], to_time=rec["hasta"],
            appointment_duration=30,
            alias=rec["alias"],
            overturn_count=rec["overturn"],
        )
        print("   [created] diary #%s for %s (%s) %s..%s"
              % (diary_id, rec["lic"], rec["alias"], today, end))
    print("== diaries now available ==")
    for d in client.list_diaries():
        print("   #%s %s | %s | %s -> %s"
              % (d["diary_id"], d["specialty"], d["professional"], d["start_date"], d["end_date"]))


def configure_demo_overturns():
    """Keep an existing idempotent demo diary aligned with the web schedule."""
    updated = _psql(r"""
UPDATE diary_opening_hours AS doh
SET overturn_count={count}
FROM diary AS d, healthcare_professional AS hp
WHERE doh.diary_id=d.id
  AND d.healthcare_professional_id=hp.id
  AND hp.license_number='MP-DEMO-CARDIO';
""".format(count=CARDIO_OVERTURN_COUNT))
    print("== HSI overbooking capacity: Cardiología allows %s per opening =="
          % CARDIO_OVERTURN_COUNT)
    if updated:
        print("   %s" % updated)


def main():
    ids = seed_db()
    try:
        seed_diaries(ids)
        configure_demo_overturns()
    except HsiError as e:
        raise SystemExit("HSI REST seed failed: %s" % e)
    print("\nDone. Book with: python3 hsi_client.py --especialidad Cardiología")


if __name__ == "__main__":
    main()
