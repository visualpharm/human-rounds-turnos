"""Runtime proofs for the explicit-EMR-selection boundary (2026-09-01).

When NO EMR connector is selected (`CONNECTOR_EMR_SYNC` unset, `off`, or
invalid — `emr_sync` is an explicit-only capability, so there is no default
tier), Core must start and operate with ZERO HSI lifecycle:

- no journal thread, no fallback thread (threading.Thread never called);
- no worker registration, no boot kick, no queued row for the journal task;
- no urllib/network probe from the «Sistema» HSI cluster or the Settings
  probe — and no default `http://localhost:8280` URL invented anywhere;
- no HSI settings card exposed, no HSI deep link on appointment payloads.

Every assertion drives the REAL surfaces (``hsi_journal.start()``,
``worker._register_jobs``, ``system_status._cluster_hsi``,
``settings_admin._probar_hsi``/``_api_keys_payload``,
``doctor_appointments`` serialization) through the REAL connector registry
(``connectors.resolve`` — never patched). Selection is toggled the way an
operator toggles it: the stored config value. The explicitly selected
legacy ``hsi`` connector and the ``emr_bridge`` connector keep today's
behavior — those branches are pinned here too.
"""
import json
import os
import subprocess
import sys
import textwrap
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.admin_panel import settings_admin, system_status  # noqa: E402
from app.core import config  # noqa: E402
from app.modules.turnos import doctor_appointments, hsi_fallback, hsi_journal  # noqa: E402
from app.modules.turnos import hsi_journal_queue  # noqa: E402
import _lifespan  # noqa: E402

from app import connectors  # noqa: E402
from app.core import app_modules  # noqa: E402

# The flags these tests toggle. The sandbox backs up + clears them so a
# developer's shell can never leak a selection (or an HSI URL) in.
_FLAGS = ("CONNECTOR_EMR_SYNC", "TURNOS_HSI_SYNC_OFF", "TURNOS_HSI_RESYNC_OFF",
          "TURNOS_QUEUE_WORKER", "TURNOS_SKIP_HSI", "HSI_URL",
          "TURNOS_HSI_WEB_URL", "EMR_BRIDGE_URL", "EMR_BRIDGE_TOKEN")


class _SelectionSandbox(unittest.TestCase):
    """Deterministic connector-selection world: no env value, no .env, no
    DB override — the registry's own resolve() then genuinely answers
    (None, None) for emr_sync."""

    def setUp(self):
        _env.set_env(self, dict.fromkeys(_FLAGS))
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        # A reachable dev database must not leak a stored selection into
        # these tests (and must not hide one in the selected-case either:
        # the stored layer is pinned empty, env decides — exactly how an
        # operator's env-var selection beats Settings while locked).
        patcher = mock.patch.object(config, "_db_overrides", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)
        config._reset_cache_para_tests()

    def tearDown(self):
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def _select(self, connector_id):
        _env.set_env(self, CONNECTOR_EMR_SYNC=connector_id)


class TestRegistryGroundTruth(_SelectionSandbox):
    """What 'no connector selected' means, straight from the registry —
    the same call every gated surface below reads."""

    def test_no_selection_resolves_to_nothing(self):
        self.assertEqual(connectors.resolve(connectors.EMR_SYNC), (None, None))
        self.assertFalse(hsi_journal._emr_selected())

    def test_off_sentinel_is_not_a_selection(self):
        self._select("off")
        self.assertEqual(connectors.resolve(connectors.EMR_SYNC), (None, "off"))
        self.assertFalse(hsi_journal._emr_selected())

    def test_invalid_id_fails_closed(self):
        self._select("no_existe")
        self.assertEqual(connectors.resolve(connectors.EMR_SYNC), (None, "invalid"))
        self.assertFalse(hsi_journal._emr_selected())

    def test_explicit_hsi_and_bridge_are_selections(self):
        for connector_id in ("hsi", "emr_bridge"):
            self._select(connector_id)
            self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                             (connector_id, "explicit"))
            self.assertTrue(hsi_journal._emr_selected(), connector_id)


