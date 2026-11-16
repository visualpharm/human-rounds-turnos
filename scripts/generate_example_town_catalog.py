#!/usr/bin/env python3
"""scripts/generate_example_town_catalog.py — SOURCE OF TRUTH for
packs/example-town/{especialidades,clusters,centros-salud,medicos,servicios,
example-localities}.json (and the `_meta.no_ofrecidos_actualmente` list
inside estudios-preparacion.json).

*** THIS SCRIPT OWNS THOSE FILES. Hand edits to the generated JSON get ***
*** silently overwritten the next time this runs. Edit THIS FILE, then  ***
*** re-run it: `python3 scripts/generate_example_town_catalog.py`.      ***

Sampleton ("example-town") is the fictional demo installation shown at
demo.humanrounds.org. It used to be a 3-doctor/8-service/2-center stub; this
generator grows it into something that looks like a real medium-sized
Argentine coastal municipality (~50 doctors, ~70 services, 5 centers,
~15-18 specialties) while staying entirely fictional — no real person, no
real matrícula registry (`fuente` is always "ficticio", never "colmed9" or
any other real registry name).

Determinism, no `random` (same discipline as
scripts/seed_demo_all.py:_pacientes() — two parallel index-zipped lists,
never a random draw): running this script twice produces byte-identical
JSON. That is what makes `git diff` after a re-run a meaningful signal (no
diff = nothing changed) and is asserted by
tests/test_data_packs.py:TestExampleTownGeneratorDeterminism.

Backward-compat constraints this generator deliberately preserves (other
test files this repo does not want touched still assert on them):
  - Center slugs "sampleton-hospital" and "riverside-clinic" keep existing (3 new
    centers are ADDED: "lakeview-clinic", "maternity-center",
    "diagnostic-center" — see test_sitedata_dir.py, test_site_identity.py).
  - Service ids "vacunacion-riverside-clinic" and "cardiologia-sampleton-hospital"
    keep existing, and the latter stays a bookable (non-walk-in) service at
    "sampleton-hospital" (test_golden_pages.py's
    TestExampleTownChainIsGeneric.SAMPLETON_SERVICE_ID/
    SAMPLETON_CENTER_SLUG — this docstring note itself went stale through
    three renames, Villa Ejemplo -> Sampleton -> Ojo Manso ->
    Sampleton again (2026-09-16, back to the language-neutral name), without the
    attribute names being updated to match; fixed 2026-08-05).
  - No walkin/walkin_con_orden service EVER carries an `agenda` key, even
    the `apertura_mismo_dia` ones — test_invariantes_datos.py's
    TestWalkinSinAgenda has no exception for same-day-opening, so the
    feature is expressed with the boolean flag alone (patient_web/site_*.py's
    `_opens_same_day` doesn't need an `agenda` block either).
  - sitio.json/example-localities.json/estudios-preparacion.json's
    "estudios" list are NOT rewritten wholesale by this script — only
    estudios-preparacion.json's `_meta.no_ofrecidos_actualmente` gets
    recomputed (see `_update_estudios_meta` at the bottom), because that
    list needs to track which studies the new servicios.json actually
    offers.

NOTE on scope: `set(centro slugs)` growing from 2 to 5 means the legacy
TURNOS_WEBDATA_DIR=packs/example-town fixture used by
test_sitedata_dir.py's `test_hospital_servicios_es_ojo_manso_sin_pinamar`
(`self.assertEqual(set(centros), {"sampleton-hospital", "riverside-clinic"})`) will
no longer hold — that assertion is a hard product requirement conflict with
"grow to 5 centers" and is left for a follow-up pass on that test file
(out of scope for this script, which only owns packs/example-town/**).
"""
import json
import os
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_DIR = os.path.join(ROOT, "packs", "example-town")


def _slug(text):
    normal = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in normal.lower() if c.isalnum())


def _norm(text):
    """Lowercase, accent-stripped — for keyword/substring matching only."""
    normal = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return normal.lower()


