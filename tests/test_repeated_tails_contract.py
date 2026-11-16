"""test_repeated_tails_contract.py - small helpers written two to six times say what they said (2026-10-28).

Fourth pass of the architecture audit: the exact-body groups of 14 to 40 AST nodes that the third pass left
below its threshold. Each group is a helper (or a class, or a method) spelled out in several files with the same
body; before one copy replaces the others, the answers of every copy are written down here, so a difference
between the copies would show as a red test now, not as a patient-visible or staff-visible change later:

  - the JSON body decoders (`core.api_body.decode`, the native gate's `read_json_body`, the panel login's
    `parse_json_body`, and the decoder of the prompts route's body contract);
  - the base64url pair of the passkeys and of the WebAuthn adapter;
  - the terminal 404 of the admin routers and of the appointment confirmation router;
  - the `_require_admin` call-through of the two appointment routers;
  - the email transports of Mailgun and Mailjet (`send`, `missing_config`, `probe`);
  - the peer address a router reads from its request (`_client_host`);
  - the empty-body 302 of three routers;
  - the `(status, body)` / `(status, message)` errors of five domains;
  - the `[backups]` log line of three backup modules;
  - the integer settings (`_int_env`) of three AI and chat modules and the stripped text settings of the mail modules;
  - the legacy-name fallback (`_env2`) of the waitlist and the reminders;
  - the header reader of the Turnstile check and of the proxy trust;
  - the `AT TIME ZONE` column of the report and slot queries;
  - the session user read by the account photo and the calendar feed;
  - the service name read by the appointment pages and the remedies;
  - the primary subtag of a language code (`es-AR` -> `es`) of the translation layer and the site identity.

Nothing here knows where a body lives, so the file stays valid when the copies become one: each entry is
addressed by "module:name" and the names stay where they were. The recorded file
`tests/golden/repeated_tails.json` is written with the code that existed before and is not touched by the
change that joins them. Re-record only from code whose answers are known to be right:
`python3 tests/test_repeated_tails_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_repeated_tails_contract.py
"""
import asyncio
import contextlib
import importlib
import inspect
import io
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "repeated_tails.json")


def _get(ref, *alternatives):
    """`module:name` -> the object; `alternatives` are other names the same helper may have after a merge."""
    module, name = ref.split(":")
    mod = importlib.import_module(module)
    for candidate in (name,) + alternatives:
        if hasattr(mod, candidate):
            return getattr(mod, candidate)
    raise AttributeError("%s has none of %s" % (module, (name,) + alternatives))


def _run(coro):
    return asyncio.run(coro)


async def _coro(value):
    return value


def _try(fn, *args, **kwargs):
    try:
        return {"value": repr(fn(*args, **kwargs))}
    except Exception as exc:  # noqa: BLE001 - the exception type is part of what is pinned
        return {"raised": type(exc).__name__, "message": str(exc)}


def _describe_response(response):
    headers = sorted([k.decode("latin-1"), v.decode("latin-1")] for k, v in response.raw_headers)
    return {"status": response.status_code, "headers": headers, "body": bytes(response.body).decode("utf-8"),
            "media_type": response.media_type, "class": type(response).__name__}


# ---- JSON body decoders ------------------------------------------------------------------------------------

BODIES = [b"", b"{}", b'{"a": 1, "b": [1, 2.5, null]}', "{\"nombre\": \"Ñandú Pérez ¿ok?\"}".encode("utf-8"),
          b"null", b"[1, 2]", b"   ", b'{"a": ', b"\xff\xfe{}", b"not json", b"\xef\xbb\xbf{}", b'{"a": NaN}',
          b'"texto"', b"0", b"{}  \n", "﻿{}".encode("utf-16"), None, bytearray(b""), bytearray(b"[7]")]

JSON_DECODERS = {
    "app.core.api_body:decode": "sync",
    "app.core.native_gate:read_json_body": "async",
    "app.auth.admin_auth:parse_json_body": "sync",
}


def _json_cases():
    out = {}
    for ref, kind in JSON_DECODERS.items():
        fn = _get(ref)
        cases = {}
        for raw in BODIES:
            if kind == "async":
                request = SimpleNamespace(body=lambda raw=raw: _coro(raw))
                cases[repr(raw)] = _try(lambda: _run(fn(request)))
            else:
                cases[repr(raw)] = _try(fn, raw)
        out[ref] = cases
    return out


