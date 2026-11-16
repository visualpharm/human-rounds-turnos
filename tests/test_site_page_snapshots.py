"""test_site_page_snapshots.py — the center, doctor and service pages do not change when their functions are cut (TASK-72).

`site_page_centers._page_center`, `site_page_doctors._page_doctor` and `site_page_service._service_info_request`
(reached through `_page_service`) were each over 150 lines. Cutting a render function into named pieces must not move one
byte of what a patient receives, and none of the existing tests says so: they look for a string in the page, not
for the page. This file pins the page itself.

What is rendered: every center, every professional and every request-mode service of the data pack the test checkout
runs on (`example-town`) plus a set of added rows built to reach each branch of the three functions (a center with
photo, gallery, quotes and tips; a center with nothing but a phone; a walk-in doctor; a demo doctor; a doctor with two
venues; a service whose first turno is far away; ...), in Spanish and in English. The server renders one page for
every device — there is no user-agent branch in `patient_web/` — so the mobile and the desktop layout are the same
bytes, told apart by CSS only.

`tests/golden/site_pages.json` holds, per page, the length and the sha256 of the markup with the content-hashed asset URLs
masked (`tests/snapshots.py:skeleton`), recorded on the code BEFORE the cut. It is never edited by hand and a red
comparison is never "fixed" by recording it again: read what moved first. `--dump DIR` writes the pages as the
server sends them (asset URLs unmasked), so two trees can be compared with `diff -r`.

  python3 tests/test_site_page_snapshots.py --write       # record (only on code already known to be right)
  python3 tests/test_site_page_snapshots.py --dump DIR    # the rendered pages, raw bytes, one file per case

When the data of the example pack changes the golden no longer describes it and the comparison is SKIPPED, as in
`test_doctor_pool_golden.py`.

The pages still read the database on their way (the catalog overlay, holidays, the theme and branding settings, the
config overrides, cached translations), so the render runs on its OWN empty database (`_dbtest.PerModuleDb`), the
state the golden was recorded on: never on the shared DATABASE_URL, whose rows would move the bytes and whose Alembic
revision, when it is not this checkout's, made the first connection wait on `upgrade head` (the product owner's run, 2026-11-07).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_site_page_snapshots.py"
"""
import contextlib
import copy
import datetime
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import _dbtest  # noqa: E402
import snapshots  # noqa: E402  (tests/snapshots.py: skeleton() and page_entry())
from app.core import clock as core_clock  # noqa: E402
from app.core import i18n  # noqa: E402
from app.modules.turnos import schedule_policy, slot_holds  # noqa: E402
from app.patient_web import (  # noqa: E402
    avatar_thumb,
    catalog,
    doctor_photos,
    site_availability,
    site_base,
    site_catalog,
    site_centers,
    site_doctors,
    site_page_centers,
    site_page_doctors,
    site_page_service,
)

GOLDEN = os.path.join(ROOT, "tests", "golden", "site_pages.json")
_db = _dbtest.PerModuleDb("sana_turnos_test_site_pages")


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


# A Tuesday morning, as in test_doctor_pool_golden.py: every window below is Monday to Friday.
NOW = datetime.datetime(2026, 10, 13, 8)
LANGS = ("es", "en")
_ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
_FRANJA = {"dias": _ALL_DAYS, "desde": "08:00", "hasta": "12:00", "cada_min": 30}


def _franjas(servicio=None, **extra):
    return [dict(_FRANJA, **({"servicio": servicio} if servicio else {}), **extra)]