def _write(fname, doc):
    path = os.path.join(PACK_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


# ============================================================ centros =====
# 5 centers with the shape a real coastal municipality has: one hospital,
# two CAPS in different neighbourhoods (Norte/Sur), a maternal-infant
# center, and a diagnostics annex. "sampleton-hospital"/"riverside-clinic" keep
# their existing slugs and copy (see module docstring).

CENTROS = [
    {
        "slug": "sampleton-hospital",
        "name": "Sampleton Hospital",
        "address": "Main Rd. 123",
        "phone": "(011) 4000-0001",
        "email": "hospital@sampleton.example",
        # Only what this center's own Servicios list has: imaging and blood
        # tests are at the Diagnostic Center, and Cardiología asks for no
        # order (demo review 2026-11-15, D30/D31).
        "description": "Hospital municipal ficticio. Guardia y consultorios de especialidades.",
        "jtbd": "Guardia las 24 horas y consultorios de especialistas: cardiología, traumatología, dermatología, oftalmología y más.",
        "jobs": [
            "Atenderte en la guardia",
            "Ver a un especialista",
            "Hacerte un electrocardiograma o una ergometría",
            "Vacunarte en la campaña antigripal",
        ],
        "maps_url": "https://maps.google.com/?q=Main+Rd.+123",
        "bookable": True,
    },
    {
        "slug": "riverside-clinic",
        "name": "Riverside Clinic",
        "address": "Station St. 456",
        "phone": "(011) 4000-0002",
        "email": "riverside@sampleton.example",
        "description": "Centro de Atención Primaria de la Salud ficticio del barrio Riverside. Medicina general, pediatría, ginecología, nutrición, odontología, psicología y vacunatorio.",
        "jtbd": "La salita del barrio Riverside: el médico clínico con o sin turno, pediatría, ginecología, odontología y vacunas.",
        "jobs": [
            "Vacunarte",
            "Ver al médico clínico sin turno",
            "Llevar a tu hijo al pediatra",
            "Sacar turno de ginecología o de odontología",
        ],
        "maps_url": "https://maps.google.com/?q=Station+St.+456",
        "bookable": True,
    },
    {
        "slug": "lakeview-clinic",
        "name": "Lakeview Clinic",
        "address": "Harbor Ave. 789",
        "phone": "(011) 4000-0003",
        "email": "lakeview@sampleton.example",
        "description": "Centro de Atención Primaria de la Salud ficticio del barrio Lakeview. Medicina general, control del adulto mayor, pediatría, nutrición, kinesiología, odontología y psicología.",
        "jtbd": "La salita del barrio Lakeview: clínica médica, control del adulto mayor, kinesiología y talleres para adultos mayores.",
        "jobs": [
            "Ver al médico clínico",
            "Consultar al nutricionista",
            "Empezar kinesiología con orden",
            "Ir al taller de gimnasia para adultos mayores",
        ],
        "maps_url": "https://maps.google.com/?q=Harbor+Ave.+789",
        "bookable": True,
    },
    {
        "slug": "maternity-center",
        "name": "Maternity Center",
        "address": "Garden Ave. 500",
        "phone": "(011) 4000-0004",
        "email": "maternity@sampleton.example",
        "description": "Centro ficticio dedicado a la salud de la mujer embarazada, los recién nacidos y los niños pequeños: obstetricia, pediatría, fonoaudiología y estimulación temprana.",
        "jtbd": "El control del embarazo, del bebé y de los primeros años, todo en un mismo edificio.",
        "jobs": [
            "Hacer el control prenatal",
            "Llevar al bebé a los controles de peso y talla",
            "Sacar turno de pediatría",
            "Hacer la pesquisa auditiva neonatal",
        ],
        "maps_url": "https://maps.google.com/?q=Garden+Ave.+500",
        "bookable": True,
    },
    {
        "slug": "diagnostic-center",
        "name": "Diagnostic Center",
        "address": "Central Dr. 234",
        "phone": "(011) 4000-0005",
        "email": "diagnostic@sampleton.example",
        "description": "Anexo ficticio de estudios, separado del edificio principal del hospital: laboratorio, radiografía, ecografías, mamografía, tomografía, ecocardiograma y audiometría.",
        "jtbd": "Los estudios de laboratorio y de imágenes, en un edificio propio para no cruzarse con la guardia.",
        "jobs": [
            "Hacerte un análisis de sangre",
            "Sacarte una radiografía o una ecografía",
            "Retirar el resultado de un estudio",
        ],
        "maps_url": "https://maps.google.com/?q=Central+Dr.+234",
        "bookable": True,
    },
]


# ======================================================= especialidades ====
# Sampleton's OWN taxonomy (pack.json declares "compose: replace" for
# both this file and clusters.json — see docs/architecture/data-packs.md).
# Cluster ids intentionally reuse packs/global/clusters.json's ids where the
# concept matches (same shared /static/icons/<id>.png assets), but this
# file's clusters.json fully REPLACES the chain's view — no inheritance.

CLUSTERS = [
    {"id": "clinica", "nombre": "Clínica y medicina general", "top": 1},
    {"id": "diagnostico", "nombre": "Diagnóstico y imagen", "top": 2},
    {"id": "trauma-kine", "nombre": "Traumatología y kinesiología", "top": 3},
    {"id": "pediatria", "nombre": "Pediatría", "top": 4},
    {"id": "salud-mental", "nombre": "Salud mental", "top": 5},
    {"id": "endocrino", "nombre": "Endocrinología, diabetes y nutrición", "top": 6},
    {"id": "cardiologia", "nombre": "Cardiología", "top": 7},
    {"id": "gineco", "nombre": "Ginecología y obstetricia", "top": 8},
    {"id": "odontologia", "nombre": "Odontología", "top": 9},
    {"id": "vacunas-prevencion", "nombre": "Vacunas y prevención", "top": 10},
    {"id": "otorrino-fono", "nombre": "Otorrinolaringología y fonoaudiología", "top": 11},
    {"id": "grupos", "nombre": "Grupos y talleres", "top": 12},
    {"id": "otras", "nombre": "Otras especialidades", "top": 13},
    # Same shape as Pinamar's card (kind walkin + descripcion): the walk-in
    # page of the installation, always the last «Más buscadas» card. Its
    # services are the pack's own walk-in ones; none ever offers appointments.
    {"id": "demanda-espontanea", "nombre": "Demanda espontánea", "kind": "walkin",
     "descripcion": "Sin turno: guardia las 24 horas en el hospital y, en las clínicas, "
                    "medicina general, vacunación y curaciones.",
     "top": 14, "icon": "/static/icons/clinica.png"},
]

# (nombre, cluster) — 18 specialties, every one of them the "especialidad"
# of at least one doctor below (checked by
# TestExampleTownCatalogIntegrity.test_every_doctor_specialty_and_cluster_exist).
ESPECIALIDADES = [
    ("Medicina general", "clinica"),
    ("Geriatría", "clinica"),
    ("Pediatría", "pediatria"),
    ("Cardiología", "cardiologia"),
    ("Nutrición", "endocrino"),
    ("Endocrinología", "endocrino"),
    ("Ginecología y Obstetricia", "gineco"),
    ("Traumatología", "trauma-kine"),
    ("Kinesiología", "trauma-kine"),
    ("Odontología", "odontologia"),
    ("Otorrinolaringología", "otorrino-fono"),
    ("Fonoaudiología", "otorrino-fono"),
    ("Psicología", "salud-mental"),
    ("Psiquiatría", "salud-mental"),
    ("Dermatología", "otras"),
    ("Oftalmología", "otras"),
    ("Diagnóstico por imágenes", "diagnostico"),
    ("Laboratorio", "diagnostico"),
    ("Urología", "otras"),
]
_ESPECIALIDAD_CLUSTER = dict(ESPECIALIDADES)

# How many doctors each specialty gets (sums to 50 — asserted below).
_CONTEO_ESPECIALIDAD = [
    ("Medicina general", 6),
    ("Geriatría", 2),
    ("Pediatría", 5),
    ("Cardiología", 4),
    ("Nutrición", 3),
    ("Endocrinología", 2),
    ("Ginecología y Obstetricia", 3),
    ("Traumatología", 3),
    ("Kinesiología", 3),
    ("Odontología", 4),
    ("Otorrinolaringología", 2),
    ("Fonoaudiología", 2),
    ("Psicología", 3),
    ("Psiquiatría", 2),
    ("Dermatología", 2),
    ("Oftalmología", 2),
    ("Diagnóstico por imágenes", 1),
    ("Laboratorio", 1),
]
assert sum(n for _, n in _CONTEO_ESPECIALIDAD) == 50
# Urología comes only with its one professional in _MEDICOS_EXTRA (TASK-58).
assert {n for n, _ in _CONTEO_ESPECIALIDAD} | {"Urología"} == {n for n, _ in ESPECIALIDADES}

# Which center(s) a specialty's doctors work at — one slug, or two to
# alternate by position-within-specialty (index parity).
_ESPECIALIDAD_CENTROS = {
    "Medicina general": ["riverside-clinic", "lakeview-clinic"],
    "Geriatría": ["lakeview-clinic"],
    "Pediatría": ["maternity-center", "riverside-clinic"],
    "Cardiología": ["sampleton-hospital"],
    "Nutrición": ["riverside-clinic", "lakeview-clinic"],
    "Endocrinología": ["sampleton-hospital"],
    "Ginecología y Obstetricia": ["maternity-center"],
    "Traumatología": ["sampleton-hospital"],
    "Kinesiología": ["lakeview-clinic"],
    "Odontología": ["riverside-clinic", "lakeview-clinic"],
    "Otorrinolaringología": ["sampleton-hospital"],
    "Fonoaudiología": ["maternity-center"],
    "Psicología": ["riverside-clinic"],
    "Psiquiatría": ["sampleton-hospital"],
    "Dermatología": ["sampleton-hospital"],
    "Oftalmología": ["sampleton-hospital"],
    "Diagnóstico por imágenes": ["diagnostic-center"],
    "Laboratorio": ["diagnostic-center"],
}


# ============================================================ médicos =====
# Two parallel, index-zipped name lists (same discipline as
# seed_demo_all.py:_pacientes()) — no `random`. 50 first names, 50
# surnames, both distinct pools from the PATIENT name lists in
# seed_demo_all.py (a demo shouldn't have a doctor and a patient sharing a
# full name by coincidence of list order).
#
# Surnames span a deliberately wide range of immigrant-community origins
# actually present in Argentina — Spanish/criollo and Italian stay the
# largest groups (historically accurate), plus Jewish, Arab/Levantine,
# Slavic, East Asian, South Asian, West African and Andean-indigenous
# surnames — so the roster doesn't read as uniformly Latin. First-name
# gender is interleaved WITHIN each specialty block on purpose: the
# original ordering put all 25 male names before all 25 female ones and
# every specialty pulls from a contiguous index range, so every
# multi-doctor specialty came out entirely one gender by accident of list
# order (all cardiologists male, all psychologists female, etc). gpt-image-2
# race/age/build variety for the portrait itself lives in
# gen_demo_doctor_photos.py, keyed off these surnames.
#
# 11 (nombre_pila, apellido) pairs are load-bearing anchors hardcoded by id
# in scripts/seed_demo_all.py and tests/test_manage_demo_seed.py — indices
# 0, 8, 13, 17, 22, 25, 31, 35, 39, 44, 48 must keep their current name AND
# specialty. Renaming/reordering anything else is safe: nothing outside
# this generator and gen_demo_doctor_photos.py references a non-anchor id.
NOMBRES_MEDICOS = [
    "Juan", "Carlos", "Miguel", "María", "Laura", "Patricia", "Jorge", "Silvia",
    "Pablo", "Roberto", "Luis", "Claudia", "Valentina", "Sergio", "Diego",
    "Camila", "Florencia", "Hernán", "Martín", "Gabriela", "Fernando",
    "Verónica", "Emiliano", "Gustavo", "Lucía", "Ana", "Ricardo", "Daniel",
    "Alejandro", "Marina", "Fernanda", "Mariana", "Marcelo", "Adrián", "Paula",
    "Julieta", "Nicolás", "Ezequiel", "Cecilia", "Carolina", "Federico",
    "Rocío", "Matías", "Victoria", "Agustina", "Gonzalo", "Rodrigo",
    "Antonella", "Milagros", "Sabrina",
]
APELLIDOS_MEDICOS = [
    "González", "Wu", "Kowalski", "Haddad", "Katz", "Martínez", "García",
    "Singh", "Sánchez", "Romero", "Diallo", "Torres", "Chen", "Ruiz", "Walsh",
    "Levin", "Acosta", "Benítez", "Medina", "Nazar", "Suárez", "Park",
    "Molina", "Ibarra", "Petrenko", "Rojas", "Domínguez", "Kim", "Quispe",
    "Bustos", "Sharma", "Núñez", "Nowak", "Karam", "Arias", "Cáceres",
    "Schmidt", "Ferreyra", "Grynberg", "Villalba", "Bianchi", "Ferraro",
    "Rossi", "Colombo", "Conti", "Bruno", "Marino", "Costa", "Greco",
    "Moretti",
]
assert len(NOMBRES_MEDICOS) == 50 and len(set(NOMBRES_MEDICOS)) == 50
assert len(APELLIDOS_MEDICOS) == 50 and len(set(APELLIDOS_MEDICOS)) == 50

# Each doctor's `genero` ("f"/"m"): the public site titles a woman
# «Cardióloga», «Médica general», «Dra.» from this field and never guesses
# from the name (demo review 2026-11-15, D14). Every given name of this
# generator is listed in exactly one of the two sets; a new name that is in
# neither stops the generator.
_NOMBRES_F = {
    "María", "Laura", "Patricia", "Silvia", "Claudia", "Valentina", "Camila",
    "Florencia", "Gabriela", "Verónica", "Lucía", "Ana", "Marina", "Fernanda",
    "Mariana", "Paula", "Julieta", "Cecilia", "Carolina", "Rocío", "Victoria",
    "Agustina", "Antonella", "Milagros", "Sabrina", "Andrea", "Lorena", "Noelia",
    "Silvina", "Natalia", "Micaela", "Julia", "Mónica", "Belén", "Graciela",
    "Soledad",
}
_NOMBRES_M = {
    "Juan", "Carlos", "Miguel", "Jorge", "Pablo", "Roberto", "Luis", "Sergio",
    "Diego", "Hernán", "Martín", "Fernando", "Emiliano", "Gustavo", "Ricardo",
    "Daniel", "Alejandro", "Marcelo", "Adrián", "Nicolás", "Ezequiel",
    "Federico", "Matías", "Gonzalo", "Rodrigo", "Tomás", "Bruno", "Esteban",
    "Raúl", "Facundo", "Leandro", "Ignacio",
}
assert not _NOMBRES_F & _NOMBRES_M


def _genero(nombre_pila):
    if nombre_pila in _NOMBRES_F:
        return "f"
    assert nombre_pila in _NOMBRES_M, f"{nombre_pila}: falta en _NOMBRES_F o _NOMBRES_M"
    return "m"

# Schedule templates: (franjas, horario_text). franjas is a list of
# (dias, desde, hasta, cada_min) bands — one band for most, two for the
# "bimodal" template, so the doctor pool doesn't read as identical
# Mon-Fri 08-12 fifty times over.
_PLANTILLAS = [
    ([((1, 2, 3, 4, 5), "08:00", "12:00", 20)], "Lunes a viernes de 8 a 12 h"),
    ([((1, 2, 3, 4, 5), "14:00", "18:00", 20)], "Lunes a viernes de 14 a 18 h"),
    ([((1, 3, 5), "09:00", "13:00", 15)], "Lunes, miércoles y viernes de 9 a 13 h"),
    ([((2, 4), "10:00", "13:00", 30)], "Martes y jueves de 10 a 13 h"),
    ([((2, 4, 6), "08:30", "11:30", 20)], "Martes, jueves y sábado de 8.30 a 11.30 h"),
    ([((3,), "09:00", "12:00", 20)], "Miércoles de 9 a 12 h"),
    ([((1, 2, 3, 4, 5), "07:00", "10:00", 15)], "Lunes a viernes de 7 a 10 h"),
    ([((1,), "09:30", "11:30", 20), ((4,), "15:00", "17:00", 20)],
     "Lunes de 9.30 a 11.30 h y jueves de 15 a 17 h"),
    ([((0, 6), "09:00", "12:00", 20)], "Sábados y domingos de 9 a 12 h"),
]

# The weekend template. The demo is shown on any day, and a Saturday or
# Sunday without a single doctor left «Pacientes de hoy» empty (2026-11-08).
# Sunday carries only what a public hospital plausibly runs on a Sunday
# morning — pediatrics and obstetrics at the maternity, general practice,
# traumatology; dermatology, ENT and nutrition offered «Hoy, 11:00» on a
# Sunday (demo review 2026-11-15, D15) and now run on Saturday only. The
# templates below are not in _PLANTILLAS on purpose: appending there would
# rotate every other doctor's schedule (`i % len(_PLANTILLAS)`).
_FIN_DE_SEMANA = 8
_SABADO = ([((6,), "09:00", "12:00", 20)], "Sábados de 9 a 12 h")


def _con_domingo(i):
    """Template `i` plus a Sunday morning (9 to 12)."""
    franjas, horario = _PLANTILLAS[i]
    return (franjas + [((0,), "09:00", "12:00", 20)],
            horario + " y domingos de 9 a 12 h")


def _agenda_de_sus_servicios(medicos, servicios):
    """Each doctor's own schedule IS the agenda of the services that run it.

    A service with an agenda of its own manages its doctors' hours at
    runtime (`schedule_policy.managed_services`: the slot generator, the
    queue, the printed day sheet); the doctor's own windows are what the
    public page's «Cuándo atiende» shows. The demo had the two disagree for
    51 of 68 doctors (Sánchez: «domingo 9–12» on his page, Tuesday and
    Thursday in the agenda), so the public page now copies the service
    agenda — one schedule, the service's (decision 2026-11-08). Windows are
    tagged with their service when the doctor serves more than one (the
    page names each office) or was already tagged; a doctor no scheduled
    service manages keeps the template of its own."""
    import sys
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from app.modules.turnos import schedule_policy

    out = []
    for m in medicos:
        suyos = [s for s in servicios
                 if schedule_policy.has_schedule(s) and schedule_policy.belongs(m, s, peers=medicos)]
        if not suyos:
            out.append(m)
            continue
        etiquetar = len(suyos) > 1 or any(f.get("servicio") for c in m["centros"] for f in c["franjas"])
        por_centro = {}
        for s in suyos:
            ag = s["agenda"]
            for b in ag.get("franjas") or [ag]:
                fr = {"dias": list(b["dias"]), "desde": b["desde"], "hasta": b["hasta"],
                      "cada_min": b["cada_min"]}
                if etiquetar:
                    fr["servicio"] = s["id"]
                por_centro.setdefault(s["centro"], []).append(fr)
        out.append(dict(
            m,
            centros=[{"centro": c, "franjas": f, "excepciones": []} for c, f in por_centro.items()],
            agenda={"franjas": list(por_centro.get(m["centro"], [])), "excepciones": []},
        ))
    return out


def _agenda_de(franjas):
    """Single-band shorthand {dias,desde,hasta,cada_min} when there's only
    one band, the {franjas,excepciones} multi-band form otherwise — same
    two shapes catalog.py's validator already accepts."""
    if len(franjas) == 1:
        dias, desde, hasta, cada = franjas[0]
        return {"dias": list(dias), "desde": desde, "hasta": hasta, "cada_min": cada}
    return {
        "franjas": [{"dias": list(d), "desde": de, "hasta": ha, "cada_min": c}
                    for d, de, ha, c in franjas],
        "excepciones": [],
    }


# Specialties whose doctors name their consultation on their own schedule
# (`franjas[].servicio`), as the desk does in Pinamar. The demo shows both
# forms: a tagged schedule here, the inference by specialty for the rest.
# Cardiology first (TASK-56, 26.10): its cluster also holds the ECG and the
# ergometry, which need an order, and an untagged cardiologist was shown as
# «Subí tu orden».
_ESPECIALIDAD_ETIQUETADA = {"Cardiología"}


def _build_medicos(servicios=()):
    especialidad_flat = []
    for nombre_esp, n in _CONTEO_ESPECIALIDAD:
        especialidad_flat.extend([nombre_esp] * n)
    assert len(especialidad_flat) == 50

    contador_por_especialidad = {}
    medicos = []
    for i in range(50):
        nombre_pila = NOMBRES_MEDICOS[i]
        apellido = APELLIDOS_MEDICOS[i]
        especialidad = especialidad_flat[i]
        cluster = _ESPECIALIDAD_CLUSTER[especialidad]

        centros_posibles = _ESPECIALIDAD_CENTROS[especialidad]
        pos = contador_por_especialidad.get(especialidad, 0)
        contador_por_especialidad[especialidad] = pos + 1
        centro = centros_posibles[pos % len(centros_posibles)]

        franjas, _horario = _PLANTILLAS[i % len(_PLANTILLAS)]
        propio = next((s["id"] for s in servicios
                       if especialidad in _ESPECIALIDAD_ETIQUETADA
                       and s["nombre"] == especialidad and s["centro"] == centro), None)
        centro_block = {
            "centro": centro,
            "franjas": [dict({"dias": list(d), "desde": de, "hasta": ha, "cada_min": c},
                             **({"servicio": propio} if propio else {}))
                        for d, de, ha, c in franjas],
            "excepciones": [],
        }
        agenda = {"franjas": list(centro_block["franjas"]), "excepciones": []}

        mid = f"{_slug(apellido)}-{_slug(nombre_pila)}"
        medicos.append({
            "id": mid,
            "nombre_pila": nombre_pila,
            "apellido": apellido,
            "nombre": f"{nombre_pila} {apellido}",
            "genero": _genero(nombre_pila),
            "especialidad": especialidad,
            "cluster": cluster,
            "centro": centro,
            "centros": [centro_block],
            "agenda": agenda,
            "matricula": {
                "numero": str(40000 + i * 173),
                "provincia": "Buenos Aires",
                "estado": "Activo",
                "confianza": "high",
                "fuente": "ficticio",
            },
        })
    return medicos


# One professional for every bookable service no doctor above covers
# (TASK-58, 26.10): the demo answered «No hay médicos de Ecografía general
# cargados» when a request was approved. Each one has a specialty of this
# pack's catalog and a schedule that names the service; the schedule
# template follows the main list's rotation.
_MEDICOS_EXTRA = [
    ("Andrea", "Olmedo", "medicina-general-maternity", "Medicina general"),
    ("Tomás", "Barreiro", "pediatria-lakeview-clinic", "Pediatría"),
    # ecocardiograma-anexo stays without a cardiologist on purpose: it is
    # the pack's one study with no specialist at its centre, and J16 pins
    # that the desk is then offered nobody of another specialty.
    ("Lorena", "Paredes", "nutricion-infantil-maternity", "Nutrición"),
    ("Noelia", "Cardozo", "planificacion-familiar-riverside-clinic", "Ginecología y Obstetricia"),
    ("Silvina", "Mansilla", "ginecologia-riverside-clinic", "Ginecología y Obstetricia"),
    ("Bruno", "Echeverría", "kinesiologia-respiratoria-maternity", "Kinesiología"),
    ("Natalia", "Funes", "odontopediatria-maternity", "Odontología"),
    ("Micaela", "Soria", "fonoaudiologia-infantil-riverside-clinic", "Fonoaudiología"),
    ("Esteban", "Lagos", "psicologia-lakeview-clinic", "Psicología"),
    ("Julia", "Avendaño", "psicologia-infantil-maternity", "Psicología"),
    ("Raúl", "Gimenez", "grupo-adicciones-lakeview-clinic", "Psicología"),
    ("Mónica", "Herrero", "ecografia-general-anexo", "Diagnóstico por imágenes"),
    ("Facundo", "Ledesma", "ecografia-abdominal-anexo", "Diagnóstico por imágenes"),
    ("Belén", "Quiroga", "ecografia-obstetrica-maternity", "Diagnóstico por imágenes"),
    ("Graciela", "Pereyra", "mamografia-anexo", "Diagnóstico por imágenes"),
    ("Leandro", "Ojeda", "tomografia-anexo", "Diagnóstico por imágenes"),
    ("Soledad", "Vallejos", "retinografia-anexo", "Oftalmología"),
    ("Ignacio", "Peralta", "urologia-hospital", "Urología"),
]


def _medicos_extra(servicios, desde):
    por_id = {s["id"]: s for s in servicios}
    out = []
    for k, (nombre_pila, apellido, sid, especialidad) in enumerate(_MEDICOS_EXTRA):
        s = por_id[sid]
        assert _ESPECIALIDAD_CLUSTER[especialidad] == s["cluster"], sid
        i = desde + k
        franjas, _horario = _PLANTILLAS[i % len(_PLANTILLAS)]
        bloque = {
            "centro": s["centro"],
            "franjas": [{"dias": list(d), "desde": de, "hasta": ha, "cada_min": c, "servicio": sid}
                        for d, de, ha, c in franjas],
            "excepciones": [],
        }
        out.append({
            "id": f"{_slug(apellido)}-{_slug(nombre_pila)}",
            "nombre_pila": nombre_pila,
            "apellido": apellido,
            "nombre": f"{nombre_pila} {apellido}",
            "genero": _genero(nombre_pila),
            "especialidad": especialidad,
            "cluster": s["cluster"],
            "centro": s["centro"],
            "centros": [bloque],
            "agenda": {"franjas": list(bloque["franjas"]), "excepciones": []},
            "matricula": {
                "numero": str(40000 + i * 173),
                "provincia": "Buenos Aires",
                "estado": "Activo",
                "confianza": "high",
                "fuente": "ficticio",
            },
        })
    return out


# ============================================================ servicios ====
# 70 services. modo distribution mirrors Pinamar's real ratio (43
# solicitud_con_orden / 38 walkin / 34 solicitud / 5 walkin_con_orden out of
# 120, see docs/pinamar) scaled to ~70: 25 solicitud_con_orden / 22 walkin /
# 20 solicitud / 3 walkin_con_orden.
#
# Every tuple: (id, nombre, centro, modo, cluster, categoria, consultorio,
#               palabras, extra) where `extra` is a dict of optional keys:
#               edad, pami, sede, turnos_activos_max, apertura_mismo_dia,
#               horario_texto (walk-in que igual se reúne un día fijo), foto,
#               agenda_plantilla (index into _PLANTILLAS, appointment
#               modes only), descripcion_extra.
_NO_APPOINTMENT_MODES = ("walkin", "walkin_con_orden")

_SERVICIOS_DEF = [
    # -- clínica (9) ----------------------------------------------------
    ("medicina-general-riverside-clinic", "Medicina general", "riverside-clinic", "solicitud",
     "clinica", "especialidades", "Consultorio 1",
     ["médico clínico", "medicina general", "clínica médica"], dict(agenda_plantilla=_con_domingo(0))),
    ("medicina-general-lakeview-clinic", "Medicina general", "lakeview-clinic", "solicitud",
     "clinica", "especialidades", "Consultorio 1",
     ["médico clínico", "medicina general", "clínica médica"], dict(agenda_plantilla=1)),
    ("medicina-general-maternity", "Medicina general", "maternity-center", "solicitud",
     "clinica", "especialidades", "Consultorio 1",
     ["médico clínico", "medicina general", "clínica médica"], dict(agenda_plantilla=2)),
    ("demanda-espontanea-riverside-clinic", "Demanda espontánea", "riverside-clinic", "walkin",
     "clinica", "especialidades", "Consultorio 1",
     ["demanda espontánea", "urgencia", "guardia"], dict()),
    ("demanda-espontanea-lakeview-clinic", "Demanda espontánea", "lakeview-clinic", "walkin",
     "clinica", "especialidades", "Consultorio 1",
     ["demanda espontánea", "urgencia", "guardia"], dict()),
    ("curaciones-riverside-clinic", "Curaciones y control de heridas", "riverside-clinic", "walkin",
     "clinica", "especialidades", "Sala de curaciones",
     ["curaciones", "heridas", "control de herida"], dict()),
    ("adulto-mayor-lakeview-clinic", "Control de salud del adulto mayor", "lakeview-clinic", "solicitud_con_orden",
     "clinica", "especialidades", "Consultorio 2",
     ["geriatría", "adulto mayor", "control geriátrico", "tercera edad"],
     dict(edad=(60, None), pami="Con PAMI, pedile a tu médico de cabecera la orden de derivación.",
          agenda_plantilla=5)),
    ("enfermeria-presion-riverside-clinic", "Enfermería - control de presión arterial", "riverside-clinic", "walkin",
     "clinica", "especialidades", "Enfermería",
     ["presión", "tensión arterial", "enfermería"], dict()),
    ("enfermeria-curaciones-lakeview-clinic", "Consulta de enfermería y curaciones", "lakeview-clinic", "walkin",
     "clinica", "especialidades", "Enfermería",
     ["enfermería", "curaciones", "inyectables"], dict()),

    # -- pediatría (6) ----------------------------------------------------
    ("pediatria-maternity", "Pediatría", "maternity-center", "solicitud",
     "pediatria", "especialidades", "Consultorio 2",
     ["pediatra", "pediatría", "niños", "control de niño sano"],
     dict(edad=(None, 15), agenda_plantilla=_FIN_DE_SEMANA)),
    ("pediatria-riverside-clinic", "Pediatría", "riverside-clinic", "solicitud",
     "pediatria", "especialidades", "Consultorio 2",
     ["pediatra", "pediatría", "niños", "control de niño sano"],
     dict(edad=(None, 15), agenda_plantilla=4)),
    ("pediatria-lakeview-clinic", "Pediatría", "lakeview-clinic", "solicitud",
     "pediatria", "especialidades", "Consultorio 2",
     ["pediatra", "pediatría", "niños", "control de niño sano"],
     dict(edad=(None, 15), agenda_plantilla=6)),
    ("nino-sano-maternity", "Control de niño sano", "maternity-center", "walkin",
     "pediatria", "especialidades", "Consultorio 2",
     ["control de niño sano", "libreta sanitaria", "control pediátrico"],
     dict(edad=(None, 6))),
    ("peso-talla-bebes-maternity", "Control de peso y talla de bebés", "maternity-center", "walkin",
     "pediatria", "especialidades", "Lactario",
     ["control de peso", "control de talla", "bebés", "puericultura"],
     dict(edad=(None, 2))),
    ("guardia-pediatrica-hospital", "Consulta espontánea de guardia pediátrica", "sampleton-hospital", "walkin",
     "pediatria", "especialidades", "Guardia",
     ["guardia pediátrica", "urgencia pediátrica"], dict()),

    # -- cardiología (5) --------------------------------------------------
    ("cardiologia-sampleton-hospital", "Cardiología", "sampleton-hospital", "solicitud",
     "cardiologia", "especialidades", "Consultorio 3",
     ["cardiología", "corazón", "electrocardiograma", "presión alta"],
     dict(pami="Con PAMI, pedile a tu médico de cabecera la orden de derivación.",
          agenda_plantilla=0)),
    ("electrocardiograma-hospital", "Electrocardiograma", "sampleton-hospital", "solicitud_con_orden",
     "cardiologia", "estudios", "Servicio de imágenes",
     ["electrocardiograma", "ecg"],
     dict(agenda_plantilla=([((1, 2, 3, 4, 5), "12:00", "14:00", 15)], "Lunes a viernes de 12 a 14 h"))),
    ("ergometria-hospital", "Ergometría", "sampleton-hospital", "solicitud_con_orden",
     "cardiologia", "estudios", "Servicio de imágenes",
     ["ergometría", "prueba de esfuerzo"],
     dict(agenda_plantilla=([((2, 4), "14:00", "17:00", 30)], "Martes y jueves de 14 a 17 h"))),
    ("marcapasos-hospital", "Control de marcapasos", "sampleton-hospital", "solicitud_con_orden",
     "cardiologia", "especialidades", "Consultorio 3",
     ["marcapasos", "control de marcapasos"],
     dict(agenda_plantilla=([((1, 3, 5), "14:00", "17:00", 20)], "Lunes, miércoles y viernes de 14 a 17 h"))),
    ("ecocardiograma-anexo", "Ecocardiograma", "diagnostic-center", "solicitud_con_orden",
     "cardiologia", "estudios", "Servicio de imágenes",
     ["ecocardiograma", "eco doppler cardíaco"], dict(agenda_plantilla=7)),

    # -- endocrino: nutrición/endocrinología (6) --------------------------
    ("nutricion-riverside-clinic", "Nutrición", "riverside-clinic", "solicitud_con_orden",
     "endocrino", "especialidades", "Consultorio 1",
     ["nutrición", "nutricionista", "dieta", "control de peso"],
     dict(agenda_plantilla=_SABADO)),
    ("nutricion-lakeview-clinic", "Nutrición", "lakeview-clinic", "solicitud_con_orden",
     "endocrino", "especialidades", "Consultorio 1",
     ["nutrición", "nutricionista", "dieta", "control de peso"],
     dict(agenda_plantilla=1)),
    ("nutricion-infantil-maternity", "Nutrición infantil", "maternity-center", "solicitud",
     "endocrino", "especialidades", "Consultorio 1",
     ["nutrición infantil", "nutricionista de niños"],
     dict(edad=(None, 12), agenda_plantilla=3)),
    ("endocrinologia-hospital", "Endocrinología", "sampleton-hospital", "solicitud_con_orden",
     "endocrino", "especialidades", "Consultorio 4",
     ["endocrinología", "diabetes", "tiroides"], dict(agenda_plantilla=4)),
    ("diabetes-lakeview-clinic", "Control de diabetes (glucemia capilar)", "lakeview-clinic", "walkin",
     "endocrino", "especialidades", "Enfermería",
     ["glucemia", "diabetes", "control de azúcar"], dict()),
    ("podologia-lakeview-clinic", "Podología para pie diabético", "lakeview-clinic", "walkin",
     "endocrino", "especialidades", "Consultorio 5",
     ["podología", "pie diabético"], dict()),

    # -- gineco (5) ---------------------------------------------------------
    ("ginecologia-obstetricia-maternity", "Ginecología y Obstetricia", "maternity-center", "solicitud_con_orden",
     "gineco", "especialidades", "Consultorio 3",
     ["ginecología", "obstetricia", "embarazo"], dict(agenda_plantilla=_con_domingo(0))),
    ("control-prenatal-maternity", "Control prenatal", "maternity-center", "solicitud",
     "gineco", "especialidades", "Consultorio 3",
     ["control prenatal", "embarazo", "obstetricia"], dict(agenda_plantilla=1)),
    ("pap-maternity", "Papanicolaou (PAP)", "maternity-center", "solicitud_con_orden",
     "gineco", "estudios", "Consultorio 3",
     ["papanicolaou", "pap", "colposcopía"],
     dict(agenda_plantilla=([((1, 2, 3, 4, 5), "12:00", "14:00", 15)], "Lunes a viernes de 12 a 14 h"))),
    ("planificacion-familiar-riverside-clinic", "Planificación familiar y anticoncepción", "riverside-clinic", "solicitud",
     "gineco", "especialidades", "Consultorio 4",
     ["planificación familiar", "anticoncepción", "métodos anticonceptivos"],
     dict(agenda_plantilla=2)),
    ("ginecologia-riverside-clinic", "Ginecología", "riverside-clinic", "solicitud_con_orden",
     "gineco", "especialidades", "Consultorio 4",
     ["ginecología", "control ginecológico"], dict(agenda_plantilla=4)),

    # -- trauma-kine (4) ------------------------------------------------
    ("traumatologia-hospital", "Traumatología", "sampleton-hospital", "solicitud_con_orden",
     "trauma-kine", "especialidades", "Consultorio 5",
     ["traumatología", "fractura", "esguince"], dict(agenda_plantilla=_con_domingo(0))),
    ("yeso-fracturas-hospital", "Yeso y control de fracturas", "sampleton-hospital", "walkin",
     "trauma-kine", "especialidades", "Yesería",
     ["yeso", "control de fractura"], dict()),
    ("kinesiologia-lakeview-clinic", "Kinesiología y rehabilitación", "lakeview-clinic", "solicitud_con_orden",
     "trauma-kine", "especialidades", "Consultorio 6",
     ["kinesiología", "rehabilitación", "fisioterapia"],
     dict(turnos_activos_max=10, sede="Rehabilitation Annex", agenda_plantilla=1)),
    ("kinesiologia-respiratoria-maternity", "Kinesiología respiratoria pediátrica", "maternity-center",
     "solicitud_con_orden", "trauma-kine", "especialidades", "Consultorio 4",
     ["kinesiología respiratoria", "bronquiolitis", "kinesiología pediátrica"],
     dict(edad=(None, 12), turnos_activos_max=10, agenda_plantilla=5)),

    # -- odontología (5) --------------------------------------------------
    ("odontologia-riverside-clinic", "Odontología general", "riverside-clinic", "solicitud",
     "odontologia", "especialidades", "Consultorio odontológico",
     ["odontología", "dentista", "caries", "limpieza dental"], dict(agenda_plantilla=0)),
    ("odontologia-lakeview-clinic", "Odontología general", "lakeview-clinic", "solicitud",
     "odontologia", "especialidades", "Consultorio odontológico",
     ["odontología", "dentista", "caries", "limpieza dental"], dict(agenda_plantilla=1)),
    ("odontopediatria-maternity", "Odontopediatría", "maternity-center", "solicitud",
     "odontologia", "especialidades", "Consultorio odontológico",
     ["odontopediatría", "dentista de niños"], dict(edad=(None, 12), agenda_plantilla=3)),
    ("urgencias-odontologicas-riverside-clinic", "Urgencias odontológicas", "riverside-clinic", "walkin_con_orden",
     "odontologia", "especialidades", "Consultorio odontológico",
     ["urgencia odontológica", "dolor de muela"], dict(apertura_mismo_dia=True)),
    ("urgencias-odontologicas-lakeview-clinic", "Urgencias odontológicas", "lakeview-clinic", "walkin",
     "odontologia", "especialidades", "Consultorio odontológico",
     ["urgencia odontológica", "dolor de muela"], dict()),

    # -- otorrino-fono (5) --------------------------------------------------
    ("otorrinolaringologia-hospital", "Otorrinolaringología", "sampleton-hospital", "solicitud_con_orden",
     "otorrino-fono", "especialidades", "Consultorio 6",
     ["otorrinolaringología", "oído", "garganta"], dict(agenda_plantilla=_SABADO)),
    ("fonoaudiologia-maternity", "Fonoaudiología", "maternity-center", "solicitud",
     "otorrino-fono", "especialidades", "Consultorio 5",
     ["fonoaudiología", "lenguaje", "audición"], dict(agenda_plantilla=4)),
    ("fonoaudiologia-infantil-riverside-clinic", "Fonoaudiología infantil (lenguaje)", "riverside-clinic", "solicitud",
     "otorrino-fono", "especialidades", "Consultorio 5",
     ["fonoaudiología infantil", "trastorno del lenguaje"],
     dict(edad=(None, 10), agenda_plantilla=6)),
    ("audiometria-anexo", "Audiometría", "diagnostic-center", "walkin",
     "otorrino-fono", "estudios", "Servicio de imágenes",
     ["audiometría", "evaluación auditiva"], dict()),
    ("otoemisiones-maternity", "Otoemisiones acústicas (pesquisa neonatal)", "maternity-center",
     "solicitud", "otorrino-fono", "estudios", "Consultorio 5",
     ["otoemisiones", "pesquisa auditiva neonatal", "oea"],
     dict(edad=(None, 1), agenda_plantilla=7)),

    # -- salud mental (5) -----------------------------------------------
    ("psicologia-riverside-clinic", "Psicología adultos", "riverside-clinic", "solicitud",
     "salud-mental", "especialidades", "Consultorio 7",
     ["psicología", "psicólogo", "salud mental"], dict(agenda_plantilla=0)),
    ("psicologia-lakeview-clinic", "Psicología adultos", "lakeview-clinic", "solicitud",
     "salud-mental", "especialidades", "Consultorio 7",
     ["psicología", "psicólogo", "salud mental"], dict(agenda_plantilla=1)),
    ("psicologia-infantil-maternity", "Psicología infantil", "maternity-center", "solicitud",
     "salud-mental", "especialidades", "Consultorio 6",
     ["psicología infantil", "psicopedagogía"], dict(edad=(None, 15), agenda_plantilla=3)),
    ("psiquiatria-hospital", "Psiquiatría", "sampleton-hospital", "solicitud_con_orden",
     "salud-mental", "especialidades", "Consultorio 7",
     ["psiquiatría", "psiquiatra"], dict(agenda_plantilla=5)),
    ("grupo-adicciones-lakeview-clinic", "Grupo de apoyo para adicciones", "lakeview-clinic", "solicitud",
     "salud-mental", "especialidades", "Sala de usos múltiples",
     ["adicciones", "grupo de apoyo", "consumo problemático"], dict(agenda_plantilla=6)),

    # -- diagnóstico (8) --------------------------------------------------
    ("laboratorio-anexo", "Laboratorio", "diagnostic-center", "walkin_con_orden",
     "diagnostico", "estudios", "Laboratorio central",
     ["análisis de sangre", "laboratorio", "análisis clínicos"],
     dict(apertura_mismo_dia=True)),
    ("radiografia-anexo", "Radiografía", "diagnostic-center", "walkin_con_orden",
     "diagnostico", "estudios", "Servicio de imágenes",
     ["radiografía", "placa", "rayos x", "rx"], dict()),
    ("ecografia-general-anexo", "Ecografía general", "diagnostic-center", "solicitud_con_orden",
     "diagnostico", "estudios", "Servicio de imágenes",
     ["eco", "ecografía", "ultrasonido"], dict(agenda_plantilla=0)),
    ("ecografia-abdominal-anexo", "Ecografía abdominal", "diagnostic-center", "solicitud_con_orden",
     "diagnostico", "estudios", "Servicio de imágenes",
     ["ecografía abdominal", "eco de abdomen"], dict(agenda_plantilla=2)),
    ("ecografia-obstetrica-maternity", "Ecografía obstétrica", "maternity-center", "solicitud_con_orden",
     "diagnostico", "estudios", "Servicio de imágenes",
     ["ecografía obstétrica", "eco del embarazo"], dict(agenda_plantilla=4)),
    ("mamografia-anexo", "Mamografía", "diagnostic-center", "solicitud_con_orden",
     "diagnostico", "estudios", "Servicio de imágenes",
     ["mamografía", "control mamario"],
     dict(pami="Con PAMI, pedile a tu médico de cabecera la orden de derivación.",
          agenda_plantilla=6)),
    ("tomografia-anexo", "Tomografía computada", "diagnostic-center", "solicitud_con_orden",
     "diagnostico", "estudios", "Servicio de imágenes",
     ["tomografía", "tac", "tomografía computada"], dict(agenda_plantilla=3)),
    ("entrega-resultados-anexo", "Entrega de resultados de laboratorio", "diagnostic-center", "walkin",
     "diagnostico", "estudios", "Laboratorio central",
     ["resultados", "retirar resultados", "resultados de laboratorio"], dict()),

    # -- grupos (8) -------------------------------------------------------
    # A workshop is a real appointment on a real day, so each one carries the
    # day and time it meets (`horario_texto`) instead of the generic walk-in
    # sign, and an illustration (`foto`) — the mosaic at /d/grupos is all
    # image. The illustrations are the Pinamar set re-tinted to this
    # installation's palette; see packs/example-town/talleres/FUENTES.md.
    ("taller-gimnasia-lakeview-clinic", "Taller de gimnasia para adultos mayores", "lakeview-clinic", "walkin",
     "grupos", "consultorios", "SUM",
     ["taller de gimnasia", "adultos mayores", "actividad física"],
     dict(sede="Lakeview Community Center", edad=(60, None),
          horario_texto="Miércoles de 13 a 14 h", foto="prevencion-de-caidas.jpg")),
    ("taller-estimulacion-maternity", "Taller de estimulación temprana", "maternity-center", "walkin",
     "grupos", "consultorios", "SUM",
     ["estimulación temprana", "taller para bebés"],
     # no `sede`: the room is already "SUM", and a venue named "Salón de Usos
     # Múltiples" next to it read "Salón de Usos Múltiples, SUM"
     dict(edad=(None, 3),
          horario_texto="Martes de 10 a 11:30 h", foto="estimulacion-temprana.jpg")),
    ("grupo-ansiedad-riverside-clinic", "Grupo de ansiedad", "riverside-clinic", "walkin",
     "grupos", "consultorios", "4",
     ["ansiedad", "grupo terapéutico", "angustia"],
     dict(horario_texto="Lunes de 12 a 14 h", foto="ansiedad.jpg")),
    ("taller-memoria-lakeview-clinic", "Taller de estimulación cognitiva", "lakeview-clinic", "walkin",
     "grupos", "consultorios", "SUM",
     ["memoria", "estimulación cognitiva", "adultos mayores"],
     dict(edad=(60, None), horario_texto="Miércoles de 14 a 15 h",
          foto="estimulacion-cognitiva.jpg")),
    ("cesacion-tabaquica-hospital", "Cesación tabáquica", "sampleton-hospital", "walkin",
     "grupos", "consultorios", "6",
     ["dejar de fumar", "tabaco", "cesación tabáquica"],
     dict(horario_texto="Martes de 12:30 a 13:30 h", foto="cesacion-tabaquica.jpg")),
    ("curso-embarazo-maternity", "Curso de acompañamiento del embarazo", "maternity-center", "walkin",
     "grupos", "consultorios", "SUM",
     ["curso de embarazo", "preparto", "acompañamiento del embarazo"],
     dict(horario_texto="Miércoles de 8 a 10 h", foto="acompanamiento-embarazo.jpg")),
    ("espacio-adolescentes-riverside-clinic", "Espacio para adolescentes", "riverside-clinic", "walkin",
     "grupos", "consultorios", "4",
     ["adolescentes", "espacio joven", "salud adolescente"],
     dict(edad=(13, 18), horario_texto="Martes de 15:30 a 17 h", foto="adolescentes.jpg")),
    ("taller-escritura-lakeview-clinic", "Taller de escritura", "lakeview-clinic", "walkin",
     "grupos", "consultorios", "SUM",
     ["taller de escritura", "escritura", "taller literario"],
     dict(sede="Lakeview Community Center", horario_texto="Viernes de 11 a 13 h",
          foto="taller-escritura.jpg")),

    # -- vacunas y prevención (5) -------------------------------------------
    ("vacunacion-riverside-clinic", "Vacunación", "riverside-clinic", "walkin",
     "vacunas-prevencion", "consultorios", "Vacunatorio",
     ["vacuna", "vacunatorio", "vacunación", "calendario de vacunación"], dict()),
    ("vacunacion-lakeview-clinic", "Vacunación", "lakeview-clinic", "walkin",
     "vacunas-prevencion", "consultorios", "Vacunatorio",
     ["vacuna", "vacunatorio", "vacunación"], dict()),
    ("vacunacion-maternity", "Vacunación", "maternity-center", "walkin",
     "vacunas-prevencion", "consultorios", "Vacunatorio",
     ["vacuna", "vacunatorio", "vacunación de bebés"], dict()),
    ("vacunacion-antigripal-hospital", "Vacunación campaña antigripal", "sampleton-hospital", "walkin",
     "vacunas-prevencion", "consultorios", "Vacunatorio",
     ["vacuna antigripal", "campaña de vacunación", "gripe"],
     dict(apertura_mismo_dia=True)),
    ("calendario-vacunacion-riverside-clinic", "Control de calendario de vacunación", "riverside-clinic", "walkin",
     "vacunas-prevencion", "consultorios", "Vacunatorio",
     ["calendario de vacunación", "libreta sanitaria"], dict()),

    # -- otras (5) ----------------------------------------------------------
    ("dermatologia-hospital", "Dermatología", "sampleton-hospital", "solicitud_con_orden",
     "otras", "especialidades", "Consultorio 8",
     ["dermatología", "piel", "lunares"], dict(agenda_plantilla=_SABADO)),
    ("oftalmologia-hospital", "Oftalmología", "sampleton-hospital", "solicitud_con_orden",
     "otras", "especialidades", "Consultorio 8",
     ["oftalmología", "ojos", "vista"], dict(agenda_plantilla=2)),
    ("agudeza-visual-escolar-hospital", "Control de agudeza visual escolar", "sampleton-hospital", "walkin",
     "otras", "especialidades", "Consultorio 8",
     ["agudeza visual", "control visual escolar"], dict(edad=(None, 18))),
    ("retinografia-anexo", "Retinografía para diabéticos", "diagnostic-center", "solicitud_con_orden",
     "otras", "estudios", "Servicio de imágenes",
     ["retinografía", "fondo de ojo diabético"],
     dict(pami="Con PAMI, pedile a tu médico de cabecera la orden de derivación.",
          agenda_plantilla=7)),
    ("urologia-hospital", "Urología", "sampleton-hospital", "solicitud_con_orden",
     "otras", "especialidades", "Consultorio 8",
     ["urología", "próstata"], dict(agenda_plantilla=5)),

    # -- guardia (1) ----------------------------------------------------------
    # The hospital's adult emergency room, the same entry Pinamar's catalog
    # carries. Without it the demo answered «guardia» only with the
    # pediatric one, and two study participants (2026-10-15) could not find
    # where the hospital's guardia is. Walk-in: never offers appointments.
    # Appended last so every earlier service keeps its `top`.
    ("guardia-sampleton-hospital", "Guardia", "sampleton-hospital", "walkin",
     "clinica", "consultorios", "Guardia",
     ["guardia", "guardia del hospital", "urgencias", "emergencias", "accidente",
      "dolor de pecho", "falta de aire"],
     dict(horario_texto="Las 24 horas, todos los días",
          cartel="No necesitás turno. Guardia las 24 horas, todos los días. "
                 "Se atiende primero a quien está más grave. Ante una emergencia llamá al {{sitio.emergency_number}}.")),
]

_CENTRO_NOMBRE = {c["slug"]: c["name"] for c in CENTROS}

# Opening hours of each centre's walk-in desks, and the few services that
# keep other hours (TASK-58). Written as a sign would, in the form
# hours.mjs reads.
_HORARIO_WALKIN_CENTRO = {
    "sampleton-hospital": "Lunes a viernes de 8 a 18 h",
    "riverside-clinic": "Lunes a viernes de 8 a 16 h",
    "lakeview-clinic": "Lunes a viernes de 8 a 14 h",
    "maternity-center": "Lunes a viernes de 8 a 13 h",
    "diagnostic-center": "Lunes a viernes de 7 a 12 h",
}
_HORARIO_WALKIN = {
    "guardia-pediatrica-hospital": "Las 24 horas, todos los días",
    "laboratorio-anexo": "Lunes a viernes de 7 a 10 h",
    "entrega-resultados-anexo": "Lunes a viernes de 11 a 13 h",
}

_CARTEL_POR_MODO = {
    "walkin": "No necesitás turno. Se atiende por llegada.",
    "walkin_con_orden": "Vení con tu orden médica. Se atiende por llegada.",
    "solicitud": "Pedí tu turno acá y el centro te lo confirma.",
    "solicitud_con_orden": "Necesitás una orden de derivación. Subí la orden acá y el centro te confirma el turno.",
}

_DESCRIPCION_POR_MODO = {
    "walkin": "Se atiende sin turno, por llegada, en {centro}.",
    "walkin_con_orden": "Se atiende sin turno pero con orden médica, por llegada, en {centro}.",
    "solicitud": "Pedí el turno desde la web y {centro} te lo confirma.",
    "solicitud_con_orden": "Subí tu orden de derivación desde la web y {centro} te confirma el turno.",
}


def _build_servicios():
    out = []
    for (sid, nombre, centro, modo, cluster, categoria, consultorio,
         palabras, extra) in _SERVICIOS_DEF:
        centro_nombre = _CENTRO_NOMBRE[centro]
        top = len(out) + 1
        cartel = extra.get("cartel") or _CARTEL_POR_MODO[modo]
        descripcion = _DESCRIPCION_POR_MODO[modo].format(centro=centro_nombre)
        s = {
            "id": sid,
            "top": top,
            "top_label": nombre,
            "nombre": nombre,
            "centro": centro,
            "modo": modo,
            "descripcion": descripcion,
            "cartel": cartel,
            "categoria": categoria,
            "edad": {"min": None, "max": None},
            "pami": extra.get("pami"),
            "cluster": cluster,
            "palabras": list(palabras),
            "consultorio": consultorio,
        }
        edad = extra.get("edad")
        if edad:
            s["edad"] = {"min": edad[0], "max": edad[1]}
        if extra.get("sede"):
            s["sede"] = extra["sede"]
        if extra.get("turnos_activos_max"):
            s["turnos_activos_max"] = extra["turnos_activos_max"]
        if extra.get("apertura_mismo_dia"):
            assert modo in _NO_APPOINTMENT_MODES, (
                f"{sid}: apertura_mismo_dia sólo vale en modos walkin (catalog.py:~775)")
            s["apertura_mismo_dia"] = True
        if "agenda_plantilla" in extra:
            assert modo not in _NO_APPOINTMENT_MODES, (
                f"{sid}: un walkin/walkin_con_orden nunca lleva agenda "
                "(test_invariantes_datos.py, sin excepción para apertura_mismo_dia)")
            plantilla = extra["agenda_plantilla"]
            franjas, horario = (plantilla if isinstance(plantilla, tuple)
                                else _PLANTILLAS[plantilla])
            s["agenda"] = _agenda_de(franjas)
            s["horario"] = horario
        elif extra.get("horario_texto"):
            # A walk-in that still meets on a fixed day (every workshop):
            # the sign is the schedule, not "come whenever". hours.mjs parses
            # it into the weekly grid exactly like an agenda-backed one.
            s["horario"] = extra["horario_texto"]
        else:
            # A walk-in states its hours, not only «come whenever» (TASK-58,
            # 26.10): the page turns them into «Abierto ahora» / «Abre el
            # lunes, 08:00»; with the sign alone a patient at 22:30 on a
            # Sunday could not tell whether the centre was open.
            s["horario"] = _HORARIO_WALKIN.get(sid) or _HORARIO_WALKIN_CENTRO[centro]
        if extra.get("foto"):
            s["foto"] = f'/webdata/talleres/{extra["foto"]}?v=1'
        # Every appointment-mode service must carry an agenda so slots
        # actually render (task requirement) — catch a forgotten one early.
        if modo not in _NO_APPOINTMENT_MODES:
            assert "agenda" in s, f"{sid}: falta agenda_plantilla (modo {modo} ofrece turnos)"
        out.append(s)
    return out


# ================================================== example-localities =====
EXAMPLE_LOCALITIES = ["Sampleton", "Riverside", "Lakeview"]


# ============================================================ escritura ====

# El `icon` sale del id, no de una columna escrita a mano: los ids de arriba ya
# reusan a propósito los de packs/global/clusters.json justamente porque
# comparten los /static/icons/<id>.png, así que repetirlos sería una segunda
# copia de esa relación esperando desincronizarse. Sin este campo el pack venía
# quedándose sin ningún ícono — su "compose: replace" tapa el de packs/global/,
# y las cards de un rubro sin retratos ("Vacunas y prevención", "Grupos y
# talleres") salían literalmente vacías en la demo.
def _con_icono(cl):
    ruta = "/static/icons/%s.png" % cl["id"]
    if not os.path.exists(os.path.join(ROOT, ruta.lstrip("/"))):
        return dict(cl)
    return dict(cl, icon=ruta)


def generate():
    servicios = _build_servicios()
    medicos = _build_medicos(servicios)
    medicos += _medicos_extra(servicios, len(medicos))
    medicos = _agenda_de_sus_servicios(medicos, servicios)

    _write("centros-salud.json", {"centros": CENTROS})

    _write("especialidades.json", {
        "note": ("Catálogo canónico de especialidades médicas -> cluster "
                 "(clusters.json de este mismo pack). Generado por "
                 "scripts/generate_example_town_catalog.py — NO editar a mano."),
        "specialties": [{"nombre": n, "cluster": c} for n, c in ESPECIALIDADES],
    })

    _write("clusters.json", {
        "note": ("Agrupación de servicios y médicos en 'clusters' para el "
                 "directorio (home, /d/<id>, nav). Generado por "
                 "scripts/generate_example_town_catalog.py — NO editar a mano."),
        "clusters": [_con_icono(c) for c in CLUSTERS],
    })

    _write("medicos.json", {
        "nota": ("Médicos ficticios de ejemplo (Sampleton), sólo para "
                 "demostrar el motor sin datos reales de ninguna "
                 "municipalidad. Generado por "
                 "scripts/generate_example_town_catalog.py — NO editar a "
                 "mano, correr el script de nuevo."),
        "nota_agendas": (f"{len(medicos)} médicos ficticios. Cada uno atiende en la agenda "
                          "de los servicios que lo programan (la copia el generador); "
                          "los que ningún servicio con agenda programa conservan una de las "
                          "9 plantillas de _PLANTILLAS."),
        "fuente": "packs/example-town — datos inventados",
        "medicos": medicos,
    })

    _write("servicios.json", {"servicios": servicios})

    _write("example-localities.json", {
        "_comment": ("Fase 2c, 2026-07-30. Ver el mismo archivo en "
                     "the private deployment pack for the rationale. Generated by "
                     "scripts/generate_example_town_catalog.py."),
        "localities": EXAMPLE_LOCALITIES,
    })

    _update_estudios_meta(servicios)
    _write_intake_diagnosticos()

    return servicios, medicos


# ==================================================== intake-diagnosticos ====
# The consultation's diagnosis picker had a catalog for cardiology only, so
# the demo's general-practice consultation said «No hay diagnósticos
# cargados para esta especialidad» (TASK-58, 26.10). The demo pack carries
# the global catalog plus a short one for general practice — a whole-file
# override (intake_catalog reads it through sitedata.path), so a real town
# keeps the global file untouched. Representative codes for a demo, same
# disclaimer as the global file.
_DX_MEDICINA_GENERAL = [
    ("mg-control", "Examen médico general", "Z00.0"),
    ("mg-iras", "Infección aguda de las vías respiratorias superiores", "J06.9"),
    ("mg-hta", "Hipertensión arterial esencial", "I10"),
    ("mg-dbt2", "Diabetes mellitus tipo 2 sin complicaciones", "E11.9"),
    ("mg-dislipemia", "Hiperlipidemia, no especificada", "E78.5"),
    ("mg-dispepsia", "Dispepsia", "K30"),
    ("mg-lumbalgia", "Lumbago, no especificado", "M54.5"),
    ("mg-itu", "Infección de vías urinarias, sitio no especificado", "N39.0"),
    ("mg-cefalea", "Cefalea", "R51"),
]


def _write_intake_diagnosticos():
    base_path = os.path.join(ROOT, "packs", "global", "intake-diagnosticos.json")
    with open(base_path, encoding="utf-8") as fh:
        base = json.load(fh)
    propios = [{"id": i, "label": label, "sistema": "CIE-10", "codigo": codigo,
                "nivel": "general", "especialidades": ["medicina general", "geriatria"],
                "estudios_sugeridos": []}
               for i, label, codigo in _DX_MEDICINA_GENERAL]
    _write("intake-diagnosticos.json", {
        "note": ("Generado por scripts/generate_example_town_catalog.py — NO editar a "
                 "mano. El catálogo global (packs/global/intake-diagnosticos.json) más "
                 "uno corto de medicina general para la demo. " + str(base.get("note") or "")),
        "diagnosticos": list(base.get("diagnosticos") or []) + propios,
    })


# ============================================ estudios-preparacion.json ====
# This script does NOT rewrite the "estudios" content (real prep-instruction
# text, sourced from research — not something to regenerate mechanically).
# It only recomputes `_meta.no_ofrecidos_actualmente`: which of the
# catalog's standard studies this pack's OWN servicios.json doesn't actually
# offer, by keyword-matching each estudio's `match` list against the
# offered services' nombre/palabras text.

def _update_estudios_meta(servicios):
    path = os.path.join(PACK_DIR, "estudios-preparacion.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)

    ofrecido_blob = _norm(" ".join(
        s["nombre"] + " " + " ".join(s.get("palabras") or []) for s in servicios
    ))

    no_ofrecidos = []
    for estudio in doc.get("estudios", []):
        keywords = estudio.get("match") or []
        offered = any(_norm(kw) in ofrecido_blob for kw in keywords if kw)
        if not offered:
            no_ofrecidos.append(estudio["id"])

    doc["_meta"]["no_ofrecidos_actualmente"] = no_ofrecidos
    doc["_meta"]["nota_generador"] = (
        "no_ofrecidos_actualmente recalculado por "
        "scripts/generate_example_town_catalog.py contra servicios.json — "
        "no editar a mano, correr el script de nuevo."
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return no_ofrecidos


if __name__ == "__main__":
    servicios, medicos = generate()
    from collections import Counter
    modos = Counter(s["modo"] for s in servicios)
    print(f"servicios: {len(servicios)} {dict(modos)}")
    print(f"medicos: {len(medicos)}")
    print(f"centros: {len(CENTROS)}")
    print(f"especialidades: {len(ESPECIALIDADES)}")
    print(f"clusters: {len(CLUSTERS)}")
