"""test_webturno_client.py — connectors/webturno/webturno.py without the network.

The live Webturno API (app_w_v70_2.php, app_pag33.php, logine.php) is replaced at the one place the module
touches the network, `urllib.request.urlopen`, by a queue of canned answers (bytes, or an exception to raise).
The clock (`time.sleep`, `time.strftime`) and the credentials (`core.config`) are replaced too, so nothing
here reaches the internet, the database or the developer's own settings, and no test waits.

What is pinned: the transport (query string, the retry-then-fail contract, the ",," repair, the three kinds of
unreadable answer), each endpoint's reading of an answer (including empty ones), the availability build (a
dead clinic or a dead doctor never aborts it), the password sync and the CLI.

Run:  python3 -m unittest discover -s tests -p test_webturno_client.py
"""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.connectors.webturno import webturno as wt_mod  # noqa: E402
from app.connectors.webturno.webturno import Webturno, WebturnoError  # noqa: E402

TOKEN = "TOKEN-A"


class _Resp:
    """What `urlopen` returns: a context manager with `read()`."""

    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class _Net:
    """A scripted `urlopen`: each call takes the next item; an Exception is raised, anything else is the body."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)

    @property
    def urls(self):
        return [r.full_url for r in self.requests]


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True))


@contextlib.contextmanager
def _net(*script):
    net = _Net(*script)
    with mock.patch.object(wt_mod.urllib.request, "urlopen", net), \
            mock.patch.object(wt_mod.time, "sleep") as sleep:
        net.sleep = sleep
        yield net


def _client(dni="27888999", password=None, timeout=20):
    with mock.patch.object(wt_mod, "_load_env", return_value={}):
        return Webturno(dni=dni, password=password, timeout=timeout)


# ---- public directory (no login) ---------------------------------------------------------------------------

class TestDirectory(unittest.TestCase):
    def test_dir_get_reads_the_public_directory_json(self):
        with _net(json.dumps({"clinicas": []})) as net:
            self.assertEqual(wt_mod._dir_get("dame_clin_inic"), {"clinicas": []})
        self.assertEqual(net.urls, ["https://webturno.com.ar/app_pag33.php?pf=dame_clin_inic"])
        self.assertEqual(net.timeouts, [20])
        self.assertEqual(net.requests[0].get_header("User-agent"), wt_mod.UA)

    def test_booking_token_comes_from_the_login_redirect_page(self):
        html = '<a href="/login/WkJ6OWpB+/Q==">entrar</a> <a href="/login/OTRO">otro</a>'
        with _net(html) as net:
            self.assertEqual(wt_mod._booking_token("77"), "WkJ6OWpB+/Q==")
        self.assertEqual(net.urls, ["https://webturno.com.ar/logine.php?c=77"])

    def test_booking_token_is_none_when_the_page_has_no_login_link(self):
        for html in ("", "<html>clinica cerrada</html>", "/login/"):
            with _net(html):
                self.assertIsNone(wt_mod._booking_token("77"), html)

    def test_booking_token_decodes_invalid_bytes_instead_of_failing(self):
        with _net(b"\xff\xfe /login/ABC123 \xff"):
            self.assertEqual(wt_mod._booking_token("1"), "ABC123")

    def test_directory_down_is_an_empty_list_with_a_note_on_stderr(self):
        err = io.StringIO()
        with mock.patch.object(wt_mod, "_dir_get", side_effect=OSError("sin red")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(wt_mod.directory_clinics(), [])
        self.assertIn("directorio público no disponible", err.getvalue())

    def test_directory_down_is_silent_when_not_verbose(self):
        err = io.StringIO()
        with mock.patch.object(wt_mod, "_dir_get", side_effect=OSError("sin red")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(wt_mod.directory_clinics(verbose=False), [])
        self.assertEqual(err.getvalue(), "")

    def test_directory_with_no_clinics_key_or_an_empty_one_is_empty(self):
        for data in ({}, {"clinicas": []}):
            with mock.patch.object(wt_mod, "_dir_get", return_value=data):
                self.assertEqual(wt_mod.directory_clinics(), [])

    def test_directory_lists_every_clinic_when_no_zone_is_given(self):
        data = {"clinicas": [
            {"idc": "1", "title": "  Pinamed ", "direccion": " Av. Bunge 1 ", "localidades": "1-11"},
            {"idc": "2", "title": "Mendoza", "direccion": None, "localidades": "99"},
            {"idc": "3", "title": None, "localidades": None},
        ]}
        tokens = {"1": "T1", "2": "T2", "3": "T3"}
        with mock.patch.object(wt_mod, "_dir_get", return_value=data), \
                mock.patch.object(wt_mod, "_booking_token", side_effect=lambda idc: tokens[idc]):
            out = wt_mod.directory_clinics()
        self.assertEqual(out, [
            {"token": "T1", "nombre": "Pinamed", "idc": "1", "direccion": "Av. Bunge 1"},
            {"token": "T2", "nombre": "Mendoza", "idc": "2", "direccion": ""},
            {"token": "T3", "nombre": "", "idc": "3", "direccion": ""},
        ])

    def test_directory_keeps_only_the_clinics_of_the_zone(self):
        data = {"clinicas": [
            {"idc": "1", "title": "Pinamed", "localidades": "1-11"},
            {"idc": "2", "title": "Mendoza", "localidades": "99"},
            {"idc": "3", "title": "Sin localidad", "localidades": ""},
            {"idc": "4", "title": "Gesell", "localidades": "-7-"},
        ]}
        with mock.patch.object(wt_mod, "_dir_get", return_value=data), \
                mock.patch.object(wt_mod, "_booking_token", side_effect=lambda idc: "T" + idc):
            out = wt_mod.directory_clinics(wt_mod.LOCALIDADES_PINAMAR)
        self.assertEqual([c["nombre"] for c in out], ["Pinamed", "Gesell"])

    def test_a_clinic_without_a_booking_token_is_skipped_and_named_on_stderr(self):
        data = {"clinicas": [
            {"idc": "1", "title": "Sin token", "localidades": "1"},
            {"idc": "2", "title": "Falla", "localidades": "1"},
            {"idc": "3", "title": "Buena", "localidades": "1"},
        ]}

        def token(idc):
            if idc == "2":
                raise OSError("timeout")
            return None if idc == "1" else "T3"

        err = io.StringIO()
        with mock.patch.object(wt_mod, "_dir_get", return_value=data), \
                mock.patch.object(wt_mod, "_booking_token", side_effect=token), \
                contextlib.redirect_stderr(err):
            out = wt_mod.directory_clinics()
        self.assertEqual([c["nombre"] for c in out], ["Buena"])
        self.assertIn("sin token de booking: Sin token (idc=1)", err.getvalue())
        self.assertIn("sin token de booking: Falla (idc=2)", err.getvalue())

    def test_a_clinic_without_a_token_is_skipped_silently_when_not_verbose(self):
        data = {"clinicas": [{"idc": "1", "title": "Sin token", "localidades": "1"}]}
        err = io.StringIO()
        with mock.patch.object(wt_mod, "_dir_get", return_value=data), \
                mock.patch.object(wt_mod, "_booking_token", return_value=None), \
                contextlib.redirect_stderr(err):
            self.assertEqual(wt_mod.directory_clinics(verbose=False), [])
        self.assertEqual(err.getvalue(), "")


# ---- credentials ----------------------------------------------------------------------------------------------

class TestCredentials(unittest.TestCase):
    def test_load_env_reads_only_the_two_webturno_keys_and_drops_the_empty_ones(self):
        seen = []
        values = {"WEBTURNO_DNI": "27888999", "WEBTURNO_PASS": ""}

        def get(key):
            seen.append(key)
            return values.get(key)

        with mock.patch.object(wt_mod.config, "get", side_effect=get):
            env = wt_mod._load_env()
        self.assertEqual(env, {"WEBTURNO_DNI": "27888999"})
        self.assertEqual(seen, ["WEBTURNO_DNI", "WEBTURNO_PASS"])

    def test_the_password_defaults_to_the_last_three_digits_of_the_dni(self):
        self.assertEqual(_client("27888999").password, "999")

    def test_an_explicit_password_wins_and_values_are_stripped(self):
        c = _client(" 27888999 ", " clave ")
        self.assertEqual((c.dni, c.password), ("27888999", "clave"))

    def test_credentials_fall_back_to_the_runtime_config(self):
        with mock.patch.object(wt_mod, "_load_env",
                               return_value={"WEBTURNO_DNI": "11222333", "WEBTURNO_PASS": "secreta"}):
            c = Webturno()
        self.assertEqual((c.dni, c.password, c.timeout), ("11222333", "secreta", 20))

    def test_the_dni_may_be_given_as_a_number(self):
        c = _client(27888999)
        self.assertEqual((c.dni, c.password), ("27888999", "999"))

    def test_no_dni_anywhere_is_an_error(self):
        with mock.patch.object(wt_mod, "_load_env", return_value={}):
            with self.assertRaises(WebturnoError) as ctx:
                Webturno()
        self.assertIn("WEBTURNO_DNI", str(ctx.exception))
        with mock.patch.object(wt_mod, "_load_env", return_value={}):
            with self.assertRaises(WebturnoError):
                Webturno(dni="   ")


# ---- transport ----------------------------------------------------------------------------------------------

class TestGet(unittest.TestCase):
    def test_the_query_string_carries_pf_and_the_params_without_the_none_ones(self):
        with _net('{"ok": 1}') as net:
            out = _client()._get("dame_turn", op="l", med=None, p2="a b/ñ", tus="P")
        self.assertEqual(out, {"ok": 1})
        self.assertTrue(net.urls[0].startswith(wt_mod.BASE + "?"))
        self.assertEqual(_query(net.urls[0]), {"pf": "dame_turn", "op": "l", "p2": "a b/ñ", "tus": "P"})
        self.assertEqual(net.requests[0].get_header("User-agent"), wt_mod.UA)

    def test_the_timeout_of_the_client_is_the_one_used(self):
        with _net("{}") as net:
            _client(timeout=7)._get("dame_tipo")
        self.assertEqual(net.timeouts, [7])

    def test_a_doubled_comma_in_the_answer_is_repaired(self):
        with _net('{"turnos": [{"idtur": 1},,{"idtur": 2}]}'):
            out = _client()._get("dame_turn")
        self.assertEqual([t["idtur"] for t in out["turnos"]], [1, 2])

    def test_whitespace_around_the_answer_is_ignored(self):
        with _net('  \n {"status": "success"} \r\n'):
            self.assertEqual(_client()._get("dame_tipo"), {"status": "success"})

    def test_invalid_utf8_is_replaced_not_fatal(self):
        with _net(b'{"nombre": "Mu\xf1oz"}'):
            self.assertEqual(_client()._get("dame_clinmed"), {"nombre": "Mu�oz"})

    def test_a_transient_failure_is_retried_with_a_growing_pause(self):
        with _net(socket.timeout("timed out"), OSError("reset"), '{"ok": true}') as net:
            self.assertEqual(_client()._get("dame_clinpag"), {"ok": True})
        self.assertEqual(len(net.requests), 3)
        self.assertEqual([c.args[0] for c in net.sleep.call_args_list], [0.6, 1.2])

    def test_three_failures_raise_a_webturno_error_naming_the_endpoint(self):
        with _net(TimeoutError("lenta"), TimeoutError("lenta"), TimeoutError("lenta")) as net:
            with self.assertRaises(WebturnoError) as ctx:
                _client()._get("dame_clinpag")
        self.assertIn("red falló en pf=dame_clinpag", str(ctx.exception))
        self.assertIn("lenta", str(ctx.exception))
        self.assertEqual(len(net.requests), 3)
        self.assertEqual(net.sleep.call_count, 2)  # no pause after the last attempt

    def test_a_read_failure_counts_as_a_network_failure_too(self):
        with _net(ConnectionResetError("cortada"), ConnectionResetError("cortada"), '{"ok": 1}') as net:
            self.assertEqual(_client()._get("dame_tipo"), {"ok": 1})
        self.assertEqual(len(net.requests), 3)

    def test_an_empty_answer_is_a_non_json_error_not_an_empty_dict(self):
        with _net(""):
            with self.assertRaises(WebturnoError) as ctx:
                _client()._get("dame_turn")
        self.assertIn("Respuesta no-JSON de pf=dame_turn", str(ctx.exception))

    def test_an_html_error_page_is_a_non_json_error_with_the_start_of_the_page(self):
        page = "<html>" + "x" * 500 + "</html>"
        with _net(page):
            with self.assertRaises(WebturnoError) as ctx:
                _client()._get("dame_turn")
        message = str(ctx.exception)
        self.assertIn("Respuesta no-JSON de pf=dame_turn: <html>", message)
        self.assertNotIn("x" * 200, message)  # only the first 120 characters travel

    def test_the_unknown_query_message_is_its_own_error(self):
        with _net("Error en nombre de consulta"):
            with self.assertRaises(WebturnoError) as ctx:
                _client()._get("pf_inventado")
        self.assertIn("Consulta desconocida: pf=pf_inventado", str(ctx.exception))

    def test_a_json_null_or_list_is_returned_as_is(self):
        with _net("null"):
            self.assertIsNone(_client()._get("dame_turn"))
        with _net("[]"):
            self.assertEqual(_client()._get("dame_turn"), [])


# ---- endpoints -------------------------------------------------------------------------------------------------

class _Recorder:
    """Replaces `Webturno._get`: records (pf, params) and answers with a canned value."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, pf, **params):
        self.calls.append((pf, params))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@contextlib.contextmanager
