"""Shared isolation for tests that characterize the external Pinamar pack.

The public checkout is self-contained around ``example-town``. Tests whose
assertions name reference-deployment centers, services, or professionals run
only when the separately stored private pack is explicitly configured. They
also select that pack for the module's duration; merely having a private root
on disk must never change the public default implicitly.
"""

import contextlib
import copy
import os
import sys
import time
import unittest
from functools import update_wrapper

import _env


def _snapshot(mapping, attempts=5):
    """A deep copy of a process-wide cache that another thread may be writing.

    ``copy.deepcopy`` walks the live dict; a daemon an earlier module left behind
    (or a request thread of this one) that stores a key in the middle of the walk
    raises ``RuntimeError: dictionary changed size during iteration``. Copy the
    top level in one step (atomic under the GIL), deep-copy that, and try again
    if a nested container still moved underneath the walk (the next attempt is a
    fresh top-level copy, so no wait is needed between them).
    """
    for attempt in range(attempts):
        try:
            return copy.deepcopy(dict(mapping))
        except RuntimeError:
            if attempt == attempts - 1:
                raise


def _refresh_import_constants():
    """Refresh pack-derived values in modules already imported by a test."""
    from app.core import site_identity
    from app.patient_web import catalog, site_base, site_catalog, site_centers

    site_base._COUNTRY = site_base._load_country()
    site_centers._EXAMPLE_LOCALITIES = site_centers._load_example_localities()
    site_catalog._SERVICES_MEMO.clear()
    catalog._OFFICIAL_SOURCES = catalog._load_official_sources()

    preparation = sys.modules.get("app.modules.turnos.preparation")
    if preparation is not None:
        preparation._SERVICIOS_PATH = preparation.sitedata.path("servicios.json")
        preparation._SERVICIOS_CACHE = None

    chat_agent = sys.modules.get("app.modules.chat.chat_agent")
    if chat_agent is not None:
        chat_agent._LOCALIDAD = (
            site_identity.get("city") or "tu localidad"
        ).strip()

    msd_data = sys.modules.get("app.connectors.msd.msd_data")
    if msd_data is not None:
        msd_data._DUMPS = [
            os.path.join(msd_data._HERE, "data", "msd_turnos.json"),
            msd_data.sitedata.path("msd_turnos.json"),
        ]
        msd_data._CACHE = None
        msd_data.SLUG_TO_MSD = msd_data._load_slug_to_msd()


def reference_pack_module():
    """Return isolated ``setUpModule``/``tearDownModule`` callables."""
    state = {}

    def restore():
        if not state.get("active"):
            return
        from app.patient_web import catalog, site_base, site_centers

        state["environment"].close()
        # Restore the exact pre-test process state. Clearing these caches is
        # not equivalent: a reference-pack test may run between two assertions
        # which deliberately seeded a cache, and losing that state makes test
        # order observable even though the environment was restored.
        for mapping, saved in state["mappings"]:
            mapping.clear()
            mapping.update(copy.deepcopy(saved))
        for target, saved in state["sets"]:
            target.clear()
            target.update(saved)
        site_base._COUNTRY = state["patient_country"]
        site_centers._EXAMPLE_LOCALITIES = copy.deepcopy(
            state["patient_example_localities"]
        )
        catalog._OFFICIAL_SOURCES = copy.deepcopy(state["official_sources"])
        for module_name, attributes in state["module_attributes"].items():
            module = sys.modules.get(module_name)
            if module is None:
                continue
            for name, value in attributes.items():
                setattr(module, name, copy.deepcopy(value))
        state["active"] = False

    def set_up():
        from app.core import config, data_packs, site_identity, theme
        from app.patient_web import catalog, site_base, site_catalog, site_centers

        # Capture before probing availability: private_pack_dir() verifies an
        # optional pack and can populate readiness/config caches even when it
        # ultimately raises. A skipped or invalid reference pack must leave
        # the public test process byte-for-byte equivalent at this boundary.
        # An empty patched_env is the snapshot: closing it puts the environment back as
        # it was here, whatever the probe or the selection below wrote.
        state["environment"] = contextlib.ExitStack()
        state["environment"].enter_context(_env.patched_env())
        state["mappings"] = [
            (config._dotenv_cache, _snapshot(config._dotenv_cache)),
            (config._db_cache, _snapshot(config._db_cache)),
            (data_packs._MANIFEST_CACHE, _snapshot(data_packs._MANIFEST_CACHE)),
            (data_packs._MERGE_CACHE, _snapshot(data_packs._MERGE_CACHE)),
            (data_packs._PRIVATE_READY_CACHE,
             _snapshot(data_packs._PRIVATE_READY_CACHE)),
            (catalog._JSON_CACHE, _snapshot(catalog._JSON_CACHE)),
            (catalog._RAW_CACHE, _snapshot(catalog._RAW_CACHE)),
            (site_base._CACHE, _snapshot(site_base._CACHE)),
            (site_catalog._SERVICES_MEMO, _snapshot(site_catalog._SERVICES_MEMO)),
            (site_identity._CACHE, _snapshot(site_identity._CACHE)),
            (theme._cache, _snapshot(theme._cache)),
        ]
        state["sets"] = [
            (data_packs._VERIFIED_REQUIRED_PACKS,
             set(data_packs._VERIFIED_REQUIRED_PACKS)),
        ]
        state["patient_country"] = site_base._COUNTRY
        state["patient_example_localities"] = copy.deepcopy(
            site_centers._EXAMPLE_LOCALITIES
        )
        state["official_sources"] = copy.deepcopy(catalog._OFFICIAL_SOURCES)
        module_attributes = {}
        for module_name, names in {
            "app.modules.turnos.preparation": ("_SERVICIOS_PATH", "_SERVICIOS_CACHE"),
            "app.modules.chat.chat_agent": ("_LOCALIDAD",),
            "app.connectors.msd.msd_data": ("_DUMPS", "_CACHE", "SLUG_TO_MSD"),
        }.items():
            module = sys.modules.get(module_name)
            if module is not None:
                module_attributes[module_name] = {
                    name: copy.deepcopy(getattr(module, name)) for name in names
                }
        state["module_attributes"] = module_attributes
        state["active"] = True
        try:
            try:
                data_packs.private_pack_dir("ar-b-pinamar")
            except data_packs.RequiredPackError as exc:
                if data_packs.private_packs_dir():
                    raise
                raise unittest.SkipTest(
                    "external private Pinamar pack is not configured"
                ) from exc
            # Pack selection is a boot condition. Locking config ensures a DB
            # override from another test cannot silently keep example-town.
            state["environment"].enter_context(_env.patched_env(
                TURNOS_WEBDATA_DIR=None,
                PACK_CHAIN="global,ar,ar-b,ar-b-pinamar",
                TURNOS_THEME_DEFAULT="pinamar",
                TURNOS_PUBLIC_ORIGIN="https://pinamar.humanrounds.org",
                TURNOS_CONFIG_LOCK="1",
            ))
            config._reset_cache_para_tests()
            data_packs.invalidate()
            catalog._JSON_CACHE.clear()
            site_base._CACHE.clear()
            site_identity._CACHE.clear()
            _refresh_import_constants()
            theme._cache["v"] = "pinamar"
            theme._cache["t"] = time.time()
            selected = tuple(data_packs.active_chain())
            if not selected or selected[-1] != "ar-b-pinamar":
                raise AssertionError(
                    f"reference pack selection failed: {selected!r}"
                )
        except BaseException:
            restore()
            raise

    def tear_down():
        restore()

    return set_up, tear_down


