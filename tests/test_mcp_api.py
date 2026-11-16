"""test_mcp_api.py — servidor MCP de Pinamar Turnos (mcp_api.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_mcp_api.py"

Dos capas:
  - PROTOCOLO (sin red / sin base): initialize, ping, tools/list, 405/JSON-RPC
    de error, auth de tools/call, rate-limit y un happy-path de
    search_services (el catálogo sale de webdata/*.json, no toca Postgres).
    La identidad (mcp_api._usuario_por_token) se monkeypatchea.
  - TOKEN (persiste): db_accounts.mcp_token_issue/regenerate/user_by_mcp_token + el
    endpoint /api/account/mcp. Se saltea si no hay Postgres (igual que
    test_ocr_dni).
"""

import base64
import os
import sys
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_FAKE_PUCO="1")

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.modules.chat import mcp_api, mcp_api_router  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests   # noqa: E402
from app.patient_web import site_catalog
from app.modules.chat import chat_tools    # noqa: E402
from app.ai import ai_cooldown  # noqa: E402
from _packtest import reference_pack_test  # noqa: E402

FAKE_USER = {"dni": "30123456", "nombre": "Juan", "apellido": "Perez"}

# 1x1 PNG transparente (misma que test_booking_requests.py) — para analyze_order.
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# Las tools que tocan el disco (analyze_order) no deben escribir en el
# data/ordenes/ real del checkout — mismo patrón que test_booking_requests.py.
_ORIG_ORDENES_DIR = booking_requests.REFERRALS_DIR


_APP = FastAPI()
_APP.include_router(mcp_api_router.router)
_CLIENT = TestClient(_APP)


def _rpc(method, params=None, rid=1, headers=None):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return _CLIENT.post("/mcp", json=msg, headers=headers or {})


# ---- protocolo (sin base) ----------------------------------------------------

class TestProtocolo(unittest.TestCase):
    def setUp(self):
        mcp_api._HITS.clear()
        _env.set_env(self, TURNOS_MCP_PER_HOUR=None)

    def test_initialize(self):
        h = _rpc("initialize", {"protocolVersion": "2025-06-18"})
        r = h.json()["result"]
        self.assertIn(r["protocolVersion"], mcp_api.PROTO_SOPORTADOS)
        self.assertEqual(r["serverInfo"]["name"], "human-rounds")
        self.assertTrue(r["instructions"])
        self.assertIn("tools", r["capabilities"])

    def test_generic_protocol_metadata_has_no_reference_deployment(self):
        with patch("app.core.site_identity.get",
                   return_value="Sampleton public health network"), \
             patch.object(mcp_api.theme, "brand_name",
                          return_value="Human Rounds"):
            result = _rpc("initialize", {
                "protocolVersion": "2025-06-18",
            }).json()["result"]
        encoded = json.dumps({
            "initialize": result,
            "tools": chat_tools.TOOLS,
        }, ensure_ascii=False).lower()
        self.assertNotIn("pinamar", encoded)
        self.assertIn("sampleton", encoded)

    def test_initialize_proto_desconocido_cae_al_default(self):
        h = _rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(h.json()["result"]["protocolVersion"], mcp_api.PROTO_DEFAULT)

    def test_ping(self):
        h = _rpc("ping")
        self.assertEqual(h.json()["result"], {})

    def test_tools_list(self):
        h = _rpc("tools/list")
        tools = h.json()["result"]["tools"]
        nombres = {t["name"] for t in tools}
        # search_doctors / my_lab_results stay refused by the guard, so they are not offered (2026-11-13)
        self.assertEqual(nombres, {"search_services", "available_appointments",
                                   "request_appointment", "my_requests",
                                   "cancel_request", "whoami", "analyze_order"})
        analyze = next(t for t in tools if t["name"] == "analyze_order")
        self.assertEqual(analyze["_meta"]["openai/fileParams"], ["file"])
        file_schema = analyze["inputSchema"]["properties"]["file"]
        self.assertEqual(set(file_schema["properties"]),
                         {"download_url", "file_id", "mime_type", "file_name"})
        self.assertEqual(file_schema["required"], ["download_url", "file_id"])
        self.assertIn({"required": ["data_b64"]}, analyze["inputSchema"]["anyOf"])

    def test_metodo_desconocido(self):
        h = _rpc("no/existe")
        self.assertEqual(h.json()["error"]["code"], -32601)

    def test_get_no_soportado_405(self):
        h = _CLIENT.get("/mcp")
        self.assertEqual(h.status_code, 405)
        self.assertEqual(h.headers.get("Allow"), "POST")

    def test_json_invalido(self):
        h = _CLIENT.post("/mcp", content=b"{no soy json")
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"]["code"], -32700)

    def test_batch_no_soportado(self):
        h = _CLIENT.post(
            "/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"]["code"], -32600)

    def test_notificacion_202_sin_cuerpo(self):
        h = _rpc("notifications/initialized", rid=None)
        self.assertEqual(h.status_code, 202)
        self.assertEqual(h.content, b"")

    def test_ruta_ajena_no_maneja(self):
        self.assertEqual(_CLIENT.post("/api/otra-cosa", json={}).status_code, 404)


