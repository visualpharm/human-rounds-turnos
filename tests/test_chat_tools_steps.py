"""test_chat_tools_steps.py - pins what `chat_tools.enlaces_para` returns, step by step.

`enlaces_para` is cut into named steps (links from the tool results, services
the answer names, centres, sections, the navigation contract, the limit).
These tests drive the public function only and assert the links a caller gets,
in order, plus the language each label is resolved in. They passed against the
single long function before the cut and must keep passing after it.

The catalogue is a recorder (centres, specialties, the installation identity,
the section labels), so nothing here depends on the shipped pack. No Postgres.

Correr:  python3 -m unittest discover -s tests -p "test_chat_tools_steps.py"
"""

import contextlib
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.modules.chat import chat_tools  # noqa: E402

# Non-Latin text is spelled with escapes.
RU_BOOK = "\u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f"
RU_VISIT = "\u041f\u0440\u0438\u0451\u043c \u0432"
RU_HOSPITAL = "\u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430"

HOME = {"label": "Servicios y turnos", "href": "/"}


def link(label, href):
    return {"label": label, "href": href}


class LinksCase(unittest.TestCase):
    def links(self, texto, respuesta, tools=None, *, centers=(), localized=None,
              specialties=(), identity=None, sections=(), labels="spanish",
              center_href=None, **kwargs):
        """One real `enlaces_para` with the catalogue replaced by recorders."""
        self.label_calls = []
        self.centros_calls = []
        self.sections_calls = []
        identity = identity or {}

        def label(clave, castellano, lang=None):
            self.label_calls.append((clave, lang))
            return f"{clave}@{lang}" if labels == "key" else castellano

        def centros(lang=None):
            self.centros_calls.append(lang)
            if isinstance(localized, BaseException):
                raise localized
            return dict(localized or {})

        def secciones(lang=None):
            self.sections_calls.append(lang)
            return list(sections)

        def centers_rows():
            if isinstance(centers, BaseException):
                raise centers
            return list(centers)

        with contextlib.ExitStack() as st:
            st.enter_context(patch("app.patient_web.catalog.centers", side_effect=centers_rows))
            st.enter_context(patch("app.patient_web.site_centers.center_href",
                                   side_effect=center_href or (lambda slug: f"/c/{slug}")))
            st.enter_context(patch("app.core.site_identity.get",
                                   side_effect=lambda key, default=None: identity.get(key, default)))
            st.enter_context(patch.object(
                chat_tools, "_etiquetas_de_especialidad", return_value=list(specialties)))
            st.enter_context(patch.object(chat_tools, "_centros_nombre", side_effect=centros))
            st.enter_context(patch.object(chat_tools, "_secciones_localizadas", side_effect=secciones))
            st.enter_context(patch.object(chat_tools, "_etiqueta_seccion", side_effect=label))
            return chat_tools.enlaces_para(texto, respuesta, tools, **kwargs)


