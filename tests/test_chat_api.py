"""test_chat_api.py — chat de turnos en el sitio (chat_api.py: POST /api/chat).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_chat_api.py"
"""

import os
import sys
import json
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_accounts  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.modules.chat import chat_api  # noqa: E402
from app.modules.chat import chat_api_router  # noqa: E402
from app.core import config  # noqa: E402

_APP = FastAPI()
_APP.include_router(chat_api_router.router)
_CLIENT = TestClient(
    _APP,
    base_url="http://127.0.0.1:7700",
    follow_redirects=False,
    client=("127.0.0.1", 54321),
)


class TestHandoffCodeFormato(unittest.TestCase):
    """Ronda 2 (responsable del producto: "el código de traspaso tiene que ser difícil de
    adivinar"): 12 chars sobre el alfabeto sin ambigüedades, vía `secrets`
    (no `random`) — pura, no toca la base."""

    def test_prefijo_y_longitud(self):
        code = db_chat._generate_handoff_code()
        self.assertTrue(code.startswith("ST-"))
        self.assertEqual(len(code) - len("ST-"), 12)

    def test_alfabeto_sin_ambiguos(self):
        code = db_chat._generate_handoff_code()
        cuerpo = code[len("ST-"):]
        for c in cuerpo:
            self.assertIn(c, db_chat._HANDOFF_ALPHABET)
        for ambiguo in "0O1I":
            self.assertNotIn(ambiguo, db_chat._HANDOFF_ALPHABET)

    def test_no_se_repiten_en_una_muestra_chica(self):
        codigos = {db_chat._generate_handoff_code() for _ in range(200)}
        self.assertEqual(len(codigos), 200)


class TestRuteoYLimite(unittest.TestCase):
    def setUp(self):
        chat_api._HITS.clear()
        _env.set_env(self, TURNOS_CHAT_PER_HOUR=None)

    def test_ruta_ajena_no_maneja(self):
        self.assertEqual(_CLIENT.post("/api/otra-cosa", json={}).status_code, 404)

    def test_get_no_maneja(self):
        self.assertEqual(_CLIENT.get("/api/chat").status_code, 405)

    def test_sin_mensaje_400(self):
        h = _CLIENT.post("/api/chat", json={})
        self.assertEqual(h.status_code, 400)

    def test_anonimo_responde(self):
        h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertEqual(h.status_code, 200)
        self.assertIn("respuesta", h.json())
        self.assertFalse(h.json()["autenticado"])

    def test_rate_limit_429(self):
        _env.set_env(self, TURNOS_CHAT_PER_HOUR="1")
        headers = {"X-Forwarded-For": "5.5.5.5"}
        h1 = _CLIENT.post("/api/chat", json={"mensaje": "hola"}, headers=headers)
        self.assertEqual(h1.status_code, 200)
        h2 = _CLIENT.post(
            "/api/chat", json={"mensaje": "hola de nuevo"}, headers=headers)
        self.assertEqual(h2.status_code, 429)

    def test_mensaje_se_recorta(self):
        largo = "a" * 3000
        h = _CLIENT.post("/api/chat", json={"mensaje": largo})
        self.assertEqual(h.status_code, 200)  # no revienta con un mensaje enorme

    def test_session_id_aisla_la_conversacion_y_vuelve_en_la_respuesta(self):
        with patch("app.modules.chat.chat_agent.chat_full", return_value={
            "texto": "hola", "opciones": [], "login": False,
            "enlaces": [], "fuentes": [],
        }) as chat:
            h = _CLIENT.post(
                "/api/chat",
                json={"mensaje": "hola", "session_id": "chat_20260729_a"})
        self.assertEqual(h.json()["session_id"], "chat_20260729_a")
        self.assertEqual(chat.call_args.kwargs["clave"],
                         "ip:127.0.0.1:chat:chat_20260729_a")

    def test_session_id_invalido_no_se_usa(self):
        h = _CLIENT.post(
            "/api/chat", json={"mensaje": "hola", "session_id": "../otra"})
        self.assertNotIn("session_id", h.json())