# ---- auth de tools/call (identidad monkeypatcheada) --------------------------

class TestToolsCallAuth(unittest.TestCase):
    def setUp(self):
        mcp_api._HITS.clear()
        _env.set_env(self, TURNOS_MCP_PER_HOUR=None)
        self._orig = mcp_api._usuario_por_token

    def tearDown(self):
        mcp_api._usuario_por_token = self._orig

    def _call(self, name, args=None, token="tok-valido"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return _rpc("tools/call", {"name": name, "arguments": args or {}}, headers=headers)

    def test_sin_token_401(self):
        h = self._call("search_services", token=None)
        self.assertEqual(h.status_code, 401)
        self.assertIn("Bearer", h.headers.get("WWW-Authenticate") or "")
        self.assertEqual(h.json()["error"]["code"], -32001)

    def test_token_invalido_401(self):
        mcp_api._usuario_por_token = lambda t: None
        h = self._call("search_services")
        self.assertEqual(h.status_code, 401)

    def test_base_caida_503(self):
        from app.core import db

        def boom(t):
            raise db.DbUnavailable("base caída")
        mcp_api._usuario_por_token = boom
        h = self._call("search_services")
        self.assertEqual(h.status_code, 503)

    def test_otra_falla_al_leer_el_token_es_500(self):
        # The product owner's hub, 2026-11-10: only a database that does not answer is a 503 «servicio no disponible»
        def boom(t):
            raise RuntimeError("otra cosa")
        mcp_api._usuario_por_token = boom
        h = self._call("search_services")
        self.assertEqual(h.status_code, 500)
        self.assertEqual(h.json()["error"]["code"], -32603)

    def test_tool_desconocida(self):
        mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)
        h = self._call("no_existe")
        self.assertEqual(h.json()["error"]["code"], -32602)

    def test_buscar_servicios_happy_path(self):
        # catálogo real desde webdata (sin base); sólo comprobamos la forma MCP.
        mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)
        h = self._call("search_services", {"query": "cardiolog"})
        res = h.json()["result"]
        self.assertIn("content", res)
        self.assertEqual(res["content"][0]["type"], "text")
        payload = json.loads(res["content"][0]["text"])
        self.assertTrue("resultados" in payload or "error" in payload)
        self.assertFalse(res["isError"])

    def test_buscar_respeta_palabras_excluidas(self):
        # Reunión municipal 2026-07-17: un servicio con `palabras_excluidas`
        # no aparece en ESA búsqueda (ni en el chat/MCP), sí en las demás.
        from app.patient_web import catalog
        from app.modules.chat import chat_tools
        fake = [
            {"id": "gine", "nombre": "Ginecología", "centro": "hospital",
             "modo": "solicitud", "palabras": ["mujer"],
             "palabras_excluidas": ["PAP"]},
            {"id": "pap", "nombre": "PAP", "centro": "hospital",
             "modo": "solicitud", "palabras": ["papanicolaou"]},
        ]
        orig = catalog.services
        catalog.services = lambda: fake
        try:
            mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)
            res = chat_tools._tool_search(dict(FAKE_USER), {"query": "pap"}, None)
            nombres = [r.get("nombre") for r in res["resultados"]]
            self.assertIn("PAP", nombres)
            self.assertNotIn("Ginecología", nombres)
            res = chat_tools._tool_search(dict(FAKE_USER), {"query": "mujer"}, None)
            nombres = [r.get("nombre") for r in res["resultados"]]
            self.assertIn("Ginecología", nombres)
        finally:
            catalog.services = orig

    def test_buscar_sin_consulta_es_error_de_tool(self):
        mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)
        h = self._call("search_services", {})
        res = h.json()["result"]
        self.assertTrue(res["isError"])  # {"error": ...} → isError True

    def test_rate_limit_429(self):
        _env.set_env(self, TURNOS_MCP_PER_HOUR="1")
        mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)
        self._call("search_services", {"query": "x"})       # consume el único
        h = self._call("search_services", {"query": "x"})   # segundo → 429
        self.assertEqual(h.status_code, 429)
        self.assertEqual(h.json()["error"]["code"], -32002)