# ---- base64url ---------------------------------------------------------------------------------------------

B64_ENCODE_INPUTS = [b"", b"\x00", b"\xfb\xff", b"hello world", bytes(range(256)), bytearray(b"ab"), "x", None, 5]
B64_DECODE_INPUTS = [None, "", "AA", "AAA", "AAAA", "A", "-_-_", "+/+/", "AA==", "AA=", " AA ", 123, b"AA", "é",
                     "%%%%", "aGVsbG8gd29ybGQ", "aGVsbG8gd29ybGQ=", "////", "____", 0, False]
B64_FUNCTIONS = {
    "app.auth.passkeys:b64u": B64_ENCODE_INPUTS, "app.auth.passkeys:b64u_dec": B64_DECODE_INPUTS,
    "app.auth.webauthn_adapter:_b64u": B64_ENCODE_INPUTS, "app.auth.webauthn_adapter:_b64u_dec": B64_DECODE_INPUTS,
}


def _b64_cases():
    return {ref: {repr(v): _try(_get(ref), v) for v in inputs} for ref, inputs in B64_FUNCTIONS.items()}


# ---- terminal 404 / redirect -------------------------------------------------------------------------------

NOT_FOUND = ["app.admin_panel.native_json:terminal_not_found", "app.modules.turnos.appointment_confirm_router:_json_not_found",
             "app.core.terminal_response:json_not_found_response"]
REDIRECTS = ["app.cuentas.accounts_router:_redirect_response", "app.modules.intake._native_support:redirect_response",
             "app.patient_web.patient_site_router:_redirect_response"]
LOCATIONS = ["/a", "/x?y=1&z=%C3%B1", "", "https://example.org/ä", "/with space", "/\r\nX: y", None, 5]


def _response_cases():
    out = {"not_found": {ref: _describe_response(_get(ref)()) for ref in NOT_FOUND}, "redirect": {}}
    for ref in REDIRECTS:
        fn = _get(ref)
        cases = {}
        for loc in LOCATIONS:
            try:
                cases[repr(loc)] = _describe_response(fn(loc))
            except Exception as exc:  # noqa: BLE001
                cases[repr(loc)] = {"raised": type(exc).__name__, "message": str(exc)}
        out["redirect"][ref] = cases
    return out


# ---- _require_admin ----------------------------------------------------------------------------------------

REQUIRE_ADMIN = ["app.modules.turnos.booking_requests_router:_require_admin",
                 "app.modules.turnos.doctor_appointments_router:_require_admin"]


def _require_admin_cases():
    out = {}
    seen = []
    state = {}

    def factory(**flags):
        seen.append(flags)

        async def dependency(request):
            if state.get("raise"):
                raise state["raise"]
            return {"usuario": "ana", "request": request.tag}
        return dependency

    variants = [{}, {"solo_admin": True}, {"permitir_medico": True, "permitir_admisionista": True},
                {"permitir_reportes": True}, {"permitir_marca": True},
                {"solo_admin": False, "permitir_medico": False, "permitir_admisionista": False,
                 "permitir_reportes": False, "permitir_marca": False}]
    for ref in REQUIRE_ADMIN:
        fn = _get(ref)
        cases = {}
        with mock.patch("app.core.deps.require_admin", factory):
            for flags in variants:
                seen.clear()
                state.clear()
                result = _try(lambda: _run(fn(SimpleNamespace(tag="r"), **flags)))
                cases[repr(flags)] = {"result": result, "flags": seen[:]}
            seen.clear()
            state["raise"] = HTTPException(status_code=403, detail={"error": "solo_admin"})
            result = _try(lambda: _run(fn(SimpleNamespace(tag="r"), solo_admin=True)))
            cases["rejected"] = {"result": result, "flags": seen[:]}
            seen.clear()
            state["raise"] = RuntimeError("boom")
            result = _try(lambda: _run(fn(SimpleNamespace(tag="r"))))
            cases["crash"] = {"result": result, "flags": seen[:]}
        out[ref] = cases
    return out


# ---- email transports --------------------------------------------------------------------------------------

