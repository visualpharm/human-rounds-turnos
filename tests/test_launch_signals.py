"""Launch signals (TASK-4, 2026-10-30): closed failure codes, hourly counters, the daily digest and Sentry Crons
check-ins — everything an installation whose logs and database nobody on the team can read tells its own
error tracker. Contract: `core/launch_signals.py`, `ops/launch_digest.py`.

What is held here:

* INERT by default — with `TURNOS_SENTRY_SIGNALS` unset, or listed but no DSN, nothing is imported, written or
  sent (the default installation behaves as before this change);
* nothing personal leaves — the digest, the failure events and the check-ins are rebuilt from allowlists, and
  the real SDK's envelope, with real patient and staff rows in the database, holds none of it;
* the digest numbers are the right numbers, a section that cannot be read costs only its own keys, and two
  processes never send two digests in a day;
* every swallowed failure of the list is wired at its call site (and every literal at a call site is in the list).
"""

import asyncio
import json
import os
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config, db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import error_tracking  # noqa: E402
from app.core import launch_signals  # noqa: E402
import _dbtest  # noqa: E402

_DSN = "https://public@fake.invalid/1"

# Everything a patient or a staff member could carry; none of it may reach the error tracker.
_DNI, _FIRST, _LAST = "24132941", "Nora", "Dominguez Quiroga"
_PHONE, _EMAIL = "2254491670", "nora.dominguez@example.com"
_STAFF, _STAFF_MAIL = "recepcion.lucia", "lucia.herrera@example.com"
_PII = (_DNI, _FIRST, _LAST, "Quiroga", _PHONE, "491670", _EMAIL, _STAFF, _STAFF_MAIL, "lucia", "Herrera")

_db = _dbtest.PerModuleDb("sana_turnos_test_launch_signals", seed=lambda: db_operators.operators_list())
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    config.ENV_PATH = ""
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sdk_capturing_envelopes():
    """The real Sentry SDK initialised by `error_tracking.init()`, its network replaced by a list of the
    envelopes that WOULD leave. Returns (sent, undo)."""
    import sentry_sdk
    from sentry_sdk.transport import Transport
    sent = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            sent.append(envelope.serialize().decode("utf-8", "replace"))

    real_init = sentry_sdk.init
    with patch.object(sentry_sdk, "init", lambda **kw: real_init(transport=Capture, **kw)):
        assert error_tracking.init()
    return sent, lambda: sentry_sdk.init()


class _Sdk(unittest.TestCase):
    """A real SDK with a capture transport, switched on with every feature."""

    def setUp(self):
        try:
            import sentry_sdk  # noqa: F401
        except ImportError:
            self.skipTest("sentry-sdk not installed in this checkout")
        _env.set_env(self, TURNOS_SENTRY_DSN=_DSN, TURNOS_SENTRY_SIGNALS="all")
        self.sent, undo = _sdk_capturing_envelopes()
        self.addCleanup(undo)
        launch_signals._last_event_at.clear()
        self.addCleanup(launch_signals._last_event_at.clear)

    def payload(self):
        import sentry_sdk
        sentry_sdk.flush()
        return "\n".join(self.sent)


# ---- 1. inert by default -----------------------------------------------------------------------

class TestInertByDefault(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS=None, TURNOS_SENTRY_DSN=None)
        sys.modules.pop("sentry_sdk", None)

    def test_nothing_is_on(self):
        self.assertEqual(launch_signals.features(), frozenset())
        self.assertFalse(launch_signals.any_active())
        for feature in launch_signals.FEATURES:
            self.assertFalse(launch_signals.active(feature))

    def test_no_call_imports_the_sdk_or_touches_the_database(self):
        with patch.object(db, "_connect", side_effect=AssertionError("database touched")):
            launch_signals.count("mail_sent")
            launch_signals.handled("mail_failed")
            self.assertFalse(launch_signals.send_digest({"requests_created": 1}))
            self.assertFalse(launch_signals.init_sentry())
            with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
                pass
            self.assertIsNone(launch_signals.checkin("turnos-worker", ("interval", 5), 1, 1, "ok"))
        self.assertNotIn("sentry_sdk", sys.modules)

    def test_listed_without_a_dsn_is_still_inert(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="all", TURNOS_SENTRY_DSN=None)
        self.assertEqual(launch_signals.features(), frozenset(launch_signals.FEATURES))
        self.assertFalse(launch_signals.any_active())
        with patch.object(db, "_connect", side_effect=AssertionError("database touched")):
            launch_signals.count("mail_sent")
            launch_signals.handled("whatsapp_failed")
        self.assertNotIn("sentry_sdk", sys.modules)

    def test_the_list_is_parsed_loosely_and_unknown_names_do_nothing(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS=" Failures ; DIGEST,nonsense ")
        self.assertEqual(launch_signals.features(), frozenset({"failures", "digest"}))
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="nonsense")
        self.assertEqual(launch_signals.features(), frozenset())
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="crons")
        self.assertEqual(launch_signals.features(), frozenset({"crons"}))

    def test_the_default_worker_and_web_start_nothing(self):
        from app.ops import launch_digest
        self.assertFalse(launch_digest.start())
        self.assertEqual(launch_digest.tick(), {"skipped": "off"})

    def test_a_default_install_gains_no_required_setting(self):
        # The one new setting is optional and absent from every default; the schema gains one table.
        self.assertNotIn("TURNOS_SENTRY_SIGNALS", os.environ)


# ---- 2. closed failure codes -------------------------------------------------------------------

class TestFailureCodes(_Sdk):
    def test_a_code_becomes_one_event_whose_message_is_the_code(self):
        launch_signals.handled("mail_failed")
        body = self.payload()
        self.assertIn('"message":"mail_failed"', body.replace(" ", ""))
        self.assertIn("failure_code", body)

    def test_a_code_outside_the_list_is_dropped(self):
        launch_signals.handled("patient 24132941 could not be mailed")
        launch_signals.handled("")
        launch_signals.handled(None)
        self.assertEqual(self.payload(), "")

    def test_one_event_per_code_per_window_but_every_occurrence_is_counted(self):
        with patch.object(launch_signals, "count") as counted:
            launch_signals.handled("whatsapp_failed")
            launch_signals.handled("whatsapp_failed")
            launch_signals.handled("mail_failed")
        self.assertEqual(counted.call_count, 3)
        self.assertEqual(self.payload().count('"failure_code"'), 2, "one for whatsapp_failed, one for mail_failed")
        later = time.monotonic() + launch_signals._FAILURE_EVENT_EVERY_S + 1
        with patch.object(launch_signals.time, "monotonic", return_value=later), patch.object(launch_signals, "count"):
            launch_signals.handled("whatsapp_failed")
        self.assertEqual(self.payload().count('"failure_code"'), 3, "the window passed: sent again")

    def test_failures_off_sends_no_event_but_the_counter_still_counts(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="digest")
        with patch.object(launch_signals, "count") as counted:
            launch_signals.handled("mail_failed")
        counted.assert_called_once_with("mail_failed")
        self.assertEqual(self.payload(), "")

    def test_a_broken_sdk_never_raises(self):
        import sentry_sdk
        with patch.object(sentry_sdk, "capture_event", side_effect=RuntimeError("boom")), \
                patch.object(launch_signals, "count"):
            launch_signals.handled("mail_failed")


class TestEveryCodeIsWired(unittest.TestCase):
    """Static pins: the list and the call sites cannot drift apart."""

    _CALL = re.compile(r"launch_signals\.(handled|count)\(\s*[\"']([a-z_]+)[\"']")

    def _sites(self):
        found = {}
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "tests", "docs", ".claude", "vendor"}]
            for name in files:
                if not name.endswith(".py") or name == "launch_signals.py":
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    for kind, code in self._CALL.findall(fh.read()):
                        found.setdefault((kind, code), []).append(os.path.relpath(path, ROOT))
        return found

    def test_every_listed_code_has_a_call_site_of_the_right_kind(self):
        sites = self._sites()
        for code in launch_signals.FAILURE_CODES:
            self.assertIn(("handled", code), sites, f"{code} is listed but nothing reports it")
        for code in launch_signals.COUNT_CODES:
            self.assertIn(("count", code), sites, f"{code} is listed but nothing counts it")

    def test_every_literal_at_a_call_site_is_in_the_list(self):
        for (kind, code), files in self._sites().items():
            allowed = launch_signals.FAILURE_CODES if kind == "handled" else launch_signals.COUNT_CODES
            self.assertIn(code, allowed, f"{kind}({code!r}) in {files} is not a closed code")

    def test_digest_keys_cover_every_counter(self):
        for code in launch_signals.ALL_CODES:
            self.assertIn(f"n_{code}", launch_signals.DIGEST_KEYS)

    def test_codes_are_plain_words(self):
        for code in launch_signals.ALL_CODES + launch_signals.DIGEST_KEYS:
            self.assertRegex(code, r"^[a-z][a-z0-9_]*$")


