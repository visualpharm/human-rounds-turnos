"""Tier 6 — install-owned town packs are durable across container rebuilds.

Synthetic acceptance for the slice that makes the first-run installer scaffold
a town pack onto the persistent volume (``data_packs.install_packs_dir()``)
and register it as the installation's REQUIRED private pack, instead of
writing a seemingly-finished pack into the ephemeral image tree (``packs/``).

No fixture reads the checkout's Pinamar pack.  Every pack, private root,
inventory, and rebuilt image is created under one temporary directory.  The
accepted contract, proven here:

  * create town -> simulate a rebuild that repopulates the image tree from the
    build context (bundled public packs only) -> the SAME validated town pack
    still resolves from the volume, and the boot gate still admits it;
  * a missing volume (unmounted / empty) or a corrupt required pack REFUSES
    boot — never a silent fallback to a bundled public copy;
  * an OPTIONAL invalid private pack still falls back to public — the
    reviewed degrade-not-crash contract is unchanged alongside this slice.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused \
        --test tests/test_install_owned_packs.py
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.ops import setup  # noqa: E402


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode, **({"encoding": "utf-8"} if mode == "w" else {})) as f:
        f.write(content)


class _InstallOwnedTestCase(unittest.TestCase):
    """Shared isolation: an image-tree ``packs/`` (ephemeral), a volume root
    (the persistent ``data/packs``), and a scrubbed boot environment."""

    _ENV_KEYS = (
        "TURNOS_DATA_DIR", "TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS",
        "TURNOS_WEBDATA_DIR", "PACK_CHAIN", "TURNOS_CONFIG_LOCK",
        "TURNOS_ENV_FILE",
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="human-rounds-install-owned-")
        self.image_packs = os.path.join(self.tmp, "image-packs")
        self.data_dir = os.path.join(self.tmp, "data")
        self.volume = os.path.join(self.data_dir, "packs")  # install_packs_dir() default
        self.legacy = os.path.join(self.tmp, "legacy")
        os.makedirs(self.image_packs)
        os.makedirs(self.legacy)
        self.patches = [
            patch.object(data_packs, "_PACKS_DIR", self.image_packs),
            patch.object(data_packs, "_LEGACY_WEBDATA_DIR", self.legacy),
            patch.object(config, "_db_overrides", return_value={}),
        ]
        for p in self.patches:
            p.start()
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(data_packs.invalidate)
        self.addCleanup(config._reset_cache_para_tests)
        # volume = <data>/packs, never the checkout
        _env.set_env(self, {**dict.fromkeys(self._ENV_KEYS), "TURNOS_DATA_DIR": self.data_dir})
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers --------------------------------------------------------
    def _seed_public(self, pack_id, asset_name=None, asset=None):
        """A bundled public pack in the image tree, with an optional asset."""
        d = os.path.join(self.image_packs, *pack_id.split("/"))
        os.makedirs(d, exist_ok=True)
        _write(os.path.join(d, "pack.json"),
               json.dumps({"id": pack_id, "level": 4, "name": pack_id}))
        if asset_name:
            _write(os.path.join(d, asset_name), asset)
        return d

    def _rebuild_image(self):
        """Simulate a container rebuild: the image tree is repopulated from
        the build context (bundled public packs only). A clinic's scaffolded
        town was never committed, so it is absent from the rebuilt image; the
        persistent volume is untouched by definition."""
        shutil.rmtree(self.image_packs)
        os.makedirs(self.image_packs)
        self._seed_public("global")  # the bundled public pack comes back

    def _wire_required(self, slug, chain="global,"):
        """The end-state the wizard's commit leaves behind: install root +
        town registered as REQUIRED private boot keys (process env here; the
        wizard writes them to the effective .env via config.save_env_keys,
        exercised in TestSaveEnvKeys)."""
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.volume, TURNOS_REQUIRED_PACKS=slug,
                     PACK_CHAIN=chain + slug)
        config._reset_cache_para_tests()
        data_packs.invalidate()


class TestScaffoldIsInstallOwned(_InstallOwnedTestCase):
    def test_scaffold_writes_the_volume_not_the_image_tree(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        pack_dir = os.path.join(self.volume, slug)
        # The clinic's town lives on the persistent volume...
        self.assertTrue(os.path.isfile(os.path.join(pack_dir, "pack.json")))
        self.assertTrue(os.path.isfile(os.path.join(pack_dir, "sitio.json")))
        # ...never inside the ephemeral image-tree packs/.
        self.assertNotIn(slug, os.listdir(self.image_packs))

    def test_scaffold_freezes_an_inventory_that_the_gate_admits(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        # A scaffolded pack carries the inventory the readiness gate checks.
        inv = os.path.join(self.volume, slug, "INVENTORY.json")
        self.assertTrue(os.path.isfile(inv))
        with open(inv, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual({e["path"] for e in doc["files"]},
                         {"pack.json", "sitio.json"})
        # Wired as required, it resolves and passes the boot gate.
        self._wire_required(slug)
        self.assertEqual(data_packs.resolve_path("sitio.json"),
                         os.path.join(self.volume, slug, "sitio.json"))
        self.assertIsNone(data_packs.verify_required_packs())


class TestDurableAcrossRebuild(_InstallOwnedTestCase):
    """THE HEADLINE: create town -> rebuild -> same validated pack resolves."""

    def test_scaffolded_town_survives_an_image_rebuild(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._wire_required(slug)
        # Pre-rebuild baseline: the town resolves from the volume.
        self.assertEqual(data_packs.manifest(slug)["name"], "Villa Nueva")
        self.assertIn(slug, data_packs.active_chain())
        self.assertIsNone(data_packs.verify_required_packs())

        # Under the OLD behavior (scaffold into the image tree) this rebuild
        # would wipe the town; here it was never in the image, so it survives.
        self._rebuild_image()
        data_packs.invalidate()
        config._reset_cache_para_tests()

        # The SAME validated pack still resolves, and the boot gate still
        # admits it — exactly the post-reboot/post-redeploy state.
        self.assertEqual(data_packs.manifest(slug)["name"], "Villa Nueva")
        self.assertIn(slug, data_packs.active_chain())
        self.assertEqual(data_packs.resolve_path("sitio.json"),
                         os.path.join(self.volume, slug, "sitio.json"))
        self.assertIsNone(data_packs.verify_required_packs())


class TestMissingOrCorruptRequiredRefusesBoot(_InstallOwnedTestCase):
    def test_unmounted_volume_refuses_boot(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._wire_required(slug)
        self.assertIsNone(data_packs.verify_required_packs())
        # Volume not mounted on reboot: never silently serve an empty site or
        # fall back to a bundled public copy.
        shutil.rmtree(self.volume)
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError, "private root invalid"):
            data_packs.verify_required_packs()

    def test_mounted_but_empty_volume_refuses_boot(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._wire_required(slug)
        # Mounted volume but the pack dir is gone (partially seeded volume).
        shutil.rmtree(os.path.join(self.volume, slug))
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError, "directory missing"):
            data_packs.verify_required_packs()

    def test_corrupt_required_pack_refuses_boot(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._wire_required(slug)
        # A byte changed after the inventory was frozen.
        _write(os.path.join(self.volume, slug, "sitio.json"), "corrupt after inventory")
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError, "inventory checksum mismatch"):
            data_packs.verify_required_packs()

    def test_required_never_falls_back_to_a_bundled_public_copy(self):
        # A bundled public town exists, AND a mounted private volume is
        # configured but missing THIS required pack. The required gate must
        # NOT mask the missing volume pack with the bundled public copy.
        self._seed_public("ar-b-pinamar", asset_name="asset.bin", asset=b"public fallback")
        os.makedirs(self.volume)  # mounted, but the required pack isn't seeded on it
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.volume, TURNOS_REQUIRED_PACKS="ar-b-pinamar",
                     PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError, "directory missing"):
            data_packs.verify_required_packs()


class TestOptionalInvalidNeverShadowsPublic(_InstallOwnedTestCase):
    def test_optional_invalid_private_pack_falls_back_to_public(self):
        # The reviewed degrade-not-crash contract is unchanged by this slice:
        # an OPTIONAL (not required) private pack that is malformed still
        # resolves to the bundled public pack, never crashes boot.
        public_dir = self._seed_public("ar-b-pinamar",
                                       asset_name="asset.bin", asset=b"public")
        bad = os.path.join(self.volume, "ar-b-pinamar")
        os.makedirs(bad)
        _write(os.path.join(bad, "pack.json"), "{")  # malformed manifest
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.volume, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))
        self.assertIsNone(data_packs.verify_required_packs())


class TestWriteInventory(_InstallOwnedTestCase):
    def test_write_inventory_round_trips_through_the_readiness_gate(self):
        d = os.path.join(self.volume, "solo")
        os.makedirs(d)
        _write(os.path.join(d, "pack.json"), json.dumps({"id": "solo"}))
        _write(os.path.join(d, "extra.json"), json.dumps({"k": 1}))
        _write(os.path.join(d, "asset.bin"), b"binary fixture")
        data_packs.write_inventory(d)
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.volume, TURNOS_REQUIRED_PACKS="solo")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertIsNone(data_packs.verify_required_packs())
        # A later mutation of any inventoried file is caught.
        _write(os.path.join(d, "asset.bin"), b"changed after inventory")
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError, "checksum mismatch"):
            data_packs.verify_required_packs()

    def test_write_inventory_skips_symlinks(self):
        d = os.path.join(self.volume, "sym")
        os.makedirs(d)
        _write(os.path.join(d, "pack.json"), json.dumps({"id": "sym"}))
        outside = os.path.join(self.tmp, "outside-secret")
        _write(outside, b"secret")
        os.symlink(outside, os.path.join(d, "escape.bin"))  # must not be stamped
        data_packs.write_inventory(d)
        with open(os.path.join(d, "INVENTORY.json"), encoding="utf-8") as f:
            paths = {e["path"] for e in json.load(f)["files"]}
        self.assertEqual(paths, {"pack.json"})


class TestSaveEnvKeys(_InstallOwnedTestCase):
    def _env_file(self):
        path = os.path.join(self.tmp, "boot.env")
        _env.set_env(self, TURNOS_ENV_FILE=path)
        config._reset_cache_para_tests()
        return path

    def test_idempotent_write_preserves_unrelated_lines_and_comments(self):
        path = self._env_file()
        _write(path, "# deployed by ops\nKEEP=1\nOLD_VARIANT=2\n")
        self.assertTrue(config.save_env_keys({
            "TURNOS_PRIVATE_PACKS_DIR": self.volume,
            "TURNOS_REQUIRED_PACKS": "villa-nueva",
        }))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("# deployed by ops", text)
        self.assertIn("KEEP=1", text)
        self.assertIn("OLD_VARIANT=2", text)
        self.assertIn("TURNOS_PRIVATE_PACKS_DIR=%s" % self.volume, text)
        self.assertIn("TURNOS_REQUIRED_PACKS=villa-nueva", text)

    def test_re_write_replaces_in_place_not_duplicate(self):
        path = self._env_file()
        config.save_env_keys({"TURNOS_REQUIRED_PACKS": "first"})
        config.save_env_keys({"TURNOS_REQUIRED_PACKS": "second"})
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count("TURNOS_REQUIRED_PACKS="), 1)
        self.assertIn("TURNOS_REQUIRED_PACKS=second", text)
        # Cache reset => the boot-setting reader sees the new value immediately.
        _env.set_env(self, TURNOS_REQUIRED_PACKS=None)  # let the file win
        self.assertEqual(data_packs._boot_settings().required_packs, "second")

    def test_unwritable_path_returns_false(self):
        # A missing parent dir => the open() raises OSError => False, not a crash.
        _env.set_env(self, TURNOS_ENV_FILE=os.path.join(self.tmp, "no-such-dir", "boot.env"))
        config._reset_cache_para_tests()
        self.assertFalse(config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"}))

    # ---- adversarial: preserve-existing-bytes-or-fail-closed ------------
    # These exercise the hardened writer against the four failure modes the
    # synthetic review flagged. None of them touch the checkout's real .env or
    # any credential: every path is under this test's own temp dir and pointed
    # at via TURNOS_ENV_FILE (an explicit replacement, not an overlay).

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses unix file permissions")
    def test_unreadable_existing_env_is_preserved_not_erased(self):
        # THE HEADLINE BUG: an existing env that cannot be read used to be
        # silently treated as empty and rewritten down to just the new key,
        # erasing every other line. It must now FAIL CLOSED, bytes untouched.
        path = self._env_file()
        original = "# deployed by ops\nKEEP=1\nDB=postgres://real\nOLD=2\n"
        _write(path, original)
        os.chmod(path, 0o000)  # unreadable
        try:
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "villa-nueva"})
        finally:
            os.chmod(path, 0o600)  # restore so the assertions and cleanup can read it
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), original)  # nothing erased

    def test_symlinked_env_target_is_refused_no_follow(self):
        # No-follow on the target: a symlinked env is refused outright, and
        # the writer never emits a byte through the link to its target.
        target = os.path.join(self.tmp, "real-config.env")
        original = "KEEP=1\nDB=postgres://real\n"
        _write(target, original)
        link = os.path.join(self.tmp, "linked.env")
        os.symlink(target, link)
        _env.set_env(self, TURNOS_ENV_FILE=link)
        config._reset_cache_para_tests()
        self.assertFalse(config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"}))
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), original)  # target untouched

    def test_preplaced_tmp_symlink_cannot_redirect_the_write(self):
        # The old writer staged to the FIXED path+".tmp"; a symlink planted
        # there redirected the staging write into an attacker file. The new
        # unique-name, O_EXCL temp is immune, and the planted symlink is
        # neither followed nor consumed.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        attacker = os.path.join(self.tmp, "attacker-capture.env")
        _write(attacker, "ATTACKER=1\n")
        old_fixed_tmp = path + ".tmp"
        os.symlink(attacker, old_fixed_tmp)
        self.assertTrue(config.save_env_keys({"TURNOS_REQUIRED_PACKS": "villa-nueva"}))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("TURNOS_REQUIRED_PACKS=villa-nueva", text)
        self.assertIn("KEEP=1", text)
        with open(attacker, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ATTACKER=1\n")  # nothing written through the link
        self.assertTrue(os.path.islink(old_fixed_tmp))  # left intact, never opened

    def test_target_swap_to_symlink_before_read_refuses_outside_bytes(self):
        # The TOCTOU a separate islink() check could not close: between a path
        # check and a following open() the target can become a symlink, and a
        # plain open then reads the LINK target's bytes into the effective env.
        # The no-follow descriptor open refuses it at one atomic step, so even a
        # swap landing in that gap is never followed and outside bytes are never
        # consumed. Deterministic: the swap is injected at the instant of the
        # descriptor open — no timing luck.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        outside = os.path.join(self.tmp, "outside-secret.env")
        _write(outside, "OUTSIDE_SECRET=pwned\n")
        real_open = os.open

        def swapping_open(p, flags, *rest):
            # The instant the writer's descriptor open targets the env path,
            # swap it to a symlink at the outside file, then let the real open
            # proceed. Deterministic stand-in for "became a symlink between the
            # check and the open". Other os.open calls (the lock file, staging)
            # target different paths and pass through untouched.
            if os.path.abspath(p) == os.path.abspath(path):
                try:
                    if os.path.lexists(p):
                        os.unlink(p)
                    os.symlink(outside, p)
                except OSError:
                    pass
            return real_open(p, flags, *rest)

        with patch.object(config.os, "open", swapping_open):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "villa-nueva"})

        self.assertFalse(result)               # the symlinked target was refused
        self.assertTrue(os.path.islink(path))  # we did NOT replace it with a
                                               # regular file built from outside bytes
        # The outside file is byte-for-byte intact: nothing was written through
        # the link, and its bytes were never copied into the effective env.
        with open(outside, encoding="utf-8") as f:
            self.assertEqual(f.read(), "OUTSIDE_SECRET=pwned\n")

    def test_concurrent_writers_never_corrupt_or_leak_staging_files(self):
        # Concurrency-safe atomic replacement: concurrent writers each stage a
        # UNIQUE temp file, so the env is never left torn/garbled the way a
        # shared fixed-name staging file could, and no staging file leaks.
        import threading
        path = self._env_file()
        _write(path, "SEED=1\n")
        keys = ("ALPHA", "BETA", "GAMMA", "DELTA")
        errors = []

        def writer(key):
            try:
                for _ in range(40):
                    if not config.save_env_keys({key: "1"}):
                        errors.append(RuntimeError("%s write failed" % key))
            except Exception as exc:  # noqa: BLE001 — surface any failure
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(k,)) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        # The file is ALWAYS well-formed: every non-blank line is KEY=VALUE.
        # A shared staging file written by two handles at once produced
        # partial/garbled lines here.
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.assertIn("=", line)
        # No staging files leaked in the env's directory.
        leftovers = [n for n in os.listdir(os.path.dirname(path) or ".")
                     if n.startswith(".env-") and n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_concurrent_one_write_per_key_all_updates_survive(self):
        # The lost-update race this closes: two successful read-modify-replace
        # calls that interleave each read the same base, stage base+their-key,
        # and the last os.replace silently wins — every other writer's key is
        # lost. Serializing the whole RMW (advisory flock on a stable lock file)
        # makes each writer re-read the prior result and layer its key, so EVERY
        # successful key update survives. Deterministic: a Barrier releases all
        # writers together BEFORE any takes the lock, maximizing the overlap that
        # without serialization loses N-1 of N distinct keys; with the lock the
        # writers contend and serialize regardless.
        import threading
        path = self._env_file()
        _write(path, "SEED=1\n")
        keys = [("KEY%02d" % i, str(i)) for i in range(12)]
        barrier = threading.Barrier(len(keys))
        errors = []

        def overlap():
            # Outside the lock by design (the hook fires before _acquire_write_lock):
            # every writer reaches here, then all are released at once into the
            # lock contend. No deadlock — the barrier never waits under the lock.
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass

        config._SAVE_ENV_KEYS_TEST_HOOK = overlap
        try:
            def writer(k, v):
                try:
                    if not config.save_env_keys({k: v}):
                        errors.append(RuntimeError("write %s failed" % k))
                except Exception as exc:  # noqa: BLE001 — surface any failure
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=kv) for kv in keys]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            config._SAVE_ENV_KEYS_TEST_HOOK = None

        self.assertEqual(errors, [])
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("SEED=1", text)
        for k, v in keys:
            self.assertIn("%s=%s" % (k, v), text, "lost key %s" % k)
        # No staging files leaked.
        leftovers = [name for name in os.listdir(os.path.dirname(path) or ".")
                     if name.startswith(".env-") and name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_existing_file_mode_is_preserved_across_a_key_update(self):
        # A key update does not silently re-permission the env: the file's
        # existing mode rides through the atomic replace.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        os.chmod(path, 0o640)
        self.assertTrue(config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"}))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    # ---- three release blockers Terra reproduced: lock/serialization/stage ---

    def test_lock_file_replaced_after_acquisition_fails_closed(self):
        # The lock file is STABLE under our own code (we never replace it), so
        # the only way its path names a DIFFERENT inode than the fd we just
        # locked is an external replacement. O_NOFOLLOW only refuses a swap AT
        # open time; after the flock a sibling opening the same path could lock
        # that other inode and bypass mutual exclusion. The post-acquisition
        # re-stat (path vs fd) detects the divergence and fails closed.
        # Deterministic: the swap is injected the instant the flock returns,
        # before the validation reads the path — no timing window.
        import fcntl as real_fcntl
        path = self._env_file()
        _write(path, "KEEP=1\n")
        lockpath = path + ".lock"
        real_flock = real_fcntl.flock

        def swapping_flock(fd, op):
            real_flock(fd, op)
            if op == real_fcntl.LOCK_EX:
                # Replace the lock path with a fresh regular file (new inode)
                # the instant we hold LOCK_EX on the original fd.
                fd2, fresh = tempfile.mkstemp(dir=os.path.dirname(lockpath) or ".")
                os.close(fd2)
                os.replace(fresh, lockpath)

        with patch.object(config._fcntl, "flock", side_effect=swapping_flock):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})

        self.assertFalse(result)  # detected the swap, never wrote unserialized
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")
        # Never reached staging => no temp leaked.
        leftovers = [n for n in os.listdir(os.path.dirname(path) or ".")
                     if n.startswith(".env-") and n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_no_fcntl_fails_closed_instead_of_writing_unserialized(self):
        # fcntl is POSIX-only. If it were ever unavailable (a non-POSIX port),
        # the old code silently skipped the flock and let two
        # read-modify-replace calls race — reopening the exact lost-update this
        # slice closed. The hardened writer fails closed: no serialization =>
        # no write, never a silent unserialized one.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        with patch.object(config, "_fcntl", None):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")  # nothing written

    def test_staging_creation_failure_returns_false_not_raises(self):
        # The fail-closed installer contract is ``return False`` on ANY write
        # failure. The staging ``tempfile.mkstemp`` used to sit OUTSIDE the
        # try/except, so a failure (disk full, the dir going read-only between
        # the lock and the stage) escaped the writer as an exception after the
        # lock was already released. Now it returns False and leaves the
        # existing bytes untouched. Deterministic: mkstemp is patched to raise.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        with patch.object(config.tempfile, "mkstemp",
                          side_effect=OSError("no space left on device")):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")  # existing bytes preserved
        # mkstemp raised before creating a temp => nothing leaked.
        leftovers = [n for n in os.listdir(os.path.dirname(path) or ".")
                     if n.startswith(".env-") and n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    # ---- three reviewer contracts GLM's first closeout left open -----------
    # (1) the late lock replacement that lands AFTER _acquire_write_lock
    #     returns and BEFORE the env commit;
    # (2) the unsafe existing-lock policy (mode + symlink);
    # (3) the fdopen/staging ownership path that must never leak an owned fd.

    def test_lock_replaced_after_acquisition_before_commit_fails_closed(self):
        # Terra's finding against the first closeout: the post-acquisition
        # identity check inside _acquire_write_lock fires ONLY right after the
        # flock. A lockpath replacement landing AFTER _acquire_write_lock
        # returned and BEFORE the env commit went undetected, and
        # save_env_keys still returned True — committing unserialized bytes
        # while a sibling could open the swapped path and lock a different
        # inode. The commit gate now re-validates path-vs-fd, so the WHOLE
        # acquisition-to-commit window is fail-closed. Deterministic: the
        # swap is injected at staging time (unambiguously past acquisition,
        # before the commit gate) — no timing luck.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        lockpath = path + ".lock"
        real_mkstemp = tempfile.mkstemp

        def swapping_mkstemp(*a, **kw):
            # We are past acquisition: replace the lock path with a fresh
            # regular file (new inode), so a sibling opening that path would
            # lock a different inode than the fd we hold.
            try:
                fd2, fresh = real_mkstemp(dir=os.path.dirname(lockpath) or ".")
                os.close(fd2)
                os.replace(fresh, lockpath)
            except OSError:
                pass
            return real_mkstemp(*a, **kw)

        with patch.object(config.tempfile, "mkstemp", side_effect=swapping_mkstemp):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})

        self.assertFalse(result)  # detected the late swap, never committed
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")  # existing bytes preserved
        # Fail-closed before the rename => no staging file leaked.
        leftovers = [n for n in os.listdir(os.path.dirname(path) or ".")
                     if n.startswith(".env-") and n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    # ---- Terra's no-follow/stable-link bypass: symlink → hardlink alias -----
    # The one remaining concrete bypass after the closeout: the identity check
    # used os.stat (follows symlinks). Replacing .lock with a SYMLINK to a
    # HARDLINK ALIAS of the same locked inode still resolved to the right inode
    # under os.stat, so _lock_path_matches_fd returned True and save_env_keys
    # committed the env while .lock was already a symlink — the no-follow/
    # stable-lock identity contract was broken. The fix is lstat (no-follow) +
    # regular-file + same-inode + a single-link policy (st_nlink == 1): the
    # locked inode must have exactly one name, so no alias exists to point a
    # same-inode symlink at.

    def test_lock_swapped_to_symlink_at_hardlink_alias_fails_closed(self):
        # THE EXACT BYPASS. After acquisition (deterministically, at staging
        # time — unambiguously past the flock, before the commit gate), an
        # actor who can write the directory hardlinks an alias of the locked
        # inode and replaces .lock with a symlink to that alias. os.stat
        # follows the symlink to the alias to the same inode, so the OLD
        # _lock_path_matches_fd returned True and save_env_keys returned True,
        # committing the env while .lock was a symlink. lstat + single-link
        # now detect it and fail closed. Deterministic: the swap is injected
        # at the mkstemp call (same instant the late-swap test uses) — no
        # timing luck, and no other os.open call targets the lock path.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        lockpath = path + ".lock"
        alias = lockpath + ".alias"
        real_mkstemp = tempfile.mkstemp

        def swapping_mkstemp(*a, **kw):
            # Past acquisition: .lock is the regular inode we hold the flock on.
            # 1) hardlink an alias of THAT inode (st_nlink -> 2). 2) replace
            # .lock with a symlink to the alias. Under os.stat the path still
            # resolves to the same inode; under lstat it is a symlink.
            try:
                if os.path.lexists(alias):
                    os.unlink(alias)
                os.link(lockpath, alias)            # alias -> locked inode
                if os.path.lexists(lockpath):
                    os.unlink(lockpath)
                os.symlink(alias, lockpath)         # .lock -> symlink -> alias
            except OSError:
                pass
            return real_mkstemp(*a, **kw)

        with patch.object(config.tempfile, "mkstemp", side_effect=swapping_mkstemp):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})

        self.assertFalse(result)              # the symlinked lock was detected
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")  # existing bytes preserved
        # Our code never "repaired" or followed the symlinked lock: it is left
        # exactly as the swap left it.
        self.assertTrue(os.path.islink(lockpath))
        # Fail-closed before the rename => no staging file leaked.
        leftovers = [n for n in os.listdir(os.path.dirname(path) or ".")
                     if n.startswith(".env-") and n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_lock_file_with_a_hardlink_alias_is_refused_at_acquisition(self):
        # The single-link policy at acquisition (defense-in-depth + the coherent
        # boundary): a pre-existing lock that already carries a hardlink alias
        # (st_nlink > 1) is not a contention point this code trusts — the alias
        # is the prerequisite of the symlink→alias→same-inode bypass above. It
        # is refused fail-closed BEFORE the flock, and the env bytes survive.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        lockpath = path + ".lock"
        alias = lockpath + ".alias"
        _write(lockpath, "")           # a pre-placed lock...
        os.chmod(lockpath, 0o600)      # ...that otherwise matches our policy
        os.link(lockpath, alias)       # ...but has a hardlink alias (nlink 2)
        result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")

    def test_unsafe_group_writable_lock_file_is_refused(self):
        # Existing lock files must satisfy a safe regular-file ownership/mode
        # policy. A pre-placed lock with group/other write bits is not a
        # contention point this code can trust — an actor with write access
        # could churn its inode. The policy refuses it fail-closed before ANY
        # write, and the existing env bytes are preserved.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        lockpath = path + ".lock"
        _write(lockpath, "")            # pre-placed lock
        os.chmod(lockpath, 0o666)       # group + other writable: unsafe
        result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")

    def test_symlinked_lock_path_is_refused_no_follow(self):
        # The safe-lock policy begins at the no-follow open: a symlink planted
        # at the lock name is never flocked through (ELOOP), fail-closed
        # before any read or write. Pinned for the lock path (distinct from
        # the env-target symlink test above).
        path = self._env_file()
        _write(path, "KEEP=1\n")
        target = os.path.join(self.tmp, "lock-target")
        _write(target, "")
        lockpath = path + ".lock"
        os.symlink(target, lockpath)
        result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")
        self.assertTrue(os.path.islink(lockpath))  # never replaced by our code

    def test_fdopen_failure_closes_owned_fd_and_unlinks_staging(self):
        # fdopen is the one step that TAKES OWNERSHIP of the staging fd: if
        # its construction fails before closefd=True takes effect, the owned
        # descriptor leaked and the exception escaped the False-return
        # installer contract (the old ``with os.fdopen(fd, ...)`` only closed
        # the fd once the context manager engaged). Deterministic: os.fdopen
        # is patched to fail on the staging (mode "w") call only; the prior
        # read of the existing env (mode "r") still succeeds.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        real_fdopen = os.fdopen
        real_mkstemp = tempfile.mkstemp
        staging = {"fd": None, "tmp": None}

        def tracking_mkstemp(*a, **kw):
            fd, tmp = real_mkstemp(*a, **kw)
            staging["fd"] = fd
            staging["tmp"] = tmp
            return fd, tmp

        def raising_fdopen(fd, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode")
            if mode == "w":
                raise OSError("fdopen boom")
            return real_fdopen(fd, *args, **kwargs)

        with patch.object(config.tempfile, "mkstemp", side_effect=tracking_mkstemp), \
             patch.object(config.os, "fdopen", side_effect=raising_fdopen):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})

        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")  # existing bytes preserved
        # The owned staging descriptor was CLOSED (not leaked): fstat on it
        # now fails EBADF. (Single-threaded, no intervening opens, so the fd
        # number was not recycled before this check.)
        self.assertIsNotNone(staging["fd"])
        with self.assertRaises(OSError):
            os.fstat(staging["fd"])
        # The staging temp was UNLINKED (not leaked on disk).
        self.assertFalse(os.path.exists(staging["tmp"]))

    def test_mid_staging_fsync_failure_unlinks_tmp_returns_false(self):
        # The staging ownership contract on the WRITE side: a failure AFTER
        # fdopen took ownership (here fsync) must still unlink the staging
        # temp and return False, never leak the temp or escape the contract.
        # Deterministic: os.fsync is patched to raise.
        path = self._env_file()
        _write(path, "KEEP=1\n")
        with patch.object(config.os, "fsync", side_effect=OSError("io error")):
            result = config.save_env_keys({"TURNOS_REQUIRED_PACKS": "x"})
        self.assertFalse(result)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP=1\n")  # existing bytes preserved
        leftovers = [n for n in os.listdir(os.path.dirname(path) or ".")
                     if n.startswith(".env-") and n.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestPackChainSurvivesLockedConfigRestart(_InstallOwnedTestCase):
    """The configuration-ownership contract: the chain that NAMES an
    install-owned (required) town pack is a boot key, persisted alongside the
    private root and required pack, so a locked-config restart
    (``TURNOS_CONFIG_LOCK=1``) still SERVES the town — not the bundled public
    default — while the required-pack gate still VALIDATES it. Before this
    contract the wizard wrote the chain to the DB only: validate-private,
    serve-public.

    Synthetic restart: the DB row the wizard also wrote is ignored under the
    lock, no ``PACK_CHAIN`` rides in the process env, and the default chain
    contains no scaffolded slug — so the town can only resolve from the boot
    ``.env`` the installer wrote. No real clinic data, credentials, or deploy.
    """

    def _env_file(self):
        path = os.path.join(self.tmp, "boot.env")
        _env.set_env(self, TURNOS_ENV_FILE=path)
        config._reset_cache_para_tests()
        return path

    def test_locked_restart_serves_the_town_from_the_boot_env(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")

        # The wizard's commit: private root + required pack + the chain naming
        # the town, ALL three as boot keys in the effective .env — the only
        # channel that survives TURNOS_CONFIG_LOCK=1.
        self._env_file()
        self.assertTrue(config.save_env_keys({
            "TURNOS_PRIVATE_PACKS_DIR": self.volume,
            "TURNOS_REQUIRED_PACKS": slug,
            "PACK_CHAIN": "global,%s" % slug,
        }))

        # The wizard also wrote the chain to the DB. Model that with a
        # DIFFERENT chain (the public default) so the assertions below can
        # only pass if the lock defers to the .env boot key, never the DB.
        # Under the lock config.get never consults the DB; this also guards a
        # future precedence regression that accidentally did.
        with patch.object(config, "_db_overrides",
                          return_value={"PACK_CHAIN": "global,ar,ar-b,ar-b-pinamar"}):
            # no process-env chain either
            _env.set_env(self, PACK_CHAIN=None, TURNOS_CONFIG_LOCK="1")
            config._reset_cache_para_tests()
            data_packs.invalidate()

            # The town is VALIDATED by the required-pack gate (boot keys)...
            self.assertIsNone(data_packs.verify_required_packs())
            # ...AND SERVED by the chain read from the .env boot key. The DB's
            # public default and the engine default neither name this town.
            chain = data_packs.active_chain()
            self.assertIn(slug, chain)
            self.assertEqual(
                data_packs.resolve_path("sitio.json"),
                os.path.join(self.volume, slug, "sitio.json"),
            )

    def test_no_env_chain_under_lock_falls_to_the_public_default(self):
        """The failure mode this contract closes, pinned as a contrast: if the
        chain is NOT a boot key (the wizard's pre-fix state wrote it to the DB
        only), a locked-config restart validates the required town yet serves
        the bundled public default — the town resolves from NEITHER."""
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        # Only the private root + required pack are boot keys; the chain is DB
        # only (the old behavior).
        self._env_file()
        self.assertTrue(config.save_env_keys({
            "TURNOS_PRIVATE_PACKS_DIR": self.volume,
            "TURNOS_REQUIRED_PACKS": slug,
        }))
        with patch.object(config, "_db_overrides",
                          return_value={"PACK_CHAIN": "global,%s" % slug}):
            _env.set_env(self, PACK_CHAIN=None, TURNOS_CONFIG_LOCK="1")
            config._reset_cache_para_tests()
            data_packs.invalidate()
            # The required town still validates from its boot keys...
            self.assertIsNone(data_packs.verify_required_packs())
            # ...but, with no boot-key chain, the lock falls through to the
            # default — which does not name the scaffolded town.
            self.assertNotIn(slug, data_packs.active_chain())


class TestReplacementContainerLifecycle(_InstallOwnedTestCase):
    """THE INSTALL-OWNED DURABILITY PROOF, end to end.

    The container's CANONICAL config path lives on the persistent volume
    (the Dockerfile's default ``TURNOS_ENV_FILE=/app/data/turnos.env``), so a
    container *replacement* — image rebuilt from a fresh build context, the
    volume untouched by definition — keeps an install-owned town pack
    REACHABLE and VALIDATED: its boot keys (private root, required pack, the
    chain naming it) are read back from the volume config the installer wrote.

    Note on the legacy default it replaces: ``.env`` is excluded by
    ``.dockerignore``, so the checkout-root ``.env`` (``config.ENV_PATH`` ->
    ``/app/.env`` in the container) was NEVER baked into the image. With no
    ``TURNOS_ENV_FILE`` default, ``save_env_keys`` created ``/app/.env`` at
    RUNTIME in the ephemeral container filesystem, which the next rebuild
    wiped. That is the durability bug this closes — an ephemeral runtime file,
    not a baked one.

    Synthetic: one temp dir models both surfaces —
      * the image tree (ephemeral): image ``packs/`` AND the checkout-root
        ``.env`` that ``config.ENV_PATH`` resolves to (runtime-created in the
        container, never baked — ``.dockerignore`` excludes it);
      * the persistent volume: ``<data>/packs`` (the town) AND
        ``<data>/turnos.env`` (the canonical config the container points
        ``TURNOS_ENV_FILE`` at).
    A "replacement" wipes the image tree and regenerates it with bundled public
    packs only — exactly what rebuilding the image does — while the volume is
    left intact. No real image is built; the lifecycle is driven through the
    real ``config._env_path`` / ``save_env_keys`` / ``data_packs`` machinery.
    """

    # The three boot keys that move together — cleared from the process env so
    # the proof forces them to resolve from the file alone.
    _TRIPLET = ("TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS", "PACK_CHAIN")

    def setUp(self):
        super().setUp()
        # The canonical container config ON THE PERSISTENT VOLUME. This is what
        # the Dockerfile's ENV TURNOS_ENV_FILE=/app/data/turnos.env points at.
        self.volume_env = os.path.join(self.data_dir, "turnos.env")
        _env.set_env(self, TURNOS_ENV_FILE=self.volume_env)
        config._reset_cache_para_tests()

    def _clear_triplet(self):
        _env.set_env(self, dict.fromkeys(self._TRIPLET))

    def _installer_commit(self, slug):
        """The wizard's commit: town on the volume, boot keys in the volume
        config (the EFFECTIVE .env, because TURNOS_ENV_FILE points there)."""
        self.assertTrue(config.save_env_keys({
            "TURNOS_PRIVATE_PACKS_DIR": self.volume,
            "TURNOS_REQUIRED_PACKS": slug,
            "PACK_CHAIN": "global,%s" % slug,
        }))

    def _assert_town_live(self, slug):
        self.assertEqual(config.env_file_path(), self.volume_env)
        # Boot keys resolve from the volume config (process triplet cleared).
        for key in self._TRIPLET:
            self.assertIsNone(os.environ.get(key), key)
        vals = config.dotenv_values()
        self.assertEqual(vals.get("TURNOS_REQUIRED_PACKS"), slug)
        self.assertIn(slug, vals.get("PACK_CHAIN", ""))
        # The town is validated by the required-pack gate AND served by the chain.
        self.assertIsNone(data_packs.verify_required_packs())
        self.assertEqual(data_packs.manifest(slug)["name"], "Villa Nueva")
        self.assertIn(slug, data_packs.active_chain())
        self.assertEqual(
            data_packs.resolve_path("sitio.json"),
            os.path.join(self.volume, slug, "sitio.json"))

    def _replace_container(self):
        """Container replacement: fresh image (bundled public packs only), same
        volume. The process env is re-scrubbed so the boot keys cannot leak in
        from the prior run."""
        self._rebuild_image()
        self._clear_triplet()
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def test_install_owned_config_survives_a_container_replacement(self):
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._installer_commit(slug)
        self._clear_triplet()
        self._assert_town_live(slug)

        # ---- container replacement: fresh image, same volume ----------------
        self._replace_container()

        # The volume config is untouched, so the boot keys STILL resolve from
        # the canonical path and the town is STILL validated + served. This is
        # exactly the post-redeploy state an install must survive.
        self._assert_town_live(slug)

    def test_locked_replacement_with_conflicting_db_serves_the_volume_town(self):
        # Gap 1: after a replacement, under TURNOS_CONFIG_LOCK=1 with a
        # CONFLICTING DB PACK_CHAIN and the process triplet cleared, the town
        # is served from the VOLUME config the installer wrote — never the DB.
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._installer_commit(slug)
        self._replace_container()  # image rebuilt, process triplet cleared

        # The wizard also wrote the chain to the DB. Model that with the PUBLIC
        # default chain (no scaffolded slug) so the assertions below can ONLY
        # pass if the lock defers to the volume boot key, never the DB. Under
        # the lock config.get never consults the DB; this also guards a future
        # precedence regression that accidentally did.
        with patch.object(config, "_db_overrides",
                          return_value={"PACK_CHAIN": "global,ar,ar-b,ar-b-pinamar"}):
            _env.set_env(self, TURNOS_CONFIG_LOCK="1")
            config._reset_cache_para_tests()
            data_packs.invalidate()
            # The town is VALIDATED by the required-pack gate (boot keys)...
            self.assertIsNone(data_packs.verify_required_packs())
            # ...AND SERVED by the chain read from the volume boot key. The
            # DB's public default does not name this town.
            self.assertIn(slug, data_packs.active_chain())
            self.assertEqual(
                data_packs.resolve_path("sitio.json"),
                os.path.join(self.volume, slug, "sitio.json"))

    def test_process_env_overrides_the_volume_config_under_lock(self):
        # Gap 1: direct process-env precedence. Under the lock the precedence is
        # process env > effective file (DB is ignored). A boot key set in the
        # process env WINS over the volume config's value; a key absent from
        # the process env falls through to the file. (Both go through the SAME
        # single effective path — there is no overlay reader for a second file.)
        self._seed_public("global")
        setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._installer_commit("villa-nueva")  # writes PACK_CHAIN to the volume
        self._clear_triplet()
        _env.set_env(self, TURNOS_CONFIG_LOCK="1")
        # File value, no process env: the volume config wins (PACK_CHAIN is
        # read by config.get, which under the lock never consults the DB).
        self.assertEqual(config.get("PACK_CHAIN"), "global,villa-nueva")
        # Process env set: it wins over the volume file value.
        _env.set_env(self, PACK_CHAIN="global")
        config._reset_cache_para_tests()
        self.assertEqual(config.get("PACK_CHAIN"), "global")
        # The volume file is unchanged underneath — it is not merged.
        self.assertEqual(
            config.dotenv_values().get("PACK_CHAIN"), "global,villa-nueva")

    def test_missing_private_root_with_config_present_refuses_boot_after_replacement(self):
        # Gap 2, stated truthfully. After the replacement the volume is retained
        # by definition; the canonical config (turnos.env) is STILL PRESENT on it
        # and STILL asserts the town is REQUIRED. What goes missing here is the
        # configured private pack ROOT directory the config points at — and THAT
        # mismatch is what fails closed, never a silent fall-through to a bundled
        # public copy.
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._installer_commit(slug)
        self._replace_container()
        self.assertIsNone(data_packs.verify_required_packs())
        # Remove the configured private root ONLY (data/packs). The canonical
        # config (data/turnos.env, a sibling) is intentionally left in place —
        # the requirement fact survives, so the gate still has something to check.
        shutil.rmtree(self.volume)
        self.assertTrue(os.path.isfile(self.volume_env))  # config still present
        self.assertEqual(
            config.dotenv_values().get("TURNOS_REQUIRED_PACKS"), slug)
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError,
                                    "private root invalid"):
            data_packs.verify_required_packs()
        # Honest boundary of THIS design: the canonical config and the packs
        # share ONE volume. A TOTAL /app/data loss removes turnos.env too, so the
        # gate no longer has the fact that any pack is required and boot degrades
        # silently to bundled public packs — NOT detectable here. Detecting
        # whole-volume loss would need the requirement-fact stored off-volume,
        # which is a separate architecture this slice deliberately does not add.

    def test_corrupt_configured_pack_with_config_present_refuses_boot_after_replacement(self):
        # Gap 2: the canonical config stays present on the retained volume; a
        # byte changed in the configured private pack it points at is caught by
        # the frozen inventory — boot refuses, never serves a tampered town
        # silently.
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        self._installer_commit(slug)
        self._replace_container()
        self.assertIsNone(data_packs.verify_required_packs())
        _write(os.path.join(self.volume, slug, "sitio.json"),
               "corrupt after replacement")
        self.assertTrue(os.path.isfile(self.volume_env))  # config still present
        data_packs.invalidate()
        with self.assertRaisesRegex(data_packs.RequiredPackError,
                                    "inventory checksum mismatch"):
            data_packs.verify_required_packs()

    def test_default_path_is_ephemeral_not_baked(self):
        # Gap 3 contrast, exercising config.ENV_PATH truthfully: with
        # TURNOS_ENV_FILE UNSET, _env_path() returns config.ENV_PATH — the
        # pre-fix container default. .dockerignore excludes .env, so nothing is
        # baked: save_env_keys CREATES that file at runtime in the ephemeral
        # container filesystem, and a replacement wipes it. ENV_PATH is patched
        # to a temp path (never the real checkout .env) so the fallback branch
        # runs honestly without touching a developer's checkout.
        self._seed_public("global")
        slug = setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        ephemeral_env = os.path.join(self.tmp, "ephemeral-container", ".env")
        # The parent dir exists (as /app does in the container); the file does
        # not — .dockerignore excludes .env, so it is never baked into the image.
        os.makedirs(os.path.dirname(ephemeral_env), exist_ok=True)
        self.assertFalse(os.path.exists(ephemeral_env))  # not baked: absent
        _env.set_env(self, TURNOS_ENV_FILE=None)
        with patch.object(config, "ENV_PATH", ephemeral_env):
            config._reset_cache_para_tests()
            self.assertEqual(config.env_file_path(), ephemeral_env)
            # The installer writes to the fallback path — creating it at runtime.
            self.assertTrue(config.save_env_keys({
                "TURNOS_REQUIRED_PACKS": slug,
                "PACK_CHAIN": "global,%s" % slug,
            }))
            self.assertTrue(os.path.isfile(ephemeral_env))  # now it exists
            self.assertEqual(
                config.dotenv_values()["TURNOS_REQUIRED_PACKS"], slug)
            # ---- container replacement: ephemeral filesystem wiped -----------
            shutil.rmtree(os.path.dirname(ephemeral_env))
            config._reset_cache_para_tests()
            # The boot key is GONE — a runtime-created path in the image's
            # ephemeral filesystem is not durable. The on-volume canonical path
            # is precisely what makes the install survive.
            self.assertNotIn("TURNOS_REQUIRED_PACKS", config.dotenv_values())

    def test_explicit_override_replaces_the_volume_path(self):
        # An operator who sets TURNOS_ENV_FILE explicitly (compose
        # `environment:`, Coolify env, `docker run -e`) REPLACES the image
        # default entirely — the volume config is not read AND not merged.
        # (No overlay: the override IS the single effective path.)
        self._seed_public("global")
        setup._scaffold_pack("Villa Nueva", "Buenos Aires", "global")
        # The volume config holds one value...
        self.assertTrue(config.save_env_keys({"TURNOS_REQUIRED_PACKS": "volume"}))
        self.assertEqual(config.env_file_path(), self.volume_env)
        # ...an explicit override points somewhere else, with its own value...
        override = os.path.join(self.tmp, "ops-override.env")
        _write(override, "TURNOS_REQUIRED_PACKS=ops-chosen\n")
        _env.set_env(self, TURNOS_ENV_FILE=override)
        config._reset_cache_para_tests()
        # ...and the override's value wins; the volume's is not merged in.
        self.assertEqual(config.env_file_path(), override)
        self.assertEqual(
            config.dotenv_values().get("TURNOS_REQUIRED_PACKS"), "ops-chosen")


if __name__ == "__main__":
    unittest.main()
