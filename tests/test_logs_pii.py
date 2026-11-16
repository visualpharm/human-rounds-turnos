"""test_logs_pii.py — the logs are anonymous (`core/log_pii.py`).

The installation's stdout/stderr are read by whoever runs the server (the
municipality's platform team for salud.pinamar.gob.ar), so no name, DNI,
phone, email, email subject, key or token may ever reach them. Each case
below runs a REAL logging path — the failure branches, where a provider or
the database echoes what it was given — with synthetic personal data, and
fails if any of it shows up in what was printed. Each case also checks that
the path did log, so a silenced branch cannot pass by printing nothing.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_logs_pii.py"
"""

import ast
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import log_pii  # noqa: E402

NOMBRE = "Marianela Quintanilla"
DNI = "30111222"
DNI_PUNTOS = "30.111.222"
TEL = "5492254415566"
TEL_LOCAL = "2254 41-5566"
EMAIL = "marianela.quintanilla@example.org"
KEY = "waha-key-ZX9QY8W7"
# Under 16 characters: the public-cut preflight reads a longer literal
# assigned to TOKEN as a real secret.
TOKEN = "tok_9f8e7d6c5b4"
SECRETOS = (NOMBRE, "Quintanilla", DNI, DNI_PUNTOS, TEL, TEL_LOCAL, EMAIL,
            "marianela.quintanilla", KEY, TOKEN)
# What a provider or the database echoes back when it fails.
ECO = f"rechazado {NOMBRE} dni={DNI} ({DNI_PUNTOS}) tel {TEL_LOCAL} <{EMAIL}> key {KEY}"