# ------------------------------------------------------------------- the added rows
# Centers: one that has everything, one with services and no doctors, one with doctors and no services, and four that
# take no appointments and differ in how they can be reached.
_CENTERS = [
    {"slug": "sx-completo", "name": "Centro Completo", "address": "Calle Uno 100.", "phone": "(011) 4000-1111",
     "whatsapp": "+54 9 11 5555-1234", "email": "completo@example.org", "description": "Descripcion del centro.",
     "jtbd": "Para atenderte en el dia.", "horario": "Lunes a viernes de 8 a 18", "notas": "Entrada por la calle lateral.",
     "jobs": ["Vacunatorio Prueba", "Servicio Repetido", "Hacer algo sin pagina"], "bookable": True},
    {"slug": "sx-servicios", "name": "Centro Solo Servicios", "address": "Calle Dos 200", "phone": "(011) 4000-2222",
     "description": "Solo servicios, sin medicos.", "bookable": True},
    {"slug": "sx-medicos", "name": "Centro Solo Medicos", "address": "Calle Tres 300", "whatsapp": "+54 9 11 5555-9999",
     "email": "medicos@example.org", "jtbd": "Ver a un profesional.", "jobs": "no es una lista", "bookable": True},
    {"slug": "sx-wa-mail", "name": "Oficina WhatsApp y Mail", "whatsapp": "+54 9 11 5555-0000",
     "email": "oficina@example.org", "bookable": False},
    {"slug": "sx-tel-mail", "name": "Oficina Telefono y Mail", "phone": "(011) 4000-3333",
     "email": "tel@example.org", "bookable": False},
    {"slug": "sx-wa-roto", "name": "Oficina WhatsApp Roto", "whatsapp": "sin numero", "phone": "(011) 4000-4444",
     "bookable": False},
    {"slug": "sx-solo-mail", "name": "Oficina Solo Mail", "email": "solo@example.org", "bookable": False},
    {"slug": "sx-vacio", "name": "Oficina Sin Datos", "bookable": False},
    {"slug": "sx-ostende", "name": "Sede Ostende", "address": "Calle Cinco 500", "description": "Segunda sede.",
     "bookable": True},
]
_INFO = {
    "sx-completo": {"photo": "/webdata/centro-photos/sx-completo.webp?v=2",
                    "facts": ["Estacionamiento gratuito", "Rampa de acceso"], "more": ["Cerrado los feriados", ""]},
    "sx-servicios": {"facts": ["Atencion sin turno previo"]},
}
_GALLERY = {
    "sx-completo": [{"src": "/webdata/centro-photos/sx-completo-1.webp", "alt": "Entrada principal"},
                    {"src": "/webdata/centro-photos/sx-completo-2.webp"}, {"alt": "sin imagen"},
                    {"src": "/webdata/centro-photos/sx-completo-3.webp"}],
    "sx-servicios": [{"src": "/webdata/centro-photos/sx-servicios-1.webp"}],
    "sx-medicos": [{"src": "/webdata/centro-photos/sx-medicos-1.webp"}, {"src": "/webdata/centro-photos/sx-medicos-2.webp"}],
}
_REVIEWS = {
    "sx-completo": {"quotes": [{"text": "Muy buena atencion."}, {"text": ""}, {"text": "Rapidos y amables."}],
                    "tips": ["Llegar temprano", "", "Llevar el DNI"]},
    "sx-servicios": {"quotes": [{"text": "Me atendieron enseguida."}]},
    "sx-medicos": {"tips": ["Pedir turno con tiempo"]},
}