MAIL_CONNECTORS = {
    "app.connectors.mailgun.adapter:MailgunConnector": "app.notifications.mailgun",
    "app.connectors.mailjet.adapter:MailjetConnector": "app.connectors.mailjet.mailjet",
}


def _mail_cases():
    out = {}
    for ref, target in MAIL_CONNECTORS.items():
        cls = _get(ref)
        calls = []

        def send(*args, **kwargs):
            calls.append([list(args), kwargs])
            return "sent-%d" % len(calls)

        with mock.patch(target + ".send", send), \
                mock.patch(target + ".missing_config", lambda: ["KEY_A", "KEY_B"]), \
                mock.patch(target + ".probe", lambda: {"ok": True, "detail": "x"}):
            conn = cls()
            cases = {}
            for label, call in (
                    ("text_only", lambda: conn.send("a@b.c", "Asunto", "Texto")),
                    ("html_none", lambda: conn.send("a@b.c", "Asunto", "Texto", None)),
                    ("html_empty", lambda: conn.send("a@b.c", "Asunto", "Texto", "")),
                    ("html", lambda: conn.send("a@b.c", "Asunto", "Texto", html="<p>Ñ</p>")),
                    ("html_positional", lambda: conn.send("a@b.c", "Asunto", "Texto", "<p>x</p>")),
                    ("keywords", lambda: conn.send(to="a@b.c", subject="S", text="T")),
                    ("missing_config", conn.missing_config), ("probe", conn.probe)):
                before = len(calls)
                cases[label] = {"result": _try(call), "calls": calls[before:]}
        cases["meta"] = {"capability": repr(cls.capability), "is_transport": any(
            b.__name__ == "EmailTransportConnector" for b in cls.__mro__),
            "send_signature": str(inspect.signature(cls.send)),
            "abstract": sorted(getattr(cls, "__abstractmethods__", ()))}
        out[ref] = cases
    return out


# ---- the peer address of a request -------------------------------------------------------------------------

CLIENT_HOSTS = ["app.auth.admin_auth_router:_client_host", "app.auth.passkeys_router:_client_host",
                "app.cuentas.accounts_router:_client_host", "app.modules.intake.interview_patient_router:_client_host"]
CLIENTS = [("1.2.3.4", 5000), None, ("", 0), ("::1", 1), ("fe80::1%eth0", 9)]


def _client_host_cases():
    from starlette.requests import Request
    out = {}
    for ref in CLIENT_HOSTS:
        fn = _get(ref)
        cases = {}
        for client in CLIENTS:
            scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"",
                     "client": client}
            cases[repr(client)] = _try(fn, Request(scope))
        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
        cases["no_client_key"] = _try(fn, Request(scope))
        out[ref] = cases
    return out


# ---- (status, body) and (status, message) errors -----------------------------------------------------------

BODY_ERRORS = ["app.ops.demo_installation:DemoAccessError", "app.patient_web.branding:BrandingError",
               "app.patient_web.doctor_photos:DoctorPhotoError", "app.modules.turnos.doctor_appointments:DomainError",
               "app.patient_web.legal:LegalError"]
MESSAGE_ERRORS = ["app.admin_panel.admin_account:AccountError", "app.admin_panel.admin_preferences:PreferenceError",
                  "app.admin_panel.doctor_patients:DoctorPatientsError"]
ERROR_ARGS = [(400, {"error": "x", "n": [1]}), (404, "no"), (500, None), (409, ["a"]), (403, {}), (418, 5)]


def _describe_error(exc):
    return {"str": str(exc), "args": repr(exc.args), "status": repr(exc.status),
            "body": repr(getattr(exc, "body", "-")), "attrs": sorted(vars(exc)),
            "is_exception": isinstance(exc, Exception), "runtime": isinstance(exc, RuntimeError)}


def _error_cases():
    out = {}
    for ref in BODY_ERRORS + MESSAGE_ERRORS:
        try:
            cls = _get(ref)
        except (AttributeError, ImportError):
            continue
        cases = {}
        for status, payload in ERROR_ARGS:
            cases[repr((status, payload))] = _describe_error(cls(status, payload))
        second = "body" if ref in BODY_ERRORS else "message"
        cases["keywords"] = _describe_error(cls(status=422, **{second: {"k": "v"}}))
        cases["raised_and_caught"] = _try(lambda: (_ for _ in ()).throw(cls(400, "x")))
        out[ref] = cases
    return out


