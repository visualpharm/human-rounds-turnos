"""test_preparation.py — pruebas de "Subí tu orden" (preparation.py + abuse_guard.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"
         (desde la raíz — SIEMPRE discover, ver CONTRIBUTING.md)

/api/preparacion MURIÓ (2026-07-16): el asistente lee la orden vía el mismo
POST /api/analyze-order que el wizard de turnos (booking_requests.py) — ver
TestAnalizarOrdenAntiAbuso más abajo. Este archivo cubre lo que sigue viviendo
en preparation.py: match_studies (matcheo puro, sin red) y GET /upload-order.

Sin base de datos: /api/analyze-order no toca Postgres (es previo al
registro), así que este archivo no necesita setUpModule/tearDownModule con
una DB de prueba — mismo espíritu que las partes sin-DB de
test_booking_requests.py, pero más simple.
"""

import io
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.ai import abuse_guard  # noqa: E402
from app.core import config  # noqa: E402
from app.auth import turnstile         # noqa: E402
from _packtest import reference_pack_test  # noqa: E402
# el checkout de dev tiene TURNOS_TURNSTILE_* en .env — los tests corren sin gate
# (turnstile.py es el dueño del par de envs; abuse_guard.py sólo delega)
abuse_guard.ENV_PATH = ""
turnstile.ENV_PATH = ""
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev
from app.modules.turnos import preparation       # noqa: E402
from app.modules.turnos import booking_requests       # noqa: E402
from app.modules.turnos import booking_requests_router, preparation_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_TEST_APP = FastAPI()
_TEST_APP.include_router(booking_requests_router.router)
_TEST_APP.include_router(preparation_router.router)
_CLIENT = TestClient(_TEST_APP)

# 1x1 PNG transparente (mismo fixture que test_booking_requests.py)
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class FakeHandler:
    """Minimal request-shaped object for low-level abuse and Turnstile helpers.

    HTTP route tests below use FastAPI's TestClient directly; this fixture is
    deliberately not an HTTP transport or response adapter.
    """

    def __init__(self, path, body=None, headers=None, ip="203.0.113.5"):
        self.path = path
        if body is None:
            self._body = b""
        elif isinstance(body, (bytes, bytearray)):
            self._body = bytes(body)
        else:
            self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}
        self.status = None
        self.json = None
        self.wfile = io.BytesIO()
        self.sent_headers = {}
        self.client_address = (ip, 12345)

    def _send_json(self, obj, status=200):
        self.json = obj
        self.status = status

    def _read_body(self):
        return self._body

    def _read_json(self):
        return json.loads(self._body.decode("utf-8")) if self._body else {}

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent_headers[k] = v

    def end_headers(self):
        pass


def _orden_body(filename="orden.png", mime="image/png", b64=_PNG_B64):
    return {"orden": {"filename": filename, "mime": mime, "data_b64": b64}}


# ---- match_estudios ----------------------------------------------------------

