"""test_intake_http.py — rutas del paciente de la entrevista previa
(modules/intake/interview_patient.py): GET /entrevista/<id>,
GET/POST /api/intake/t/<patient_token>[/message].

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_intake_http.py"
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
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.modules.intake import interview_patient  # noqa: E402
from app.modules.intake import interview_patient_router  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakehttp",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_USER = {"dni": "27888999", "nombre": "Ana", "apellido": "López"}
_OTRO = {"dni": "30111222", "nombre": "Otro", "apellido": "Paciente"}

_APP = FastAPI()
_APP.include_router(interview_patient_router.router)
_CLIENT = TestClient(
    _APP,
    base_url="http://127.0.0.1:7700",
    follow_redirects=False,
    client=("127.0.0.1", 54321),
)


class TestRuteoAjeno(unittest.TestCase):
    def test_get_ajeno_no_maneja(self):
        self.assertEqual(_CLIENT.get("/otra-cosa").status_code, 404)

    def test_post_ajeno_no_maneja(self):
        self.assertEqual(_CLIENT.post("/otra-cosa", json={}).status_code, 404)

    def test_get_de_mensaje_no_maneja(self):
        self.assertEqual(_CLIENT.get("/api/intake/t/tok/message").status_code, 405)


class TestTokenDesconocido(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        interview_patient._HITS.clear()

    def test_mensaje_token_desconocido_404(self):
        h = _CLIENT.post(
            "/api/intake/t/no-existe/message", json={"texto": "hola"})
        self.assertEqual(h.status_code, 404)

    def test_transcript_token_desconocido_404(self):
        h = _CLIENT.get("/api/intake/t/no-existe")
        self.assertEqual(h.status_code, 404)


class TestMensajeYTranscript(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        interview_patient._HITS.clear()
        _env.set_env(self, INTAKE_PER_HOUR=None)

    def _interview(self):
        return db_clinical.interview_create(entry_mode="post_booking", matched=True)

    def test_transcript_siembra_la_primera_pregunta_y_la_devuelve(self):
        interview = self._interview()
        h = _CLIENT.get(f"/api/intake/t/{interview['patient_token']}")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["status"], "active")
        self.assertEqual(len(h.json()["mensajes"]), 1)
        self.assertEqual(h.json()["mensajes"][0]["role"], "assistant")

    def test_mensaje_se_responde_y_no_expone_el_marcador(self):
        interview = self._interview()
        token = interview["patient_token"]
        _CLIENT.get(f"/api/intake/t/{token}")  # siembra la primera pregunta
        h = _CLIENT.post(
            f"/api/intake/t/{token}/message",
            json={"texto": "me duele la cabeza"})
        self.assertEqual(h.status_code, 200)
        self.assertIn("texto", h.json())
        self.assertIn("fin", h.json())
        self.assertNotIn("[fin]", h.json()["texto"])

    def test_mensaje_sin_texto_400(self):
        interview = self._interview()
        h = _CLIENT.post(
            f"/api/intake/t/{interview['patient_token']}/message",
            json={"texto": "  "})
        self.assertEqual(h.status_code, 400)

    def test_mensaje_en_entrevista_cerrada_409(self):
        interview = self._interview()
        db_clinical.interview_set_status(interview["id"], "done")
        h = _CLIENT.post(
            f"/api/intake/t/{interview['patient_token']}/message",
            json={"texto": "hola"})
        self.assertEqual(h.status_code, 409)

    def test_rate_limit_429(self):
        _env.set_env(self, INTAKE_PER_HOUR="1")
        interview = self._interview()
        token = interview["patient_token"]
        headers = {"X-Forwarded-For": "6.6.6.6"}
        h1 = _CLIENT.post(
            f"/api/intake/t/{token}/message", json={"texto": "uno"},
            headers=headers)
        self.assertEqual(h1.status_code, 200)
        h2 = _CLIENT.post(
            f"/api/intake/t/{token}/message", json={"texto": "dos"},
            headers=headers)
        self.assertEqual(h2.status_code, 429)

    # ---- foto adjunta (2026-08-01, componente de chat compartido) ---------

    _PNG_1X1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
        "42YAAAAASUVORK5CYII=")

    def test_mensaje_con_foto_responde_200_y_opciones_siempre_presente(self):
        interview = self._interview()
        token = interview["patient_token"]
        h = _CLIENT.post(f"/api/intake/t/{token}/message", json={
            "texto": "", "adjunto": {
                "filename": "sarpullido.png", "mime": "image/png",
                "data_b64": base64.b64encode(self._PNG_1X1).decode(),
            }})
        self.assertEqual(h.status_code, 200)
        self.assertIn("texto", h.json())
        self.assertIn("opciones", h.json())
        self.assertIsInstance(h.json()["opciones"], list)

    def test_reconexion_muestra_el_texto_de_un_turno_con_foto_sin_romper(self):
        # El mensaje de usuario queda persistido como lista OpenAI-style
        # (texto + image_url) — el reconector (GET, _mensajes_publicos) sólo
        # debe mostrar el texto, nunca el data URI crudo de la imagen.
        interview = self._interview()
        token = interview["patient_token"]
        _CLIENT.get(f"/api/intake/t/{token}")  # siembra la primera pregunta
        h1 = _CLIENT.post(f"/api/intake/t/{token}/message", json={
            "texto": "mirá esta mancha", "adjunto": {
            "mime": "image/png", "data_b64": base64.b64encode(self._PNG_1X1).decode()}})
        self.assertEqual(h1.status_code, 200)

        h2 = _CLIENT.get(f"/api/intake/t/{token}")
        self.assertEqual(h2.status_code, 200)
        mensajes_usuario = [m for m in h2.json()["mensajes"] if m["role"] == "user"]
        self.assertEqual(len(mensajes_usuario), 1)
        self.assertEqual(mensajes_usuario[0]["texto"], "mirá esta mancha")
        self.assertNotIn("base64", mensajes_usuario[0]["texto"])
        self.assertNotIn("data:image", mensajes_usuario[0]["texto"])

    def test_foto_con_data_url_completa_tambien_funciona(self):
        interview = self._interview()
        token = interview["patient_token"]
        data_url = "data:image/png;base64," + base64.b64encode(self._PNG_1X1).decode()
        h = _CLIENT.post(f"/api/intake/t/{token}/message", json={
            "texto": "mirá esto", "adjunto": {"data_b64": data_url}})
        self.assertEqual(h.status_code, 200)

    def test_pdf_adjunto_rechazado_solo_imagen(self):
        interview = self._interview()
        token = interview["patient_token"]
        h = _CLIENT.post(f"/api/intake/t/{token}/message", json={
            "texto": "", "adjunto": {
                "filename": "orden.pdf", "mime": "application/pdf",
                "data_b64": base64.b64encode(b"%PDF-1.4 fake").decode(),
            }})
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"], "adjunto_invalido")

    def test_foto_demasiado_pesada_rechazada(self):
        interview = self._interview()
        token = interview["patient_token"]
        gigante = base64.b64encode(b"0" * (interview_patient._MAX_IMAGEN_BYTES + 1)).decode()
        h = _CLIENT.post(f"/api/intake/t/{token}/message", json={
            "texto": "", "adjunto": {"mime": "image/png", "data_b64": gigante}})
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"], "adjunto_invalido")

    def test_sin_texto_ni_adjunto_sigue_dando_400(self):
        interview = self._interview()
        token = interview["patient_token"]
        h = _CLIENT.post(
            f"/api/intake/t/{token}/message", json={"texto": "  "})
        self.assertEqual(h.status_code, 400)


class TestReopen(unittest.TestCase):
    """POST /api/intake/t/<token>/reopen — "Actualizar entrevista"
    (2026-08-01): una entrevista 'done' vuelve a 'active', conserva el
    transcript entero y siembra la pregunta de actualización; el gate es el
    mismo token opaco que el resto de estas rutas (sin sesión extra)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        interview_patient._HITS.clear()

    def _interview(self, **over):
        return db_clinical.interview_create(entry_mode="post_booking", matched=True, **over)

    def _reopen(self, token):
        return _CLIENT.post(f"/api/intake/t/{token}/reopen")

    def test_token_desconocido_404(self):
        h = self._reopen("no-existe")
        self.assertEqual(h.status_code, 404)

    def test_reabre_una_entrevista_done_y_siembra_la_pregunta_de_actualizacion(self):
        interview = self._interview()
        token = interview["patient_token"]
        db_clinical.interview_set_status(interview["id"], "done")

        h = self._reopen(token)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["status"], "active")
        mensajes = h.json()["mensajes"]
        self.assertTrue(mensajes)
        self.assertEqual(mensajes[-1]["role"], "assistant")
        from app.modules.intake import interview_agent
        self.assertEqual(mensajes[-1]["texto"], interview_agent._FAKE_REABIERTA)

        actualizada = db_clinical.interview_get(interview["id"])
        self.assertEqual(actualizada["status"], "active")
        self.assertIsNotNone(actualizada["reopened_at"])

    def test_conserva_el_transcript_previo_al_reabrir(self):
        interview = self._interview()
        token = interview["patient_token"]
        _CLIENT.get(f"/api/intake/t/{token}")  # siembra la primera pregunta
        _CLIENT.post(
            f"/api/intake/t/{token}/message",
            json={"texto": "me duele la cabeza"})
        db_clinical.interview_set_status(interview["id"], "done")

        h = self._reopen(token)
        self.assertEqual(h.status_code, 200)
        textos_usuario = [
            m["texto"] for m in h.json()["mensajes"] if m["role"] == "user"]
        self.assertIn("me duele la cabeza", textos_usuario)

    def test_mensaje_ya_no_da_409_despues_de_reabrir(self):
        interview = self._interview()
        token = interview["patient_token"]
        db_clinical.interview_set_status(interview["id"], "done")
        self._reopen(token)

        h = _CLIENT.post(
            f"/api/intake/t/{token}/message", json={"texto": "algo cambió"})
        self.assertEqual(h.status_code, 200)

    def test_reabrir_una_entrevista_ya_activa_es_idempotente(self):
        interview = self._interview()
        token = interview["patient_token"]
        _CLIENT.get(f"/api/intake/t/{token}")  # siembra la primera pregunta

        h = self._reopen(token)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["status"], "active")
        # Idempotente: no corrió OTRO turno del modelo — sigue habiendo un
        # solo mensaje (la primera pregunta sembrada por el GET de arriba).
        self.assertEqual(len(h.json()["mensajes"]), 1)

    def test_mensaje_agota_el_cupo_y_despues_reabrir_da_429(self):
        # Mismo bucket por IP que /message (interview_patient._allowed) — un
        # mensaje ya consumió el único cupo de la hora, así que reabrir con
        # la MISMA ip también pega el 429.
        _env.set_env(self, INTAKE_PER_HOUR="1")
        interview = self._interview()
        token = interview["patient_token"]
        db_clinical.interview_set_status(interview["id"], "done")
        headers = {"X-Forwarded-For": "9.9.9.9"}
        _CLIENT.post(
            f"/api/intake/t/{token}/message", json={"texto": "uno"},
            headers=headers)
        h2 = _CLIENT.post(
            f"/api/intake/t/{token}/reopen", headers=headers)
        self.assertEqual(h2.status_code, 429)

    def test_rate_limit_429_al_reabrir(self):
        _env.set_env(self, INTAKE_PER_HOUR="1")
        interview = self._interview()
        token = interview["patient_token"]
        db_clinical.interview_set_status(interview["id"], "done")
        headers = {"X-Forwarded-For": "8.8.8.8"}
        h1 = _CLIENT.post(
            f"/api/intake/t/{token}/reopen", headers=headers)
        self.assertEqual(h1.status_code, 200)
        h2 = _CLIENT.post(
            f"/api/intake/t/{token}/message", json={"texto": "otro"},
            headers=headers)
        self.assertEqual(h2.status_code, 429)