# ---- 3. what leaves: the allowlists ------------------------------------------------------------

class TestScrubbing(unittest.TestCase):
    def test_a_tag_other_than_a_listed_failure_code_is_dropped(self):
        for tags in ({"failure_code": "24132941"}, {"failure_code": "mail failed for nora"},
                     {"paciente": _FIRST}, {"failure_code": ["mail_failed"]}, "nora", None):
            out = error_tracking.scrub_event({"level": "warning", "message": "mail_failed", "tags": tags})
            self.assertNotIn("tags", out, tags)
        out = error_tracking.scrub_event({"level": "warning", "message": "mail_failed",
                                          "tags": {"failure_code": "mail_failed", "paciente": _FIRST}})
        self.assertEqual(out["tags"], {"failure_code": "mail_failed"})

    def test_a_launch_signal_event_carries_no_breadcrumbs_but_an_error_event_keeps_its_own(self):
        crumbs = {"values": [{"category": "query", "message": "SELECT 1"}]}
        signal = error_tracking.scrub_event({"level": "info", "logger": "launch_signals", "message": "daily digest",
                                             "breadcrumbs": crumbs})
        self.assertNotIn("breadcrumbs", signal)
        error = error_tracking.scrub_event({"level": "error", "logger": "x", "message": "boom", "breadcrumbs": crumbs})
        self.assertEqual([c["category"] for c in error["breadcrumbs"]["values"]], ["query"])

    def test_the_digest_context_keeps_closed_keys_with_integer_values_only(self):
        dirty = {"requests_created": 4, "queue_todo": True, "requests_approved": "3", "n_mail_sent": 2.5,
                 _DNI: 5, "paciente": _FIRST, "requests_rejected": _FIRST, "hsi_outbox_error": 0}
        out = error_tracking.scrub_event({"level": "info", "message": "daily digest",
                                          "contexts": {"launch_digest": dirty, "custom": {"dni": _DNI}}})
        self.assertEqual(out["contexts"], {"launch_digest": {"requests_created": 4, "hsi_outbox_error": 0}})
        self.assertNotIn(_DNI, json.dumps(out))

    def test_a_check_in_keeps_slug_status_numbers_and_schedule_only(self):
        dirty = {"type": "check_in", "monitor_slug": "turnos-worker", "check_in_id": "abc123", "status": "ok",
                 "duration": 1.5, "environment": "salud", "release": "a97f8118",
                 "message": f"DNI {_DNI}", "contexts": {"x": _DNI}, "tags": {"dni": _DNI},
                 "monitor_config": {"schedule": {"type": "crontab", "value": "*/5 * * * *", "note": _FIRST},
                                    "timezone": "UTC", "checkin_margin": 10, "max_runtime": 4, "name": _FIRST,
                                    "failure_issue_threshold": 1, "recovery_threshold": 1}}
        out = error_tracking.scrub_event(dirty)
        self.assertEqual(out["monitor_config"]["schedule"], {"type": "crontab", "value": "*/5 * * * *"})
        self.assertNotIn("message", out)
        self.assertNotIn("contexts", out)
        self.assertNotIn("tags", out)
        self.assertNotIn(_DNI, json.dumps(out))
        self.assertNotIn(_FIRST, json.dumps(out))

    def test_check_in_configuration_keeps_only_numbers_utc_and_a_plain_schedule(self):
        base = {"type": "check_in", "monitor_slug": "turnos-worker", "check_in_id": "x", "status": "ok"}
        dirty = {"monitor_config": {
            "timezone": "America/Argentina/Buenos_Aires", "checkin_margin": "Nora Dominguez", "max_runtime": True,
            "failure_issue_threshold": 1, "recovery_threshold": 2.5,
            "schedule": {"type": "crontab", "value": f"nora {_DNI} dominguez"}}}
        out = error_tracking.scrub_event({**base, **dirty})
        self.assertEqual(out["monitor_config"], {"failure_issue_threshold": 1})
        out = error_tracking.scrub_event({**base, "monitor_config": {
            "schedule": {"type": "interval", "value": 30, "unit": "day"}}})
        self.assertEqual(out["monitor_config"], {})
        out = error_tracking.scrub_event({**base, "monitor_config": {
            "schedule": {"type": "interval", "value": 360, "unit": "minute"}, "timezone": "UTC", "checkin_margin": 5}})
        self.assertEqual(out["monitor_config"], {"checkin_margin": 5, "timezone": "UTC",
                                                 "schedule": {"type": "interval", "value": 360, "unit": "minute"}})

    def test_a_check_in_with_a_free_text_slug_or_status_is_dropped_whole(self):
        base = {"type": "check_in", "monitor_slug": "turnos-worker", "check_in_id": "x", "status": "ok"}
        self.assertIsNotNone(error_tracking.scrub_event(dict(base)))
        for patch_ in ({"monitor_slug": f"worker {_DNI}"}, {"monitor_slug": "Turnos Worker"},
                       {"monitor_slug": "x" * 80}, {"monitor_slug": None}, {"status": "Nora Dominguez"},
                       {"status": None}):
            self.assertIsNone(error_tracking.scrub_event({**base, **patch_}), patch_)

    def test_send_digest_drops_everything_but_closed_integer_keys(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="digest", TURNOS_SENTRY_DSN=_DSN)
        import sentry_sdk
        with patch.object(sentry_sdk, "capture_event", return_value="id") as capture:
            self.assertTrue(launch_signals.send_digest(
                {"requests_created": 3, "queue_todo": True, "paciente": _FIRST, _DNI: 1, "requests_approved": "7"}))
        digest = capture.call_args.args[0]["contexts"]["launch_digest"]
        self.assertEqual(digest, {"requests_created": 3})


class TestScrubbingEndToEnd(_Sdk):
    """The real SDK, with data that looks like patients in scope: what would leave holds none of it."""

    def test_digest_failure_and_check_in_envelopes_hold_numbers_and_codes_only(self):
        # None of it can travel through these functions' own parameters: they take a closed code or numbers.
        numbers = {key: 7 for key in launch_signals.DIGEST_KEYS}
        numbers["requests_pending"] = 11
        launch_signals.send_digest(numbers)
        launch_signals.handled("mail_failed")
        with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
            pass
        body = self.payload()
        self.assertIn("requests_pending", body)
        self.assertIn("monitor_slug", body)
        for secret in _PII:
            self.assertNotIn(secret, body)


# ---- 4. Sentry Crons ---------------------------------------------------------------------------

class TestChecks(_Sdk):
    def test_a_clean_run_is_started_then_ok_with_the_same_id(self):
        with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
            pass
        sentry_events = [json.loads(line) for line in self.payload().splitlines()
                         if '"monitor_slug"' in line]
        self.assertEqual([e["status"] for e in sentry_events], ["in_progress", "ok"])
        self.assertEqual(sentry_events[0]["check_in_id"], sentry_events[1]["check_in_id"])
        self.assertEqual(sentry_events[0]["monitor_slug"], "turnos-worker")
        self.assertEqual(sentry_events[1]["monitor_config"]["schedule"], {"type": "crontab", "value": "*/5 * * * *"})
        self.assertGreaterEqual(sentry_events[1]["duration"], 0)

    def test_a_failing_run_reports_error_and_still_raises(self):
        with self.assertRaises(ZeroDivisionError):
            with launch_signals.monitored("turnos-backup-data", ("interval", 360), 210, 180):
                1 / 0
        events = [json.loads(line) for line in self.payload().splitlines() if '"monitor_slug"' in line]
        self.assertEqual([e["status"] for e in events], ["in_progress", "error"])
        self.assertEqual(events[1]["monitor_config"]["schedule"], {"type": "interval", "value": 360, "unit": "minute"})

    def test_crons_off_sends_no_check_in(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="failures,digest")
        with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
            pass
        self.assertEqual(self.payload(), "")

    def test_a_broken_sdk_never_breaks_the_job(self):
        from sentry_sdk import crons
        with patch.object(crons, "capture_checkin", side_effect=RuntimeError("boom")):
            with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
                ran = True
        self.assertTrue(ran)