class TestMatchEstudios(unittest.TestCase):
    def test_especifico_gana_al_generico(self):
        matched, no_rec = preparation.match_studies(["ecografia abdominal"])
        self.assertEqual([e["id"] for e in matched], ["ecografia-abdominal"])
        self.assertEqual(no_rec, [])

    def test_insensible_a_tildes(self):
        matched, _ = preparation.match_studies(["ecografía abdominal"])
        self.assertEqual([e["id"] for e in matched], ["ecografia-abdominal"])

    def test_generico_cuando_no_hay_calificador(self):
        matched, _ = preparation.match_studies(["ecografia"])
        self.assertEqual([e["id"] for e in matched], ["ecografia-general"])

    def test_multi_estudio_y_dedup(self):
        matched, no_rec = preparation.match_studies(
            ["glucemia en ayunas", "hemograma", "algo-inventado-raro"])
        ids = [e["id"] for e in matched]
        # glucemia y hemograma(->laboratorio-sangre) son estudios distintos
        self.assertIn("glucemia", ids)
        self.assertIn("laboratorio-sangre", ids)
        self.assertEqual(len(ids), len(set(ids)))  # sin duplicados
        self.assertIn("algo-inventado-raro", no_rec)

    def test_desconocido_va_a_no_reconocidos(self):
        matched, no_rec = preparation.match_studies(["una practica que no existe"])
        self.assertEqual(matched, [])
        self.assertEqual(no_rec, ["una practica que no existe"])

    def test_dedup_mismo_estudio_dos_veces(self):
        matched, _ = preparation.match_studies(["glucemia", "glucemia en ayunas"])
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "glucemia")

    @reference_pack_test
    def test_fallback_por_servicio_id(self):
        # nada matcheó en los textos de la orden -> reintenta contra el
        # nombre del servicio de la página. Frase elegida para no contener
        # ninguna keyword corta del dataset (eco/tac/pap/mapa/rmn/...).
        texto = "blah blah sin sentido qwerty zxcvbn"
        matched, no_rec = preparation.match_studies(
            [texto], servicio_id="audiometria-ostende")
        self.assertTrue(matched)
        self.assertEqual(matched[0]["id"], "audiometria")
        # el texto original de la orden sigue sin reconocerse
        self.assertIn(texto, no_rec)

    def test_dos_estudios_que_se_preparan_igual_son_una_sola_tarjeta(self):
        # Ver la MISMA tarjeta dos veces se lee como un bug (responsable del producto, 2026-07-12):
        # misma preparación -> una tarjeta con los dos nombres en el título.
        base = {"ayuno": {"required": False}, "preparacion": ["Vení con ropa cómoda."],
                "traer": ["DNI"], "sin_preparacion": True}
        a = dict(base, id="uno", nombre="Estudio A")
        b = dict(base, id="dos", nombre="Estudio B")
        distinto = dict(base, id="tres", nombre="Estudio C", preparacion=["Traé la medicación."])
        out = preparation._merge_identical([a, b, distinto])
        self.assertEqual([e["nombre"] for e in out], ["Estudio A y Estudio B", "Estudio C"])
        self.assertEqual(out[0]["ids"], ["uno", "dos"])

    def test_el_servicio_de_la_pagina_no_agrega_una_segunda_tarjeta(self):
        # La orden pide un estudio distinto al de la página: manda la orden
        # (regla 8), el servicio es SÓLO respaldo — antes salían dos tarjetas.
        matched, _ = preparation.match_studies(
            ["Ecocardiograma"], servicio_id="electrocardiograma-hospital")
        self.assertEqual([e["id"] for e in matched], ["ecocardiograma"])

    def test_sin_textos_ni_servicio_no_rompe(self):
        matched, no_rec = preparation.match_studies([])
        self.assertEqual((matched, no_rec), ([], []))
        matched, no_rec = preparation.match_studies(None, servicio_id=None)
        self.assertEqual((matched, no_rec), ([], []))


# ---- abuse_guard.py ------------------------------------------------------------------