class TestAutenticado(unittest.TestCase):
    def setUp(self):
        chat_api._HITS.clear()
        self._orig = None
        from app.cuentas import accounts
        self._accounts = accounts
        self._orig = accounts.current_user

    def tearDown(self):
        self._accounts.current_user = self._orig

    def test_logueado_marca_autenticado(self):
        self._accounts.current_user = lambda h: {"dni": "30111222", "nombre": "Nico"}
        h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertTrue(h.json()["autenticado"])


class TestOpcionesYLogin(unittest.TestCase):
    """La web conserva la lista en el mensaje y no repite `opciones` como
    botones; [login] sí se propaga para montar el acceso embebido."""

    def setUp(self):
        chat_api._HITS.clear()

    def test_opciones_viajan_aparte_para_los_botones_del_widget(self):
        # Ronda 2026-08-01 (chat-ui.js, componente compartido con la
        # entrevista): el widget vuelve a pintar las opciones del modelo
        # como botones tocables bajo la burbuja — la respuesta las lleva
        # aparte en `opciones`, SIN el marcador crudo en el texto.
        with patch("app.modules.chat.chat_agent.chat_full", return_value={
            "texto": (
                "Elegí un centro:\n"
                "* Hospital de Pinamar\n"
                "* CAPS Ostende\n"
                "* CAPS Valeria del Mar"
            ),
            "opciones": [
                "Hospital de Pinamar", "CAPS Ostende", "CAPS Valeria del Mar",
            ],
            "login": False,
            "enlaces": [],
            "fuentes": [],
        }):
            h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["opciones"],
                         ["Hospital de Pinamar", "CAPS Ostende", "CAPS Valeria del Mar"])
        self.assertEqual(h.json()["respuesta"].count("Hospital de Pinamar"), 1)
        self.assertEqual(h.json()["respuesta"].count("CAPS Ostende"), 1)
        self.assertEqual(h.json()["respuesta"].count("CAPS Valeria del Mar"), 1)
        self.assertNotIn("[opciones", h.json()["respuesta"])

    def test_sin_opciones_no_aparece_la_clave(self):
        h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertEqual(h.status_code, 200)
        self.assertNotIn("opciones", h.json())

    def test_login_se_devuelve_cuando_el_modelo_lo_marca(self):
        h = _CLIENT.post(
            "/api/chat", json={"mensaje": "MARKERS:Necesitás iniciar sesión. [login]"})
        self.assertTrue(h.json()["login"])
        self.assertNotIn("[login]", h.json()["respuesta"])

    def test_titulo_del_modelo_se_devuelve_separado_del_mensaje(self):
        with patch("app.modules.chat.chat_agent.chat_full", return_value={
            "texto": "Hay turnos.",
            "titulo": "Turno de cardiología",
            "opciones": [],
            "login": False,
            "enlaces": [],
            "fuentes": [],
        }):
            h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertEqual(h.json()["titulo"], "Turno de cardiología")
        self.assertNotIn("[titulo", h.json()["respuesta"])

    def test_sin_login_no_aparece_la_clave(self):
        h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertNotIn("login", h.json())


