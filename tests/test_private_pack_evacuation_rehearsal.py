"""Synthetic offline rehearsal of evacuating a private town pack.

This is the bounded proof that a fictional ``ar-b-pinamar`` can leave a
temporary public tree, come back from an external archive, and render the
same representative routes in-process.  It exercises the reviewed inventory,
archive, required-pack, and rendering contracts.  Importing this module and
running its suite never opens, lists, or walks the checkout's Pinamar pack.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover \
        -s tests -p "test_private_pack_evacuation_rehearsal.py"
"""

import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from scripts import pack_inventory  # noqa: E402
from scripts import rehearse_private_pack_evacuation as rehearsal  # noqa: E402

catalog = None
site_base = None
site_catalog = None
site_identity = None
theme = None


CHECKOUT_PRIVATE = os.path.join(os.path.realpath(ROOT), "packs", "ar-b-pinamar")
_ENV_KEYS = (
    "TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS", "TURNOS_WEBDATA_DIR",
    "PACK_CHAIN", "TURNOS_CONFIG_LOCK", "TURNOS_ENV_FILE", "TURNOS_DATA_DIR",
    "TURNOS_PUBLIC_ORIGIN", "TURNOS_THEME_DEFAULT",
)


def _load_render_modules():
    """Import pack-aware modules only after the runtime is pointed at fixtures."""
    global catalog, site_base, site_catalog, site_identity, theme
    if catalog is not None:
        return
    from app.core import site_identity as site_identity_mod
    from app.core import theme as theme_mod
    from app.patient_web import catalog as catalog_mod
    from app.patient_web import site_base as site_base_mod
    from app.patient_web import site_catalog as site_catalog_mod
    site_identity = site_identity_mod
    theme = theme_mod
    catalog = catalog_mod
    site_base = site_base_mod
    site_catalog = site_catalog_mod


def _identidad(tipo, base, incluir_borrados=False, strict=False):
    meta = {it.get(catalog.id_key(tipo)): {"_origen": "json", "_borrado": False}
            for it in base}
    return list(base), meta


def _policy(path):
    path.write_text(json.dumps({
        "policy_version": "2.3.0-tier7-test",
        "policy_name": "test policy",
        "required_public_artifacts": [],
        "forbidden_paths": [
            {"id": "private-town-pack", "match": "prefix",
             "pattern": "packs/ar-b-pinamar", "description": "private"},
            {"id": "private-family", "match": "regex",
             "pattern": "(^|/)packs/[^/]+-private($|/)", "description": "private"},
        ],
        "secret_marker_classes": [], "external_evidence": [],
    }), encoding="utf-8")


def _stub_public_pack(root, pack_id):
    directory = Path(root) / "packs" / pack_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.json").write_text(json.dumps({"id": pack_id}), encoding="utf-8")
    (directory / "payload.bin").write_bytes(pack_id.encode("ascii"))
    return directory


def _clear_caches():
    _load_render_modules()
    catalog._JSON_CACHE.clear()
    catalog.invalidate()
    site_base._CACHE.clear()
    site_catalog._SERVICES_MEMO.clear()
    site_identity._CACHE.clear()
    data_packs.invalidate()


