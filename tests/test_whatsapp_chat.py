"""test_whatsapp_chat.py — canal de WhatsApp (whatsapp_chat.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_whatsapp_chat.py"

Cubre: el gate del secreto compartido del webhook, fromMe/grupo ignorados,
el matcheo de teléfono (único/ambiguo/desconocido, variantes +54/9),
media -> analyze_order, y el código de traspaso web->WhatsApp (válido /
vencido / basura, con rate-limit de intentos fallidos por teléfono) — sin
tocar nunca la identidad de reserva (que sigue siendo 100% el teléfono).
"""

import os
import sys
import json
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.modules.chat import whatsapp_chat, whatsapp_chat_router  # noqa: E402
from app.notifications import otp_send         # noqa: E402


def _msg_event(**over):
    payload = {
        "id": "abc", "from": "5492255111222@c.us", "fromMe": False,
        "body": "hola", "hasMedia": False,
    }
    payload.update(over)
    return {"event": "message", "session": "default", "payload": payload}


class NativeWebhookTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Registered before set_env: cleanups run last-in first-out, so the config cache is dropped after the environment is back.
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, ENABLED_MODULES="turnos,chat")
        config._reset_cache_para_tests()
        app = FastAPI()
        app.include_router(whatsapp_chat_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def _post(self, body):
        return self.client.post(
            "/api/whatsapp/webhook",
            json=body,
            headers={"X-Webhook-Secret": "s"},
        )


class TestGateDelSecreto(NativeWebhookTestCase):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET=None)

    def test_sin_env_el_endpoint_no_existe(self):
        response = self.client.post("/api/whatsapp/webhook", json=_msg_event())
        self.assertEqual(response.status_code, 404)

    def test_con_env_secreto_incorrecto_401(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="correcto")
        response = self.client.post(
            "/api/whatsapp/webhook", json=_msg_event(),
            headers={"X-Webhook-Secret": "incorrecto"})
        self.assertEqual(response.status_code, 401)

    def test_con_env_secreto_correcto_por_header(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="correcto")
        response = self.client.post(
            "/api/whatsapp/webhook", json=_msg_event(),
            headers={"X-Webhook-Secret": "correcto"})
        self.assertEqual(response.status_code, 200)

    def test_secreto_por_query_string(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="correcto")
        response = self.client.post(
            "/api/whatsapp/webhook?secret=correcto", json=_msg_event())
        self.assertEqual(response.status_code, 200)

    def test_secreto_por_bearer(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="correcto")
        response = self.client.post(
            "/api/whatsapp/webhook", json=_msg_event(),
            headers={"Authorization": "Bearer correcto"})
        self.assertEqual(response.status_code, 200)

    def test_metodo_get_sin_hub_params_403(self):
        # GET es el handshake de verificación de Meta (ver TestVerificacionMeta
        # más abajo) — SÍ lo maneja (a diferencia de antes de que Meta se
        # sumara), pero sin los query params de Meta no hay nada que verificar.
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="correcto")
        response = self.client.get("/api/whatsapp/webhook")
        self.assertEqual(response.status_code, 403)

    def test_metodo_put_no_maneja(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="correcto")
        response = self.client.put("/api/whatsapp/webhook")
        self.assertEqual(response.status_code, 405)


class TestVerificacionMeta(NativeWebhookTestCase):
    """GET /api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=...
    &hub.challenge=... — el handshake que Meta exige antes de aceptar la URL
    del webhook en su panel."""

    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="el-secreto")

    def _get(self, qs):
        return self.client.get("/api/whatsapp/webhook?" + qs)

    def test_token_correcto_devuelve_el_challenge(self):
        response = self._get("hub.mode=subscribe&hub.verify_token=el-secreto&hub.challenge=123456")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "123456")

    def test_token_incorrecto_403(self):
        response = self._get("hub.mode=subscribe&hub.verify_token=otro&hub.challenge=123456")
        self.assertEqual(response.status_code, 403)

    def test_modo_distinto_de_subscribe_403(self):
        response = self._get("hub.mode=unsubscribe&hub.verify_token=el-secreto&hub.challenge=1")
        self.assertEqual(response.status_code, 403)

    def test_sin_secreto_configurado_no_maneja(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET=None)
        response = self._get("hub.mode=subscribe&hub.verify_token=x&hub.challenge=1")
        self.assertEqual(response.status_code, 404)


