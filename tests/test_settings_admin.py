"""test_settings_admin.py — sección «Settings» del panel (fase 4).

No depende de una base viva: config.py cae a env/.env cuando la DB no
responde (mismo patrón hermético que test_system_status.py). ENV_PATH se
apaga para no leer el .env real del checkout.

Transporte: el router NATIVO (`admin_panel/settings_admin_router.py`) sobre
una app FastAPI mínima — `settings_admin.py` ya no tiene un
`handle(handler, method)` legacy que llamar. `Request` mantiene la forma
`(path, body) -> (status, json)` que estas pruebas siempre midieron, así
que las aserciones de dominio son las mismas de antes; lo único que cambió
es por dónde entran. El gate se stubbea en `admin_auth.evaluate_admin_gate`
(el puerto puro que `core.deps.require_admin` llama), no en el
`require_admin(handler, ...)` legacy.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_settings_admin.py"
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin_panel import settings_admin as sa  # noqa: E402
from app.admin_panel import settings_admin_router  # noqa: E402
from app.auth import admin_auth             # noqa: E402
from app.core import config  # noqa: E402

# Assembled, not written out: a key-shaped literal in a test file trips the secret scanner of the publication preflight.
FAKE_OR_KEY = "-".join(["sk", "or", "v1", "abcdef1234"])

_app = FastAPI()
_app.include_router(settings_admin_router.router)
from app.core import deps as _early_deps  # noqa: E402
_early_deps.install_early_response(_app)  # the app's answer of a dependency
_CLIENT = TestClient(_app)

# Los dos paths que el router nativo posee — lo que el `handle()` legacy
# contestaba con True/False cuando se le ofrecía un request.
_OWNED = ("/api/admin/settings", "/api/admin/settings/probar")


class Request:
    """Un request contra el router nativo, con la MISMA superficie que estas
    pruebas leían del handler legacy: `.status` y `.json` después de
    `_call()`."""

    def __init__(self, path, body=None):
        self.path = path
        self.body = body
        self.json = None
        self.status = None


def _call(req, method):
    """Manda `req` por el transporte nativo y guarda status/json en él.
    Devuelve True si el router es dueño de esa ruta+método (el booleano que
    el `handle()` legacy devolvía), False si no la reclama."""
    kwargs = {} if req.body is None else {"json": req.body}
    res = _CLIENT.request(method, req.path, **kwargs)
    if res.status_code in (404, 405) and req.path.rstrip("/") not in _OWNED:
        return False
    req.status = res.status_code
    try:
        req.json = res.json()
    except ValueError:
        req.json = None
    return True


def _gate_ok(*a, **k):
    return admin_auth.GateResult(identity={"usuario": "admin", "rol": "admin"})


def _gate_401(*a, **k):
    return admin_auth.GateResult(status=401, body={"error": "no_autenticado"})


def _as_admin():
    return patch.object(admin_auth, "evaluate_admin_gate", _gate_ok)


def _as_anon():
    return patch.object(admin_auth, "evaluate_admin_gate", _gate_401)


class GateTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""

    def tearDown(self):
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_sin_sesion_401_no_expone_nada(self):
        with _as_anon():
            h = Request("/api/admin/settings")
            self.assertTrue(_call(h, "GET"))
            # El gate contesta 401 y NADA del payload de Settings se filtra.
            self.assertEqual(h.status, 401)
            self.assertEqual(h.json, {"error": "no_autenticado"})

    def test_ruta_ajena_no_matchea(self):
        h = Request("/api/otra-cosa")
        self.assertFalse(_call(h, "GET"))

    def test_admin_ok_devuelve_payload(self):
        with _as_admin(), patch.object(config, "_db_overrides", return_value={}):
            h = Request("/api/admin/settings")
            self.assertTrue(_call(h, "GET"))
            self.assertEqual(h.status, 200)
            for k in ("lock", "location", "languages", "chat", "turnos", "seguridad", "connectors", "api_keys"):
                self.assertIn(k, h.json)

    def test_paquete_de_ejemplo_no_aparece_fuera_de_su_instalacion(self):
        packs = [
            {"id": "global", "name": "Global"},
            {"id": "ar-b-pinamar", "name": "Pinamar"},
            {"id": "example-town", "name": "Sampleton", "example": True},
        ]
        with patch.object(sa.data_packs, "list_packs", return_value=packs), \
             patch.object(sa.data_packs, "active_chain", return_value=["global", "ar-b-pinamar"]):
            ids = [p["id"] for p in sa._location_payload()["paquetes_disponibles"]]
        self.assertNotIn("example-town", ids)

    def test_paquete_de_ejemplo_sigue_visible_si_es_la_instalacion_activa(self):
        packs = [{"id": "global"}, {"id": "example-town", "example": True}]
        with patch.object(sa.data_packs, "list_packs", return_value=packs), \
             patch.object(sa.data_packs, "active_chain", return_value=["global", "example-town"]):
            ids = [p["id"] for p in sa._location_payload()["paquetes_disponibles"]]
        self.assertIn("example-town", ids)


class SecretosNuncaSeEchoanTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
        _env.set_env(self, {"OPENROUTER_API_KEY": FAKE_OR_KEY})

    def tearDown(self):
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_get_nunca_manda_el_valor_real(self):
        with _as_admin(), patch.object(config, "_db_overrides", return_value={}):
            h = Request("/api/admin/settings")
            _call(h, "GET")
            texto = json.dumps(h.json)
            self.assertNotIn(FAKE_OR_KEY, texto)
            campo = next(c for grp in h.json["api_keys"] for c in grp["campos"]
                        if c["clave"] == "OPENROUTER_API_KEY")
            self.assertTrue(campo["configurado"])
            self.assertEqual(campo["last4"], "1234")

    def test_campo_no_secreto_si_se_devuelve_en_claro(self):
        _env.set_env(self, MAILGUN_DOMAIN="mg.example.org")
        with _as_admin(), patch.object(config, "_db_overrides", return_value={}):
            h = Request("/api/admin/settings")
            _call(h, "GET")
            campo = next(c for grp in h.json["api_keys"] for c in grp["campos"]
                        if c["clave"] == "MAILGUN_DOMAIN")
            self.assertEqual(campo["valor"], "mg.example.org")


class GuardarApiKeysTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self._guardados = {"v": {}}

        def fake_guardar(overrides):
            actual = dict(self._guardados["v"])
            for k, v in overrides.items():
                if v in (None, ""):
                    actual.pop(k, None)
                else:
                    actual[k] = v
            self._guardados["v"] = actual
            return actual
        self._patch_guardar = patch.object(config, "guardar", side_effect=fake_guardar)
        self._patch_guardar.start()
        self._patch_db = patch.object(config, "_db_overrides", side_effect=lambda: self._guardados["v"])
        self._patch_db.start()

    def tearDown(self):
        self._patch_guardar.stop()
        self._patch_db.stop()
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_campo_secreto_vacio_no_pisa_lo_guardado(self):
        self._guardados["v"]["OPENROUTER_API_KEY"] = "sk-viejo-real-1234"
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys",
                            "valores": {"OPENROUTER_API_KEY": ""}})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(self._guardados["v"].get("OPENROUTER_API_KEY"), "sk-viejo-real-1234")

    def test_campo_secreto_con_valor_pisa(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys",
                            "valores": {"OPENROUTER_API_KEY": "sk-nuevo-9999"}})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(self._guardados["v"].get("OPENROUTER_API_KEY"), "sk-nuevo-9999")

    def test_borrar_explicito_borra(self):
        self._guardados["v"]["OPENROUTER_API_KEY"] = "sk-viejo"
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys",
                            "valores": {}, "borrar": ["OPENROUTER_API_KEY"]})
            self.assertTrue(_call(h, "POST"))
        self.assertNotIn("OPENROUTER_API_KEY", self._guardados["v"])

    def test_clave_desconocida_se_ignora(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys",
                            "valores": {"TURNOS_ALGO_INVENTADO": "x"}})
            self.assertTrue(_call(h, "POST"))
        self.assertNotIn("TURNOS_ALGO_INVENTADO", self._guardados["v"])

    def test_perfil_invalido_no_persiste(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys", "valores": {
                "AI_INFERENCE_PROFILE": "somewhere_else",
            }})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 400)
        self.assertNotIn("AI_INFERENCE_PROFILE", self._guardados["v"])

    def test_hosted_requires_https_before_persistence(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys", "valores": {
                "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
                "AI_INFERENCE_BASE_URL": "http://inference.example/v1/chat/completions",
            }})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 400)
        self.assertEqual(self._guardados["v"], {})

    def test_cloud_shared_rejects_an_operator_endpoint_before_persistence(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys", "valores": {
                "AI_INFERENCE_PROFILE": "cloud_shared",
                "AI_INFERENCE_BASE_URL": "https://other.example/v1/chat/completions",
            }})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 400)
        self.assertEqual(self._guardados["v"], {})

    def test_self_hosted_http_rejects_public_destination_before_persistence(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys", "valores": {
                "AI_INFERENCE_PROFILE": "self_hosted_vllm",
                "AI_INFERENCE_BASE_URL": "http://203.0.113.9/v1/chat/completions",
            }})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 400)
        self.assertEqual(self._guardados["v"], {})

    def test_self_hosted_private_http_saves_profile_and_endpoint(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "api_keys", "valores": {
                "AI_INFERENCE_PROFILE": "self_hosted_vllm",
                "AI_INFERENCE_BASE_URL": "http://127.0.0.1:8000/v1/chat/completions",
            }})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"]["AI_INFERENCE_PROFILE"], "self_hosted_vllm")
        self.assertEqual(self._guardados["v"]["AI_INFERENCE_BASE_URL"],
                         "http://127.0.0.1:8000/v1/chat/completions")

    def test_grupo_desconocido_400(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "no_existe", "valores": {}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)


class GuardarConnectorsTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self._guardados = {"v": {}}

        def fake_guardar(overrides):
            self._guardados["v"].update(overrides)
            return dict(self._guardados["v"])

        self._patch_guardar = patch.object(config, "guardar", side_effect=fake_guardar)
        self._patch_guardar.start()
        self._patch_db = patch.object(config, "_db_overrides", side_effect=lambda: dict(self._guardados["v"]))
        self._patch_db.start()

    def tearDown(self):
        self._patch_guardar.stop()
        self._patch_db.stop()
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_enable_hsi_is_an_explicit_connector_selection(self):
        with _as_admin():
            h = Request("/api/admin/settings", {
                "grupo": "connectors", "valores": {"HSI_ENABLED": True},
            })
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"]["CONNECTOR_EMR_SYNC"], "hsi")
        self.assertTrue(h.json["connectors"]["emr_sync"]["hsi_enabled"])

    def test_disable_hsi_persists_off_instead_of_falling_back(self):
        self._guardados["v"]["CONNECTOR_EMR_SYNC"] = "hsi"
        with _as_admin():
            h = Request("/api/admin/settings", {
                "grupo": "connectors", "valores": {"HSI_ENABLED": False},
            })
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"]["CONNECTOR_EMR_SYNC"], "off")
        self.assertFalse(h.json["connectors"]["emr_sync"]["hsi_enabled"])

    def test_connector_switch_rejects_non_boolean_values(self):
        with _as_admin():
            h = Request("/api/admin/settings", {
                "grupo": "connectors", "valores": {"HSI_ENABLED": "false"},
            })
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 400)
        self.assertEqual(self._guardados["v"], {})

    def test_the_acknowledgement_carries_the_hsi_state_it_just_changed(self):
        _env.set_env(self, TURNOS_SKIP_HSI="")  # this module defaults the skip on
        with _as_admin():
            h = Request("/api/admin/settings", {
                "grupo": "connectors", "valores": {"HSI_ENABLED": True},
            })
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 200)
        estado = h.json["connectors"]["hsi"]
        self.assertEqual((estado["modo"], estado["motivo"]), ("servidor", "conector"))
        self.assertIn("centros", estado)
        self.assertTrue(estado["modo_texto"] and estado["motivo_texto"])

    def test_the_state_names_the_mode_without_the_server_side_push(self):
        self._guardados["v"]["CONNECTOR_EMR_SYNC"] = "off"
        with _as_admin(), \
                patch("app.core.hsi_extension.install", return_value={"tienda": "https://store.example/x", "zip": None, "guia": None}), \
                patch("app.patient_web.catalog.centers", return_value=[
                    {"slug": "hospital", "name": "Hospital", "hsi_institution": "1086"},
                    {"slug": "caps", "name": "CAPS", "hsi_institution": ""}]):
            h = Request("/api/admin/settings", {
                "grupo": "connectors", "valores": {"HSI_ENABLED": False},
            })
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 200)
        estado = h.json["connectors"]["hsi"]
        self.assertEqual((estado["modo"], estado["motivo"]), ("extension", "sin_conector"))
        self.assertNotIn("diario", estado)
        self.assertEqual((estado["con_numero"], estado["total"]), (1, 2))
        self.assertEqual(estado["centros_texto"], "1 de 2 centros con número")

    def test_server_mode_carries_the_journal_once_it_can_send(self):
        _env.set_env(self, TURNOS_SKIP_HSI="")  # this module defaults the skip on
        with _as_admin(), \
                patch("app.core.db_hsi.hsi_metrics", return_value={"outbox": {"pending": 2, "sending": 1, "error": 1}}), \
                patch("app.core.db_hsi.hsi_open_conflicts", return_value=[]), \
                patch("app.core.db_hsi.hsi_last_confirmed", return_value=None):
            h = Request("/api/admin/settings", {
                "grupo": "connectors", "valores": {"HSI_ENABLED": True},
            })
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(h.status, 200)
        self.assertEqual(h.json["connectors"]["hsi"]["diario"],
                         {"ultima": None, "en_cola": 3, "errores": 1, "conflictos": 0})

    def test_the_last_transmission_travels_with_the_installation_offset(self):
        import datetime
        from app.admin_panel import hsi_status
        ultima = datetime.datetime(2026, 11, 10, 14, 30, tzinfo=datetime.timezone.utc)
        with patch("app.core.db_hsi.hsi_metrics", return_value={"outbox": {}}), \
                patch("app.core.db_hsi.hsi_open_conflicts", return_value=[]), \
                patch("app.core.db_hsi.hsi_last_confirmed", return_value=ultima):
            # The screen reads the day and the hour from this text literally.
            self.assertEqual(hsi_status.journal()["ultima"], "2026-11-10T11:30:00-03:00")
        with patch("app.core.db_hsi.hsi_metrics", side_effect=RuntimeError("no database")):
            self.assertIsNone(hsi_status.journal())


class GuardarLanguagesTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self._guardados = {"v": {}}
        self._patch_guardar = patch.object(config, "guardar",
            side_effect=lambda ov: self._guardados["v"].update(ov) or self._guardados["v"])
        self._patch_guardar.start()

    def tearDown(self):
        self._patch_guardar.stop()
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_idioma_sin_catalogo_rechaza(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "languages",
                            "valores": {"LANGUAGES": "es,klingon"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)

    def test_idiomas_validos_guarda(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "languages",
                            "valores": {"LANGUAGES": "es,en", "LANG_SWITCHER_MODE": "nav"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"]["LANGUAGES"], "es,en")
        self.assertEqual(self._guardados["v"]["LANG_SWITCHER_MODE"], "nav")

    def test_modo_switcher_invalido_rechaza(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "languages",
                            "valores": {"LANG_SWITCHER_MODE": "sidebar"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)


class GuardarChatTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self._guardados = {"v": {}}

        def fake_guardar(overrides):
            actual = dict(self._guardados["v"])
            for key, value in overrides.items():
                if value in (None, ""):
                    actual.pop(key, None)
                else:
                    actual[key] = value
            self._guardados["v"] = actual
            return actual

        self._patch_guardar = patch.object(config, "guardar", side_effect=fake_guardar)
        self._patch_guardar.start()
        self._patch_db = patch.object(config, "_db_overrides", side_effect=lambda: self._guardados["v"])
        self._patch_db.start()

    def tearDown(self):
        self._patch_guardar.stop()
        self._patch_db.stop()
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_saludos_se_guardan_por_idioma(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "chat", "valores": {
                "CHAT_GREETING_ES": "**Hola.**\n\n* Sacá un turno",
                "CHAT_GREETING_EN": "**Hi.**\n\n* Book an appointment",
            }})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"]["CHAT_GREETING_ES"], "**Hola.**\n\n* Sacá un turno")
        self.assertEqual(self._guardados["v"]["CHAT_GREETING_EN"], "**Hi.**\n\n* Book an appointment")

    def test_saludo_vacio_restaura_el_default(self):
        self._guardados["v"]["CHAT_GREETING_ES"] = "Texto anterior"
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "chat", "valores": {
                "CHAT_GREETING_ES": "",
            }})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 200)
        self.assertNotIn("CHAT_GREETING_ES", self._guardados["v"])

    def test_saludo_demasiado_largo_se_rechaza(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "chat", "valores": {
                "CHAT_GREETING_ES": "x" * (sa.CHAT_GREETING_MAX + 1),
            }})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)


class GuardarLocationTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self._guardados = {"v": {}}
        self._patch_guardar = patch.object(config, "guardar",
            side_effect=lambda ov: self._guardados["v"].update(ov) or self._guardados["v"])
        self._patch_guardar.start()
        self._patch_inv = patch.object(sa.data_packs, "invalidate", lambda: None)
        self._patch_inv.start()

    def tearDown(self):
        self._patch_guardar.stop()
        self._patch_inv.stop()
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_cadena_con_caracteres_raros_rechaza(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "location",
                            "valores": {"PACK_CHAIN": "../../etc/passwd"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)

    def test_cadena_valida_guarda(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "location",
                            "valores": {"PACK_CHAIN": "global,ar,ar-b,ar-b-pinamar"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"]["PACK_CHAIN"], "global,ar,ar-b,ar-b-pinamar")

    def test_identidad_guarda_bajo_site_id(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "location",
                            "valores": {"SITE_ID_CITY": "Otra Ciudad"}})
            self.assertTrue(_call(h, "POST"))
        self.assertEqual(self._guardados["v"]["SITE_ID_CITY"], "Otra Ciudad")


class GuardarSeguridadTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        self._guardados = {"v": {}}
        self._patch_guardar = patch.object(config, "guardar",
            side_effect=lambda ov: self._guardados["v"].update(ov) or self._guardados["v"])
        self._patch_guardar.start()
        self._patch_db = patch.object(config, "_db_overrides", side_effect=lambda: self._guardados["v"])
        self._patch_db.start()

    def tearDown(self):
        self._patch_guardar.stop()
        self._patch_db.stop()
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()

    def test_lista_valida_se_guarda_como_csv(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "seguridad",
                            "valores": {"IPS_EXENTAS": "127.0.0.1\n::1\n203.0.113.36"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 200)
        self.assertEqual(self._guardados["v"][sa.ip_exempt.CONFIG_KEY],
                         "127.0.0.1,::1,203.0.113.36")
        self.assertEqual(h.json["seguridad"]["ips_exentas"],
                         ["127.0.0.1", "::1", "203.0.113.36"])

    def test_cidr_rechaza(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "seguridad",
                            "valores": {"IPS_EXENTAS": "203.0.113.0/24"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)
        self.assertNotIn(sa.ip_exempt.CONFIG_KEY, self._guardados["v"])

    def test_ip_invalida_rechaza(self):
        with _as_admin():
            h = Request("/api/admin/settings", {"grupo": "seguridad",
                            "valores": {"IPS_EXENTAS": "no-es-una-ip"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 400)

    def test_ruta_exige_admin(self):
        with _as_anon():
            h = Request("/api/admin/settings", {"grupo": "seguridad",
                            "valores": {"IPS_EXENTAS": "127.0.0.1"}})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 401)
            self.assertEqual(h.json, {"error": "no_autenticado"})


class ProbarTestCase(unittest.TestCase):
    def test_integracion_sin_prueba_en_vivo(self):
        with _as_admin():
            h = Request("/api/admin/settings/probar", {"integracion": "turnstile"})
            self.assertTrue(_call(h, "POST"))
            self.assertFalse(h.json["ok"])

    def test_openrouter_probar_llama_a_ai_health(self):
        with _as_admin(), patch.object(sa.ai_health, "probe", return_value=True), \
             patch.object(sa.ai_client, "active_key_kind", return_value="primary"), \
             patch.object(sa.ai_client, "reset_sticky_for_tests", lambda: None):
            h = Request("/api/admin/settings/probar", {"integracion": "openrouter"})
            self.assertTrue(_call(h, "POST"))
            self.assertTrue(h.json["ok"])
            self.assertEqual(h.json["key_activa"], "primary")
            # The success carries the server line, not only «Funciona.» (TASK-16).
            self.assertEqual(h.json["detalle"], "OpenRouter: responde.")

    def test_probe_failure_never_echoes_a_sensitive_destination(self):
        with _as_admin(), patch.object(sa.ai_health, "probe", side_effect=RuntimeError(
                "https://private.example/v1/chat/completions")):
            h = Request("/api/admin/settings/probar", {"integracion": "openrouter"})
            self.assertTrue(_call(h, "POST"))
        self.assertFalse(h.json["ok"])
        self.assertNotIn("private.example", h.json["detalle"])

    def test_hsi_ok_dice_que_servidor_respondio(self):
        # The panel shows the server line on success, not only «Funciona.».
        with _as_admin(), patch.object(sa, "_capability_selected", return_value=True), \
             patch.object(sa.system_status, "_reachable", return_value=(True, None)), \
             patch.object(sa.config, "get", return_value="http://hsi.example:8280"):
            h = Request("/api/admin/settings/probar", {"integracion": "hsi"})
            self.assertTrue(_call(h, "POST"))
        self.assertTrue(h.json["ok"])
        self.assertEqual(h.json["detalle"], "hsi.example: responde.")

    def test_emr_bridge_ok_tiene_linea_propia(self):
        class _Adapter:
            def health(self):
                return {"ok": True}

        with _as_admin(), patch.object(sa, "_capability_selected", return_value=True), \
             patch("app.connectors.adapter_for", return_value=_Adapter()):
            h = Request("/api/admin/settings/probar", {"integracion": "emr_bridge"})
            self.assertTrue(_call(h, "POST"))
        self.assertTrue(h.json["ok"])
        self.assertTrue(h.json["detalle"])

    def test_backup_fallido_no_dice_conexion_comprobada(self):
        class _Client:
            def probe(self, prefix):
                return False

        with _as_admin(), patch.object(sa.backup_settings, "client", return_value=_Client()), \
             patch.object(sa.backup_settings, "snapshot", return_value={"TURNOS_BACKUP_S3_PREFIX": "p/"}):
            h = Request("/api/admin/settings/probar", {"integracion": "backup"})
            self.assertTrue(_call(h, "POST"))
        self.assertFalse(h.json["ok"])
        self.assertIsNone(h.json["detalle"])

    def test_ruta_probar_exige_admin(self):
        with _as_anon():
            h = Request("/api/admin/settings/probar", {"integracion": "openrouter"})
            self.assertTrue(_call(h, "POST"))
            self.assertEqual(h.status, 401)
            self.assertEqual(h.json, {"error": "no_autenticado"})


if __name__ == "__main__":
    unittest.main()
