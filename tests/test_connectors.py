"""Tests for connectors/__init__.py — the Phase 6b connector framework:
registry discovery, active-connector resolution, and the degradation
contract ("no connector configured -> capability degrades, never crashes"),
plus the registry rewire (2026-08-20): `resolve()`'s four states, the
reserved `off` sentinel, `adapter_for()`/`_instantiate_adapter()`, the
`"default": true` manifest flag, and the in-tree compatibility pins.

Uses a temp connectors/ directory (monkeypatched onto connectors.ROOT) so
these tests stay meaningful regardless of how many real connectors later
slices add under the real connectors/ dir. Adapter-instantiation tests
additionally extend `connectors.__path__` for their duration (swapping ROOT
alone does not make a temp dir importable) and purge the modules they load.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import textwrap
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.core import config  # noqa: E402
from app import connectors  # noqa: E402
from _fstime import restore_mtime_after_ctime_moves  # noqa: E402


def _write_manifest(base, connector_id, capability, country="AR", config_keys=None,
                    extra=None):
    d = os.path.join(base, connector_id)
    os.makedirs(d, exist_ok=True)
    doc = {
        "id": connector_id,
        "country": country,
        "capability": capability,
        "config_keys": config_keys or [],
        "description": f"test connector {connector_id}",
    }
    doc.update(extra or {})
    with open(os.path.join(d, "connector.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return d


def _write_adapter(base, connector_id, capability, adapter_src=None, impl_src=None,
                   extra=None):
    """A temp connector PACKAGE: manifest + `__init__.py` + `adapter.py`
    (+ `impl.py` when given), importable once `connectors.__path__` includes
    `base` (TempAdapterConnectorsBase does that). `adapter_src=None` writes
    no adapter.py at all (the "missing adapter" case)."""
    d = _write_manifest(base, connector_id, capability, extra=extra)
    with open(os.path.join(d, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    if impl_src is not None:
        with open(os.path.join(d, "impl.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(impl_src))
    if adapter_src is not None:
        with open(os.path.join(d, "adapter.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(adapter_src))
    return d


def _config_get_returning(values):
    """A stand-in for `config.get` reading ONLY `values` (a dict) — so no DB,
    ambient environment, or checkout .env participates in a test."""
    def fake_get(key, default=None, legacy=None):
        return values.get(key, default)
    return fake_get


class TempConnectorsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sana-connectors-test-")
        self._orig_root = connectors.ROOT
        connectors.ROOT = self._tmp
        config._reset_cache_para_tests()

    def tearDown(self):
        connectors.ROOT = self._orig_root
        shutil.rmtree(self._tmp, ignore_errors=True)
        config._reset_cache_para_tests()
        # Undo any DB-shaped overrides this test wrote via config.guardar —
        # config.guardar hits db_catalog.catalog_upsert, which needs a real DB; tests
        # here stick to monkeypatching config.get directly instead.

    def _patch_config(self, **values):
        """Patch `connectors.config.get` to read only `values` for the rest of
        this test (restored on tearDown). Keys are the raw config names, e.g.
        CONNECTOR_SLOT_SOURCE="off"."""
        patcher = mock.patch.object(connectors.config, "get", _config_get_returning(values))
        patcher.start()
        self.addCleanup(patcher.stop)


class TempAdapterConnectorsBase(TempConnectorsBase):
    """Import-resolution-safe fixture for adapter instantiation: the temp dir
    joins `connectors.__path__` so `importlib.import_module("connectors.<tmp
    id>.adapter")` resolves there; temp ids use a prefix no in-tree dir can
    ever collide with (and that exercises the identifier rule); tearDown
    purges every module the test loaded and drops the path entry again.
    Nothing ever touches `sys.path`."""

    PREFIX = "tmpconn_"

    def setUp(self):
        super().setUp()
        connectors.__path__.append(self._tmp)
        importlib.invalidate_caches()

    def tearDown(self):
        try:
            connectors.__path__.remove(self._tmp)
        except ValueError:
            pass
        for name in [n for n in sys.modules if n.startswith(connectors.__name__ + "." + self.PREFIX)]:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        super().tearDown()


class TestRegistryDiscovery(TempConnectorsBase):
    def test_no_connectors_dir_yields_empty_list(self):
        self.assertEqual(connectors.list_connector_ids(), [])
        self.assertEqual(connectors.list_connectors(), [])

    def test_discovers_manifests_sorted_by_id(self):
        _write_manifest(self._tmp, "zeta", connectors.SLOT_SOURCE)
        _write_manifest(self._tmp, "alpha", connectors.IDENTITY_VERIFICATION)
        self.assertEqual(connectors.list_connector_ids(), ["alpha", "zeta"])
        ids = [m["id"] for m in connectors.list_connectors()]
        self.assertEqual(ids, ["alpha", "zeta"])

    def test_directory_without_connector_json_is_ignored(self):
        os.makedirs(os.path.join(self._tmp, "not_a_connector"), exist_ok=True)
        self.assertEqual(connectors.list_connector_ids(), [])

    def test_broken_manifest_json_degrades_to_none_not_a_crash(self):
        d = os.path.join(self._tmp, "broken")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "connector.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertIsNone(connectors.load_manifest("broken"))
        # Discovery still finds the DIRECTORY (it has a connector.json file),
        # but list_connectors() silently drops it instead of raising.
        self.assertIn("broken", connectors.list_connector_ids())
        self.assertEqual(connectors.list_connectors(), [])

    def test_manifest_id_mismatch_is_rejected(self):
        _write_manifest(self._tmp, "real_dir_name", connectors.INSURANCE_LOOKUP)
        # Corrupt the id field so it no longer matches the directory name.
        path = os.path.join(self._tmp, "real_dir_name", "connector.json")
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["id"] = "someone_else"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        self.assertIsNone(connectors.load_manifest("real_dir_name"))

    def test_connectors_for_capability_filters(self):
        _write_manifest(self._tmp, "renaper_like", connectors.IDENTITY_VERIFICATION)
        _write_manifest(self._tmp, "puco_like", connectors.INSURANCE_LOOKUP)
        found = connectors.connectors_for_capability(connectors.IDENTITY_VERIFICATION)
        self.assertEqual([m["id"] for m in found], ["renaper_like"])

    def test_manifest_memo_sees_a_rewrite_and_hands_out_copies(self):
        # load_manifest memoizes parsed manifests by (mtime, size) so the
        # listing loops don't re-parse a dozen files per service cell — the
        # memo must never serve a stale parse after a rewrite, and a caller
        # mutating the returned dict must not poison the next caller.
        _write_manifest(self._tmp, "memo", connectors.SLOT_SOURCE)
        first = connectors.load_manifest("memo")
        self.assertEqual(first["capability"], connectors.SLOT_SOURCE)
        first["capability"] = "mutated-by-caller"
        first["config_keys"].append("POISON")
        self.assertEqual(connectors.load_manifest("memo")["capability"], connectors.SLOT_SOURCE)
        self.assertNotIn("POISON", connectors.load_manifest("memo")["config_keys"])
        _write_manifest(self._tmp, "memo", connectors.EMR_SYNC, extra={"default": True})
        again = connectors.load_manifest("memo")
        self.assertEqual(again["capability"], connectors.EMR_SYNC)
        self.assertIs(again["default"], True)
        shutil.rmtree(os.path.join(self._tmp, "memo"))
        self.assertIsNone(connectors.load_manifest("memo"))

    def test_same_size_rewrite_with_restored_mtime_invalidates_memo(self):
        _write_manifest(self._tmp, "memo", connectors.SLOT_SOURCE,
                        extra={"description": "AAAA"})
        path = os.path.join(self._tmp, "memo", "connector.json")
        before = os.stat(path)
        self.assertEqual(connectors.load_manifest("memo")["description"], "AAAA")
        with open(path, encoding="utf-8") as f:
            payload = f.read().replace('"AAAA"', '"BBBB"')
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        restore_mtime_after_ctime_moves(path, before)
        self.assertEqual(connectors.load_manifest("memo")["description"], "BBBB")

    def test_traversal_symlink_and_non_identifier_ids_are_rejected(self):
        outside = tempfile.mkdtemp(prefix="outside-connectors-")
        self.addCleanup(shutil.rmtree, outside, True)
        _write_manifest(outside, "evil", connectors.SLOT_SOURCE)
        os.symlink(os.path.join(outside, "evil"), os.path.join(self._tmp, "evil"))
        linked_manifest_dir = os.path.join(self._tmp, "linked_manifest")
        os.makedirs(linked_manifest_dir)
        os.symlink(os.path.join(outside, "evil", "connector.json"),
                   os.path.join(linked_manifest_dir, "connector.json"))
        _write_manifest(self._tmp, "bad_id", connectors.SLOT_SOURCE)
        os.rename(os.path.join(self._tmp, "bad_id"), os.path.join(self._tmp, "bad-id"))
        self.assertNotIn("evil", connectors.list_connector_ids())
        self.assertNotIn("linked_manifest", connectors.list_connector_ids())
        self.assertNotIn("bad-id", connectors.list_connector_ids())
        self.assertIsNone(connectors.load_manifest("evil"))
        self.assertIsNone(connectors.load_manifest("linked_manifest"))
        self.assertIsNone(connectors.load_manifest("../outside/evil"))
        self.assertIsNone(connectors.load_manifest("bad-id"))

    def test_unknown_capability_manifest_is_rejected(self):
        _write_manifest(self._tmp, "unknown", "future_unimplemented_capability",
                        extra={"default": True})
        self.assertIsNone(connectors.load_manifest("unknown"))
        self.assertEqual(connectors.resolve("future_unimplemented_capability"), (None, None))

    def test_non_boolean_default_makes_the_manifest_invalid(self):
        # "default" is a JSON boolean or nothing: a string "true", a number, a
        # null are malformed manifest data — refused outright (not found),
        # never half-honored, so they can never select an installation default.
        for bad in ("true", "yes", 1, 0, None, [True], {"v": True}):
            with self.subTest(default=bad):
                _write_manifest(self._tmp, "badflag", connectors.SLOT_SOURCE,
                                extra={"default": bad})
                self.assertIsNone(connectors.load_manifest("badflag"))
                self.assertEqual(connectors.connectors_for_capability(connectors.SLOT_SOURCE), [])
        _write_manifest(self._tmp, "badflag", connectors.SLOT_SOURCE, extra={"default": False})
        self.assertIs(connectors.load_manifest("badflag")["default"], False)


class TestActiveConnectorResolution(TempConnectorsBase):
    def test_no_active_connector_by_default(self):
        _write_manifest(self._tmp, "puco_like", connectors.INSURANCE_LOOKUP)
        self.assertIsNone(connectors.active_connector_id(connectors.INSURANCE_LOOKUP))
        self.assertIsNone(connectors.active_connector(connectors.INSURANCE_LOOKUP))

    def test_active_connector_resolves_once_configured(self):
        _write_manifest(self._tmp, "puco_like", connectors.INSURANCE_LOOKUP)
        self.addCleanup(config._reset_cache_para_tests)   # dropped after the environment is back (LIFO)
        _env.set_env(self, CONNECTOR_INSURANCE_LOOKUP="puco_like")
        config._reset_cache_para_tests()
        self.assertEqual(
            connectors.active_connector_id(connectors.INSURANCE_LOOKUP), "puco_like")
        manifest = connectors.active_connector(connectors.INSURANCE_LOOKUP)
        self.assertEqual(manifest["id"], "puco_like")

    def test_active_connector_capability_mismatch_degrades_to_none(self):
        # A stale/hand-edited env pointing CONNECTOR_INSURANCE_LOOKUP at a
        # connector that actually implements a DIFFERENT capability must not
        # resolve — degrade, don't hand back the wrong capability's connector.
        _write_manifest(self._tmp, "wrong_cap", connectors.SLOT_SOURCE)
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, CONNECTOR_INSURANCE_LOOKUP="wrong_cap")
        config._reset_cache_para_tests()
        self.assertIsNone(connectors.active_connector(connectors.INSURANCE_LOOKUP))

    def test_unknown_connector_id_degrades_to_none(self):
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, CONNECTOR_EMR_SYNC="does-not-exist")
        config._reset_cache_para_tests()
        self.assertIsNone(connectors.active_connector(connectors.EMR_SYNC))


class TestResolve(TempConnectorsBase):
    """`resolve()` — explicit / off / default / invalid / nothing. Every case
    patches `connectors.config.get` to a controlled value: no DB, no ambient
    environment, no checkout .env ever participates."""

    CAP = connectors.SLOT_SOURCE
    KEY = "CONNECTOR_SLOT_SOURCE"

    def test_absent_value_and_unique_flagged_default(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        _write_manifest(self._tmp, "plain", self.CAP)
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), ("flagged", "default"))

    def test_absent_value_and_sole_implementer_is_the_default(self):
        _write_manifest(self._tmp, "only_one", self.CAP)
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), ("only_one", "default"))

    def test_emr_sync_never_gets_an_implicit_default(self):
        _write_manifest(self._tmp, "only_emr", connectors.EMR_SYNC,
                        extra={"default": True})
        self._patch_config()
        self.assertEqual(connectors.resolve(connectors.EMR_SYNC), (None, None))

    def test_two_flagged_defaults_is_no_default(self):
        _write_manifest(self._tmp, "first", self.CAP, extra={"default": True})
        _write_manifest(self._tmp, "second", self.CAP, extra={"default": True})
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), (None, None))

    def test_several_unflagged_implementers_is_no_default(self):
        _write_manifest(self._tmp, "first", self.CAP)
        _write_manifest(self._tmp, "second", self.CAP)
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), (None, None))

    def test_no_implementer_is_nothing(self):
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), (None, None))
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_explicit_valid_id_beats_the_default(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        _write_manifest(self._tmp, "plain", self.CAP)
        self._patch_config(**{self.KEY: "plain"})
        self.assertEqual(connectors.resolve(self.CAP), ("plain", "explicit"))
        self.assertEqual(connectors.active_connector_id(self.CAP), "plain")

    def test_off_beats_the_default(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        self._patch_config(**{self.KEY: connectors.OFF})
        self.assertEqual(connectors.resolve(self.CAP), (None, "off"))
        # The stored-value contract is untouched: raw "off" comes back raw,
        # and the manifest-level accessor sees no connector.
        self.assertEqual(connectors.active_connector_id(self.CAP), "off")
        self.assertIsNone(connectors.active_connector(self.CAP))
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_explicit_unknown_id_is_invalid_not_the_default(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        self._patch_config(**{self.KEY: "nope"})
        self.assertEqual(connectors.resolve(self.CAP), (None, "invalid"))
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_explicit_id_of_the_wrong_capability_is_invalid(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        _write_manifest(self._tmp, "other_cap", connectors.INSURANCE_LOOKUP)
        self._patch_config(**{self.KEY: "other_cap"})
        self.assertEqual(connectors.resolve(self.CAP), (None, "invalid"))

    def test_explicit_broken_manifest_is_invalid(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        d = os.path.join(self._tmp, "broken")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "connector.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self._patch_config(**{self.KEY: "broken"})
        self.assertEqual(connectors.resolve(self.CAP), (None, "invalid"))

    def test_explicit_non_identifier_value_is_invalid(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        for raw in ("../x", "a-b", "msd ", "Off", "off ", "1abc"):
            with self.subTest(raw=raw):
                self._patch_config(**{self.KEY: raw})
                self.assertEqual(connectors.resolve(self.CAP), (None, "invalid"))
                self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_empty_string_counts_as_absent(self):
        _write_manifest(self._tmp, "flagged", self.CAP, extra={"default": True})
        self._patch_config(**{self.KEY: ""})
        self.assertEqual(connectors.resolve(self.CAP), ("flagged", "default"))
        self.assertIsNone(connectors.active_connector_id(self.CAP))

    def test_non_boolean_default_is_never_selected(self):
        # "default": "true" makes THAT manifest invalid (not discovered at
        # all), so the other, unflagged implementer becomes the sole one and
        # the default; with two such manifests nothing is selected.
        _write_manifest(self._tmp, "stringy", self.CAP, extra={"default": "true"})
        _write_manifest(self._tmp, "plain", self.CAP)
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), ("plain", "default"))
        _write_manifest(self._tmp, "numeric", self.CAP, extra={"default": 1})
        self.assertEqual(connectors.resolve(self.CAP), ("plain", "default"))
        # And naming an invalid-manifest connector explicitly fails closed.
        self._patch_config(**{self.KEY: "stringy"})
        self.assertEqual(connectors.resolve(self.CAP), (None, "invalid"))


class TestOffSentinelReserved(TempConnectorsBase):
    def test_a_directory_named_off_is_never_a_connector(self):
        _write_manifest(self._tmp, connectors.OFF, connectors.SLOT_SOURCE)
        _write_manifest(self._tmp, "real_one", connectors.SLOT_SOURCE)
        self.assertEqual(connectors.list_connector_ids(), ["real_one"])
        self.assertIsNone(connectors.load_manifest(connectors.OFF))
        self.assertEqual([m["id"] for m in connectors.list_connectors()], ["real_one"])
        self.assertIsNone(connectors._instantiate_adapter(connectors.OFF))
        with mock.patch.object(connectors.config, "guardar") as guardar:
            with self.assertRaises(ValueError):
                connectors.enable(connectors.OFF)
            guardar.assert_not_called()
        # Even with a well-formed `off` manifest on disk, a stored "off" means
        # off — never "the connector called off".
        self._patch_config(CONNECTOR_SLOT_SOURCE=connectors.OFF)
        self.assertEqual(connectors.resolve(connectors.SLOT_SOURCE), (None, "off"))


_ADAPTER_OK = '''
from app.connectors import SlotSourceConnector
from . import impl as _impl


class TmpConnector(SlotSourceConnector):
    def slots_for_service(self, servicio):
        return _impl.answer(servicio)
'''

_IMPL_OK = '''
def answer(servicio):
    return {"echo": servicio}
'''

_ADAPTER_TWO = '''
from app.connectors import SlotSourceConnector


class One(SlotSourceConnector):
    def slots_for_service(self, servicio):
        return {}


class Two(SlotSourceConnector):
    def slots_for_service(self, servicio):
        return {}
'''

_ADAPTER_NONE = '''
from app.connectors import SlotSourceConnector, InsuranceLookupConnector


class WrongCapability(InsuranceLookupConnector):
    def available(self):
        return False

    def lookup(self, national_id):
        return False, []


class StillAbstract(SlotSourceConnector):
    pass
'''

_ADAPTER_RAISES = '''
raise RuntimeError("this adapter explodes at import")
'''

_ADAPTER_CTOR_RAISES = '''
from app.connectors import SlotSourceConnector


class NeedsArgs(SlotSourceConnector):
    def __init__(self, required):
        self.required = required

    def slots_for_service(self, servicio):
        return {}
'''


class TestAdapterFor(TempAdapterConnectorsBase):
    CAP = connectors.SLOT_SOURCE

    def test_default_connector_instantiates_to_the_capability_abc(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_OK, _IMPL_OK,
                       extra={"default": True})
        self._patch_config()
        adapter = connectors.adapter_for(self.CAP)
        self.assertIsInstance(adapter, connectors.SlotSourceConnector)
        self.assertEqual(type(adapter).__module__, connectors.__name__ + ".tmpconn_a.adapter")
        # The adapter's relative `from . import impl as _impl` resolved.
        self.assertEqual(adapter.slots_for_service({"id": "s1"}), {"echo": {"id": "s1"}})
        # A fresh instance per call — never a shared singleton.
        self.assertIsNot(connectors.adapter_for(self.CAP), adapter)

    def test_explicit_connector_instantiates(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_OK, _IMPL_OK,
                       extra={"default": True})
        _write_adapter(self._tmp, "tmpconn_b", self.CAP, _ADAPTER_OK, _IMPL_OK)
        self._patch_config(CONNECTOR_SLOT_SOURCE="tmpconn_b")
        adapter = connectors.adapter_for(self.CAP)
        self.assertEqual(type(adapter).__module__, connectors.__name__ + ".tmpconn_b.adapter")

    def test_off_and_invalid_yield_none(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_OK, _IMPL_OK,
                       extra={"default": True})
        self._patch_config(CONNECTOR_SLOT_SOURCE=connectors.OFF)
        self.assertIsNone(connectors.adapter_for(self.CAP))
        self._patch_config(CONNECTOR_SLOT_SOURCE="tmpconn_zzz")
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_missing_adapter_module_yields_none(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, adapter_src=None,
                       extra={"default": True})
        self._patch_config()
        self.assertEqual(connectors.resolve(self.CAP), ("tmpconn_a", "default"))
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_adapter_that_fails_to_import_yields_none(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_RAISES,
                       extra={"default": True})
        self._patch_config()
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_adapter_with_no_concrete_subclass_yields_none(self):
        # A class of ANOTHER capability and an abstract subclass of the right
        # one: neither counts. The imported ABC names themselves are not
        # candidates either (their __module__ is `connectors`).
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_NONE,
                       extra={"default": True})
        self._patch_config()
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_adapter_with_two_subclasses_yields_none(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_TWO,
                       extra={"default": True})
        self._patch_config()
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_adapter_whose_constructor_needs_arguments_yields_none(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_CTOR_RAISES,
                       extra={"default": True})
        self._patch_config()
        self.assertIsNone(connectors.adapter_for(self.CAP))

    def test_instantiate_adapter_refuses_reserved_and_non_identifier_ids(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_OK, _IMPL_OK)
        self.assertIsNotNone(connectors._instantiate_adapter("tmpconn_a"))
        for bad in (None, "", connectors.OFF, "../tmpconn_a", "tmpconn-a", "tmpconn_a "):
            with self.subTest(id=bad):
                self.assertIsNone(connectors._instantiate_adapter(bad))

    def test_preloaded_adapter_from_another_origin_is_rejected(self):
        _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_OK, _IMPL_OK)
        name = connectors.__name__ + ".tmpconn_a.adapter"
        injected = types.ModuleType(name)
        injected.__file__ = "/outside/adapter.py"
        injected.__spec__ = types.SimpleNamespace(origin="/outside/adapter.py")
        sys.modules[name] = injected
        self.addCleanup(sys.modules.pop, name, None)
        self.assertIsNone(connectors._instantiate_adapter("tmpconn_a"))

    def test_symlinked_adapter_and_package_source_are_rejected(self):
        outside = tempfile.mkdtemp(prefix="outside-adapter-")
        self.addCleanup(shutil.rmtree, outside, True)
        outside_adapter = os.path.join(outside, "adapter.py")
        outside_init = os.path.join(outside, "__init__.py")
        for path, content in ((outside_adapter, _ADAPTER_OK), (outside_init, "")):
            with open(path, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent(content))

        d = _write_adapter(self._tmp, "tmpconn_a", self.CAP, _ADAPTER_OK, _IMPL_OK)
        os.unlink(os.path.join(d, "adapter.py"))
        os.symlink(outside_adapter, os.path.join(d, "adapter.py"))
        self.assertIsNone(connectors._instantiate_adapter("tmpconn_a"))

        shutil.rmtree(d)
        d = _write_adapter(self._tmp, "tmpconn_b", self.CAP, _ADAPTER_OK, _IMPL_OK)
        os.unlink(os.path.join(d, "__init__.py"))
        os.symlink(outside_init, os.path.join(d, "__init__.py"))
        self.assertIsNone(connectors._instantiate_adapter("tmpconn_b"))


class TestDisableWritesSentinel(TempConnectorsBase):
    def test_disable_persists_off_not_none(self):
        # config.guardar DELETES a key whose value is None/"" — which would
        # silently mean "back to the default", not "off". The sentinel is
        # what keeps "disabled" distinguishable from "never configured".
        with mock.patch.object(connectors.config, "guardar", return_value={}) as guardar:
            connectors.disable(connectors.SLOT_SOURCE)
        guardar.assert_called_once_with({"CONNECTOR_SLOT_SOURCE": connectors.OFF})

    def test_disable_rejects_an_unknown_capability_before_writing(self):
        with mock.patch.object(connectors.config, "guardar") as guardar:
            with self.assertRaises(ValueError):
                connectors.disable("not_a_capability")
            guardar.assert_not_called()

    def test_enable_overwrites_off_with_the_explicit_id_and_keeps_values(self):
        _write_manifest(self._tmp, "tmpconn_x", connectors.SLOT_SOURCE)
        with mock.patch.object(connectors.config, "guardar", return_value={}) as guardar:
            connectors.enable("tmpconn_x", {"SOME_KEY": "v"})
        guardar.assert_called_once_with({"SOME_KEY": "v", "CONNECTOR_SLOT_SOURCE": "tmpconn_x"})


class TestInTreeManifests(unittest.TestCase):
    """Against the REAL connectors/ dir: every adapter instantiates, at most
    one flagged default per capability, and the explicit Core/Enterprise
    boundary: open helpers preserve their compatibility defaults while EMR
    integrations remain unselected until an operator chooses one."""

    def setUp(self):
        patcher = mock.patch.object(connectors.config, "get", _config_get_returning({}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_in_tree_adapter_instantiates_to_its_capability_abc(self):
        ids = connectors.list_connector_ids()
        self.assertIn("renaper", ids)
        for connector_id in ids:
            with self.subTest(connector=connector_id):
                manifest = connectors.load_manifest(connector_id)
                abc_cls = connectors.abc_for_capability(manifest["capability"])
                adapter = connectors._instantiate_adapter(connector_id)
                self.assertIsInstance(adapter, abc_cls)

    def test_at_most_one_flagged_default_per_capability(self):
        for capability in connectors.CAPABILITIES:
            flagged = [m["id"] for m in connectors.connectors_for_capability(capability)
                       if m.get("default") is True]
            self.assertLessEqual(len(flagged), 1, (capability, flagged))

    def test_unconfigured_installation_resolves_exactly_the_pre_rewire_modules(self):
        self.assertEqual(connectors.resolve(connectors.IDENTITY_VERIFICATION), ("renaper", "default"))
        self.assertEqual(connectors.resolve(connectors.INSURANCE_LOOKUP), ("puco", "default"))
        self.assertEqual(connectors.resolve(connectors.LICENSE_LOOKUP), ("refeps", "default"))
        self.assertEqual(connectors.resolve(connectors.SLOT_SOURCE), ("msd", "default"))
        # EMR is an institutional integration: neither the temporary HSI
        # adapter nor the Enterprise bridge is selected implicitly.
        self.assertEqual(connectors.resolve(connectors.EMR_SYNC), (None, None))
        # Two unflagged transports (waha, meta): no default — otp_send.py's own
        # wa_provider()/OTP_PROVIDER stays the selection, by design.
        self.assertEqual(connectors.resolve(connectors.OTP_TRANSPORT), (None, None))
        # Three archives, none flagged: a fan-out, never a single default.
        self.assertEqual(connectors.resolve(connectors.CLINICAL_HISTORY_LOOKUP), (None, None))

    def test_webturno_is_registered_but_not_the_default(self):
        self.assertIsNot(connectors.load_manifest("webturno").get("default"), True)
        self.assertIs(connectors.load_manifest("msd").get("default"), True)
        self.assertEqual(type(connectors.adapter_for(connectors.SLOT_SOURCE)).__name__,
                         "MsdConnector")

    def test_adapter_for_returns_the_real_adapter_classes(self):
        expected = {
            connectors.IDENTITY_VERIFICATION: "RenaperConnector",
            connectors.INSURANCE_LOOKUP: "PucoConnector",
            connectors.LICENSE_LOOKUP: "RefepsConnector",
            connectors.SLOT_SOURCE: "MsdConnector",
        }
        for capability, cls_name in expected.items():
            with self.subTest(capability=capability):
                adapter = connectors.adapter_for(capability)
                self.assertEqual(type(adapter).__name__, cls_name)
                self.assertIsInstance(adapter, connectors.abc_for_capability(capability))


class TestConfigKeyStatus(TempConnectorsBase):
    def test_missing_keys_reported_missing(self):
        _write_manifest(self._tmp, "renaper_like", connectors.IDENTITY_VERIFICATION,
                         config_keys=["TURNOS_RENAPER_CLIENT_ID", "TURNOS_RENAPER_CLIENT_SECRET"])
        status = connectors.config_key_status("renaper_like")
        self.assertEqual(status, {
            "TURNOS_RENAPER_CLIENT_ID": "missing",
            "TURNOS_RENAPER_CLIENT_SECRET": "missing",
        })
        self.assertFalse(connectors.is_configured("renaper_like"))

    def test_set_keys_reported_set(self):
        _write_manifest(self._tmp, "renaper_like", connectors.IDENTITY_VERIFICATION,
                         config_keys=["TURNOS_RENAPER_CLIENT_ID"])
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, TURNOS_RENAPER_CLIENT_ID="abc123")
        config._reset_cache_para_tests()
        self.assertEqual(connectors.config_key_status("renaper_like"),
                          {"TURNOS_RENAPER_CLIENT_ID": "set"})
        self.assertTrue(connectors.is_configured("renaper_like"))

    def test_no_config_keys_is_always_configured(self):
        _write_manifest(self._tmp, "stub_like", connectors.SLOT_SOURCE, config_keys=[])
        self.assertEqual(connectors.config_key_status("stub_like"), {})
        self.assertTrue(connectors.is_configured("stub_like"))

    def test_unknown_connector_status_is_empty(self):
        self.assertEqual(connectors.config_key_status("nope"), {})


class TestAbcForCapability(unittest.TestCase):
    def test_every_capability_has_an_abc(self):
        for capability in connectors.CAPABILITIES:
            abc_cls = connectors.abc_for_capability(capability)
            self.assertIsNotNone(abc_cls, capability)
            self.assertEqual(abc_cls.capability, capability)

    def test_unknown_capability_returns_none(self):
        self.assertIsNone(connectors.abc_for_capability("not_a_real_capability"))

    def test_abcs_cannot_be_instantiated_directly(self):
        with self.assertRaises(TypeError):
            connectors.IdentityVerificationConnector()

    def test_insurance_lookup_cached_is_not_abstract_and_defaults_to_empty(self):
        # `cached()` is the registration POST's read of what a lookup already
        # learned; a connector that keeps no cache inherits "nothing cached".
        class Minimal(connectors.InsuranceLookupConnector):
            def available(self):
                return False

            def lookup(self, national_id):
                return False, []

        self.assertEqual(Minimal().cached("12345678"), [])


if __name__ == "__main__":
    unittest.main()
