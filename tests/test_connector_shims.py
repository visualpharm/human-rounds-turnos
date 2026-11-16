"""Tests for the Phase 6b connectors package: renaper/puco/refeps live only
under connectors/<id>/ now — the repo-root compat shims that used to make
`import renaper` (etc.) resolve to the same module object were retired in
Tier 2 batch L (2026-08-07, docs/roadmap/tier2-physical-modularization-plan.md)
once every importer and every `mock.patch("<old_name>...")` string had been
rewritten to the dotted `connectors.<id>.<mod>` path (see
docs/connectors.md's "Compat shims (retired ...)" section). This file now
proves the retirement stuck (the bare root name is really gone) and that the
direct import + monkeypatch story the shims used to guarantee still holds
through the real package path.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import _env  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestShimsRetired(unittest.TestCase):
    """The nine root shim files are gone — the bare top-level name must no
    longer resolve to anything. A regression here means someone re-added a
    compat shim (or restored the old file) instead of using the connectors
    package directly."""

    def test_renaper_has_no_root_module(self):
        with self.assertRaises(ImportError):
            __import__("renaper")

    def test_puco_has_no_root_module(self):
        with self.assertRaises(ImportError):
            __import__("puco")

    def test_refeps_has_no_root_module(self):
        with self.assertRaises(ImportError):
            __import__("refeps")

    def test_direct_import_works_and_monkeypatch_is_visible_repo_wide(self):
        # Every consumer now imports `connectors.refeps.refeps` directly (no
        # shim object to share), so a patch on that one canonical module is
        # by construction the same object every caller sees.
        from app.connectors.refeps import refeps
        original = refeps.buscar_matricula
        try:
            refeps.buscar_matricula = lambda *a, **kw: ["patched"]
            self.assertEqual(refeps.buscar_matricula(), ["patched"])
        finally:
            refeps.buscar_matricula = original


class TestConnectorManifestsAndAdapters(unittest.TestCase):
    def test_renaper_manifest_matches_capability(self):
        from app import connectors
        manifest = connectors.load_manifest("renaper")
        self.assertEqual(manifest["capability"], connectors.IDENTITY_VERIFICATION)
        self.assertEqual(manifest["country"], "AR")

    def test_puco_manifest_matches_capability(self):
        from app import connectors
        manifest = connectors.load_manifest("puco")
        self.assertEqual(manifest["capability"], connectors.INSURANCE_LOOKUP)

    def test_refeps_manifest_matches_capability(self):
        from app import connectors
        manifest = connectors.load_manifest("refeps")
        self.assertEqual(manifest["capability"], connectors.LICENSE_LOOKUP)

    def test_renaper_adapter_authenticates_stub_identity(self):
        # File-isolated (see the DB-pollution note on the test right below)
        # even though this one is a plain read of a seeded identity.
        from app.connectors.renaper.adapter import RenaperConnector
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "identities.json")
            with _env.patched_env({"TURNOS_TEST_IDENTITIES_PATH": store}, clear=False):
                conn = RenaperConnector()
                person = conn.authenticate("99000001", provider="stub")
                self.assertEqual(person["dni"], "99000001")

    def test_renaper_adapter_synthesizes_an_unregistered_identity(self):
        # The product owner, 2026-08-03: the stub accepts ANY plausible DNI now — it no
        # longer raises IdentityNotFound, it synthesizes a persona instead.
        # File-isolated (TURNOS_TEST_IDENTITIES_PATH) so this doesn't write
        # a throwaway identity into the real dev database.
        from app.connectors.renaper.adapter import RenaperConnector
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "identities.json")
            with _env.patched_env({"TURNOS_TEST_IDENTITIES_PATH": store}, clear=False):
                conn = RenaperConnector()
                person = conn.authenticate("00000000", provider="stub")
                self.assertEqual(person["dni"], "00000000")

    def test_puco_adapter_reports_availability_and_looks_up(self):
        _env.set_env(self, TURNOS_FAKE_PUCO="1")
        from app.connectors.puco.adapter import PucoConnector
        conn = PucoConnector()
        self.assertTrue(conn.available())
        ok, obras = conn.lookup("12345670")
        self.assertTrue(ok)

    def test_refeps_adapter_wraps_refeps_error_as_provider_unavailable(self):
        from app import connectors
        from app.connectors.refeps import refeps as real
        from app.connectors.refeps.adapter import RefepsConnector
        original = real.buscar_matricula

        def boom(*a, **kw):
            raise real.RefepsError("boom")

        real.buscar_matricula = boom
        try:
            conn = RefepsConnector()
            with self.assertRaises(connectors.ProviderUnavailable):
                conn.search_by_license("12345")
        finally:
            real.buscar_matricula = original

    def test_webturno_adapter_normalizes_slots_to_the_shared_shape(self):
        from app.connectors.webturno import webturno as real
        from app.connectors.webturno.adapter import WebturnoConnector

        class FakeWebturno:
            def slots(self, clinic, idme):
                self.seen = (clinic, idme)
                return [{"fecha": "2099-01-02T09:30:00", "idtur": "slot-1"}]

        fake = FakeWebturno()
        with patch.object(real, "Webturno", lambda: fake):
            result = WebturnoConnector().slots_for_service(
                {"clinic_token": "clinic", "idme": "doctor", "medico": "Doctor Example"})
        self.assertEqual(fake.seen, ("clinic", "doctor"))
        self.assertEqual(result, {"medicos": [{
            "medico": "Doctor Example", "medico_id": "doctor",
            "turnos": [{"dia": "2099-01-02", "hora": "09:30", "idtur": "slot-1"}],
        }]})

    def test_renaper_adapter_forwards_existing_user(self):
        # Registry rewire (2026-08-20): the login reaches renaper.authenticate
        # through the adapter, so the adapter MUST forward **kwargs —
        # `existing_user` is how the stub knows the DNI belongs to a real
        # account and refuses it (2026-10-23: never opened, never copied).
        # Dropping it would silently let the stub synthesize a persona over
        # a real account. File-isolated identity store, as the tests above.
        from app.connectors import IdentityNotFound
        from app.connectors.renaper import renaper as real
        from app.connectors.renaper.adapter import RenaperConnector
        civil = {"nombre": "Real", "apellido": "Account", "nacimiento": "1980-01-01",
                 "sexo": "F"}
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "identities.json")
            with _env.patched_env({"TURNOS_TEST_IDENTITIES_PATH": store}, clear=False):
                with self.assertRaises(IdentityNotFound):
                    RenaperConnector().authenticate(
                        "30111222", provider="stub", existing_user=civil)
                self.assertIsNone(real.get_test_identity("30111222"))

    def test_renaper_adapter_call_reaches_the_impl_verbatim(self):
        from app.connectors.renaper import renaper as real
        from app.connectors.renaper.adapter import RenaperConnector
        sentinel = {"nombre": "X"}
        with patch.object(real, "authenticate", return_value={"dni": "30111222"}) as auth:
            out = RenaperConnector().authenticate("30111222", provider="stub",
                                                  existing_user=sentinel)
        self.assertEqual(out, {"dni": "30111222"})
        auth.assert_called_once_with("30111222", "stub", existing_user=sentinel)

    def test_puco_adapter_cached_reads_cache_only(self):
        # `cached()` is what the registration POST's coverage attach reads
        # (cuentas/accounts.py): never a network call, [] before any lookup,
        # exactly puco.cacheada() after one.
        from app.connectors.puco import puco
        from app.connectors.puco.adapter import PucoConnector
        dni = "12345670"  # fake mode: last digit 0 -> IOMA
        puco._CACHE.pop(dni, None)
        self.addCleanup(puco._CACHE.pop, dni, None)
        _env.set_env(self, TURNOS_FAKE_PUCO="1")
        conn = PucoConnector()
        self.assertEqual(conn.cached(dni), [])
        ok, obras = conn.lookup(dni)
        self.assertTrue(ok)
        self.assertTrue(obras)
        self.assertEqual(conn.cached(dni), obras)
        self.assertEqual(conn.cached(dni), puco.cacheada(dni))
        with patch.object(puco, "consultar", side_effect=AssertionError("cached() called out")):
            self.assertEqual(conn.cached(dni), obras)


if __name__ == "__main__":
    unittest.main()
