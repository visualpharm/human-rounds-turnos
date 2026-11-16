"""What `worker.main()` does at start-up, seen from outside: the exit code, the
key log line and which side effects ran (arm the outbound guard, touch the
schema, build the queue app, register jobs, start the health server, run the
worker loop) for each reason to stop, and that a signal landing right after
any of those effects ends the process cleanly (exit 0) before the next one.

No database and no real worker: the collaborators are recorded doubles and
`_register_jobs`, `_start_health_server` and `_boot_and_run` are patched.  The
expected values are literals; the internal step functions are not pinned.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import worker  # noqa: E402

GREEN = ["ensure_schema", "build_app", "register_jobs", "start_health_server", "boot_and_run", "httpd.shutdown"]
EFFECTS = {"guard.arm": "arm", "queue_infra.ensure_schema": "ensure_schema", "queue_infra.build_app": "build_app",
           "register_jobs": "register_jobs", "start_health_server": "start_health_server",
           "boot_and_run": "boot_and_run", "httpd.shutdown": "httpd.shutdown"}


class _Run:
    """One `_main_body` call.  `stop_on` is the moment a signal arrives: "start", an event name (right after a
    collaborator was touched) or "log:<text>" (right after that line was logged)."""

    def __init__(self, stop_on=None, guard=False, arm_error=False, mode="1", db_url="postgresql://x/y",
                 schema_error=False, register=None, queues=("waitlist", "maintenance"), health=True,
                 boot_error=None, early=True, **env):
        self.events, self.logs, self.boot, self.flag = [], [], {}, stop_on == "start"
        env = {"TURNOS_QUEUE_WORKER": "1", "TURNOS_WORKER_STALLED_S": env.get("stalled"),
               "TURNOS_TEST_UNSAFE_STALLED": env.get("unsafe"), "TURNOS_TEST_SEAMS": env.get("seams"),
               "TURNOS_WORKER_GRACEFUL_TIMEOUT": env.get("graceful")}
        run = self

        def note(event):
            run.events.append(event)
            run.flag = run.flag or event == stop_on

        def log(line):
            run.logs.append(line)
            run.flag = run.flag or (stop_on or "").startswith("log:") and stop_on[4:] in line

        class State(worker._SignalState):
            stop_requested = property(lambda self: run.flag, lambda self, value: None)

        class Guard:
            def requested(self):
                note("guard.requested")
                return guard

            def arm(self):
                note("guard.arm")
                if arm_error:
                    raise RuntimeError("no se pudo armar")

        class Db:
            def _db_url(self):
                note("db._db_url")
                return db_url

        class Infra:
            def worker_mode(self):
                note("queue_infra.worker_mode")
                return mode

            def ensure_schema(self):
                note("queue_infra.ensure_schema")
                if schema_error:
                    raise RuntimeError("postgres caido")

            def build_app(self):
                note("queue_infra.build_app")
                return "APP"

        class Httpd:
            def shutdown(self):
                note("httpd.shutdown")

        def register_jobs(app):
            note("register_jobs")
            if register:
                raise register
            return list(queues)

        def start_health():
            note("start_health_server")
            return Httpd() if health_on else None

        async def boot(app, colas, name, graceful, state, restore):
            note("boot_and_run")
            run.boot = {"colas": list(colas), "graceful": graceful, "restore": restore is not None}
            if boot_error:
                raise boot_error

        health_on = health
        worker._STATE["fase"] = "arrancando"
        with _env.patched_env(env, clear=False), patch.object(worker, "_log", log), \
                patch.object(worker, "_register_jobs", register_jobs), \
                patch.object(worker, "_start_health_server", start_health), patch.object(worker, "_boot_and_run", boot):
            self.code = worker._main_body(Db(), Guard(), Infra(), State(), (lambda *a: None) if early else None)
        self.fase = worker._STATE["fase"]
        self.effects = [EFFECTS[e] for e in self.events if e in EFFECTS]

    def logged(self, text):
        return any(text in line for line in self.logs)


class TestWorkerStartUp(unittest.TestCase):
    def test_each_reason_to_stop_has_its_code_its_log_line_and_its_effects(self):
        cases = [  # (options, exit code, log text, side effects that ran)
            (dict(), 0, "apagado con gracia", GREEN),
            (dict(guard=True), 0, "candado de salida armado", ["arm"] + GREEN),
            (dict(guard=True, arm_error=True), worker.EXIT_CONFIG, "sin candado no corro", ["arm"]),
            (dict(mode=""), worker.EXIT_CONFIG, "no es un modo válido", []),
            (dict(stalled="abc"), worker.EXIT_CONFIG, "no es un número", []),
            (dict(stalled="nan"), worker.EXIT_CONFIG, "no es un número finito", []),
            (dict(stalled="5"), worker.EXIT_CONFIG, "piso seguro", []),
            (dict(stalled="5", unsafe="1"), worker.EXIT_CONFIG, "piso seguro", []),
            (dict(stalled="5", unsafe="1", seams="1"), 0, "apagado con gracia", GREEN),
            (dict(db_url=""), worker.EXIT_CONFIG, "DATABASE_URL", []),
            (dict(schema_error=True), worker.EXIT_TRANSIENT, "no se pudo asegurar el esquema", ["ensure_schema"]),
            (dict(register=ValueError("intervalo")), worker.EXIT_CONFIG, "configuración inválida",
             ["ensure_schema", "build_app", "register_jobs"]),
            (dict(queues=("maintenance",)), 0, "sólo mantenimiento", GREEN),
            (dict(health=False), 0, "apagado con gracia", GREEN[:-1]),
            (dict(boot_error=asyncio.CancelledError("x")), worker.EXIT_TRANSIENT, "cancelado inesperadamente", GREEN),
            (dict(boot_error=RuntimeError("caida")), worker.EXIT_TRANSIENT, "terminó con error", GREEN),
        ]
        for options, code, text, effects in cases:
            with self.subTest(**{k: repr(v) for k, v in options.items()}):
                run = _Run(**options)
                self.assertEqual((run.code, run.logged(text), run.effects), (code, True, effects), run.logs)
                self.assertEqual(run.fase, "error" if code == worker.EXIT_TRANSIENT and run.boot else
                                 "arrancando" if code else "apagando")

    def test_the_worker_loop_gets_the_queues_the_graceful_budget_and_the_early_handlers(self):
        run = _Run()
        self.assertEqual(run.boot, {"colas": ["waitlist", "maintenance"], "graceful": 20.0, "restore": True})
        self.assertEqual(_Run(graceful="37").boot["graceful"], 37.0)
        self.assertEqual(_Run(graceful="veinte").boot["graceful"], 20.0)
        self.assertFalse(_Run(early=False).boot["restore"])

    def test_a_signal_ends_the_process_cleanly_before_the_next_side_effect(self):
        shut = ["ensure_schema", "build_app", "register_jobs", "start_health_server", "httpd.shutdown"]
        cases = [  # (the moment, options, side effects that ran)
            ("start", {}, []),
            ("start", dict(mode="", guard=True), []),  # not even the invalid mode is judged
            ("guard.requested", dict(guard=True), []),
            ("guard.arm", dict(guard=True, mode=""), ["arm"]),  # armed, then stops before judging the mode
            ("queue_infra.worker_mode", dict(stalled="abc"), []),
            ("db._db_url", {}, []),
            ("queue_infra.ensure_schema", {}, shut[:1]),
            ("queue_infra.build_app", {}, shut[:2]),
            ("register_jobs", {}, shut[:3]),
            ("start_health_server", {}, shut),
            ("log:arrancando", {}, shut),
        ]
        for moment, options, effects in cases:
            with self.subTest(moment, **{k: repr(v) for k, v in options.items()}):
                run = _Run(stop_on=moment, **options)
                self.assertEqual((run.code, run.effects, run.fase), (0, effects, "apagando"))
                self.assertTrue(run.logged("apagando con gracia"), run.logs)
        self.assertEqual(_Run(stop_on="start", health=False).effects, [])


if __name__ == "__main__":
    unittest.main()
