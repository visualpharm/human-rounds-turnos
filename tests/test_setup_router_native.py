"""Native HTTP contract tests for ``ops/setup_router.py``.

``ops.setup`` owns the canonical page/process functions and
``ops.setup_router`` is their sole HTTP transport. This file proves the
    TRANSPORT wiring (cookie read/write through a real `Request`/`Response`,
    the fresh-install 404 gate, method dispatch) preserves the canonical
    page/process contracts, plus the one behavior the campaign brief calls
    out explicitly: `/setup` is unreachable/404 on a configured installation.

No live DB: reuses `test_setup.py`'s `_FakeDB`/`_patched_db` fixture.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_setup_router_native.py"
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TEST_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_SETUP_SKIP_GEOIP="1")
_env.process_defaults(DATABASE_URL="")

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.ops import setup  # noqa: E402
from test_setup import _FakeDB, _patched_db  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402


class SetupRouterTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        setup._reset_cache_para_tests()
        self.fake = _FakeDB()
        self._db_patch = _patched_db(self.fake)
        self._db_patch.__enter__()
        self._runtime_patch = patch("app.ops.web_runtime.start_background_services")
        self._runtime_patch.start()
        self.addCleanup(self._runtime_patch.stop)
        self.client = TestClient(app_module.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self._db_patch.__exit__(None, None, None)
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()
        setup._reset_cache_para_tests()


class TestFreshInstallGate(SetupRouterTestCase):
    """``/setup`` stays unreachable on a configured installation."""

    def test_configured_install_404s_setup_with_canonical_body(self):
        self.fake.operator_create("someone-else", pass_hash="x")

        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.content,
            b"<!doctype html><title>404</title><p>No encontrado.</p>",
        )
        self.assertEqual(resp.headers["content-type"], "text/html; charset=utf-8")

    def test_configured_install_404s_setup_subpaths_too(self):
        self.fake.operator_create("someone-else", pass_hash="x")
        resp = self.client.post("/setup/admin", data={"username": "x"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.content, b"<!doctype html><title>404</title><p>No encontrado.</p>")

    def test_configured_install_404s_unknown_method(self):
        self.fake.operator_create("someone-else", pass_hash="x")
        resp = self.client.put("/setup")
        self.assertEqual(resp.status_code, 404)

    def test_fresh_install_unknown_sub_step_is_404_not_the_terminal_page(self):
        # No operator/config row -> fresh. An unrecognized sub-step still
        # gets the setup contract's minimal body, never the rich native
        # terminal error page (a matched-but-unrecognized /setup/* path
        # must not silently fall through).
        resp = self.client.get("/setup/not-a-real-step")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.content, b"<!doctype html><title>404</title><p>No encontrado.</p>")


class TestWelcomeAndLocation(SetupRouterTestCase):
    def test_welcome_page_matches_canonical_renderer(self):
        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, setup.page_welcome().encode("utf-8"))

    def test_location_get_with_country_proposal(self):
        resp = self.client.get("/setup/location", headers={"CF-IPCountry": "AR"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('value="Argentina"', resp.text)

    def test_location_post_missing_fields_uses_canonical_error(self):
        resp = self.client.post("/setup/location", data={"country": "", "town": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("completá al menos el país y la localidad", resp.text.lower())

    def test_location_post_success_redirects_and_sets_cookie(self):
        resp = self.client.post(
            "/setup/location",
            data={"country": "Argentina", "province": "", "town": "Pinamar"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/setup/packs")
        self.assertIn("st_setup", resp.cookies)


class TestFullWalkthrough(SetupRouterTestCase):
    """Drives the whole wizard through the native router and checks it
    lands on the canonical "done" page and produces the operator/config
    effects covered directly by ``test_setup.TestWizardEndToEnd``."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="sana-setup-router-test-")
        self.packs_dir = os.path.join(self._tmp, "packs")
        os.makedirs(self.packs_dir)
        os.makedirs(os.path.join(self.packs_dir, "global"))
        with open(os.path.join(self.packs_dir, "global", "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "global", "level": 1, "parent": None, "language": "es", "name": "Global"}, f)
        self.data_dir = os.path.join(self._tmp, "data")
        self.env_file = os.path.join(self._tmp, "boot.env")
        self.addCleanup(data_packs.invalidate)  # both registered before the env: they run once it is back
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, TURNOS_DATA_DIR=self.data_dir, TURNOS_ENV_FILE=self.env_file,
                     TURNOS_PRIVATE_PACKS_DIR=None, TURNOS_REQUIRED_PACKS=None)
        self._packs_patch = patch.object(data_packs, "_PACKS_DIR", self.packs_dir)
        self._packs_patch.__enter__()
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def tearDown(self):
        self._packs_patch.__exit__(None, None, None)
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def test_full_walkthrough_creates_operator_via_native_router(self):
        r1 = self.client.post(
            "/setup/location",
            data={"country": "Argentina", "province": "Buenos Aires", "town": "Pinamar"},
            follow_redirects=False,
        )
        cookies = dict(r1.cookies)

        r2 = self.client.post("/setup/packs", data={"mode": "scaffold", "pack_name": "Pinamar"},
                              cookies=cookies, follow_redirects=False)
        cookies.update(dict(r2.cookies))

        r3 = self.client.post("/setup/language", data={"languages": ["es"], "lang_default": "es"},
                              cookies=cookies, follow_redirects=False)
        cookies.update(dict(r3.cookies))

        r4 = self.client.post(
            "/setup/admin",
            data={"username": "nativeadmin", "password": "supersecret1",
                  "password2": "supersecret1", "email": "", "totp": "later"},
            cookies=cookies, follow_redirects=False,
        )
        cookies.update(dict(r4.cookies))

        r5 = self.client.post("/setup/apikeys", data={"openrouter_key": ""},
                              cookies=cookies, follow_redirects=False)
        self.assertEqual(r5.status_code, 200, r5.text)
        self.assertIn("nativeadmin", r5.text)
        self.assertIn("Max-Age=0", r5.headers.get("set-cookie", ""))
        self.assertIn("nativeadmin", self.fake.operators)
        self.assertTrue(setup.is_fresh_install() is False)


if __name__ == "__main__":
    unittest.main()
