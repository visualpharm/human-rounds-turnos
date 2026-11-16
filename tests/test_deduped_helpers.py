"""Helpers that replaced identical private copies (TASK-52, fifth pass): each says what its copies said."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1")


class PreparationTextFold(unittest.TestCase):
    """prep_model.normalize (was prep_model._normalize and site_preparation._norm_prep)."""

    def test_ascii_lowercase_single_spaces(self):
        from app.modules.turnos import prep_model
        cases = {
            None: "",
            "": "",
            "  Ecografía   Abdominal ": "ecografia abdominal",
            "ÁÉÍÓÚ ñ ü": "aeiou n u",
            "ﬁnal": "final",
            5: "5",
            "\u0441": "",  # letters of other alphabets are dropped, as before
        }
        for value, want in cases.items():
            self.assertEqual(prep_model.normalize(value), want, repr(value))


class NameFold(unittest.TestCase):
    """site_base._norm_name (site_catalog._norm_txt was an identical copy)."""

    def test_ascii_lowercase_keeps_inner_spacing(self):
        from app.patient_web import site_base
        cases = {None: "", "Cardiología": "cardiologia", "  Niño-Sano ": "  nino-sano ", "A  B": "a  b"}
        for value, want in cases.items():
            self.assertEqual(site_base._norm_name(value), want, repr(value))


class CleanText(unittest.TestCase):
    """text_fold.clean (was `_text` in the EMR bridge, the HSI normalization and the EMR observations)."""

    def test_trimmed_text_or_none(self):
        from app.core import text_fold
        cases = {None: None, "": None, "   ": None, 0: None, False: None, " a b ": "a b", 5: "5", "\n x\t": "x"}
        for value, want in cases.items():
            self.assertEqual(text_fold.clean(value), want, repr(value))


class PlainText(unittest.TestCase):
    """text_fold.plain (was `_fold` in chat_agent and `_plain` in chat_language); the golden of
    test_text_folders_contract records the answers of the old copies on the old tree."""

    def test_lowercase_then_no_accents(self):
        from app.core import text_fold
        cases = {None: "", "": "", 0: "", "Cardiología": "cardiologia", "ÁÉÍ Ñ": "aei n",
                 "  A  b ": "  a  b ", 5: "5"}
        for value, want in cases.items():
            self.assertEqual(text_fold.plain(value), want, repr(value))


class SameNameTokens(unittest.TestCase):
    """text_fold.same_name_tokens (the set rule of ai_referral._name_matches and doctor_appointments._name_matches)."""

    def test_rule(self):
        from app.core import text_fold
        s = lambda text: set(text.split())  # noqa: E731
        yes = [("ana perez", "ana perez"), ("ana", "ana maria perez"), ("ana maria perez", "perez ana"),
               ("perez gomez ana", "ana gomez"), ("juan carlos", "juan carlos lopez")]
        no = [("ana perez", "juan perez"), ("ana", "juan"), ("ana perez", "maria gomez"), ("", "ana"), ("ana", "")]
        for a, b in yes:
            self.assertTrue(text_fold.same_name_tokens(s(a), s(b)), (a, b))
            self.assertTrue(text_fold.same_name_tokens(s(b), s(a)), (b, a))
        for a, b in no:
            self.assertFalse(text_fold.same_name_tokens(s(a), s(b)), (a, b))

    def test_both_callers_keep_their_own_normalisation(self):
        from app.ai import ai_referral
        from app.modules.turnos import doctor_appointments
        self.assertTrue(ai_referral._name_matches("PÉREZ, Ana", "ana perez"))
        self.assertTrue(doctor_appointments._name_matches("Dra. Pérez Ana", "Ana Perez"))
        self.assertFalse(ai_referral._name_matches(None, "ana perez"))
        self.assertFalse(doctor_appointments._name_matches("Juan Perez", "Ana Perez Gomez"))


class LicenseNumber(unittest.TestCase):
    """catalog.license_number (was intake_admin._matricula and doctor_appointments._license_number)."""

    def test_string_dict_or_nothing(self):
        from app.patient_web import catalog
        cases = [({}, None), ({"matricula": None}, None), ({"matricula": "  "}, None), ({"matricula": " MP 12 "}, "MP 12"),
                 ({"matricula": {"numero": "MN 7"}}, "MN 7"), ({"matricula": {"numero": ""}}, None),
                 ({"matricula": {}}, None), ({"matricula": 5}, None)]
        for doctor, want in cases:
            self.assertEqual(catalog.license_number(doctor), want, doctor)


class EnvSize(unittest.TestCase):
    """settings.size_or (the parse of threadpool_size and of the database pool size, core/settings.py)."""

    def test_default_for_unset_blank_or_garbage_and_never_below_one(self):
        from app.core import settings
        for raw, want in ((None, 7), ("", 7), ("  ", 7), ("abc", 7), ("1.5", 7), (" 12 ", 12), ("0", 1), ("-4", 1)):
            self.assertEqual(settings.size_or(raw, 7), want, raw)

    def test_the_two_sizes_read_their_own_variables(self):
        from app.core import concurrency, db
        with _env.patched_env(TURNOS_THREADPOOL_SIZE="9", TURNOS_DB_POOL="3"):
            self.assertEqual((concurrency.threadpool_size(), db._pool_size()), (9, 3))
        with _env.patched_env(TURNOS_THREADPOOL_SIZE="", TURNOS_DB_POOL=""):
            self.assertEqual((concurrency.threadpool_size(), db._pool_size()),
                             (concurrency.THREADPOOL_SIZE_DEFAULT, db.POOL_SIZE_DEFAULT))


class StaffRowHead(unittest.TestCase):
    """booking_requests_router._staff_row: the head of the six staff actions on one request."""

    def _run(self, req_id, **patches):
        import asyncio
        from app.modules.turnos import booking_requests_router as router
        base = {"_terminal_404": lambda: "404", "_db_unavailable_response": lambda request: "503",
                "_json_response": lambda request, payload, status: ("json", status, payload),
                "_require_admin": mock.AsyncMock(return_value={"usuario": "ana"})}
        base.update(patches)
        with mock.patch.multiple(router, **base):
            return asyncio.run(router._staff_row("request", req_id))

    def test_path_shape_is_declined_before_any_session_check(self):
        gate = mock.AsyncMock()
        self.assertEqual(self._run("abc", _require_admin=gate), (None, None, "404"))
        gate.assert_not_called()

    def test_row_found(self):
        from app.modules.turnos import booking_requests as bkr
        with mock.patch.object(bkr, "_staff_row_or_error", return_value=({"id": 7}, None)):
            self.assertEqual(self._run("7"), ({"usuario": "ana"}, {"id": 7}, None))

    def test_refusal_and_database_down_become_the_response(self):
        from app.modules.turnos import booking_requests as bkr
        with mock.patch.object(bkr, "_staff_row_or_error", return_value=(None, (403, {"error": "x"}))):
            self.assertEqual(self._run("7"), (None, None, ("json", 403, {"error": "x"})))
        with mock.patch.object(bkr, "_staff_row_or_error", side_effect=bkr.store.DbUnavailable("down")):
            self.assertEqual(self._run("7"), (None, None, "503"))


class MetaRequest(unittest.TestCase):
    """otp_send._meta_request: the POST both Cloud API senders (free text and template) build."""

    def _request(self, destino, table):
        import json
        from app.core import config
        from app.notifications import otp_send
        with mock.patch.object(config, "get_text", side_effect=lambda name, default="": table.get(name, default)):
            req = otp_send._meta_request(destino, {"type": "text", "text": {"body": "hola"}})
        return req, (json.loads(req.data) if req is not None else None)

    def test_not_configured_or_no_number_sends_nothing(self):
        self.assertIsNone(self._request("5492254111222", {})[0])
        self.assertIsNone(self._request("5492254111222", {"META_WA_TOKEN": "t"})[0])
        full = {"META_WA_TOKEN": "t", "META_WA_PHONE_NUMBER_ID": "99"}
        self.assertIsNone(self._request("sin numero", full)[0])
        self.assertIsNone(self._request(None, full)[0])

    def test_request_shape_and_body_order(self):
        table = {"META_WA_TOKEN": "tok", "META_WA_PHONE_NUMBER_ID": "99", "META_WA_GRAPH_VERSION": "/v30.0"}
        req, body = self._request("+54 9 2254 11-1222", table)
        self.assertEqual(req.full_url, "https://graph.facebook.com/v30.0/99/messages")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(list(body), ["messaging_product", "to", "type", "text"])
        self.assertEqual((body["messaging_product"], body["to"], body["text"]), ("whatsapp", "5492254111222", {"body": "hola"}))


class OverLimit(unittest.TestCase):
    """rate_window.over_limit (the body of accounts._register_throttled and accounts._login_throttled)."""

    def test_counts_only_hits_inside_the_window_and_records_nothing(self):
        import time
        from app.core import rate_window
        now = time.time()
        hits = {"ip": [now - 500, now - 100, now - 5]}
        self.assertFalse(rate_window.over_limit(hits, "ip", 3, 200))   # two inside the window
        self.assertEqual(len(hits["ip"]), 2)                           # the old one is gone
        self.assertTrue(rate_window.over_limit(hits, "ip", 2, 200))
        self.assertEqual(len(hits["ip"]), 2)                           # asking does not add a hit

    def test_unknown_key_is_never_over_and_keeps_the_key_as_given(self):
        from app.core import rate_window
        hits = {}
        self.assertFalse(rate_window.over_limit(hits, None, 1, 60))
        self.assertEqual(hits, {None: []})
        self.assertTrue(rate_window.over_limit({"x": [1e18]}, "x", 1, 60))

    def test_accounts_throttles_use_their_own_dicts(self):
        from app.cuentas import accounts
        accounts._REGISTERED.clear()
        accounts._LOGIN_ATTEMPTS.clear()
        self.addCleanup(accounts._REGISTERED.clear)
        self.addCleanup(accounts._LOGIN_ATTEMPTS.clear)
        import time
        accounts._LOGIN_ATTEMPTS["1.2.3.4"] = [time.time()] * accounts._LOGIN_MAX
        self.assertTrue(accounts._login_throttled("1.2.3.4"))
        self.assertFalse(accounts._register_throttled("1.2.3.4"))
        self.assertFalse(accounts._login_throttled("5.6.7.8"))


class OcrPart(unittest.TestCase):
    """ocr_dni._leer_parte: leer_foto and leer_dorso differ only in prompt, key and extractor."""

    def test_found_missing_and_fake(self):
        from app.cuentas import ocr_dni
        costs = (0.5, "model-x", "svc-y")
        with _env.patched_env(TURNOS_FAKE_AI=""), \
                mock.patch.object(ocr_dni, "_leer", return_value=({"k": 1},) + costs) as leer:
            found = ocr_dni._leer_parte("data", "image/png", lambda: {"fake": True}, "PROMPT", "campo", lambda leido: [leido["k"]])
            self.assertEqual(found, {"ok": True, "campo": [1], "_costo": 0.5, "_modelo": "model-x", "_svc": "svc-y"})
            leer.assert_called_once_with("data", "image/png", "PROMPT")
            missing = ocr_dni._leer_parte("data", "image/png", lambda: {"fake": True}, "PROMPT", "campo", lambda leido: None)
            self.assertEqual(missing, {"ok": False, "_costo": 0.5, "_modelo": "model-x", "_svc": "svc-y"})
        with _env.patched_env(TURNOS_FAKE_AI="1"), mock.patch.object(ocr_dni, "_leer") as leer:
            self.assertEqual(ocr_dni._leer_parte("d", "m", lambda: {"fake": True}, "P", "campo", lambda leido: 1), {"fake": True})
            leer.assert_not_called()

    def test_the_two_readers_pass_their_own_parts(self):
        from app.cuentas import ocr_dni
        with mock.patch.object(ocr_dni, "_leer_parte", return_value="out") as part:
            self.assertEqual(ocr_dni.leer_foto("d", "m"), "out")
            self.assertEqual(ocr_dni.leer_dorso("d", "m"), "out")
        (foto, dorso) = part.call_args_list
        self.assertEqual(foto.args[2:], (ocr_dni._fake_foto, ocr_dni._PROMPT_FOTO, "bbox", ocr_dni._bbox_from))
        self.assertEqual(dorso.args[2:], (ocr_dni._fake_dorso, ocr_dni._PROMPT_DORSO, "domicilio", ocr_dni._domicilio_from))


class PackValue(unittest.TestCase):
    """data_packs.value: the nested entry of a pack file, or None (the loaders keep their own fallbacks)."""

    def test_entries_missing_files_and_bad_json(self):
        import json
        import tempfile
        from app.core import data_packs
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ok.json"), "w", encoding="utf-8") as fh:
                json.dump({"a": {"b": {"c": "deep"}, "list": [1]}, "text": "x", "nada": None}, fh)
            with open(os.path.join(tmp, "bad.json"), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with open(os.path.join(tmp, "array.json"), "w", encoding="utf-8") as fh:
                fh.write("[1, 2]")
            with _env.patched_env(TURNOS_WEBDATA_DIR=tmp):
                self.assertEqual(data_packs.value("ok.json", "a", "b", "c"), "deep")
                self.assertEqual(data_packs.value("ok.json", "a", "list"), [1])
                self.assertEqual(data_packs.value("ok.json", "text"), "x")
                self.assertEqual(data_packs.value("ok.json")["text"], "x")
                self.assertIsNone(data_packs.value("ok.json", "nada"))
                self.assertIsNone(data_packs.value("ok.json", "absent"))
                self.assertIsNone(data_packs.value("missing.json", "a"))         # no file: None
                self.assertIsNone(data_packs.value("bad.json", "a"))             # not JSON: None (the loaders always caught it)
                self.assertEqual(data_packs.value("array.json"), [1, 2])
                # the wrong SHAPE fails loudly, as the hand-written loaders did
                with self.assertRaises(AttributeError):
                    data_packs.value("ok.json", "text", "deeper")                # a string has no entries
                with self.assertRaises(AttributeError):
                    data_packs.value("ok.json", "a", "list", "x")                # nor a list
                with self.assertRaises(AttributeError):
                    data_packs.value("array.json", "a")

    def test_pack_setting_precedence(self):
        import json
        import tempfile
        from app.core import config, data_packs
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "endpoints.json"), "w", encoding="utf-8") as fh:
                json.dump({"svc": {"url": "https://pack.example/", "empty": "", "num": 5}}, fh)
            with _env.patched_env(TURNOS_WEBDATA_DIR=tmp), \
                    mock.patch.object(config, "get", side_effect=lambda key, default=None, legacy=None: {"SET": "https://env.example/"}.get(key, default)):
                self.assertEqual(data_packs.pack_setting("SET", "fallback", "endpoints.json", "svc", "url"), "https://env.example/")
                self.assertEqual(data_packs.pack_setting("UNSET", "fallback", "endpoints.json", "svc", "url"), "https://pack.example/")
                for keys in (("svc", "empty"), ("svc", "num"), ("svc", "absent"), ("nope", "url")):
                    self.assertEqual(data_packs.pack_setting("UNSET", "fallback", "endpoints.json", *keys), "fallback", keys)
                self.assertEqual(data_packs.pack_setting("UNSET", "fallback", "missing.json", "svc", "url"), "fallback")

    def test_currency_and_country_read_their_file_once(self):
        from app.core import data_packs
        from app.core import format as fmt
        from app.patient_web import site_base
        doc = {"name": "Testland", "iso2": "TL", "currency": {"code": "TLD", "symbol": "T", "locale": "tl-TL"}}
        with mock.patch.object(data_packs, "value", return_value=doc) as value:
            self.assertEqual(fmt._load_currency(), {"code": "TLD", "symbol": "T", "locale": "tl-TL", "iso2": "TL"})
            self.assertEqual(value.call_count, 1)
        with mock.patch.object(data_packs, "value", return_value=doc) as value:
            self.assertEqual(site_base._load_country(), {"name": "Testland", "iso2": "TL"})
            self.assertEqual(value.call_count, 1)
        for bad in (None, {}):
            with mock.patch.object(data_packs, "value", return_value=bad):
                self.assertEqual(fmt._load_currency(), fmt._FALLBACK_CURRENCY)
                self.assertEqual(site_base._load_country(), site_base._FALLBACK_COUNTRY)

    def test_a_pack_of_the_wrong_shape_still_stops_the_import(self):
        """Same cases and answers as the hand-written loaders (checked against the previous tree): unreadable or
        missing -> the literal; a list/string where an object is expected -> AttributeError."""
        import json
        import subprocess
        import sys
        import tempfile
        probe = ("import sys; sys.path.insert(0, %r)\n"
                 "import app.core.tz; import app.core.format; import app.patient_web.site_base; import app.connectors.puco.puco; import app.connectors.msd.msd_data, "
                 "app.cuentas.auth_renaper\nprint('imported')" % ROOT)
        cases = {"{not json": "imported", "": "imported", "[1, 2]": "AttributeError", '"x"': "AttributeError"}
        for body, want in cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                for name in ("country.json", "endpoints.json", "msd-institutions.json"):
                    with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                        fh.write(body)
                env = dict(os.environ, TURNOS_WEBDATA_DIR=tmp, TURNOS_FAKE_AI="1")
                out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, cwd=ROOT)
                got = "imported" if out.returncode == 0 else out.stderr.strip().splitlines()[-1].split(":")[0]
                self.assertEqual(got, want, body)

    def test_loaders_keep_their_answers_on_the_reference_pack(self):
        from app.connectors.msd import msd_data
        from app.connectors.puco import puco
        from app.connectors.refeps import refeps
        from app.core import format as fmt
        from app.core import tz
        self.assertTrue(puco.SISA_URL.startswith("https://"))
        self.assertTrue(refeps.URL.startswith("https://"))
        self.assertTrue(msd_data.SLUG_TO_MSD)
        self.assertEqual(fmt.PHONE["code"], "54")
        self.assertEqual((fmt.DNI["length_min"], fmt.DNI["length_max"]), (7, 9))
        self.assertEqual(fmt.CURRENCY["code"], "ARS")
        self.assertEqual(tz.tz_name(), "America/Argentina/Buenos_Aires")


class LegacyUrlTails(unittest.TestCase):
    """legacy_urls._rewrite_with_tail: the old-name API families that rename the closing segment."""

    def test_each_family(self):
        from app.core import legacy_urls
        cases = {
            "/api/solicitudes/7": "/api/requests/7",
            "/api/solicitudes/7/cancelar": "/api/requests/7/cancel",
            "/api/solicitudes/7/lista-espera": "/api/requests/7/waitlist",
            "/api/solicitudes/cancelar": "/api/requests/cancelar",
            "/api/solicitudes/": "/api/requests/",
            "/api/admin/solicitudes/7/aprobar": "/api/admin/requests/7/approve",
            "/api/admin/solicitudes/7/rechazar": "/api/admin/requests/7/reject",
            "/api/admin/solicitudes/7/otra": "/api/admin/requests/7/otra",
            "/api/admin/medicos/m1/turnos": "/api/admin/doctors/m1/appointments",
            "/api/admin/medicos/m1/foto": "/api/admin/doctors/m1/photo",
            "/api/admin/medicos/m1": "/api/admin/doctors/m1",
            "/api/oferta/tok/aceptar": "/api/offer/tok/accept",
            "/api/oferta/tok/rechazar": "/api/offer/tok/reject",
            "/api/oferta/tok": None,
            "/api/oferta/tok/otra": None,
            "/api/otra/7": None,
        }
        for old, want in cases.items():
            self.assertEqual(legacy_urls._new_api_path(old), want, old)


class EmrEntity(unittest.TestCase):
    """emr_observations._entity (the body of schedule() and patient())."""

    def test_projection_and_refusals(self):
        from app.connectors import ProviderUnavailable
        from app.modules.turnos import emr_observations as obs
        self.assertEqual(obs.schedule({"id": " s1 ", "specialty": "Cardio ", "extra": 1}),
                         {"id": "s1", "professional_id": None, "specialty": "Cardio", "professional_name": None})
        self.assertEqual(list(obs.patient({"id": 5, "given_name": "Ana"})),
                         ["id", "document_number", "given_name", "family_name"])
        for fn, label in ((obs.schedule, "schedule"), (obs.patient, "patient")):
            with self.assertRaises(ProviderUnavailable) as ctx:
                fn({"id": "  "})
            self.assertEqual(str(ctx.exception), "EMR %s observation omitted id" % label)
            with self.assertRaises(ProviderUnavailable) as ctx:
                fn([])
            self.assertEqual(str(ctx.exception), "EMR observation %s must be an object" % label)


class EnvironText(unittest.TestCase):
    """config.environ_text (was `_env` in chart_summary and lab_analysis): the process environment only."""

    def test_reads_only_the_process_environment_and_strips(self):
        from app.core import config
        with _env.patched_env(TURNOS_DRY5_PROBE="  on \n"):
            self.assertEqual(config.environ_text("TURNOS_DRY5_PROBE"), "on")
            with mock.patch.object(config, "get", return_value="from-db") as getter:
                self.assertEqual(config.environ_text("TURNOS_DRY5_PROBE"), "on")
                getter.assert_not_called()
        self.assertEqual(config.environ_text("TURNOS_DRY5_PROBE"), "")

    def test_otp_send_reads_through_get_text(self):
        from app.core import config
        from app.notifications import otp_send

        def fake_get(key, default=None, legacy=None):
            return "  tok " if key == "TURNOS_WA_PROVIDER" else default

        self.assertFalse(hasattr(otp_send, "_env"))
        with mock.patch.object(config, "get", side_effect=fake_get):
            self.assertEqual(config.get_text("TURNOS_WA_PROVIDER"), "tok")
            self.assertEqual(config.get_text("OTHER", "dflt"), "dflt")


class IsoInInstallationTimezone(unittest.TestCase):
    """tz.iso (was `_iso` in consult_admin and intake_admin, and the inline `astimezone(_ART).isoformat()` copies)."""

    def test_none_stays_none_and_instants_move_to_the_installation_zone(self):
        import datetime
        from app.core import tz
        self.assertIsNone(tz.iso(None))
        utc = datetime.datetime(2026, 10, 30, 15, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(tz.iso(utc), utc.astimezone(tz.tzinfo()).isoformat())
        # a naive datetime is read in the interpreter's zone first, exactly like `astimezone` always did
        naive = datetime.datetime(2026, 10, 30, 12, 0)
        self.assertEqual(tz.iso(naive), naive.astimezone(tz.tzinfo()).isoformat())


class HtmlEscape(unittest.TestCase):
    """site_base._esc is the one HTML escape of the intake pages and the info pages (their copies were identical)."""

    def test_escapes_quotes_and_none(self):
        from app.patient_web import site_base
        self.assertEqual(site_base._esc(None), "")
        self.assertEqual(site_base._esc(5), "5")
        self.assertEqual(site_base._esc("<a href=\"x\">O'Brien & co</a>"),
                         "&lt;a href=&quot;x&quot;&gt;O&#x27;Brien &amp; co&lt;/a&gt;")

    def test_intake_and_info_pages_use_it(self):
        from app.modules.intake import intake_admin, printable
        from app.patient_web import info_pages, site_base
        for mod in (intake_admin, printable, info_pages):
            self.assertIs(mod.site_base, site_base)
            self.assertFalse(hasattr(mod, "_esc"), mod.__name__)


class StatusErrorBases(unittest.TestCase):
    """The domain errors that repeated an __init__ now only name their base (core/status_error.py)."""

    def test_body_status_errors(self):
        from app.core.status_error import BodyStatusError
        from app.modules.turnos import doctor_appointments
        from app.patient_web import legal
        for cls in (doctor_appointments.DomainError, legal.LegalError):
            self.assertTrue(issubclass(cls, BodyStatusError), cls)
            exc = cls(404, {"error": "x"})
            self.assertEqual((exc.status, exc.body, exc.args), (404, {"error": "x"}, ({"error": "x"},)))

    def test_message_status_error(self):
        from app.admin_panel import doctor_patients
        from app.core.status_error import MessageStatusError
        exc = doctor_patients.DoctorPatientsError(400, "mal")
        self.assertIsInstance(exc, MessageStatusError)
        self.assertEqual((exc.status, str(exc)), (400, "mal"))

    def test_remote_error_keeps_message_and_optional_status(self):
        from app.connectors.hsi import hsi_client
        from app.core import backup_s3
        from app.core.status_error import RemoteError
        for cls in (hsi_client.HsiError, backup_s3.S3Error):
            self.assertTrue(issubclass(cls, RemoteError), cls)
            self.assertEqual((str(cls("boom")), cls("boom").status), ("boom", None))
            self.assertEqual((str(cls("boom", 503)), cls("boom", 503).status), ("boom", 503))
        # the S3 transport stays a RuntimeError, the HSI client does not become one
        self.assertTrue(issubclass(backup_s3.S3Error, RuntimeError))
        self.assertFalse(issubclass(hsi_client.HsiError, RuntimeError))
        self.assertTrue(issubclass(hsi_client.HsiConflict, hsi_client.HsiError))


if __name__ == "__main__":
    unittest.main()
