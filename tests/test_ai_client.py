"""test_ai_client.py — backup-key failover for the shared OpenRouter caller
(ai_client.py, Fase 4 del rework — ítem 6 del responsable del producto).

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_ai_client.py"
"""

import io
import os
import sys
import unittest
import urllib.error
import urllib.request
from email.message import Message
from urllib.response import addinfourl
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.ai import ai_client  # noqa: E402
from app.core import config  # noqa: E402


_minted = []


def _http_error(code):
    """An HTTPError is file-like: unclosed it surfaces as ignored-deallocator
    shutdown noise under `-W error::ResourceWarning` (Python 3.14). The ones
    raised here into propagating paths end up swallowed by assertRaises, so
    the factory tracks every instance and tearDown closes them (idempotent —
    ai_client's own failover close is fine to repeat)."""
    err = urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(b""))
    _minted.append(err)
    return err


class AiClientTestCase(unittest.TestCase):
    def setUp(self):
        # Registered before set_env: cleanups run last-in first-out, so the sticky key is reset after the environment is back.
        self.addCleanup(ai_client.reset_sticky_for_tests)
        _env.set_env(self, OPENROUTER_API_KEY="sk-primary", OPENROUTER_API_KEY_BACKUP="sk-backup")
        ai_client.reset_sticky_for_tests()

    def tearDown(self):
        while _minted:
            _minted.pop().close()

    # ---- 402/403 fail over to the backup key -----------------------------

    def test_402_falla_al_respaldo(self):
        calls = []

        def fake_post(body, api_key, timeout, headers_extra=None, base_url=None):
            calls.append(api_key)
            if api_key == "sk-primary":
                raise _http_error(402)
            return {"ok": True, "via": api_key}

        with patch.object(ai_client, "_post", side_effect=fake_post):
            data = ai_client.call({"model": "x"})
        self.assertEqual(data, {"ok": True, "via": "sk-backup"})
        self.assertEqual(calls, ["sk-primary", "sk-backup"])
        self.assertEqual(ai_client.active_key_kind(), "backup")

    def test_403_falla_al_respaldo(self):
        def fake_post(body, api_key, timeout, headers_extra=None, base_url=None):
            if api_key == "sk-primary":
                raise _http_error(403)
            return {"ok": True}

        with patch.object(ai_client, "_post", side_effect=fake_post):
            data = ai_client.call({"model": "x"})
        self.assertEqual(data, {"ok": True})
        self.assertEqual(ai_client.active_key_kind(), "backup")

    # ---- a broader outage (5xx/429/timeouts) is NOT a key problem --------

    def test_500_no_reintenta_con_otra_key(self):
        calls = []

        def fake_post(body, api_key, timeout, headers_extra=None, base_url=None):
            calls.append(api_key)
            raise _http_error(500)

        with patch.object(ai_client, "_post", side_effect=fake_post):
            with self.assertRaises(urllib.error.HTTPError):
                ai_client.call({"model": "x"})
        self.assertEqual(calls, ["sk-primary"])  # un solo intento, no dos

    def test_429_no_reintenta_con_otra_key(self):
        def fake_post(body, api_key, timeout, headers_extra=None, base_url=None):
            raise _http_error(429)

        with patch.object(ai_client, "_post", side_effect=fake_post):
            with self.assertRaises(urllib.error.HTTPError):
                ai_client.call({"model": "x"})

    # ---- sin backup configurado: 402 simplemente falla --------------------

    def test_402_sin_respaldo_configurado_propaga(self):
        _env.set_env(self, OPENROUTER_API_KEY_BACKUP=None)

        def fake_post(body, api_key, timeout, headers_extra=None, base_url=None):
            raise _http_error(402)

        with patch.object(ai_client, "_post", side_effect=fake_post):
            with self.assertRaises(urllib.error.HTTPError):
                ai_client.call({"model": "x"})

    # ---- sticky: una vez activo el respaldo, no se vuelve a pegar a la ----
    # ---- key muerta en cada llamada hasta que pasen ~5 min. --------------

    def test_key_pegajosa_no_reintenta_la_primaria_muerta(self):
        def fake_post_first(body, api_key, timeout, headers_extra=None, base_url=None):
            if api_key == "sk-primary":
                raise _http_error(402)
            return {"ok": True}

        with patch.object(ai_client, "_post", side_effect=fake_post_first):
            ai_client.call({"model": "x"})  # primary falla, respaldo activo

        calls = []

        def fake_post_second(body, api_key, timeout, headers_extra=None, base_url=None):
            calls.append(api_key)
            return {"ok": True}

        with patch.object(ai_client, "_post", side_effect=fake_post_second):
            ai_client.call({"model": "x"})
        # va DIRECTO al respaldo, ni siquiera intenta la primaria caída.
        self.assertEqual(calls, ["sk-backup"])

    def test_sticky_expira_reintenta_primaria(self):
        def fake_post_first(body, api_key, timeout, headers_extra=None, base_url=None):
            if api_key == "sk-primary":
                raise _http_error(402)
            return {"ok": True}

        with patch.object(ai_client, "_post", side_effect=fake_post_first):
            ai_client.call({"model": "x"})
        self.assertEqual(ai_client.active_key_kind(), "backup")

        # simulate the TTL elapsing
        ai_client._active["since"] = 0.0

        calls = []

        def fake_post_second(body, api_key, timeout, headers_extra=None, base_url=None):
            calls.append(api_key)
            return {"ok": True}

        with patch.object(ai_client, "_post", side_effect=fake_post_second):
            ai_client.call({"model": "x"})
        self.assertEqual(calls, ["sk-primary"])  # la stickiness venció, reintenta

    # ---- active_key_kind() sin llamadas -----------------------------------

    def test_active_key_kind_sin_backup(self):
        _env.set_env(self, OPENROUTER_API_KEY_BACKUP=None)
        self.assertEqual(ai_client.active_key_kind(), "primary")

    def test_active_key_kind_sin_ninguna_key(self):
        _env.set_env(self, OPENROUTER_API_KEY=None, OPENROUTER_API_KEY_BACKUP=None)
        self.assertEqual(ai_client.active_key_kind(), "none")

    def test_active_key_kind_solo_backup(self):
        _env.set_env(self, OPENROUTER_API_KEY=None)
        self.assertEqual(ai_client.active_key_kind(), "backup")