class TestAbuso(unittest.TestCase):
    def setUp(self):
        abuse_guard._HITS.clear()

    def tearDown(self):
        abuse_guard._HITS.clear()

    def test_permite_hasta_el_limite_y_despues_corta(self):
        _env.set_env(self, TURNOS_AI_PER_HOUR="3", TURNOS_AI_PER_DAY="100")
        ip = "10.0.0.1"
        for _ in range(3):
            self.assertTrue(abuse_guard.allowed(ip))
        self.assertFalse(abuse_guard.allowed(ip))

    def test_ip_distinta_tiene_su_propio_cupo(self):
        _env.set_env(self, TURNOS_AI_PER_HOUR="1", TURNOS_AI_PER_DAY="100")
        self.assertTrue(abuse_guard.allowed("1.1.1.1"))
        self.assertFalse(abuse_guard.allowed("1.1.1.1"))
        self.assertTrue(abuse_guard.allowed("2.2.2.2"))

    def test_ventana_expira_con_el_tiempo(self):
        _env.set_env(self, TURNOS_AI_PER_HOUR="1", TURNOS_AI_PER_DAY="100")
        ip = "9.9.9.9"
        base = [1_000_000.0]
        with patch("time.time", lambda: base[0]):
            self.assertTrue(abuse_guard.allowed(ip))
            self.assertFalse(abuse_guard.allowed(ip))
            base[0] += 3601  # más de una hora después: la ventana horaria expiró
            self.assertTrue(abuse_guard.allowed(ip))

    def test_limite_diario_corta_aunque_la_hora_no_este_llena(self):
        _env.set_env(self, TURNOS_AI_PER_HOUR="100", TURNOS_AI_PER_DAY="2")
        ip = "8.8.8.8"
        self.assertTrue(abuse_guard.allowed(ip))
        self.assertTrue(abuse_guard.allowed(ip))
        self.assertFalse(abuse_guard.allowed(ip))

    def test_turnstile_ok_sin_secret_no_gatea(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET=None)
        request = FakeHandler("/x")
        self.assertTrue(abuse_guard.turnstile_ok(
            request.headers, request.client_address[0], ""))

    def test_turnstile_sin_token_deja_pasar_al_paciente(self):
        """Gate BLANDO en las rutas de IA del paciente (2026-07-20). El widget
        de Cloudflare a veces no resuelve nunca y el token llega vacío: antes
        eso era un 403 en cada reintento y la persona no podía subir una orden
        válida. Ahora pasa — el rate limit por IP es el que corta el abuso."""
        _env.set_env(self, {"TURNOS_TURNSTILE_SECRET": "un-secret-de-prueba", "TURNOS_TURNSTILE_SITEKEY": "0xSITEKEY-DE-TEST"})
        turnstile.invalidar_cache()
        # Sin este assert el test pasaría vacío si el gate estuviera apagado:
        # lo que se prueba es que pasa CON el gate corriendo.
        with patch.object(turnstile, "encendido", return_value=True):
            request = FakeHandler("/x")
            self.assertTrue(turnstile.activo_from(request.headers))
            self.assertTrue(abuse_guard.turnstile_ok(
                request.headers, request.client_address[0], ""))

    def test_sin_sitekey_el_gate_no_corre(self):
        """Secret sin sitekey = el cliente no puede renderizar widget ni
        producir token. Exigirlo sería un lazo cerrado, así que no corre."""
        _env.set_env(self, {"TURNOS_TURNSTILE_SECRET": "un-secret-de-prueba", "TURNOS_TURNSTILE_SITEKEY": None})
        request = FakeHandler("/x")
        self.assertFalse(turnstile.activo_from(request.headers))
        self.assertIsNone(turnstile.sitekey_from(request.headers))

    def test_login_sigue_fail_closed(self):
        """El aflojamiento es SÓLO para las rutas de IA del paciente: los gates
        de verdad (login de admin/paciente) siguen rechazando sin token."""
        _env.set_env(self, {"TURNOS_TURNSTILE_SECRET": "un-secret-de-prueba", "TURNOS_TURNSTILE_SITEKEY": "0xSITEKEY-DE-TEST"})
        with patch.object(turnstile, "encendido", return_value=True):
            request = FakeHandler("/x")
            self.assertFalse(turnstile.verificar_from(
                request.headers, request.client_address[0], ""))

    def test_client_ip_ignora_x_forwarded_for_sin_verificar(self):
        """proxy_trust design (fail-closed): X-Forwarded-For is client-
        controllable when the request comes straight to origin (no CDN in
        front — see AGENTS.md), so the abuse cooldown must never key on it.
        A bare X-Forwarded-For, even a plausible one, is ignored — the real
        socket peer is what gets the quota."""
        h = FakeHandler("/x", headers={"X-Forwarded-For": "5.5.5.5, 6.6.6.6"}, ip="7.7.7.7")
        self.assertEqual(abuse_guard.client_ip(
            h.headers, h.client_address[0]), "7.7.7.7")
        # Last-resort branch (no forwarded headers): the answer must be the
        # real peer in BOTH serving topologies. The handler carries the same
        # signed marker app.py would attach for that peer — with TURNOS_ASGI
        # unset it is ignored (socket peer rules); with TURNOS_ASGI=1 it is
        # what proves the peer, exercising the fail-closed verification path.
        from app.core import proxy_trust
        self.addCleanup(config._reset_cache_para_tests)   # after the secret is put back
        _env.set_env(self, {"TURNOS_PROXY_TRUST_SECRET": "test-proxy-trust-abuso"})
        config._reset_cache_para_tests()
        h2 = FakeHandler("/x", headers={proxy_trust.HEADER: proxy_trust.sign("7.7.7.7")},
                         ip="7.7.7.7")
        self.assertEqual(abuse_guard.client_ip(
            h2.headers, h2.client_address[0]), "7.7.7.7")
        # The trusted mechanism wins over a spoofed X-Forwarded-For even
        # when both are present on the same request — the abuse cooldown
        # keys on the VERIFIED peer, never on whatever the client claims.
        h3 = FakeHandler("/x", headers={
            "X-Forwarded-For": "5.5.5.5",
            proxy_trust.HEADER: proxy_trust.sign("7.7.7.7"),
        }, ip="7.7.7.7")
        self.assertEqual(abuse_guard.client_ip(
            h3.headers, h3.client_address[0]), "7.7.7.7")