def _answers(answer):
    rec = _Recorder(answer)
    with mock.patch.object(Webturno, "_get", lambda self, pf, **p: rec(pf, **p)):
        yield rec


class TestEndpoints(unittest.TestCase):
    def test_login_returns_the_user_on_success(self):
        user = {"status": "success", "nombre": "ANA"}
        with _answers(user) as rec:
            self.assertEqual(_client().login(), user)
        self.assertEqual(rec.calls, [("dame_tipo", {"p0": "27888999", "p1": "999",
                                                    "p2": wt_mod.PINAMED_PUBLIC_CLINIC_ID})])

    def test_login_to_another_clinic_uses_that_token(self):
        with _answers({"status": "success"}) as rec:
            _client().login(TOKEN)
        self.assertEqual(rec.calls[0][1]["p2"], TOKEN)

    def test_login_fails_loudly_on_a_refusal_or_an_answer_without_status(self):
        for answer in ({"status": "error", "mensaje": "clave"}, {}):
            with _answers(answer):
                with self.assertRaises(WebturnoError) as ctx:
                    _client().login()
            self.assertIn("Login falló", str(ctx.exception))

    def test_my_clinics_seeds_pinamed_first_when_webturno_does_not_list_it(self):
        with _answers({"clinicas": [{"idc": "X1", "nombre": "Clinica Uno"}, {"idc": "X2", "nombre": "Dos"}]}) as rec:
            out = _client().my_clinics()
        self.assertEqual(out, [
            {"token": wt_mod.PINAMED_PUBLIC_CLINIC_ID, "nombre": "Pinamed"},
            {"token": "X1", "nombre": "Clinica Uno"},
            {"token": "X2", "nombre": "Dos"},
        ])
        self.assertEqual(rec.calls, [("dame_clinpag", {"p0": "27888999"})])

    def test_my_clinics_does_not_duplicate_pinamed_when_it_is_listed(self):
        answer = {"clinicas": [{"idc": "X1", "nombre": "Uno"},
                               {"idc": wt_mod.PINAMED_PUBLIC_CLINIC_ID, "nombre": "Pinamed Real"}]}
        with _answers(answer):
            out = _client().my_clinics()
        self.assertEqual([c["nombre"] for c in out], ["Uno", "Pinamed Real"])

    def test_my_clinics_with_an_empty_answer_still_has_pinamed(self):
        for answer in ({}, {"clinicas": []}):
            with _answers(answer):
                self.assertEqual(_client().my_clinics(),
                                 [{"token": wt_mod.PINAMED_PUBLIC_CLINIC_ID, "nombre": "Pinamed"}])

    def test_doctors_slots_and_my_appointments_read_their_list_key(self):
        with _answers({"especialistas": [{"idme": "5"}], "turnos": [{"idtur": 9}]}) as rec:
            c = _client()
            self.assertEqual(c.doctors(TOKEN), [{"idme": "5"}])
            self.assertEqual(c.slots(TOKEN, "5"), [{"idtur": 9}])
            self.assertEqual(c.my_appointments(TOKEN), [{"idtur": 9}])
        self.assertEqual(rec.calls, [
            ("dame_clinmed", {"p2": TOKEN, "tus": "P", "med": 0}),
            ("dame_turn", {"op": "l", "med": "5", "p2": TOKEN, "tus": "P", "p0": "27888999"}),
            ("dame_turn", {"op": "a", "med": 0, "p2": TOKEN, "tus": "P", "p0": "27888999"}),
        ])

    def test_empty_answers_are_empty_lists(self):
        with _answers({}):
            c = _client()
            self.assertEqual(c.doctors(TOKEN), [])
            self.assertEqual(c.slots(TOKEN, "5"), [])
            self.assertEqual(c.my_appointments(TOKEN), [])
            self.assertEqual(c.upcoming_appointments(), [])

    def test_upcoming_appointments_are_normalised_deduplicated_and_sorted(self):
        answer = {"turnos": [
            {"idtur": "B", "fecha": "2026-11-20 10:00", "fectur": "20/11 10:00", "medico": "  Dr. House ",
             "especialidad": " Clinica ", "nomclin": " (Pinamed) ", "esttur": "A"},
            {"idtur": "A", "fecha": "2026-11-10 09:00", "fectur": "10/11 09:00", "medico": None,
             "especialidad": None, "nomclin": None, "esttur": "A"},
            {"idtur": "B", "fecha": "2099-01-01 00:00", "medico": "duplicado"},
            {"idtur": "C", "fecha": None},
        ]}
        with _answers(answer) as rec:
            out = _client().upcoming_appointments()
        self.assertEqual([t["idtur"] for t in out], ["C", "A", "B"])  # a missing date sorts first
        by_id = {t["idtur"]: t for t in out}
        self.assertEqual(by_id["B"]["medico"], "Dr. House")
        self.assertEqual(by_id["B"]["especialidad"], "Clinica")
        self.assertEqual(by_id["B"]["clinica"], "Pinamed")
        self.assertEqual(by_id["B"]["fecha"], "2026-11-20 10:00")  # the first copy wins
        self.assertEqual((by_id["A"]["medico"], by_id["A"]["especialidad"], by_id["A"]["clinica"]), ("", "", ""))
        self.assertEqual(rec.calls[0][0], "dame_turn")
        self.assertEqual(rec.calls[0][1]["op"], "a")
        self.assertEqual(rec.calls[0][1]["p2"], wt_mod.PINAMED_PUBLIC_CLINIC_ID)

    def test_book_and_cancel_send_the_mod_tur_request_and_return_the_answer_untouched(self):
        answer = {"status": "success", "mensaje": "ok", "color": "verde"}
        with _answers(answer) as rec:
            c = _client()
            self.assertEqual(c.book(TOKEN, "T9", idme="5"), answer)
            self.assertEqual(c.cancel_appointment(TOKEN, "T9", med=3), answer)
        base = {"usuario": "27888999", "tus": "P", "p0": "27888999", "p2": TOKEN, "idt": "T9"}
        self.assertEqual(rec.calls, [("mod_tur", {"op": "T", **base}), ("mod_tur", {"op": "C", **base})])

    def test_auth_ok_is_true_only_on_success(self):
        for answer, expected in (({"status": "success"}, True), ({"status": "error"}, False), ({}, False)):
            with _answers(answer) as rec:
                self.assertIs(_client().auth_ok(TOKEN, "clave"), expected)
            self.assertEqual(rec.calls, [("dame_tipo", {"p0": "27888999", "p1": "clave", "p2": TOKEN})])

    def test_change_password_returns_the_raw_answer(self):
        with _answers({"status": "success"}) as rec:
            self.assertEqual(_client().change_password(TOKEN, "nueva"), {"status": "success"})
        self.assertEqual(rec.calls, [("modif_pass", {"op": "p", "p0": "27888999", "p1": "nueva",
                                                     "p2": TOKEN, "usuario": "27888999"})])

    def test_a_dead_network_surfaces_as_webturno_error_from_every_endpoint(self):
        with _answers(WebturnoError("red falló")):
            c = _client()
            for call in (c.login, c.my_clinics, c.upcoming_appointments):
                with self.assertRaises(WebturnoError):
                    call()
            with self.assertRaises(WebturnoError):
                c.slots(TOKEN, "5")