# Services. `cluster` names an example-town cluster, or a made-up one (no cluster page).
_SERVICES = [
    {"id": "sx-vacunatorio", "nombre": "Vacunatorio Prueba", "centro": "sx-completo", "cluster": "clinica",
     "modo": "walkin", "horario": "Lunes a viernes de 8 a 12"},
    {"id": "sx-repetido-1", "nombre": "Servicio Repetido", "centro": "sx-completo", "cluster": "clinica",
     "modo": "solicitud", "agenda": {"franjas": _franjas()}},
    {"id": "sx-repetido-2", "nombre": "Servicio Repetido", "centro": "sx-completo", "cluster": "clinica",
     "modo": "solicitud", "sede": "Anexo", "edad": {"min": 18, "max": 65}},
    {"id": "sx-turno", "nombre": "Consulta con Turno", "centro": "sx-completo", "cluster": "clinica", "modo": "solicitud",
     "agenda": {"franjas": _franjas()}, "descripcion": "Consulta de prueba.", "cartel": "Traer el DNI y la credencial.",
     "pami": "PAMI cubre esta consulta.", "consultorio": "3", "horario": "Lunes a viernes de 8 a 12"},
    {"id": "sx-orden", "nombre": "Estudio con Orden", "centro": "sx-completo", "cluster": "diagnostico",
     "modo": "solicitud_con_orden", "agenda": {"franjas": _franjas()}, "cartel": "Necesitas una orden medica.",
     "consultorio": "Sala de estudios", "edad": {"max": 12}, "horario": "Con turno, según agenda",
     "announcement": {"title": "Aviso de prueba", "body": "El servicio cierra por obras."}},
    {"id": "sx-lejano", "nombre": "Consulta Lejana", "centro": "sx-servicios", "cluster": "clinica", "modo": "solicitud",
     "dias_anticipacion": 1, "agenda": {"franjas": [{"dias": [4], "desde": "09:00", "hasta": "10:00", "cada_min": 30}]},
     "edad": {"min": 3}},
    {"id": "sx-sin-agenda", "nombre": "Consulta Sin Agenda", "centro": "sx-servicios", "cluster": "clinica",
     "modo": "solicitud_con_orden", "sede": "Planta alta"},
    {"id": "sx-sin-centro", "nombre": "Consulta Sin Centro", "centro": "no-existe", "cluster": "otro-cluster",
     "modo": "solicitud"},
    {"id": "sx-doble-centro", "nombre": "Consulta Doble", "centro": "sx-completo", "cluster": "clinica",
     "modo": "solicitud", "agenda": {"franjas": _franjas()}},
    {"id": "sx-doble-ostende", "nombre": "Consulta Doble", "centro": "sx-ostende", "cluster": "clinica",
     "modo": "solicitud", "agenda": {"franjas": _franjas()}},
]


def _doctor(did, nombre, **fields):
    row = {"id": did, "nombre": nombre, "centro": "sx-completo", "cluster": "clinica", "especialidad": "Cardiologia"}
    row.update(fields)
    return row


_DOCTORS = [
    # a turno in a service that takes requests
    _doctor("sx-ana", "Ana Prueba", centros=[{"centro": "sx-completo", "franjas": _franjas("sx-turno")}]),
    # the same with an order, a second specialty and a male name
    _doctor("sx-juan", "Juan Prueba", especialidad="Clinica medica", especialidad_2="Hematologia",
            centros=[{"centro": "sx-completo", "franjas": _franjas("sx-orden")}]),
    # a service with a schedule too far away: the band says when it opens
    _doctor("sx-lejos", "Rosa Lejana", centro="sx-servicios", cluster="clinica",
            centros=[{"centro": "sx-servicios", "franjas": [
                {"dias": [4], "desde": "09:00", "hasta": "10:00", "cada_min": 30, "servicio": "sx-lejano"}]}]),
    # a service without any schedule: the center confirms the date
    _doctor("sx-sin-fecha", "Lia Prueba", centro="sx-servicios", especialidad="Consulta Sin Agenda"),
    # no service at all, at a center / at no center / at a center the data does not know
    _doctor("sx-suelto", "Mario Prueba", cluster="sin-cluster", especialidad="Arqueologia",
            centro="sx-medicos"),
    _doctor("sx-sin-centro", "Xi Prueba", cluster="sin-cluster", especialidad="", centro=""),
    _doctor("sx-centro-roto", "Eva Prueba", cluster="sin-cluster", especialidad="Cirugia", centro="no-existe"),
    # walk-in: with a schedule table and without it
    _doctor("sx-guardia", "Dante Prueba", cluster="clinica", especialidad="Guardia",
            centros=[{"centro": "sx-completo", "franjas": _franjas("sx-vacunatorio")}]),
    _doctor("sx-guardia-llana", "Pia Prueba", cluster="sin-cluster", especialidad="Vacunacion", centro="sx-completo",
            agenda={"dias": [1], "desde": "08:00", "hasta": "12:00", "cada_min": 30, "servicio": "sx-vacunatorio"}),
    # a demo doctor, with an invented schedule
    _doctor("sx-demo", "Demo Prueba", demo=True, cluster="sin-cluster", especialidad="Demostracion",
            agenda_demo={"dias": _ALL_DAYS, "desde": "08:00", "hasta": "12:00", "cada_min": 30}),
    # several things at once, and two venues
    _doctor("sx-varios", "Beatriz Prueba", centros=[
        {"centro": "sx-completo", "franjas": _franjas("sx-turno") + _franjas("sx-vacunatorio")
         + _franjas("sx-orden")},
        {"centro": "sx-ostende", "franjas": _franjas("sx-doble-ostende")}]),
    # a doctor with several tagged services, none of them walk-in
    _doctor("sx-dos-servicios", "Hector Prueba", centros=[
        {"centro": "sx-completo", "franjas": _franjas("sx-turno") + _franjas("sx-repetido-1")}]),
    # a doctor of the cluster that the service page infers, with and without a photo
    _doctor("sx-inferido", "Nora Prueba", centro="sx-completo", cluster="clinica", especialidad="Consulta con Turno",
            centros=[{"centro": "sx-completo", "franjas": _franjas()}]),
    _doctor("sx-foto", "Luis Foto", centros=[{"centro": "sx-completo", "franjas": _franjas("sx-turno")}]),
    _doctor("sx-foto-sin-miniatura", "Olga Foto", centros=[{"centro": "sx-completo", "franjas": _franjas("sx-turno")}]),
]
_PHOTO_STYLE = {"sx-foto": "background-position:10% 20%", "sx-foto-sin-miniatura": "background-position:30% 40%"}
_PHOTO_THUMB = {"sx-foto": "/avatar-thumb/sx-foto.webp"}
_PHOTO_URL = {"sx-foto": "/doctor-photo/sx-foto?v=1"}


