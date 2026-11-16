"""Phase 6b: hsi connector package — manifest, adapter, and (post Tier 2 batch
L, 2026-08-07) proof that the four repo-root compat shims (`hsi_client.py`,
`hsi_sync.py`, `hsi_resolver.py`, `hsi_resync.py`) are actually gone and every
consumer's monkeypatch story still works through the real `connectors.hsi.*`
modules directly."""
import os
import sys
import unittest
from unittest import mock
import _env  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestHsiShimsRetired(unittest.TestCase):
    def test_none_of_the_four_root_modules_exist_anymore(self):
        for name in ("hsi_client", "hsi_sync", "hsi_resolver", "hsi_resync"):
            with self.assertRaises(ImportError):
                __import__(name)

    def test_monkeypatch_through_the_real_module_is_visible_to_lazy_importers(self):
        # booking_requests.py does `from connectors.hsi import hsi_client`
        # lazily inside a function; simulate that by importing fresh here and
        # confirming a patch made on the one real module object is visible.
        from app.connectors.hsi import hsi_client
        original = hsi_client.push_turno
        try:
            hsi_client.push_turno = lambda *a, **kw: {"estado": "patched"}
            import importlib
            reimported = importlib.import_module("app.connectors.hsi.hsi_client")
            self.assertEqual(reimported.push_turno()["estado"], "patched")
        finally:
            hsi_client.push_turno = original


class TestHsiManifestAndAdapter(unittest.TestCase):
    def test_manifest_matches_capability(self):
        from app import connectors
        manifest = connectors.load_manifest("hsi")
        self.assertEqual(manifest["capability"], connectors.EMR_SYNC)
        self.assertEqual(manifest["country"], "AR")

    def test_adapter_skips_cleanly_with_turnos_skip_hsi(self):
        _env.set_env(self, TURNOS_SKIP_HSI="1")
        from app.connectors.hsi.adapter import HsiConnector
        conn = HsiConnector()
        result = conn.push_appointment(
            "20111222", "Ana", "Pérez", "1990-01-01", "F",
            "Cardiología", "2026-08-01T09:00:00")
        self.assertEqual(result, {"skipped": True})

    def test_core_booking_and_journal_do_not_import_the_hsi_wire_client(self):
        """The temporary HSI adapter owns the vendor import; the Core paths
        use the EMR connector boundary and can select Enterprise instead."""
        for relative in (
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
        ):
            with open(os.path.join(ROOT, relative), encoding="utf-8") as source:
                self.assertNotIn("from app.connectors.hsi import hsi_client", source.read(), relative)

    def test_adapter_projects_hsi_result_without_preserving_wire_aliases(self):
        from app.connectors.hsi import adapter
        from app.connectors.hsi.adapter import HsiConnector

        class Wire:
            @staticmethod
            def push_turno(*_args, **_kwargs):
                return {
                    "estado": "ok", "appointment_id": 44, "patient_id": 55,
                    "actual_turno_dt": "2026-09-02T09:30:00-03:00",
                    "checkpoint": {"id": 44, "diaryId": 7, "patientId": 55,
                                   "date": "2026-09-02", "hour": "09:30:00",
                                   "appointmentStateId": 1, "updatedOn": "v2"},
                    "vendor_debug": "must not cross the boundary",
                }

        with mock.patch.object(adapter, "_impl_module", return_value=Wire):
            result = HsiConnector().execute_intent("create", {
                "dni": "30111222", "nombre": "Ana", "apellido": "Example",
                "when": "2026-09-02T09:30:00-03:00",
            }, "core-key")
        self.assertEqual(result["checkpoint"], {
            "appointment_id": "44", "schedule_id": "7", "patient_id": "55",
            "scheduled_at": "2026-09-02T09:30:00-03:00", "state": "scheduled",
            "version": "v2",
        })
        self.assertNotIn("vendor_debug", result)
        self.assertEqual(set(result), {"estado", "appointment_id", "patient_id",
                                       "actual_turno_dt", "requested_turno_dt", "deduplicated",
                                       "slot_changed", "sobreturno", "checkpoint"})

    def test_adapter_rejects_an_unknown_normalized_checkpoint_state(self):
        from app.connectors import ProviderUnavailable
        from app.connectors.hsi import normalization

        with self.assertRaises(ProviderUnavailable):
            normalization.checkpoint_to_hsi({
                "appointment_id": "44", "state": "vendor_pending",
                "scheduled_at": "2026-09-02T09:30:00-03:00",
            })

    def test_hsi_checkpoint_ids_cross_wire_boundary_losslessly(self):
        from app.connectors import ProviderUnavailable
        from app.connectors.hsi import normalization
        core = {"appointment_id": "44", "schedule_id": "7", "patient_id": "55",
                "state": "scheduled", "scheduled_at": "2026-09-02T09:30:00-03:00"}
        wire = normalization.checkpoint_to_hsi(core)
        self.assertEqual((wire["id"], wire["diaryId"], wire["patientId"]), (44, 7, 55))
        self.assertEqual(normalization.checkpoint_from_hsi(wire)["appointment_id"], "44")
        for bad in (None, 0, -1, True, 1.5, [44], "44x", " 44"):
            with self.subTest(bad=bad):
                with self.assertRaises(ProviderUnavailable):
                    normalization.checkpoint_to_hsi(dict(core, schedule_id=bad))

    def test_legacy_read_observes_only_one_exact_scheduled_target(self):
        from app.connectors.hsi import adapter
        from app.connectors.hsi.adapter import HsiConnector

        class Wire:
            rows = []
            moves = 0

            class HsiClient:
                def login(self):
                    pass

                def appointments_for_diary(self, diary, start, end):
                    self.assert_query = (diary, start, end)
                    return Wire.rows

                def reassign_appointment(self, *_args):
                    Wire.moves += 1
                    raise AssertionError("read path must never move")

        payload = {"appointment_id": "44", "checkpoint": {
            "appointment_id": "44", "schedule_id": "7", "patient_id": "55",
            "scheduled_at": "2026-09-01T09:30:00-03:00", "state": "scheduled"},
            "when": "2026-09-02T12:30:00Z"}
        b = {"id": 44, "patientId": 55, "date": "2026-09-02", "hour": "09:30:00",
             "appointmentStateId": 1, "updatedOn": "v2"}
        with mock.patch.object(adapter, "_impl_module", return_value=Wire):
            conn = HsiConnector()
            Wire.rows = [b]
            result = conn.observe_intent("reassign", payload)
            self.assertEqual(result["checkpoint"], {
                "appointment_id": "44", "schedule_id": "7", "patient_id": "55",
                "scheduled_at": "2026-09-02T09:30:00-03:00", "state": "scheduled",
                "version": "v2"})
            self.assertEqual(result["actual_turno_dt"], "2026-09-02T09:30:00-03:00")
            for rows in ([], [b, dict(b)], [dict(b, patientId=56)],
                         [dict(b, appointmentStateId=4)], [dict(b, hour="09:45:00")],
                         [dict(b, appointmentStateId=99)], [dict(b, diaryId=8)],
                         [dict(b, hour="malformed")],
                         [dict(b, hour="09:30:00junk")],
                         [dict(b, patient={"id": 56})]):
                with self.subTest(rows=rows):
                    Wire.rows = rows
                    self.assertIsNone(conn.observe_intent("reassign", payload))
            self.assertEqual(Wire.moves, 0)