# ---- whoami --------------------------------------------------------------------

class TestWhoami(unittest.TestCase):
    def setUp(self):
        mcp_api._HITS.clear()
        self._orig = mcp_api._usuario_por_token

    def tearDown(self):
        mcp_api._usuario_por_token = self._orig

    def _call(self, args=None):
        return _rpc("tools/call", {"name": "whoami", "arguments": args or {}},
                    headers={"Authorization": "Bearer tok"})

    def test_forma_e_identidad(self):
        usuario = dict(FAKE_USER, nacimiento="1980-01-01", sexo="M",
                       contacto="+54 9 2255 111111", pass_hash="no-debe-salir")
        mcp_api._usuario_por_token = lambda t: usuario
        h = self._call()
        payload = json.loads(h.json()["result"]["content"][0]["text"])
        self.assertEqual(payload["dni"], FAKE_USER["dni"])
        self.assertEqual(payload["nombre"], "Juan")
        self.assertTrue(payload["tiene_contacto"])
        self.assertFalse(payload["tiene_email"])
        self.assertIn("dependientes", payload)
        self.assertIsInstance(payload["dependientes"], list)
        # nunca se filtra el hash ni ningún token
        blob = json.dumps(payload)
        self.assertNotIn("pass_hash", blob)
        self.assertNotIn("no-debe-salir", blob)


# ---- analyze_order (lee la orden con IA fake) -----------------------------------