# ---- logs ---------------------------------------------------------------------------------------------------

BACKUP_LOGS = ["app.core.backup_lock:_log", "app.ops.data_backups:_log", "app.ops.purge_guard:_log"]
LOG_MESSAGES = ["x", "ñ ✓", "", 5, None, "a\nb"]


def _log_cases():
    out = {}
    for ref in BACKUP_LOGS:
        fn = _get(ref)
        cases = {}
        for msg in LOG_MESSAGES:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                result = _try(fn, msg)
            cases[repr(msg)] = {"result": result, "stderr": buf.getvalue()}
        out[ref] = cases
    return out


# ---- settings readers ---------------------------------------------------------------------------------------

INT_ENVS = ["app.ai.abuse_guard:_int_env", "app.ai.ai_cooldown:_int_env", "app.modules.chat.whatsapp_chat:_int_env"]
INT_VALUES = ["", "5", " 7 ", "abc", "-3", "1.5", "٣", "0", "1_0", "+4", "1e3", None, 12, "5\n", "0x10"]
TEXT_GETTERS = ["app.notifications.mail:setting", "app.notifications.mailgun:_get"]
TEXT_VALUES = [None, "", "  x  ", "\tv\n", "plain", "0", 0, False, 5, " ñ "]


def _fake_get(table, calls):
    def get(key, default=None, legacy=None):
        calls.append([key, default, legacy])
        value = table.get(key, default)
        if isinstance(value, BaseException):
            raise value
        return value
    return get


def _settings_cases():
    out = {"int_env": {}, "text": {}}
    for ref in INT_ENVS:
        fn = _get(ref)
        cases = {}
        for value in INT_VALUES + [ValueError("inside"), RuntimeError("down")]:
            calls = []
            with mock.patch("app.core.config.get", _fake_get({"K": value}, calls)):
                cases[repr(value)] = {"result": _try(fn, "K", 42), "calls": calls}
        calls = []
        with mock.patch("app.core.config.get", _fake_get({}, calls)):
            cases["absent"] = {"result": _try(fn, "K", 42), "calls": calls}
        out["int_env"][ref] = cases
    for ref in TEXT_GETTERS:
        fn = _get(ref)
        cases = {}
        for value in TEXT_VALUES:
            calls = []
            with mock.patch("app.core.config.get", _fake_get({"K": value}, calls)):
                cases[repr(value)] = {"result": _try(fn, "K"), "calls": calls}
        calls = []
        with mock.patch("app.core.config.get", _fake_get({}, calls)):
            cases["absent"] = {"result": _try(fn, "K"), "calls": calls}
            cases["absent_default"] = {"result": _try(fn, "K", "dflt"), "calls": calls[1:]}
        out["text"][ref] = cases
    return out


def _legacy_get(table, calls):
    def get(key, default=None, legacy=None):
        calls.append([key, default, legacy])
        for name in (key, legacy):
            if name and table.get(name):
                return table[name]
        return default
    return get


def _legacy_cases():
    out = {}
    targets = [("app.modules.turnos.waitlist", "TURNOS_WAITLIST_INTERVAL_MIN", "TURNOS_LISTA_ESPERA_INTERVAL_MIN",
                "TURNOS_WAITLIST_OFF", "TURNOS_LISTA_ESPERA_OFF"),
               ("app.notifications.reminders", "TURNOS_REMINDER_INTERVAL_MIN", "TURNOS_RECORDATORIO_INTERVAL_MIN",
                "TURNOS_REMINDERS_OFF", "TURNOS_RECORDATORIOS_OFF")]
    for module, new_key, old_key, off_new, off_old in targets:
        mod = importlib.import_module(module)
        cases = {}
        for label, table in (("none", {}), ("new", {new_key: "15"}), ("old", {old_key: "20"}),
                             ("both", {new_key: "5", old_key: "9"}), ("bad_new", {new_key: "x"}),
                             ("zero", {new_key: "0"}), ("blank", {new_key: " "}), ("neg", {old_key: "-4"})):
            calls = []
            with mock.patch("app.core.config.get", _legacy_get(table, calls)):
                cases["interval_" + label] = {"result": _try(mod._interval_min), "calls": calls}
        for label, table in (("off_new", {off_new: "1"}), ("off_old", {off_old: "1"})):
            calls = []
            buf = io.StringIO()
            with mock.patch("app.core.config.get", _legacy_get(table, calls)), contextlib.redirect_stderr(buf):
                cases[label] = {"result": _try(mod.start), "calls": calls, "stderr": buf.getvalue()}
        out[module] = cases
    return out