class TestNoThreadStartup(_SelectionSandbox):
    """The web-process lifecycle: without a selection, start() returns
    None and threading.Thread is NEVER instantiated — the strongest
    in-process statement of 'zero HSI thread startup'."""

    def test_journal_thread_does_not_start_without_selection(self):
        with mock.patch("threading.Thread") as thread_cls:
            self.assertIsNone(hsi_journal.start())
        thread_cls.assert_not_called()

    def test_journal_thread_absent_even_with_an_hsi_url_lying_around(self):
        # The gate is the SELECTION, not the presence of HSI_* config: a
        # stale HSI_URL from a previous installation must not resurrect
        # the thread (nor any probe below).
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        with mock.patch("threading.Thread") as thread_cls:
            self.assertIsNone(hsi_journal.start())
        thread_cls.assert_not_called()

    def test_fallback_thread_does_not_start_without_selection(self):
        with mock.patch("threading.Thread") as thread_cls:
            self.assertIsNone(hsi_fallback.start())
        thread_cls.assert_not_called()

    def test_selected_hsi_keeps_the_journal_thread_exactly_as_before(self):
        self._select("hsi")
        with mock.patch("threading.Thread") as thread_cls:
            hsi_journal.start()
        thread_cls.assert_called_once()
        self.assertEqual(thread_cls.call_args.kwargs["name"], "hsi-sync")

    def test_selected_bridge_keeps_the_journal_thread(self):
        self._select("emr_bridge")
        with mock.patch("threading.Thread") as thread_cls:
            hsi_journal.start()
        thread_cls.assert_called_once()

    def test_selected_hsi_keeps_the_fallback_thread_exactly_as_before(self):
        self._select("hsi")
        with mock.patch("threading.Thread") as thread_cls:
            hsi_fallback.start()
        thread_cls.assert_called_once()
        self.assertEqual(thread_cls.call_args.kwargs["name"], "hsi-resync")


class TestRuntimeDeselection(_SelectionSandbox):
    """A running runtime re-reads selection before every unit of EMR work.

    Startup gates alone are insufficient: a thread or a queued self-sweep
    may already exist when an operator changes CONNECTOR_EMR_SYNC. These
    tests transition from both valid connector types to every nonselection
    value and assert no core work or continuation is performed.
    """

    def _remove_selection(self, value):
        _env.set_env(self, CONNECTOR_EMR_SYNC=value)     # None removes it

    def _transitions(self):
        for selected in ("hsi", "emr_bridge"):
            for removed in (None, "off", "not_a_connector"):
                with self.subTest(selected=selected, removed=removed):
                    self._select(selected)
                    self.assertTrue(hsi_journal._emr_selected())
                    self._remove_selection(removed)
                    self.assertFalse(hsi_journal._emr_selected())
                    yield selected, removed

    def test_journal_and_fallback_ticks_do_zero_work_after_deselection(self):
        for _selected, _removed in self._transitions():
            with mock.patch.object(hsi_journal, "backfill_pending") as backfill, \
                    mock.patch.object(hsi_journal, "replay") as replay, \
                    mock.patch.object(hsi_journal, "reconcile") as reconcile, \
                    mock.patch("app.core.db_booking.booking_requests_hsi_pending") as pending:
                result = hsi_journal.tick()
                self.assertEqual(result["backfilled"], 0)
                self.assertEqual(hsi_fallback.tick(), 0)
            backfill.assert_not_called()
            replay.assert_not_called()
            reconcile.assert_not_called()
            pending.assert_not_called()

    def test_replay_item_and_queued_sweep_do_not_continue_after_deselection(self):
        import asyncio
        import procrastinate
        import procrastinate.testing

        for _selected, _removed in self._transitions():
            app = procrastinate.App(connector=procrastinate.testing.InMemoryConnector())
            sweep = hsi_journal_queue.register(app, 5)
            with mock.patch("app.core.db_hsi.hsi_outbox_claim_id") as claim, \
                    mock.patch.object(hsi_journal, "tick") as tick, \
                    mock.patch.object(app.tasks[hsi_journal_queue._TASK_NAME], "configure") as configure:
                self.assertIsNone(hsi_journal.replay_item(91))
                result = asyncio.run(sweep.func(0))
            self.assertEqual(result["backfilled"], 0)
            claim.assert_not_called()
            tick.assert_not_called()
            configure.assert_not_called()

    def test_rekick_refuses_to_create_a_new_chain_after_deselection(self):
        import asyncio
        import procrastinate
        import procrastinate.testing

        for _selected, _removed in self._transitions():
            app = procrastinate.App(connector=procrastinate.testing.InMemoryConnector())
            # core.db is process-wide: an app daemon an earlier module left running
            # (tests/_lifespan.py) connects while it is patched. Only rekick's own
            # callers count.
            ours = []

            def connect(*_args, **_kwargs):
                if threading.current_thread().name not in _lifespan.LIFESPAN_THREADS:
                    ours.append(threading.current_thread().name)
                return mock.MagicMock()

            with mock.patch("app.core.db._connect", side_effect=connect):
                self.assertFalse(asyncio.run(hsi_journal_queue.rekick(app)))
            self.assertEqual(ours, [])