class TestFormaYNormalizacion(unittest.TestCase):
    """Distinguir Meta de WAHA por forma, e ignorar statuses-only/eco."""

    def test_es_meta_por_object(self):
        self.assertTrue(whatsapp_chat._es_meta({"object": "whatsapp_business_account"}))
        self.assertFalse(whatsapp_chat._es_meta({"event": "message"}))
        self.assertFalse(whatsapp_chat._es_meta({}))

    def test_meta_texto_se_normaliza(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "messages": [{"from": "5492255111222", "type": "text",
                             "text": {"body": "hola"}}],
            }}]}],
        }
        mensajes = whatsapp_chat._normalizar_meta(body)
        self.assertEqual(len(mensajes), 1)
        m = mensajes[0]
        self.assertEqual(m["from"], "5492255111222")
        self.assertEqual(m["body"], "hola")
        self.assertIsNone(m["media"])
        self.assertFalse(m["from_me"])
        self.assertFalse(m["is_group"])

    def test_meta_statuses_only_se_ignora(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "statuses", "value": {
                "statuses": [{"id": "wamid.x", "status": "delivered"}],
            }}]}],
        }
        self.assertEqual(whatsapp_chat._normalizar_meta(body), [])

    def test_meta_imagen_trae_media_con_provider_meta(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "messages": [{"from": "5492255111222", "type": "image",
                             "image": {"id": "media123", "mime_type": "image/jpeg"}}],
            }}]}],
        }
        mensajes = whatsapp_chat._normalizar_meta(body)
        self.assertEqual(mensajes[0]["media"], {
            "provider": "meta", "media_id": "media123",
            "mime_type": "image/jpeg", "filename": None,
        })

    def test_meta_mensaje_sin_from_se_ignora(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "messages": [{"type": "text", "text": {"body": "hola"}}],
            }}]}],
        }
        self.assertEqual(whatsapp_chat._normalizar_meta(body), [])

    def test_waha_texto_se_normaliza(self):
        mensajes = whatsapp_chat._normalizar_waha(_msg_event(body="hola"))
        self.assertEqual(len(mensajes), 1)
        self.assertEqual(mensajes[0]["from"], "5492255111222")
        self.assertEqual(mensajes[0]["body"], "hola")


class TestFromMeYGrupo(NativeWebhookTestCase):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="s")
        self._enviados = []
        self._patch = patch.object(otp_send, "enviar_texto",
                                   side_effect=lambda d, t: self._enviados.append((d, t)) or True)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        super().tearDown()

    def test_fromme_se_ignora(self):
        h = self._post(_msg_event(fromMe=True))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(self._enviados, [])

    def test_grupo_se_ignora(self):
        h = self._post(_msg_event(**{"from": "12036302xxxx-xxxx@g.us"}))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(self._enviados, [])

    def test_evento_no_message_se_ignora(self):
        ev = _msg_event()
        ev["event"] = "session.status"
        h = self._post(ev)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(self._enviados, [])

    def test_telefono_desconocido_responde_invitacion(self):
        h = self._post(_msg_event(**{"from": "5492255000999@c.us"}))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._enviados), 1)
        destino, texto = self._enviados[0]
        self.assertIn("2255000999", destino)
        self.assertIn("registrate", texto.lower())