# ---- GET /upload-order (página del asistente) -----------------------------------

class TestPaginaPreparacion(unittest.TestCase):
    def test_ruta_get_sirve_pagina(self):
        h = _CLIENT.request("GET", "/upload-order")
        self.assertEqual(h.status_code, 200)
        html = h.content.decode()
        self.assertIn("Subí tu orden", html)
        self.assertIn('id="prep-title"', html)
        self.assertIn('id="prep-sub"', html)
        self.assertIn('/vendor/lottie/lottie_light.min.js?v=1', html)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.module_url("modules/turnos/upload-order.mjs"), html)
        self.assertIn('<script type="importmap">', html)
        self.assertIn("takePendingFile: takePendingFile", html)
        self.assertIn("uploadUnavailable: uploadUnavailable", html)

    def test_contacto_se_muestra_una_vez_en_la_pagina_y_si_falla_la_ia(self):
        for ia_ok in (True, False):
            with self.subTest(ia_ok=ia_ok):
                with patch.object(preparation, "_contact_html", return_value="CONTACTO SEGURO"), \
                        patch("app.ai.ai_health.available", return_value=ia_ok):
                    h = _CLIENT.request("GET", "/upload-order")
                html = h.content.decode()
                self.assertEqual(html.count('class="wizard-help"'), 1)
                self.assertEqual(html.count("CONTACTO SEGURO"), 1)

    def test_sin_contacto_valido_no_aparece_el_bloque_de_ayuda(self):
        with patch.object(preparation, "_contact_html", return_value=""):
            h = _CLIENT.request("GET", "/upload-order")
        self.assertNotIn('class="wizard-help"', h.content.decode())

    def test_contacto_whatsapp_usa_el_formato_compartido(self):
        raw = "02254 15 123456"
        html = preparation._contact_html({"name": "CAPS", "whatsapp": raw})
        self.assertIn('href="https://wa.me/5492254123456"', html)
        self.assertIn(">+54 9 2254 12-3456</a>", html)
        self.assertNotIn(">02254 15 123456</a>", html)

    def test_contacto_telefono_usa_el_formato_compartido(self):
        raw = "2254 401234"
        html = preparation._contact_html({"name": "CAPS", "phone": raw})
        self.assertIn('href="tel:+542254401234"', html)
        self.assertIn(">+54 2254 40-1234</a>", html)
        self.assertNotIn(">2254 401234</a>", html)

    @reference_pack_test
    def test_servicio_va_en_breadcrumb_y_no_en_subtitulo(self):
        h = _CLIENT.request("GET", "/upload-order?service=ecografia-hospital")
        html = h.content.decode()
        crumb = html.split('<nav class="svc-crumb"', 1)[1].split("</nav>", 1)[0]
        self.assertLess(crumb.index("Hospital de Pinamar"), crumb.index("Ecografía"))
        self.assertIn('href="/center/hospital-pinamar"', crumb)
        self.assertIn('href="/service/ecografia-hospital"', crumb)
        self.assertIn('<p class="svc-sub" id="prep-sub" hidden></p>', html)

    def test_resultado_no_promete_instrucciones_que_no_existen(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app", "modules", "turnos", "upload-order.mjs"), encoding="utf-8") as source:
            frontend = source.read()
        # "Dónde sacar el turno" pasó por T() (i18n, 2026-08-04): el título
        # se arma una vez en `tituloTurnos` y se reusa en el spinner y en el
        # section-heading, en vez de repetir el literal en cada lugar.
        self.assertIn('T("upload.result.where_title", null, "Dónde sacar el turno")', frontend)
        self.assertIn('setPage(cards ? tituloPreparacion(prepEntries) : tituloTurnos, "")', frontend)
        self.assertIn('"Cómo prepararte para una ecografía abdominal"', frontend)
        self.assertIn("subEl.hidden = !sub", frontend)
        self.assertIn('var leidoSec = cards ? "" : queLeyoHtml', frontend)
        self.assertIn("var mostrarNombreTarjeta = prepEntries.length > 1", frontend)
        self.assertLess(
            frontend.index("prepSec +"),
            frontend.index("leidoSec +"),
        )
        self.assertLess(
            frontend.index("leidoSec +"),
            frontend.index('<div class="prep-sec"><div class="prep-sec-h">\' + esc(tituloTurnos) + \'</div>'),
        )
        self.assertIn("¿Querés buscar y subir la orden correcta?", frontend)
        self.assertNotIn("La preparación puede variar según el centro", frontend)
        self.assertNotIn("Subir otra orden", frontend)
        self.assertNotIn("el centro te indica la preparación", frontend)

    def test_eval_jerarquia_tipografica_tiene_un_estilo_dominante(self):
        """The preparation card must not recreate four equally dense text
        styles. Section headings own the dark/bold treatment; explanatory
        copy stays regular. The note below the fasting emphasis is normal body
        text, not a third gray visual treatment."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app", "modules", "turnos", "upload-order.mjs"), encoding="utf-8") as source:
            frontend = source.read()
        self.assertIn(
            ".prep-sub{font-size:18px;line-height:1.3;font-weight:700;"
            "color:var(--text);",
            frontend,
        )
        self.assertIn(".prep-block{margin:24px 0 0;}", frontend)
        self.assertIn(".prep-resumen{font-size:17px;line-height:1.5;color:var(--text);", frontend)
        self.assertIn(".prep-nota{font-size:16px;line-height:1.5;color:var(--text);", frontend)
        # "Referencias" pasó por T() (i18n, 2026-08-04) — mismo texto, ahora traducible.
        self.assertIn('<details class="prep-fuentes"><summary>\' + esc(T("upload.result.sources", null, "Referencias"))', frontend)
        self.assertNotIn("text-transform:uppercase", frontend)
        self.assertNotIn("font-weight:700;color:var(--text-3)", frontend)

    def test_wizard_de_turnos_conserva_el_turno_antes_de_la_preparacion(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as source:
            frontend = source.read()
        success = frontend[
            frontend.index("function renderSuccess"):
            frontend.index("function submit", frontend.index("function renderSuccess"))
        ]
        self.assertLess(success.index("successHtml(false)"), success.index("prepHtml +"))

    def test_ruta_desconocida_false(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                h = _CLIENT.request(method, "/api/otra-cosa")
                self.assertEqual(h.status_code, 404)

    def test_api_preparacion_ya_no_existe(self):
        # El endpoint murió (2026-07-16): el asistente lee la orden vía
        # /api/analyze-order, compartido con el wizard de turnos.
        h = _CLIENT.request("POST", "/api/preparacion")
        self.assertEqual(h.status_code, 404)


# ---- Phase 3 tie-in: /api/analyze-order ahora también anti-abuso -------------

class TestAnalizarOrdenAntiAbuso(unittest.TestCase):
    def setUp(self):
        abuse_guard._HITS.clear()
        _env.set_env(self, TURNOS_TURNSTILE_SECRET=None, TURNOS_AI_PER_HOUR=None,
                     TURNOS_AI_PER_DAY=None)
        self._orig_pending = booking_requests.REFERRALS_DIR
        booking_requests.REFERRALS_DIR = tempfile.mkdtemp(prefix="ordenes-prep-test-")

    def tearDown(self):
        shutil.rmtree(booking_requests.REFERRALS_DIR, ignore_errors=True)
        booking_requests.REFERRALS_DIR = self._orig_pending
        abuse_guard._HITS.clear()

    def test_limite_429(self):
        _env.set_env(self, TURNOS_AI_PER_HOUR="1", TURNOS_AI_PER_DAY="100")
        body = {"servicio_id": "cardiologia", "especialidad": "Cardiología", **_orden_body()}
        h1 = TestClient(_TEST_APP, client=("33.33.33.33", 50000)).request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h1.status_code, 200)
        h2 = TestClient(_TEST_APP, client=("33.33.33.33", 50000)).request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h2.status_code, 429)

    def test_turnstile_sin_token_no_bloquea_la_orden(self):
        """Una orden válida se analiza aunque el widget no haya dado token
        (responsable del producto, 2026-07-20: en prod tiraba 403 una y otra vez). El turno en un
        hospital público no se cae por un captcha que no renderizó."""
        _env.set_env(self, TURNOS_TURNSTILE_SECRET="secreto-de-test",
                     TURNOS_TURNSTILE_SITEKEY="0xSITEKEY-DE-TEST")
        body = {"servicio_id": "cardiologia", "especialidad": "Cardiología", **_orden_body()}
        h = TestClient(_TEST_APP, client=("22.22.22.22", 50000)).request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h.status_code, 200)

    def test_ok_adjunta_preparacion_al_ai_json(self):
        body = {"servicio_id": "audiometria-ostende", "especialidad": "Audiometría", **_orden_body()}
        h = TestClient(_TEST_APP, client=("11.11.11.11", 50000)).request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h.status_code, 200)
        self.assertIn("preparacion", h.json()["ai"])

    def test_ok_expone_preparacion_no_reconocidos(self):
        # El backend conserva lo no reconocido para diagnóstico y revisión
        # interna. La interfaz no lo convierte en una promesa de instrucciones
        # futuras si el catálogo no tiene indicaciones concretas.
        body = {"servicio_id": "audiometria-ostende", "especialidad": "Audiometría", **_orden_body()}
        h = TestClient(_TEST_APP, client=("11.11.11.12", 50000)).request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h.status_code, 200)
        self.assertIn("preparacion_no_reconocidos", h.json()["ai"])
        self.assertIsInstance(h.json()["ai"]["preparacion_no_reconocidos"], list)


if __name__ == "__main__":
    unittest.main()


class TestPrepJsEnLasPaginas(unittest.TestCase):
    """prep.mjs decides which sections a preparation has. Its consumers —
    including the native admin-preparations controller — import it from
    the fingerprint manifest before their first render; no compatibility
    global or classic prep script may remain."""

    _CONSUMIDORES = (
        "modules/turnos/service.mjs",
        "platform/account-requests.mjs",
        "modules/turnos/upload-order.mjs",
        "platform/admin-preparations.mjs",
    )

    def _asset_url(self, name):
        from app.core import static_fingerprint
        if name.startswith("modules/"):
            return static_fingerprint.module_url(name)
        return static_fingerprint.url(name)

    def _assert_pareado(self, html, donde):
        from app.core import static_fingerprint
        usa = [c for c in self._CONSUMIDORES
               if self._asset_url(c) in html]
        if not usa:
            return
        self.assertIn(static_fingerprint.url("platform/prep.mjs"), html,
                      f"{donde} carga {usa} pero no publica platform/prep.mjs")
        self.assertNotIn("/prep.js", html)

    def test_paginas_de_servicio(self):
        from app.patient_web import site_catalog, site_page_service
        for sid in ("vacunatorio-pinamar", "ecografia-hospital", "cardiologia-hospital"):
            s = site_catalog._service_by_id(sid)
            if not s:
                continue
            self._assert_pareado(site_page_service._page_service(sid).decode(), f"/service/{sid}")

    def test_pagina_subi_tu_orden(self):
        h = _CLIENT.request("GET", "/upload-order")
        self._assert_pareado(h.content.decode(), "/upload-order")

    def test_api_preparaciones_sirve_el_catalogo(self):
        # el cliente ya no lee /webdata/estudios-preparacion.json directo: ese
        # camino se saltea las ediciones hechas desde el panel
        h = _CLIENT.request("GET", "/api/preparation-studies")
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["estudios"])

    def test_ningun_cliente_lee_el_json_estatico(self):
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            self.assertNotIn("/webdata/estudios-preparacion.json", f.read())