class TestNoWorkerStartup(_SelectionSandbox):
    """The queue worker: without a selection the journal job is not
    registered (no task, queue not consumed) and the boot path queues no
    row for it. Proven in-process against the real registry composition,
    and end to end through the operator-facing `worker.py --jobs` audit
    in a fresh subprocess."""

    def _app(self):
        import procrastinate
        import procrastinate.testing
        return procrastinate.App(connector=procrastinate.testing.InMemoryConnector())

    def test_journal_job_not_registered_without_selection(self):
        from app import worker
        from app.modules.turnos import hsi_journal_queue
        app = self._app()
        queues = worker._register_jobs(app)
        self.assertNotIn("hsi_sync", queues)
        self.assertNotIn(hsi_journal_queue._TASK_NAME, app.tasks)
        self.assertNotIn("worker.maintenance.hsi_sync_watchdog", app.tasks)

    def test_selected_hsi_registers_the_journal_job(self):
        from app import worker
        from app.modules.turnos import hsi_journal_queue
        self._select("hsi")
        app = self._app()
        queues = worker._register_jobs(app)
        self.assertIn("hsi_sync", queues)
        self.assertIn(hsi_journal_queue._TASK_NAME, app.tasks)
        self.assertIn("worker.maintenance.hsi_sync_watchdog", app.tasks)

    def test_selected_bridge_registers_the_journal_job(self):
        from app import worker
        from app.modules.turnos import hsi_journal_queue
        self._select("emr_bridge")
        app = self._app()
        queues = worker._register_jobs(app)
        self.assertIn("hsi_sync", queues)
        self.assertIn(hsi_journal_queue._TASK_NAME, app.tasks)
        self.assertIn("worker.maintenance.hsi_sync_watchdog", app.tasks)

    def test_boot_startuup_defers_no_journal_row_without_selection(self):
        import asyncio
        from app import worker
        from app.modules.turnos import hsi_journal_queue

        async def _boot():
            app = self._app()
            worker._register_jobs(app)
            await worker._defer_startup_jobs(app)
            return app

        app = asyncio.run(_boot())
        jobs = getattr(app.connector, "jobs", {})
        job_rows = list(jobs.values()) if isinstance(jobs, dict) else list(jobs)
        self.assertFalse([j for j in job_rows
                          if j.get("task_name") == hsi_journal_queue._TASK_NAME],
                         job_rows)

    def test_worker_jobs_audit_reports_disabled_without_selection(self):
        env = {"PATH": os.environ.get("PATH", ""),
               "TURNOS_ENV_FILE": os.devnull,
               "TURNOS_FAKE_AI": "1"}
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "app", "worker.py"),
                               "--jobs"],
                              capture_output=True, text=True, timeout=60,
                              cwd=ROOT, env=env, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        audit = json.loads(proc.stdout)
        hsi = next(j for j in audit["jobs"] if j["id"] == "hsi_sync")
        self.assertFalse(hsi["enabled"], hsi)
        self.assertIsNone(hsi["cadence"], hsi)

    def test_worker_jobs_audit_reports_enabled_when_selected(self):
        env = {"PATH": os.environ.get("PATH", ""),
               "TURNOS_ENV_FILE": os.devnull,
               "TURNOS_FAKE_AI": "1",
               "CONNECTOR_EMR_SYNC": "hsi"}
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "app", "worker.py"),
                               "--jobs"],
                              capture_output=True, text=True, timeout=60,
                              cwd=ROOT, env=env, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        audit = json.loads(proc.stdout)
        hsi = next(j for j in audit["jobs"] if j["id"] == "hsi_sync")
        self.assertTrue(hsi["enabled"], hsi)