class TestExtraerCode(unittest.TestCase):
    def test_encuentra_el_codigo_formato_legado_de_6(self):
        # Códigos de 6 emitidos ANTES de la ronda 2026-07-23 (db lengthened
        # 6->12) siguen bindeando mientras estén vigentes — no rompemos
        # códigos ya en vuelo por endurecer el formato.
        self.assertEqual(whatsapp_chat._extraer_code("hola mi código es ST-AB23CD"), "ST-AB23CD")

    def test_encuentra_el_codigo_formato_nuevo_de_12(self):
        self.assertEqual(
            whatsapp_chat._extraer_code("hola mi código es ST-AB23CD45EFGH"),
            "ST-AB23CD45EFGH")

    def test_case_insensitive(self):
        self.assertEqual(whatsapp_chat._extraer_code("st-ab23cd45efgh"), "ST-AB23CD45EFGH")

    def test_sin_codigo(self):
        self.assertIsNone(whatsapp_chat._extraer_code("hola como va"))

    def test_no_matchea_mas_de_12_chars(self):
        # 13 chars corridos: no hay boundary donde el regex pueda cerrar sin
        # cortar en medio de una racha de caracteres de palabra -> ningún
        # código válido de este formato mide más de 12.
        self.assertIsNone(whatsapp_chat._extraer_code("ST-AB23CD45EFGHI"))


class TestTelDe(unittest.TestCase):
    def test_de_chat_id_whatsapp(self):
        self.assertEqual(whatsapp_chat._tel_de("5492255111222@c.us"), "5492255111222")

    def test_sin_sufijo(self):
        self.assertEqual(whatsapp_chat._tel_de("2255111222"), "2255111222")


class TestIndiceOpcion(unittest.TestCase):
    """_indice_opcion/_mapear_opcion son puras: mapear "1"/"a" a la opción
    real que las trajo, ANTES de que el texto llegue al motor (2026-08-01,
    quick-reply options)."""

    def test_numero_1_indexado_es_indice_0(self):
        self.assertEqual(whatsapp_chat._indice_opcion("1"), 0)

    def test_numero_2(self):
        self.assertEqual(whatsapp_chat._indice_opcion("2"), 1)

    def test_letra_a_es_indice_0(self):
        self.assertEqual(whatsapp_chat._indice_opcion("a"), 0)

    def test_letra_mayuscula_y_con_punto(self):
        self.assertEqual(whatsapp_chat._indice_opcion("B."), 1)

    def test_numero_con_parentesis(self):
        self.assertEqual(whatsapp_chat._indice_opcion("3)"), 2)

    def test_frase_normal_no_es_un_indice(self):
        self.assertIsNone(whatsapp_chat._indice_opcion("quiero el turno de mañana"))

    def test_vacio_no_es_un_indice(self):
        self.assertIsNone(whatsapp_chat._indice_opcion(""))

    def test_cero_no_es_un_indice_valido(self):
        # las opciones se numeran desde 1 — "0" no matchea ninguna.
        self.assertIsNone(whatsapp_chat._indice_opcion("0"))

    def test_mapear_reemplaza_por_el_texto_de_la_opcion(self):
        opciones = ["Hospital de Pinamar", "CAPS Ostende"]
        self.assertEqual(whatsapp_chat._mapear_opcion("1", opciones), "Hospital de Pinamar")
        self.assertEqual(whatsapp_chat._mapear_opcion("2", opciones), "CAPS Ostende")
        self.assertEqual(whatsapp_chat._mapear_opcion("b", opciones), "CAPS Ostende")

    def test_mapear_fuera_de_rango_deja_el_texto_intacto(self):
        opciones = ["Sí", "No"]
        self.assertEqual(whatsapp_chat._mapear_opcion("5", opciones), "5")

    def test_mapear_sin_opciones_ofrecidas_deja_el_texto_intacto(self):
        self.assertEqual(whatsapp_chat._mapear_opcion("1", []), "1")

    def test_mapear_una_frase_nunca_se_reinterpreta(self):
        opciones = ["Sí", "No"]
        self.assertEqual(whatsapp_chat._mapear_opcion("no puedo ir", opciones), "no puedo ir")


