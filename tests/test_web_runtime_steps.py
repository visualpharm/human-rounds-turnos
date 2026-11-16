"""``start_background_services`` step by step: what each service does, in which order, and what is logged.

The function is one entry point made of ordered, independent, fail-open steps. These tests record
every starter call and every log line in one event list, so the exact interleaving is pinned for
the success path, for each failing step (the next steps must still run) and for the optional
(falsy) starters. ``test_web_runtime.py`` keeps the lifespan and shutdown contracts.
"""
import unittest
from unittest import mock

import _env
from app import connectors
from app.ai import ai_health, chart_summary
from app.core import ai_register, app_modules, concurrency, db, error_tracking, log_pii
from app.modules.turnos import booking_referrals, hsi_fallback, hsi_journal, waitlist
from app.notifications import reminders
from app.ops import backup_full, cache_warmup, data_backups, demo_refresh, housekeeping
from app.ops import translate_warmup, web_runtime
from app.patient_web import site_doctors


class Probe(RuntimeError):
    """Carries an HTTP-like code: `log_pii.exc` prints it, a bare type name does not."""
    code = 503


INVENTORY = object()
OPTIONAL_STARTERS_RUN = {"error-init": True, "demo": True, "chart": True, "translation": True, "cache": True}


def P(message):
    return ("print", message)


def baseline(*, fallback=False):
    """Every service starts: the events of a clean run, in order."""
    emr = [("hsi-resync",), P("Resincronización EMR de compatibilidad iniciada.")] if fallback else \
        [("hsi-sync",), P("Sincronización bidireccional EMR iniciada.")]
    return [
        ("threadpool",),
        P("Concurrencia: 7 hilos, pool de 5 conexiones, espera de 2.5 s por una conexión libre."),
        ("access-log",),
        ("ai-inventory",), ("ai-observe", INVENTORY),
        ("error-init",), P("Rastreo de errores (Sentry) iniciado."),
        ("reminders",), P("Recordatorios de turno iniciados."),
        ("waitlist",), P("Barrido de lista de espera iniciado."),
        ("referrals",), P("Retención de órdenes iniciada."),
        ("housekeeping",), P("Higiene periódica de tablas iniciada."),
        ("demo",), P("Regeneración diaria de la demo iniciada."),
        ("chart",), P("Pre-generación de resúmenes del paciente iniciada."),
        ("translation",), P("Traducción del catálogo en segundo plano iniciada."),
        ("avatar-thread", site_doctors.warm_avatar_sprite, "avatar-warmup", True),
        ("avatar-start",), P("Atlas de avatares en precalentamiento."),
        ("cache",), P("Caches del camino público en precalentamiento."),
        ("resolve", connectors.EMR_SYNC),
        *emr,
        ("backups",),
        ("backup-full",),
        ("ai-health",), P("Monitor de salud de IA iniciado."),
        ("modules",), P("Ciclo de vida de app modules iniciado."),
    ]


def with_failure(events, key, message, success=True):
    """The same run when `key` raises: its success line (if it has one) is replaced by `message`."""
    at = next(n for n, e in enumerate(events) if e[0] == key)
    rest = events[at + 1:]
    if success:
        assert rest[0][0] == "print", (key, rest[0])
        rest = rest[1:]
    return events[:at + 1] + [P(message)] + rest


class StartupStepsTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.failing = {}
        self.returns = dict(OPTIONAL_STARTERS_RUN)
        self.resolved = ("hsi", "explicit")
        self.print_kwargs = []
        _env.set_env(self, TURNOS_HSI_SYNC_OFF=None)

    # -- harness ------------------------------------------------------------------------------

    def hook(self, key):
        def call(*args, **kwargs):
            self.events.append((key, *args))
            if key in self.failing:
                raise self.failing[key]
            return self.returns.get(key)
        return call

    def log(self, message, **kwargs):
        self.events.append(P(message))
        self.print_kwargs.append(kwargs)

    def run_startup(self):
        test = self

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                test.events.append(("avatar-thread", target, name, daemon))
                if "avatar-thread" in test.failing:
                    raise test.failing["avatar-thread"]

            def start(self):
                test.hook("avatar-start")()

        def resolve(capability):
            test.events.append(("resolve", capability))
            if "resolve" in test.failing:
                raise test.failing["resolve"]
            return test.resolved

        patches = [
            mock.patch.object(concurrency, "apply_threadpool_size", self.hook_value("threadpool", 7)),
            mock.patch.object(db, "_pool_size", return_value=5),
            mock.patch.object(db, "_pool_timeout", return_value=2.5),
            mock.patch.object(log_pii, "install_access_log_filter", self.hook("access-log")),
            mock.patch.object(ai_register, "inventory", self.hook_value("ai-inventory", INVENTORY)),
            mock.patch.object(ai_register, "observe_inventory", self.hook("ai-observe")),
            mock.patch.object(error_tracking, "init", self.hook("error-init")),
            mock.patch.object(reminders, "start", self.hook("reminders")),
            mock.patch.object(waitlist, "start", self.hook("waitlist")),
            mock.patch.object(booking_referrals, "start_referral_sweep", self.hook("referrals")),
            mock.patch.object(housekeeping, "start", self.hook("housekeeping")),
            mock.patch.object(demo_refresh, "start", self.hook("demo")),
            mock.patch.object(chart_summary, "start", self.hook("chart")),
            mock.patch.object(translate_warmup, "start", self.hook("translation")),
            mock.patch.object(web_runtime.threading, "Thread", FakeThread),
            mock.patch.object(cache_warmup, "start", self.hook("cache")),
            mock.patch.object(connectors, "resolve", resolve),
            mock.patch.object(hsi_fallback, "start", self.hook("hsi-resync")),
            mock.patch.object(hsi_journal, "start", self.hook("hsi-sync")),
            mock.patch.object(data_backups, "start", self.hook("backups")),
            mock.patch.object(backup_full, "start", self.hook("backup-full")),
            mock.patch.object(ai_health, "start", self.hook("ai-health")),
            mock.patch.object(app_modules, "startup_all", self.hook("modules")),
            mock.patch.object(web_runtime, "print", self.log, create=True),
        ]
        for patcher in patches:
            self.enterContext(patcher)
        web_runtime.start_background_services()

    def hook_value(self, key, value):
        self.returns[key] = value
        return self.hook(key)

    # -- the success path ---------------------------------------------------------------------

    def test_clean_run_with_the_journal_is_exactly_the_established_sequence(self):
        self.run_startup()
        self.assertEqual(self.events, baseline())
        self.assertTrue(all(kwargs == {"flush": True} for kwargs in self.print_kwargs))
        self.assertEqual(len(self.print_kwargs), sum(1 for e in self.events if e[0] == "print"))

    def test_only_the_value_one_selects_the_compatibility_resync(self):
        for value, fallback in (("1", True), ("0", False), ("", False), ("true", False)):
            with self.subTest(value=value):
                self.events.clear()
                _env.set_env(self, TURNOS_HSI_SYNC_OFF=value)
                self.run_startup()
                self.assertEqual(self.events, baseline(fallback=fallback))

    def test_no_selected_emr_starts_neither_lifecycle_and_prints_nothing_about_it(self):
        self.resolved = (None, "off")
        self.run_startup()
        expected = [e for e in baseline() if e not in (("hsi-sync",), P("Sincronización bidireccional EMR iniciada."))]
        self.assertEqual(self.events, expected)
        self.assertIn(("resolve", connectors.EMR_SYNC), self.events)

    def test_a_registry_defect_stops_the_run_at_the_lookup(self):
        self.failing["resolve"] = AssertionError("registry defect")
        with self.assertRaisesRegex(AssertionError, "registry defect"):
            self.run_startup()
        self.assertEqual(self.events, baseline()[:baseline().index(("resolve", connectors.EMR_SYNC)) + 1])

    def test_a_base_exception_is_not_swallowed_and_later_services_do_not_run(self):
        self.failing["waitlist"] = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_startup()
        self.assertEqual(self.events, baseline()[:baseline().index(("waitlist",)) + 1])

    # -- optional starters --------------------------------------------------------------------

    def test_a_starter_that_declines_prints_no_success_line(self):
        optional = {
            "error-init": "Rastreo de errores (Sentry) iniciado.",
            "demo": "Regeneración diaria de la demo iniciada.",
            "chart": "Pre-generación de resúmenes del paciente iniciada.",
            "translation": "Traducción del catálogo en segundo plano iniciada.",
            "cache": "Caches del camino público en precalentamiento.",
        }
        for key, message in optional.items():
            for declined in (False, None, 0):
                with self.subTest(key=key, returns=declined):
                    self.events.clear()
                    self.returns = dict(OPTIONAL_STARTERS_RUN, **{key: declined})
                    self.run_startup()
                    self.assertEqual(self.events, [e for e in baseline() if e != P(message)])

    def test_the_unconditional_starters_log_whatever_they_return(self):
        for key in ("reminders", "waitlist", "referrals", "housekeeping", "hsi-sync", "ai-health", "modules"):
            with self.subTest(key=key):
                self.events.clear()
                self.returns = dict(OPTIONAL_STARTERS_RUN, **{key: False})
                self.run_startup()
                self.assertEqual(self.events, baseline())

    def test_the_data_backup_starters_have_no_success_line(self):
        self.run_startup()
        at = self.events.index(("backups",))
        self.assertEqual(self.events[at:at + 3], [("backups",), ("backup-full",), ("ai-health",)])

    # -- fail-open: one failing step is reported and the next ones still run -----------------

    def assert_fail_open(self, key, message, *, success=True, fallback=False):
        self.events.clear()
        self.failing[key] = Probe("secret-text")
        self.run_startup()
        self.assertEqual(self.events, with_failure(baseline(fallback=fallback), key, message, success))
        self.assertFalse(any("secret-text" in str(e) for e in self.events))
        self.failing.clear()

    def test_steps_that_log_only_the_exception_type(self):
        self.assert_fail_open("threadpool", "Tamaño del pool de hilos no aplicado: Probe")
        self.assert_fail_open("access-log", "Filtro del log de accesos no instalado: Probe", success=False)
        self.assert_fail_open("ai-observe", "AI register startup refresh unavailable: Probe", success=False)
        self.assert_fail_open("error-init", "Rastreo de errores no iniciado: Probe")

    def test_a_failing_inventory_never_reaches_the_observer(self):
        self.failing["ai-inventory"] = Probe("secret-text")
        self.run_startup()
        expected = baseline()
        at = expected.index(("ai-inventory",))
        self.assertEqual(self.events, expected[:at + 1] + [P("AI register startup refresh unavailable: Probe")]
                         + expected[at + 2:])

    def test_steps_that_log_the_exception_through_log_pii(self):
        for key, message in (
                ("reminders", "Recordatorios no iniciados: Probe (503)"),
                ("waitlist", "Barrido de lista de espera no iniciado: Probe (503)"),
                ("referrals", "Retención de órdenes no iniciada: Probe (503)"),
                ("housekeeping", "Higiene periódica de tablas no iniciada: Probe (503)"),
                ("demo", "Regeneración diaria de la demo no iniciada: Probe (503)"),
                ("chart", "Pre-generación de resúmenes no iniciada: Probe (503)"),
                ("translation", "Traducción del catálogo no iniciada: Probe (503)"),
                ("avatar-start", "Precalentamiento de avatares no iniciado: Probe (503)"),
                ("cache", "Precalentamiento de caches no iniciado: Probe (503)"),
                ("hsi-sync", "Sincronización bidireccional EMR no iniciada: Probe (503)"),
                ("ai-health", "Monitor de salud de IA no iniciado: Probe (503)"),
                ("modules", "Ciclo de vida de app modules no iniciado: Probe (503)")):
            with self.subTest(key=key):
                self.assert_fail_open(key, message)

    def test_the_compatibility_resync_failure_is_logged_and_the_run_goes_on(self):
        _env.set_env(self, TURNOS_HSI_SYNC_OFF="1")
        self.assert_fail_open("hsi-resync", "Resincronización EMR de compatibilidad no iniciada: Probe (503)",
                              fallback=True)

    def test_the_backup_steps_fail_open_without_a_success_line(self):
        self.assert_fail_open("backups", "Backup de data/ no iniciado: Probe (503)", success=False)
        self.assert_fail_open("backup-full", "Encrypted backup producer not started", success=False)

    def test_a_failing_thread_constructor_is_reported_and_nothing_is_started(self):
        self.failing["avatar-thread"] = Probe("secret-text")
        self.run_startup()
        expected = baseline()
        at = next(n for n, e in enumerate(expected) if e[0] == "avatar-thread")
        self.assertEqual(self.events, expected[:at + 1] + [P("Precalentamiento de avatares no iniciado: Probe (503)")]
                         + expected[at + 3:])
        self.assertNotIn(("avatar-start",), self.events)


if __name__ == "__main__":
    unittest.main()
