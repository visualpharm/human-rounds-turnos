"""Every bookable service of the demo pack has someone to see the patient,
and the general-practice consultation has a short diagnosis catalog
(TASK-58, 26.10).

The AI-persona run on the demo met «No hay médicos de Ecografía general
cargados» when approving a request, and «No hay diagnósticos cargados para
esta especialidad» in the general-practice consultation. Both are demo data
(packs/example-town, written by scripts/generate_example_town_catalog.py).

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_demo_catalog_coverage.py
"""
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")


class TestDemoCatalogCoverage(unittest.TestCase):
    def setUp(self):
        from app.patient_web import catalog, site_base
        from app.core import site_identity
        caches = (site_identity._CACHE, catalog._JSON_CACHE, site_base._CACHE)
        self.addCleanup(lambda: [cache.clear() for cache in caches])  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(ROOT, "packs", "example-town"))
        for cache in caches:
            cache.clear()

    def test_every_bookable_service_has_a_doctor_the_desk_can_assign(self):
        from app.patient_web import site_catalog
        from app.modules.turnos import booking_queue
        pool = booking_queue._doctor_pool_by_service()
        sin = [s["id"] for s in site_catalog._services()
               if str(s.get("modo") or "").startswith("solicitud") and not pool.get(s["id"])]
        # The one deliberate gap: a study with no specialist at its centre
        # (tests/e2e/journey-j16 pins what the desk sees then).
        self.assertEqual(sin, ["ecocardiograma-anexo"])

    # A week with no exceptions in the pack: Sunday 8 … Saturday 14 November 2026.
    WEEK = [date(2026, 11, 8) + timedelta(days=n) for n in range(7)]

    def test_the_public_page_shows_the_hours_the_agenda_books(self):
        """One schedule (decision 2026-11-08): what a doctor's page says under
        «Cuándo atiende» is, day by day, what the slot generator, the queue
        and the printed day sheet read (`agenda_print.day_windows`). The
        demo had 51 of 68 doctors disagree — Sánchez «domingo 9–12» on his
        page, Tuesday and Thursday in the agenda."""
        from app.patient_web import catalog, site_availability
        from app.modules.turnos import agenda_print
        distintos = []
        for m in catalog.doctors():
            filas = site_availability._schedule_lines(m)
            for dia in self.WEEK:
                semana = dia.isoweekday() % 7
                # a set: the page has a row per office, so two services booked in the
                # same hours (the demo's dentists: Odontología and the same-day
                # Urgencias odontológicas) are two rows and one window of the sheet
                pagina = sorted({(f["desde"], f["hasta"], f["centro"] or "") for f in filas
                                 if semana in f["dias"]})
                agenda = sorted((v["desde"], v["hasta"], v["centro"] or "")
                                for v in agenda_print.day_windows(m, dia))
                if pagina != agenda:
                    distintos.append((m["id"], dia.isoformat(), pagina, agenda))
        self.assertEqual(distintos, [])

    def test_a_doctor_is_never_in_two_offices_at_the_same_hour(self):
        """One doctor's windows, across services and centres, never overlap on
        a weekday, and each window tagged with a service lies inside that
        service's agenda. The four hospital cardiology services shared their
        cardiologists with agendas 07-10, 08-12 and 09-13 (Camila Levin's page
        listed all three on Monday, 15.11); the cluster's agendas are now
        laid side by side in the generator."""
        from app.patient_web import catalog, site_catalog
        servicios = {s["id"]: s for s in site_catalog._services()}

        def bands(agenda):
            agenda = agenda or {}
            return agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])

        solapes, fuera = [], []
        for m in catalog.doctors():
            ventanas = [(c.get("centro"), f) for c in m.get("centros") or [] for f in c.get("franjas") or []]
            for i, (ca, a) in enumerate(ventanas):
                for cb, b in ventanas[i + 1:]:
                    if set(a["dias"]) & set(b["dias"]) and a["desde"] < b["hasta"] and b["desde"] < a["hasta"]:
                        solapes.append((m["id"], ca, a.get("servicio"), cb, b.get("servicio")))
                svc = servicios.get(a.get("servicio") or "")
                if svc and svc.get("agenda"):
                    dentro = any(set(a["dias"]) <= set(s["dias"]) and s["desde"] <= a["desde"]
                                 and a["hasta"] <= s["hasta"] for s in bands(svc["agenda"]))
                    if not dentro:
                        fuera.append((m["id"], svc["id"], a["desde"], a["hasta"]))
        self.assertEqual(solapes, [])
        self.assertEqual(fuera, [])

    def test_each_seeded_service_names_a_doctor_who_works_in_it(self):
        """The seed books its history on (service, doctor) pairs: a doctor
        outside the service's pool booked it in another service's hours
        (Ecografía general with the tomography doctor, 12:30, 15.11)."""
        from app.modules.turnos import schedule_policy
        from app.patient_web import catalog
        import scripts.seed_demo_all as sd
        medicos = {m["id"]: m for m in catalog.doctors()}
        servicios = {s["id"]: s for s in catalog.services()}
        ajenos = [(svc, mid) for svc, _centro, _nombre, mid in sd._SERVICIOS
                  if not schedule_policy.belongs(medicos[mid], servicios[svc], peers=list(medicos.values()))]
        self.assertEqual(ajenos, [])

    def test_a_center_describes_only_services_it_has(self):
        """A centre's description, «para qué ir» line and jobs name only
        services held at that centre (demo review 15.11: Sampleton Hospital
        promised X-rays and blood tests, which are at the Diagnostic Center).
        A service is named by the first significant word of its name, matched
        on the word's stem («Vacunarte» is Vacunación)."""
        import json
        import unicodedata
        from app.core import sitedata

        def norm(text):
            text = unicodedata.normalize("NFD", str(text or "").lower())
            return "".join(ch if ch.isalpha() else " " for ch in text if unicodedata.category(ch) != "Mn")

        generic = {"control", "consulta", "taller", "grupo", "curso", "entrega", "espacio", "demanda",
                   "urgencias", "enfermeria", "curaciones", "guardia", "medicina", "planificacion",
                   "cesacion", "yeso", "papanicolaou", "otoemisiones", "podologia", "retinografia", "salud"}
        with open(sitedata.path("servicios.json"), encoding="utf-8") as f:
            servicios = json.load(f)["servicios"]
        with open(sitedata.path("centros-salud.json"), encoding="utf-8") as f:
            centros = json.load(f)["centros"]

        def stem(service_name):
            for word in norm(service_name).split():
                if len(word) > 3 and word not in generic:
                    return word[:6]
            return None

        todos = {stem(s["nombre"]) for s in servicios} - {None}
        ajenos = []
        for c in centros:
            propios = {stem(s["nombre"]) for s in servicios if s.get("centro") == c["slug"]}
            texto = " ".join([c.get("description") or "", c.get("jtbd") or ""] + list(c.get("jobs") or []))
            for word in set(norm(texto).split()):
                for st in todos - propios:
                    if word.startswith(st):
                        ajenos.append((c["slug"], word))
        self.assertEqual(sorted(ajenos), [])

    def test_every_day_of_the_week_has_doctors_at_work_in_the_demo(self):
        """The demo is shown on any day: each of the seven has doctors whose
        «Pacientes de hoy» the seed fills, and every hour it fills is inside
        that doctor's agenda (a Sunday had none, 08.11)."""
        from unittest import mock
        from datetime import datetime
        from app.core import tz
        from app.modules.turnos import agenda_print
        import scripts.seed_demo_all as sd
        for dia in self.WEEK:
            with self.subTest(dia=dia.isoformat()), \
                    mock.patch.object(sd, "_now", lambda d=dia: datetime(d.year, d.month, d.day, 6,
                                                                          tzinfo=tz.tzinfo())):
                agendas = sd.AgendaDemo()
                grupos = sd._agenda_de_hoy(agendas)
                self.assertGreaterEqual(len({g[0] for g in grupos}), 4)
                medicos = {m["id"]: m for m in agendas.medicos()}
                for medico_id, _nombre, _svc, _centro, horas in grupos:
                    ventanas = agenda_print.day_windows(medicos[medico_id], dia)
                    fuera = [h for h in horas if not any(v["desde"] <= h < v["hasta"] for v in ventanas)]
                    self.assertEqual(fuera, [], medico_id)

    def test_only_plausible_sunday_services_open_on_a_sunday(self):
        """Demo review 2026-11-15 (D15): dermatology offered «Primer turno:
        Hoy, 11:00» on a Sunday. Sunday morning is kept to what a public
        hospital plausibly runs then; the day queue still has its doctors
        (the test above)."""
        from app.patient_web import site_catalog
        domingo = sorted(
            s["id"] for s in site_catalog._services()
            if any(0 in (f.get("dias") or []) for f in (s.get("agenda") or {}).get("franjas") or []
                   ) or 0 in ((s.get("agenda") or {}).get("dias") or []))
        self.assertEqual(domingo, ["ginecologia-obstetricia-maternity",
                                   "medicina-general-riverside-clinic",
                                   "pediatria-maternity", "traumatologia-hospital"])

    def test_general_practice_has_a_short_diagnosis_catalog(self):
        from app.modules.intake import intake_catalog
        intake_catalog._CACHE.clear()
        dx = intake_catalog.diagnoses("Medicina general")
        self.assertGreaterEqual(len(dx), 5)
        self.assertTrue(all(d.get("codigo") for d in dx))
        # cardiology keeps its own catalog in the demo pack
        self.assertTrue(intake_catalog.diagnoses("Cardiología"))