class TestLinksFromToolResults(LinksCase):
    def test_nothing_usable_in_the_results_gives_the_home_link(self):
        out = self.links("hola", "Hola", [("search_doctors", "x"), ("whatever", {"a": 1}),
                                           ("search_services", None)])
        self.assertEqual(out, [HOME])
        self.assertEqual(self.links("hola", "Hola"), [HOME])

    def test_doctor_results_link_the_directory_the_doctor_and_the_centre(self):
        result = {"directorio_href": "/dir", "resultados": [
            {"nombre": "Rivera", "centro": "Hospital", "href": "/doctor/rivera",
             "centro_href": "/center/h"},
            {"nombre": "Gomez", "centro": "CAPS", "href": "/doctor/gomez",
             "centro_href": "/center/c"},
            "not a row"]}
        out = self.links("quien atiende", "Atiende Rivera.", [("search_doctors", result)])
        self.assertEqual(out, [link("Médicos y profesionales", "/dir"),
                               link("Rivera — Hospital", "/doctor/rivera"),
                               link("Hospital", "/center/h")])

    def test_doctor_results_without_a_mention_link_every_row(self):
        result = {"resultados": [
            {"nombre": "Rivera", "href": "/doctor/rivera"},
            {"nombre": "Gomez", "centro": "CAPS", "href": "/doctor/gomez"},
            {"centro": "Sin nombre", "href": "/doctor/x"}]}
        out = self.links("quien atiende", "Hay varios.", [("search_doctors", result)])
        self.assertEqual(out, [link("Médicos y profesionales", "/doctors"),
                               link("Rivera", "/doctor/rivera"),
                               link("Gomez — CAPS", "/doctor/gomez")])

    def test_service_rows_link_the_centre_to_the_service_and_the_named_service(self):
        result = {"resultados": [
            {"nombre": "Cardiología", "centro": "Hospital", "href": "/s/card-h",
             "centro_href": "/center/h"},
            {"nombre": "Pediatría", "href": "/s/ped"}]}
        out = self.links("turnos", "Cardiología en el Hospital y Pediatría.",
                         [("search_services", result)])
        self.assertEqual(out, [link("Hospital", "/s/card-h"), link("Pediatría", "/s/ped"),
                               link("Cardiología", "/s/card-h")])

    def test_only_the_rows_the_answer_names_are_linked(self):
        result = {"resultados": [
            {"nombre": "Cardiología", "href": "/s/card"},
            {"nombre": "Pediatría", "href": "/s/ped"}]}
        out = self.links("turnos", "Te sirve Pediatría.", [("search_services", result)])
        self.assertEqual(out, [link("Pediatría", "/s/ped")])

    def test_entry_point_options_are_linked_with_service_and_centre(self):
        result = {"resultados": [], "puerta_de_entrada": {
            "turnos": [{"nombre": "Medicina general", "centro": "CAPS Centro", "href": "/s/mg"},
                       {"nombre": "Clínica", "href": "/s/cl"}, "x", {"nombre": "Sin href"}],
            "sin_turno": [{"nombre": "Guardia", "centro": "Hospital", "href": "/s/g"}, None]}}
        out = self.links("tengo tos", "Empezá por Medicina general.", [("search_services", result)])
        self.assertEqual(out, [link("Medicina general — CAPS Centro", "/s/mg"),
                               link("Clínica", "/s/cl"), link("Guardia — Hospital", "/s/g")])

    def test_entry_point_options_are_only_read_from_a_service_search(self):
        door = {"puerta_de_entrada": {"turnos": [{"nombre": "Medicina general", "href": "/s/mg"}]}}
        for name in ("search_doctors", "available_appointments", "whoami"):
            with self.subTest(name):
                self.assertEqual(self.links("x", "y", [(name, door)]),
                                 self.links("x", "y", [(name, {})]))

    def test_available_appointments_result_is_its_own_row(self):
        result = {"nombre": "Cardio", "centro": "Hosp", "href": "/s/x", "centro_href": "/center/y"}
        out = self.links("turnos", "Hay turnos.", [("available_appointments", result)])
        self.assertEqual(out, [link("Cardio — Hosp", "/s/x"), link("Hosp", "/center/y")])

    def test_a_proposal_links_its_confirmation_only_for_booking_tools(self):
        proposal = {"confirmation_required": True, "confirmation_label": "Confirmar turno",
                    "href": "/confirm/1"}
        for name in ("request_appointment", "cancel_request"):
            with self.subTest(name):
                self.assertEqual(self.links("x", "y", [(name, proposal)]),
                                 [link("Confirmar turno", "/confirm/1")])
        self.assertEqual(self.links("x", "y", [("my_requests", proposal)]),
                         [link("Mis turnos", "/my-requests")])
        done = dict(proposal, ok=True)
        self.assertEqual(self.links("x", "y", [("request_appointment", done)]),
                         [link("Confirmar turno", "/confirm/1"), link("Mis turnos", "/my-requests")])

    def test_account_tools_link_their_screens(self):
        cases = [
            ("request_appointment", {"ok": True}, [link("Mis turnos", "/my-requests")]),
            ("request_appointment", {"ok": False}, [HOME]),
            ("my_requests", {}, [link("Mis turnos", "/my-requests")]),
            ("my_lab_results", {}, [link("Laboratorio", "/account#labs")]),
            ("whoami", {}, [link("Mi cuenta", "/account")]),
        ]
        for name, result, expected in cases:
            with self.subTest(name, result=result):
                self.assertEqual(self.links("x", "y", [(name, result)]), expected)

    def test_links_are_deduped_by_label_and_must_be_app_paths(self):
        result = {"resultados": [
            {"nombre": "Cardiología", "centro": "Hospital", "href": "/s/a"},
            {"nombre": "Pediatría", "centro": " hospital ", "href": "/s/b"},
            {"nombre": "Clínica", "centro": "Otro", "href": "https://elsewhere.example/x"},
            {"nombre": "Nutrición", "centro": "", "href": ""}]}
        out = self.links("turnos", "x", [("search_services", result)])
        self.assertEqual(out, [link("Hospital", "/s/a")])
        # the same word through two tools keeps the first destination
        out = self.links("x", "y", [("my_requests", {}), ("request_appointment", {"ok": True})])
        self.assertEqual(out, [link("Mis turnos", "/my-requests")])

    def test_results_are_read_in_order_across_tools(self):
        out = self.links("x", "y", [("whoami", {}), ("my_lab_results", {}), ("my_requests", {})])
        self.assertEqual(out, [link("Mi cuenta", "/account"), link("Laboratorio", "/account#labs"),
                               link("Mis turnos", "/my-requests")])


