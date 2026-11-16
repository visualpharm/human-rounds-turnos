"""Synthetic adversarial coverage for Tier 6 private-pack readiness.

No fixture reads the checkout's Pinamar pack.  Every pack, private root,
inventory, and symlink is created under one temporary directory.
"""

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.services import static_assets  # noqa: E402
from app import asgi_main  # noqa: E402
from _livehttp import LiveServer  # noqa: E402
from _fstime import restore_mtime_after_ctime_moves  # noqa: E402


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _inventory(directory):
    files = []
    for current, _, names in os.walk(directory):
        for name in sorted(names):
            path = os.path.join(current, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            if rel == "INVENTORY.json":
                continue
            with open(path, "rb") as f:
                body = f.read()
            files.append({"path": rel, "size": len(body),
                          "sha256": hashlib.sha256(body).hexdigest()})
    _write(os.path.join(directory, "INVENTORY.json"),
           json.dumps({"files": files}))


def _pack(root, pack_id="ar-b-pinamar", asset=b"private fixture"):
    directory = os.path.join(root, *pack_id.split("/"))
    _write(os.path.join(directory, "pack.json"), json.dumps({"id": pack_id}))
    _write(os.path.join(directory, "asset.bin"), asset)
    _inventory(directory)
    return directory


class RequiredPackReadinessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="human-rounds-private-pack-")
        self.public = os.path.join(self.tmp, "public-packs")
        self.private = os.path.join(self.tmp, "private-packs")
        self.legacy = os.path.join(self.tmp, "legacy")
        os.makedirs(self.public)
        os.makedirs(self.private)
        os.makedirs(self.legacy)
        self.patches = [
            patch.object(data_packs, "_PACKS_DIR", self.public),
            patch.object(data_packs, "_LEGACY_WEBDATA_DIR", self.legacy),
            patch.object(static_assets, "WEBDATA_DIR", self.legacy),
            patch.object(config, "_db_overrides", return_value={}),
        ]
        for item in self.patches:
            item.start()
        _env.set_env(self, dict.fromkeys((
            "TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS",
            "TURNOS_WEBDATA_DIR", "PACK_CHAIN", "TURNOS_CONFIG_LOCK",
            "TURNOS_ENV_FILE",
        )))
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def tearDown(self):
        config._reset_cache_para_tests()
        data_packs.invalidate()
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _require_private(self):
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.private, TURNOS_REQUIRED_PACKS="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def _assert_refused(self, expected):
        with self.assertRaisesRegex(data_packs.RequiredPackError, expected) as caught:
            data_packs.verify_required_packs()
        # Diagnostics intentionally never echo fixture paths, asset bytes, or hashes.
        self.assertNotIn(self.tmp, str(caught.exception))
        self.assertNotIn("private fixture", str(caught.exception))

    def test_unset_new_settings_leave_public_resolution_unchanged(self):
        public_dir = _pack(self.public, asset=b"public fixture")
        _env.set_env(self, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))
        self.assertIsNone(data_packs.verify_required_packs())

    def test_valid_private_stub_wins_resolution_and_passes_readiness(self):
        _pack(self.public, asset=b"public fixture")
        private_dir = _pack(self.private)
        self._require_private()
        _env.set_env(self, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(private_dir, "asset.bin"))
        self.assertIsNone(data_packs.verify_required_packs())

    def test_private_pack_dir_requires_a_verified_private_pack(self):
        with self.assertRaisesRegex(data_packs.RequiredPackError,
                                    "private pack unavailable"):
            data_packs.private_pack_dir("ar-b-pinamar")

        expected = _pack(self.private)
        self._require_private()
        self.assertEqual(data_packs.private_pack_dir("ar-b-pinamar"), expected)

        with self.assertRaisesRegex(ValueError, "invalid pack identifier"):
            data_packs.private_pack_dir("../outside")

    def test_nested_private_pack_uses_the_town_packs_closed_inventory(self):
        top = os.path.join(self.private, "ar-b-pinamar")
        nested = os.path.join(top, "centers", "example-hospital")
        _write(os.path.join(top, "pack.json"),
               json.dumps({"id": "ar-b-pinamar"}))
        _write(os.path.join(nested, "pack.json"), json.dumps({
            "id": "ar-b-pinamar/centers/example-hospital",
        }))
        _write(os.path.join(nested, "preparation.json"), "{}")
        _inventory(top)
        self._require_private()

        self.assertEqual(
            data_packs.private_pack_dir(
                "ar-b-pinamar/centers/example-hospital"
            ),
            nested,
        )

    def test_db_configuration_is_not_used_by_the_pre_migration_safety_gate(self):
        public_dir = _pack(self.public, asset=b"public boot fixture")
        _pack(self.private)
        with patch.object(config, "_db_overrides", return_value={
            "TURNOS_PRIVATE_PACKS_DIR": self.private,
            "TURNOS_REQUIRED_PACKS": "ar-b-pinamar",
        }):
            config._reset_cache_para_tests()
            data_packs.invalidate()
            _env.set_env(self, PACK_CHAIN="ar-b-pinamar")
            self.assertEqual(data_packs.resolve_path("asset.bin"),
                             os.path.join(public_dir, "asset.bin"))
            self.assertIsNone(data_packs.verify_required_packs())

    def test_effective_env_file_configures_pre_migration_safety_gate(self):
        private_dir = _pack(self.private, asset=b"file fixture")
        env_file = os.path.join(self.tmp, "boot.env")
        _write(env_file, "TURNOS_PRIVATE_PACKS_DIR=%s\nTURNOS_REQUIRED_PACKS=ar-b-pinamar\n" % self.private)
        _env.set_env(self, TURNOS_ENV_FILE=env_file, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(private_dir, "asset.bin"))
        self.assertIsNone(data_packs.verify_required_packs())

    def test_config_lock_keeps_process_environment_above_effective_file(self):
        locked_private = os.path.join(self.tmp, "locked-private")
        locked_dir = _pack(locked_private, asset=b"locked fixture")
        env_file = os.path.join(self.tmp, "boot.env")
        _write(env_file, "TURNOS_PRIVATE_PACKS_DIR=%s\nTURNOS_REQUIRED_PACKS=wrong-pack\n" % self.private)
        with patch.object(config, "_db_overrides", return_value={
            "TURNOS_PRIVATE_PACKS_DIR": self.private,
            "TURNOS_REQUIRED_PACKS": "wrong-pack",
        }):
            _env.set_env(self, TURNOS_CONFIG_LOCK="1", TURNOS_ENV_FILE=env_file,
                         TURNOS_PRIVATE_PACKS_DIR=locked_private,
                         TURNOS_REQUIRED_PACKS="ar-b-pinamar", PACK_CHAIN="ar-b-pinamar")
            config._reset_cache_para_tests()
            data_packs.invalidate()
            self.assertEqual(data_packs.resolve_path("asset.bin"),
                             os.path.join(locked_dir, "asset.bin"))
            self.assertIsNone(data_packs.verify_required_packs())

    def test_optional_malformed_private_pack_falls_back_to_public(self):
        public_dir = _pack(self.public, asset=b"public fixture")
        private_dir = _pack(self.private)
        _write(os.path.join(private_dir, "pack.json"), "{")
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.private, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))
        self.assertIsNone(data_packs.verify_required_packs())

    def test_optional_invalid_inventory_and_checksum_fall_back_to_public(self):
        public_dir = _pack(self.public, asset=b"public fixture")
        private_dir = _pack(self.private)
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.private, PACK_CHAIN="ar-b-pinamar")
        os.unlink(os.path.join(private_dir, "INVENTORY.json"))
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))
        _write(os.path.join(private_dir, "INVENTORY.json"), "{")
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))
        _inventory(private_dir)
        _write(os.path.join(private_dir, "asset.bin"), b"corrupt after inventory")
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))

    def test_optional_warm_cache_detects_same_size_rewrite_with_restored_mtime(self):
        public_dir = _pack(self.public, asset=b"public fixture")
        private_dir = _pack(self.private, asset=b"ABCD")
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.private, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(private_dir, "asset.bin"))
        asset = os.path.join(private_dir, "asset.bin")
        before = os.stat(asset)
        _write(asset, b"WXYZ")
        restore_mtime_after_ctime_moves(asset, before)
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))

    def test_verified_required_pack_does_not_rescan_inventory_per_request(self):
        public_dir = _pack(self.public, asset=b"public fallback")
        private_dir = _pack(self.private, asset=b"verified fixture")
        self._require_private()
        _env.set_env(self, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertIsNone(data_packs.verify_required_packs())

        with patch.object(data_packs, "_private_tree_stamp",
                          side_effect=AssertionError("request-time tree scan")):
            self.assertEqual(data_packs.resolve_path("asset.bin"),
                             os.path.join(private_dir, "asset.bin"))
            self.assertEqual(data_packs.resolve_path("asset.bin"),
                             os.path.join(private_dir, "asset.bin"))

        # If the process configuration stops requiring this pack, the old
        # boot admission cannot weaken optional-pack mutation detection.
        _env.set_env(self, TURNOS_REQUIRED_PACKS=None)
        asset = os.path.join(private_dir, "asset.bin")
        before = os.stat(asset)
        _write(asset, b"tampered fixture")
        restore_mtime_after_ctime_moves(asset, before)
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))

    def test_optional_wrong_id_root_and_symlink_escapes_fall_back_to_public(self):
        public_dir = _pack(self.public, asset=b"public fixture")
        private_dir = _pack(self.private)
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.private, PACK_CHAIN="ar-b-pinamar")
        _write(os.path.join(private_dir, "pack.json"), json.dumps({"id": "wrong"}))
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))

        outside = os.path.join(self.tmp, "optional-outside")
        _pack(outside)
        root_link = os.path.join(self.tmp, "optional-root-link")
        os.symlink(outside, root_link)
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=root_link)
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))

        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self.private)
        shutil.rmtree(self.private)
        os.makedirs(self.private)
        os.symlink(outside, os.path.join(self.private, "ar-b-pinamar"))
        data_packs.invalidate()
        self.assertEqual(data_packs.resolve_path("asset.bin"),
                         os.path.join(public_dir, "asset.bin"))

    def test_required_missing_directory_fails_closed(self):
        self._require_private()
        self._assert_refused("directory missing")

    def test_required_public_pack_is_valid_without_a_private_root(self):
        _pack(self.public, asset=b"self-contained fixture")
        _env.set_env(self, TURNOS_REQUIRED_PACKS="ar-b-pinamar")
        config._reset_cache_para_tests()
        self.assertIsNone(data_packs.verify_required_packs())

    def test_configured_private_required_pack_never_falls_back_to_public(self):
        _pack(self.public, asset=b"public fallback must not boot")
        self._require_private()
        self._assert_refused("directory missing")

    def test_required_malformed_and_wrong_identity_manifests_fail_closed(self):
        directory = _pack(self.private)
        self._require_private()
        _write(os.path.join(directory, "pack.json"), "{")
        self._assert_refused("manifest invalid")
        _write(os.path.join(directory, "pack.json"), json.dumps({"id": "other"}))
        self._assert_refused("manifest identity invalid")

    def test_required_absent_invalid_and_untruthful_inventory_fail_closed(self):
        directory = _pack(self.private)
        self._require_private()
        os.unlink(os.path.join(directory, "INVENTORY.json"))
        self._assert_refused("inventory invalid")
        _write(os.path.join(directory, "INVENTORY.json"), json.dumps({"files": []}))
        self._assert_refused("inventory invalid")
        _inventory(directory)
        with open(os.path.join(directory, "INVENTORY.json"), encoding="utf-8") as f:
            doc = json.load(f)
        doc["files"].append(dict(doc["files"][0]))
        _write(os.path.join(directory, "INVENTORY.json"), json.dumps(doc))
        self._assert_refused("inventory invalid")

    def test_required_checksum_mismatch_fails_closed(self):
        directory = _pack(self.private)
        self._require_private()
        _write(os.path.join(directory, "asset.bin"), b"changed after inventory")
        self._assert_refused("inventory checksum mismatch")

    def test_required_root_and_pack_symlink_escapes_fail_closed(self):
        outside = os.path.join(self.tmp, "outside-secret-fixture")
        _pack(outside)
        root_link = os.path.join(self.tmp, "private-root-link")
        os.symlink(outside, root_link)
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=root_link, TURNOS_REQUIRED_PACKS="ar-b-pinamar")
        config._reset_cache_para_tests()
        self._assert_refused("private root invalid")

        shutil.rmtree(self.private)
        os.makedirs(self.private)
        os.symlink(outside, os.path.join(self.private, "ar-b-pinamar"))
        self._require_private()
        self._assert_refused("directory outside allowed root")

    def test_required_file_symlink_fails_closed(self):
        directory = _pack(self.private)
        outside = os.path.join(self.tmp, "outside-asset")
        _write(outside, b"outside")
        os.unlink(os.path.join(directory, "asset.bin"))
        os.symlink(outside, os.path.join(directory, "asset.bin"))
        self._require_private()
        self._assert_refused("pack file invalid")

    def test_private_dirfd_descent_rejects_a_directory_swapped_after_realpath(self):
        directory = _pack(self.private)
        nested = os.path.join(directory, "nested")
        _write(os.path.join(nested, "asset.bin"), b"inside")
        target_before_swap = os.path.realpath(os.path.join(nested, "asset.bin"))
        outside = os.path.join(self.tmp, "outside-after-realpath")
        _write(os.path.join(outside, "asset.bin"), b"outside")
        os.rename(nested, nested + "-original")
        os.symlink(outside, nested)
        # The path was verified before the directory changed. A conventional
        # absolute open would now follow the symlink; dirfd + O_NOFOLLOW does
        # not, and this uses a real filesystem replacement, not a mock.
        with self.assertRaises(OSError):
            static_assets.open_verified_private_file(
                os.path.realpath(self.private), target_before_swap
            )

    def test_required_identifier_escape_fails_closed(self):
        _env.set_env(self, TURNOS_REQUIRED_PACKS="../../outside-secret-fixture")
        config._reset_cache_para_tests()
        self._assert_refused("identifier invalid")

    def test_asgi_required_pack_failure_exits_before_any_public_listener(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        public_port = probe.getsockname()[1]
        probe.close()
        empty_private = os.path.join(self.tmp, "asgi-empty-private")
        os.makedirs(empty_private)
        env = dict(os.environ)
        env.update({
            "TURNOS_ASGI": "1",
            "HOST": "127.0.0.1",
            "PORT": str(public_port),
            "TURNOS_PRIVATE_PACKS_DIR": empty_private,
            "TURNOS_REQUIRED_PACKS": "ar-b-pinamar",
            "TURNOS_ENV_FILE": os.path.join(self.tmp, "missing.env"),
            "DATABASE_URL": "",
            "TURNOS_FAKE_AI": "1",
            "TURNOS_SKIP_HSI": "1",
        })
        completed = subprocess.run(
            [sys.executable, "asgi_main.py"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("directory missing", completed.stderr + completed.stdout)
        with socket.socket() as client:
            client.settimeout(0.5)
            self.assertNotEqual(client.connect_ex(("127.0.0.1", public_port)), 0)

    def test_asgi_preflight_refuses_before_worker_supervision(self):
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            with patch.object(asgi_main, "_verify_required_packs",
                              side_effect=data_packs.RequiredPackError("blocked")):
                with patch.object(asgi_main, "_start_queue_worker_supervisor") as worker:
                    with patch.object(asgi_main, "_asgi_mode") as asgi:
                        with self.assertRaisesRegex(data_packs.RequiredPackError, "blocked"):
                            asgi_main.main()
        worker.assert_not_called()
        asgi.assert_not_called()

    def test_asgi_default_path_starts_one_native_listener_without_server_import(self):
        fake_uvicorn = types.SimpleNamespace(run=lambda *_, **__: None)
        fake_app = types.SimpleNamespace(app=object())

        with patch.dict(sys.modules, {"uvicorn": fake_uvicorn, "app.main": fake_app}):
            with patch.object(asgi_main, "_verify_required_packs") as verify:
                with patch.object(fake_uvicorn, "run", wraps=fake_uvicorn.run) as run:
                    asgi_main._asgi_mode()
        verify.assert_called_once_with()
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertIs(args[0], fake_app.app)
        self.assertEqual(kwargs["port"], int(os.environ.get("PORT", "7700")))
        with open(asgi_main.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        asgi_source = source[source.index("def _asgi_mode"):source.index("def main")]
        self.assertNotIn("import server", asgi_source)
        self.assertNotIn("7701", asgi_source)
        self.assertNotIn("legacy-server", asgi_source)

    def test_verified_private_assets_serve_and_escape_attempts_are_denied(self):
        private_dir = _pack(self.private, asset=b"served-private-asset")
        outside = os.path.join(self.tmp, "outside-asset")
        _write(outside, b"must-not-serve")
        self._require_private()
        _env.set_env(self, PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        with LiveServer() as base_url:
            port = int(base_url.rsplit(":", 1)[1])
            def get(path):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request("GET", path)
                    response = conn.getresponse()
                    return response.status, response.read()
                finally:
                    conn.close()

            self.assertEqual(get("/webdata/asset.bin"), (200, b"served-private-asset"))
            self.assertEqual(get("/data/asset.bin"), (200, b"served-private-asset"))
            # After the valid private pack is admitted, a later malicious
            # asset symlink must still be denied by request-time containment.
            os.symlink(outside, os.path.join(private_dir, "escape.bin"))
            for path in ("/webdata/../outside-asset", "/data/../outside-asset",
                         "/webdata/escape.bin", "/data/escape.bin"):
                with self.subTest(path=path):
                    self.assertEqual(get(path)[0], 403)

    def test_beneath_checkout_private_root_survives_post_realpath_swap(self):
        # The race the dirfd descent exists for: a verified private root under
        # the checkout, where a directory is swapped to an outside symlink
        # AFTER every realpath/isfile/containment check has already passed.
        # Deterministic — the swap fires from inside the descent seam itself,
        # not by timing.  Proven through BOTH URL aliases.
        #
        # ``nested/asset.bin`` is part of the frozen INVENTORY from the start,
        # so the private pack stays ready for resolution; ``fresh_nested``
        # rewrites it with identical bytes so the checksummed inventory still
        # matches after each rebuild.
        private_root = tempfile.mkdtemp(dir=static_assets.APP_ROOT, prefix=".tier6-beneath-")
        self.addCleanup(shutil.rmtree, private_root, True)
        private_real = os.path.realpath(private_root)
        self.assertTrue(private_real.startswith(os.path.realpath(static_assets.APP_ROOT) + os.sep))
        directory = os.path.join(private_root, "ar-b-pinamar")
        nested = os.path.join(directory, "nested")
        _write(os.path.join(directory, "pack.json"), json.dumps({"id": "ar-b-pinamar"}))
        _write(os.path.join(nested, "asset.bin"), b"inside")
        _inventory(directory)
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=private_root,
                     TURNOS_REQUIRED_PACKS="ar-b-pinamar", PACK_CHAIN="ar-b-pinamar")
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertIsNone(data_packs.verify_required_packs())

        outside = os.path.join(self.tmp, "post-realpath-outside")
        _write(os.path.join(outside, "asset.bin"), b"escaped-secret")

        def fresh_nested():
            # Rebuild the trusted tree the realpath/containment checks see
            # before each probe, with identical bytes so the checksummed
            # inventory still matches.  os.unlink the symlink never touches
            # the outside target; rmtree would follow it.
            if os.path.islink(nested):
                os.unlink(nested)
            elif os.path.isdir(nested):
                shutil.rmtree(nested)
            _write(os.path.join(nested, "asset.bin"), b"inside")

        original = static_assets._open_regular_beneath
        engaged = []
        swap_on_next = {"enabled": False}

        def instrumented_descent(root, relative):
            # The descent is engaged only in the private branch, so reaching
            # here proves the request did not take the public stdlib branch.
            engaged.append(root)
            if swap_on_next["enabled"] and os.path.isdir(nested):
                shutil.rmtree(nested)
                os.symlink(outside, nested)
            return original(root, relative)

        with patch.object(
            static_assets, "_open_regular_beneath", instrumented_descent
        ):
            with LiveServer() as base_url:
                port = int(base_url.rsplit(":", 1)[1])

                def get(path):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    try:
                        conn.request("GET", path)
                        response = conn.getresponse()
                        return response.status, response.read()
                    finally:
                        conn.close()

                for alias in ("/webdata/nested/asset.bin",
                              "/data/nested/asset.bin"):
                    # Control: the same path resolves and serves through the
                    # descent with no swap, so the deny below is not incidental.
                    fresh_nested()
                    engaged.clear()
                    swap_on_next["enabled"] = False
                    status, body = get(alias)
                    with self.subTest(alias=alias, phase="control"):
                        self.assertEqual(status, 200)
                        self.assertEqual(body, b"inside")
                        self.assertEqual(engaged, [private_real])
                    # Treatment: the trusted directory is swapped to a symlink
                    # to an outside location after realpath, before the open.
                    fresh_nested()
                    engaged.clear()
                    swap_on_next["enabled"] = True
                    status, body = get(alias)
                    with self.subTest(alias=alias, phase="swap"):
                        # Engaged = this is the private dirfd branch, and the
                        # descent still ran; O_NOFOLLOW refused the swapped
                        # component, so it fails closed instead of escaping.
                        self.assertEqual(engaged, [private_real])
                        self.assertEqual(status, 403)
                        self.assertNotIn(b"escaped-secret", body)

    def test_public_static_serving_is_unchanged_without_a_private_root(self):
        # No TURNOS_PRIVATE_PACKS_DIR: the private dirfd descent must not be
        # engaged at all.  A public-pack target serves through the exact
        # stdlib path SimpleHTTPRequestHandler always used, via BOTH aliases.
        public_root = tempfile.mkdtemp(dir=static_assets.APP_ROOT, prefix=".tier6-public-")
        self.addCleanup(shutil.rmtree, public_root, True)
        _pack(public_root, asset=b"public-static-asset")
        # setUp pointed _PACKS_DIR at a temp dir OUTSIDE the checkout; re-point
        # it at the beneath-checkout fixture so the else branch's relpath and
        # SimpleHTTPRequestHandler stay inside ROOT and serve the real bytes.
        engaged = []
        original = static_assets.open_verified_private_file

        def spy(root, target):
            engaged.append(True)
            return original(root, target)

        with patch.object(data_packs, "_PACKS_DIR", public_root):
            with patch.object(static_assets, "open_verified_private_file", spy):
                _env.set_env(self, PACK_CHAIN="ar-b-pinamar")
                config._reset_cache_para_tests()
                data_packs.invalidate()
                self.assertIsNone(data_packs.private_packs_dir())
                with LiveServer() as base_url:
                    port = int(base_url.rsplit(":", 1)[1])

                    def get(path):
                        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        try:
                            conn.request("GET", path)
                            response = conn.getresponse()
                            return response.status, response.read()
                        finally:
                            conn.close()

                    for alias in ("/webdata/asset.bin", "/data/asset.bin"):
                        with self.subTest(alias=alias):
                            status, body = get(alias)
                            self.assertEqual(status, 200)
                            self.assertEqual(body, b"public-static-asset")
                    # The new private-serving code path never runs for a
                    # public target — ordinary static behavior is unchanged.
                    self.assertEqual(engaged, [])


if __name__ == "__main__":
    unittest.main()
