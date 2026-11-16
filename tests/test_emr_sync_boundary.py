"""Core side of the EMR sync boundary — generic-interface-only proof.

What this file proves, at runtime, with NO HSI/vendor import anywhere in
it (the fake connector below is a generic ``EmrSyncConnector``):

- ``modules/turnos`` reaches conflict resolution ONLY through the generic
  ``EmrSyncConnector`` entry points (``run_conflict_resolution`` /
  ``conflict_panel`` / ``apply_conflict_resolution``) resolved via
  ``connectors.adapter_for(EMR_SYNC)`` — every Core surface runs end to
  end against the fake, with its recorded calls and returns flowing
  through the real Core functions.
- connectors without a resolver inherit the ABC's documented safe
  defaults (real ABC code, not a mock).
- importing and exercising those Core surfaces loads NO ``connectors.hsi``
  module at all — the vendor edge is gone from Core, not merely hidden,
  and no Core module holds a vendor module reference.

The vendor side of the same boundary (explicit ``CONNECTOR_EMR_SYNC=hsi``
resolving to ``HsiConnector`` and delegating to ``connectors/hsi/hsi_resolver.py``)
is proven in ``test_hsi_connector.py``, and the resolver's own decision
rules in ``test_hsi_resolver.py``.  Moving the HSI vendor package out of
this repository into the proprietary Enterprise service is a LATER slice:
no such repository exists today, so ``connectors/hsi/`` remains a temporary
compatibility implementation, not a licensing boundary or completed split.
"""
import importlib
import importlib.util
import os
import subprocess
import sys
import textwrap
import types
import unittest
from contextlib import contextmanager
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app import connectors  # noqa: E402
from app.modules.turnos import booking_emr, booking_requests, hsi_journal  # noqa: E402 — the Core consumers


class FakeGenericConnector(connectors.EmrSyncConnector):
    """A generic EMR connector that records every resolver entry point.

    No HSI shape anywhere: this is what any third connector implementing
    the capability looks like to Core.
    """

    def __init__(self):
        self.calls = []

    def push_appointment(self, *args, **kwargs):
        return {"skipped": True}

    def run_conflict_resolution(self, now=None):
        self.calls.append(("run", now))
        return {"resueltos": 1, "abiertos": 0}

    def conflict_panel(self, limit=60):
        self.calls.append(("panel", limit))
        return {"abiertos": 0, "metricas": {"pct_total": 100}}

    def apply_conflict_resolution(self, action, conflict_ids,
                                  resolved_by="operador", now=None):
        self.calls.append(("apply", action, list(conflict_ids), resolved_by))
        return {"ok": True, "resultados": [{"conflicto_id": i, "ok": True}
                                           for i in conflict_ids]}


@contextmanager
def _with_fake_connector(fake):
    """Route EMR_SYNC to `fake` through the same seam production uses;
    every other capability keeps its real routing.

    The journal now correctly requires an explicit EMR selection before it
    runs.  Model that selection here as well as the adapter, so this remains a
    generic Core-boundary proof rather than relying on a vendor manifest.
    """
    real_adapter_for = connectors.adapter_for
    real_resolve = connectors.resolve

    def _route(capability):
        return fake if capability == connectors.EMR_SYNC \
            else real_adapter_for(capability)

    def _resolve(capability):
        if capability == connectors.EMR_SYNC:
            return ("generic_test", "explicit") if fake is not None \
                else (None, None)
        return real_resolve(capability)

    with mock.patch.object(connectors, "adapter_for", _route), \
            mock.patch.object(connectors, "resolve", _resolve):
        yield