# ------------------------------------------------------------------- the world
def _extend(name, doc):
    """`doc` of pack file `name` plus the added rows."""
    doc = copy.deepcopy(doc)
    if name == "centros-salud.json":
        doc.setdefault("centros", []).extend(copy.deepcopy(_CENTERS))
    elif name == "servicios.json":
        doc.setdefault("servicios", []).extend(copy.deepcopy(_SERVICES))
    elif name == "medicos.json":
        doc.setdefault("medicos", []).extend(copy.deepcopy(_DOCTORS))
    elif name == "centros-info.json":
        doc.update(copy.deepcopy(_INFO))
    elif name == "centros-galeria.json":
        doc.update(copy.deepcopy(_GALLERY))
    elif name == "centros-resenas.json":
        doc.update(copy.deepcopy(_REVIEWS))
    return doc


_FILES = ("centros-salud.json", "servicios.json", "medicos.json", "centros-info.json", "centros-galeria.json",
          "centros-resenas.json")
# everything else of the pack the three pages read (identity, clusters, the information pages, announcements): part of
# what the golden was recorded from.
_PACK_FILES = _FILES + ("sitio.json", "clusters.json", "especialidades.json", "enlaces.json",
                        "estudios-preparacion.json", "announcements.json", "example-localities.json",
                        "known-cities.json")


def _photo_url(mid, ent="doctors"):
    """No upload on disk decides a page: only the portraits named above exist."""
    return _PHOTO_URL.get(mid) if ent == "doctors" else None


@contextlib.contextmanager
def _world():
    """The pack as the pages read it plus the added rows, deterministic: the clock fixed, no database overlay, no
    external slot source, no held slot, the portraits decided by the tables above."""
    real_load = site_base._load
    extended = {}

    def load(name, center=None):
        doc = real_load(name, center)
        if name not in _FILES:
            return doc
        # one object per file for the whole render: the services memo re-reads everything for a new one
        if name not in extended:
            extended[name] = _extend(name, doc if doc is not None else {})
        return extended[name]

    patches = [
        patch.object(site_base, "_load", load),
        patch.object(site_base, "_with_overlay", lambda tipo, base: base),
        patch.object(site_base, "_now_art", return_value=NOW),
        patch.object(site_availability, "_slot_source_info", return_value={}),
        patch.object(site_availability, "_place_of", return_value={}),
        patch.object(slot_holds, "taken", return_value=set()),
        patch.object(site_doctors, "_photo_style", lambda mid, chico=False: _PHOTO_STYLE.get(mid, "")),
        patch.object(doctor_photos, "photo_url", _photo_url),
        patch.object(avatar_thumb, "url", lambda mid: _PHOTO_THUMB.get(mid, "")),
        patch.object(site_availability, "_OPENING_MEMO", {}),
        patch.object(core_clock, "_source", lambda: NOW.replace(tzinfo=site_base._ART).astimezone(datetime.timezone.utc)),
    ]
    for p in patches:
        p.start()
    site_catalog._SERVICES_MEMO.clear()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()
        site_catalog._SERVICES_MEMO.clear()