# ---- next-slot rows ---------------------------------------------------------------------------------------

class TestApplySlots(unittest.TestCase):
    def apply(self, slots):
        with mock.patch.object(wt_mod.time, "strftime", return_value="2026-11-06T09:00:00-0300"):
            return wt_mod._apply_slots({"clinic_token": TOKEN, "idme": "5"}, slots)

    def test_no_slots_leaves_the_doctor_offline(self):
        for slots in ([], None):
            row = self.apply(slots)
            self.assertEqual((row["online"], row["turnos_libres"], row["slots"]), (False, 0, []))
            self.assertIsNone(row["proximo_turno"])
            self.assertIsNone(row["proximo_turno_fmt"])
            self.assertEqual(row["refreshed"], "2026-11-06T09:00:00-0300")

    def test_the_earliest_slot_is_the_next_one_and_the_list_keeps_eight(self):
        slots = [{"fecha": f"2026-11-{30 - i:02d} 10:00", "fectur": f"f{i}", "idtur": i, "otro": "x"}
                 for i in range(10)]
        row = self.apply(slots)
        self.assertTrue(row["online"])
        self.assertEqual(row["turnos_libres"], 10)  # counted before the cut to eight
        self.assertEqual(row["proximo_turno"], "2026-11-21 10:00")
        self.assertEqual(row["proximo_turno_fmt"], "f9")
        self.assertEqual(len(row["slots"]), 8)
        self.assertEqual(row["slots"][0], {"fecha": "2026-11-21 10:00", "fectur": "f9", "idtur": 9})
        self.assertEqual(row["slots"][-1]["fecha"], "2026-11-28 10:00")
        self.assertEqual(set(row["slots"][0]), {"fecha", "fectur", "idtur"})

    def test_the_callers_list_is_not_mutated_and_a_slot_without_a_date_sorts_first(self):
        slots = [{"fecha": "2026-11-10 09:00", "idtur": 1}, {"idtur": 2}]
        row = self.apply(slots)
        self.assertEqual([s["idtur"] for s in slots], [1, 2])
        self.assertEqual([s["idtur"] for s in row["slots"]], [2, 1])
        self.assertIsNone(row["proximo_turno"])  # the dateless one is "first"; the row says so honestly
        self.assertTrue(row["online"])