class TestNamedServices(LinksCase):
    def test_a_unique_service_name_links_once_beside_its_centre(self):
        result = {"resultados": [
            {"nombre": "Cardiología", "centro": "Hospital", "href": "/s/card"},
            {"nombre": "Cardiología", "centro": "Hospital 2", "href": "/s/card"}]}
        out = self.links("x", "Cardiología te espera", [("search_services", result)])
        self.assertEqual(out, [link("Hospital", "/s/card"), link("Hospital 2", "/s/card"),
                               link("Cardiología", "/s/card")])

    def test_a_name_offered_at_several_places_does_not_pick_one(self):
        result = {"resultados": [
            {"nombre": "Medicina general", "centro": "CAPS A", "href": "/s/a"},
            {"nombre": "Medicina General", "centro": "CAPS B", "href": "/s/b"}]}
        out = self.links("x", "Medicina general en CAPS A y CAPS B", [("search_services", result)])
        self.assertEqual(out, [link("CAPS A", "/s/a"), link("CAPS B", "/s/b")])

    def test_only_search_services_names_count_and_only_app_paths(self):
        rows = {"resultados": [{"nombre": "Cardiología", "centro": "Hospital", "href": "/s/c"}]}
        out = self.links("x", "Cardiología", [("search_doctors", rows)])
        self.assertEqual(out, [link("Médicos y profesionales", "/doctors"),
                               link("Cardiología — Hospital", "/s/c")])
        result = {"resultados": [{"nombre": "Cardiología", "centro": "Hospital", "href": "http://x"}]}
        self.assertEqual(self.links("x", "Cardiología", [("search_services", result)]), [HOME])

    def test_a_service_the_answer_does_not_name_is_not_linked_by_name(self):
        result = {"resultados": [{"nombre": "Cardiología", "centro": "Hospital", "href": "/s/c"},
                                 {"nombre": "Pediatría", "centro": "CAPS", "href": "/s/p"}]}
        out = self.links("x", "Te sirve Cardiología.", [("search_services", result)])
        self.assertEqual(out, [link("Hospital", "/s/c"), link("Cardiología", "/s/c")])

    def test_specialty_pages_follow_the_named_services(self):
        result = {"resultados": [{"nombre": "Pediatría", "centro": "CAPS", "href": "/s/p"}]}
        out = self.links(
            "x", "Pediatría y también Cardiología", [("search_services", result)],
            specialties=[("Pediatría", "/d/pediatria"), ("Cardiología", "/d/cardio"),
                         ("Neurología", "/d/neuro")])
        self.assertEqual(out, [link("CAPS", "/s/p"), link("Pediatría", "/s/p"),
                               link("Cardiología", "/d/cardio")])

    def test_specialty_pages_come_after_the_tool_links_and_before_the_centres(self):
        out = self.links(
            "Hospital de Pinamar", "Cardiología en el Hospital de Pinamar", [("whoami", {})],
            specialties=[("Cardiología", "/d/cardio")],
            centers=[{"name": "Hospital de Pinamar", "slug": "hospital"}])
        self.assertEqual(out, [link("Mi cuenta", "/account"), link("Cardiología", "/d/cardio"),
                               link("Hospital de Pinamar", "/c/hospital")])