class TestAnalyzeOrderTool(unittest.TestCase):
    def setUp(self):
        mcp_api._HITS.clear()
        ai_cooldown._reset_para_tests()
        self._orig = mcp_api._usuario_por_token
        mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)

    def tearDown(self):
        mcp_api._usuario_por_token = self._orig
        ai_cooldown._reset_para_tests()

    def _call(self, args):
        return _rpc("tools/call", {"name": "analyze_order", "arguments": args},
                    headers={"Authorization": "Bearer tok"})

    def test_lee_la_orden_y_devuelve_token(self):
        h = self._call({"filename": "orden.png", "mime": "image/png",
                        "data_b64": _PNG_B64, "service_id": "cardiologia"})
        res = h.json()["result"]
        payload = json.loads(res["content"][0]["text"])
        self.assertFalse(res["isError"], payload)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["orden_token"])
        self.assertEqual(payload["veredicto"], "parece_correcta")
        self.assertIn("campos", payload)
        # el token quedó persistido del lado del server (pending/<token>.json)
        meta = booking_referrals._load_pending(payload["orden_token"])
        self.assertIsNotNone(meta)

    def test_lee_archivo_adjunto_nativo_y_devuelve_token(self):
        file_ref = {
            "download_url": "https://files.oaiusercontent.com/scan/orden",
            "file_id": "file-orden",
            "mime_type": "image/png",
            "file_name": "orden.png",
        }
        with patch.object(chat_tools, "_download_openai_file",
                          return_value=(
                              base64.b64decode(_PNG_B64),
                              "image/png", "orden.png")) as download:
            h = self._call({"file": file_ref, "service_id": "cardiologia"})
        payload = json.loads(h.json()["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"], payload)
        download.assert_called_once_with(file_ref, booking_requests.MAX_REFERRAL_BYTES)

    def test_sin_archivo_es_error_de_tool(self):
        h = self._call({})
        res = h.json()["result"]
        self.assertTrue(res["isError"])
        payload = json.loads(res["content"][0]["text"])
        self.assertIn("file o data_b64", payload["error"])

    def test_rechaza_url_de_archivo_fuera_del_cdn(self):
        h = self._call({"file": {
            "download_url": "http://127.0.0.1/secreto",
            "file_id": "file-mal",
        }})
        res = h.json()["result"]
        self.assertTrue(res["isError"])
        payload = json.loads(res["content"][0]["text"])
        self.assertIn("URL", payload["error"])

    def test_enfriamiento_bloquea_tras_la_rafaga(self):
        _env.set_env(self, TURNOS_IMG_BURST="1", TURNOS_IMG_BASE_SECONDS="600")
        args = {"filename": "orden.png", "mime": "image/png", "data_b64": _PNG_B64}
        h1 = self._call(args)
        self.assertFalse(h1.json()["result"]["isError"])
        h2 = self._call(args)  # mismo dni (identidad) -> segunda análisis bloqueado
        res2 = h2.json()["result"]
        self.assertTrue(res2["isError"])
        payload2 = json.loads(res2["content"][0]["text"])
        self.assertEqual(payload2["error"], "enfriamiento")
        self.assertGreater(payload2["retry_after_seconds"], 0)


class TestOpenAIFileDownload(unittest.TestCase):
    class _Response:
        def __init__(self, data, headers=None,
                     url="https://files.oaiusercontent.com/scan/orden"):
            self._data = data
            self.headers = headers or {}
            self._url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return self._url

        def read(self, limit):
            return self._data[:limit]

    class _Opener:
        def __init__(self, response):
            self.response = response

        def open(self, _req, timeout):
            self.timeout = timeout
            return self.response

    def test_descarga_referencia_nativa(self):
        response = self._Response(
            b"\x89PNG\r\n\x1a\nresto",
            {"Content-Length": "13", "Content-Type": "image/png; charset=binary"})
        with patch("app.modules.chat.chat_tools.urllib.request.build_opener",
                   return_value=self._Opener(response)):
            data, mime, name = chat_tools._download_openai_file({
                "download_url": "https://files.oaiusercontent.com/scan/orden",
                "file_id": "file-orden",
                "file_name": "orden.png",
            }, 100)
        self.assertEqual(data, b"\x89PNG\r\n\x1a\nresto")
        self.assertEqual(mime, "image/png")
        self.assertEqual(name, "orden.png")

    def test_rechaza_descarga_declarada_demasiado_grande(self):
        response = self._Response(b"x", {"Content-Length": "101"})
        with patch("app.modules.chat.chat_tools.urllib.request.build_opener",
                   return_value=self._Opener(response)):
            with self.assertRaisesRegex(ValueError, "pesado"):
                chat_tools._download_openai_file({
                    "download_url": "https://files.oaiusercontent.com/scan/orden",
                    "file_id": "file-orden",
                }, 100)

    def test_rechaza_host_parecido(self):
        self.assertFalse(chat_tools._url_archivo_openai(
            "https://files.oaiusercontent.com.evil.example/orden"))
        self.assertFalse(chat_tools._url_archivo_openai(
            "https://user@files.oaiusercontent.com/orden"))