# ---- availability build ----------------------------------------------------------------------------------

class _FakeWt:
    """The slice of Webturno that build_availability / refresh_doctors / sync_passwords use."""

    def __init__(self, clinics=None, doctors=None, slots=None, mine=None, dni="27888999"):
        self.dni = dni
        self._clinics = clinics if clinics is not None else [{"token": "C1", "nombre": "Uno"}]
        self._doctors = doctors or {}
        self._slots = slots or {}
        self._mine = mine
        self.logins = 0
        self.slot_calls = []

    def login(self):
        self.logins += 1

    def my_clinics(self):
        return list(self._clinics)

    def doctors(self, token):
        got = self._doctors.get(token, [])
        if isinstance(got, BaseException):
            raise got
        return got

    def slots(self, token, idme):
        self.slot_calls.append((token, idme))
        got = self._slots.get((token, idme), [])
        if isinstance(got, BaseException):
            raise got
        return got

    def upcoming_appointments(self):
        if isinstance(self._mine, BaseException):
            raise self._mine
        return self._mine or []


DOC = {"idme": "5", "nombre": " Dr. House ", "horario": " Lu a Vi ", "mensaje": None, "pie": " pie ",
       "especialidades": [{"nom": "Clinica"}, {"nom": "Cardio"}, {}],
       "obrasSociales": [{"nom": "OSDE"}, {}]}