def reference_pack_case(cls):
    """Run one ``unittest.TestCase`` class against the external pack.

    On a normal public checkout the class is visibly skipped. When the pack is
    configured, every test gets a fresh selection/restoration boundary so a
    content characterization cannot leak its deployment into later modules.
    """
    original_set_up = getattr(cls, "setUp", None)
    original_tear_down = getattr(cls, "tearDown", None)
    original_run = getattr(cls, "run")

    def set_up(self):
        activate, restore = reference_pack_module()
        self._reference_pack_restore = restore
        activate()
        # unittest does not call tearDown when setUp fails. Register the
        # boundary first so every exit path restores the public selection.
        self.addCleanup(restore)
        try:
            if original_set_up is not None:
                original_set_up(self)
        except BaseException:
            restore()
            raise

    def tear_down(self):
        try:
            if original_tear_down is not None:
                original_tear_down(self)
        finally:
            self._reference_pack_restore()

    def run(self, result=None):
        try:
            return original_run(self, result)
        finally:
            restore = getattr(self, "_reference_pack_restore", None)
            if restore is not None:
                restore()

    cls.setUp = set_up
    cls.tearDown = tear_down
    cls.run = run
    return cls


class _ReferencePackTest:
    """Descriptor that activates the reference pack before ``setUp`` runs."""

    def __init__(self, test_method):
        self.test_method = test_method
        update_wrapper(self, test_method)

    def __set_name__(self, owner, name):
        names = getattr(owner, "_reference_pack_test_names", None)
        if names is None:
            names = set()
            owner._reference_pack_test_names = names
        names.add(name)
        if getattr(owner, "_reference_pack_setup_wrapped", False):
            return

        original_set_up = getattr(owner, "setUp", None)

        def set_up(instance):
            if instance._testMethodName not in owner._reference_pack_test_names:
                if original_set_up is not None:
                    original_set_up(instance)
                return
            activate, restore = reference_pack_module()
            instance._reference_pack_restore = restore
            activate()
            instance.addCleanup(restore)
            try:
                if original_set_up is not None:
                    original_set_up(instance)
            except BaseException:
                restore()
                raise

        owner.setUp = set_up
        owner._reference_pack_setup_wrapped = True

        if not getattr(owner, "_reference_pack_run_wrapped", False):
            original_run = getattr(owner, "run")

            def run(instance, result=None):
                try:
                    return original_run(instance, result)
                finally:
                    restore = getattr(instance, "_reference_pack_restore", None)
                    if restore is not None:
                        restore()

            owner.run = run
            owner._reference_pack_run_wrapped = True

    def __get__(self, instance, owner=None):
        bound = self.test_method.__get__(instance, owner)
        if instance is None:
            return bound

        # Cleanup is deliberately owned by TestCase.addCleanup()/run(), after
        # the class's own tearDown. Restoring here would make tearDown observe
        # the public pack even though setUp and the test body saw Pinamar.
        return bound


def reference_pack_test(test_method):
    """Isolate one deployment-specific method before its ``setUp`` boundary."""
    # Availability is intentionally resolved inside the per-test setup, not
    # while Python imports the module. The probe itself may warm config/pack
    # caches; reference_pack_module snapshots before it and restores on skip,
    # invalid configuration, success, and BaseException.
    return _ReferencePackTest(test_method)