class _FakeApp:
    """Just enough of a procrastinate App for registration: decorators that return the function, and a `tasks`
    registry (empty by default: looking a task up in it fails like a lost defer)."""

    def __init__(self, tasks=None):
        self.tasks = {} if tasks is None else tasks

    def periodic(self, **_kw):
        return lambda fn: fn

    def task(self, **_kw):
        return lambda fn: fn


class _Recorder:
    """A stand-in for `launch_signals.monitored` that records enter/exit."""

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, slug, schedule, margin, max_runtime):
        self.calls.append(("monitored", slug, schedule, margin, max_runtime))
        return self

    def __enter__(self):
        self.calls.append(("enter",))

    def __exit__(self, exc_type, *_):
        self.calls.append(("exit", exc_type.__name__ if exc_type else None))
        return False


class TestMonitorsAreWired(unittest.TestCase):
    def test_a_periodic_sweep_with_a_monitor_runs_inside_it(self):
        from app.core import queue_infra
        calls = []
        sweep = queue_infra.register_periodic_sweep(
            _FakeApp(), queue="q", task_name="t.x", interval_min=15, body=lambda ts: calls.append(("body", ts)) or 5,
            monitor=("turnos-x", 20, 30))
        with patch.object(launch_signals, "monitored", _Recorder(calls)):
            self.assertEqual(sweep(123), 5)
        self.assertEqual(calls, [("monitored", "turnos-x", ("crontab", "*/15 * * * *"), 20, 30), ("enter",),
                                 ("body", 123), ("exit", None)])

    def test_a_periodic_sweep_without_a_monitor_is_what_it_was(self):
        from app.core import queue_infra
        calls = []
        sweep = queue_infra.register_periodic_sweep(
            _FakeApp(), queue="q", task_name="t.x", interval_min=15, body=lambda ts: calls.append(ts) or 5)
        with patch.object(launch_signals, "monitored", side_effect=AssertionError("monitor used")):
            self.assertEqual(sweep(9), 5)
        self.assertEqual(calls, [9])

    def test_reminders_and_waitlist_register_monitored_sweeps(self):
        from app.modules.turnos import waitlist_queue
        from app.notifications import reminders_queue
        for module, slug in ((reminders_queue, "turnos-reminders"), (waitlist_queue, "turnos-waitlist")):
            with patch("app.core.queue_infra.register_periodic_sweep") as register:
                module.register(object(), 10)
            self.assertEqual(register.call_args.kwargs["monitor"], (slug, 10, 30), module.__name__)

    def test_the_worker_heartbeat_task_is_inert_when_off_and_monitored_when_on(self):
        from app import worker
        _env.set_env(self, TURNOS_SENTRY_SIGNALS=None, TURNOS_SENTRY_DSN=None)
        task = worker._register_launch_signals(_FakeApp())
        with patch("app.ops.launch_digest.tick") as tick:
            self.assertEqual(asyncio.run(task(1)), {"skipped": "off"})
        tick.assert_not_called()
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="crons,digest", TURNOS_SENTRY_DSN=_DSN)
        calls = []
        with patch.object(launch_signals, "monitored", _Recorder(calls)), \
                patch("app.ops.launch_digest.tick", return_value={"skipped": "not_due"}) as tick:
            self.assertEqual(asyncio.run(task(1)), {"skipped": "not_due"})
        tick.assert_called_once_with()
        self.assertEqual(calls[0], ("monitored", "turnos-worker", ("crontab", "*/5 * * * *"), 10, 4))

    def test_a_default_worker_registers_no_signal_task_and_a_signalled_one_does(self):
        from app import worker
        names = []

        class App(_FakeApp):
            def task(self, **kw):
                names.append(kw.get("name"))
                return lambda fn: fn

        _env.set_env(self, TURNOS_SENTRY_SIGNALS=None, TURNOS_SENTRY_DSN=None)
        worker._register_jobs(App())
        self.assertNotIn("worker.maintenance.launch_signals", names)
        self.assertEqual(worker._maint_tasks(), worker._MAINT_TASKS)
        self.assertNotIn("worker.maintenance.launch_signals", worker._allowed_task_names())
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="crons", TURNOS_SENTRY_DSN=_DSN)
        names.clear()
        worker._register_jobs(App())
        self.assertIn("worker.maintenance.launch_signals", names)
        self.assertIn("worker.maintenance.launch_signals", worker._maint_tasks())
        self.assertIn("worker.maintenance.launch_signals", worker._allowed_task_names())
        self.assertIn("worker.maintenance.launch_signals", worker._audit_jobs()["maintenance"]["tasks"])

    def test_the_worker_starts_the_client_in_signals_only_mode(self):
        import sentry_sdk
        try:
            import sentry_sdk  # noqa: F811
        except ImportError:
            self.skipTest("sentry-sdk not installed in this checkout")
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="digest", TURNOS_SENTRY_DSN=_DSN)
        seen = {}
        with patch.object(sentry_sdk, "init", lambda **kw: seen.update(kw)):
            self.assertTrue(launch_signals.init_sentry())
            self.assertIs(seen["default_integrations"], False)
            self.assertIs(seen["auto_enabling_integrations"], False)
            seen.clear()
            self.assertTrue(error_tracking.init())
            self.assertNotIn("default_integrations", seen, "the web process keeps its error reporting")

    def test_a_signals_only_client_reports_no_error_of_its_own_but_sends_the_signals(self):
        import logging
        try:
            import sentry_sdk  # noqa: F401
        except ImportError:
            self.skipTest("sentry-sdk not installed in this checkout")
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="all", TURNOS_SENTRY_DSN=_DSN)
        from sentry_sdk.transport import Transport
        sent = []

        class Capture(Transport):
            def capture_envelope(self, envelope):
                sent.append(envelope.serialize().decode("utf-8", "replace"))

        real_init = sentry_sdk.init
        with patch.object(sentry_sdk, "init", lambda **kw: real_init(transport=Capture, **kw)):
            self.assertTrue(launch_signals.init_sentry())
        self.addCleanup(sentry_sdk.init)
        launch_signals._last_event_at.clear()
        logging.getLogger("worker-test").error("DNI %s could not be processed", _DNI)
        sentry_sdk.flush()
        self.assertEqual(sent, [], "an ordinary error log of the worker is not sent")
        launch_signals.handled("mail_failed")
        with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
            pass
        sentry_sdk.flush()
        body = "\n".join(sent)
        self.assertIn("mail_failed", body)
        self.assertIn("monitor_slug", body)
        self.assertNotIn(_DNI, body)
        self.assertNotIn('"breadcrumbs"', body)
        self.assertNotIn('"integrations":["', body.replace(" ", ""))

    def test_the_full_backup_check_in_starts_with_the_run_and_ends_ok_or_error_with_the_same_id(self):
        from app.core import backup_settings
        from app.ops import backup_full, backup_full_queue
        key = ("turnos-backup-full", ("interval", 1440), 180, 240)

        def runner(outcome, start=True):
            def run(**kw):
                if start:
                    kw["on_start"]()
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return run

        with patch.object(backup_full_queue, "enabled", return_value=True), \
                patch.object(backup_settings, "snapshot", return_value={"TURNOS_BACKUP_INTERVAL_MIN": "1440"}), \
                patch.object(launch_signals, "checkin", return_value="cid-1") as checkin, \
                patch.object(launch_signals, "handled") as handled:
            with patch.object(backup_full, "run", runner({"skipped": "not_due"}, start=False)):
                self.assertEqual(backup_full_queue._scheduled_run(1), {"skipped": "not_due"})
            checkin.assert_not_called()
            with patch.object(backup_full, "run", runner({"id": "a"})):
                backup_full_queue._scheduled_run(1)
            self.assertEqual([c.args[4] for c in checkin.call_args_list], ["in_progress", "ok"])
            self.assertEqual(checkin.call_args_list[0].args[:4], key)
            self.assertEqual(checkin.call_args_list[1].args[6], "cid-1", "the end closes the check-in the start opened")
            checkin.reset_mock()
            with patch.object(backup_full, "run", runner(backup_full.BackupError("x"))):
                with self.assertRaises(backup_full.BackupError):
                    backup_full_queue._scheduled_run(1)
            self.assertEqual([c.args[4] for c in checkin.call_args_list], ["in_progress", "error"])
            self.assertEqual(checkin.call_args_list[1].args[6], "cid-1")
            handled.assert_called_once_with("backup_full_failed")
            checkin.reset_mock()
            with patch.object(backup_full, "run", runner(backup_full.BackupError("pg_dump"), start=False)):
                with self.assertRaises(backup_full.BackupError):
                    backup_full_queue._scheduled_run(1)
            self.assertEqual([c.args[4] for c in checkin.call_args_list], ["error"], "failed before it began")
            self.assertIsNone(checkin.call_args_list[0].args[6])

    def test_the_full_backup_run_calls_on_start_only_once_it_really_begins(self):
        import datetime as dt
        import tempfile
        from app.core import backup_settings
        from app.ops import backup_full
        cfg = dict(backup_settings.DEFAULTS, TURNOS_BACKUP_S3_ENDPOINT="https://r2.example")

        def dump(_path, _url):
            raise backup_full.BackupError("synthetic failure after the start")

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(backup_full, "preflight"), patch.object(backup_full.settings, "validate"), \
                patch.object(backup_full.settings, "configured", return_value=True), \
                patch.object(backup_full.settings, "keys", return_value={}), \
                patch.object(backup_full.db, "_db_url", return_value=""):
            started = []
            store = MagicMock()
            store.list.return_value = []
            with self.assertRaises(backup_full.BackupError):
                backup_full.run(cfg=cfg, cloud=MagicMock(), store=store, dump_fn=dump, roots={"data": folder},
                                private_packs=None, env_file=os.path.join(folder, "turnos.env"),
                                scheduled=True, on_start=lambda: started.append("start"))
            self.assertEqual(started, ["start"], "announced once, when the run began")
            store.save.assert_called()
            started.clear()
            store.list.return_value = [{"started_at": dt.datetime.now(dt.timezone.utc).isoformat()}]
            result = backup_full.run(cfg=cfg, cloud=MagicMock(), store=store, dump_fn=dump, roots={"data": folder},
                                     private_packs=None, env_file=os.path.join(folder, "turnos.env"),
                                     scheduled=True, on_start=lambda: started.append("start"))
            self.assertEqual(result, {"skipped": "not_due"})
            self.assertEqual(started, [], "a pass that is not due announces nothing")

    def test_the_full_backup_thread_reports_a_failed_pass_and_stays_quiet_otherwise(self):
        from app.ops import backup_full
        with patch.object(backup_full.settings, "configured", return_value=True), \
                patch.object(launch_signals, "handled") as handled:
            with patch.object(backup_full, "run", side_effect=backup_full.BackupError("x")):
                backup_full._attempt()
            handled.assert_called_once_with("backup_full_failed")
            handled.reset_mock()
            with patch.object(backup_full, "run", return_value={"skipped": "not_due"}):
                backup_full._attempt()
            handled.assert_not_called()
        with patch.object(backup_full.settings, "configured", return_value=False), \
                patch.object(backup_full, "run", side_effect=AssertionError("ran unconfigured")), \
                patch.object(launch_signals, "handled") as handled:
            backup_full._attempt()
        handled.assert_not_called()

    def test_the_full_backup_check_is_untouched_when_backups_are_not_configured(self):
        from app.ops import backup_full_queue
        with patch.object(backup_full_queue, "enabled", return_value=False), \
                patch.object(launch_signals, "checkin") as checkin:
            self.assertEqual(backup_full_queue._scheduled_run(1), {"skipped": "unconfigured"})
        checkin.assert_not_called()

    def test_the_data_backup_sweep_is_monitored_and_a_lost_reschedule_is_a_code(self):
        from app.ops import data_backups, data_backups_queue
        calls = []
        sweep = data_backups_queue.register(_FakeApp(), 360)
        with patch.object(launch_signals, "monitored", _Recorder(calls)), \
                patch.object(launch_signals, "handled") as handled, \
                patch.object(data_backups, "tick", return_value=3), \
                patch("app.core.queue_infra.test_hold"):
            result = asyncio.run(sweep(1))
        self.assertEqual(result, {"uploaded": 3})
        self.assertEqual(calls[0], ("monitored", "turnos-backup-data", ("interval", 360), 210, 180))
        self.assertEqual(calls[-1], ("exit", None))
        handled.assert_called_once_with("backup_data_reschedule_failed")