class TestBuildAvailability(unittest.TestCase):
    def build(self, wt, **kw):
        err = io.StringIO()
        kw.setdefault("include_directory", False)
        kw.setdefault("verbose", False)
        with mock.patch.object(wt_mod.time, "strftime", return_value="NOW"), contextlib.redirect_stderr(err):
            out = wt_mod.build_availability(wt, **kw)
        return out, err.getvalue()

    def test_one_row_per_doctor_with_the_next_slot_from_the_api(self):
        slots = [{"fecha": "2026-11-12 10:00", "fectur": "12/11", "idtur": 2},
                 {"fecha": "2026-11-10 09:00", "fectur": "10/11", "idtur": 1}]
        wt = _FakeWt(doctors={"C1": [DOC, {"idme": "6", "nombre": "Sin agenda"}]},
                     slots={("C1", "5"): slots}, mine=[{"idtur": 1}])
        out, _ = self.build(wt)
        self.assertEqual(wt.logins, 1)
        self.assertEqual((out["paciente"], out["clinicas"], out["total_medicos"], out["con_turno_online"]),
                         ("27888999", ["Uno"], 2, 1))
        self.assertEqual(out["generado"], "NOW")
        self.assertEqual(out["mis_turnos"], [{"idtur": 1}])
        house, other = out["medicos"]
        self.assertEqual((house["clinica"], house["clinic_token"], house["idme"], house["nombre"]),
                         ("Uno", "C1", "5", "Dr. House"))
        self.assertEqual((house["especialidad"], house["horario"], house["mensaje"], house["pie"]),
                         ("Clinica, Cardio, ", "Lu a Vi", "", "pie"))
        self.assertEqual(house["obras_sociales"], ["OSDE", ""])
        self.assertEqual((house["online"], house["proximo_turno"], house["turnos_libres"]),
                         (True, "2026-11-10 09:00", 2))
        self.assertEqual((other["online"], other["proximo_turno"], other["turnos_libres"], other["slots"]),
                         (False, None, 0, []))
        self.assertEqual(other["obras_sociales"], [])

    def test_a_clinic_whose_doctors_fail_is_skipped_and_the_rest_are_kept(self):
        wt = _FakeWt(clinics=[{"token": "C1", "nombre": "Caida"}, {"token": "C2", "nombre": "Viva"}],
                     doctors={"C1": WebturnoError("red falló"), "C2": [DOC]})
        out, err = self.build(wt, verbose=True)
        self.assertEqual([m["clinica"] for m in out["medicos"]], ["Viva"])
        self.assertEqual(out["clinicas"], ["Caida", "Viva"])  # the clinic list still names it
        self.assertIn("[Caida] sin médicos", err)
        self.assertIn("[Viva] 1 médicos", err)
        self.assertIn("Clínicas a recorrer: 2", err)
        self.assertIn("Listo: 1 médicos, 0 con turno online.", err)

    def test_a_doctor_whose_slots_fail_stays_offline_without_aborting_the_build(self):
        wt = _FakeWt(doctors={"C1": [DOC, dict(DOC, idme="6")]},
                     slots={("C1", "5"): OSError("timeout"),
                            ("C1", "6"): [{"fecha": "2026-11-10 09:00", "fectur": "x", "idtur": 1}]})
        out, _ = self.build(wt)
        online = {m["idme"]: m["online"] for m in out["medicos"]}
        self.assertEqual(online, {"5": False, "6": True})
        self.assertEqual(out["con_turno_online"], 1)

    def test_my_appointments_failing_at_the_end_gives_an_empty_list(self):
        wt = _FakeWt(doctors={"C1": [DOC]}, mine=WebturnoError("red falló"))
        out, _ = self.build(wt)
        self.assertEqual(out["mis_turnos"], [])
        self.assertEqual(out["total_medicos"], 1)

    def test_no_clinics_and_no_doctors_is_an_empty_but_valid_database(self):
        out, _ = self.build(_FakeWt(clinics=[]))
        self.assertEqual((out["clinicas"], out["medicos"], out["total_medicos"], out["con_turno_online"],
                          out["mis_turnos"]), ([], [], 0, 0, []))

    def test_the_public_directory_adds_clinics_not_already_registered(self):
        wt = _FakeWt(doctors={"C1": [], "C9": [DOC]})
        directory = [{"token": "C1", "nombre": "Duplicada"}, {"token": "C9", "nombre": "Del directorio"},
                     {"token": "C9", "nombre": "Repetida en el directorio"}]
        with mock.patch.object(wt_mod, "directory_clinics", return_value=directory) as listing:
            out, _ = self.build(wt, include_directory=True)
        listing.assert_called_once_with(wt_mod.LOCALIDADES_PINAMAR, verbose=False)
        self.assertEqual(out["clinicas"], ["Uno", "Del directorio"])
        self.assertEqual([m["clinic_token"] for m in out["medicos"]], ["C9"])

    def test_the_directory_is_not_asked_when_it_is_switched_off(self):
        with mock.patch.object(wt_mod, "directory_clinics") as listing:
            self.build(_FakeWt(), include_directory=False)
        listing.assert_not_called()

    def test_progress_is_reported_every_ten_agendas_when_verbose(self):
        doctors = [dict(DOC, idme=str(i)) for i in range(10)]
        out, err = self.build(_FakeWt(doctors={"C1": doctors}), verbose=True)
        self.assertEqual(out["total_medicos"], 10)
        self.assertIn("...10/10 agendas leídas", err)

    def test_nothing_is_printed_when_not_verbose(self):
        _, err = self.build(_FakeWt(doctors={"C1": [DOC]}), verbose=False)
        self.assertEqual(err, "")


