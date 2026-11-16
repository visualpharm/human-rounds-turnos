"""test_setup.py — first-run installer wizard (fase 5).

No depende de una base viva: todas las funciones de `db` que setup.py toca
se reemplazan por un store en memoria (`_FakeDB`), el mismo patrón que
test_settings_admin.py usa para config.py. `data_packs._PACKS_DIR` se
monkeypatchea a un directorio temporal (mismo patrón que test_data_packs.py)
para que scaffoldear un pack nuevo nunca toque `packs/` de verdad.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_setup.py"
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_SETUP_SKIP_GEOIP="1")

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.auth import password_hashing  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app.ops import setup            # noqa: E402
from app.ops import setup_router  # noqa: E402


# ============================================================== fakes

class _FakeDB:
    """In-memory stand-in for the handful of db.py functions setup.py calls.
    Mirrors the real row shapes closely enough (a "doc" key on catalog rows)
    that config.py's own DB layer works unmodified against it."""

    def __init__(self):
        self.operators = {}
        self.catalog = {}   # (type, id) -> doc
        self.security = {}  # username -> deadline

    def operators_list(self):
        return list(self.operators.values())

    def catalog_list(self, tipo):
        return [{"type": tipo, "id": i, "doc": d} for (t, i), d in self.catalog.items() if t == tipo]

    def catalog_get(self, tipo, id_):
        d = self.catalog.get((tipo, id_))
        return {"doc": d} if d is not None else None

    def catalog_upsert(self, tipo, id_, doc, borrado=False, connection=None):
        self.catalog[(tipo, id_)] = dict(doc)
        return {"type": tipo, "id": id_, "doc": doc, "deleted": borrado}

    def operator_get(self, usuario):
        return self.operators.get(usuario)

    def operator_create(self, usuario, password=None, rol="medico", totp_secret=None,
                        medico_id=None, email=None, centros=None, pass_hash=None):
        if usuario in self.operators:
            raise RuntimeError("username ya existe (constraint UNIQUE simulada)")
        row = {"usuario": usuario, "rol": rol, "totp_secret": totp_secret,
               "email": email, "pass_hash": pass_hash or password, "activo": True}
        self.operators[usuario] = row
        return dict(row)

    def admin_security_set_deadline(self, usuario, deadline):
        self.security[usuario] = deadline
        return {"usuario": usuario, "totp_deadline": deadline}


@contextlib.contextmanager
def _patched_db(fake):
    # Slice 8 writes config plus audit evidence in a single transaction.
    # Keep the in-memory fixture on that boundary, never open a real DB.
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value.__enter__.return_value
    def execute(sql, params=None):
        # `config.guardar` runs Core statements: SQL text with named parameters.
        values = list((params or {}).values())
        lock = "pg_advisory_xact_lock" in sql and values == ["config:settings"]
        read = ("FROM catalog" in sql and sql.rstrip().endswith("FOR UPDATE")
                and sorted(values) == ["config", "settings"])
        if not (lock or read):
            raise AssertionError("Unexpected setup transaction SQL: %s %r" % (sql, params))
    cur.execute.side_effect = execute
    cur.fetchone.side_effect = lambda: fake.catalog_get("config", "settings")
    def append(events, connection=None):
        if events:
            raise AssertionError("First-run setup unexpectedly changed an AI function")
    with patch.object(db, "_connect", return_value=conn), \
         patch.object(db_ai, "ai_register_append", side_effect=append), \
         patch.object(db_operators, "operators_list", side_effect=fake.operators_list), \
         patch.object(db_catalog, "catalog_list", side_effect=fake.catalog_list), \
         patch.object(db_catalog, "catalog_get", side_effect=fake.catalog_get), \
         patch.object(db_catalog, "catalog_upsert", side_effect=fake.catalog_upsert), \
         patch.object(db_operators, "operator_get", side_effect=fake.operator_get), \
         patch.object(db_operators, "operator_create", side_effect=fake.operator_create), \
         patch.object(db_auth, "admin_security_set_deadline", side_effect=fake.admin_security_set_deadline):
        yield fake


# ============================================================== base

class SetupTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        setup._reset_cache_para_tests()
        self.fake = _FakeDB()
        self._db_patch = _patched_db(self.fake)
        self._db_patch.__enter__()
        app = FastAPI()
        app.include_router(setup_router.router)
        self.client = TestClient(app, client=("203.0.113.5", 51000))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self._db_patch.__exit__(None, None, None)
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()
        setup._reset_cache_para_tests()