def _run_core_surfaces(testcase, fake):
    """Drive every Core resolver surface once against `fake`."""
    with _with_fake_connector(fake), \
            mock.patch.object(booking_requests.store, "hsi_sync_status",
                              return_value={"counts": {}, "outbox": [],
                                            "conflicts": []}):
        testcase.assertEqual(booking_emr._hsi_resolver_run_core()[0], 200)
        testcase.assertEqual(booking_emr._hsi_sync_status_core()[0], 200)
        testcase.assertEqual(booking_emr._hsi_conflicts_apply_core(
            {"usuario": "rosa"}, {"accion": "mover", "conflictos": ["5"]})[0], 200)
    with _with_fake_connector(fake), \
            mock.patch.object(hsi_journal, "backfill_pending", return_value=0), \
            mock.patch.object(hsi_journal, "replay", return_value=0), \
            mock.patch.object(hsi_journal, "reconcile",
                              return_value={"schedules": 0, "appointments": 0,
                                            "patients": 0, "conflicts": 0}):
        testcase.assertEqual(hsi_journal.tick()["resolved"], {"resueltos": 1, "abiertos": 0})


class TestAbcDefaultsDegradeSafely(unittest.TestCase):
    """A connector that implements only the abstract minimum inherits the
    documented safe defaults — real ABC code at runtime."""

    def test_minimal_connector_gets_the_documented_defaults(self):
        class Minimal(connectors.EmrSyncConnector):
            def push_appointment(self, *args, **kwargs):
                return {"skipped": True}

        conn = Minimal()
        self.assertEqual(conn.run_conflict_resolution(), {})
        self.assertIsNone(conn.conflict_panel())
        self.assertEqual(conn.apply_conflict_resolution("mover", [1]),
                         {"ok": False, "error": "resolucion_no_disponible",
                          "resultados": []})
        self.assertFalse(conn.supports_auto_resolution())


class TestCoreSurfacesUseOnlyTheGenericInterface(unittest.TestCase):
    """The real Core functions, driven by a generic fake connector."""

    def test_resolver_run_core_round_trips_the_generic_entry_points(self):
        fake = FakeGenericConnector()
        with _with_fake_connector(fake):
            status, payload = booking_emr._hsi_resolver_run_core()
        self.assertEqual(status, 200)
        self.assertEqual(payload["resumen"], {"resueltos": 1, "abiertos": 0})
        self.assertEqual(payload["panel"], {"abiertos": 0,
                                            "metricas": {"pct_total": 100}})
        self.assertIn(("run", None), fake.calls)
        self.assertIn(("panel", 60), fake.calls)

    def test_sync_status_core_builds_the_panel_through_the_connector(self):
        fake = FakeGenericConnector()
        with _with_fake_connector(fake), \
                mock.patch.object(booking_requests.store, "hsi_sync_status",
                                  return_value={"counts": {}, "outbox": [],
                                                "conflicts": []}):
            status, payload = booking_emr._hsi_sync_status_core()
        self.assertEqual(status, 200)
        self.assertEqual(payload["panel"]["metricas"]["pct_total"], 100)

    def test_conflicts_apply_core_forwards_the_operator_action(self):
        fake = FakeGenericConnector()
        with _with_fake_connector(fake):
            status, payload = booking_emr._hsi_conflicts_apply_core(
                {"usuario": "rosa"}, {"accion": "mover", "conflictos": ["5", "6"]})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual([r["conflicto_id"] for r in payload["resultados"]], [5, 6])
        self.assertIn(("apply", "mover", [5, 6], "rosa"), fake.calls)

    def test_journal_tick_resolves_through_the_connector(self):
        fake = FakeGenericConnector()
        with _with_fake_connector(fake), \
                mock.patch.object(hsi_journal, "backfill_pending", return_value=0), \
                mock.patch.object(hsi_journal, "replay", return_value=0), \
                mock.patch.object(hsi_journal, "reconcile",
                                  return_value={"schedules": 0, "appointments": 0,
                                            "patients": 0, "conflicts": 0}):
            outcome = hsi_journal.tick()
        self.assertEqual(outcome["resolved"], {"resueltos": 1, "abiertos": 0})

    def test_unrouted_capability_degrades_without_a_connector(self):
        with _with_fake_connector(None), \
                mock.patch.object(booking_requests.store, "hsi_sync_status",
                                  return_value={"counts": {}, "outbox": [],
                                                "conflicts": []}):
            status, payload = booking_emr._hsi_resolver_run_core()
            self.assertEqual((status, payload["resumen"], payload["panel"]),
                             (200, {}, None))
            status, payload = booking_emr._hsi_sync_status_core()
            self.assertEqual((status, payload["panel"]), (200, None))
            status, payload = booking_emr._hsi_conflicts_apply_core(
                {"usuario": "rosa"}, {"accion": "mover", "conflictos": ["5"]})
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"], "resolucion_no_disponible")
        with _with_fake_connector(None), \
                mock.patch.object(hsi_journal, "backfill_pending", return_value=0), \
                mock.patch.object(hsi_journal, "replay", return_value=0), \
                mock.patch.object(hsi_journal, "reconcile",
                                  return_value={"schedules": 0, "appointments": 0,
                                            "patients": 0, "conflicts": 0}):
            self.assertEqual(hsi_journal.tick()["resolved"], {})