class TestPaginaEntrevista(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        # These ownership/shell cases exercise an explicitly enabled site.
        # A new installation now defaults OFF; settings tests pin that gate.
        _env.set_env(self, INTAKE_ENABLED="1")
        self._orig_user = booking_requests._get_user
        interview_patient._HITS.clear()

    def tearDown(self):
        booking_requests._get_user = self._orig_user

    def _crear_solicitud(self, dni=_USER["dni"]):
        return db_booking.create_booking_request(
            dni, "Ana", "López", "1980-01-01", "wa:11-5555",
            "cardiologia-test-intake", "hospital-pinamar", None)

    def test_sin_sesion_401(self):
        booking_requests._get_user = lambda h: None
        sid = self._crear_solicitud()
        h = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h.status_code, 401)

    def test_solicitud_ajena_403(self):
        booking_requests._get_user = lambda h: dict(_OTRO)
        sid = self._crear_solicitud()
        h = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h.status_code, 403)

    def test_solicitud_inexistente_403(self):
        booking_requests._get_user = lambda h: dict(_USER)
        h = _CLIENT.get("/entrevista/999999999")
        self.assertEqual(h.status_code, 403)

    def test_dueno_200_y_crea_la_fila_una_sola_vez(self):
        booking_requests._get_user = lambda h: dict(_USER)
        sid = self._crear_solicitud()
        h1 = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h1.status_code, 200)
        primera = db_clinical.interview_for_request(sid)
        self.assertIsNotNone(primera)

        h2 = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h2.status_code, 200)
        segunda = db_clinical.interview_for_request(sid)
        self.assertEqual(primera["id"], segunda["id"])  # idempotente

    def test_deshabilitada_redirige_a_la_solicitud(self):
        booking_requests._get_user = lambda h: dict(_USER)
        sid = self._crear_solicitud()
        with patch("app.modules.intake.intake_admin.enabled_for", return_value=False):
            h = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h.status_code, 302)
        self.assertEqual(h.headers.get("Location"), "/my-requests")

    def test_la_charla_es_una_pagina_del_sitio_sin_el_segundo_chat(self):
        # Segunda vuelta 2026-08-01: en una computadora la entrevista se abre
        # DENTRO del sitio (barra y pie de siempre, columna angosta); en el
        # teléfono vuelve a ser la pantalla entera, y eso lo decide el ancho
        # en entrevista.css, no el servidor. Lo único que nunca viaja acá es
        # el asistente de reservas: dos chats en una pantalla confunden.
        booking_requests._get_user = lambda h: dict(_USER)
        sid = self._crear_solicitud()
        h = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h.status_code, 200)
        html = h.text
        self.assertIn('id="stznav"', html)
        # Tier 4 slice 6: nav.js sale por su URL fingerprinted, no el literal
        # /nav.js?v=NN — sigue siendo el mismo script, misma barra del sitio.
        self.assertIn(f'src="{static_fingerprint.url("platform/nav.mjs")}"', html)
        self.assertIn('<footer class="site">', html)
        self.assertNotIn("chat-widget.js", html)
        self.assertNotIn('id="stz-chat-launcher"', html)
        self.assertNotIn('id="chat-boot-error"', html)
        self.assertNotIn("chat-ui.js", html)
        self.assertIn('window.SANA_STATIC=', html)
        self.assertIn("platform/chat-ui.mjs", html)
        self.assertIn('body class="iv-chat-body"', html)
        self.assertIn("id=\"iv-host\"", html)
        i_entrevista = html.index('src="/static/modules/intake/entrevista.')
        self.assertGreater(i_entrevista, html.index("</head>"))