class TestAdjunto(unittest.TestCase):
    """POST /api/chat con `adjunto` — la orden médica subida DENTRO del chat
    (TURNOS_FAKE_AI=1 hace que booking_referrals.analyze_referral_bytes no pegue a red)."""

    def setUp(self):
        chat_api._HITS.clear()
        from app.cuentas import accounts
        self._accounts = accounts
        self._orig = accounts.current_user

    def tearDown(self):
        self._accounts.current_user = self._orig

    _PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+A8AAQUBAScY42YAAAAASUVORK5CYII=")

    def test_anonimo_con_adjunto_lo_lee_sin_pedir_login(self):
        analisis = {
            "ok": True,
            "orden_token": "tok-anonimo",
            "veredicto": "parece_correcta",
            "campos": {"practicas": ["Ecografía abdominal"]},
        }
        with patch("app.modules.chat.chat_tools.analizar_orden_para", return_value=analisis):
            h = _CLIENT.post("/api/chat", json={
                "mensaje": "",
                "adjunto": {
                    "filename": "orden.png", "mime": "image/png",
                    "data_b64": self._PNG_B64,
                },
            })
        self.assertEqual(h.status_code, 200)
        self.assertFalse(h.json()["autenticado"])
        self.assertNotIn("login", h.json())
        self.assertIn("Ecografía abdominal", h.json()["respuesta"])
        self.assertTrue(any(link["href"].startswith("/service/")
                            for link in h.json()["enlaces"]))

    def test_narracion_de_orden_no_expone_identidad_ni_diagnostico(self):
        from app.modules.chat import chat_tools
        text = chat_tools.narrar_adjunto("", {
            "ok": True, "orden_token": "tok-seguro",
            "veredicto": "parece_correcta",
            "campos": {
                "paciente": {"valor": "Persona Privada"},
                "dni": {"valor": "12345678"},
                "diagnostico": {"valor": "dato clínico"},
                "especialidad": {"valor": "Ecografía"},
                "practicas": {"valor": ["Ecografía abdominal"]},
            },
        })
        self.assertIn("Ecografía abdominal", text)
        self.assertNotIn("Persona Privada", text)
        self.assertNotIn("12345678", text)
        self.assertNotIn("dato clínico", text)

    def test_narracion_de_orden_advierte_jurisdiccion_al_motor(self):
        # El motor conversacional (usuarios logueados) no ve el campo
        # "documento" directamente -- sólo el texto narrado -- así que la
        # advertencia de jurisdicción tiene que viajar ahí para que no
        # reserve un turno con una orden que no corresponde a este centro.
        from app.modules.chat import chat_tools
        text = chat_tools.narrar_adjunto("", {
            "ok": True, "orden_token": "tok-jurisdiccion",
            "veredicto": "dudosa",
            "campos": {"especialidad": {"valor": "Cardiología"}},
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": False, "jurisdiccion_detectada": "Chile"},
        })
        self.assertIn("jurisdicción", text)
        self.assertIn("Chile", text)
        self.assertIn("no la uses", text)

    def test_orden_walkin_no_pide_login_y_descarta_el_pending(self):
        from app.modules.chat import chat_tools
        result = {
            "ok": True, "orden_token": "tok-walkin",
            "veredicto": "parece_correcta",
            "campos": {"practicas": {"valor": ["Hemograma"]}},
        }
        search = {"resultados": [{
            "servicio_id": "laboratorio-hospital",
            "nombre": "Laboratorio y extracciones",
            "centro": "Hospital de Pinamar",
            "como_se_atiende": "sin turno: por orden de llegada",
            "se_pide_por_aca": False,
            "horario": "Lunes a viernes a las 6:15 h",
            "href": "/service/laboratorio-hospital",
        }]}
        with patch.object(chat_tools, "_tool_search", return_value=search), \
             patch.object(chat_tools, "_descartar_pending") as discard:
            out = chat_tools.orientar_orden_anonima(result)
        self.assertFalse(out["login"])
        self.assertIn("No necesitás iniciar sesión", out["texto"])
        discard.assert_called_once_with("tok-walkin")

    def test_orden_de_otra_jurisdiccion_no_pide_resubir(self):
        # A diferencia de documento_equivocado, la orden ESTÁ bien — sólo no
        # corresponde a este centro. No tiene sentido pedir que suban de
        # nuevo la misma foto (eso no cambia la jurisdicción de origen).
        from app.modules.chat import chat_tools
        result = {
            "ok": True, "orden_token": "tok-extranjero",
            "veredicto": "dudosa",
            "campos": {},
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": False, "jurisdiccion_detectada": "Chile"},
        }
        out = chat_tools.orientar_orden_anonima(result)
        self.assertFalse(out["login"])
        self.assertNotIn("subir_otra_orden", out)
        self.assertIn("jurisdicción", out["texto"])
        self.assertIn("Chile", out["texto"])
        self.assertIn("Medicina general", out["texto"])

    def test_anonimo_con_orden_de_otra_jurisdiccion_no_ofrece_subir_otra(self):
        analisis = {
            "ok": True, "orden_token": "tok-extranjero-2",
            "veredicto": "dudosa",
            "campos": {},
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": False, "jurisdiccion_detectada": "Chile"},
        }
        with patch("app.modules.chat.chat_tools.analizar_orden_para", return_value=analisis):
            h = _CLIENT.post("/api/chat", json={
                "mensaje": "",
                "adjunto": {
                    "filename": "orden.png", "mime": "image/png",
                    "data_b64": self._PNG_B64,
                },
            })
        self.assertEqual(h.status_code, 200)
        self.assertFalse(h.json()["autenticado"])
        self.assertNotIn("login", h.json())
        self.assertNotIn("subir_otra_orden", h.json())
        self.assertIn("jurisdicción", h.json()["respuesta"])

    def test_logueado_con_adjunto_lo_analiza_y_narra(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        self._accounts.current_user = lambda h: {"dni": "30222333", "nombre": "Marta"}
        h = _CLIENT.post("/api/chat", json={
            "mensaje": "acá va mi orden",
            "adjunto": {
                "filename": "orden.png", "mime": "image/png",
                "data_b64": self._PNG_B64,
            },
        })
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["autenticado"])
        # el motor fake ecoa lo que ve — que incluya la narración confirma
        # que analizar_orden_para corrió ANTES de llamar al motor.
        self.assertIn("orden_token", h.json()["respuesta"])

    def test_adjunto_invalido_400(self):
        self._accounts.current_user = lambda h: {"dni": "30222334", "nombre": "Marta"}
        h = _CLIENT.post("/api/chat", json={
            "mensaje": "", "adjunto": {
                "filename": "x", "mime": "image/png",
                "data_b64": "no-es-base64-válido!!",
            },
        })
        self.assertEqual(h.status_code, 400)

    def test_documento_equivocado_ofrece_subir_otra_y_no_login(self):
        self._accounts.current_user = lambda h: {"dni": "30222335", "nombre": "Marta"}
        analisis = {
            "ok": True,
            "orden_token": "tok-equivocado",
            "veredicto": "documento_equivocado",
        }
        respuesta_modelo = {
            "texto": "Ese documento no es una orden médica.",
            "opciones": [],
            "login": True,
        }
        with patch("app.modules.chat.chat_tools.analizar_orden_para", return_value=analisis), \
             patch("app.modules.chat.chat_tools.narrar_adjunto", return_value="documento equivocado"), \
             patch("app.modules.chat.chat_agent.chat_full", return_value=respuesta_modelo):
            h = _CLIENT.post("/api/chat", json={
                "mensaje": "",
                "adjunto": {
                    "filename": "factura.png", "mime": "image/png",
                    "data_b64": self._PNG_B64,
                },
            })
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["subir_otra_orden"])
        self.assertNotIn("login", h.json())