# ---- header reader, AT TIME ZONE, session user, service name ----------------------------------------------

class _Raises:
    def get(self, name):
        raise RuntimeError("no")


HEADERS = [None, {}, {"Host": "a"}, {"Host": ""}, {"Host": None}, {"host": "x"}, _Raises(), ["Host"], 5,
           {"Host": 0}, {"Host": " Space.Example "}]


def _header_cases():
    out = {}
    for ref, alt in (("app.auth.turnstile:_header_value", ()), ("app.core.proxy_trust:_header_value", ("header_value",))):
        fn = _get(ref, *alt)
        out[ref] = {("raises" if isinstance(h, _Raises) else repr(h)): _try(fn, h, "Host") for h in HEADERS}
        from starlette.datastructures import Headers
        out[ref]["starlette"] = _try(fn, Headers({"host": "H.example", "x-a": "1"}), "Host")
        out[ref]["missing_name"] = _try(fn, {"A": "1"}, "B")
    return out


def _sql_cases():
    import sqlalchemy as sa
    from sqlalchemy.dialects import postgresql
    from app.core import db_values
    out = {}
    for ref in ("app.core.db_slots:_at_art", "app.core.db_reports:_local"):
        fn = _get(ref)
        cases = {}
        for label, column in (("column", sa.column("created")), ("now", sa.func.now()),
                              ("qualified", sa.column("appointment_at"))):
            expr = fn(column)
            cases[label] = {"sql": str(expr.compile(dialect=postgresql.dialect(),
                                                    compile_kwargs={"literal_binds": True})),
                            "class": type(expr).__name__}
        with mock.patch.object(db_values, "_ART_SQL", "UTC"):
            cases["patched_zone"] = str(fn(sa.column("c")).compile(dialect=postgresql.dialect(),
                                                                    compile_kwargs={"literal_binds": True}))
        out[ref] = cases
    return out


def _user_cases():
    from app import cuentas
    out = {}
    for ref in ("app.cuentas.account_photo:_user", "app.modules.turnos.calendar_feed:_current_user"):
        fn = _get(ref)
        cases = {}
        seen = []
        for label, behaviour in (("user", lambda r: {"dni": "30123456", "r": r}), ("none", lambda r: None),
                                 ("boom", _raiser)):
            seen.clear()

            def current_user(request, behaviour=behaviour):
                seen.append(request)
                return behaviour(request)
            with mock.patch("app.cuentas.accounts.current_user", current_user):
                cases[label] = {"result": _try(fn, "REQ"), "seen": seen[:]}
        # `None` in sys.modules makes `from cuentas import accounts` fail (once the attribute is gone too).
        accounts = cuentas.accounts
        saved = sys.modules["app.cuentas.accounts"]
        sys.modules["app.cuentas.accounts"] = None
        del cuentas.accounts
        try:
            cases["import_fails"] = _try(fn, "REQ")
        finally:
            sys.modules["app.cuentas.accounts"] = saved
            cuentas.accounts = accounts
        out[ref] = cases
    return out


def _raiser(_request):
    raise RuntimeError("down")


def _service_name_cases():
    out = {}
    for ref in ("app.modules.turnos.appointment_page_support:service_name", "app.modules.turnos.remedies:_service_name"):
        fn = _get(ref)
        cases = {}
        for sid in (None, "", 0, "pediatria", 7, " x "):
            with mock.patch("app.modules.turnos.booking_requests._service_name", lambda s: "Servicio %s" % (s,)):
                cases["ok " + repr(sid)] = _try(fn, sid)
            with mock.patch("app.modules.turnos.booking_requests._service_name", _raiser):
                cases["fails " + repr(sid)] = _try(fn, sid)
            with mock.patch("app.modules.turnos.booking_requests._service_name", lambda s: None):
                cases["none " + repr(sid)] = _try(fn, sid)
        out[ref] = cases
    return out