class LogsPiiCase(unittest.TestCase):
    def ran(self, fn, *args, **kwargs):
        """Runs `fn`, returns everything it printed (stdout + stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            fn(*args, **kwargs)
        return out.getvalue() + err.getvalue()

    def assertAnonymous(self, printed):
        self.assertTrue(printed.strip(), "the path under test logged nothing")
        for valor in SECRETOS:
            self.assertNotIn(valor, printed)


class TestMasks(unittest.TestCase):
    def test_masks_keep_only_what_correlates(self):
        self.assertEqual(log_pii.dni(DNI_PUNTOS), "*****222")
        self.assertEqual(log_pii.phone("+54 9 " + TEL_LOCAL), "*********5566")
        self.assertEqual(log_pii.email(EMAIL), "***@example.org")
        self.assertEqual(log_pii.emails(f"{EMAIL}, b@x.com"), "***@example.org, ***@x.com")
        self.assertEqual(log_pii.email(""), "")

    def test_exc_keeps_the_type_never_the_message(self):
        import urllib.error
        self.assertEqual(log_pii.exc(ValueError(ECO)), "ValueError")
        self.assertEqual(log_pii.exc(KeyError()), "KeyError")
        http = urllib.error.HTTPError("https://api.example/x", 502, ECO, {}, None)
        # An HTTPError is also a response file: Python 3.13+ warns when one is
        # left for the garbage collector (ResourceWarning), so close it.
        self.addCleanup(http.close)
        self.assertEqual(log_pii.exc(http), "HTTPError (502)")

    def test_provider_code_keeps_only_a_short_number(self):
        # El código de error de Meta (131026, «no entregable») dice qué falló;
        # cualquier otra cosa del cuerpo — un DNI, un teléfono, un texto — no.
        self.assertEqual(log_pii.provider_code(131026), "131026")
        self.assertEqual(log_pii.provider_code("132001"), "132001")
        for crudo in (DNI, TEL_LOCAL, ECO, "", None, True, 3.5, "12a"):
            self.assertEqual(log_pii.provider_code(crudo), "")

    def test_meta_template_failure_logs_the_code_not_the_body(self):
        import io
        import json
        import shutil
        import tempfile
        import urllib.error
        from contextlib import redirect_stderr
        from unittest import mock
        from app.notifications import otp_send
        datos = tempfile.mkdtemp(prefix="logs_pii_meta_")
        self.addCleanup(shutil.rmtree, datos, ignore_errors=True)
        _env.set_env(self, TURNOS_DATA_DIR=datos)   # the failed send records Meta's last error there
        cuerpo = json.dumps({"error": {"code": 131026, "message": ECO}}).encode()
        http = urllib.error.HTTPError("https://graph.example/x", 400, ECO, {},
                                      io.BytesIO(cuerpo))
        self.addCleanup(http.close)
        cfg = {"token": "tok", "phone_number_id": "1", "graph_version": "v1"}
        salida = io.StringIO()
        with mock.patch.object(otp_send, "_meta_config", return_value=cfg), \
                mock.patch("urllib.request.urlopen", side_effect=http), \
                redirect_stderr(salida):
            self.assertFalse(otp_send._enviar_meta_plantilla(
                "+54 9 " + TEL_LOCAL, "turnos_recordatorio", "es_AR", []))
        linea = salida.getvalue()
        self.assertIn("HTTPError (400) 131026", linea)
        self.assertNotIn(ECO, linea)
        self.assertNotIn(TEL_LOCAL, linea)


class TestAccessLog(unittest.TestCase):
    """uvicorn's access line carries the full request target: the panel asks
    `/api/admin/patient-summary?dni=…`, so the query must never reach it."""

    def _record(self, target):
        import logging
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                                 '%s - "%s %s HTTP/%s" %d',
                                 ("10.0.0.1:5555", "GET", target, "1.1", 200), None)

    def test_query_and_fragment_dropped_ids_masked(self):
        rec = self._record(f"/api/admin/patient-summary?dni={DNI}&medico=3")
        self.assertTrue(log_pii.AccessLogFilter().filter(rec))
        linea = rec.getMessage()
        self.assertNotIn(DNI, linea)
        self.assertIn('"GET /api/admin/patient-summary HTTP/1.1" 200', linea)
        rec = self._record(f"/api/requests/{DNI}/cancel#{TOKEN}")
        log_pii.AccessLogFilter().filter(rec)
        self.assertNotIn(DNI, rec.getMessage())
        self.assertNotIn(TOKEN, rec.getMessage())

    def test_installed_on_uvicorn_access(self):
        import logging
        log_pii.install_access_log_filter()
        log_pii.install_access_log_filter()
        filtros = [f for f in logging.getLogger("uvicorn.access").filters
                   if isinstance(f, log_pii.AccessLogFilter)]
        self.assertEqual(len(filtros), 1)


class TestNotifications(LogsPiiCase):
    def test_mailgun_unconfigured_skip(self):
        from app.notifications import mailgun
        with mock.patch.object(mailgun, "_cfg", return_value=("", "", "", True)):
            self.assertAnonymous(self.ran(mailgun.send, EMAIL, f"Tu turno, {NOMBRE}", "x"))

    def test_mailgun_send_failure(self):
        from app.notifications import mailgun
        opener = mock.Mock()
        opener.open.side_effect = OSError(ECO)
        with mock.patch.object(mailgun, "_cfg", return_value=(KEY, "mg.example", "f", True)), \
                mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertAnonymous(self.ran(mailgun.send, EMAIL, f"Tu turno, {NOMBRE}", "x"))

    def test_waha_unconfigured_failed_and_invalid(self):
        from app.notifications import otp_send
        with mock.patch.object(otp_send, "_cfg_whatsapp", return_value=("", "", "default")):
            self.assertAnonymous(self.ran(otp_send._enviar_whatsapp, TEL, "código 123456"))
        with mock.patch.object(otp_send, "_cfg_whatsapp",
                               return_value=("http://127.0.0.1:3000", KEY, "default")), \
                mock.patch("urllib.request.urlopen", side_effect=OSError(ECO)):
            self.assertAnonymous(self.ran(otp_send._enviar_whatsapp, TEL, "código 123456"))
        with mock.patch.object(otp_send, "_cfg_whatsapp",
                               return_value=("http://127.0.0.1:3000", KEY, "default")), \
                mock.patch.object(otp_send, "_chat_id", return_value="@c.us"):
            self.assertAnonymous(self.ran(otp_send._enviar_whatsapp, TEL, "código 123456"))

    def test_sms_stub(self):
        from app.notifications import otp_send
        self.assertAnonymous(self.ran(otp_send._enviar_sms, TEL, "código 123456"))

    def test_waha_media_download_never_prints_the_key(self):
        from app.modules.chat import whatsapp_chat
        from app.notifications import otp_send
        url = f"http://127.0.0.1:3000/api/files/{TEL}.jpg?token={TOKEN}"
        with mock.patch.object(otp_send, "waha_config",
                               return_value=("http://127.0.0.1:3000", KEY, "default")), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("401")):
            printed = self.ran(whatsapp_chat._descargar_media_waha, url)
        self.assertAnonymous(printed)
        self.assertIn("con X-Api-Key", printed)


class TestOps(LogsPiiCase):
    def test_disk_alert_recipient(self):
        from app.auth import admin_auth
        from app.notifications import mail
        from app.ops import housekeeping
        medida = {"libre_gb": 1.0, "total_gb": 50.0}
        with _env.patched_env({"TURNOS_TEST_SEAMS": ""}), \
                mock.patch.object(admin_auth, "owner_email", return_value=EMAIL), \
                mock.patch.object(mail, "send", return_value=True):
            self.assertAnonymous(self.ran(housekeeping._send_disk_alert_email, medida, 2.0))
        with mock.patch.object(admin_auth, "owner_email", return_value=EMAIL), \
                mock.patch.object(mail, "send", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(housekeeping._send_disk_alert_email, medida, 2.0))


class TestClinicalAi(LogsPiiCase):
    def test_chart_summary_pregeneration_failure(self):
        from app.ai import chart_summary
        from app.core import db_clinical
        with mock.patch.object(db_clinical, "upcoming_for_summaries", return_value=[(DNI, 7)]), \
                mock.patch.object(db_clinical, "summary_get", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(chart_summary.tick))

    def test_lab_analysis_persist_failure(self):
        from app.ai import lab_analysis
        from app.core import db_clinical
        fila = {"tipo_estudio": "x", "resumen": "", "tono": "normal", "sensible": False,
                "valores": [], "modelo": "m"}
        with mock.patch.object(db_clinical, "lab_analysis_upsert", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(lab_analysis._persistir_fila, DNI, "abcdef0123",
                                          "p", {"fecha": None, "sistema": "x"}, fila, {}))

    def test_health_profile_read_failure(self):
        from app.core import db_clinical
        from app.modules.intake import health_profile
        with mock.patch.object(db_clinical, "health_profile_get", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(health_profile.leer, DNI))

    def test_chat_linked_conversations_failure(self):
        from app.modules.chat import chat_agent
        usuario = {"dni": DNI, "nombre": NOMBRE, "contacto": TEL}
        with mock.patch.object(chat_agent, "_patient_key", return_value="dni:" + DNI), \
                mock.patch.object(chat_agent.chat_store, "linked_whatsapp_claves",
                                  side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(chat_agent._conversation_pairs, usuario, "web", "k"))


def _boom():
    raise ValueError(ECO)


class TestTracebacks(LogsPiiCase):
    """A traceback keeps the frames and the exception types — where it
    broke — and loses every message."""

    def _chained(self):
        try:
            try:
                _boom()
            except ValueError as inner:
                raise RuntimeError(ECO) from inner
        except RuntimeError as e:
            return e

    def test_traceback_text_frames_and_types_only(self):
        texto = log_pii.traceback_text(self._chained())
        self.assertAnonymous(texto)
        self.assertIn("_boom", texto)
        self.assertIn("ValueError", texto)
        self.assertIn("RuntimeError", texto)
        self.assertIn("Traceback (most recent call last)", texto)

    def test_sqlstate_is_the_code(self):
        class FakePgError(Exception):
            sqlstate = "23505"
        self.assertEqual(log_pii.exc(FakePgError(ECO)), "FakePgError (SQLSTATE 23505)")

    def test_print_traceback_inside_except(self):
        def path():
            try:
                _boom()
            except ValueError:
                log_pii.print_traceback()
        self.assertAnonymous(self.ran(path))

    def test_key_masks_phone_and_dni_keys(self):
        self.assertEqual(log_pii.key("tel:" + TEL), "tel:*********5566")
        self.assertEqual(log_pii.key("dni:" + DNI), "dni:*****222")
        self.assertEqual(log_pii.key("interview:42"), "interview:42")
        self.assertNotIn(TOKEN, log_pii.key(TOKEN))
        self.assertTrue(log_pii.key(TOKEN).endswith(TOKEN[-4:]))


class TestInstall(LogsPiiCase):
    """`log_pii.install()` (asgi_main.py and worker.py, before anything can
    fail): what a LIBRARY logs — uvicorn's «Exception in ASGI application»,
    a failed procrastinate job, asyncio's never-retrieved task — and every
    uncaught exception in any thread, all without a message."""

    def setUp(self):
        import logging
        import threading
        self._saved = (logging.getLogRecordFactory(), sys.excepthook, threading.excepthook)
        log_pii.install()
        log_pii.install()  # idempotent: one wrapper, not two

    def tearDown(self):
        import logging
        import threading
        factory, hook, thook = self._saved
        logging.setLogRecordFactory(factory)
        sys.excepthook = hook
        threading.excepthook = thook

    def _logged(self, emit):
        import logging
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger = logging.getLogger("uvicorn.error")
        logger.addHandler(handler)
        propagate, logger.propagate = logger.propagate, False
        try:
            emit(logger)
        finally:
            logger.removeHandler(handler)
            logger.propagate = propagate
        return buf.getvalue()

    def test_library_exception_log_loses_the_message(self):
        def emit(logger):
            try:
                _boom()
            except ValueError as e:
                logger.error("Exception in ASGI application\n", exc_info=e)
                logger.exception("job failed")
                logger.error("Task exception was never retrieved: %s", e)
                logger.error(e)
        printed = self._logged(emit)
        self.assertAnonymous(printed)
        self.assertIn("_boom", printed)
        self.assertIn("ValueError", printed)

    def test_single_wrapper(self):
        import logging
        factory = logging.getLogRecordFactory()
        self.assertTrue(getattr(factory, "_log_pii", False))
        rec = factory("x", logging.ERROR, __file__, 1, "%s", (ValueError(ECO),), None)
        self.assertEqual(rec.getMessage(), "ValueError")

    def test_uncaught_in_a_thread(self):
        import threading

        def run():
            t = threading.Thread(target=_boom, name="barrido")
            t.start()
            t.join()
        printed = self.ran(run)
        self.assertAnonymous(printed)
        self.assertIn("barrido", printed)

    def test_uncaught_in_the_main_thread(self):
        try:
            _boom()
        except ValueError as e:
            printed = self.ran(sys.excepthook, type(e), e, e.__traceback__)
        self.assertAnonymous(printed)
        self.assertIn("ValueError", printed)


class TestTypicalFailurePaths(LogsPiiCase):
    """The everyday failure branches: the database or a provider fails and
    its error echoes the patient's data back."""

    def test_mail_router_send_failure(self):
        from app.notifications import mail
        adapter = mock.Mock()
        adapter.send.side_effect = RuntimeError(ECO)
        with mock.patch.object(mail, "transport", return_value=(adapter, "mailgun", "default")):
            self.assertAnonymous(self.ran(mail.send, EMAIL, f"Tu turno, {NOMBRE}", "x"))
        with mock.patch.object(mail, "transport", return_value=(None, None, "off")):
            self.assertAnonymous(self.ran(mail.send, EMAIL, f"Tu turno, {NOMBRE}", "x"))

    def test_mailjet_send_failure(self):
        from app.connectors.mailjet import mailjet
        with mock.patch.object(mailjet.mail, "enabled", return_value=True), \
                mock.patch.object(mailjet, "missing_config", return_value=[]), \
                mock.patch.object(mailjet, "_auth_header", return_value={}), \
                mock.patch.object(mailjet, "_message", return_value={}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError(ECO)):
            self.assertAnonymous(self.ran(mailjet.send, EMAIL, f"Tu turno, {NOMBRE}", "x"))

    def test_mailjet_unconfigured_skip_never_prints_the_subject(self):
        from app.connectors.mailjet import mailjet
        with mock.patch.object(mailjet.mail, "enabled", return_value=True), \
                mock.patch.object(mailjet, "missing_config", return_value=["MAILJET_API_KEY"]):
            self.assertAnonymous(self.ran(mailjet.send, EMAIL, f"Tu turno, {NOMBRE}", "x"))

    def test_reminders_db_failures(self):
        from datetime import datetime, timezone
        from app.core import db_booking
        from app.notifications import reminders
        mediodia = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)  # 12:00 ART
        with mock.patch.object(db_booking, "booking_requests_to_remind", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(reminders.tick, mediodia))
        fila = {"id": 7, "dni": DNI, "nombre": NOMBRE, "email": EMAIL}
        with mock.patch.object(db_booking, "booking_requests_to_remind", return_value=[fila]), \
                mock.patch.object(db_booking, "claim_reminder", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(reminders.tick, mediodia))

    def test_chat_history_failure_masks_the_whatsapp_key(self):
        from app.modules.chat import chat_agent
        with mock.patch.object(chat_agent, "_conversation_pairs", side_effect=RuntimeError(ECO)):
            printed = self.ran(chat_agent._load_history,
                               {"dni": DNI, "nombre": NOMBRE}, "whatsapp", "tel:" + TEL)
        self.assertAnonymous(printed)
        self.assertIn("tel:*********5566", printed)

    def test_interview_history_failures(self):
        from app.core import db_chat
        from app.modules.intake import interview_agent
        with mock.patch.object(db_chat, "chat_history", side_effect=RuntimeError(ECO)), \
                mock.patch.object(db_chat, "chat_save_message", side_effect=RuntimeError(ECO)):
            self.assertAnonymous(self.ran(interview_agent._load_history, "dni:" + DNI))
            self.assertAnonymous(self.ran(interview_agent._save, "dni:" + DNI, {"content": ECO}))

    def test_housekeeping_purge_failure(self):
        from app.ops import housekeeping
        from app.core import db_accounts
        from app.core import db_slots
        from app.core import db_analytics
        with mock.patch.object(db_accounts, "purge_idle_sessions", side_effect=RuntimeError(ECO)), \
                mock.patch.object(db_analytics, "purge_old_searches", return_value=0), \
                mock.patch.object(db_analytics, "purge_old_login_panel_events", return_value=0), \
                mock.patch.object(db_slots, "purge_summarized_chat_messages", return_value=0), \
                mock.patch.object(db_analytics, "purge_old_alert_claims", return_value=0), \
                mock.patch("app.modules.turnos.study_store.purge", return_value=0):
            printed = self.ran(housekeeping.tick_purges)
        self.assertAnonymous(printed)
        self.assertIn("sessions: fallo (RuntimeError)", printed)