class TestResolverEntryPoints(unittest.TestCase):
    """The vendor side of the EMR resolver boundary (the Core side is
    test_emr_sync_boundary.py, which imports nothing from this package):
    explicit ``CONNECTOR_EMR_SYNC=hsi`` resolves to HsiConnector, and its generic
    EmrSyncConnector entry points delegate to the vendor resolver that
    stays inside connectors/hsi/."""

    def _clean_selection(self):
        from app.core import config
        saved = config.ENV_PATH
        config.ENV_PATH = ""  # the checkout's .env never participates
        config._reset_cache_para_tests()
        return saved

    def _restore_selection(self, saved):
        from app.core import config
        config.ENV_PATH = saved
        config._reset_cache_para_tests()

    def test_unconfigured_registry_is_off_and_explicit_hsi_resolves(self):
        from app import connectors
        from app.core import config
        from unittest import mock
        saved = self._clean_selection()
        try:
            with _env.patched_env(CONNECTOR_EMR_SYNC=None):
                config._reset_cache_para_tests()  # re-read after the pop
                self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                                 (None, None))
                self.assertIsNone(connectors.adapter_for(connectors.EMR_SYNC))
                with _env.patched_env(CONNECTOR_EMR_SYNC="hsi"):
                    config._reset_cache_para_tests()
                    self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                                     ("hsi", "explicit"))
                    adapter = connectors.adapter_for(connectors.EMR_SYNC)
        finally:
            self._restore_selection(saved)
        self.assertIsNotNone(adapter)
        self.assertEqual(type(adapter).__module__, "app.connectors.hsi.adapter")
        self.assertTrue(adapter.supports_auto_resolution())

    def test_entry_points_delegate_to_the_vendor_resolver(self):
        from app import connectors
        from app.core import config
        from app.connectors.hsi import hsi_resolver
        from unittest import mock
        calls = {"run": [], "panel": [], "apply": []}
        with _env.patched_env({"CONNECTOR_EMR_SYNC": "hsi"}), \
             mock.patch.object(config, "get", side_effect=lambda key, default=None, legacy=None:
                               "hsi" if key == "CONNECTOR_EMR_SYNC" else default), \
             mock.patch.object(hsi_resolver, "resolver_pass",
                               side_effect=lambda now=None:
                               calls["run"].append(now) or {"ran": True}), \
             mock.patch.object(hsi_resolver, "estado_panel",
                               side_effect=lambda limit=60:
                               calls["panel"].append(limit) or {"panel": True}), \
             mock.patch.object(hsi_resolver, "aplicar_resolucion",
                               side_effect=lambda accion, ids,
                               resuelto_por="operador", now=None:
                               calls["apply"].append((accion, ids, resuelto_por, now))
                               or {"ok": True, "resultados": []}):
            adapter = connectors.adapter_for(connectors.EMR_SYNC)
            self.assertEqual(adapter.run_conflict_resolution(now="lunes"), {"ran": True})
            self.assertEqual(adapter.conflict_panel(limit=5), {"panel": True})
            self.assertTrue(adapter.apply_conflict_resolution(
                "mover", [7], resolved_by="rosa", now="ahora")["ok"])
        self.assertEqual(calls["run"], ["lunes"])
        self.assertEqual(calls["panel"], [5])
        self.assertEqual(calls["apply"], [("mover", [7], "rosa", "ahora")])


if __name__ == "__main__":
    unittest.main()