class TestRefreshDoctors(unittest.TestCase):
    def db(self):
        return {"medicos": [
            {"clinic_token": "C1", "idme": "5", "nombre": "A", "slots": [], "online": False},
            {"clinic_token": "C1", "idme": "6", "nombre": "B", "slots": [], "online": False},
        ]}

    def refresh(self, wt, db, pairs):
        with mock.patch.object(wt_mod.time, "strftime", return_value="NOW"):
            return wt_mod.refresh_doctors(wt, db, pairs)

    def test_only_the_asked_pairs_are_refreshed_in_place(self):
        db = self.db()
        wt = _FakeWt(slots={("C1", "5"): [{"fecha": "2026-11-10 09:00", "fectur": "f", "idtur": 1}]})
        out = self.refresh(wt, db, [{"clinic_token": "C1", "idme": "5"}])
        self.assertEqual(out, [db["medicos"][0]])
        self.assertTrue(db["medicos"][0]["online"])
        self.assertEqual(db["medicos"][0]["refreshed"], "NOW")
        self.assertFalse(db["medicos"][1]["online"])
        self.assertEqual(wt.slot_calls, [("C1", "5")])

    def test_unknown_pairs_and_duplicates_are_ignored(self):
        db = self.db()
        wt = _FakeWt()
        pairs = [{"clinic_token": "C1", "idme": "5"}, {"clinic_token": "C1", "idme": "5"},
                 {"clinic_token": "ZZ", "idme": "1"}, {}]
        out = self.refresh(wt, db, pairs)
        self.assertEqual(len(out), 1)
        self.assertEqual(wt.slot_calls, [("C1", "5")])

    def test_no_pairs_or_an_empty_database_fetches_nothing(self):
        wt = _FakeWt()
        self.assertEqual(self.refresh(wt, self.db(), None), [])
        self.assertEqual(self.refresh(wt, self.db(), []), [])
        self.assertEqual(self.refresh(wt, {}, [{"clinic_token": "C1", "idme": "5"}]), [])
        self.assertEqual(wt.slot_calls, [])

    def test_a_webturno_error_leaves_the_doctor_offline_instead_of_failing(self):
        db = self.db()
        db["medicos"][0].update(online=True, turnos_libres=3)
        wt = _FakeWt(slots={("C1", "5"): WebturnoError("red falló")})
        self.refresh(wt, db, [{"clinic_token": "C1", "idme": "5"}])
        self.assertEqual((db["medicos"][0]["online"], db["medicos"][0]["turnos_libres"]), (False, 0))