# ============================================================== is_fresh_install

class TestIsFreshInstall(SetupTestCase):
    def test_fresh_when_empty(self):
        _env.set_env(self, TURNOS_ADMIN_PASS=None)
        config._reset_cache_para_tests()
        self.assertTrue(setup.is_fresh_install())

    def test_an_owner_password_in_the_environment_is_never_fresh(self):
        # Security review 2026-11 (L-1): an emptied or restored database behind an installation
        # whose owner is configured must not open /setup to the first visitor.
        _env.set_env(self, TURNOS_ADMIN_PASS="owner-pass-from-env")
        config._reset_cache_para_tests()
        self.assertFalse(setup.is_fresh_install())
        self.assertEqual(self.client.get("/setup").status_code, 404)

    def test_not_fresh_with_an_operator(self):
        self.fake.operators["admin"] = {"usuario": "admin"}
        self.assertFalse(setup.is_fresh_install())

    def test_not_fresh_with_a_config_row(self):
        self.fake.catalog[("config", "settings")] = {"PACK_CHAIN": "global"}
        self.assertFalse(setup.is_fresh_install())

    def test_db_down_is_not_fresh(self):
        with patch.object(db_operators, "operators_list", side_effect=db.DbUnavailable("down")):
            self.assertFalse(setup.is_fresh_install())

    def test_demo_mode_is_never_fresh(self):
        with patch.object(demo_installation, "enabled", return_value=True):
            self.assertFalse(setup.is_fresh_install())

    def test_latch_sticks_without_further_db_calls(self):
        self.fake.operators["admin"] = {"usuario": "admin"}
        self.assertFalse(setup.is_fresh_install())
        # Now break the DB entirely — a latched "configured" process must
        # not need it again.
        with patch.object(db_operators, "operators_list", side_effect=AssertionError("should not be called")):
            self.assertFalse(setup.is_fresh_install())


# ============================================================== gate / routing