class TestCentres(LinksCase):
    CENTERS = [{"name": "Hospital de Pinamar", "slug": "hospital"},
               {"name": "CAPS Ostende", "slug": "ostende"},
               {"name": "Sin slug"}, {"slug": "sin-nombre"}]

    def test_a_centre_named_in_the_question_or_the_answer_is_linked(self):
        out = self.links("horarios del hospital de pinamar", "Abre temprano.", centers=self.CENTERS)
        self.assertEqual(out, [link("Hospital de Pinamar", "/c/hospital")])
        out = self.links("horarios", "Andá a CAPS Ostende.", centers=self.CENTERS)
        self.assertEqual(out, [link("CAPS Ostende", "/c/ostende")])
        out = self.links("sin slug y sin-nombre", "x", centers=self.CENTERS)
        self.assertEqual(out, [HOME])

    def test_the_centre_is_also_linked_by_its_localized_spelling(self):
        out = self.links(RU_BOOK, RU_VISIT + " " + RU_HOSPITAL + ".", centers=self.CENTERS,
                         localized={"hospital": RU_HOSPITAL, "ostende": ""}, lang="ru")
        self.assertEqual(out, [link(RU_HOSPITAL, "/c/hospital")])
        self.assertEqual(self.centros_calls, ["ru"])
        out = self.links("x", "Hospital de Pinamar (" + RU_HOSPITAL + ")", centers=self.CENTERS,
                         localized={"hospital": RU_HOSPITAL}, lang="ru")
        self.assertEqual(out, [link("Hospital de Pinamar", "/c/hospital"),
                               link(RU_HOSPITAL, "/c/hospital")])

    def test_the_emergency_department_links_the_installations_hospital(self):
        identity = {"hospital": "Sampleton", "main_center": "samp-main"}
        for texto, respuesta in (("Necesito la guardia", "Andá."), ("x", "Llamá a emergencias.")):
            with self.subTest(texto=texto):
                self.assertEqual(self.links(texto, respuesta, identity=identity),
                                 [link("Sampleton", "/center/samp-main")])
        self.assertEqual(self.links("x", "Andá al hospital.", identity=identity), [HOME])
        self.assertEqual(self.links("guardia", "x", identity={"hospital": "Sampleton"}), [HOME])
        self.assertEqual(self.links("guardia", "x", identity={"main_center": "samp"}), [HOME])
        self.assertEqual(self.links("guardia", "x", identity={"hospital": " ", "main_center": "m"}),
                         [HOME])

    def test_the_guardia_link_follows_the_centre_links(self):
        out = self.links("guardia del hospital de pinamar", "x", centers=self.CENTERS,
                         identity={"hospital": "Sampleton", "main_center": "m"})
        self.assertEqual(out, [link("Hospital de Pinamar", "/c/hospital"),
                               link("Sampleton", "/center/m")])

    def test_a_catalogue_failure_keeps_what_was_found_and_continues(self):
        base = [("whoami", {})]
        out = self.links("cancelar", "x", base, centers=RuntimeError("catalog down"))
        self.assertEqual(out, [link("Mi cuenta", "/account"), link("Mis turnos", "/my-requests")])
        out = self.links("cancelar", "x", base, centers=self.CENTERS, localized=RuntimeError("x"))
        self.assertEqual(out, [link("Mi cuenta", "/account"), link("Mis turnos", "/my-requests")])
        out = self.links("hospital de pinamar caps ostende", "x", centers=self.CENTERS,
                         center_href=lambda slug: (_ for _ in ()).throw(RuntimeError("boom"))
                         if slug == "ostende" else f"/c/{slug}")
        self.assertEqual(out, [link("Hospital de Pinamar", "/c/hospital")])