# ---- lo que necesita Postgres: matcheo de teléfono, handoff, media --------

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_whatsapp_chat", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


# Monotonic per-registration peer IP so every account this module creates
# keys to its OWN accounts._register throttle bucket — STRUCTURAL isolation
# (a distinct IP by construction) instead of clearing a shared global. A
# markerless handler would otherwise key to "" (under TURNOS_ASGI=1 every
# proxy-less fixture in the process resolves to "" and shares one bucket),
# and enough registrations across the discover run would trip the
# 10-accounts/hour cap. Runtime code (_register/_register_throttled) is
# untouched; only the fixture's peer identity changes.
_PEER_SEQ = [0]


def _next_peer_ip():
    # Injective for n < 254*256*256 (~16.6M registrations): the last octet is
    # a base-254 digit offset into 2..255, so no two sequence values can ever
    # fold to the same address. The previous `(n & 0xFF) or 2` remapped every
    # 256th value's last octet to 2 and collided with a real .2 neighbor
    # (n=256 -> 127.0.1.2, same as n=258), sharing a throttle bucket every
    # 256th registration. Octets 0 and 1 stay excluded from the last position
    # on purpose: never a .0 pseudo-network address, never exactly 127.0.0.1.
    _PEER_SEQ[0] += 1
    n = _PEER_SEQ[0]
    return "127.%d.%d.%d" % (
        (n // (254 * 256)) % 256, (n // 254) % 256, n % 254 + 2)


class TestPeerIpGeneratorCollisionFree(unittest.TestCase):
    """Regression for the every-256th peer-IP fold (Tier 4 review, 2026-08-08):
    the old generator's `(n & 0xFF) or 2` collided every 256th value with an
    earlier .2 address, silently sharing a registration-throttle bucket. No
    database needed — this asserts on the generator alone."""

    def test_distinct_well_beyond_the_old_collision_boundary(self):
        count = 3 * 256 + 8  # spans the old fold three times over
        ips = [_next_peer_ip() for _ in range(count)]
        self.assertEqual(len(set(ips)), count,
                         "generated peer IPs collided within %d values" % count)
        for ip in ips:
            octets = [int(x) for x in ip.split(".")]
            self.assertEqual(octets[0], 127)
            self.assertTrue(all(0 <= o <= 255 for o in octets), ip)
            self.assertGreaterEqual(octets[3], 2,
                                    "last octet must avoid .0 and 127.0.0.1")


class TestMatcheoTelefono(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        from app.cuentas import accounts
        self._accounts = accounts
        # Real signing secret so the per-registration marker verifies under a
        # TURNOS_ASGI=1 CI pass (proxy_trust trusts a marker only with a real
        # secret). Put back after the test so it never leaks to other modules.
        _env.set_env(self, {"TURNOS_PROXY_TRUST_SECRET": "test-proxy-trust-whatsapp"})

    def _registrar(self, dni, contacto, email):
        ip = _next_peer_ip()
        self._accounts._register_core(
            {"dni": dni, "password": "secreta1", "nombre": "T",
             "apellido": "U", "contacto": contacto, "email": email}, ip)

    def test_match_unico_formatos_variados(self):
        self._registrar("30001111", "+54 9 2255 111333", "u1@ejemplo.com")
        # el mismo teléfono, distintos formatos de entrada
        for candidato in ("5492255111333", "542255111333", "2255-111333", "2255111333"):
            u = db_chat.user_by_phone(candidato)
            self.assertIsNotNone(u, candidato)
            self.assertEqual(u["dni"], "30001111")

    def test_sin_match_desconocido(self):
        self.assertIsNone(db_chat.user_by_phone("5492255999888"))

    def test_ambiguo_dos_cuentas_mismos_10_digitos_no_matchea(self):
        self._registrar("30002222", "+54 9 2255 222444", "u2@ejemplo.com")
        self._registrar("30003333", "2255-222444", "u3@ejemplo.com")  # mismos 10 dígitos
        self.assertIsNone(db_chat.user_by_phone("5492255222444"))


_CONTADOR_DNI = [0]


class TestWebhookConDB(NativeWebhookTestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        super().setUp()
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="s", TURNOS_WA_HANDOFF_PER_HOUR=None)
        self._enviados = []
        self._patch = patch.object(otp_send, "enviar_texto",
                                   side_effect=lambda d, t: self._enviados.append((d, t)) or True)
        self._patch.start()
        from app.cuentas import accounts
        self._accounts = accounts
        # El alta tiene un freno por IP (accounts._REGISTERED, 10 cuentas por
        # hora — 2026-08-01, revisión de seguridad de la demo pública). Es
        # estado en memoria de PROCESO, no de test: sin este clear la 11ª cuenta que
        # se da de alta en todo el proceso (contando otras clases de este
        # mismo archivo) vuelve 429 y el test queda con un teléfono sin
        # cuenta. Mismo patrón que test_accounts.py.
        accounts._REGISTERED.clear()
        # DNI/teléfono ÚNICOS por test: la base persiste entre tests de la
        # misma clase (una sola conexión al mismo Postgres de test), así que
        # reusar un DNI fijo choca con "dni_registrado" a partir del 2º test.
        _CONTADOR_DNI[0] += 1
        n = _CONTADOR_DNI[0]
        self._dni = f"3000{n:04d}"
        self._tel = f"549225500{n:04d}"
        accounts._register_core(
            {"dni": self._dni, "password": "secreta1", "nombre": "Marta",
             "apellido": "Diaz", "contacto": self._tel,
             "email": f"marta{n}@ejemplo.com"}, _next_peer_ip())

    def tearDown(self):
        self._patch.stop()
        whatsapp_chat._INTENTOS_FALLIDOS.clear()
        self._accounts._REGISTERED.clear()
        super().tearDown()

    def test_usuario_matcheado_recibe_respuesta_del_motor(self):
        h = self._post(_msg_event(**{"from": f"{self._tel}@c.us", "body": "hola"}))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._enviados), 2)
        self.assertIn('inteligencia artificial', self._enviados[0][1])
        self.assertTrue(self._enviados[-1][1])  # respuesta después del aviso

    def test_respuesta_numerica_se_mapea_a_la_opcion_antes_del_motor(self):
        # Integración de punta a punta (2026-08-01, quick-reply options):
        # el motor de WhatsApp (bajo TURNOS_FAKE_AI) hace eco literal del
        # texto que recibe ("Recibido (fake): <texto>") — clavamos que lo
        # que recibió es la OPCIÓN, no el "1" bare que mandó la persona.
        db_chat.chat_save_message(
            "whatsapp", f"tel:{self._tel}",
            {"role": "assistant",
             "content": "Elegí un centro:\n[opciones: Hospital de Pinamar | CAPS Ostende]"})
        h = self._post(_msg_event(**{"from": f"{self._tel}@c.us", "body": "1"}))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._enviados), 2)
        self.assertIn("Hospital de Pinamar", self._enviados[-1][1])

    def test_una_frase_normal_no_se_reinterpreta_como_opcion(self):
        db_chat.chat_save_message(
            "whatsapp", f"tel:{self._tel}",
            {"role": "assistant", "content": "¿Qué necesitás? [opciones: Turno | Orden]"})
        h = self._post(_msg_event(**{"from": f"{self._tel}@c.us", "body": "quiero un turno de cardiología"}))
        self.assertEqual(h.status_code, 200)
        self.assertIn("quiero un turno de cardiología", self._enviados[-1][1])

    def test_media_se_lee_y_se_narra_en_el_texto(self):
        with patch.object(whatsapp_chat, "_descargar_media",
                          return_value=(b"\x89PNG\r\n\x1a\nresto", "image/png")):
            h = self._post(_msg_event(**{
                "from": f"{self._tel}@c.us", "body": "te mando la orden",
                "hasMedia": True, "media": {"url": "https://waha.local/x", "filename": "orden.png"},
            }))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._enviados), 2)
        self.assertIn('imagen completa', self._enviados[0][1])

    def test_codigo_handoff_valido_copia_historial(self):
        from app.core import db_chat
        clave_web = f"ip:1.2.3.{_CONTADOR_DNI[0]}"
        code = db_chat.chat_handoff_create(clave_web)
        db_chat.chat_save_message("web", clave_web, {"role": "user", "content": "cuánto sale"})
        h = self._post(_msg_event(**{
            "from": f"{self._tel}@c.us", "body": f"vengo del chat, mi código es {code}"}))
        self.assertEqual(h.status_code, 200)
        historial = db_chat.chat_history("whatsapp", f"tel:{self._tel}", 50)
        contenidos = [m.get("content") for m in historial]
        self.assertIn("cuánto sale", contenidos)

    def test_codigo_handoff_es_single_use(self):
        from app.core import db_chat
        code = db_chat.chat_handoff_create(f"ip:9.9.9.{_CONTADOR_DNI[0]}")
        self._post(_msg_event(**{"from": f"{self._tel}@c.us", "body": f"código {code}"}))
        segunda = db_chat.chat_handoff_use(code, self._tel)
        self.assertIsNone(segunda)

    def test_codigo_handoff_no_autoriza_reserva_ajena(self):
        # El code vino de una conversación web SIN cuenta (ip:*); usarlo desde
        # un teléfono que NO está registrado sigue sin poder reservar — el
        # traspaso es sólo texto, nunca autorización.
        from app.core import db_chat
        code = db_chat.chat_handoff_create(f"ip:5.5.5.{_CONTADOR_DNI[0]}")
        tel_ajeno = f"549225509{_CONTADOR_DNI[0]:04d}"
        h = self._post(_msg_event(**{"from": f"{tel_ajeno}@c.us", "body": f"código {code}"}))
        self.assertEqual(h.status_code, 200)
        destino, texto = self._enviados[-1]
        self.assertIn("registrate", texto.lower())

    def test_codigo_handoff_de_sesion_logueada_autoriza_reserva(self):
        # El responsable del producto, 2026-08-02: un código emitido por una sesión LOGUEADA
        # ("dni:<dni>") autoriza reservas al redimirse, aunque el teléfono
        # que lo redime no sea el registrado en la cuenta — a diferencia del
        # caso "ip:*" de test_codigo_handoff_no_autoriza_reserva_ajena.
        from app.core import db_chat
        code = db_chat.chat_handoff_create(f"dni:{self._dni}")
        tel_ajeno = f"549225508{_CONTADOR_DNI[0]:04d}"
        h = self._post(_msg_event(**{"from": f"{tel_ajeno}@c.us", "body": f"código {code}"}))
        self.assertEqual(h.status_code, 200)
        destino, texto = self._enviados[-1]
        self.assertNotIn("registrate", texto.lower())

    def test_grant_de_handoff_sigue_vigente_en_mensajes_posteriores(self):
        # El código es de un solo uso, pero la autorización que otorga tiene
        # que sobrevivir a mensajes SIGUIENTES (sin código) desde el mismo
        # teléfono — si no, la persona tendría que repetir el código en
        # cada mensaje.
        from app.core import db_chat
        code = db_chat.chat_handoff_create(f"dni:{self._dni}")
        tel_ajeno = f"549225507{_CONTADOR_DNI[0]:04d}"
        self._post(_msg_event(**{"from": f"{tel_ajeno}@c.us", "body": f"código {code}"}))
        self._enviados.clear()
        h = self._post(_msg_event(**{"from": f"{tel_ajeno}@c.us", "body": "hola de nuevo"}))
        self.assertEqual(h.status_code, 200)
        self.assertNotIn("registrate", self._enviados[-1][1].lower())

    def test_grant_de_handoff_vence(self):
        from app.core import db_chat
        code = db_chat.chat_handoff_create(f"dni:{self._dni}")
        tel_ajeno = f"549225506{_CONTADOR_DNI[0]:04d}"
        db_chat.chat_handoff_use(code, tel_ajeno)
        self.assertIsNone(db_chat.user_by_handoff_phone(tel_ajeno, grant_days=0))

    def test_codigo_valido_narra_bienvenida_al_motor(self):
        # Ronda 2 (responsable del producto): redimir un código con éxito YA NO contesta con un
        # saludo fijo -- se teje una instrucción de bienvenida en el texto
        # ANTES de llamar al motor (mismo patrón que el path de media), así
        # que el modelo puede saludar/resumir/repreguntar con el historial
        # recién copiado en su contexto. El modelo fake ecoa el texto tal
        # cual, así que la instrucción tiene que aparecer en la respuesta.
        from app.core import db_chat
        clave_web = f"ip:7.7.7.{_CONTADOR_DNI[0]}"
        code = db_chat.chat_handoff_create(clave_web)
        db_chat.chat_save_message("web", clave_web, {"role": "user", "content": "cuánto sale"})
        h = self._post(_msg_event(**{
            "from": f"{self._tel}@c.us", "body": f"vengo del chat, mi código es {code}"}))
        self.assertEqual(h.status_code, 200)

        historial = db_chat.chat_history("whatsapp", f"tel:{self._tel}", 50)
        turnos_usuario = [m for m in historial if m.get("role") == "user"]
        self.assertIn("dale la bienvenida", turnos_usuario[-1]["content"])

        destino, texto = self._enviados[-1]
        self.assertIn("dale la bienvenida", texto)
        # el saludo fijo viejo ya no se antepone
        self.assertNotIn("Dale, seguimos", texto)

    def test_codigo_invalido_no_rompe_el_flujo(self):
        h = self._post(_msg_event(**{
            "from": f"{self._tel}@c.us", "body": "mi código es ST-ZZZZZZ"}))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._enviados), 2)
        self.assertIn("no es válido", self._enviados[-1][1])

    def test_rate_limit_de_intentos_fallidos_por_telefono(self):
        _env.set_env(self, TURNOS_WA_HANDOFF_PER_HOUR="2")
        for _ in range(3):
            self._post(_msg_event(**{
                "from": f"{self._tel}@c.us", "body": "código ST-000000"}))
        # las primeras 2 avisan "no es válido"; a partir de la 3ra el intento
        # de código se ignora en silencio (no cuenta contra el rate limit del
        # motor) pero el mensaje normal se sigue procesando.
        avisos_invalido = sum(1 for _, t in self._enviados if "no es válido" in t)
        self.assertEqual(avisos_invalido, 2)

    def _meta_body(self, **msg_over):
        msg = {"from": self._tel, "type": "text", "text": {"body": "hola"}}
        msg.update(msg_over)
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"phone_number_id": "123"},
                "messages": [msg],
            }}]}],
        }

    def test_meta_texto_llega_al_motor_y_responde(self):
        h = self._post(self._meta_body())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._enviados), 2)
        self.assertIn('inteligencia artificial', self._enviados[0][1])
        self.assertTrue(self._enviados[-1][1])

    def test_meta_imagen_se_lee_via_dos_llamadas_graph(self):
        with patch.object(whatsapp_chat, "_descargar_media_meta",
                          return_value=(b"\x89PNG\r\n\x1a\nresto", "image/png")) as mock_dl:
            h = self._post(self._meta_body(
                type="image", text=None,
                image={"id": "media-abc", "mime_type": "image/png"}))
        self.assertEqual(h.status_code, 200)
        mock_dl.assert_called_once_with("media-abc")
        self.assertEqual(len(self._enviados), 2)
        self.assertIn('imagen completa', self._enviados[0][1])

    def test_meta_statuses_only_no_dispara_respuesta(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "statuses", "value": {
                "statuses": [{"id": "wamid.x", "status": "read"}],
            }}]}],
        }
        h = self._post(body)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(self._enviados, [])