# ---- 5. the call sites --------------------------------------------------------------------------

class TestCallSites(unittest.TestCase):
    def test_mail_counts_sent_and_reports_failed_and_raised(self):
        from app.notifications import mail
        adapter = MagicMock()
        with patch.object(mail, "transport", return_value=(adapter, "mailgun", "default")), \
                patch.object(mail, "_carries_loopback_link", return_value=False), \
                patch.object(launch_signals, "count") as counted, patch.object(launch_signals, "handled") as handled:
            adapter.send.return_value = True
            self.assertTrue(mail.send("a@b.co", "s", "t"))
            counted.assert_called_once_with("mail_sent")
            handled.assert_not_called()
            adapter.send.return_value = False
            self.assertFalse(mail.send("a@b.co", "s", "t"))
            adapter.send.side_effect = RuntimeError("boom")
            self.assertFalse(mail.send("a@b.co", "s", "t"))
            self.assertEqual([c.args for c in handled.call_args_list], [("mail_failed",), ("mail_failed",)])

    def test_mail_that_is_not_routed_is_neither_sent_nor_failed(self):
        from app.notifications import mail
        with patch.object(mail, "transport", return_value=(None, None, "off")), \
                patch.object(mail, "_carries_loopback_link", return_value=False), \
                patch.object(launch_signals, "count") as counted, patch.object(launch_signals, "handled") as handled:
            self.assertFalse(mail.send("a@b.co", "s", "t"))
        counted.assert_not_called()
        handled.assert_not_called()

    def test_whatsapp_counts_what_the_transport_answered_for(self):
        from app.notifications import otp_send
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"messages": [{"id": "wamid.1"}]}'
        with patch.object(otp_send, "_meta_request", return_value=MagicMock()), \
                patch.object(launch_signals, "count") as counted, patch.object(launch_signals, "handled") as handled:
            with patch("urllib.request.urlopen", return_value=response):
                self.assertTrue(otp_send._enviar_meta("5492254491670", "hola"))
            counted.assert_called_once_with("whatsapp_sent")
            with patch("urllib.request.urlopen", side_effect=OSError("down")):
                self.assertFalse(otp_send._enviar_meta("5492254491670", "hola"))
            with patch("urllib.request.urlopen", side_effect=OSError("down")):
                self.assertFalse(otp_send._enviar_meta_plantilla("5492254491670", "turnos_recordatorio", "es_AR", ["x"]))
            self.assertEqual([c.args for c in handled.call_args_list], [("whatsapp_failed",), ("whatsapp_failed",)])

    def test_whatsapp_without_a_provider_is_neither_sent_nor_failed(self):
        from app.notifications import otp_send
        _env.set_env(self, TURNOS_WA_PROVIDER=None, META_WA_TOKEN=None)
        with patch.object(launch_signals, "count") as counted, patch.object(launch_signals, "handled") as handled:
            self.assertFalse(otp_send.enviar_texto("5492254491670", "hola"))
        counted.assert_not_called()
        handled.assert_not_called()

    def test_staff_sign_ins_are_counted_by_outcome(self):
        from app.auth import admin_auth
        with patch.object(launch_signals, "count") as counted:
            admin_auth._record_attempt("203.0.113.7")
            admin_auth._ATTEMPTS.clear()
            with patch("app.core.db_operators.operator_mark_access", side_effect=RuntimeError("db down")):
                admin_auth.marcar_acceso("recepcion.lucia")
        self.assertEqual([c.args for c in counted.call_args_list], [("staff_login_failed",), ("staff_login_ok",)])

    def test_the_reminders_sweeps_report_a_failed_tick(self):
        from app.notifications import reminders, reminders_queue
        with patch.object(launch_signals, "handled") as handled:
            sweep = reminders_queue.register(_FakeApp(), 10)
            with patch.object(reminders, "tick", side_effect=RuntimeError("a")), \
                    patch.object(reminders, "tick_interview", side_effect=RuntimeError("b")):
                sweep(1)
        self.assertEqual([c.args for c in handled.call_args_list], [("reminders_sweep_failed",)] * 2)

    def test_the_reminders_thread_reports_a_failed_tick(self):
        from app.notifications import reminders
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        with patch.object(reminders, "_stop", stop), patch.object(launch_signals, "handled") as handled, \
                patch.object(reminders, "tick", side_effect=RuntimeError("a")), \
                patch.object(reminders, "tick_interview", side_effect=RuntimeError("b")):
            reminders._loop(10)
        self.assertEqual(handled.call_count, 2)

    def test_the_waitlist_sweeps_report_a_failed_tick(self):
        from app.modules.turnos import waitlist, waitlist_queue
        with patch.object(launch_signals, "handled") as handled:
            sweep = waitlist_queue.register(_FakeApp(), 10)
            with patch.object(waitlist, "tick", side_effect=RuntimeError("a")):
                with self.assertRaises(RuntimeError):
                    sweep(1)
        handled.assert_called_once_with("waitlist_sweep_failed")
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        with patch.object(waitlist, "_stop", stop), patch.object(launch_signals, "handled") as handled, \
                patch.object(waitlist, "tick", side_effect=RuntimeError("a")):
            waitlist._loop(10)
        handled.assert_called_once_with("waitlist_sweep_failed")

    def test_the_data_backup_sweep_reports_journal_and_upload_failures(self):
        import tempfile
        from app.ops import data_backups
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x")
            candidates = [("a.txt", path, os.stat(path))]
            with patch.object(launch_signals, "handled") as handled, \
                    patch.object(data_backups, "_candidate_paths", return_value=candidates):
                with patch.object(data_backups.db_backup, "backup_journal_map", side_effect=RuntimeError("no")):
                    data_backups._tick_unlocked(lambda *a: None, injected=True)
                handled.assert_called_once_with("backup_data_journal_failed")
                handled.reset_mock()
                with patch.object(data_backups.db_backup, "backup_journal_map", return_value={}):
                    data_backups._tick_unlocked(MagicMock(side_effect=RuntimeError("b2")), injected=True)
                handled.assert_called_once_with("backup_data_upload_failed")
                handled.reset_mock()
                with patch.object(data_backups.db_backup, "backup_journal_map", return_value={}), \
                        patch.object(data_backups.db_backup, "backup_journal_commit", side_effect=RuntimeError("no")):
                    data_backups._tick_unlocked(lambda *a: None, injected=True)
                handled.assert_called_once_with("backup_data_journal_failed")

    def test_the_hsi_chain_reports_a_lost_reschedule(self):
        from app.modules.turnos import hsi_journal, hsi_journal_queue
        task = MagicMock()
        task.configure.return_value.defer_async.side_effect = RuntimeError("lost")
        sweep = hsi_journal_queue.register(_FakeApp({hsi_journal_queue._TASK_NAME: task}), 5)
        with patch.object(hsi_journal, "_off", return_value=False), \
                patch.object(hsi_journal, "_emr_selected", return_value=True), \
                patch.object(hsi_journal, "tick", return_value={"backfilled": 0}), \
                patch("app.core.queue_infra.test_hold"), patch.object(launch_signals, "handled") as handled:
            asyncio.run(sweep(1))
        handled.assert_called_once_with("hsi_sync_reschedule_failed")