# ---- passwords --------------------------------------------------------------------------------------------

class TestPasswords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(wt_mod, "__file__", os.path.join(self.tmp.name, "webturno.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, cfg):
        with open(os.path.join(self.tmp.name, "passwords.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def test_the_file_is_read_next_to_the_module(self):
        self.write({"candidates": ["123"], "desired": "unica"})
        self.assertEqual(wt_mod.load_passwords(), {"candidates": ["123", "unica"], "desired": "unica"})

    def test_the_desired_password_is_not_added_twice(self):
        self.write({"candidates": ["unica", "123"], "desired": "unica"})
        self.assertEqual(wt_mod.load_passwords()["candidates"], ["unica", "123"])

    def test_candidates_default_to_just_the_desired_one(self):
        self.write({"desired": "unica"})
        self.assertEqual(wt_mod.load_passwords()["candidates"], ["unica"])

    def test_a_missing_file_or_a_missing_desired_password_is_an_error(self):
        with self.assertRaises(WebturnoError) as ctx:
            wt_mod.load_passwords()
        self.assertIn("Falta passwords.json", str(ctx.exception))
        for cfg in ({"candidates": ["1"]}, {"candidates": ["1"], "desired": ""}):
            self.write(cfg)
            with self.assertRaises(WebturnoError) as ctx:
                wt_mod.load_passwords()
            self.assertIn("sin 'desired'", str(ctx.exception))

    def test_safe_auth_turns_a_webturno_error_into_false(self):
        wt = mock.Mock()
        wt.auth_ok.side_effect = WebturnoError("red falló")
        self.assertIs(wt_mod._safe_auth(wt, "C1", "x"), False)
        wt.auth_ok.side_effect = None
        wt.auth_ok.return_value = True
        self.assertIs(wt_mod._safe_auth(wt, "C1", "x"), True)


class _PwWt:
    """Per clinic, the one password that authenticates; change_password moves it (or not)."""

    def __init__(self, current, change_sticks=True):
        self.current = dict(current)
        self.change_sticks = change_sticks
        self.changed = []
        self.tried = []

    def my_clinics(self):
        return [{"token": t, "nombre": f"Clinica {t}"} for t in self.current]

    def auth_ok(self, token, password):
        self.tried.append((token, password))
        if self.current[token] == "RED":
            raise WebturnoError("red falló")
        return self.current[token] == password

    def change_password(self, token, new):
        self.changed.append((token, new))
        if self.change_sticks:
            self.current[token] = new


class TestSyncPasswords(unittest.TestCase):
    CFG = {"candidates": ["123", "999", "unica"], "desired": "unica"}

    def sync(self, wt, **kw):
        err = io.StringIO()
        kw.setdefault("verbose", False)
        with mock.patch.object(wt_mod, "load_passwords", return_value=json.loads(json.dumps(self.CFG))), \
                contextlib.redirect_stderr(err):
            return wt_mod.sync_passwords(wt, **kw), err.getvalue()

    def test_detection_only_reports_and_changes_nothing(self):
        wt = _PwWt({"A": "unica", "B": "999", "C": "desconocida"})
        out, _ = self.sync(wt)
        self.assertEqual(out["desired"], "unica")
        a, b, c = out["clinicas"]
        self.assertEqual((a["actual"], a["ok"], a["cambiada"]), ("unica", True, False))
        self.assertEqual((b["actual"], b["ok"], b["cambiada"]), ("999", False, False))
        self.assertEqual((c["actual"], c["ok"], c["nota"]), (None, False, "ninguna candidata autenticó"))
        self.assertEqual(wt.changed, [])

    def test_candidates_are_tried_in_order_and_stop_at_the_first_that_works(self):
        wt = _PwWt({"A": "999"})
        self.sync(wt)
        self.assertEqual(wt.tried, [("A", "123"), ("A", "999")])

    def test_apply_unifies_the_clinics_and_verifies_the_change(self):
        wt = _PwWt({"A": "unica", "B": "999"})
        out, _ = self.sync(wt, apply=True)
        self.assertEqual(wt.changed, [("B", "unica")])
        b = out["clinicas"][1]
        self.assertEqual((b["actual"], b["ok"], b["cambiada"]), ("unica", True, True))
        self.assertNotIn("nota", b)

    def test_apply_reports_a_change_that_did_not_verify(self):
        wt = _PwWt({"B": "999"}, change_sticks=False)
        out, _ = self.sync(wt, apply=True)
        b = out["clinicas"][0]
        self.assertEqual((b["actual"], b["ok"], b["cambiada"], b["nota"]), ("999", False, True, "el cambio no verificó"))

    def test_apply_leaves_a_clinic_nobody_can_open_alone(self):
        wt = _PwWt({"C": "desconocida"})
        out, _ = self.sync(wt, apply=True)
        self.assertEqual(wt.changed, [])
        self.assertEqual(out["clinicas"][0]["nota"], "ninguna candidata autenticó")

    def test_a_clinic_that_errors_counts_as_unopened(self):
        out, _ = self.sync(_PwWt({"R": "RED"}))
        self.assertIsNone(out["clinicas"][0]["actual"])

    def test_verbose_prints_one_line_per_clinic_with_its_mark(self):
        wt = _PwWt({"A": "unica", "B": "999", "C": "desconocida"})
        _, err = self.sync(wt, apply=True, verbose=True)
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("✓", lines[0])
        self.assertIn("(deseada)", lines[0])
        self.assertIn("[cambiada]", lines[1])
        self.assertIn("✗", lines[2])
        self.assertIn("—", lines[2])

    def test_verbose_marks_a_known_but_different_password_with_a_dot(self):
        _, err = self.sync(_PwWt({"B": "999"}), verbose=True)
        self.assertIn("·", err)
        self.assertIn("999", err)


# ---- CLI ---------------------------------------------------------------------------------------------------

class TestMain(unittest.TestCase):
    def run_main(self, argv, wt=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(wt_mod, "Webturno", return_value=wt or mock.Mock()), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wt_mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_no_arguments_prints_the_usage_and_succeeds(self):
        code, out, _ = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("python webturno.py login", out)

    def test_an_unknown_command_fails_with_a_message(self):
        code, out, err = self.run_main(["bailar"])
        self.assertEqual(code, 1)
        self.assertIn("comando desconocido: bailar", err)
        self.assertEqual(out, "")

    def test_login_clinics_doctors_and_slots_print_json(self):
        wt = mock.Mock()
        wt.login.return_value = {"status": "success", "nombre": "Muñoz"}
        wt.my_clinics.return_value = [{"token": "C1", "nombre": "Uno"}]
        wt.doctors.return_value = [{"idme": "5"}]
        wt.slots.return_value = [{"idtur": 1}]
        code, out, _ = self.run_main(["login"], wt)
        self.assertEqual((code, json.loads(out)["nombre"]), (0, "Muñoz"))
        self.assertIn("Muñoz", out)  # ensure_ascii=False
        self.assertEqual(json.loads(self.run_main(["clinics"], wt)[1]), [{"token": "C1", "nombre": "Uno"}])
        self.assertEqual(json.loads(self.run_main(["doctors", "C1"], wt)[1]), [{"idme": "5"}])
        wt.doctors.assert_called_with("C1")
        self.assertEqual(json.loads(self.run_main(["slots", "C1", "5"], wt)[1]), [{"idtur": 1}])
        wt.slots.assert_called_with("C1", "5")

    def test_appointments_lists_every_clinic_once_per_idtur(self):
        wt = mock.Mock()
        wt.my_clinics.return_value = [{"token": "C1", "nombre": "Uno"}, {"token": "C2", "nombre": "Dos"}]
        wt.my_appointments.side_effect = lambda t: [{"idtur": "A", "c": t}, {"idtur": "B" if t == "C1" else "A", "c": t}]
        code, out, _ = self.run_main(["appointments"], wt)
        self.assertEqual(code, 0)
        self.assertEqual([(t["idtur"], t["c"]) for t in json.loads(out)], [("A", "C1"), ("B", "C1")])

    def test_db_writes_availability_json_under_the_repo_root_data_dir(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "data"))
            with mock.patch.object(wt_mod, "_ROOT", root), \
                    mock.patch.object(wt_mod, "build_availability", return_value={"medicos": [], "n": "ñ"}) as build:
                code, out, _ = self.run_main(["db"])
            path = os.path.join(root, "data", "availability.json")
            self.assertEqual((code, out.strip()), (0, path))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"medicos": [], "n": "ñ"})
        build.assert_called_once()

    def test_passwords_detects_by_default_and_applies_with_the_flag(self):
        wt = mock.Mock()
        with mock.patch.object(wt_mod, "sync_passwords", return_value={"desired": "x", "clinicas": []}) as sync:
            code, out, err = self.run_main(["passwords"], wt)
            self.assertEqual((code, json.loads(out)), (0, {"desired": "x", "clinicas": []}))
            self.assertIn("DETECCIÓN", err)
            self.assertIs(sync.call_args.kwargs["apply"], False)
            code, out, err = self.run_main(["passwords", "--apply"], wt)
            self.assertIn("APLICANDO", err)
            self.assertIs(sync.call_args.kwargs["apply"], True)


if __name__ == "__main__":
    unittest.main()