class TestSections(LinksCase):
    SECTIONS = [("Mis solicitudes", "/my-requests"), ("Mis turnos", "/my-requests"),
                ("Mi cuenta", "/account"), ("Ingresar", "/account")]

    def test_a_named_section_is_linked_once_per_destination(self):
        out = self.links("x", "Mirá en mis turnos o en Mis solicitudes, o ingresar.",
                         sections=self.SECTIONS, lang="en")
        self.assertEqual(out, [link("Mis solicitudes", "/my-requests"), link("Ingresar", "/account")])
        self.assertEqual(self.sections_calls, ["en"])

    def test_a_section_label_already_linked_does_not_claim_the_destination(self):
        out = self.links("x", "Mis turnos y Mis solicitudes", [("my_requests", {})],
                         sections=[("Mis turnos", "/my-requests"), ("Mis solicitudes", "/my-requests")])
        self.assertEqual(out, [link("Mis turnos", "/my-requests"),
                               link("Mis solicitudes", "/my-requests")])

    def test_sections_match_without_regard_to_case_accents_or_alphabet(self):
        out = self.links("x", "\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u041c\u041e\u0418 \u0417\u0410\u042f\u0412\u041a\u0418 \u0438 Médicos",
                         sections=[("\u041c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438", "/my-requests"), ("Medicos", "/doctors")])
        self.assertEqual(out, [link("\u041c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438", "/my-requests"), link("Medicos", "/doctors")])

    def test_a_non_latin_section_label_needs_to_be_in_the_answer(self):
        # The unnamed labels come first: links are deduped by the ASCII-folded
        # label, which is "" for every non-Latin one, so only the first of them fits.
        out = self.links("x", "\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u043c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438",
                         sections=[("\u041c\u043e\u0439 \u0430\u043a\u043a\u0430\u0443\u043d\u0442", "/account"), ("\u30de\u30a4\u30da\u30fc\u30b8", "/account"),
                                   ("\u041c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438", "/my-requests")])
        self.assertEqual(out, [link("\u041c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438", "/my-requests")])

    def test_a_section_already_linked_by_name_is_not_added_again_by_the_contract(self):
        sections = [("Doctors", "/doctors"), ("Upload order", "/upload-order"),
                    ("Centers", "/centers"), ("Sign in", "/account"), ("My requests", "/my-requests")]
        out = self.links(
            "subir la foto de mi orden, ingresar, cancelar",
            "Doctors, Upload order, Centers, Sign in, My requests. Mirá el directorio de médicos, "
            "los centros de salud y Subí tu orden.", sections=sections)
        self.assertEqual(out, [link(label, href) for label, href in sections])

    def test_sections_come_after_centres_and_before_the_contract(self):
        out = self.links("cancelar en el hospital de pinamar", "Mirá Mi cuenta. Subí tu orden.",
                         centers=[{"name": "Hospital de Pinamar", "slug": "hospital"}],
                         sections=[("Mi cuenta", "/account")])
        self.assertEqual(out, [link("Hospital de Pinamar", "/c/hospital"),
                               link("Mi cuenta", "/account"), link("Subí tu orden", "/upload-order"),
                               link("Mis turnos", "/my-requests")])


class TestNavigationContract(LinksCase):
    def test_answer_terms_link_the_site_sections(self):
        cases = [
            ("directorio de médicos", link("Médicos y profesionales", "/doctors")),
            ("lista de profesionales", link("Médicos y profesionales", "/doctors")),
            ("Médicos y profesionales", link("Médicos y profesionales", "/doctors")),
            ("Subí tu orden", link("Subí tu orden", "/upload-order")),
            ("centros de salud", link("Centros de salud", "/centers")),
            ("lista de centros", link("Centros de salud", "/centers")),
            ("estudios y diagnóstico", link("Estudios", "/d/diagnostico")),
            ("lista de estudios", link("Estudios", "/d/diagnostico")),
            ("talleres y grupos", link("Talleres", "/d/grupos")),
            ("lista de talleres", link("Talleres", "/d/grupos")),
        ]
        for term, expected in cases:
            with self.subTest(term):
                self.assertEqual(self.links("hola", f"Mirá la {term} del sitio."), [expected])
                # the question alone never triggers an answer term (an upload
                # verb plus "orden" in the question is its own rule, below)
                if term != "Subí tu orden":
                    self.assertEqual(self.links(f"la {term}", "Mirá el sitio."), [HOME])

    def test_question_terms_link_the_site_sections(self):
        cases = [
            ("quiero subir una foto de mi orden", link("Subí tu orden", "/upload-order")),
            ("la orden en pdf", link("Subí tu orden", "/upload-order")),
            ("mi orden, un archivo", link("Subí tu orden", "/upload-order")),
            ("adjunto la orden", link("Subí tu orden", "/upload-order")),
            ("sube la orden", link("Subí tu orden", "/upload-order")),
            ("me olvidé la contraseña", link("Ingresar", "/account")),
            ("quiero iniciar sesión", link("Ingresar", "/account")),
            ("no puedo ingresar", link("Ingresar", "/account")),
            ("mi cuenta", link("Ingresar", "/account")),
            ("quiero cancelar", link("Mis turnos", "/my-requests")),
            ("mis turnos", link("Mis turnos", "/my-requests")),
            ("mis solicitudes", link("Mis turnos", "/my-requests")),
            ("cambiar la fecha", link("Mis turnos", "/my-requests")),
            ("reprogramar", link("Mis turnos", "/my-requests")),
        ]
        for term, expected in cases:
            with self.subTest(term):
                self.assertEqual(self.links(term, "Listo."), [expected])
                # the same words in the answer are not a question
                self.assertEqual(self.links("hola", f"Mirá {term}."), [HOME])

    def test_an_order_alone_or_an_upload_verb_alone_is_not_enough(self):
        self.assertEqual(self.links("tengo una orden", "Listo."), [HOME])
        self.assertEqual(self.links("mandame la foto", "Listo."), [HOME])

    def test_several_terms_give_links_in_contract_order(self):
        out = self.links("subir la foto de mi orden y cancelar, no puedo ingresar",
                         "Mirá la lista de talleres y la lista de estudios y la lista de centros "
                         "y el directorio de médicos y Subí tu orden.")
        self.assertEqual(out, [link("Médicos y profesionales", "/doctors"),
                               link("Subí tu orden", "/upload-order"),
                               link("Centros de salud", "/centers"), link("Estudios", "/d/diagnostico"),
                               link("Talleres", "/d/grupos"), link("Ingresar", "/account"),
                               link("Mis turnos", "/my-requests")])


