"""test_small_helpers_contract.py - six small helpers written twice (or five times) say what they said (2026-10-28).

Third pass of the architecture audit: the exact-body groups the second pass left. Each group is a function
(or a class) spelled out in two or more files with the same body; before one copy replaces the others the
answers of every copy are written down here, so a difference between the copies would show as a red test
now, not as a patient-visible change later:

  - the appointment pages' side reads of `appointment_confirm` and `appointment_offer` (service name,
    centre name, friendly time, the `data-token` attribute);
  - the doctor's specialty of `intake.analysis` and `intake.interview_agent`;
  - the magic-byte sniffer of `account_photo` (and, apart, the one of `core.photo_guard`);
  - the JSON response both authentication routers build from an `AuthResult`, and the one the body
    contract builds for them;
  - `chain_alive()` of the two self-rescheduling queue chains;
  - the `(status, body)` errors the intake, prompt and summary domains raise for their routers.

Nothing here knows where a body lives, so the file stays valid when the copies become one. The recorded
file `tests/golden/small_helpers.json` is written with the code that existed before and is not touched by
the change that joins them. Re-record only from code whose answers are known to be right:
`python3 tests/test_small_helpers_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_small_helpers_contract.py
"""
import importlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "small_helpers.json")


def _try(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - the exception type is part of what is pinned
        return "raises " + type(exc).__name__


def _raiser(message="down"):
    def fail(*_args, **_kwargs):
        raise RuntimeError(message)
    return fail


# ---- appointment pages ------------------------------------------------------------------------------

APPOINTMENT_MODULES = ["app.modules.turnos.appointment_confirm", "app.modules.turnos.appointment_offer"]
SERVICE_IDS = [None, "", 0, "pediatria", 7]
SLUGS = [None, "", "hospital", 7, "caps-1", "nope"]
CENTER_LISTS = {
    "match_slug": [{"slug": "hospital", "name": "Hospital de Pinamar"}],
    "match_id": [{"id": 7, "name": "Centro Siete"}, {"slug": "caps-1", "id": "x", "name": "CAPS Uno"}],
    "no_name": [{"slug": "hospital"}, {"slug": "caps-1", "name": None}],
    "first_wins": [{"slug": "hospital", "name": "Uno"}, {"slug": "hospital", "name": "Dos"}],
    "junk": ["hospital", None, 3, {"slug": "hospital", "name": "Tras la basura"}],
    "empty": [],
    "none": None,
}
INSTANTS = [None, "", 0, "2026-10-28 09:30", datetime(2026, 10, 28, 12, 30, tzinfo=timezone.utc)]
TOKENS = ["abc", "", 'a"b', "<x>&'", "ñandú", None]


def _appointment_cases():
    out = {}
    for name in APPOINTMENT_MODULES:
        mod = importlib.import_module(name)
        case = {"service_name": {}, "center_name": {}, "friendly": {}, "data_token_attribute": {}}
        with mock.patch("app.modules.turnos.booking_requests._service_name", lambda sid: "Servicio %s" % sid):
            for sid in SERVICE_IDS:
                case["service_name"][repr(sid)] = _try(mod._service_name, sid)
        with mock.patch("app.modules.turnos.booking_requests._service_name", _raiser()):
            for sid in SERVICE_IDS:
                case["service_name"]["raises:" + repr(sid)] = _try(mod._service_name, sid)
        for label, centers in CENTER_LISTS.items():
            with mock.patch("app.patient_web.catalog.centers", lambda centers=centers: centers):
                for slug in SLUGS:
                    case["center_name"]["%s:%r" % (label, slug)] = _try(mod._center_name, slug)
        with mock.patch("app.patient_web.catalog.centers", _raiser()):
            case["center_name"]["raises"] = _try(mod._center_name, "hospital")
        with mock.patch("app.notifications.send._friendly_appointment_time", lambda dt: "el %s" % (dt,)):
            for dt in INSTANTS:
                case["friendly"][repr(dt)] = _try(mod._friendly_appointment, dt)
        with mock.patch("app.notifications.send._friendly_appointment_time", _raiser()):
            for dt in INSTANTS:
                case["friendly"]["raises:" + repr(dt)] = _try(mod._friendly_appointment, dt)
        for token in TOKENS:
            case["data_token_attribute"][repr(token)] = _try(mod._data_token_attribute, token)
        out[name] = case
    return out


# ---- the doctor's specialty --------------------------------------------------------------------------

ESPECIALIDAD_MODULES = ["app.modules.intake.analysis", "app.modules.intake.interview_agent"]
INTERVIEWS = [None, {}, {"doctor_id": None}, {"doctor_id": 0}, {"doctor_id": 7}, {"doctor_id": "7"},
              {"doctor_id": "ghost"}]
DOCTOR_LISTS = {
    "plain": [{"id": 7, "especialidad": "Cardiología"}],
    "padded": [{"id": "7", "especialidad": "  Pediatría \n"}],
    "blank": [{"id": 7, "especialidad": "   "}],
    "missing": [{"id": 7}],
    "none_value": [{"id": 7, "especialidad": None}],
    "first_wins": [{"id": 7, "especialidad": "Uno"}, {"id": 7, "especialidad": "Dos"}],
    "other_only": [{"id": 9, "especialidad": "Otra"}],
    "junk": [None, {"id": 7, "especialidad": "Tras la basura"}],
    "empty": [],
    "none": None,
}


def _especialidad_cases():
    out = {}
    for name in ESPECIALIDAD_MODULES:
        mod = importlib.import_module(name)
        case = {}
        for label, doctors in DOCTOR_LISTS.items():
            with mock.patch("app.patient_web.catalog.doctors", lambda doctors=doctors: doctors):
                for interview in INTERVIEWS:
                    case["%s:%r" % (label, interview)] = _try(mod._especialidad_for, interview)
        with mock.patch("app.patient_web.catalog.doctors", _raiser()):
            for interview in INTERVIEWS:
                case["raises:%r" % (interview,)] = _try(mod._especialidad_for, interview)
        out[name] = case
    return out


# ---- magic bytes -------------------------------------------------------------------------------------

SNIFF_INPUTS = {
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png_short": b"\x89PNG\r\n\x1a", "jpg": b"\xff\xd8\xff\xe0abc",
    "jpg_short": b"\xff\xd8", "webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ", "riff_not_webp": b"RIFF\x00\x00\x00\x00WAVEfmt ",
    "heic": b"\x00\x00\x00\x18ftypheic\x00\x00", "gif": b"GIF89a....", "pdf": b"%PDF-1.4", "svg": b"<svg xmlns=''>",
    "empty": b"", "text": b"hola", "bytearray_png": bytearray(b"\x89PNG\r\n\x1a\n"), "none": None, "str": "PNG",
}


def _sniff_cases():
    from app.core import photo_guard
    from app.cuentas import account_photo
    return {
        "app.cuentas.account_photo._sniff": {k: _try(account_photo._sniff, v) for k, v in SNIFF_INPUTS.items()},
        "app.core.photo_guard.sniff": {k: _try(photo_guard.sniff, v) for k, v in SNIFF_INPUTS.items()},
    }


# ---- the response of an AuthResult ----------------------------------------------------------------------

AUTH_RESULTS = [(200, {"ok": True}, None), (200, {"ok": True, "usuario": "Ñandú"}, "sid=abc; Path=/; HttpOnly"),
                (401, {"error": "credenciales"}, ""), (429, {"error": "demasiados_intentos"}, None),
                (200, {"n": [1, 2.5, None], "t": "¿Cómo está? — «bien»"}, "x=1")]


def _describe(response):
    headers = sorted([k.decode("latin-1"), v.decode("latin-1")] for k, v in response.raw_headers)
    return {"status": response.status_code, "headers": headers, "body": response.body.decode("utf-8"),
            "background": response.background is not None}


def _auth_response_cases():
    from app.auth import admin_auth, api_models
    builders = {"app.auth.admin_auth_router._response": importlib.import_module("app.auth.admin_auth_router")._response,
                "app.auth.passkeys_router._response": importlib.import_module("app.auth.passkeys_router")._response}
    out = {}
    for name, fn in builders.items():
        out[name] = {"%d/%s" % (i, status): _describe(fn(admin_auth.AuthResult(status, body, cookie)))
                     for i, (status, body, cookie) in enumerate(AUTH_RESULTS)}
    out["app.auth.api_models._auth_response"] = {
        "%d/%s" % (i, status): _describe(api_models._auth_response(None, body, status))
        for i, (status, body, _cookie) in enumerate(AUTH_RESULTS)}
    return out


# ---- chain_alive -----------------------------------------------------------------------------------------

CHAIN_MODULES = ["app.modules.turnos.hsi_journal_queue", "app.ops.data_backups_queue"]


class _Cursor:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def __enter__(self):
        self.log.append(["cursor_open", self.name])
        return self

    def __exit__(self, *exc):
        self.log.append(["cursor_close", self.name])
        return False


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        self.log.append(["connect_open"])
        return self

    def __exit__(self, *exc):
        self.log.append(["connect_close"])
        return False

    def cursor(self):
        return _Cursor("own", self.log)


def _chain_alive_cases():
    out = {}
    for name in CHAIN_MODULES:
        mod = importlib.import_module(name)
        case = {}
        for answer in (True, False):
            log = []

            def task_chain_alive(cur, task_name, log=log, answer=answer):
                log.append(["task_chain_alive", cur.name if hasattr(cur, "name") else cur, task_name])
                return answer

            with mock.patch("app.core.queue_infra.task_chain_alive", task_chain_alive), \
                    mock.patch("app.core.db._connect", lambda log=log: _Conn(log)):
                case["own/%s" % answer] = [_try(mod.chain_alive), list(log)]
                log2 = []
                log.clear()
                case["given/%s" % answer] = [mod.chain_alive(_Cursor("given", log2)), log + log2]
        out[name] = case
    return out


# ---- (status, body) errors -------------------------------------------------------------------------------

ERROR_CLASSES = [("app.ai.chart_summary", "PatientSummaryError"), ("app.ai.prompts", "PromptSaveError"),
                 ("app.modules.intake.consult_admin", "ConsultAdminError"),
                 ("app.modules.intake.intake_admin", "IntakeAdminError"),
                 ("app.modules.intake.interview_patient", "InterviewApiError")]
ERROR_BODIES = [{"error": "falta medico"}, {"error": None}, {"otro": 1}, {}, "texto", None, 5, ["a"]]


def _error_cases():
    out = {}
    for module, cls_name in ERROR_CLASSES:
        cls = getattr(importlib.import_module(module), cls_name)
        case = {"is_exception": issubclass(cls, Exception), "module": cls.__module__, "name": cls.__qualname__,
                "caught_as_exception": True}
        for body in ERROR_BODIES:
            exc = cls(400, body)
            case[repr(body)] = {"status": exc.status, "body": exc.body, "str": str(exc), "args": repr(exc.args)}
        try:
            raise cls(503, {"error": "x"})
        except Exception as exc:  # noqa: BLE001
            case["raised"] = [type(exc).__name__, exc.status, exc.body]
        out["%s.%s" % (module, cls_name)] = case
    return out


def _record():
    out = {"appointment": _appointment_cases(), "especialidad": _especialidad_cases(), "sniff": _sniff_cases(),
           "auth_response": _auth_response_cases(), "chain_alive": _chain_alive_cases(),
           "errors": _error_cases()}
    return json.loads(json.dumps(out, sort_keys=True, ensure_ascii=False, default=repr))


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class SmallHelpersContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def _same(self, group):
        self.assertEqual(sorted(self.got[group]), sorted(self.want[group]))
        for name in sorted(self.want[group]):
            self.assertEqual(self.got[group][name], self.want[group][name], name)

    def test_appointment_page_side_reads(self):
        self._same("appointment")

    def test_the_doctors_specialty(self):
        self._same("especialidad")

    def test_magic_bytes(self):
        self._same("sniff")

    def test_the_authentication_response(self):
        self._same("auth_response")

    def test_chain_alive(self):
        self._same("chain_alive")

    def test_status_body_errors(self):
        self._same("errors")

    def test_what_is_pinned_apart_stays_apart(self):
        sniff = self.got["sniff"]
        # the Gravatar edge does not take HEIC; the shared sniffer of uploads does
        self.assertEqual(sniff["app.cuentas.account_photo._sniff"]["heic"], None)
        self.assertEqual(sniff["app.core.photo_guard.sniff"]["heic"], "heic")
        # the two queue chains look at their own task
        alive = self.got["chain_alive"]
        tasks = {n: alive[n]["own/True"][1][2][2] for n in CHAIN_MODULES}
        self.assertEqual(len(set(tasks.values())), 2)


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("wrote", GOLDEN)
    else:
        unittest.main()
