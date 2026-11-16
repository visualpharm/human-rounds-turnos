"""Isolation contracts for deployment-specific reference-pack tests."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

import _env
from app.core import config, data_packs, site_identity, theme
from app.patient_web import catalog, site_base, site_catalog
from _packtest import reference_pack_case, reference_pack_module, reference_pack_test


_KEYS = (
    "PACK_CHAIN",
    "TURNOS_WEBDATA_DIR",
    "TURNOS_THEME_DEFAULT",
    "TURNOS_PUBLIC_ORIGIN",
    "TURNOS_CONFIG_LOCK",
)


class ReferencePackIsolationTest(unittest.TestCase):
    def setUp(self):
        # Every key the pack activation may touch is listed (at its current value) so the fixture puts
        # all of them back, not only the two this setUp changes.
        _env.set_env(self, {**{key: os.environ.get(key) for key in _KEYS},
                            "PACK_CHAIN": "global,example-town", "TURNOS_CONFIG_LOCK": None})
        self.theme_cache = dict(theme._cache)
        theme._cache.update(v="humanrounds", t=123.0)

    def tearDown(self):
        theme._cache.clear()
        theme._cache.update(self.theme_cache)
        data_packs.invalidate()

    def _available(self):
        return patch.object(
            data_packs,
            "private_pack_dir",
            return_value="/synthetic/reference-pack",
        )

    def test_module_boundary_forces_lock_and_restores_exact_state(self):
        seeded = [
            (config._dotenv_cache, {"path": "seed", "vals": {"A": "B"}, "mtime": 7}),
            (config._db_cache, {"v": {"seed": True}, "t": 8.0}),
            (data_packs._MANIFEST_CACHE, {"seed": (1, {"id": "seed"})}),
            (data_packs._MERGE_CACHE, {("seed",): ((1,), ["value"])}),
            (data_packs._PRIVATE_READY_CACHE, {"seed": ((), True)}),
            (catalog._JSON_CACHE, {"seed": (1, {"value": True})}),
            (catalog._RAW_CACHE, {"seed": (1.0, [{"id": "seed"}])}),
            (site_base._CACHE, {"seed": (1, {"value": True})}),
            (site_catalog._SERVICES_MEMO, {"seed": {"value": True}}),
            (site_identity._CACHE, {"seed": (1, {"value": True})}),
        ]
        originals = []
        for mapping, value in seeded:
            originals.append((mapping, dict(mapping)))
            mapping.clear()
            mapping.update(value)
        activate, restore = reference_pack_module()
        try:
            with self._available(), patch.object(
                data_packs,
                "active_chain",
                return_value=("global", "ar", "ar-b", "ar-b-pinamar"),
            ), patch("_packtest._refresh_import_constants"):
                activate()
                self.assertEqual(os.environ["TURNOS_CONFIG_LOCK"], "1")
                self.assertEqual(
                    data_packs.active_chain(),
                    ("global", "ar", "ar-b", "ar-b-pinamar"),
                )
                restore()
            for mapping, value in seeded:
                self.assertEqual(mapping, value)
        finally:
            for mapping, original in originals:
                mapping.clear()
                mapping.update(original)
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)
        self.assertEqual(theme._cache, {"v": "humanrounds", "t": 123.0})

    def test_failed_original_setup_runs_registered_cleanup(self):
        class Broken(unittest.TestCase):
            def setUp(self):
                raise RuntimeError("synthetic setup failure")

            def runTest(self):
                pass

        with self._available(), patch.object(
            data_packs,
            "active_chain",
            return_value=("global", "ar", "ar-b", "ar-b-pinamar"),
        ), patch("_packtest._refresh_import_constants"):
            wrapped = reference_pack_case(Broken)()
            result = unittest.TestResult()
            wrapped.run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)
        self.assertEqual(theme._cache, {"v": "humanrounds", "t": 123.0})

    def test_keyboard_interrupt_from_case_setup_restores_immediately(self):
        class Interrupted(unittest.TestCase):
            def setUp(self):
                raise KeyboardInterrupt("synthetic interrupt")

            def runTest(self):
                pass

        with self._available(), patch.object(
            data_packs,
            "active_chain",
            return_value=("global", "ar", "ar-b", "ar-b-pinamar"),
        ), patch("_packtest._refresh_import_constants"):
            wrapped = reference_pack_case(Interrupted)()
            with self.assertRaises(KeyboardInterrupt):
                wrapped.setUp()
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)
        self.assertEqual(theme._cache, {"v": "humanrounds", "t": 123.0})

    def test_keyboard_interrupt_from_case_body_restores_immediately(self):
        class Interrupted(unittest.TestCase):
            def runTest(self):
                raise KeyboardInterrupt("synthetic body interrupt")

        with self._available(), patch.object(
            data_packs,
            "active_chain",
            side_effect=lambda: tuple(os.environ["PACK_CHAIN"].split(",")),
        ), patch("_packtest._refresh_import_constants"):
            with self.assertRaises(KeyboardInterrupt):
                reference_pack_case(Interrupted)().run(unittest.TestResult())
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)

    def test_method_boundary_selects_pack_before_original_setup(self):
        observations = []

        with self._available(), patch.object(
            data_packs,
            "active_chain",
            side_effect=lambda: tuple(os.environ["PACK_CHAIN"].split(",")),
        ), patch("_packtest._refresh_import_constants"):
            class Mixed(unittest.TestCase):
                def setUp(self):
                    observations.append((self._testMethodName, os.environ["PACK_CHAIN"]))

                @reference_pack_test
                def test_reference(self):
                    self.assertEqual(os.environ["PACK_CHAIN"], "global,ar,ar-b,ar-b-pinamar")

                def test_public(self):
                    self.assertEqual(os.environ["PACK_CHAIN"], "global,example-town")

            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(Mixed).run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(observations, [
            ("test_public", "global,example-town"),
            ("test_reference", "global,ar,ar-b,ar-b-pinamar"),
        ])
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")

    def test_method_boundary_keeps_pack_through_original_teardown(self):
        observations = []
        with self._available(), patch.object(
            data_packs,
            "active_chain",
            side_effect=lambda: tuple(os.environ["PACK_CHAIN"].split(",")),
        ), patch("_packtest._refresh_import_constants"):
            class Lifecycle(unittest.TestCase):
                def setUp(self):
                    observations.append(("setUp", os.environ["PACK_CHAIN"]))

                @reference_pack_test
                def test_reference(self):
                    observations.append(("body", os.environ["PACK_CHAIN"]))

                def tearDown(self):
                    observations.append(("tearDown", os.environ["PACK_CHAIN"]))

            result = unittest.TestResult()
            Lifecycle("test_reference").run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(observations, [
            ("setUp", "global,ar,ar-b,ar-b-pinamar"),
            ("body", "global,ar,ar-b,ar-b-pinamar"),
            ("tearDown", "global,ar,ar-b,ar-b-pinamar"),
        ])
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")

    def test_availability_probe_cache_mutation_is_restored(self):
        original = dict(data_packs._PRIVATE_READY_CACHE)
        data_packs._PRIVATE_READY_CACHE.clear()
        data_packs._PRIVATE_READY_CACHE["seed"] = ("original", True)

        def available(_pack_id):
            data_packs._PRIVATE_READY_CACHE["probe"] = ("mutated", True)
            return "/synthetic/reference-pack"

        activate, restore = reference_pack_module()
        try:
            with patch.object(data_packs, "private_pack_dir",
                              side_effect=available), patch.object(
                data_packs, "active_chain",
                return_value=("global", "ar", "ar-b", "ar-b-pinamar"),
            ), patch("_packtest._refresh_import_constants"):
                activate()
                restore()
            self.assertEqual(data_packs._PRIVATE_READY_CACHE,
                             {"seed": ("original", True)})
        finally:
            data_packs._PRIVATE_READY_CACHE.clear()
            data_packs._PRIVATE_READY_CACHE.update(original)

    def test_keyboard_interrupt_from_method_setup_restores_immediately(self):
        with self._available(), patch.object(
            data_packs,
            "active_chain",
            side_effect=lambda: tuple(os.environ["PACK_CHAIN"].split(",")),
        ), patch("_packtest._refresh_import_constants"):
            class Interrupted(unittest.TestCase):
                def setUp(self):
                    raise KeyboardInterrupt("synthetic interrupt")

                @reference_pack_test
                def test_reference(self):
                    pass

            case = Interrupted("test_reference")
            with self.assertRaises(KeyboardInterrupt):
                case.setUp()
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)
        self.assertEqual(theme._cache, {"v": "humanrounds", "t": 123.0})

    def test_keyboard_interrupt_from_method_body_restores_immediately(self):
        with self._available(), patch.object(
            data_packs,
            "active_chain",
            side_effect=lambda: tuple(os.environ["PACK_CHAIN"].split(",")),
        ), patch("_packtest._refresh_import_constants"):
            class Interrupted(unittest.TestCase):
                @reference_pack_test
                def test_reference(self):
                    raise KeyboardInterrupt("synthetic body interrupt")

            with self.assertRaises(KeyboardInterrupt):
                Interrupted("test_reference").run(unittest.TestResult())
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)

    def test_partial_activation_failure_rolls_back(self):
        activate, _ = reference_pack_module()
        with self._available(), patch(
            "_packtest._refresh_import_constants",
            side_effect=RuntimeError("synthetic refresh failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic refresh failure"):
                activate()
        self.assertEqual(os.environ.get("PACK_CHAIN"), "global,example-town")
        self.assertNotIn("TURNOS_CONFIG_LOCK", os.environ)
        self.assertEqual(theme._cache, {"v": "humanrounds", "t": 123.0})

    def test_configured_but_invalid_private_pack_fails_instead_of_skipping(self):
        original = dict(data_packs._PRIVATE_READY_CACHE)
        data_packs._PRIVATE_READY_CACHE.clear()
        data_packs._PRIVATE_READY_CACHE["seed"] = ("original", True)

        def invalid(_pack_id):
            data_packs._PRIVATE_READY_CACHE["probe"] = ("mutated", False)
            raise data_packs.RequiredPackError("synthetic corrupt pack")

        try:
            with patch.object(data_packs, "private_pack_dir",
                              side_effect=invalid), patch.object(
                data_packs, "private_packs_dir", return_value="/configured/private"
            ):
                class Invalid(unittest.TestCase):
                    @reference_pack_test
                    def test_reference(self):
                        self.fail("invalid pack must not reach the test body")

                result = unittest.TestResult()
                Invalid("test_reference").run(result)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("synthetic corrupt pack", result.errors[0][1])
            self.assertEqual(result.skipped, [])
            self.assertEqual(data_packs._PRIVATE_READY_CACHE,
                             {"seed": ("original", True)})
        finally:
            data_packs._PRIVATE_READY_CACHE.clear()
            data_packs._PRIVATE_READY_CACHE.update(original)

    def test_unconfigured_probe_skip_restores_cache_exactly(self):
        original = dict(data_packs._PRIVATE_READY_CACHE)
        data_packs._PRIVATE_READY_CACHE.clear()
        data_packs._PRIVATE_READY_CACHE["seed"] = ("original", True)

        def absent(_pack_id):
            data_packs._PRIVATE_READY_CACHE["probe"] = ("mutated", False)
            raise data_packs.RequiredPackError("synthetic absent pack")

        try:
            with patch.object(data_packs, "private_pack_dir",
                              side_effect=absent), patch.object(
                data_packs, "private_packs_dir", return_value=None
            ):
                class Absent(unittest.TestCase):
                    @reference_pack_test
                    def test_reference(self):
                        self.fail("absent pack must not reach the test body")

                result = unittest.TestResult()
                Absent("test_reference").run(result)
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.skipped), 1)
            self.assertEqual(result.skipped[0][1],
                             "external private Pinamar pack is not configured")
            self.assertEqual(data_packs._PRIVATE_READY_CACHE,
                             {"seed": ("original", True)})
        finally:
            data_packs._PRIVATE_READY_CACHE.clear()
            data_packs._PRIVATE_READY_CACHE.update(original)

    def test_managed_ci_allows_only_visible_external_pack_skips(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "ci_local.sh").read_text()
        self.assertIn('scripts/run_backend_tests.py --report "$REPORT"', source)
        self.assertIn('--result-json "$REPORT"', source)
        self.assertIn(
            "scripts/check_unittest_coverage.py \"$LOG\" --result-json \"$REPORT\"",
            source,
        )

        hosted = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        ).read_text()
        self.assertIn(
            "scripts/check_unittest_coverage.py test-output.log --result-json backend-results.json",
            hosted,
        )
        self.assertIn("scripts/run_backend_tests.py --report backend-results.json", hosted)
        self.assertIn("--result-json backend-results.json", hosted)

    def test_generic_center_payload_contract_is_not_class_skipped(self):
        source = (Path(__file__).with_name("test_centros_tiles.py")).read_text()
        self.assertNotIn("@reference_pack_case\nclass TestApiCentros", source)
        self.assertNotIn("@reference_pack_test\n    def test_tile_payload_minimo", source)

    def test_snapshot_survives_a_thread_storing_keys_during_the_copy(self):
        import threading

        from _packtest import _snapshot

        class Writer(dict):
            """A cache whose walk another thread's store interrupts, `interrupted` times."""
            interrupted = 2

            def _walk(self):
                if type(self).interrupted:
                    type(self).interrupted -= 1
                    raise RuntimeError("dictionary changed size during iteration")

            def __iter__(self):  # a dict subclass with its own __iter__ is copied by keys()
                self._walk()
                return super().__iter__()

            def keys(self):
                self._walk()
                return super().keys()

        cache = {"seed": (1, {"value": True})}
        stop = threading.Event()

        def writer():
            index = 0
            while not stop.is_set():
                cache["k%d" % (index % 50)] = (index, {"n": index})
                index += 1

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                copy_ = _snapshot(cache)
                self.assertIn("seed", copy_)
        finally:
            stop.set()
            thread.join(5)

        # A walk that keeps failing is retried a few times, then reported, never swallowed.
        flaky = Writer(seed=(1, {}))
        Writer.interrupted = 2
        self.assertEqual(_snapshot(flaky), {"seed": (1, {})})
        Writer.interrupted = 99
        with self.assertRaises(RuntimeError):
            _snapshot(flaky, attempts=3)


if __name__ == "__main__":
    unittest.main()