class TestPaginaEntrevistaConToken(unittest.TestCase):
    """GET /entrevista/<id>?t=<patient_token> (2026-08-03): el link que va
    en el mail entra directo al chat, SIN sesión — mismo modelo que
    appointment_offers.token. El token sólo autoriza SU PROPIA entrevista,
    nunca la cuenta entera: un token de otra solicitud (o inventado) nunca
    dice si `id` existe."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        self._orig_user = booking_requests._get_user
        booking_requests._get_user = lambda h: None  # sin sesión, todo el rato
        interview_patient._HITS.clear()

    def tearDown(self):
        booking_requests._get_user = self._orig_user

    def _crear_solicitud(self, dni=_USER["dni"]):
        return db_booking.create_booking_request(
            dni, "Ana", "López", "1980-01-01", "wa:11-5555",
            "cardiologia-test-intake", "hospital-pinamar", None)

    def test_token_abre_el_chat_sin_sesion(self):
        sid = self._crear_solicitud()
        iv = db_clinical.interview_create(entry_mode="post_booking", request_id=sid,
                                  dni=_USER["dni"], matched=True)
        h = _CLIENT.get(f"/entrevista/{sid}?t={iv['patient_token']}")
        self.assertEqual(h.status_code, 200)
        html = h.text
        self.assertIn(iv["patient_token"], html)
        self.assertIn("id=\"iv-host\"", html)

    def test_token_no_crea_una_segunda_fila(self):
        sid = self._crear_solicitud()
        iv = db_clinical.interview_create(entry_mode="post_booking", request_id=sid,
                                  dni=_USER["dni"], matched=True)
        h = _CLIENT.get(f"/entrevista/{sid}?t={iv['patient_token']}")
        self.assertEqual(h.status_code, 200)
        otra = db_clinical.interview_for_request(sid)
        self.assertEqual(otra["id"], iv["id"])  # el GET con token nunca crea/muta

    def test_token_de_otra_solicitud_404(self):
        sid_propia = self._crear_solicitud()
        sid_ajena = self._crear_solicitud(dni=_OTRO["dni"])
        iv_ajena = db_clinical.interview_create(entry_mode="post_booking", request_id=sid_ajena,
                                        dni=_OTRO["dni"], matched=True)
        h = _CLIENT.get(f"/entrevista/{sid_propia}?t={iv_ajena['patient_token']}")
        self.assertEqual(h.status_code, 404)

    def test_token_desconocido_404(self):
        sid = self._crear_solicitud()
        h = _CLIENT.get(f"/entrevista/{sid}?t=no-existe")
        self.assertEqual(h.status_code, 404)

    def test_sin_token_y_sin_sesion_sigue_dando_401(self):
        # El camino viejo (sin ?t=) no cambia: sigue exigiendo sesión.
        sid = self._crear_solicitud()
        h = _CLIENT.get(f"/entrevista/{sid}")
        self.assertEqual(h.status_code, 401)


class TestEntrevistaPatientToken(unittest.TestCase):
    """modules.turnos.booking_requests._entrevista_patient_token (2026-08-03): el helper
    que notifications.py usa para que el link del mail lleve el token —
    lazily crea (o reutiliza) la entrevista, best-effort en cualquier
    falla."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")

    def _crear_solicitud(self, dni=_USER["dni"]):
        return db_booking.create_booking_request(
            dni, "Ana", "López", "1980-01-01", "wa:11-5555",
            "cardiologia-test-intake", "hospital-pinamar", None)

    def test_crea_la_entrevista_y_devuelve_su_token(self):
        sid = self._crear_solicitud()
        token = booking_requests._entrevista_patient_token(sid)
        self.assertTrue(token)
        iv = db_clinical.interview_for_request(sid)
        self.assertEqual(iv["patient_token"], token)

    def test_reutiliza_la_misma_entrevista_en_la_segunda_llamada(self):
        sid = self._crear_solicitud()
        t1 = booking_requests._entrevista_patient_token(sid)
        t2 = booking_requests._entrevista_patient_token(sid)
        self.assertEqual(t1, t2)

    def test_solicitud_inexistente_degrada_a_none(self):
        self.assertIsNone(booking_requests._entrevista_patient_token(999999999))


class TestPaginaQrPantallaCompleta(unittest.TestCase):
    """El mismo chrome="chat" (sin header/footer/widget) aplica al ingreso
    por QR de puerta, con o sin sesión de cuenta activa."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        self._orig_user = booking_requests._get_user
        interview_patient._HITS.clear()

    def tearDown(self):
        booking_requests._get_user = self._orig_user

    def test_qr_sin_sesion_tambien_es_pantalla_completa(self):
        from app.modules.intake import intake_admin
        booking_requests._get_user = lambda h: None
        with patch.object(intake_admin, "doctor_by_door_token",
                          return_value={"id": "med-test-fs", "nombre": "Dra. Test"}), \
             patch.object(intake_admin, "enabled_for", return_value=True):
            h = _CLIENT.get("/e/token-test-fs")
        self.assertEqual(h.status_code, 200)
        html = h.text
        self.assertIn('id="stznav"', html)
        self.assertNotIn("chat-widget.js", html)
        self.assertNotIn('id="stz-chat-launcher"', html)
        self.assertNotIn('id="chat-boot-error"', html)
        self.assertIn('body class="iv-chat-body"', html)
        self.assertIn("data-qr-start=", html)


if __name__ == "__main__":
    unittest.main()