class TestSwallowedAfterSave(unittest.TestCase):
    """The steps after an action that had already persisted (the product owner's hub, 2026-11-10): the patient notices, the
    side steps of a new request and the 503 each report their code; the request is never broken by it, and the
    log line names the exception's type and the request id — never the exception's text."""

    _ROW = {"id": "7", "nombre": _FIRST, "apellido": _LAST, "dni": _DNI, "solicitante_dni": _DNI,
            "contacto": _PHONE, "servicio_id": "eco-abdominal", "centro_slug": "hospital-pinamar",
            "motivo_rechazo": "otro"}

    def _swallowed(self, call, code):
        """Runs `call` with `launch_signals.handled` recorded and stderr captured; returns the log text."""
        import contextlib
        import io
        err = io.StringIO()
        with patch.object(launch_signals, "handled") as handled, contextlib.redirect_stderr(err):
            call()
        self.assertIn((code,), [c.args for c in handled.call_args_list])
        log = err.getvalue()
        for value in _PII:
            self.assertNotIn(value, log)
        return log

    def test_each_patient_notice_reports_its_own_code(self):
        from app.notifications import send
        boom = RuntimeError(f"template for {_EMAIL} {_DNI}")
        with patch.object(send, "_destination_email", side_effect=boom):
            for notify, code in ((send.notify_received, "notify_received_failed"),
                                 (send.notify_approved, "notify_approved_failed"),
                                 (send.notify_rejected, "notify_rejected_failed"),
                                 (send.notify_cancelled, "notify_cancelled_failed")):
                log = self._swallowed(lambda: notify(dict(self._ROW)), code)
                self.assertIn("RuntimeError", log)
                self.assertIn("id=7", log)
        with patch.object(send, "_destination_email", return_value=""), \
                patch.object(send, "_whatsapp_confirmation", side_effect=boom):
            self._swallowed(lambda: send.notify_approved(dict(self._ROW)), "notify_approved_failed")

    def test_the_whatsapp_helpers_report_a_failure_inside_them(self):
        from datetime import datetime, timezone
        from app.notifications import otp_send, send
        boom = RuntimeError(f"template for {_PHONE}")
        row = dict(self._ROW, turno_dt=datetime(2026, 7, 17, 13, 30, tzinfo=timezone.utc))
        remedies = MagicMock()
        remedies.whatsapp_text.side_effect = boom
        with patch.object(send, "_destination_phone", return_value=_PHONE), \
                patch.object(otp_send, "notificacion_permitida", return_value=True), \
                patch.object(otp_send, "enviar_notificacion") as sent, \
                patch.object(send, "_wa_fields", side_effect=boom), \
                patch.object(send, "_turnos_remedies", remedies):
            self._swallowed(lambda: send._whatsapp_confirmation(row, "es"), "notify_approved_failed")
            self._swallowed(lambda: send._whatsapp_rejection(row, {"titulo": "t"}, _FIRST, "eco", "es"),
                            "notify_rejected_failed")
        sent.assert_not_called()

    def test_a_reminder_that_raises_answers_failed_and_the_sweep_reports_it(self):
        from app.notifications import send
        with patch.object(send, "_destination_lang", side_effect=RuntimeError(_EMAIL)), \
                patch.object(launch_signals, "handled"):
            self.assertEqual(send.notify_reminder(dict(self._ROW)), "failed")
        from datetime import date, datetime
        from app.notifications import reminders
        at = {"day": date(2026, 7, 17), "now": datetime(2026, 7, 16, 10, 0, tzinfo=reminders._ART)}
        with patch("app.core.db_booking.release_reminder") as release:
            self._swallowed(lambda: reminders._release(self._ROW, **at), "reminder_failed")
            self._swallowed(lambda: reminders._release(self._ROW, interview=True, **at), "interview_reminder_failed")
        self.assertEqual([c.args for c in release.call_args_list],
                         [("7", "reminder_at"), ("7", "interview_reminder_at")])
        with patch.object(send, "_interview_pending", side_effect=RuntimeError(_EMAIL)), \
                patch.object(launch_signals, "handled"):
            self.assertIs(send.notify_interview_reminder(dict(self._ROW)), False)
        # A release the database refuses loses that reminder (as a crash after the claim always did), never the sweep.
        with patch("app.core.db_booking.release_reminder", side_effect=RuntimeError(_DNI)):
            log = self._swallowed(lambda: reminders._release(self._ROW, **at), "reminder_failed")
        self.assertIn("no se pudo liberar recordatorio id=7: RuntimeError", log)

    def test_a_refused_mail_is_a_failure_only_when_the_provider_could_have_sent_it(self):
        # One send answers why (the product owner's hub review, 2026-11-10): the provider is asked once, and its refusal is
        # read for its reason afterwards from the same provider, never from a second routing.
        from app.notifications import mail
        adapter = MagicMock()
        adapter.missing_config.return_value = []
        adapter.send.return_value = False
        # Mail on and the outbound kill switch off, whatever a neighbour left in the process (one-process run:
        # tests/_routes_fixture.py defaults MAIL_ENABLED=0 for the whole routes tier).
        with patch.object(mail, "enabled", return_value=True), \
                patch("app.core.outbound_guard.armed", return_value=False), \
                patch.object(mail, "_carries_loopback_link", return_value=False), \
                patch.object(launch_signals, "handled"), patch.object(launch_signals, "count"):
            with patch.object(mail, "transport", return_value=(adapter, "mailgun", "default")) as routed:
                self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.FAILED)
                self.assertFalse(mail.send("a@b.co", "s", "t"))
                with patch("app.core.outbound_guard.armed", return_value=True):
                    self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.SKIPPED,
                                     "the outbound kill switch is configuration, not a failure")
                with patch.object(mail, "enabled", return_value=False):
                    self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.SKIPPED, "MAIL_ENABLED=0")
                adapter.missing_config.return_value = ["MAILGUN_API_KEY"]
                self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.SKIPPED,
                                 "a provider without its keys never sends: not a failure")
                adapter.missing_config.side_effect = RuntimeError("x")
                self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.FAILED, "undecidable: retried, never lost")
                adapter.missing_config.side_effect = None
                adapter.send.return_value = True
                self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.SENT)
                self.assertIs(mail.send("a@b.co", "s", "t"), True)
                self.assertEqual(routed.call_count, 8, "one routing per message")
            with patch.object(mail, "transport", return_value=(None, None, "off")):
                self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.SKIPPED)
            with patch.object(mail, "transport", side_effect=RuntimeError("x")):
                self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.FAILED, "undecidable: retried, never lost")
        with patch.object(mail, "_carries_loopback_link", return_value=True):
            self.assertEqual(mail.deliver("a@b.co", "s", "t"), mail.SKIPPED)
        self.assertFalse(hasattr(mail, "unsendable"), "one function decides; no second copy of its conditions")

    def test_a_new_request_reports_a_lost_hold_link_and_a_lost_reading(self):
        from types import SimpleNamespace
        from app.modules.turnos import booking_create
        store = booking_create.store
        b = SimpleNamespace(sid="41", slot_dt=object(), hold_token="t", stored_ai={"x": 1}, dni=_DNI,
                            nombre=_FIRST, apellido=_LAST, servicio_id="eco-abdominal", orden_filename=None)
        with patch.object(store, "hold_link_booking_request", side_effect=RuntimeError(_DNI)):
            self._swallowed(lambda: self.assertIsNone(booking_create._step_link_hold(b)), "booking_hold_link_failed")
        with patch.object(booking_create.ai_referral, "reconcile_with_request", return_value={}), \
                patch.object(store, "save_ai", side_effect=RuntimeError(_DNI)):
            self._swallowed(lambda: self.assertIsNone(booking_create._step_analysis(b)), "booking_ai_save_failed")
        with patch.object(booking_create.ai_referral, "reconcile_with_request", return_value={}), \
                patch.object(store, "save_ai", side_effect=store.DbUnavailable("down")), \
                patch.object(launch_signals, "handled") as handled:
            with self.assertRaises(store.DbUnavailable):
                booking_create._step_analysis(b)
        handled.assert_not_called()

    def test_a_new_request_reports_a_referral_file_it_could_not_store_or_link(self):
        import tempfile
        from types import SimpleNamespace
        from app.modules.turnos import booking_create
        store, referrals = booking_create.store, booking_create.booking_referrals
        with tempfile.TemporaryDirectory() as folder:
            pending = os.path.join(folder, "pending.bin")

            def request():
                with open(pending, "wb") as fh:
                    fh.write(b"%PDF-1.4")
                return SimpleNamespace(sid="41", pending_path=pending, orden_token="tok", orden_bytes=b"%PDF-1.4",
                                       orden_mime="application/pdf", orden_filename="orden.pdf")

            with patch.object(booking_create.bkr, "REFERRALS_DIR", folder), \
                    patch.object(referrals, "_discard_pending_meta"):
                b = request()
                with patch.object(store, "set_referral", side_effect=RuntimeError(_DNI)):
                    self._swallowed(lambda: booking_create._step_referral_file(b), "booking_referral_failed")
                self.assertTrue(b.orden_path, "the file itself was stored")
                b = request()
                with patch.object(booking_create.os, "replace", side_effect=OSError(28, "full")), \
                        patch("shutil.copyfile", side_effect=OSError(28, "full")), \
                        patch.object(store, "set_referral") as linked:
                    self._swallowed(lambda: booking_create._step_referral_file(b), "booking_referral_failed")
                self.assertIsNone(b.orden_path)
                linked.assert_not_called()

    def test_the_503_reports_db_unavailable_once_per_window_and_answers_the_same(self):
        import threading
        from app.core import db, deps
        now = [1000.0]
        deps._db_signal_last.clear()
        self.addCleanup(deps._db_signal_last.clear)

        def answer():
            response = asyncio.run(deps.render_db_unavailable(None, db.DbUnavailable(f"pool {_DNI}")))
            for thread in threading.enumerate():
                if thread.name == "db-unavailable-signal":
                    thread.join(5)
            return response

        with patch.object(deps, "_clock", lambda: now[0]), patch.object(launch_signals, "handled") as handled:
            for _ in range(3):
                response = answer()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["Retry-After"], "3")
            now[0] += deps._DB_UNAVAILABLE_SIGNAL_EVERY_S - 1
            answer()
            self.assertEqual([c.args for c in handled.call_args_list], [("db_unavailable",)],
                             "one event in 15 minutes, whatever the number of refused requests")
            now[0] += 2
            answer()
        self.assertEqual([c.args for c in handled.call_args_list], [("db_unavailable",)] * 2)
        self.assertEqual(deps._DB_UNAVAILABLE_SIGNAL_EVERY_S, 900)

    def test_the_routes_that_answer_their_own_503_report_it_through_the_same_window(self):
        # The product owner's hub review, 2026-11-10: these routes catch `DbUnavailable` themselves, so the uncaught-503
        # handler never sees their outage; each answers through `deps.answer_db_unavailable`, which reports in
        # the shared window (every other such route: tests/test_db_unavailable_answer.py).
        from starlette.requests import Request
        from app.core import deps
        from app.admin_panel import reports_router
        from app.ai import ai_costs, ai_costs_router
        request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []})

        def costs():
            with patch.object(ai_costs, "_report_data", side_effect=db.DbUnavailable(f"pool {_DNI}")):
                return asyncio.run(ai_costs_router.admin_costs(request, identity={}))

        answers = {"reports": lambda: reports_router._db_error_response(request),
                   "AI costs": costs}
        for name, answer in answers.items():
            with patch.object(deps, "signal_db_unavailable") as signal:
                self.assertEqual(answer().status_code, 503, name)
            signal.assert_called_once_with()


