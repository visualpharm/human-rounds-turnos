"""Contracts for the web-process lifecycle extracted from ``server.py``."""

import contextlib
import asyncio
import io
import os
import unittest
from unittest import mock

import _env
from app import main as app_module
from app.ai import ai_health, chart_summary
from app import connectors
from app.modules.turnos import hsi_fallback as hsi_resync
from app.modules.turnos import hsi_journal as hsi_sync
from app.core import app_modules, error_tracking
from app.modules.turnos import booking_referrals, waitlist
from app.notifications import reminders
from app.ops import cache_warmup, data_backups, demo_refresh, housekeeping
from app.ops import translate_warmup, web_runtime
from app.patient_web import site_doctors


class _Thread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        _CALLS.append("avatar")


_CALLS = []


class TestWebRuntime(unittest.TestCase):
    def setUp(self):
        _CALLS.clear()

    @staticmethod
    def _starter(label, result=None):
        def start():
            _CALLS.append(label)
            return result

        return start

    def _patches(self):
        return (
            mock.patch.object(error_tracking, "init", self._starter("error", True)),
            mock.patch.object(reminders, "start", self._starter("reminders")),
            mock.patch.object(waitlist, "start", self._starter("waitlist")),
            mock.patch.object(
                booking_referrals,
                "start_referral_sweep",
                self._starter("referrals"),
            ),
            mock.patch.object(housekeeping, "start", self._starter("housekeeping")),
            mock.patch.object(demo_refresh, "start", self._starter("demo", True)),
            mock.patch.object(chart_summary, "start", self._starter("chart", True)),
            mock.patch.object(
                translate_warmup,
                "start",
                self._starter("translation", True),
            ),
            mock.patch.object(site_doctors, "warm_avatar_sprite", lambda: None),
            mock.patch.object(web_runtime.threading, "Thread", _Thread),
            mock.patch.object(cache_warmup, "start", self._starter("cache", True)),
            mock.patch.object(hsi_resync, "start", self._starter("hsi-resync")),
            mock.patch.object(hsi_sync, "start", self._starter("hsi-sync")),
            mock.patch.object(data_backups, "start", self._starter("backups")),
            mock.patch.object(ai_health, "start", self._starter("ai-health")),
            mock.patch.object(app_modules, "startup_all", self._starter("modules")),
        )

    def test_all_services_keep_the_established_order(self):
        _env.set_env(self, TURNOS_HSI_SYNC_OFF="1", CONNECTOR_EMR_SYNC="hsi")
        with contextlib.ExitStack() as stack:
            for patcher in self._patches():
                stack.enter_context(patcher)
            web_runtime.start_background_services()

        self.assertEqual(
            _CALLS,
            [
                "error",
                "reminders",
                "waitlist",
                "referrals",
                "housekeeping",
                "demo",
                "chart",
                "translation",
                "avatar",
                "cache",
                "hsi-resync",
                "backups",
                "ai-health",
                "modules",
            ],
        )

    def test_legacy_hsi_fallback_stays_off_with_the_journal_enabled(self):
        _env.set_env(self, TURNOS_HSI_SYNC_OFF=None, CONNECTOR_EMR_SYNC="hsi")
        with contextlib.ExitStack() as stack:
            for patcher in self._patches():
                stack.enter_context(patcher)
            web_runtime.start_background_services()
        self.assertNotIn("hsi-resync", _CALLS)
        self.assertIn("hsi-sync", _CALLS)

    def test_unselected_emr_does_not_start_or_claim_any_hsi_lifecycle(self):
        _env.set_env(self, CONNECTOR_EMR_SYNC="off")
        with contextlib.ExitStack() as stack:
            for patcher in self._patches():
                stack.enter_context(patcher)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                web_runtime.start_background_services()
        self.assertNotIn("hsi-resync", _CALLS)
        self.assertNotIn("hsi-sync", _CALLS)
        self.assertNotIn("Sincronización", output.getvalue())

    def test_registry_programmer_failure_is_not_hidden_as_an_emr_degradation(self):
        with contextlib.ExitStack() as stack:
            for patcher in self._patches():
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                connectors, "resolve", side_effect=AssertionError("registry defect")))
            with self.assertRaisesRegex(AssertionError, "registry defect"):
                web_runtime.start_background_services()

    def test_one_failed_service_does_not_block_later_services(self):
        _env.set_env(self, TURNOS_HSI_SYNC_OFF=None)
        with contextlib.ExitStack() as stack:
            patchers = list(self._patches())
            patchers[2] = mock.patch.object(
                waitlist,
                "start",
                mock.Mock(side_effect=RuntimeError("synthetic failure")),
            )
            for patcher in patchers:
                stack.enter_context(patcher)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                web_runtime.start_background_services()
        self.assertIn(
            "Barrido de lista de espera no iniciado: RuntimeError",
            output.getvalue(),
        )
        self.assertNotIn("synthetic failure", output.getvalue())  # core/log_pii
        self.assertIn("modules", _CALLS)

    def test_module_shutdown_is_owned_by_the_runtime_boundary(self):
        with mock.patch.object(app_modules, "shutdown_all") as shutdown:
            web_runtime.stop_app_modules()
        shutdown.assert_called_once_with()

    def test_module_shutdown_failure_is_logged_and_swallowed(self):
        # The handler must not itself raise (it used `log_pii` without
        # importing it, so a failing shutdown escaped the lifespan as NameError).
        output = io.StringIO()
        with (
            mock.patch.object(app_modules, "shutdown_all", side_effect=RuntimeError("synthetic failure")),
            contextlib.redirect_stdout(output),
        ):
            web_runtime.stop_app_modules()
        self.assertIn("shutdown falló: RuntimeError", output.getvalue())
        self.assertNotIn("synthetic failure", output.getvalue())

    def test_asgi_lifespan_starts_and_stops_background_ownership_once(self):
        async def exercise():
            async with app_module._lifespan(app_module.app):
                self.assertEqual(start.call_count, 1)
                stop.assert_not_called()

        with (
            mock.patch.object(web_runtime, "start_background_services") as start,
            mock.patch.object(web_runtime, "stop_app_modules") as stop,
        ):
            asyncio.run(exercise())
        start.assert_called_once_with()
        stop.assert_called_once_with()

    def test_asgi_startup_failure_stops_modules_once_and_propagates(self):
        class SyntheticStartupFailure(BaseException):
            pass

        async def exercise():
            async with app_module._lifespan(app_module.app):
                self.fail("a failed startup must never enter the serving lifespan")

        with (
            mock.patch.object(
                web_runtime,
                "start_background_services",
                side_effect=SyntheticStartupFailure("synthetic startup failure"),
            ) as start,
            mock.patch.object(web_runtime, "stop_app_modules") as stop,
        ):
            with self.assertRaisesRegex(SyntheticStartupFailure, "synthetic startup failure"):
                asyncio.run(exercise())
        start.assert_called_once_with()
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