class TestAdopcionHistorial(unittest.TestCase):
    """Al loguearse, la conversación "dni:<dni>" adopta el historial de la
    "ip:<ip>" anónima previa si todavía no tiene nada propio (chat_api.py +
    db_chat.chat_adopt_history)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        chat_api._HITS.clear()
        from app.cuentas import accounts
        self._accounts = accounts
        self._orig = accounts.current_user

    def tearDown(self):
        self._accounts.current_user = self._orig

    def test_historial_anonimo_se_copia_al_loguearse(self):
        ip = "8.8.4.4"
        self._accounts.current_user = lambda h: None
        _CLIENT.post(
            "/api/chat", json={"mensaje": "hola, antes de loguearme"},
            headers={"X-Forwarded-For": ip})

        self._accounts.current_user = lambda h: {"dni": "30444555", "nombre": "Rita"}
        _CLIENT.post(
            "/api/chat", json={"mensaje": "ya logueada"},
            headers={"X-Forwarded-For": ip})

        historial = db_chat.chat_history("web", "dni:30444555", 50)
        contenidos = [m.get("content") for m in historial]
        self.assertIn("hola, antes de loguearme", contenidos)
        self.assertIn("ya logueada", contenidos)


class TestTranscripts(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_CHAT_EXPORT_TOKEN=None)
        # Isolate the one canonical effective env-file reader.
        self._config_env_path = config.ENV_PATH
        config.ENV_PATH = ""

    def tearDown(self):
        config.ENV_PATH = self._config_env_path

    def test_sin_env_la_ruta_no_existe(self):
        self.assertEqual(_CLIENT.get("/api/chat-transcripts").status_code, 404)

    def test_con_env_pero_token_incorrecto_403(self):
        _env.set_env(self, {"TURNOS_CHAT_EXPORT_TOKEN": "secreto-correcto"})
        h = _CLIENT.get(
            "/api/chat-transcripts",
            headers={"Authorization": "Bearer otra-cosa"})
        self.assertEqual(h.status_code, 403)

    def test_con_token_correcto_200(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        _env.set_env(self, {"TURNOS_CHAT_EXPORT_TOKEN": "secreto-correcto"})
        db_chat.chat_save_message("web", "dni:transcript-test",
                                 {"role": "user", "content": "hola"})
        h = _CLIENT.get(
            "/api/chat-transcripts?hours=24",
            headers={"Authorization": "Bearer secreto-correcto"})
        self.assertEqual(h.status_code, 200)
        claves = [c["clave"] for c in h.json()["conversaciones"]]
        self.assertIn("dni:transcript-test", claves)


class TestHandoffWhatsapp(unittest.TestCase):
    def setUp(self):
        chat_api._HITS.clear()
        _env.set_env(self, TURNOS_WA_NUMERO=None)

    def test_sin_env_no_hay_link(self):
        h = _CLIENT.post("/api/chat", json={"mensaje": "hola"})
        self.assertNotIn("whatsapp", h.json())

    def test_con_env_y_base_hay_link(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        _env.set_env(self, TURNOS_WA_NUMERO="5492255111222")
        h = _CLIENT.post(
            "/api/chat", json={"mensaje": "hola"},
            headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertIn("whatsapp", h.json())
        self.assertEqual(h.json()["whatsapp"]["numero"], "5492255111222")
        self.assertTrue(h.json()["whatsapp"]["link"].startswith(
            "https://wa.me/5492255111222"))
        self.assertIn("ST-", h.json()["whatsapp"]["codigo"])

    def test_get_whatsapp_da_el_link_sin_codigo_apenas_se_abre_el_panel(self):
        # El control de "pasar a WhatsApp" está en pantalla desde que se abre
        # el chat (responsable del producto, 2026-08-02), así que el enlace tiene que existir
        # ANTES del primer mensaje. Sin código: el código muda el HISTORIAL, y
        # todavía no hay ninguno que mudar (y así no se escribe una fila por
        # cada visitante que abre el panel y no escribe nada).
        _env.set_env(self, TURNOS_WA_NUMERO="5492255111222")
        h = _CLIENT.get("/api/chat/whatsapp")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["numero"], "5492255111222")
        self.assertTrue(h.json()["link"].startswith(
            "https://wa.me/5492255111222?text="))
        self.assertNotIn("codigo", h.json())
        self.assertNotIn("digo%20es", h.json()["link"])

    def test_get_whatsapp_sin_env_no_existe(self):
        # Una instalación sin línea de WhatsApp no muestra el control: 404,
        # no un enlace muerto.
        self.assertEqual(_CLIENT.get("/api/chat/whatsapp").status_code, 404)

    def test_numero_de_test_de_meta_us_arma_un_link_valido(self):
        # TURNOS_WA_NUMERO = el número de test de Meta (Cloud API), formato
        # US sin '+': el link wa.me se arma igual (wa.me sólo pide dígitos,
        # sin importar el país) — 2026-07-23, do not relitigate.
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        _env.set_env(self, TURNOS_WA_NUMERO="15556314211")
        h = _CLIENT.post(
            "/api/chat", json={"mensaje": "hola"},
            headers={"X-Forwarded-For": "8.8.8.10"})
        self.assertIn("whatsapp", h.json())
        self.assertEqual(h.json()["whatsapp"]["numero"], "15556314211")
        self.assertTrue(h.json()["whatsapp"]["link"].startswith(
            "https://wa.me/15556314211?text="))

    def test_mismo_clave_reusa_el_mismo_codigo(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        _env.set_env(self, TURNOS_WA_NUMERO="5492255111222")
        headers = {"X-Forwarded-For": "8.8.8.9"}
        h1 = _CLIENT.post("/api/chat", json={"mensaje": "hola"}, headers=headers)
        h2 = _CLIENT.post("/api/chat", json={"mensaje": "de nuevo"}, headers=headers)
        self.assertEqual(
            h1.json()["whatsapp"]["codigo"], h2.json()["whatsapp"]["codigo"])


class TestChatStream(unittest.TestCase):
    """POST /api/chat/stream — el responsable del producto, 2026-08-02: revela la respuesta ya
    terminada en pedazos (`event: chunk`) en vez de mandarla entera de una,
    y cierra con `event: done` con el mismo cuerpo que /api/chat mandaría."""

    def setUp(self):
        chat_api._HITS.clear()
        self._patch_delay = patch.object(chat_api, "_STREAM_DELAY", 0)
        self._patch_delay.start()

    def tearDown(self):
        self._patch_delay.stop()

    def _eventos(self, crudo):
        texto = crudo.decode("utf-8").strip("\n")
        eventos = []
        for bloque in texto.split("\n\n"):
            if not bloque.strip():
                continue
            lineas = bloque.split("\n")
            evento = next(l[len("event: "):] for l in lineas if l.startswith("event: "))
            datos = json.loads(next(l[len("data: "):] for l in lineas if l.startswith("data: ")))
            eventos.append((evento, datos))
        return eventos

    def test_revela_la_respuesta_en_chunks_y_termina_en_done(self):
        h = _CLIENT.post("/api/chat/stream", json={"mensaje": "hola"})
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.headers["content-type"].startswith("text/event-stream"))
        eventos = self._eventos(h.content)
        self.assertGreaterEqual(len(eventos), 2)
        self.assertTrue(all(e[0] == "chunk" for e in eventos[:-1]))
        self.assertEqual(eventos[-1][0], "done")
        reconstruido = "".join(d["texto"] for _, d in eventos[:-1])
        self.assertEqual(reconstruido, eventos[-1][1]["respuesta"])
        self.assertIn("autenticado", eventos[-1][1])

    def test_sin_mensaje_no_streamea_manda_400_normal(self):
        h = _CLIENT.post("/api/chat/stream", json={})
        self.assertEqual(h.status_code, 400)
        self.assertTrue(h.headers["content-type"].startswith("application/json"))
        self.assertEqual(h.json()["error"], "falta el mensaje")

    def test_rate_limit_no_streamea_manda_429_normal(self):
        for _ in range(20):
            chat_api._allowed("127.0.0.1")
        h = _CLIENT.post("/api/chat/stream", json={"mensaje": "hola"})
        self.assertEqual(h.status_code, 429)
        self.assertTrue(h.headers["content-type"].startswith("application/json"))
        self.assertEqual(h.json()["error"], "limite")

    def test_turno_lento_manda_eventos_estado_antes_de_los_chunks(self):
        """el responsable del producto, 2026-08-04: un turno que tarda (búsqueda en agenda, el
        modelo) no deja el socket mudo — mientras `compute_output` sigue
        corriendo en su propio hilo, el hilo del pedido manda `event: estado`
        cada `_ESTADO_POLL` para que Cloudflare nunca vea la conexión
        inactiva. El framing final (chunks + done) no cambia."""
        original = chat_api.compute_output
        real_event = chat_api_router._event
        estado_visto = threading.Event()

        def _evento_con_aviso(event, payload):
            if event == "estado":
                estado_visto.set()
            return real_event(event, payload)

        def _lento(handler):
            # The turn stays busy until the stream has announced its first
            # `estado`; a stream that never does fails after 30 s, not forever.
            if not estado_visto.wait(30):
                raise AssertionError("el stream nunca mandó un evento estado")
            return original(handler)

        with patch.object(chat_api, "_ESTADO_POLL", 0.02), \
                patch.object(chat_api_router, "_event", side_effect=_evento_con_aviso), \
                patch.object(chat_api, "compute_output", side_effect=_lento):
            h = _CLIENT.post("/api/chat/stream", json={"mensaje": "hola"})

        self.assertEqual(h.status_code, 200)
        eventos = self._eventos(h.content)
        tipos = [e[0] for e in eventos]
        self.assertIn("estado", tipos)
        # Los estados sólo aparecen ANTES del primer chunk, nunca mezclados.
        primer_chunk = tipos.index("chunk")
        self.assertNotIn("estado", tipos[primer_chunk:])
        self.assertEqual(tipos[-1], "done")
        for _, datos in [e for e in eventos if e[0] == "estado"]:
            self.assertIn("mensaje", datos)
            self.assertTrue(datos["mensaje"])
        chunks = [d["texto"] for t, d in eventos if t == "chunk"]
        self.assertEqual("".join(chunks), eventos[-1][1]["respuesta"])
        self.assertIn("autenticado", eventos[-1][1])


class TestHistorial(unittest.TestCase):
    """GET /api/chat/history — hidratación del widget con la conversación
    fusionada (2026-08-03, "una conversación por paciente entre web y
    WhatsApp"). Identidad SIEMPRE de la sesión, nunca de la query string."""

    def setUp(self):
        from app.cuentas import accounts
        self._accounts = accounts
        self._orig = accounts.current_user

    def tearDown(self):
        self._accounts.current_user = self._orig

    def test_sin_sesion_devuelve_vacio(self):
        self._accounts.current_user = lambda h: None
        h = _CLIENT.get("/api/chat/history")
        self.assertEqual(h.json(), {"mensajes": []})

    def test_logueado_trae_los_turnos_vinculados(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        dni = "30777888"
        tel = "5492255777888"
        db_accounts.create_user(dni, "Rita", "Gómez", "1985-01-01", "F", tel,
                        "secreta1", email="rita.hist@ejemplo.com")
        db_chat.chat_save_message("whatsapp", f"tel:{tel}",
                              {"role": "user", "content": "vengo de WhatsApp"})
        self._accounts.current_user = lambda h: db_accounts.get_user(dni)
        h = _CLIENT.get("/api/chat/history")
        textos = [m["texto"] for m in h.json()["mensajes"]]
        self.assertIn("vengo de WhatsApp", textos)

    def test_ignora_un_session_id_con_formato_invalido(self):
        self._accounts.current_user = lambda h: None
        h = _CLIENT.get("/api/chat/history?session_id=%3Cscript%3E")
        self.assertEqual(h.json(), {"mensajes": []})


# ---- setup de Postgres (get-or-create de código de handoff) -----------------

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_chatapi", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


if __name__ == "__main__":
    unittest.main()