class TestNewCodesLeaveAsCodes(_Sdk):
    def test_each_code_of_2026_11_10_reaches_the_tracker_as_its_code_alone(self):
        codes = ("notify_received_failed", "notify_approved_failed", "notify_rejected_failed",
                 "notify_cancelled_failed", "reminder_failed", "interview_reminder_failed", "booking_referral_failed",
                 "booking_ai_save_failed",
                 "booking_hold_link_failed", "db_unavailable")
        with patch.object(launch_signals, "count"):
            for code in codes:
                self.assertIn(code, launch_signals.FAILURE_CODES)
                launch_signals.handled(code)
        body = self.payload().replace(" ", "")
        for code in codes:
            self.assertIn(f'"message":"{code}"', body)
        for value in _PII:
            self.assertNotIn(value, body)


# ---- 6. counters and the digest, against a real database ---------------------------------------

class _DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _env.set_env(self, TURNOS_SENTRY_DSN=_DSN, TURNOS_SENTRY_SIGNALS="digest")
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE launch_signal_counters, appointment_events, booking_requests, hsi_outbox, "
                        "hsi_sync_conflicts, operators RESTART IDENTITY CASCADE")
        self.addCleanup(self._truncate)
        from app.core import queue_infra
        queue_infra.ensure_schema()   # the queue tables are part of a real installation

    def _truncate(self):
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE launch_signal_counters, appointment_events, booking_requests, hsi_outbox, "
                        "hsi_sync_conflicts, operators RESTART IDENTITY CASCADE")

    def sql(self, statement, params=()):
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall() if cur.description else None

    def request(self, dni=_DNI):
        return db_booking.create_booking_request(dni, _FIRST, _LAST, "1980-02-03", f"{_PHONE} {_EMAIL}",
                                                 "medicina-general", "hospital", actor=_STAFF)