class TestDescargarMediaMeta(unittest.TestCase):
    """Los dos GET de Graph (resolver id -> url, después bajar la url) —
    mockeados, no de red."""

    def setUp(self):
        _env.set_env(self, META_WA_TOKEN="tok-de-test")

    def test_dos_llamadas_bearer_devuelven_los_bytes(self):
        respuestas = [
            json.dumps({"url": "https://graph.facebook.com/v25.0/media-abc"}).encode("utf-8"),
            b"\xff\xd8\xffresto-de-la-imagen",
        ]

        class _Resp:
            def __init__(self, data):
                self._data = data
                self.headers = {"Content-Type": "image/jpeg"}
                self.status = 200

            def read(self):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        llamadas = []

        def fake_urlopen(req, timeout=10):
            llamadas.append(req.full_url)
            return _Resp(respuestas.pop(0))

        with patch.object(whatsapp_chat.urllib.request, "urlopen", side_effect=fake_urlopen):
            data, mime = whatsapp_chat._descargar_media_meta("media-abc")
        self.assertEqual(data, b"\xff\xd8\xffresto-de-la-imagen")
        self.assertEqual(len(llamadas), 2)
        self.assertIn("media-abc", llamadas[0])

    def test_sin_token_no_intenta_red(self):
        _env.set_env(self, META_WA_TOKEN=None)
        data, mime = whatsapp_chat._descargar_media_meta("media-abc")
        self.assertIsNone(data)