# ---- request_appointment: el gate de servicios con orden médica ----------------

class TestRequestAppointmentOrdenGate(unittest.TestCase):
    """El gate en sí (bloquear walkin siempre; bloquear con_orden sin token) no
    necesita Postgres — sólo llega hasta ANTES de tocar booking_requests._create. El
    happy-path que SÍ crea la solicitud vive en TestRequestConOrdenDB (con
    Postgres) más abajo."""

    def setUp(self):
        mcp_api._HITS.clear()
        self._orig_token = mcp_api._usuario_por_token
        mcp_api._usuario_por_token = lambda t: dict(FAKE_USER)
        self._orig_servicio = site_catalog._service_by_id

    def tearDown(self):
        mcp_api._usuario_por_token = self._orig_token
        site_catalog._service_by_id = self._orig_servicio

    def _call(self, args):
        return _rpc("tools/call", {"name": "request_appointment", "arguments": args},
                    headers={"Authorization": "Bearer tok"})

    def test_walkin_bloqueado_siempre_incluso_con_orden_token(self):
        site_catalog._service_by_id = lambda sid: {
            "id": sid, "modo": "walkin", "centro": "hospital", "horario": "8 a 12"}
        h = self._call({"service_id": "guardia", "orden_token": "lo-que-sea"})
        payload = json.loads(h.json()["result"]["content"][0]["text"])
        self.assertIn("orden de llegada", payload["error"])

    def test_con_orden_sin_token_bloqueado(self):
        site_catalog._service_by_id = lambda sid: {
            "id": sid, "modo": "solicitud_con_orden", "centro": "hospital"}
        h = self._call({"service_id": "cardiologia"})
        payload = json.loads(h.json()["result"]["content"][0]["text"])
        self.assertIn("ORDEN MÉDICA", payload["error"])
        self.assertIn("como_seguir", payload)

    def test_con_orden_token_invalido_bloqueado(self):
        site_catalog._service_by_id = lambda sid: {
            "id": sid, "modo": "solicitud_con_orden", "centro": "hospital"}
        h = self._call({"service_id": "cardiologia", "orden_token": "no-existe-este-token"})
        payload = json.loads(h.json()["result"]["content"][0]["text"])
        self.assertIn("orden_token", payload["error"])


class TestBearerYRateLimit(unittest.TestCase):
    def test_bearer_parsea(self):
        headers = {"Authorization": "Bearer abc123"}
        self.assertEqual(mcp_api._bearer(headers), "abc123")

    def test_bearer_ausente(self):
        self.assertIsNone(mcp_api._bearer({}))

    def test_permitido_corta_en_el_limite(self):
        mcp_api._HITS.clear()
        self.addCleanup(mcp_api._HITS.clear)
        _env.set_env(self, TURNOS_MCP_PER_HOUR="2")
        self.assertTrue(mcp_api._permitido("t"))
        self.assertTrue(mcp_api._permitido("t"))
        self.assertFalse(mcp_api._permitido("t"))


# ---- token del conector (persiste) -------------------------------------------

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_mcp", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