def _case_ids():
    """[(kind, id)] — every center, doctor and request-mode service the pages can be asked for, plus ids that name
    nothing (the page answers None)."""
    centers = [c["slug"] for c in site_centers._centers()]
    doctors = [m["id"] for m in site_catalog._doctors()]
    services = [s["id"] for s in site_catalog._services() if s.get("modo") in ("solicitud", "solicitud_con_orden")]
    cases = ([("center", slug) for slug in centers + ["no-such-center"]]
             + [("doctor", mid) for mid in doctors + ["no-such-doctor"]]
             + [("service", sid) for sid in services + ["no-such-service"]])
    return cases


_RENDER = {"center": site_page_centers._page_center, "doctor": site_page_doctors._page_doctor,
           "service": site_page_service._page_service}


def render_all():
    """({case key: page bytes or None}, the sha256 of the pack the pages were built from)."""
    pages, inputs = {}, {}
    with _world():
        for name in _PACK_FILES:
            inputs[name] = site_base._load(name)
        for lang in LANGS:
            _, token = i18n.bind_lang(lang)
            try:
                for kind, ident in _case_ids():
                    pages["%s/%s/%s" % (lang, kind, ident)] = _RENDER[kind](ident)
            finally:
                i18n.unbind(token)
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return pages, digest


def _entry(body):
    return None if body is None else snapshots.page_entry(body)


def _record():
    pages, digest = render_all()
    return {"pack_inputs_sha256": digest, "pages": {k: _entry(v) for k, v in pages.items()}}


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


def _dump(directory):
    os.makedirs(directory, exist_ok=True)
    pages, _ = render_all()
    for key, body in pages.items():
        name = key.replace("/", "__") + ".html"
        with open(os.path.join(directory, name), "wb") as fh:
            fh.write(body if body is not None else b"None")


class SitePageSnapshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _db.have_db:
            raise unittest.SkipTest(_db.skip_reason or "no database of its own")
        cls.pages, cls.digest = render_all()

    def test_every_page_is_what_it_was_before_the_functions_were_cut(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            want = json.load(fh)
        if self.digest != want["pack_inputs_sha256"]:
            self.skipTest("the example-town data changed since the golden was recorded")
        self.assertGreater(len(want["pages"]), 200)
        got = {k: _entry(v) for k, v in self.pages.items()}
        self.assertEqual(sorted(got), sorted(want["pages"]), "the set of pages changed")
        for key in sorted(want["pages"]):
            self.assertEqual(got[key], want["pages"][key],
                             "%s changed (markup, copy or the set of linked assets)" % key)

    def test_a_page_is_the_same_on_every_render(self):
        """The golden means nothing if a page changes with the wall clock or with the order of the calls."""
        again, _ = render_all()
        self.assertEqual({k: _entry(v) for k, v in again.items()}, {k: _entry(v) for k, v in self.pages.items()})

    def test_unknown_ids_have_no_page(self):
        for lang in LANGS:
            for kind, ident in (("center", "no-such-center"), ("doctor", "no-such-doctor"),
                                ("service", "no-such-service")):
                self.assertIsNone(self.pages["%s/%s/%s" % (lang, kind, ident)])


if __name__ == "__main__":
    if "--write" in sys.argv or "--dump" in sys.argv:
        setUpModule()
        if not _db.have_db:
            sys.exit(_db.skip_reason or "no database of its own")
        try:
            if "--write" in sys.argv:
                _write()
            else:
                _dump(sys.argv[sys.argv.index("--dump") + 1])
        finally:
            tearDownModule()
    else:
        unittest.main()