class TestCoreLoadsNoVendorModules(unittest.TestCase):
    """Runtime proof that the vendor edge is absent from Core — no
    ``connectors.hsi`` module is loaded or referenced by exercising the
    Core surfaces, and this test file itself never imports one."""

    def test_exercising_core_loads_no_vendor_modules(self):
        before = {name for name in sys.modules if name.startswith("app.connectors.hsi")}
        importlib.import_module("app.modules.turnos.booking_requests")
        importlib.import_module("app.modules.turnos.hsi_journal")
        _run_core_surfaces(self, FakeGenericConnector())
        after = {name for name in sys.modules if name.startswith("app.connectors.hsi")}
        self.assertEqual(after - before, set(),
                         "Core loaded vendor modules: %s" % (after - before))

    def test_no_core_module_holds_a_vendor_module_reference(self):
        importlib.import_module("app.modules.turnos.booking_requests")
        importlib.import_module("app.modules.turnos.hsi_journal")
        core_mods = [m for name, m in sys.modules.items()
                     if name.startswith(("app.modules.turnos.", "app.core."))
                     and isinstance(m, types.ModuleType)]
        self.assertTrue(core_mods)
        for mod in core_mods:
            for attr, value in vars(mod).items():
                if isinstance(value, types.ModuleType) \
                        and str(value.__name__).startswith("app.connectors.hsi"):
                    self.fail(f"{mod.__name__}.{attr} -> {value.__name__}")

    def test_no_resolver_copy_exists_in_core(self):
        self.assertIsNone(importlib.util.find_spec("app.modules.turnos.emr_resolver"))

    def test_asgi_composition_initializes_when_vendor_package_is_absent(self):
        """A clean process can build Core while the HSI package is missing.

        Blocking imports in a fresh interpreter is the executable equivalent
        of removing ``connectors/hsi`` from a Core distribution.  This is
        intentionally stronger than the in-process module census above: no
        earlier test can have populated ``sys.modules`` and hidden an import
        edge from the finder.
        """
        probe = textwrap.dedent(
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

            from fastapi import FastAPI
            from app import connectors
            from app import main as app

            async def exercise_lifespan():
                async with app.app.router.lifespan_context(app.app):
                    assert isinstance(app.app, FastAPI)
                    assert len(app.app.routes) > 0
                    assert callable(app.app.build_middleware_stack())

            with tempfile.TemporaryDirectory(prefix="core-connectors-") as tmp:
                # Model the eventual Core distribution: HSI is physically
                # absent while the generic Enterprise bridge remains.
                shutil.copytree(
                    pathlib.Path.cwd() / "app" / "connectors" / "emr_bridge",
                    pathlib.Path(tmp) / "emr_bridge",
                )
                connectors.ROOT = tmp
                connectors._MANIFEST_MEMO.clear()
                assert connectors.resolve(connectors.EMR_SYNC) == (None, None)
                asyncio.run(exercise_lifespan())
                assert isinstance(app.app, FastAPI)
                assert not any(
                    name == "app.connectors.hsi" or name.startswith("app.connectors.hsi.")
                    for name in sys.modules
                )
                assert not attempted_vendor_imports, attempted_vendor_imports
            print("core-asgi-without-hsi-ok")
            """
        )
        # A minimal environment proves the default Core startup shape and
        # prevents a developer's credentials, database URL, or OFF/delegation
        # flags from either contacting a real service or bypassing HSI startup.
        env = {
            "TURNOS_ASGI": "1",
            "TURNOS_ENV_FILE": os.devnull,
            "TURNOS_FAKE_AI": "1",
        }
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertTrue(result.stdout.splitlines())
        self.assertEqual(result.stdout.splitlines()[-1],
                         "core-asgi-without-hsi-ok")

    def test_worker_registry_builds_when_vendor_package_is_absent(self):
        """The Core worker composes its real registry without HSI code.

        The durable EMR journal remains Core-owned infrastructure: it is
        registered under its persisted compatibility task name even when no
        Enterprise connector is installed.  The name is opaque queue data,
        not an import edge.  This fresh-process proof prevents an earlier
        import from hiding a dependency on the vendor package.
        """
        probe = textwrap.dedent(
            """
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

            with tempfile.TemporaryDirectory(prefix="core-connectors-") as tmp:
                shutil.copytree(
                    pathlib.Path.cwd() / "app" / "connectors" / "emr_bridge",
                    pathlib.Path(tmp) / "emr_bridge",
                )
                connectors.ROOT = tmp
                connectors._MANIFEST_MEMO.clear()
                assert connectors.resolve(connectors.EMR_SYNC) == (None, None)

                app = procrastinate.App(
                    connector=procrastinate.testing.InMemoryConnector()
                )
                queues = worker._register_jobs(app)
                assert hsi_journal_queue._QUEUE not in queues, queues
                assert hsi_journal_queue._TASK_NAME not in app.tasks, app.tasks
                assert not any(
                    name == "app.connectors.hsi" or name.startswith("app.connectors.hsi.")
                    for name in sys.modules
                )
                assert not attempted_vendor_imports, attempted_vendor_imports
            print("core-worker-without-hsi-ok")
            """
        )
        env = {
            "TURNOS_ENV_FILE": os.devnull,
            "TURNOS_FAKE_AI": "1",
        }
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertTrue(result.stdout.splitlines())
        self.assertEqual(result.stdout.splitlines()[-1],
                         "core-worker-without-hsi-ok")


class TestCoreSourceHasNoVendorImportEdge(unittest.TestCase):
    """Source-level companion to the runtime proofs above (the same shape
    as test_hsi_connector.py's wire-client gate): Core files may not
    import the vendor package at all."""

    CORE_FILES = (
        "app/modules/turnos/booking_requests.py",
        "app/modules/turnos/booking_create.py",
        "app/modules/turnos/booking_referrals.py",
        "app/modules/turnos/booking_queue.py",
        "app/modules/turnos/booking_decisions.py",
        "app/modules/turnos/booking_patient.py",
        "app/modules/turnos/booking_capacity.py",
        "app/modules/turnos/booking_emr.py",
        "app/modules/turnos/panel_settings.py",
        "app/modules/turnos/hsi_journal.py",
        "app/modules/turnos/hsi_journal_queue.py",
        "app/modules/turnos/hsi_fallback.py",
    )
    # hsi_journal_queue's persisted task name "connectors.hsi.hsi_sync.tick"
    # is a queue-row compatibility string, not an import — it matches none
    # of these patterns.
    FORBIDDEN = ("from app.connectors.hsi import", "import app.connectors.hsi",
                 "from app.connectors.hsi.", "app.connectors.hsi.hsi_resolver")

    def test_core_files_do_not_import_the_vendor_package(self):
        for relative in self.CORE_FILES:
            with open(os.path.join(ROOT, relative), encoding="utf-8") as source:
                text = source.read()
            for forbidden in self.FORBIDDEN:
                self.assertNotIn(forbidden, text, f"{relative}: {forbidden}")


if __name__ == "__main__":
    unittest.main()
