"""Hermetic contracts for the offline AI-profile conformance harness."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _env


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("bench_ai_profiles", ROOT / "scripts/bench_ai_profiles.py")
bench = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bench)


def _corpus(root: Path) -> Path:
    corpus = root / "corpus"
    corpus.mkdir()
    assets, cases = [], []
    verdicts = (["parece_correcta"] * 11 + ["dudosa", "dudosa", "dudosa", "documento_equivocado"])
    for number, verdict in enumerate(verdicts):
        name = f"fixture-{number:02d}.jpg"
        payload = b"\xff\xd8\xff" + bytes([number]) + b"offline"
        (corpus / name).write_bytes(payload)
        assets.append({
            "path": name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
            "media_type": "image/jpeg", "provenance": {
                "classification": "synthetic", "source": "from_scratch",
                "from_scratch": True, "no_real": True, "no_derived": True,
            }, "review": {"attested": True, "reviewer_id": "hr-a9d37c5e10b84f62", "date": "2026-08-19"},
        })
        jurisdiction = "foreign" if number == 13 else ("not_applicable" if verdict == "documento_equivocado" else "local")
        cases.append({"path": name, "expected_verdict": verdict,
                      "document_kind": "non_medical" if verdict == "documento_equivocado" else "medical_referral",
                      "jurisdiction": jurisdiction, "expected_fields": []})
    (corpus / "assets.json").write_text(json.dumps({"manifest_version": "1.0", "assets": assets}), encoding="utf-8")
    (corpus / "golden.json").write_text(json.dumps({"schema_version": "1.0", "cases": cases}), encoding="utf-8")
    return corpus


@contextlib.contextmanager
def _config_caches_owned_by_this_thread():
    """Keep OTHER threads from republishing core.config's two caches.

    Daemon threads an earlier module's app lifespan left running (waitlist,
    warmups) read the config at any moment. While a test holds sentinel values
    in os.environ, such a read re-caches `.env` under the sentinel
    TURNOS_ENV_FILE ("before-9") or refreshes the 3-second DB overlay, and the
    snapshot comparison fails although the benchmark restored everything it
    touched. Other threads get the values without the cache write: `{}` for the
    env file (the sentinel path does not exist and the benchmark's own is
    /dev/null — both read as empty) and the overlay currently cached."""
    from app.core import config

    me = threading.get_ident()
    real_dotenv, real_db_overrides = config._dotenv, config._db_overrides

    def dotenv():
        return real_dotenv() if threading.get_ident() == me else {}

    def db_overrides():
        if threading.get_ident() == me:
            return real_db_overrides()
        return dict(config._db_cache["v"] or {})

    with mock.patch.object(config, "_dotenv", dotenv), \
            mock.patch.object(config, "_db_overrides", db_overrides):
        yield


class OfflineAiProfileBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.corpus = _corpus(Path(self.tempdir.name))
        self.responses = ROOT / "packs/example-town/ordenes-demo/offline-profile-responses.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_profiles_route_real_callers_and_score_all_fixtures(self):
        rows = bench.run_benchmark(self.corpus, self.responses)
        self.assertEqual([row["profile"] for row in rows], list(bench.PROFILE_NAMES))
        for row in rows:
            self.assertTrue(row["all_ok"])
            self.assertEqual((row["referral_passed"], row["referral_total"]), (15, 15))
            self.assertEqual(row["call_count"], 18)
            self.assertTrue(row["routing_ok"])
            self.assertTrue(row["chat_tool_turn"])
            self.assertTrue(row["translation"])
            self.assertTrue(row["dni_ocr"])
            self.assertIsNone(row["provider_latency_ms"])
            self.assertIsNone(row["provider_cost_usd"])

    def test_profile_destination_contracts_remain_distinct(self):
        from app.ai import ai_client
        for profile_name in bench.PROFILE_NAMES:
            transport = bench._FakeTransport(profile_name, bench.load_responses(self.responses))
            with bench._isolated_profile(profile_name, transport):
                self.assertEqual(ai_client.profile(), profile_name)
                self.assertTrue(ai_client.available())
                self.assertEqual(os.environ["TURNOS_CONFIG_LOCK"], "1")
                self.assertEqual(os.environ["TURNOS_ENV_FILE"], "/dev/null")
                destination = ai_client.destination()
                if profile_name == "cloud_shared":
                    self.assertEqual(destination, ai_client.OPENROUTER_URL)
                elif profile_name == "hosted_jurisdiction":
                    self.assertEqual(destination, "https://offline.invalid/v1/chat/completions")
                else:
                    self.assertEqual(destination, "http://127.0.0.1:9/v1/chat/completions")

    def test_workload_payload_shapes_cover_all_four_real_callers(self):
        responses = bench.load_responses(self.responses)
        transport = bench._FakeTransport("cloud_shared", responses)
        with bench._isolated_profile("cloud_shared", transport):
            outcome = bench._run_workloads(bench.load_verified_corpus(self.corpus), responses, transport)
        self.assertEqual(outcome["referral_total"], 15)
        self.assertTrue(outcome["payload_shapes_ok"])
        self.assertEqual(outcome["payload_shapes"], ["chat_tool_turn", "dni_ocr", "referral", "translation"])
        self.assertEqual(len(transport.routes), 18)
        self.assertTrue(transport.routing_ok())

    def test_bad_manifest_fails_before_any_jpeg_is_opened(self):
        manifest = json.loads((self.corpus / "assets.json").read_text(encoding="utf-8"))
        manifest["assets"][0]["path"] = "../escape.jpg"
        (self.corpus / "assets.json").write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("JPEG must not open")):
            with self.assertRaises(bench.CorpusError):
                bench.load_verified_corpus(self.corpus)

    def test_missing_fixture_fails_closed(self):
        (self.corpus / "fixture-00.jpg").unlink()
        with self.assertRaises(bench.CorpusError):
            bench.load_verified_corpus(self.corpus)

    def test_socket_use_is_denied(self):
        with bench._deny_sockets():
            with self.assertRaises(bench.SocketDenied):
                socket.socket()
            with self.assertRaises(bench.SocketDenied):
                socket.create_connection(("127.0.0.1", 9))

    def test_globals_restore_after_workload_failure(self):
        from app.ai import ai_client
        from app.core import config

        original_post = ai_client._post
        original_env_path = config.ENV_PATH
        original_active = dict(ai_client._active)
        original_socket = socket.socket
        original_connection = socket.create_connection
        original_getaddrinfo = socket.getaddrinfo
        original_dotenv_cache = dict(config._dotenv_cache)
        original_db_cache = dict(config._db_cache)
        sentinels = {key: f"before-{number}" for number, key in enumerate(bench.ENV_KEYS)}
        with _config_caches_owned_by_this_thread(), _env.patched_env(sentinels):
            with mock.patch.object(bench, "_run_workloads", side_effect=RuntimeError("forced failure")):
                with self.assertRaises(RuntimeError):
                    bench.run_benchmark(self.corpus, self.responses)
            self.assertIs(ai_client._post, original_post)
            self.assertEqual(config.ENV_PATH, original_env_path)
            self.assertEqual(ai_client._active, original_active)
            self.assertIs(socket.socket, original_socket)
            self.assertIs(socket.create_connection, original_connection)
            self.assertIs(socket.getaddrinfo, original_getaddrinfo)
            self.assertEqual(config._dotenv_cache, original_dotenv_cache)
            self.assertEqual(config._db_cache, original_db_cache)
            self.assertEqual({key: os.environ.get(key) for key in bench.ENV_KEYS}, sentinels)

    def test_config_lock_and_chat_seams_make_zero_db_or_catalog_reads(self):
        from app.ai import prompts
        from app.core import config
        from app.core import db_chat
        from app.core import db_catalog
        from app.core import db_ai
        from app.patient_web import catalog

        # The benchmark is single-threaded: count only ITS calls. The patches
        # are process-wide, and daemon threads an earlier module's app
        # lifespan left running (waitlist, warmups) read the catalog too.
        me = threading.get_ident()
        calls = {}

        def watch(owner, name):
            real = getattr(owner, name)

            def spy(*args, **kwargs):
                if threading.get_ident() == me:
                    calls[name] = calls.get(name, 0) + 1
                return real(*args, **kwargs)
            return mock.patch.object(owner, name, side_effect=spy)

        watched = [(config, "_db_overrides"), (prompts, "_overrides"), (db_catalog, "catalog_get"),
                   (db_ai, "prompts_all"), (db_chat, "chat_history_multi"), (db_chat, "chat_save_message"),
                   (db_chat, "chat_count_user_messages"), (db_chat, "chat_first_user_message"),
                   (catalog, "specialties"), (catalog, "centers")]
        with contextlib.ExitStack() as stack:
            for owner, name in watched:
                stack.enter_context(watch(owner, name))
            rows = bench.run_benchmark(self.corpus, self.responses)
        self.assertTrue(all(row["all_ok"] for row in rows))
        self.assertEqual(calls, {})

    def test_response_fixture_validation_is_strict_and_rejects_empty_chat_args(self):
        original = json.loads(self.responses.read_text(encoding="utf-8"))
        mutations = [
            lambda doc: doc.pop("translation"),
            lambda doc: doc.__setitem__("translation", ""),
            lambda doc: doc["chat_tool_turn"]["function"].__setitem__("arguments", "{}"),
            lambda doc: doc["dni_ocr"].__setitem__("foto", {"x": 0.9, "y": 0.9, "w": 0.4, "h": 0.4}),
            lambda doc: doc["referral_templates"]["matching"].pop("dni"),
        ]
        for number, mutate in enumerate(mutations):
            doc = json.loads(json.dumps(original))
            mutate(doc)
            path = Path(self.tempdir.name) / f"bad-responses-{number}.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.subTest(number=number), self.assertRaises(bench.CorpusError):
                bench.load_responses(path)

    def test_invalid_request_shape_fails_before_category_is_recorded(self):
        transport = bench._FakeTransport("cloud_shared", bench.load_responses(self.responses))
        with self.assertRaises(AssertionError):
            transport.post({"messages": [], "tools": [{}]}, bench.PLACEHOLDER_KEY, 1,
                           base_url=bench.PROFILE_DESTINATIONS["cloud_shared"])
        self.assertEqual(transport.payload_shapes, [])

    def test_cli_table_and_json_have_exactly_three_profile_rows(self):
        table_out, table_err = io.StringIO(), io.StringIO()
        status = bench.main(["--corpus-root", str(self.corpus), "--responses", str(self.responses), "--format", "table"], table_out, table_err)
        self.assertEqual(status, 0, table_err.getvalue())
        self.assertEqual(len(table_out.getvalue().strip().splitlines()), 4)  # header + three profiles

        json_out, json_err = io.StringIO(), io.StringIO()
        status = bench.main(["--corpus-root", str(self.corpus), "--responses", str(self.responses), "--format", "json"], json_out, json_err)
        self.assertEqual(status, 0, json_err.getvalue())
        self.assertEqual(len(json.loads(json_out.getvalue())), 3)


if __name__ == "__main__":
    unittest.main()