class TestStaticGate(unittest.TestCase):
    """No log line of the SERVER code (everything but scripts/, vendor/ and
    the tests) interpolates a caught exception raw — `{e!r}`, `str(e)`,
    `%s` with `e` — nor calls `traceback.print_exc()`/`logger.exception`:
    through `log_pii.exc()`/`log_pii.print_traceback()` or not at all.
    manage.py is the operator's own terminal and prints its own
    validation errors (`str(exc)` of a ValueError it raised)."""

    SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "tests", "__pycache__", ".claude",
                 "stories", "storybook-static", "scripts", "vendor", "tools"}
    SKIP_FILES = {os.path.join("app", "manage.py"), os.path.join("app", "core", "log_pii.py")}
    LOG_NAMES = {"print", "_log", "log"}
    LOG_ATTRS = {"warning", "warn", "error", "info", "debug", "exception", "critical", "write"}
    SAFE_CALLS = {"exc", "type", "isinstance", "print_traceback", "traceback_text",
                  "provider_code"}
    SAFE_ATTRS = {"code", "status", "errno", "status_code", "strerror", "__name__", "sqlstate"}

    def _is_log(self, call):
        f = call.func
        if isinstance(f, ast.Name):
            return f.id in self.LOG_NAMES or f.id.startswith("_log")
        return isinstance(f, ast.Attribute) and f.attr in self.LOG_ATTRS

    def _raw_use(self, node, names):
        safe = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                fn = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if fn in self.SAFE_CALLS:
                    safe.update(id(a) for a in ast.walk(n))
            if isinstance(n, ast.Attribute) and n.attr in self.SAFE_ATTRS:
                safe.update(id(a) for a in ast.walk(n))
        return any(isinstance(n, ast.Name) and n.id in names and id(n) not in safe
                   for n in ast.walk(node))

    def test_no_raw_exception_text_in_server_logs(self):
        malos = []
        for dp, dns, fns in os.walk(ROOT):
            dns[:] = [d for d in dns if d not in self.SKIP_DIRS]
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), ROOT)
                if not fn.endswith(".py") or rel in self.SKIP_FILES:
                    continue
                with open(os.path.join(dp, fn), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        f = node.func
                        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                                and f.value.id == "traceback"):
                            malos.append(f"{rel}:{node.lineno} traceback.{f.attr}")
                        if any(k.arg == "exc_info" for k in node.keywords) or (
                                isinstance(f, ast.Attribute) and f.attr == "exception"
                                and node.args):
                            malos.append(f"{rel}:{node.lineno} exc_info/exception")
                    if isinstance(node, ast.ExceptHandler) and node.name:
                        names = {node.name}
                        for st in ast.walk(node):
                            if isinstance(st, ast.Assign) and self._raw_use(st.value, names):
                                names.update(t.id for t in st.targets if isinstance(t, ast.Name))
                        for st in ast.walk(node):
                            if isinstance(st, ast.Call) and self._is_log(st) and (
                                    any(self._raw_use(a, names) for a in st.args)
                                    or any(self._raw_use(k.value, names) for k in st.keywords)):
                                malos.append(f"{rel}:{st.lineno} {node.name}")
        self.assertEqual(sorted(set(malos)), [])



if __name__ == "__main__":
    unittest.main()