class TestSeleccionDeProveedor(unittest.TestCase):
    """otp_send.wa_provider() — el seam de transporte SALIENTE."""

    def setUp(self):
        _env.set_env(self, TURNOS_WA_PROVIDER=None, META_WA_TOKEN=None, OTP_WAHA_URL=None)

    def test_explicito_manda(self):
        # El opt-in de WAHA exige loopback además del env explícito
        # (contrato de transporte 2026-08-14, docstring de otp_send).
        _env.set_env(self, TURNOS_WA_PROVIDER="waha", OTP_WAHA_URL="http://127.0.0.1:3005",
                     META_WA_TOKEN="tok")  # aunque Meta esté configurada
        self.assertEqual(otp_send.wa_provider(), "waha")

    def test_auto_meta_si_hay_token(self):
        _env.set_env(self, META_WA_TOKEN="tok", OTP_WAHA_URL="http://waha.local")
        self.assertEqual(otp_send.wa_provider(), "meta")

    def test_sin_meta_no_cae_a_waha(self):
        # ANTES auto-detectaba WAHA acá; el contrato de transporte lo prohíbe:
        # sin Meta y sin opt-in explícito el resultado es "" (no-op), nunca un
        # fallback silencioso a un transporte no oficial.
        _env.set_env(self, OTP_WAHA_URL="http://waha.local")
        self.assertEqual(otp_send.wa_provider(), "")

    def test_waha_explicito_remoto_queda_apagado(self):
        _env.set_env(self, TURNOS_WA_PROVIDER="waha", OTP_WAHA_URL="http://192.168.1.156:3005")
        self.assertEqual(otp_send.wa_provider(), "")

    def test_sin_nada_configurado(self):
        self.assertEqual(otp_send.wa_provider(), "")

    def test_enviar_texto_sin_proveedor_es_noop(self):
        self.assertFalse(otp_send.enviar_texto("5492255111222", "hola"))

    def test_enviar_texto_usa_meta_cuando_corresponde(self):
        _env.set_env(self, META_WA_TOKEN="tok", META_WA_PHONE_NUMBER_ID="123")
        with patch.object(otp_send, "_enviar_meta", return_value=True) as mock_meta:
            ok = otp_send.enviar_texto("5492255111222", "hola")
        self.assertTrue(ok)
        mock_meta.assert_called_once()


if __name__ == "__main__":
    unittest.main()