class TestToken(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        self._dni = "41555666"
        from app.cuentas import accounts
        accounts._register_core(
            {"dni": self._dni, "password": "secreta1", "nombre": "Ema",
             "apellido": "Ruiz", "email": "ema@ejemplo.com"},
            "127.0.0.11")

    def test_con_token_no_crea_otro_y_solo_muestra_los_ultimos_cuatro(self):
        # The module's database outlives each test: regenerate gives this test its own token.
        t = db_accounts.mcp_token_regenerate(self._dni)
        self.assertTrue(t)
        visto = db_accounts.mcp_token_issue(self._dni)
        self.assertEqual(visto, {"token": None, "hint": t[-4:], "last_used": None})  # la base no lo tiene en claro
        self.assertEqual(db_accounts.mcp_token_issue(self._dni), visto)  # no crea otro
        self.assertEqual(db_accounts.user_by_mcp_token(t)["dni"], self._dni)

    def test_resuelve_usuario(self):
        t = db_accounts.mcp_token_regenerate(self._dni)
        u = db_accounts.user_by_mcp_token(t)
        self.assertIsNotNone(u)
        self.assertEqual(u["dni"], self._dni)
        self.assertNotIn("pass_hash", u)  # _usuario_public no filtra el hash

    def test_regenerar_revoca_el_anterior(self):
        viejo = db_accounts.mcp_token_regenerate(self._dni)
        nuevo = db_accounts.mcp_token_regenerate(self._dni)
        self.assertNotEqual(viejo, nuevo)
        self.assertIsNone(db_accounts.user_by_mcp_token(viejo))  # el viejo ya no resuelve
        self.assertIsNotNone(db_accounts.user_by_mcp_token(nuevo))

    def test_token_ausente_no_resuelve(self):
        self.assertIsNone(db_accounts.user_by_mcp_token(""))
        self.assertIsNone(db_accounts.user_by_mcp_token("no-existe"))

    def test_dni_inexistente(self):
        self.assertIsNone(db_accounts.mcp_token_issue("99999999"))

    def test_presupuesto_diario_es_atomico_y_persistente(self):
        from datetime import date
        dia = date(2099, 1, 2)
        self.assertEqual(db_ai.mcp_ai_reserve(2, dia=dia), 1)
        self.assertEqual(db_ai.mcp_ai_reserve(2, dia=dia), 2)
        self.assertIsNone(db_ai.mcp_ai_reserve(2, dia=dia))


# ---- request_appointment con orden_token: el happy-path (necesita Postgres) ---

class TestRequestConOrdenDB(unittest.TestCase):
    """`ecografia-hospital` es un servicio 'solicitud_con_orden' REAL del
    catálogo (webdata/servicios.json, sin límite de edad) — se usa tal cual
    en vez de monkeypatchear el catálogo, porque acá SÍ pasa por
    booking_requests._create (que valida contra el catálogo real)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        ai_cooldown._reset_para_tests()
        mcp_api._HITS.clear()
        self._dni = "40777888"
        from app.cuentas import accounts
        accounts._register_core(
            {"dni": self._dni, "password": "secreta1", "nombre": "Rosa",
             "apellido": "Vega", "email": "rosa@ejemplo.com"},
            "127.0.0.12")
        self._token = db_accounts.mcp_token_regenerate(self._dni)

    def tearDown(self):
        ai_cooldown._reset_para_tests()

    def _call(self, name, args):
        return _rpc("tools/call", {"name": name, "arguments": args},
                    headers={"Authorization": f"Bearer {self._token}"})

    @reference_pack_test
    def test_con_orden_token_valido_crea_la_solicitud(self):
        h_analyze = self._call("analyze_order", {
            "filename": "orden.png", "mime": "image/png", "data_b64": _PNG_B64,
            "service_id": "ecografia-hospital"})
        analyze_payload = json.loads(h_analyze.json()["result"]["content"][0]["text"])
        self.assertTrue(analyze_payload["ok"], analyze_payload)
        orden_token = analyze_payload["orden_token"]

        h_req = self._call("request_appointment", {
            "service_id": "ecografia-hospital", "orden_token": orden_token})
        res = h_req.json()["result"]
        payload = json.loads(res["content"][0]["text"])
        # El conector solo propone: la solicitud se crea en la web, con el login
        # del paciente (ToolInvocation(mcp_ai_guarded=True) nunca confirma).
        self.assertFalse(res["isError"], payload)
        self.assertTrue(payload["confirmation_required"], payload)
        self.assertNotIn("solicitud_id", payload)


if __name__ == "__main__":
    unittest.main()