LANGUAGES = [None, "", "en", "EN-us", "pt-BR", "es_AR", " en ", "-", "en-", "-en", "zh-Hans-CN", 5, False, "ÑU-x"]


def _language_cases():
    out = {}
    for ref in ("app.core.translate:_primary", "app.core.site_identity:_lang_primary"):
        fn = _get(ref)
        out[ref] = {repr(v): _try(fn, v) for v in LANGUAGES}
    return out


def _prompts_decoder_cases():
    """What the prompts route's body contract does with a body: the decoder it hands to `api_body.read_body`."""
    from app.ai import prompts_router
    return {repr(raw): _try(prompts_router._BODY.decoder, raw) for raw in BODIES}


# Every helper pinned above, whatever it is now (a function, an alias of one, a method): the names, kinds and
# defaults of its parameters are part of what a caller may rely on (a keyword call), annotations are not.
SIGNATURES = (
    list(JSON_DECODERS) + list(B64_FUNCTIONS) + NOT_FOUND + REDIRECTS + REQUIRE_ADMIN + CLIENT_HOSTS + BACKUP_LOGS
    + INT_ENVS + TEXT_GETTERS + BODY_ERRORS + MESSAGE_ERRORS + list(MAIL_CONNECTORS) + [
        "app.auth.turnstile:_header_value", "app.core.proxy_trust:_header_value|header_value", "app.core.db_slots:_at_art",
        "app.core.db_reports:_local", "app.cuentas.account_photo:_user", "app.modules.turnos.calendar_feed:_current_user",
        "app.modules.turnos.appointment_page_support:service_name", "app.modules.turnos.remedies:_service_name",
        "app.core.translate:_primary", "app.core.site_identity:_lang_primary", "app.modules.turnos.waitlist:_interval_min",
        "app.notifications.reminders:_interval_min"])


def _signature_cases():
    out = {}
    for entry in SIGNATURES:
        ref, _, alt = entry.partition("|")
        try:
            target = _get(ref, *([alt] if alt else []))
        except (AttributeError, ImportError):
            continue
        sig = inspect.signature(target)
        out[ref] = [[p.name, p.kind.name, repr(p.default)] for p in sig.parameters.values()]
    return out


def _record():
    out = {"json": _json_cases(), "base64url": _b64_cases(), "responses": _response_cases(),
           "require_admin": _require_admin_cases(), "mail": _mail_cases(), "client_host": _client_host_cases(),
           "errors": _error_cases(), "logs": _log_cases(), "settings": _settings_cases(),
           "legacy_names": _legacy_cases(), "header": _header_cases(), "sql": _sql_cases(),
           "session_user": _user_cases(), "service_name": _service_name_cases(), "language": _language_cases(),
           "prompts_decoder": _prompts_decoder_cases(), "signatures": _signature_cases()}
    return json.loads(json.dumps(out, sort_keys=True, ensure_ascii=False, default=repr))


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class RepeatedTailsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def test_the_same_groups_are_pinned(self):
        self.assertEqual(sorted(self.got), sorted(self.want))

    def _same(self, group):
        self.assertEqual(sorted(self.got[group]), sorted(self.want[group]), group)
        for key in sorted(self.want[group]):
            self.assertEqual(self.got[group][key], self.want[group][key], "%s / %s" % (group, key))

    def test_json_body_decoders(self):
        self._same("json")

    def test_base64url(self):
        self._same("base64url")

    def test_terminal_404_and_redirect(self):
        self._same("responses")

    def test_require_admin_call_through(self):
        self._same("require_admin")

    def test_email_transports(self):
        self._same("mail")

    def test_client_host(self):
        self._same("client_host")

    def test_status_errors(self):
        self._same("errors")

    def test_backup_log_line(self):
        self._same("logs")

    def test_settings_readers(self):
        self._same("settings")

    def test_legacy_name_fallback(self):
        self._same("legacy_names")

    def test_header_reader(self):
        self._same("header")

    def test_at_time_zone_column(self):
        self._same("sql")

    def test_session_user(self):
        self._same("session_user")

    def test_service_name(self):
        self._same("service_name")

    def test_primary_language(self):
        self._same("language")

    def test_prompts_route_body_decoder(self):
        self._same("prompts_decoder")

    def test_parameter_names_of_every_helper(self):
        self._same("signatures")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("wrote", GOLDEN)
    else:
        unittest.main()