class TestGateRouting(SetupTestCase):
    """The native router owns only `/setup*`; global request interception
    was a property of the retired stdlib dispatcher and is intentionally
    absent from the native topology."""

    def test_non_setup_paths_are_not_claimed(self):
        for method, path in (
            ("GET", "/"),
            ("GET", "/doctors"),
            ("GET", "/human-rounds.css"),
            ("GET", "/webdata/photos.json"),
            ("POST", "/api/admin/login"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, 404)

    def test_setup_get_serves_wizard_when_fresh(self):
        response = self.client.get("/setup")
        self.assertEqual(response.status_code, 200)

    def test_configured_install_never_touches_other_routes(self):
        self.fake.operators["admin"] = {"usuario": "admin"}
        self.assertEqual(self.client.get("/").status_code, 404)

    def test_configured_install_404s_setup_forever(self):
        self.fake.operators["admin"] = {"usuario": "admin"}
        self.assertEqual(self.client.get("/setup").status_code, 404)

    def test_configured_install_404s_setup_subpaths_too(self):
        self.fake.operators["admin"] = {"usuario": "admin"}
        response = self.client.post("/setup/admin", data={"username": "x"})
        self.assertEqual(response.status_code, 404)

    def test_db_down_does_not_force_setup_mode(self):
        with patch.object(db_operators, "operators_list", side_effect=db.DbUnavailable("down")):
            self.assertEqual(self.client.get("/").status_code, 404)
            self.assertEqual(self.client.get("/setup").status_code, 404)


# ============================================================== country proposal

class TestCountryProposal(SetupTestCase):
    def test_cf_ipcountry_header_honored(self):
        response = self.client.get("/setup/location", headers={"CF-IPCountry": "AR"})
        self.assertIn("Argentina", response.text)

    def test_unknown_country_code_shown_raw(self):
        response = self.client.get("/setup/location", headers={"CF-IPCountry": "ZZ"})
        self.assertIn('value="ZZ"', response.text)

    def test_geoip_lookup_failure_degrades_gracefully(self):
        with _env.patched_env({"TURNOS_SETUP_SKIP_GEOIP": "0"}), \
             patch.object(setup, "_geoip_lookup", side_effect=OSError("no network")):
            response = self.client.get("/setup/location")
        self.assertEqual(response.status_code, 200)  # never raises, never blocks the page

    def test_geoip_skip_env_short_circuits(self):
        with patch.object(setup, "_geoip_lookup", side_effect=AssertionError("should not be called")):
            response = self.client.get("/setup/location")
        self.assertEqual(response.status_code, 200)


# ============================================================== full wizard walk

class TestProposeChain(unittest.TestCase):
    """The pack chain the wizard proposes for what the deployer typed."""

    PACKS = [
        {"id": "global", "level": 1, "name": "Global"},
        {"id": "ar", "level": 2, "name": "Argentina"},
        {"id": "ar-b", "level": 3, "name": "Buenos Aires (provincia)"},
        {"id": "example-town", "level": 4, "name": "Sampleton"},
    ]

    def _chain(self, country, province, town):
        with patch.object(data_packs, "list_packs", return_value=self.PACKS):
            return setup._propose_chain(country, province, town)

    def test_the_name_typed_is_the_one_of_the_pack(self):
        self.assertEqual(self._chain("Argentina", "Buenos Aires (provincia)", "Sampleton"),
                         (["global", "ar", "ar-b"], "example-town", "ar-b"))

    def test_a_province_typed_without_the_note_finds_its_pack(self):
        self.assertEqual(self._chain("Argentina", "Buenos Aires", "Sampleton"),
                         (["global", "ar", "ar-b"], "example-town", "ar-b"))

    def test_case_and_accents_do_not_matter(self):
        self.assertEqual(self._chain("  argentina ", "BUENOS AIRES", "sámpleton"),
                         (["global", "ar", "ar-b"], "example-town", "ar-b"))

    def test_an_unknown_province_stays_out_of_the_chain(self):
        self.assertEqual(self._chain("Argentina", "Córdoba", "Villa Nueva"), (["global", "ar"], None, "ar"))

    def test_an_empty_name_matches_nothing(self):
        self.assertEqual(self._chain("", "", ""), (["global"], None, "global"))


class TestWizardEndToEnd(SetupTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="sana-setup-test-")
        self.packs_dir = os.path.join(self._tmp, "packs")
        os.makedirs(self.packs_dir)
        os.makedirs(os.path.join(self.packs_dir, "global"))
        with open(os.path.join(self.packs_dir, "global", "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "global", "level": 1, "parent": None, "language": "es", "name": "Global"}, f)
        # The wizard now scaffolds onto the persistent volume and writes the
        # required-pack boot keys (TURNOS_PRIVATE_PACKS_DIR /
        # TURNOS_REQUIRED_PACKS) to the effective .env — so both the
        # install-owned root and that .env must land in the temp dir, never
        # the real checkout's data/ or .env.
        self.data_dir = os.path.join(self._tmp, "data")
        self.env_file = os.path.join(self._tmp, "boot.env")
        self.install_root = os.path.join(self.data_dir, "packs")
        _env.set_env(self, TURNOS_DATA_DIR=self.data_dir, TURNOS_ENV_FILE=self.env_file,
                     TURNOS_PRIVATE_PACKS_DIR=None, TURNOS_REQUIRED_PACKS=None)
        self._packs_patch = patch.object(data_packs, "_PACKS_DIR", self.packs_dir)
        self._packs_patch.__enter__()
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def tearDown(self):
        self._packs_patch.__exit__(None, None, None)
        config._reset_cache_para_tests()
        data_packs.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _cookie_from(self, response):
        raw = response.headers.get("set-cookie")
        self.assertIsNotNone(raw, "expected a Set-Cookie on this response")
        return raw.split(";", 1)[0]

    def _post(self, path, data, cookie=None):
        headers = {"Cookie": cookie} if cookie else None
        return self.client.post(
            path, data=data, headers=headers, follow_redirects=False)

    def test_full_walkthrough_creates_operator_config_and_pack(self):
        # 1) location
        h1 = self.client.post(
            "/setup/location",
            data={"country": "Argentina", "province": "Buenos Aires",
                  "town": "Villa Nueva"},
            follow_redirects=False,
        )
        self.assertEqual(h1.status_code, 303)
        self.assertEqual(h1.headers.get("location"), "/setup/packs")
        cookie = self._cookie_from(h1)

        # 2) packs — no town pack matches "Villa Nueva": scaffold branch
        h2get = self.client.get("/setup/packs", headers={"Cookie": cookie})
        self.assertIn("Villa Nueva", h2get.text)

        h2 = self.client.post(
            "/setup/packs",
            headers={"Cookie": cookie},
            data={"mode": "scaffold", "pack_name": "Villa Nueva"},
            follow_redirects=False,
        )
        self.assertEqual(h2.status_code, 303)
        self.assertEqual(h2.headers.get("location"), "/setup/language")
        cookie = self._cookie_from(h2)

        # 3) language
        h3 = self.client.post(
            "/setup/language",
            headers={"Cookie": cookie},
            data={"languages": ["es"], "lang_default": "es"},
            follow_redirects=False,
        )
        self.assertEqual(h3.status_code, 303)
        self.assertEqual(h3.headers.get("location"), "/setup/admin")
        cookie = self._cookie_from(h3)

        # 4) admin account
        h4 = self.client.post(
            "/setup/admin",
            headers={"Cookie": cookie},
            data={"username": "villanueva.admin", "password": "supersecret1",
                  "password2": "supersecret1", "email": "", "totp": "later"},
            follow_redirects=False,
        )
        self.assertEqual(h4.status_code, 303, h4.text)
        self.assertEqual(h4.headers.get("location"), "/setup/backups")
        cookie = self._cookie_from(h4)
        # the plaintext password never rides in the cookie payload
        self.assertNotIn("supersecret1", cookie)

        h5get = self.client.get("/setup/apikeys", headers={"Cookie": cookie})
        self.assertNotIn("HSI", h5get.text)
        self.assertIn("El resto de las integraciones se configura después", h5get.text)

        # 5) api keys — finish
        h5 = self.client.post(
            "/setup/apikeys",
            headers={"Cookie": cookie},
            data={"openrouter_key": ""},
            follow_redirects=False,
        )
        self.assertEqual(h5.status_code, 200, h5.text)
        self.assertIn("villanueva.admin", h5.text)

        # --- assertions on what actually got persisted -------------------
        self.assertIn("villanueva.admin", self.fake.operators)
        op = self.fake.operators["villanueva.admin"]
        self.assertEqual(op["rol"], "admin")
        self.assertIsNone(op["totp_secret"])
        self.assertNotEqual(op["pass_hash"], "supersecret1")  # hashed, not plaintext
        self.assertTrue(
            password_hashing.verify_and_update(
                op["pass_hash"], "supersecret1"
            )[0]
        )

        settings_doc = self.fake.catalog[("config", "settings")]
        self.assertEqual(settings_doc.get("LANGUAGES"), "es")
        chain = settings_doc.get("PACK_CHAIN", "").split(",")
        self.assertEqual(chain[0], "global")
        town_pack_id = chain[-1]
        self.assertTrue(town_pack_id.startswith("villa-nueva"))

        # the pack actually landed on disk and resolves through data_packs
        manifest = data_packs.manifest(town_pack_id)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["name"], "Villa Nueva")
        self.assertEqual(manifest["level"], 4)
        # DURABILITY (Tier 6): the scaffolded town lives on the install-owned
        # volume root (data/packs under the temp data dir), NOT the image-tree
        # packs/ — a rebuild that repopulates packs/ from the image cannot wipe
        # the clinic's own data.
        sitio_path = os.path.join(self.install_root, town_pack_id, "sitio.json")
        with open(sitio_path, encoding="utf-8") as f:
            sitio = json.load(f)
        self.assertEqual(sitio["city"], "Villa Nueva")
        self.assertEqual(sitio["province"], "Buenos Aires")
        # regression: an explicit "" here used to override
        # site_identity._DEFAULTS's neutral fallback and print a broken
        # sentence on the home page ("...encontrá los servicios . Urgencias:
        # ...") since a scaffolded pack has no centers yet to link to.
        self.assertNotIn("network_links_html", sitio)
        # Nothing of the clinic's ever touched the image-tree packs/: a rebuild
        # would leave it untouched, and the town still resolves from the volume.
        self.assertNotIn(town_pack_id, os.listdir(self.packs_dir))
        # The wizard registered the install root + the town + the chain that
        # names it as boot keys in the effective .env (process/.env only, never
        # the DB). All three move together: the readiness gate validates the
        # town AND, under TURNOS_CONFIG_LOCK=1, the chain still serves it
        # instead of falling through to the bundled public default.
        with open(self.env_file, encoding="utf-8") as f:
            env_text = f.read()
        self.assertIn("TURNOS_PRIVATE_PACKS_DIR=%s" % self.install_root, env_text)
        self.assertIn("TURNOS_REQUIRED_PACKS=%s" % town_pack_id, env_text)
        chain_lines = [ln for ln in env_text.splitlines() if ln.startswith("PACK_CHAIN=")]
        self.assertTrue(chain_lines, "PACK_CHAIN must be a boot key for an install-owned town")
        self.assertEqual(chain_lines[0], "PACK_CHAIN=global,%s" % town_pack_id)
        config._reset_cache_para_tests()
        data_packs.invalidate()
        self.assertIsNone(data_packs.verify_required_packs())

        # Synthetic locked-config restart (TURNOS_CONFIG_LOCK=1): the DB row
        # the wizard also wrote is ignored, no PACK_CHAIN rides in the process
        # env, and the engine default names no scaffolded slug — so the town
        # can only resolve from the .env boot key the wizard just pinned. This
        # is the configuration-ownership contract end-to-end through the real
        # commit: validate-private AND serve-private, never the bundled public
        # default.
        _env.set_env(self, PACK_CHAIN=None)
        try:
            with _env.patched_env(TURNOS_CONFIG_LOCK="1"):
                config._reset_cache_para_tests()
                data_packs.invalidate()
                self.assertIsNone(data_packs.verify_required_packs())
                self.assertIn(town_pack_id, data_packs.active_chain())
        finally:
            config._reset_cache_para_tests()
            data_packs.invalidate()

        # A scaffolded town persists its neutral theme choice explicitly.
        # This remains stable even if a future engine release changes the
        # source default, while a matched existing pack remains operator-owned.
        self.assertEqual(self.fake.catalog.get(("config", "tema")), {"tema": "humanrounds"})

        # closed the instant it committed — no more DB round-trips needed
        with patch.object(db_operators, "operators_list", side_effect=AssertionError("should not be called")):
            self.assertFalse(setup.is_fresh_install())

        # and /setup is gone
        self.assertEqual(self.client.get("/setup").status_code, 404)

    def test_totp_now_routes_first_sign_in_to_verified_account_setup(self):
        h1 = self._post(
            "/setup/location",
            {"country": "Argentina", "province": "", "town": "Otra Localidad"},
        )
        cookie = self._cookie_from(h1)
        h2 = self._post(
            "/setup/packs", {"mode": "scaffold", "pack_name": "Otra Localidad"}, cookie)
        cookie = self._cookie_from(h2)
        h3 = self._post(
            "/setup/language", {"languages": ["es"], "lang_default": "es"}, cookie)
        cookie = self._cookie_from(h3)
        h4 = self._post(
            "/setup/admin",
            {"username": "otra.admin", "password": "supersecret1",
             "password2": "supersecret1", "totp": "now"},
            cookie,
        )
        cookie = self._cookie_from(h4)
        h5 = self._post("/setup/apikeys", {"openrouter_key": ""}, cookie)
        self.assertEqual(h5.status_code, 200)
        self.assertNotIn("otpauth://totp/", h5.text)
        self.assertIn("/admin/account?setup=2fa", h5.text)
        self.assertIn("Google Authenticator", h5.text)
        self.assertIn("Microsoft Authenticator", h5.text)
        self.assertIn("6 dígitos", h5.text)
        self.assertIsNone(self.fake.operators["otra.admin"]["totp_secret"])
        self.assertIsNotNone(self.fake.security["otra.admin"])

    def test_matched_town_pack_skips_scaffold(self):
        os.makedirs(os.path.join(self.packs_dir, "example-town"))
        with open(os.path.join(self.packs_dir, "example-town", "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "example-town", "level": 4, "parent": "global",
                      "language": "es", "name": "Sampleton"}, f)
        data_packs.invalidate()

        h1 = self._post(
            "/setup/location", {"country": "Argentina", "province": "", "town": "Sampleton"})
        cookie = self._cookie_from(h1)

        h2get = self.client.get("/setup/packs", headers={"Cookie": cookie})
        self.assertIn("example-town", h2get.text)

        h2 = self._post(
            "/setup/packs", {"mode": "match", "town_id": "example-town"}, cookie)
        cookie = self._cookie_from(h2)
        h3 = self._post(
            "/setup/language", {"languages": ["es"], "lang_default": "es"}, cookie)
        cookie = self._cookie_from(h3)
        h4 = self._post(
            "/setup/admin",
            {"username": "ejemplo.admin", "password": "supersecret1",
             "password2": "supersecret1", "totp": "later"},
            cookie,
        )
        cookie = self._cookie_from(h4)
        h5 = self._post("/setup/apikeys", {"openrouter_key": "sk-or-x"}, cookie)
        self.assertEqual(h5.status_code, 200, h5.text)

        settings_doc = self.fake.catalog[("config", "settings")]
        self.assertEqual(settings_doc.get("PACK_CHAIN"), "global,example-town")
        self.assertEqual(settings_doc.get("OPENROUTER_API_KEY"), "sk-or-x")
        # no new pack directory was created for a match
        self.assertEqual(sorted(os.listdir(self.packs_dir)), ["example-town", "global"])
        # Scope: only a SCAFFOLDED (install-owned) town writes boot keys to the
        # .env. A matched pre-existing pack references bundled public content,
        # so the wizard must not pin a private root, a required pack, or the
        # chain into the .env — the reviewed matched-case behavior is unchanged
        # by the configuration-ownership fix.
        self.assertFalse(os.path.exists(self.env_file))
        # matching an EXISTING pack (e.g. someone really deploying for the
        # town a pack already represents) must never force the theme to
        # "humanrounds" — that reset is scoped to brand-new scaffolded towns only.
        self.assertNotIn(("config", "tema"), self.fake.catalog)

    def test_password_mismatch_rerenders_admin_step(self):
        h1 = self._post(
            "/setup/location", {"country": "Argentina", "province": "", "town": "Foo"})
        cookie = self._cookie_from(h1)
        h2 = self._post("/setup/packs", {"mode": "scaffold", "pack_name": "Foo"}, cookie)
        cookie = self._cookie_from(h2)
        h3 = self._post(
            "/setup/language", {"languages": ["es"], "lang_default": "es"}, cookie)
        cookie = self._cookie_from(h3)
        h4 = self._post(
            "/setup/admin",
            {"username": "foo.admin", "password": "supersecret1",
             "password2": "different", "totp": "later"},
            cookie,
        )
        self.assertEqual(h4.status_code, 200)
        self.assertIn("no coinciden", h4.text.lower())
        self.assertNotIn("foo.admin", self.fake.operators)

    def test_concurrent_wizard_finishing_first_blocks_the_second(self):
        """Two browser tabs both mid-wizard against a genuinely fresh
        install (zero operators) is the only way a username collision can
        reach the admin/apikeys steps at all — anything pre-existing in
        `operators` would already have 404'd the whole wizard at the gate
        (see TestGateRouting). Model that race: this session gets all the
        way to the last step, then ANOTHER session commits first."""
        h1 = self._post(
            "/setup/location", {"country": "Argentina", "province": "", "town": "Bar"})
        cookie = self._cookie_from(h1)
        h2 = self._post("/setup/packs", {"mode": "scaffold", "pack_name": "Bar"}, cookie)
        cookie = self._cookie_from(h2)
        h3 = self._post(
            "/setup/language", {"languages": ["es"], "lang_default": "es"}, cookie)
        cookie = self._cookie_from(h3)
        h4 = self._post(
            "/setup/admin",
            {"username": "bar.admin", "password": "supersecret1",
             "password2": "supersecret1", "totp": "later"},
            cookie,
        )
        cookie = self._cookie_from(h4)

        # The other tab wins the race and finishes first.
        self.fake.operators["someone.else"] = {"usuario": "someone.else"}
        setup._reset_cache_para_tests()  # a fresh process would see this on its next request

        h5 = self._post("/setup/apikeys", {"openrouter_key": ""}, cookie)
        self.assertEqual(h5.status_code, 404)
        # never created a second operator, never wrote config twice
        self.assertNotIn("bar.admin", self.fake.operators)


# ============================================================== golden: static/config untouched

class TestConfiguredInstallDoesNotChangeConfig(SetupTestCase):
    def test_setup_module_never_calls_config_guardar_when_configured(self):
        self.fake.operators["admin"] = {"usuario": "admin"}
        with patch.object(config, "guardar", side_effect=AssertionError("should not be called")):
            self.assertEqual(self.client.get("/").status_code, 404)


if __name__ == "__main__":
    unittest.main()