class AiInferenceProfileTestCase(unittest.TestCase):
    _KEYS = (
        "OPENROUTER_API_KEY", "OPENROUTER_API_KEY_BACKUP",
        "AI_INFERENCE_PROFILE", "AI_INFERENCE_BASE_URL",
        "AI_INFERENCE_API_KEY", "AI_INFERENCE_API_KEY_BACKUP",
    )

    def setUp(self):
        # Cleanups run last-in first-out: the environment is put back first, then the patch and ENV_PATH,
        # and the config cache and the sticky key are dropped last.
        self.addCleanup(ai_client.reset_sticky_for_tests)
        self.addCleanup(config._reset_cache_para_tests)
        self.addCleanup(setattr, config, "ENV_PATH", config.ENV_PATH)
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        self._db = patch.object(config, "_db_overrides", return_value={})
        self._db.start()
        self.addCleanup(self._db.stop)
        _env.set_env(self, dict.fromkeys(self._KEYS))
        ai_client.reset_sticky_for_tests()

    def tearDown(self):
        while _minted:
            _minted.pop().close()

    def test_default_cloud_is_byte_compatible_and_uses_legacy_keys(self):
        _env.set_env(self, OPENROUTER_API_KEY="legacy-primary", OPENROUTER_API_KEY_BACKUP="legacy-backup")
        self.assertEqual(ai_client.profile(), "cloud_shared")
        self.assertEqual(ai_client.destination(), ai_client.OPENROUTER_URL)
        self.assertEqual(ai_client.primary_key(), "legacy-primary")
        self.assertEqual(ai_client.backup_key(), "legacy-backup")
        self.assertTrue(ai_client.available())

    def test_cloud_prefers_dedicated_keys_without_changing_destination(self):
        _env.set_env(self, {
            "OPENROUTER_API_KEY": "legacy-primary",
            "OPENROUTER_API_KEY_BACKUP": "legacy-backup",
            "AI_INFERENCE_API_KEY": "dedicated-primary",
            "AI_INFERENCE_API_KEY_BACKUP": "dedicated-backup",
        })
        self.assertEqual(ai_client.destination(), ai_client.OPENROUTER_URL)
        self.assertEqual(ai_client.primary_key(), "dedicated-primary")
        self.assertEqual(ai_client.backup_key(), "dedicated-backup")

    def test_non_cloud_never_falls_back_to_openrouter_keys(self):
        _env.set_env(self, {
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": "https://inference.example/v1/chat/completions",
            "OPENROUTER_API_KEY": "legacy-primary",
            "OPENROUTER_API_KEY_BACKUP": "legacy-backup",
        })
        self.assertEqual(ai_client.primary_key(), "")
        self.assertEqual(ai_client.backup_key(), "")
        self.assertFalse(ai_client.available())
        _env.set_env(self, {"AI_INFERENCE_API_KEY": "dedicated-primary"})
        self.assertTrue(ai_client.available())

    def test_non_cloud_availability_never_reads_openrouter_config(self):
        values = {
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": "https://inference.example/v1/chat/completions",
            "AI_INFERENCE_API_KEY": "dedicated-primary",
            "AI_INFERENCE_API_KEY_BACKUP": "",
        }
        reads = []

        def get(key, default=None, legacy=None):
            reads.append(key)
            return values.get(key, default)

        with patch.object(ai_client.config, "get", side_effect=get):
            self.assertTrue(ai_client.available())
        self.assertFalse(any(key.startswith("OPENROUTER_") for key in reads))

    def test_non_cloud_ignores_an_explicit_legacy_key_override(self):
        _env.set_env(self, {
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": "https://inference.example/v1/chat/completions",
            "AI_INFERENCE_API_KEY": "dedicated-primary",
        })
        with patch.object(ai_client, "_post", return_value={"ok": True}) as post:
            ai_client.call({"model": "x"}, api_key="legacy-key")
        self.assertEqual(post.call_args.args[1], "dedicated-primary")

    def test_profile_url_policy_rejects_unsafe_and_ambiguous_destinations(self):
        self.assertIsNone(ai_client.validate_destination(
            "hosted_jurisdiction", "https://inference.example/v1/chat/completions"))
        for url in (
            "http://inference.example/v1/chat/completions",
            "https://key@example.test/v1/chat/completions",
            "https://inference.example/v1/chat/completions?target=other",
            "https://inference.example/v1/chat/completions#other",
            "https://inference.example/",
        ):
            self.assertIsNotNone(ai_client.validate_destination("hosted_jurisdiction", url), url)
        self.assertIsNone(ai_client.validate_destination(
            "self_hosted_vllm", "http://127.0.0.1:8000/v1/chat/completions"))
        self.assertIsNone(ai_client.validate_destination(
            "self_hosted_vllm", "http://192.168.10.2/v1/chat/completions"))
        self.assertIsNotNone(ai_client.validate_destination(
            "self_hosted_vllm", "http://203.0.113.9/v1/chat/completions"))
        self.assertIsNotNone(ai_client.validate_destination(
            "self_hosted_vllm", "http://vllm.internal/v1/chat/completions"))

    def test_redirects_are_refused_at_the_configured_destination(self):
        _env.set_env(self, OPENROUTER_API_KEY="legacy-primary")
        redirect_handler = ai_client._RefuseRedirects()
        request = urllib.request.Request(ai_client.OPENROUTER_URL)
        headers = Message()
        headers["Location"] = "https://redirected.example/v1/chat/completions"
        response = addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
        # This is urllib's actual redirect chain, driven with a synthetic
        # 302 response: the custom handler declines the Location and the
        # default error handler raises instead of following it.
        with self.assertRaises(urllib.error.HTTPError) as redirected:
            urllib.request.build_opener(redirect_handler).error(
                "https", request, response, 302, "Found", headers)
        self.assertEqual(redirected.exception.code, 302)
        redirected.exception.close()

        opener = MagicMock()
        reqs = []

        def redirect(req, timeout):
            reqs.append((req.full_url, req.data))
            raise _http_error(302)

        opener.open.side_effect = redirect
        with patch("urllib.request.build_opener", return_value=opener) as build:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                ai_client._post({"model": "x", "messages": []}, "legacy-primary", 5)
        self.assertEqual(raised.exception.code, 302)
        self.assertEqual(reqs, [(ai_client.OPENROUTER_URL, b'{"model": "x", "messages": []}')])
        self.assertIsInstance(build.call_args.args[0], ai_client._RefuseRedirects)

    def test_call_keeps_one_destination_and_key_snapshot_through_failover(self):
        hosted = "https://inference.example/v1/chat/completions"
        _env.set_env(self, {
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": hosted,
            "AI_INFERENCE_API_KEY": "dedicated-primary",
            "AI_INFERENCE_API_KEY_BACKUP": "dedicated-backup",
        })
        calls = []

        def fake_post(body, api_key, timeout, headers_extra=None, base_url=None):
            calls.append((api_key, base_url))
            if api_key == "dedicated-primary":
                # A concurrent settings save must not redirect this retry or
                # the already-selected dedicated backup toward OpenRouter.
                _env.set_env(self, {
                    "AI_INFERENCE_PROFILE": "cloud_shared",
                    "OPENROUTER_API_KEY": "legacy-primary",
                })
                raise _http_error(402)
            return {"ok": True}

        with patch.object(ai_client, "_post", side_effect=fake_post):
            self.assertEqual(ai_client.call({"model": "x"}), {"ok": True})
        self.assertEqual(calls, [("dedicated-primary", hosted), ("dedicated-backup", hosted)])


if __name__ == "__main__":
    unittest.main()