class TestDemoCenterIntrosPromiseOnlyTheirServices(unittest.TestCase):
    """A center's intro names only what its own Servicios list has, and says
    «derivación» only when its consultations do ask for an order (demo review
    2026-11-15, D30/D31: the hospital promised imaging, blood tests and
    specialists «con derivación» while its Cardiología read «Sin orden»)."""

    # a word of the intro → what the center's services must include
    _NEEDS = {
        "radiograf": ("radiograf",),
        "ecograf": ("ecograf",),
        "imágenes": ("radiograf", "ecograf", "tomograf", "mamograf"),
        "sangre": ("laboratorio",),
        "laboratorio": ("laboratorio",),
        "nutrici": ("nutrici",),
        "kinesiolog": ("kinesiolog",),
        "odontol": ("odontol",),
        "vacun": ("vacun",),
        "pediatr": ("pediatr",),
    }

    def test_every_intro_word_has_its_service(self):
        import json
        pack = os.path.join(ROOT, "packs", "example-town")
        with open(os.path.join(pack, "centros-salud.json"), encoding="utf-8") as fh:
            centros = json.load(fh)["centros"]
        with open(os.path.join(pack, "servicios.json"), encoding="utf-8") as fh:
            servicios = json.load(fh)["servicios"]
        faltas = []
        for c in centros:
            mios = [sv for sv in servicios if sv.get("centro") == c["slug"]]
            nombres = " ".join(str(sv.get("nombre") or "") for sv in mios).lower()
            intro = " ".join([c.get("jtbd") or "", c.get("description") or ""]
                             + list(c.get("jobs") or [])).lower()
            for palabra, hace_falta in self._NEEDS.items():
                if palabra in intro and not any(h in nombres for h in hace_falta):
                    faltas.append((c["slug"], palabra))
            if "derivación" in intro:
                sin_orden = [sv["nombre"] for sv in mios if sv.get("modo") == "solicitud"]
                if sin_orden:
                    faltas.append((c["slug"], "derivación", sin_orden[:3]))
        self.assertEqual(faltas, [])