class TestCounters(_DbCase):
    def test_counts_add_up_within_the_hour(self):
        launch_signals.count("mail_sent")
        launch_signals.count("mail_sent")
        launch_signals.count("staff_login_failed")
        rows = dict(self.sql("SELECT code, sum(n) FROM launch_signal_counters GROUP BY code"))
        self.assertEqual(rows, {"mail_sent": 2, "staff_login_failed": 1})
        self.assertEqual(self.sql("SELECT count(*) FROM launch_signal_counters WHERE code='mail_sent'"), [(1,)])

    def test_a_code_outside_the_list_writes_nothing(self):
        launch_signals.count("patient_24132941")
        launch_signals.count("digest_sent")
        self.assertEqual(self.sql("SELECT count(*) FROM launch_signal_counters"), [(0,)])

    def test_digest_off_writes_nothing(self):
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="failures")
        launch_signals.count("mail_sent")
        launch_signals.handled("mail_failed")
        self.assertEqual(self.sql("SELECT count(*) FROM launch_signal_counters"), [(0,)])

    def test_a_down_database_never_raises(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("down")):
            launch_signals.count("mail_sent")

    def test_a_counter_write_never_waits_for_a_row_another_writer_holds(self):
        import psycopg
        import threading
        holder = psycopg.connect(_db.test_url, connect_timeout=5)
        self.addCleanup(holder.close)
        holder.execute("INSERT INTO launch_signal_counters (bucket, code, n) "
                       "VALUES (date_trunc('hour', now()), 'mail_sent', 1)")   # open transaction: the row is locked
        result = {}

        def write():
            started = time.monotonic()
            launch_signals.count("mail_sent")
            result["waited"] = time.monotonic() - started

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        writer.join(8)
        stuck = writer.is_alive()
        holder.commit()      # whatever still waits is released here: a broken guard fails the test, never hangs it
        writer.join(8)
        self.assertFalse(stuck, "the write waited for the row another writer holds")
        self.assertLess(result["waited"], 3, "the write gives up after its lock timeout")
        self.assertGreaterEqual(result["waited"], 0.3, "and it did try")
        self.assertEqual(self.sql("SELECT n FROM launch_signal_counters WHERE code = 'mail_sent'"), [(1,)])
        launch_signals.count("mail_sent")
        self.assertEqual(self.sql("SELECT n FROM launch_signal_counters WHERE code = 'mail_sent'"), [(2,)])

    def test_the_table_holds_an_hour_a_closed_code_and_a_number(self):
        columns = self.sql("SELECT column_name FROM information_schema.columns "
                           "WHERE table_name='launch_signal_counters' ORDER BY column_name")
        self.assertEqual([c[0] for c in columns], ["bucket", "code", "n"])


class TestDigest(_DbCase):
    def seed(self):
        a, b, c = self.request(), self.request("30111222"), self.request("27999888")
        db_booking.reject(b, "falta la orden", resuelta_por=_STAFF)
        db_booking.cancel(c, actor=_STAFF, skip_hsi_without_connector=True)
        db_operators.operator_create(_STAFF, "pass-1234-abcd-xyz", "admisionista", email=_STAFF_MAIL)
        self.sql("INSERT INTO hsi_outbox (entity_type, entity_id, operation, payload, idempotency_key, state) "
                 "VALUES ('appointment', %s, 'create', %s::jsonb, 'k1', 'error'), "
                 "('appointment', %s, 'create', %s::jsonb, 'k2', 'pending')",
                 (_DNI, json.dumps({"paciente": f"{_FIRST} {_LAST}", "dni": _DNI}), _DNI,
                  json.dumps({"tel": _PHONE})))
        self.sql("INSERT INTO hsi_sync_conflicts (entity_type, entity_id, direction, reason, local_payload) "
                 "VALUES ('appointment', %s, 'outbound', %s, %s::jsonb)",
                 (_DNI, f"{_FIRST} {_LAST}", json.dumps({"dni": _DNI})))
        for code, times in (("mail_sent", 5), ("mail_failed", 2), ("staff_login_ok", 3), ("staff_login_failed", 1)):
            for _ in range(times):
                launch_signals.count(code)
        return a

    def test_the_numbers_are_the_numbers(self):
        self.seed()
        numbers = launch_digest().collect()
        self.assertEqual((numbers["requests_created"], numbers["requests_rejected"], numbers["requests_cancelled"],
                          numbers["requests_approved"], numbers["requests_pending"]), (3, 1, 1, 0, 1))
        self.assertEqual((numbers["hsi_outbox_pending"], numbers["hsi_outbox_error"], numbers["hsi_outbox_conflict"],
                          numbers["hsi_conflicts_open"]), (1, 1, 0, 1))
        self.assertEqual((numbers["n_mail_sent"], numbers["n_mail_failed"], numbers["n_staff_login_ok"],
                          numbers["n_staff_login_failed"], numbers["n_whatsapp_sent"]), (5, 2, 3, 1, 0))
        self.assertEqual(numbers["schema_revision"], int(_head_number()))
        self.assertEqual(numbers["sections_failed"], 0)
        self.assertEqual((numbers["backup_data_last_age_s"], numbers["backup_full_last_ok_age_s"]), (-1, -1),
                         "no backup yet is -1, not a missing key")
        for key, value in numbers.items():
            self.assertIn(key, launch_signals.DIGEST_KEYS)
            self.assertIs(type(value), int, key)

    def test_events_older_than_a_day_are_not_counted(self):
        self.seed()
        self.sql("UPDATE appointment_events SET created = now() - interval '25 hours' WHERE kind = 'creada'")
        self.sql("UPDATE launch_signal_counters SET bucket = bucket - interval '25 hours'")
        numbers = launch_digest().collect()
        self.assertEqual(numbers["requests_created"], 0)
        self.assertEqual(numbers["n_mail_sent"], 0)
        self.sql("UPDATE launch_signal_counters SET bucket = date_trunc('hour', now()) - interval '24 hours' "
                 "WHERE code = 'mail_sent'")
        self.assertEqual(launch_digest().collect()["n_mail_sent"], 5, "the whole hour that began 24 hours ago counts")
        self.sql("UPDATE launch_signal_counters SET bucket = date_trunc('hour', now()) - interval '25 hours' "
                 "WHERE code = 'mail_sent'")
        self.assertEqual(launch_digest().collect()["n_mail_sent"], 0)
        self.assertEqual(numbers["requests_pending"], 1, "pending is a state now, not a 24 hour window")

    def test_the_queue_numbers_come_from_the_queue_tables(self):
        from app.core import queue_infra
        queue_infra.ensure_schema()
        self.sql("TRUNCATE procrastinate_jobs RESTART IDENTITY CASCADE")
        self.addCleanup(self.sql, "TRUNCATE procrastinate_jobs RESTART IDENTITY CASCADE")
        insert = ("INSERT INTO procrastinate_jobs (queue_name, task_name, status, scheduled_at, lock, args) "
                  "VALUES ('q', %s, %s::procrastinate_job_status, now() - %s::interval, %s, %s::jsonb)")
        for task, status, age, lock in (
                ("t.a", "todo", "2 hours", None), ("t.b", "todo", "10 minutes", None),
                ("t.c", "todo", "-3 hours", None),     # not due yet: scheduled in the future
                ("t.d", "doing", "1 minute", "l1"), ("t.e", "succeeded", "1 minute", None)):
            self.sql(insert, (task, status, age, lock, json.dumps({"dni": _DNI, "paciente": _FIRST})))
        job = self.sql("SELECT id FROM procrastinate_jobs WHERE task_name = 't.e'")[0][0]
        self.sql("INSERT INTO procrastinate_events (job_id, type) VALUES (%s, 'failed'), (%s, 'succeeded')", (job, job))
        self.sql("INSERT INTO procrastinate_events (job_id, type, at) VALUES (%s, 'failed', now() - interval '30 hours')",
                 (job,))
        numbers = launch_digest().collect()
        self.assertEqual((numbers["queue_todo"], numbers["queue_doing"], numbers["queue_failed_24h"]), (3, 1, 1))
        self.assertTrue(2 * 3600 - 30 <= numbers["queue_oldest_todo_age_s"] <= 2 * 3600 + 120,
                        numbers["queue_oldest_todo_age_s"])
        self.assertNotIn(_DNI, json.dumps(numbers))

    def test_backup_ages_are_seconds_since_the_last_stored_and_the_last_verified_backup(self):
        self.sql("INSERT INTO backup_journal (path, sha256, size, mtime_ns, remote_name, uploaded_at) VALUES "
                 "('a.txt', 'x', 1, 1, 'r/a', now() - interval '2 hours'), "
                 "('b.txt', 'y', 1, 1, 'r/b', now() - interval '5 hours')")
        self.sql("INSERT INTO backup_runs (id, started_at, finished_at, status, uploaded, integrity_verified) VALUES "
                 "('1', now() - interval '30 hours', now() - interval '29 hours', 'ok', true, true), "
                 "('2', now() - interval '2 hours', now() - interval '1 hours', 'ok', true, false), "
                 "('3', now() - interval '2 hours', now() - interval '1 hours', 'failed', false, false)")
        self.addCleanup(self.sql, "TRUNCATE backup_journal, backup_runs")
        numbers = launch_digest().collect()
        self.assertTrue(7200 - 5 <= numbers["backup_data_last_age_s"] <= 7200 + 60, numbers["backup_data_last_age_s"])
        self.assertTrue(29 * 3600 - 5 <= numbers["backup_full_last_ok_age_s"] <= 29 * 3600 + 60,
                        "only the run that was uploaded AND verified counts")

    def test_a_section_that_cannot_be_read_costs_only_its_own_keys(self):
        self.seed()
        digest = launch_digest()
        with patch.object(digest, "_hsi", side_effect=RuntimeError("no table")):
            numbers = digest.collect()
        self.assertNotIn("hsi_outbox_error", numbers)
        self.assertEqual(numbers["sections_failed"], 1, "a missing measurement is told, not silent")
        self.assertEqual(numbers["requests_created"], 3)
        self.assertEqual(numbers["n_mail_sent"], 5)

    def test_nothing_personal_leaves_in_the_real_envelope(self):
        try:
            import sentry_sdk  # noqa: F401
        except ImportError:
            self.skipTest("sentry-sdk not installed in this checkout")
        self.seed()
        sent, undo = _sdk_capturing_envelopes()
        self.addCleanup(undo)
        digest = launch_digest()
        with patch.object(digest, "_claim_today", return_value=True):
            result = digest.tick(now=_at_hour(13))
        import sentry_sdk
        sentry_sdk.flush()
        self.assertTrue(result["sent"], result)
        body = "\n".join(sent)
        self.assertIn("requests_created", body)
        self.assertIn("n_mail_sent", body)
        for secret in _PII + ("falta la orden", "Quiroga"):
            self.assertNotIn(secret, body)
        digest_event = next(json.loads(line) for line in body.splitlines() if '"launch_digest"' in line)
        self.assertTrue(all(type(v) is int for v in digest_event["contexts"]["launch_digest"].values()))

    def test_no_selected_column_can_hold_a_person(self):
        """Every statement of the digest, run once, selects only counts, ages and a revision: no column of a
        table that holds names, documents, contacts, free text or payloads."""
        self.seed()
        from app.core import sqlcore
        seen = []
        real_run = sqlcore.run

        def spy(cur, stmt):
            seen.append(str(sqlcore.compile_statement(stmt)[0]))
            return real_run(cur, stmt)

        with patch.object(sqlcore, "run", spy):
            launch_digest().collect()
        self.assertTrue(seen)
        forbidden = ("first_name", "last_name", "dni", "contact", "birth_date", "payload", "reason",
                     "rejection_reason", "patient_note", "username", "email", "actor", "entity_id", "last_error",
                     "local_payload", "remote_payload", "resolved_by", "assigned_doctor", "ai_json", "args")
        for statement in seen:
            select_list = re.split(r"\bfrom\b", statement.lower(), maxsplit=1)[0]
            for word in forbidden:
                self.assertNotRegex(select_list, rf"\b{word}\b", f"{word} selected in: {statement}")