class CheckoutPrivateGuard:
    """Fail the test if anything descends into the checked-in Pinamar pack."""

    def __init__(self):
        self.hits = []

    def _check(self, path, listing=False):
        if path is None:
            return
        try:
            text = os.fsdecode(path)
        except (TypeError, ValueError):
            return
        if not (os.path.isabs(text) or os.sep in text):
            return
        real = os.path.realpath(text)
        if real == CHECKOUT_PRIVATE:
            if not listing:
                return
        elif not real.startswith(CHECKOUT_PRIVATE + os.sep):
            return
        rel = os.path.relpath(real, os.path.realpath(ROOT))
        self.hits.append(real)
        raise AssertionError(
            "rehearsal reached checkout private pack: %s" % rel)

    def __enter__(self):
        self._open = os.open
        self._scandir = os.scandir
        self._walk = os.walk
        self._builtin_open = builtins.open
        self._io_open = io.open
        self._copytree = shutil.copytree
        self._copy2 = shutil.copy2

        def open_guard(path, *args, **kwargs):
            if kwargs.get("dir_fd") is None:
                self._check(path)
            return self._open(path, *args, **kwargs)

        def builtin_open_guard(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                self._check(file)
            return self._builtin_open(file, *args, **kwargs)

        def scandir_guard(path, *args, **kwargs):
            if kwargs.get("dir_fd") is None:
                self._check(path, listing=True)
            return self._scandir(path, *args, **kwargs)

        def walk_guard(top, *args, **kwargs):
            self._check(top, listing=True)
            return self._walk(top, *args, **kwargs)

        def copytree_guard(src, dst, *args, **kwargs):
            self._check(src, listing=True)
            self._check(dst, listing=True)
            return self._copytree(src, dst, *args, **kwargs)

        def copy2_guard(src, dst, *args, **kwargs):
            self._check(src)
            self._check(dst)
            return self._copy2(src, dst, *args, **kwargs)

        os.open = open_guard
        os.scandir = scandir_guard
        os.walk = walk_guard
        builtins.open = builtin_open_guard
        io.open = builtin_open_guard
        shutil.copytree = copytree_guard
        shutil.copy2 = copy2_guard
        return self

    def __exit__(self, *exc):
        os.open = self._open
        os.scandir = self._scandir
        os.walk = self._walk
        builtins.open = self._builtin_open
        io.open = self._io_open
        shutil.copytree = self._copytree
        shutil.copy2 = self._copy2
        return False


class PrivatePackEvacuationRehearsalTest(unittest.TestCase):
    def setUp(self):
        self.guard = CheckoutPrivateGuard()
        self.guard.__enter__()
        self.tmp = tempfile.mkdtemp(prefix="human-rounds-evac-")
        self.checkout = os.path.join(self.tmp, "public-tree")
        self.external = os.path.join(self.tmp, "external-private")
        self.restored_root = os.path.join(self.tmp, "restored-private")
        os.makedirs(self.checkout)
        os.makedirs(self.external)
        os.makedirs(self.restored_root)
        self.packs_dir = os.path.join(self.tmp, "render-packs")
        self.legacy = os.path.join(self.tmp, "legacy")
        os.makedirs(self.packs_dir)
        os.makedirs(self.legacy)
        self.patches = [
            patch.object(data_packs, "_PACKS_DIR", self.packs_dir),
            patch.object(data_packs, "_LEGACY_WEBDATA_DIR", self.legacy),
            patch.object(config, "_db_overrides", return_value={}),
        ]
        for p in self.patches:
            p.start()
        _env.set_env(self, {k: None for k in _ENV_KEYS},
                     PACK_CHAIN=rehearsal.PACK_ID,
                     TURNOS_PUBLIC_ORIGIN="https://ficcion.example",
                     TURNOS_THEME_DEFAULT="humanrounds", TURNOS_ENV_FILE=os.devnull)
        config._reset_cache_para_tests()
        data_packs.invalidate()
        _load_render_modules()
        self.patches.extend([
            patch.object(catalog, "merge_with_overlay", _identidad),
            patch.object(theme, "_cache", {"v": "humanrounds", "t": time.time()}),
        ])
        for p in self.patches[-2:]:
            p.start()
        _clear_caches()

    def tearDown(self):
        _clear_caches()
        config._reset_cache_para_tests()
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.guard.__exit__(None, None, None)
        self.assertEqual(self.guard.hits, [])

    def _seed_in_tree(self):
        return rehearsal.seed_synthetic_pack(
            os.path.join(self.packs_dir, rehearsal.PACK_ID))

    def _arm_required(self, private_root):
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=private_root,
                     TURNOS_REQUIRED_PACKS=rehearsal.PACK_ID, PACK_CHAIN=rehearsal.PACK_ID)
        config._reset_cache_para_tests()
        _clear_caches()

    def test_full_rehearsal_restores_identically_and_clears_the_public_tree(self):
        in_tree = self._seed_in_tree()
        rehearsal.stamp_private_inventory(in_tree)
        before = rehearsal.render_representative_routes()
        self.assertEqual(set(before), {
            "centers", "service", "center", "photo",
        })
        for name, body in before.items():
            self.assertTrue(body, msg="%s rendered empty" % name)
        self.assertIn(b"Hospital Ficcion", before["centers"])
        self.assertIn(b"Hospital Ficcion", before["center"])
        self.assertIn(b"Consulta", before["service"])
        self.assertEqual(before["photo"], rehearsal.PHOTO_BYTES)
        self.assertNotIn(b"pinamar", before["centers"].lower())
        self.assertNotIn(b"pinamar", before["service"].lower())
        self.assertNotIn(b"pinamar", before["center"].lower())

        archive = os.path.join(self.external, "ar-b-pinamar.tar")
        rehearsal.archive_pack(in_tree, archive)
        restored = rehearsal.restore_pack(archive, self.restored_root)
        self.assertEqual(os.path.basename(restored), rehearsal.PACK_ID)
        rehearsal.verify_restored_bytes(restored)
        rehearsal.remove_in_tree_pack(self.packs_dir)
        self.assertFalse(os.path.exists(in_tree))

        self._arm_required(self.restored_root)
        data_packs.verify_required_packs()
        after = rehearsal.render_representative_routes()
        self.assertEqual(before, after)
        restored = os.path.realpath(self.restored_root)
        resolved = os.path.realpath(data_packs.resolve_path("sitio.json"))
        self.assertTrue(resolved.startswith(restored))
        photo = os.path.realpath(data_packs.resolve_path(rehearsal.PHOTO_REL))
        self.assertTrue(photo.startswith(restored))
        self.assertEqual(rehearsal.PHOTO_ROUTE, "/webdata/" + rehearsal.PHOTO_REL)

    def test_public_inventory_classifies_synthetic_private_without_opening_children(self):
        for pack_id in pack_inventory.PUBLIC_PACK_IDS:
            _stub_public_pack(self.checkout, pack_id)
        private = rehearsal.seed_synthetic_pack(
            os.path.join(self.checkout, "packs", rehearsal.PACK_ID))
        sentinel = os.path.join(private, "never-open.txt")
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write("synthetic sentinel")
        policy = Path(self.checkout) / "policy.json"
        _policy(policy)

        original_open = pack_inventory.os.open

        def guarded_open(name, *args, **kwargs):
            text = os.fsdecode(name) if not isinstance(name, int) else ""
            self.assertNotEqual(os.path.basename(text), "never-open.txt")
            return original_open(name, *args, **kwargs)

        with patch.object(pack_inventory.os, "open", side_effect=guarded_open):
            report, code = pack_inventory.build_inventory(
                Path(self.checkout), policy, [Path(self.external)])
        self.assertEqual(code, 0)
        self.assertEqual(report["categories"]["excluded_private"], 1)
        self.assertEqual(report["categories"]["external_opaque"], 1)
        self.assertNotIn("ar-b-pinamar", json.dumps(report, sort_keys=True))
        self.assertNotIn("never-open", json.dumps(report, sort_keys=True))

    def test_public_inventory_clears_after_in_tree_removal(self):
        for pack_id in pack_inventory.PUBLIC_PACK_IDS:
            _stub_public_pack(self.checkout, pack_id)
        private = rehearsal.seed_synthetic_pack(
            os.path.join(self.checkout, "packs", rehearsal.PACK_ID))
        policy = Path(self.checkout) / "policy.json"
        _policy(policy)
        before, code = pack_inventory.build_inventory(Path(self.checkout), policy)
        self.assertEqual(code, 0)
        self.assertEqual(before["categories"]["excluded_private"], 1)

        rehearsal.remove_in_tree_pack(os.path.join(self.checkout, "packs"))
        self.assertFalse(os.path.exists(private))
        after, code = pack_inventory.build_inventory(Path(self.checkout), policy)
        self.assertEqual(code, 0)
        self.assertEqual(after["categories"]["excluded_private"], 0)

    def test_checkout_private_pack_is_refused_without_reading_it(self):
        archive = os.path.join(self.external, "forbidden.tar")
        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.archive_pack(CHECKOUT_PRIVATE, archive)
        self.assertFalse(os.path.exists(archive))
        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.stamp_private_inventory(CHECKOUT_PRIVATE)
        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.restore_pack(archive if os.path.exists(archive) else
                                   os.path.join(self.external, "missing.tar"),
                                   CHECKOUT_PRIVATE)
        child = os.path.join(CHECKOUT_PRIVATE, "sitio.json")
        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.assert_not_checkout_private(child)

    def test_missing_restored_file_fails_closed_before_render(self):
        in_tree = self._seed_in_tree()
        rehearsal.stamp_private_inventory(in_tree)
        archive = os.path.join(self.external, "ar-b-pinamar.tar")
        rehearsal.archive_pack(in_tree, archive)
        restored = rehearsal.restore_pack(archive, self.restored_root)
        os.unlink(os.path.join(restored, rehearsal.PHOTO_REL))
        rendered = []

        def render():
            rendered.append(True)
            return rehearsal.render_representative_routes()

        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.verify_then_render(restored, render)
        self.assertEqual(rendered, [])
        self._arm_required(self.restored_root)
        with self.assertRaises(data_packs.RequiredPackError):
            data_packs.verify_required_packs()

    def test_altered_restored_bytes_fail_closed_before_render(self):
        in_tree = self._seed_in_tree()
        rehearsal.stamp_private_inventory(in_tree)
        archive = os.path.join(self.external, "ar-b-pinamar.tar")
        rehearsal.archive_pack(in_tree, archive)
        restored = rehearsal.restore_pack(archive, self.restored_root)
        target = os.path.join(restored, "sitio.json")
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n")
        rendered = []

        def render():
            rendered.append(True)
            return {}

        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.verify_then_render(restored, render)
        self.assertEqual(rendered, [])
        self._arm_required(self.restored_root)
        with self.assertRaises(data_packs.RequiredPackError) as ctx:
            data_packs.verify_required_packs()
        self.assertIn("checksum mismatch", str(ctx.exception))

    def test_symlink_in_source_pack_fails_closed_on_archive(self):
        in_tree = self._seed_in_tree()
        rehearsal.stamp_private_inventory(in_tree)
        os.symlink("/etc/passwd", os.path.join(in_tree, "escape.webp"))
        archive = os.path.join(self.external, "escape.tar")
        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.archive_pack(in_tree, archive)
        self.assertFalse(os.path.exists(archive))

    def test_tar_member_escape_fails_closed_on_restore(self):
        archive = os.path.join(self.external, "escape.tar")
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo(name="../outside.txt")
            payload = b"escaped"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(rehearsal.EvacuationError):
            rehearsal.restore_pack(archive, self.restored_root)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "outside.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(self.restored_root, rehearsal.PACK_ID)))

    def test_required_pack_gate_admits_the_restored_external_copy(self):
        in_tree = self._seed_in_tree()
        rehearsal.stamp_private_inventory(in_tree)
        archive = os.path.join(self.external, "ar-b-pinamar.tar")
        rehearsal.archive_pack(in_tree, archive)
        restored = rehearsal.restore_pack(archive, self.restored_root)
        rehearsal.remove_in_tree_pack(self.packs_dir)
        self._arm_required(self.restored_root)
        data_packs.verify_required_packs()
        self.assertEqual(
            os.path.realpath(data_packs.resolve_path(rehearsal.PHOTO_REL)),
            os.path.realpath(os.path.join(restored, rehearsal.PHOTO_REL)))

    def test_archive_bytes_are_stable_across_mtime_and_ownership(self):
        in_tree = self._seed_in_tree()
        rehearsal.stamp_private_inventory(in_tree)
        first = os.path.join(self.external, "mtime-a.tar")
        rehearsal.archive_pack(in_tree, first)
        with open(first, "rb") as f:
            before = f.read()
        for current, dirs, names in os.walk(in_tree, followlinks=False):
            for name in list(dirs) + list(names):
                path = os.path.join(current, name)
                os.utime(path, (1_700_000_001, 1_700_000_001))
                if os.path.isfile(path) and not os.path.islink(path):
                    os.chmod(path, 0o600)
        second = os.path.join(self.external, "mtime-b.tar")
        rehearsal.archive_pack(in_tree, second)
        with open(second, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)
        with tarfile.open(first, mode="r:") as tar:
            members = tar.getmembers()
        self.assertTrue(members)
        for info in members:
            self.assertEqual(info.mtime, 0)
            self.assertEqual(info.uid, 0)
            self.assertEqual(info.gid, 0)
            self.assertEqual(info.uname, "")
            self.assertEqual(info.gname, "")
            self.assertEqual(info.mode & 0o777, 0o644)

    def test_cli_compares_representative_renders_and_cleans_up(self):
        calls = []
        real_render = rehearsal.render_representative_routes
        real_mkdtemp = rehearsal.tempfile.mkdtemp
        created = []

        def tracked_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        def wrapped_render():
            rendered = real_render()
            calls.append(rendered)
            return rendered

        with patch.object(rehearsal.tempfile, "mkdtemp", tracked_mkdtemp):
            with patch.object(rehearsal, "render_representative_routes",
                              side_effect=wrapped_render):
                self.assertEqual(rehearsal.main([]), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(created)
        for path in created:
            self.assertFalse(os.path.exists(path))

        calls.clear()
        created.clear()
        diverged = {"n": 0}

        def diverge():
            diverged["n"] += 1
            rendered = dict(real_render())
            if diverged["n"] == 2:
                rendered["centers"] = rendered["centers"] + b"-mutated"
            calls.append(rendered)
            return rendered

        with patch.object(rehearsal.tempfile, "mkdtemp", tracked_mkdtemp):
            with patch.object(rehearsal, "render_representative_routes",
                              side_effect=diverge):
                with self.assertRaises(rehearsal.EvacuationError):
                    rehearsal.main([])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertTrue(created)
        for path in created:
            self.assertFalse(os.path.exists(path))

        created.clear()

        def boom():
            raise RuntimeError("synthetic render failure")

        with patch.object(rehearsal.tempfile, "mkdtemp", tracked_mkdtemp):
            with patch.object(rehearsal, "render_representative_routes",
                              side_effect=boom):
                with self.assertRaises(RuntimeError):
                    rehearsal.main([])
        self.assertTrue(created)
        for path in created:
            self.assertFalse(os.path.exists(path))


_ISOLATION_CHILD = "TURNOS_EVAC_ISOLATION_CHILD"

_ISOLATION_PROBE = r"""
import builtins
import io
import os
import sys
import unittest

root = sys.argv[1]
mode = sys.argv[2]
checkout = os.path.join(os.path.realpath(root), "packs", "ar-b-pinamar")
hits = []

def _decode(path):
    if path is None:
        return None
    try:
        return os.fsdecode(path)
    except (TypeError, ValueError):
        return None

def _beneath(path, listing=False):
    text = _decode(path)
    if not text or not (os.path.isabs(text) or os.sep in text):
        return False
    real = os.path.realpath(text)
    if real == checkout:
        return listing
    return real.startswith(checkout + os.sep)

_open, _scandir, _walk = os.open, os.scandir, os.walk
_builtin_open = builtins.open

def open_guard(path, *args, **kwargs):
    if kwargs.get("dir_fd") is None and _beneath(path, listing=False):
        hits.append(("open", _decode(path)))
        raise AssertionError("opened checkout private pack child")
    return _open(path, *args, **kwargs)

def builtin_open_guard(file, *args, **kwargs):
    if isinstance(file, (str, bytes, os.PathLike)) and _beneath(file, listing=False):
        hits.append(("builtin.open", _decode(file)))
        raise AssertionError("opened checkout private pack child")
    return _builtin_open(file, *args, **kwargs)

def scandir_guard(path, *args, **kwargs):
    if kwargs.get("dir_fd") is None and _beneath(path, listing=True):
        hits.append(("scandir", _decode(path)))
        raise AssertionError("scandir checkout private pack")
    return _scandir(path, *args, **kwargs)

def walk_guard(top, *args, **kwargs):
    if _beneath(top, listing=True):
        hits.append(("walk", _decode(top)))
        raise AssertionError("walk checkout private pack")
    return _walk(top, *args, **kwargs)

os.open = open_guard
os.scandir = scandir_guard
os.walk = walk_guard
builtins.open = builtin_open_guard
io.open = builtin_open_guard
for key in ("TURNOS_WEBDATA_DIR", "PACK_CHAIN", "TURNOS_PRIVATE_PACKS_DIR",
            "TURNOS_REQUIRED_PACKS", "TURNOS_ENV_FILE", "TURNOS_DATA_DIR"):
    os.environ.pop(key, None)
os.environ["TURNOS_EVAC_ISOLATION_CHILD"] = "1"
os.environ.setdefault("TURNOS_FAKE_AI", "1")
os.environ.setdefault("TURNOS_SKIP_HSI", "1")
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tests"))
os.chdir(root)
if mode == "import":
    import test_private_pack_evacuation_rehearsal  # noqa: F401
elif mode == "suite":
    suite = unittest.defaultTestLoader.discover(
        "tests", pattern="test_private_pack_evacuation_rehearsal.py")
    result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    if hits:
        sys.stderr.write("checkout private pack hits: %r\n" % (hits,))
        raise SystemExit(1)
    raise SystemExit(0 if result.wasSuccessful() else 2)
else:
    raise SystemExit("unknown isolation mode")
if hits:
    sys.stderr.write("checkout private pack hits: %r\n" % (hits,))
    raise SystemExit(1)
print("ok")
"""


@unittest.skipIf(os.environ.get(_ISOLATION_CHILD), "nested isolation probe")
class PrivatePackEvacuationImportIsolationTest(unittest.TestCase):
    def _probe(self, mode, timeout):
        env = os.environ.copy()
        env["TURNOS_FAKE_AI"] = "1"
        env["TURNOS_SKIP_HSI"] = "1"
        env["DATABASE_URL"] = ""
        env.pop("TURNOS_PRIVATE_PACKS_DIR", None)
        completed = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", "-c",
             _ISOLATION_PROBE, ROOT, mode],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        self.assertEqual(
            completed.returncode, 0,
            "isolation probe %s failed:\nstdout:\n%s\nstderr:\n%s" % (
                mode, completed.stdout, completed.stderr))
        return completed

    def test_import_never_opens_scandir_or_walks_checkout_private_pack(self):
        completed = self._probe("import", 30)
        self.assertIn("ok", completed.stdout)

    def test_full_suite_never_opens_scandir_or_walks_checkout_private_pack(self):
        completed = self._probe("suite", 120)
        self.assertIn("Ran 13 tests", completed.stderr)
        self.assertRegex(completed.stderr, r"\nOK")


if __name__ == "__main__":
    unittest.main()