class TestLimitAndLanguage(LinksCase):
    def many(self, n):
        return [("search_services", {"resultados": [
            {"nombre": f"S{i}", "centro": f"C{i}", "href": f"/s/{i}"} for i in range(n)]})]

    def test_default_limit_is_twelve(self):
        out = self.links("x", "y", self.many(15))
        self.assertEqual([item["label"] for item in out], [f"C{i}" for i in range(12)])

    def test_the_limit_is_read_leniently(self):
        cases = [(3, 3), ("4", 4), (None, 12), (0, 12), ("abc", 12), ("", 12), (-3, 1), (1, 1),
                 (50, 15)]
        for limite, expected in cases:
            with self.subTest(limite=limite):
                self.assertEqual(len(self.links("x", "y", self.many(15), limite=limite)), expected)

    def test_the_home_link_is_the_fallback_even_with_a_limit(self):
        self.assertEqual(self.links("x", "y", limite=0), [HOME])
        self.assertEqual(self.links("x", "y", limite=-5), [HOME])
        self.assertEqual(self.links("x", "y", self.many(1)), [link("C0", "/s/0")])

    def test_every_label_is_resolved_in_the_conversation_language(self):
        proposal = {"confirmation_required": True, "confirmation_label": "Confirmar", "href": "/c"}
        scenarios = [
            ([("search_doctors", {})], ["page.doctors.title"]),
            ([("request_appointment", {"ok": True})], ["nav.my_appointments"]),
            ([("my_requests", {})], ["nav.my_appointments"]),
            ([("my_lab_results", {})], ["account.mine.lab.default_study"]),
            ([("whoami", {})], ["nav.my_account"]),
            ([("request_appointment", proposal)], []),
            ([], ["chat.link_home"]),
        ]
        for tools, keys in scenarios:
            with self.subTest(tools=tools):
                out = self.links("hola", "Hola", tools, labels="key", lang="pt")
                self.assertEqual([c for c in self.label_calls], [(k, "pt") for k in keys])
                self.assertEqual([item["label"] for item in out][:len(keys)],
                                 [f"{k}@pt" for k in keys])

    def test_contract_labels_are_resolved_in_the_conversation_language(self):
        out = self.links(
            "subir la foto de mi orden, ingresar, cancelar",
            "directorio de médicos, centros de salud, estudios y diagnóstico, talleres y grupos",
            labels="key", lang="de")
        self.assertEqual([item["label"] for item in out], [
            "page.doctors.title@de", "home.centers.heading@de", "nav.studies@de", "nav.groups@de",
            "nav.upload_order@de", "nav.sign_in@de", "nav.my_appointments@de"])
        self.assertEqual({lang for _, lang in self.label_calls}, {"de"})
        self.assertEqual(self.sections_calls, ["de"])
        self.assertEqual(self.centros_calls, ["de"])


if __name__ == "__main__":
    unittest.main()