class TestDigestSchedule(_DbCase):
    def test_sent_once_a_day_after_midday_utc_by_whoever_gets_there_first(self):
        digest = launch_digest()
        import sentry_sdk
        with patch.object(sentry_sdk, "capture_event", return_value="id") as capture:
            self.assertEqual(digest.tick(now=_at_hour(11)), {"skipped": "not_due"})
            first = digest.tick(now=_at_hour(13))
            second = digest.tick(now=_at_hour(14))
        self.assertTrue(first["sent"])
        self.assertEqual(second, {"skipped": "already_sent"})
        self.assertEqual(capture.call_count, 1)
        self.sql("DELETE FROM launch_signal_counters WHERE code = 'digest_sent'")   # the next day
        with patch.object(sentry_sdk, "capture_event", return_value="id") as capture:
            self.assertTrue(digest.tick(now=_at_hour(13))["sent"])
        self.assertEqual(capture.call_count, 1)

    def test_two_processes_racing_send_one(self):
        import threading
        digest = launch_digest()
        import sentry_sdk
        results = []
        with patch.object(sentry_sdk, "capture_event", return_value="id") as capture:
            threads = [threading.Thread(target=lambda: results.append(digest.tick(now=_at_hour(13))))
                       for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(sum(1 for r in results if r.get("sent")), 1)

    def _claims(self):
        return self.sql("SELECT count(*) FROM launch_signal_counters WHERE code = 'digest_sent'")[0][0]

    def test_a_digest_that_crashes_gives_the_day_back_and_is_a_failure_code(self):
        digest = launch_digest()
        import sentry_sdk
        with patch.object(digest, "collect", side_effect=RuntimeError("boom")), \
                patch.object(launch_signals, "handled") as handled:
            self.assertEqual(digest.tick(now=_at_hour(13)), {"error": "RuntimeError"})
        handled.assert_called_once_with("digest_failed")
        self.assertEqual(self._claims(), 0)
        with patch.object(sentry_sdk, "capture_event", return_value="id") as capture:
            self.assertTrue(digest.tick(now=_at_hour(13))["sent"])
        self.assertEqual(capture.call_count, 1)

    def test_a_digest_the_client_refused_gives_the_day_back(self):
        digest = launch_digest()
        import sentry_sdk
        with patch.object(sentry_sdk, "capture_event", return_value=None), patch.object(launch_signals, "handled") as handled:
            self.assertIn("error", digest.tick(now=_at_hour(13)))
        handled.assert_called_once_with("digest_failed")
        self.assertEqual(self._claims(), 0)

    def test_a_sent_digest_stays_sent_when_the_housekeeping_fails(self):
        digest = launch_digest()
        import sentry_sdk
        with patch.object(sentry_sdk, "capture_event", return_value="id") as capture, \
                patch.object(digest, "_purge_old_counters", side_effect=RuntimeError("boom")):
            self.assertTrue(digest.tick(now=_at_hour(13))["sent"])
            self.assertEqual(digest.tick(now=_at_hour(14)), {"skipped": "already_sent"})
        self.assertEqual(capture.call_count, 1, "a failed cleanup must not let a second process send again")
        self.assertEqual(self._claims(), 1)

    def test_the_claim_failing_is_a_failure_code_and_sends_nothing(self):
        digest = launch_digest()
        import sentry_sdk
        with patch.object(digest, "_claim_today", side_effect=db.DbUnavailable("down")), \
                patch.object(sentry_sdk, "capture_event") as capture, patch.object(launch_signals, "handled") as handled:
            self.assertEqual(digest.tick(now=_at_hour(13)), {"error": "DbUnavailable"})
        capture.assert_not_called()
        handled.assert_called_once_with("digest_failed")

    def test_old_counters_are_purged_after_a_send(self):
        self.sql("INSERT INTO launch_signal_counters (bucket, code, n) VALUES (now() - interval '20 days', 'mail_sent', 4)")
        digest = launch_digest()
        import sentry_sdk
        with patch.object(sentry_sdk, "capture_event", return_value="id"):
            digest.tick(now=_at_hour(13))
        self.assertEqual(self.sql("SELECT count(*) FROM launch_signal_counters WHERE code = 'mail_sent'"), [(0,)])

    def test_a_down_database_never_raises(self):
        digest = launch_digest()
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("down")):
            self.assertIn("error", digest.tick(now=_at_hour(13)))

    def test_the_web_thread_starts_only_without_a_worker(self):
        digest = launch_digest()
        with patch.object(digest.threading, "Thread") as thread:
            _env.set_env(self, TURNOS_QUEUE_WORKER="1")
            self.assertFalse(digest.start())
            thread.assert_not_called()
            _env.set_env(self, TURNOS_QUEUE_WORKER=None)
            self.assertTrue(digest.start())
            thread.assert_called_once()
        _env.set_env(self, TURNOS_SENTRY_SIGNALS="failures")
        self.assertFalse(digest.start())
        with patch.object(digest.threading, "Thread") as thread:
            _env.set_env(self, TURNOS_SENTRY_SIGNALS="crons")
            self.assertTrue(digest.start(), "the heartbeat alone is a reason to run the thread")

    def test_the_web_thread_checks_in_each_pass_and_runs_the_digest_inside_it(self):
        digest = launch_digest()
        calls = []
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        with patch.object(digest, "_stop", stop), patch.object(launch_signals, "monitored", _Recorder(calls)), \
                patch.object(digest, "tick", side_effect=lambda: calls.append(("tick",))):
            digest._loop()
        self.assertEqual(calls, [("monitored", "turnos-web-heartbeat", ("interval", 10), 10, 5), ("enter",),
                                 ("tick",), ("exit", None)])


def launch_digest():
    from app.ops import launch_digest as module
    return module


def _at_hour(hour):
    from datetime import datetime, timezone
    return datetime(2026, 10, 31, hour, 0, tzinfo=timezone.utc)


def _head_number():
    versions = os.path.join(ROOT, "alembic", "versions")
    numbers = sorted(n.split("_", 1)[0] for n in os.listdir(versions) if re.match(r"^\d{4}_", n))
    return numbers[-1]


def _has_table(name):
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
        return cur.fetchone()[0]


if __name__ == "__main__":
    unittest.main()