class TestNoNetworkProbe(_SelectionSandbox):
    """«Sistema» and Settings: without a selection no urllib call is made
    and no default localhost URL is invented — even with HSI_URL set, the
    selection gate fires before any URL resolution."""

    def _cluster(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            cluster = system_status._cluster_hsi()
        self.urlopen = urlopen
        return cluster

    def test_hsi_cluster_names_the_state_and_probes_nothing(self):
        # Slice 7 (2026-11-10): without a selection the card is still there,
        # naming HOW a turno reaches HSI here — but nothing is probed: no
        # selection, no URL resolution, no network call at all.
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        with mock.patch("app.core.hsi_extension.install", return_value=None), \
                mock.patch("app.core.hsi_extension.store_url", return_value=None):
            cluster = self._cluster()
        self.assertIsNotNone(cluster)
        self.assertEqual(cluster["items"][0]["label"], "Modo")
        self.assertEqual(cluster["items"][0]["valor"], "Apagado")
        labels = [item["label"] for item in cluster["items"]]
        self.assertNotIn("Sincronización", labels)
        self.assertNotIn("Conectividad", labels)
        self.urlopen.assert_not_called()

    def test_hsi_admin_section_is_absent_without_a_selection(self):
        section_ids = {
            item if isinstance(item, str) else item["id"]
            for item in app_modules.sections_payload_for_role("admin")
        }
        self.assertNotIn("hsi", section_ids)
        self.assertNotIn("hsi", app_modules.enabled_admin_sections())

    def test_hsi_admin_section_returns_only_for_the_hsi_connector(self):
        self._select("hsi")
        section_ids = {
            item if isinstance(item, str) else item["id"]
            for item in app_modules.sections_payload_for_role("admin")
        }
        self.assertIn("hsi", section_ids)
        self.assertIn("hsi", app_modules.enabled_admin_sections())

    def test_selected_hsi_still_probes_the_configured_url(self):
        self._select("hsi")
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        cluster = self._cluster()
        # Slice 7: the mode row comes first; the probe row follows it, and
        # the probe still fires exactly once with the configured URL.
        self.assertEqual(cluster["items"][0]["label"], "Modo")
        self.assertEqual(cluster["items"][0]["valor"], "Envío desde el servidor")
        labels = [item["label"] for item in cluster["items"]]
        self.assertIn("Sincronización", labels)
        self.assertEqual(self.urlopen.call_count, 1)
        requested = self.urlopen.call_args.args[0]
        self.assertEqual(getattr(requested, "full_url", str(requested)),
                         "http://hsi.institution.invalid:8280")

    def test_settings_probe_refuses_without_selection(self):
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        with mock.patch("urllib.request.urlopen") as urlopen:
            resultado = settings_admin._probar_hsi()
        self.assertFalse(resultado["ok"])
        self.assertIn("conector EMR", resultado["detalle"])
        urlopen.assert_not_called()

    def test_settings_probe_keeps_legacy_default_when_selected(self):
        # An explicitly selected HSI with no HSI_URL keeps today's legacy
        # localhost default — that behavior belongs to the selection.
        self._select("hsi")
        with mock.patch("urllib.request.urlopen") as urlopen:
            settings_admin._probar_hsi()
        self.assertEqual(urlopen.call_count, 1)
        requested = urlopen.call_args.args[0]
        self.assertEqual(getattr(requested, "full_url", str(requested)),
                         "http://localhost:8280")

    def test_settings_card_hidden_without_selection(self):
        grupos = settings_admin._api_keys_payload()
        self.assertNotIn("hsi", [g["id"] for g in grupos])

    def test_settings_card_present_when_selected(self):
        self._select("hsi")
        grupos = settings_admin._api_keys_payload()
        self.assertIn("hsi", [g["id"] for g in grupos])

    def test_selected_bridge_has_generic_settings_but_never_hsi_settings(self):
        self._select("emr_bridge")
        ids = [g["id"] for g in settings_admin._api_keys_payload()]
        self.assertIn("emr_bridge", ids)
        self.assertNotIn("hsi", ids)

    def test_unconfigured_bridge_settings_probe_degrades_without_network(self):
        self._select("emr_bridge")
        with mock.patch("urllib.request.urlopen") as urlopen:
            resultado = settings_admin._probar_emr_bridge()
        self.assertFalse(resultado["ok"])
        self.assertIn("no respondió", resultado["detalle"])
        urlopen.assert_not_called()

    def test_selected_bridge_hsi_probe_is_refused_without_network(self):
        self._select("emr_bridge")
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        with mock.patch("urllib.request.urlopen") as urlopen:
            resultado = settings_admin._probar_hsi()
        self.assertFalse(resultado["ok"])
        urlopen.assert_not_called()

    def test_unconfigured_bridge_has_generic_status_not_hsi_or_network(self):
        self._select("emr_bridge")
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        with mock.patch.object(system_status, "_probe_json", return_value=(None, "offline")), \
                mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(system_status._cluster_hsi())
            payload = system_status.status(type("Request", (), {"headers": {}})())
        clusters = {cluster["clave"]: cluster for cluster in payload["clusters"]}
        self.assertNotIn("hsi", clusters)
        self.assertEqual(clusters["emr_bridge"]["items"][0]["valor"], "Incompleta")
        urlopen.assert_not_called()

    def test_configured_bridge_health_probes_only_its_own_url(self):
        self._select("emr_bridge")
        _env.set_env(self, EMR_BRIDGE_URL="https://bridge.institution.invalid",
                     EMR_BRIDGE_TOKEN="test-token",
                     HSI_URL="http://hsi.institution.invalid:8280")
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
            cluster = system_status._cluster_emr_bridge()
        self.assertEqual(cluster["estado"], "ok")
        requested = urlopen.call_args.args[0]
        self.assertEqual(requested.full_url, "https://bridge.institution.invalid/v1/health")

    def test_bridge_programmer_failure_is_not_reported_as_connectivity(self):
        self._select("emr_bridge")
        _env.set_env(self, EMR_BRIDGE_URL="https://bridge.institution.invalid",
                     EMR_BRIDGE_TOKEN="test-token")
        with mock.patch(
            "app.connectors.emr_bridge.adapter.EnterpriseBridgeConnector.health",
            side_effect=AssertionError("programmer defect"),
        ):
            with self.assertRaisesRegex(AssertionError, "programmer defect"):
                system_status._cluster_emr_bridge()


class TestNoEmrLinks(_SelectionSandbox):
    """Appointment payloads: without a selection there is no HSI deep
    link and no default base URL — `hsi_url`/`hsi_patient_url` are None
    even for a row that carries a mirrored hsi_patient_id."""

    ROW = {
        "id": 5, "estado": "aprobada",
        "turno_dt": datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        "nombre": "Ana", "apellido": "Prueba", "dni": "30111222",
        "servicio_id": "clinica-medica", "hsi_patient_id": "4242",
    }

    def test_web_base_is_none_without_selection(self):
        _env.set_env(self, HSI_URL="http://hsi.institution.invalid:8280")
        self.assertIsNone(doctor_appointments._hsi_web_base())

    def test_no_links_on_the_appointment_payload(self):
        public = doctor_appointments._appointment_public(dict(self.ROW))
        self.assertIsNone(public["hsi_url"])
        self.assertIsNone(public["hsi_patient_url"])

    def test_selected_hsi_keeps_links_and_legacy_default_base(self):
        self._select("hsi")
        self.assertEqual(doctor_appointments._hsi_web_base(),
                         "http://localhost:8280")
        public = doctor_appointments._appointment_public(dict(self.ROW))
        self.assertEqual(public["hsi_url"], "http://localhost:8280")
        self.assertEqual(public["hsi_patient_url"],
                         "http://localhost:8280/institucion/1/pacientes/profile/4242")

    def test_selected_hsi_honors_configured_web_url(self):
        self._select("hsi")
        _env.set_env(self, TURNOS_HSI_WEB_URL="https://hsi.pinamar.gob.ar")
        public = doctor_appointments._appointment_public(dict(self.ROW))
        self.assertEqual(public["hsi_url"], "https://hsi.pinamar.gob.ar")
        self.assertTrue(public["hsi_patient_url"].startswith("https://hsi.pinamar.gob.ar/"))

    def test_selected_bridge_never_yields_an_hsi_base(self):
        self._select("emr_bridge")
        _env.set_env(self, HSI_URL="http://bridge-illustrative.invalid:9443")
        public = doctor_appointments._appointment_public(dict(self.ROW))
        self.assertIsNone(doctor_appointments._hsi_web_base())
        self.assertIsNone(public["hsi_url"])
        self.assertIsNone(public["hsi_patient_url"])


class TestFreshProcessCoreBootsHsiIdle(unittest.TestCase):
    """Fresh-interpreter proof (no earlier import can hide an edge): with
    a clean environment the ASGI composition and the worker registry both
    build while the HSI journal path stays completely idle — no task, no
    queue, no vendor module loaded."""

    PROBE = textwrap.dedent(
        """
        import asyncio
        import importlib.abc
        import pathlib
        import shutil
        import sys
        import tempfile

        attempted_vendor_imports = []

        class VendorPackageAbsent(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "app.connectors.hsi" or fullname.startswith("app.connectors.hsi."):
                    attempted_vendor_imports.append(fullname)
                    raise ImportError("vendor package absent: " + fullname)
                return None

        sys.meta_path.insert(0, VendorPackageAbsent())

        from app import connectors
        import procrastinate
        import procrastinate.testing
        from app import worker
        from app.modules.turnos import hsi_journal_queue

        assert connectors.resolve(connectors.EMR_SYNC) == (None, None)

        app = procrastinate.App(connector=procrastinate.testing.InMemoryConnector())
        queues = worker._register_jobs(app)
        assert hsi_journal_queue._QUEUE not in queues, queues
        assert hsi_journal_queue._TASK_NAME not in app.tasks, app.tasks

        async def boot():
            await worker._defer_startup_jobs(app)
            jobs = getattr(app.connector, "jobs", {})
            rows = list(jobs.values()) if isinstance(jobs, dict) else list(jobs)
            assert not [j for j in rows
                        if j.get("task_name") == hsi_journal_queue._TASK_NAME], rows

        asyncio.run(boot())

        with tempfile.TemporaryDirectory(prefix="core-connectors-") as tmp:
            shutil.copytree(
                pathlib.Path.cwd() / "app" / "connectors" / "emr_bridge",
                pathlib.Path(tmp) / "emr_bridge",
            )
            connectors.ROOT = tmp
            connectors._MANIFEST_MEMO.clear()
            # An install with only the Enterprise bridge package installed and
            # nothing selected still resolves nothing for emr_sync.
            assert connectors.resolve(connectors.EMR_SYNC) == (None, None)

        assert not any(
            name == "app.connectors.hsi" or name.startswith("app.connectors.hsi.")
            for name in sys.modules
        )
        assert not attempted_vendor_imports, attempted_vendor_imports
        print("core-boots-hsi-idle-ok")
        """
    )

    def test_core_and_worker_build_with_zero_hsi_lifecycle(self):
        env = {"PATH": os.environ.get("PATH", ""),
               "TURNOS_ENV_FILE": os.devnull,
               "TURNOS_FAKE_AI": "1"}
        result = subprocess.run([sys.executable, "-c", self.PROBE],
                                cwd=ROOT, env=env, text=True,
                                capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0,
                         f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertEqual(result.stdout.splitlines()[-1], "core-boots-hsi-idle-ok")


if __name__ == "__main__":
    unittest.main()
