"""test_slot_holds.py — soft-lock de turnos (slot_holds.py + db.py:turno_holds).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_slot_holds -v

Decisión 2026-07-16 (reunión con el mantenedor): hoy nada bloquea un slot; elegir un
horario concreto en el wizard toma un hold server-side por unos minutos
mientras se completa el resto del formulario — sólo MIRAR la grilla nunca
bloquea nada. Cubre: mecánica del hold en db.py (adquirir/conflicto/renovar/
mover/vencer), el endpoint /api/slot-hold, ocupados() (holds + aprobadas
futuras), el filtrado en patient_web/site_*.py (banda + grilla del wizard) y el ciclo
completo en booking_requests.py (crear/aprobar/rechazar).

Sigue el patrón de test_doctor_appointments.py: webdata propio vía
TURNOS_WEBDATA_DIR (aislado del real) + base de test dedicada
`sana_turnos_test_reservas`; los endpoints se ejercitan con TestClient.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_booking  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_reports  # noqa: E402
from app.patient_web import catalog      # noqa: E402
from app.patient_web import site_availability, site_base, site_catalog
from app.modules.turnos import slot_holds      # noqa: E402
from app.modules.turnos import booking_requests   # noqa: E402
from app.auth import admin_auth    # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import turnstile          # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""   # no leer el .env real (mismo truco que los otros tests)
from app.ai import abuse_guard  # noqa: E402
abuse_guard.ENV_PATH = ""
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev

_ART = timezone(timedelta(hours=-3))

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reservas", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE slot_holds RESTART IDENTITY")
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")


# ---- fixtures del catálogo (aisladas del webdata real) ----------------------
# Agenda bien ancha (todos los días, 00:00-23:40 cada 20 min) para que siempre
# haya de sobra: filtrar el primer slot ocupado tiene que dejar un segundo
# disponible sin pelear con la hora real a la que corre el test.
_AGENDA_ANCHA = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00", "hasta": "23:40", "cada_min": 20}
_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "cardiologia", "nombre": "Cardiología", "centro": "centro-a",
     "modo": "solicitud", "agenda": _AGENDA_ANCHA},
    {"id": "vacunacion", "nombre": "Vacunación", "centro": "centro-a", "modo": "walkin"},
    # Walk-in que ADEMÁS abre su listado el mismo día (the reference-deployment counterpart, reunión
    # 2026-07-24): el único otro caso en que `_offers_appointments` da
    # verdadero para un walk-in — y por eso también tiene que ser holdable
    # (data-integrity guards, 2026-08-05, tier 14: la mesa tiene que poder
    # chocar contra un hold vigente acá también, no sólo en `solicitud*`).
    {"id": "guardia-mismodia", "nombre": "Guardia (mismo día)", "centro": "centro-a",
     "modo": "walkin_con_orden", "apertura_mismo_dia": True},
]}
_MEDICOS = {"medicos": []}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump(_CENTROS, f)
    with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
        json.dump(_SERVICIOS, f)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump(_MEDICOS, f)


class TestServicioHoldable(unittest.TestCase):
    """`_service_holdable` == `site_availability._offers_appointments` (data-integrity
    guards, 2026-08-05, tier 14) — sin base de datos, sólo dicts de catálogo."""

    def test_solicitud_holdable(self):
        self.assertTrue(slot_holds._service_holdable({"modo": "solicitud"}))

    def test_solicitud_con_orden_holdable(self):
        self.assertTrue(slot_holds._service_holdable({"modo": "solicitud_con_orden"}))

    def test_walkin_liso_no_holdable(self):
        self.assertFalse(slot_holds._service_holdable({"modo": "walkin"}))

    def test_walkin_con_orden_sin_apertura_no_holdable(self):
        self.assertFalse(slot_holds._service_holdable({"modo": "walkin_con_orden"}))

    def test_walkin_con_orden_con_apertura_mismo_dia_si_holdable(self):
        self.assertTrue(slot_holds._service_holdable(
            {"modo": "walkin_con_orden", "apertura_mismo_dia": True}))

    def test_walkin_liso_con_apertura_mismo_dia_si_holdable(self):
        self.assertTrue(slot_holds._service_holdable(
            {"modo": "walkin", "apertura_mismo_dia": True}))

    def test_no_dict_no_holdable(self):
        self.assertFalse(slot_holds._service_holdable(None))
        self.assertFalse(slot_holds._service_holdable("cardiologia"))


def _primeros_slots(n=2):
    """[(dia,hora), ...] — los próximos `n` slots reales de _AGENDA_ANCHA
    calculados AHORA (no hardcodeados: la agenda es tan ancha que siempre hay
    de sobra, pero la fecha/hora exacta depende de cuándo corre el test)."""
    slots = site_availability._agenda_slots(_AGENDA_ANCHA)
    return [(s["dia"], s["hora"]) for s in slots[:n]]


# ============================================================================
# Mecánica del hold en db.py — sin webdata, sólo la tabla turno_holds.
# ============================================================================

# El día de los holds es RELATIVO a hoy: un hold ligado a una solicitud sólo
# bloquea mientras el turno sigue en el futuro (ver `test_vincular_a_slot_pasado
# _no_bloquea` acá abajo, que prueba justo lo contrario). Con la fecha fija que
# había, el test se puso en rojo solo el 2026-08-01 — el día que el calendario
# la alcanzó.
_DIA = (datetime.now(_ART) + timedelta(days=7)).date().isoformat()


class TestDbHolds(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_adquirir_libre(self):
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA"))

    def test_segundo_token_conflicto(self):
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA"))
        self.assertFalse(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))

    def test_carrera_un_solo_ganador(self):
        """N hilos pelean por el MISMO slot a la vez (INSERT ... ON CONFLICT en
        una sola sentencia): exactamente uno gana. Los tests de arriba lo prueban
        secuencialmente; este prueba la atomicidad real bajo concurrencia."""
        import threading
        n = 12
        barrera = threading.Barrier(n)
        ganados = []
        lock = threading.Lock()

        def intentar(i):
            barrera.wait()  # que todos peguen a la vez
            ok = db_slots.hold_acquire("cardiologia", _DIA, "10:00", f"tok{i}")
            if ok:
                with lock:
                    ganados.append(i)

        hilos = [threading.Thread(target=intentar, args=(i,)) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(len(ganados), 1, f"esperaba 1 ganador, hubo {len(ganados)}")
        activos = [h for h in db_slots.holds_active()
                   if h["servicio_id"] == "cardiologia" and h["hora"] == "10:00"]
        self.assertEqual(len(activos), 1)
        self.assertEqual(activos[0]["token"], f"tok{ganados[0]}")

    def test_mismo_token_renueva(self):
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA"))
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA"))

    def test_mismo_token_slot_nuevo_libera_el_anterior(self):
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA"))
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:20", "tokA"))
        # el slot viejo quedó libre: otro token lo puede tomar
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))
        # y el token original conserva SÓLO el nuevo
        activos = {(h["dia"], h["hora"]) for h in db_slots.holds_active() if h["token"] == "tokA"}
        self.assertEqual(activos, {(_DIA, "09:20")})

    def test_expira_libera(self):
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA", ttl_min=15))
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("UPDATE slot_holds SET expires = now() - interval '1 second' WHERE token='tokA'")
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))

    def test_liberar(self):
        db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA")
        db_slots.hold_release("tokA")
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))

    def test_liberar_no_toca_holds_de_otro_token(self):
        db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA")
        db_slots.hold_release("tokA-otro-que-no-existe")
        self.assertFalse(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))

    def test_holds_activos(self):
        db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA")
        activos = db_slots.holds_active()
        self.assertTrue(any(h["token"] == "tokA" and h["servicio_id"] == "cardiologia"
                            and h["dia"] == _DIA and h["hora"] == "09:00" for h in activos))

    def test_vincular_bloquea_hasta_liberar_la_solicitud(self):
        db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokA")
        sid = db_booking.create_booking_request("20111222", "Ana", "Gómez", "1990-01-01", "wa:1",
                                 "cardiologia", "centro-a")
        expira = datetime.fromisoformat(_DIA + "T09:00").replace(tzinfo=_ART)
        db_slots.hold_link_booking_request("tokA", sid, expira)
        # ligado a la solicitud: otro token NO puede tomarlo
        self.assertFalse(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))
        db_slots.holds_release_booking_request(sid)
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))

    def test_vincular_a_slot_pasado_no_bloquea(self):
        db_slots.hold_acquire("cardiologia", "2020-01-01", "09:00", "tokA")
        sid = db_booking.create_booking_request("20111223", "Ana", "Gómez", "1990-01-01", "wa:1",
                                 "cardiologia", "centro-a")
        expira_pasada = datetime(2020, 1, 1, 9, 0, tzinfo=_ART)
        db_slots.hold_link_booking_request("tokA", sid, expira_pasada)
        activos = [h for h in db_slots.holds_active() if h["token"] == "tokA"]
        self.assertEqual(activos, [])

    def test_holds_liberar_solicitud_sin_holds_no_rompe(self):
        db_slots.holds_release_booking_request(999999)  # no existe nada ligado — no-op


class TestHoldColumnTypes(unittest.TestCase):
    """problems.md №13: `slot_holds.day/time` are `date`/`time`, so «9:00» and
    «09:00» are the same slot and «2024-13-45» cannot be stored, even by a
    writer that bypasses the code. The callers still see ISO text."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def _types(self):
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_name='slot_holds' AND column_name IN ('day', 'time')")
            return dict(cur.fetchall())

    def test_las_columnas_son_date_y_time(self):
        self.assertEqual(self._types(), {"day": "date", "time": "time without time zone"})

    def test_la_base_rechaza_una_fecha_imposible_y_un_duplicado_con_otro_formato(self):
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            with self.assertRaises(psycopg.errors.DatetimeFieldOverflow):
                cur.execute("INSERT INTO slot_holds (service_id, day, time, token, expires) "
                            "VALUES ('cardiologia', '2024-13-45', '09:00', 't', now())")
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("INSERT INTO slot_holds (service_id, day, time, token, expires) "
                        "VALUES ('cardiologia', %s, '09:00', 'a', now())", (_DIA,))
            with self.assertRaises(psycopg.errors.UniqueViolation):
                cur.execute("INSERT INTO slot_holds (service_id, day, time, token, expires) "
                            "VALUES ('cardiologia', %s, '9:00', 'b', now())", (_DIA,))

    def test_nueve_y_cero_nueve_es_el_mismo_slot(self):
        self.assertTrue(db_slots.hold_acquire("cardiologia", _DIA, "9:00", "tokA"))
        self.assertFalse(db_slots.hold_acquire("cardiologia", _DIA, "09:00", "tokB"))
        self.assertEqual([(h["dia"], h["hora"]) for h in db_slots.holds_active()], [(_DIA, "09:00")])

    def test_un_valor_imposible_no_toma_el_slot_ni_levanta(self):
        for dia, hora in (("2024-13-45", "09:00"), (_DIA, "25:61"), (_DIA, ""), ("", "09:00"),
                          (_DIA + "x", "09:00")):
            self.assertFalse(db_slots.hold_acquire("cardiologia", dia, hora, "tokA"), (dia, hora))
        self.assertEqual(db_slots.holds_active(), [])

    def test_el_endpoint_rechaza_un_dia_o_una_hora_que_no_existen(self):
        for dia, hora in (("2026-13-45", "09:00"), (_DIA, "25:61")):
            body = json.dumps({"servicio_id": "cardiologia", "dia": dia, "hora": hora}).encode()
            status, payload = slot_holds.create_hold("203.0.113.9", body, lambda: 3)
            self.assertEqual((status, payload["error"]), (400, "servicio_id/dia/hora inválidos"), (dia, hora))

    def test_los_lectores_siguen_recibiendo_texto_iso(self):
        db_slots.hold_acquire("cardiologia", _DIA, "09:20", "tokA")
        sid = db_booking.create_booking_request("20111224", "Ana", "Gómez", "1990-01-01", "wa:1",
                                        "cardiologia", "centro-a")
        db_slots.hold_link_booking_request("tokA", sid, datetime.now(_ART) + timedelta(days=8))
        self.assertEqual(db_slots.hold_of_booking_request(sid), {"dia": _DIA, "hora": "09:20"})
        self.assertEqual(db_reports.reports_holds_day_hour(dias=1), [(_DIA, "09:20")])
        self.assertEqual(db_slots.active_booking_request_by_slot("20111224", "cardiologia", _DIA, "09:20")["id"], sid)
        self.assertIsNone(db_slots.active_booking_request_by_slot("20111224", "cardiologia", "2024-13-45", "09:20"))


# ============================================================================
# Base compartida por los tests que necesitan webdata + DB juntos.
# ============================================================================

class ReservasFixtureTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._tmp = tempfile.mkdtemp(prefix="slot-holds-test-")
        webdir = os.path.join(self._tmp, "webdata")
        _write_fixtures(webdir)
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(slot_holds.invalidate)
        self.addCleanup(site_base._CACHE.clear)
        self.addCleanup(catalog.invalidate)
        self.addCleanup(catalog._JSON_CACHE.clear)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        slot_holds.invalidate()
        slot_holds._HITS.clear()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()


# ---- endpoint HTTP -----------------------------------------------------------

class TestReservasEndpoint(ReservasFixtureTestCase):
    def _post(self, body, ip="1.2.3.4"):
        # proxy_trust.real_peer_from() (core/proxy_trust.py) fails closed on
        # a spoofable X-Forwarded-For header outside signed-proxy mode and
        # resolves the peer from the ASGI connection's client host instead —
        # every TestClient defaults to the SAME fixed host, so a header
        # alone can't distinguish "IP"s for the rate limiter. Bind the
        # TestClient to the fake peer directly so `slot_holds._HITS`
        # actually keys on different identities per call.
        return TestClient(app_module.app, client=(ip, 12345)).post(
            "/api/slot-hold", json=body)

    def test_adquirir_ok(self):
        dia, hora = _primeros_slots(1)[0]
        h = self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora})
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["token"])
        self.assertEqual(h.json()["ttl_min"], slot_holds._TTL_MIN)

    def test_segundo_token_409(self):
        dia, hora = _primeros_slots(1)[0]
        h1 = self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora, "token": "tokA"})
        self.assertEqual(h1.status_code, 200)
        h2 = self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora, "token": "tokB"})
        self.assertEqual(h2.status_code, 409)
        self.assertEqual(h2.json()["error"], "turno_tomado")

    def test_mismo_token_renueva_200(self):
        dia, hora = _primeros_slots(1)[0]
        self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora, "token": "tokA"})
        h2 = self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora, "token": "tokA"})
        self.assertEqual(h2.status_code, 200)

    def test_dia_hora_invalidos_400(self):
        h = self._post({"servicio_id": "cardiologia", "dia": "no-es-fecha", "hora": "09h00"})
        self.assertEqual(h.status_code, 400)

    def test_falta_servicio_id_400(self):
        dia, hora = _primeros_slots(1)[0]
        h = self._post({"dia": dia, "hora": hora})
        self.assertEqual(h.status_code, 400)

    def test_servicio_walkin_no_reservable_400(self):
        h = self._post({"servicio_id": "vacunacion", "dia": _DIA, "hora": "09:00"})
        self.assertEqual(h.status_code, 400)

    def test_servicio_walkin_apertura_mismo_dia_si_es_reservable(self):
        # data-integrity guards (2026-08-05, tier 14): antes de la extensión
        # de `_service_holdable` este mismo POST daba 400 "servicio no
        # reservable" — y un paciente jamás podía sostener un hold sobre un
        # slot de mismo-día, así que la mesa tampoco tenía nada que chocar.
        h = self._post({"servicio_id": "guardia-mismodia", "dia": _DIA, "hora": "09:00"})
        self.assertEqual(h.status_code, 200, h.text)
        self.assertTrue(h.json()["token"])

    def test_servicio_inexistente_400(self):
        h = self._post({"servicio_id": "no-existe", "dia": _DIA, "hora": "09:00"})
        self.assertEqual(h.status_code, 400)

    def test_delete_libera(self):
        dia, hora = _primeros_slots(1)[0]
        self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora, "token": "tokA"})
        rd = TestClient(app_module.app).delete("/api/slot-hold/tokA")
        self.assertEqual(rd.json(), {"ok": True})
        h2 = self._post({"servicio_id": "cardiologia", "dia": dia, "hora": hora, "token": "tokB"})
        self.assertEqual(h2.status_code, 200)

    def test_delete_sin_token_sigue_devolviendo_ok(self):
        rd = TestClient(app_module.app).delete("/api/slot-hold/no-existia")
        self.assertEqual(rd.json(), {"ok": True})

    def test_metodo_get_no_lo_maneja(self):
        # Native contract: the route is POST-only and nothing else claims the
        # path on GET, so the request falls through to the native terminal —
        # which answers /api/* with the flat JSON 404, never Starlette's 405
        # (test_module_router_fallthrough.py pins the same contract).
        r = TestClient(app_module.app).get("/api/slot-hold")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "not found"})

    def test_rate_limit_por_ip(self):
        _env.set_env(self, TURNOS_HOLD_PER_HOUR="2")
        slots = _primeros_slots(3)
        self._post({"servicio_id": "cardiologia", "dia": slots[0][0], "hora": slots[0][1]}, ip="9.9.9.9")
        self._post({"servicio_id": "cardiologia", "dia": slots[1][0], "hora": slots[1][1]}, ip="9.9.9.9")
        h3 = self._post({"servicio_id": "cardiologia", "dia": slots[2][0], "hora": slots[2][1]}, ip="9.9.9.9")
        self.assertEqual(h3.status_code, 429)
        # otra IP no está limitada
        h4 = self._post({"servicio_id": "cardiologia", "dia": slots[2][0], "hora": slots[2][1]}, ip="8.8.8.8")
        self.assertEqual(h4.status_code, 200)


# ---- ocupados(): holds + aprobadas futuras -----------------------------------

class TestOcupados(ReservasFixtureTestCase):
    def test_incluye_hold_activo(self):
        dia, hora = _primeros_slots(1)[0]
        db_slots.hold_acquire("cardiologia", dia, hora, "tokA")
        slot_holds.invalidate()
        self.assertIn(("cardiologia", dia, hora), slot_holds.taken())

    def test_excluye_el_propio_token(self):
        dia, hora = _primeros_slots(1)[0]
        db_slots.hold_acquire("cardiologia", dia, hora, "tokMio")
        slot_holds.invalidate()
        self.assertIn(("cardiologia", dia, hora), slot_holds.taken())
        self.assertNotIn(("cardiologia", dia, hora), slot_holds.taken(exclude_token="tokMio"))

    def test_incluye_aprobada_futura(self):
        dia, hora = _primeros_slots(1)[0]
        sid = db_booking.create_booking_request("20111222", "Ana", "Gómez", "1990-01-01", "wa:1",
                                 "cardiologia", "centro-a")
        turno_dt = datetime(int(dia[:4]), int(dia[5:7]), int(dia[8:10]),
                            int(hora[:2]), int(hora[3:5]), tzinfo=_ART) + timedelta(days=90)
        db_booking.approve(sid, turno_dt, {"estado": "skip"})
        slot_holds.invalidate()
        dia_f, hora_f = turno_dt.strftime("%Y-%m-%d"), turno_dt.strftime("%H:%M")
        self.assertIn(("cardiologia", dia_f, hora_f), slot_holds.taken())

    def test_no_incluye_aprobada_pasada(self):
        pasado = datetime.now(_ART) - timedelta(days=5)
        sid = db_booking.create_booking_request("20111224", "Ana", "Gómez", "1990-01-01", "wa:1",
                                 "cardiologia", "centro-a")
        db_booking.approve(sid, pasado, {"estado": "skip"})
        slot_holds.invalidate()
        dia_p, hora_p = pasado.strftime("%Y-%m-%d"), pasado.strftime("%H:%M")
        self.assertNotIn(("cardiologia", dia_p, hora_p), slot_holds.taken())


class TestOfertaDeListaDeEspera(ReservasFixtureTestCase):
    """«Por 5 minutos este turno es tuyo, garantizado» (mail de la oferta): una oferta viva de la lista
    de espera tiene el horario tomado como un hold — la grilla no lo ofrece y nadie lo toma — hasta que
    la ventana se cierra, la paciente dice que no o lo toma (entonces lo sostiene su turno aprobado)."""

    def _oferta(self, ventana_min=5):
        from app.modules.turnos import waitlist
        dia, hora = _primeros_slots(1)[0]
        turno_dt = datetime(int(dia[:4]), int(dia[5:7]), int(dia[8:10]),
                            int(hora[:2]), int(hora[3:5]), tzinfo=_ART) + timedelta(days=90)
        sid = db_booking.create_booking_request("20111301", "Ana", "Gómez", "1990-01-01", "wa:1",
                                        "cardiologia", "centro-a")
        db_booking.approve(sid, turno_dt + timedelta(days=30), {"estado": "skip"})
        wid = waitlist.opt_in(sid, "20111301", "ana@example.invalid", "cardiologia", "centro-a",
                              turno_dt + timedelta(days=30))
        ahora = datetime.now(timezone.utc)
        token = "tok-oferta-" + "x" * 20
        db_slots.create_appointment_offer(turno_dt, "cardiologia", "centro-a", wid, token,
                                    ahora, ahora + timedelta(minutes=ventana_min))
        slot_holds.invalidate()
        return ("cardiologia", turno_dt.strftime("%Y-%m-%d"), turno_dt.strftime("%H:%M")), token

    def test_una_oferta_viva_ocupa_el_horario(self):
        slot, _token = self._oferta()
        self.assertIn(slot, slot_holds.taken())

    def test_nadie_toma_el_horario_de_una_oferta_viva(self):
        slot, _token = self._oferta()
        self.assertFalse(slot_holds.try_take(*slot, "tokOtro"))

    def test_con_la_ventana_cerrada_ya_no_lo_ocupa_aunque_el_barrido_no_corrio(self):
        slot, _token = self._oferta(ventana_min=-1)
        self.assertNotIn(slot, slot_holds.taken())
        self.assertTrue(slot_holds.try_take(*slot, "tokOtro"))

    def test_si_la_paciente_dice_que_no_el_horario_vuelve(self):
        from app.modules.turnos import waitlist
        slot, token = self._oferta()
        self.assertTrue(waitlist.reject_offer(token)[0])
        slot_holds.invalidate()
        self.assertNotIn(slot, slot_holds.taken())
        self.assertTrue(slot_holds.try_take(*slot, "tokOtro"))

    def test_tomar_la_oferta_pasa_el_horario_a_su_turno_sin_soltarlo_en_el_medio(self):
        from app.modules.turnos import appointment_offer
        slot, token = self._oferta()
        self.assertIn(slot, slot_holds.taken())          # sostenido por la oferta
        appointment_offer.accept_offer(None, token)
        slot_holds.invalidate()
        self.assertEqual([], slot_holds._live_offer_slots())   # la oferta ya no sostiene nada…
        self.assertIn(slot, slot_holds.taken())          # …lo sostiene el turno aprobado de la paciente
        self.assertFalse(slot_holds.try_take(*slot, "tokOtro"))


# ---- filtrado en patient_web/site_*.py: banda + grilla del wizard ----------------------

class TestHospitalFiltraOcupados(ReservasFixtureTestCase):
    def _servicio(self):
        s = site_catalog._service_by_id("cardiologia")
        self.assertIsNotNone(s)
        return s

    def test_turnos_servicio_filtra_slot_tomado(self):
        s = self._servicio()
        antes = site_availability._appointments_for_service(s)
        primero = antes["medicos"][0]["turnos"][0]
        db_slots.hold_acquire("cardiologia", primero["dia"], primero["hora"], "tokX")
        slot_holds.invalidate()
        despues = site_availability._appointments_for_service(s)
        self.assertNotIn(primero, despues["medicos"][0]["turnos"])

    def test_turnos_servicio_exclude_token_ve_su_propio_hold(self):
        s = self._servicio()
        antes = site_availability._appointments_for_service(s)
        primero = antes["medicos"][0]["turnos"][0]
        db_slots.hold_acquire("cardiologia", primero["dia"], primero["hora"], "tokX")
        slot_holds.invalidate()
        propio = site_availability._appointments_for_service(s, exclude_token="tokX")
        self.assertIn(primero, propio["medicos"][0]["turnos"])

    def test_reprogramacion_con_medico_ofrece_solo_su_agenda(self):
        servicio = {"id": "ergometria-hospital", "centro": "hospital",
                    "modo": "solicitud"}
        doctor = {"nombre": "Dra. Horario", "centro": "hospital",
                  "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00",
                                             "cada_min": 30, "cupos": {"web": 1}}],
                             "excepciones": []}}
        with patch("app.patient_web.site_catalog._doctors", return_value=[doctor]):
            result = site_availability._appointments_for_doctor(servicio, "Dra. Horario")
        horas = {t["hora"] for t in result["medicos"][0]["turnos"]}
        self.assertEqual(horas, {"10:00", "10:30"})

    def test_proximo_turno_best_salta_ocupado(self):
        s = self._servicio()
        best_antes = site_availability._next_appointment_best(s, ocupados=set())
        self.assertIsNotNone(best_antes)
        db_slots.hold_acquire("cardiologia", best_antes[0], best_antes[1], "tokX")
        slot_holds.invalidate()
        best_despues = site_availability._next_appointment_best(s)  # ocupados=None -> resuelve solo
        self.assertNotEqual(best_antes, best_despues)

    def test_banda_y_wizard_consistentes_con_hold_activo(self):
        """Invariante 'lo que la banda promete, el wizard lo sabe ofrecer' se
        mantiene con un hold activo: el turno que ofrece la banda
        (_service_next) está entre los que deja elegir _appointments_for_service."""
        s = self._servicio()
        antes = site_availability._appointments_for_service(s)
        primero = antes["medicos"][0]["turnos"][0]
        db_slots.hold_acquire("cardiologia", primero["dia"], primero["hora"], "tokX")
        slot_holds.invalidate()
        px = site_availability._service_next(s)
        self.assertEqual(px.get("kind"), "turno")
        oferta = site_availability._appointments_for_service(s)
        ofrecidos = [(t["dia"], t["hora"]) for t in oferta["medicos"][0]["turnos"]]
        self.assertIn((px["dia"], px["hora"]), ofrecidos)
        self.assertNotIn((primero["dia"], primero["hora"]), ofrecidos)


# ---- ciclo completo en booking_requests.py: crear / aprobar / rechazar -----------

_USER = {"dni": "27888999", "nombre": "Ana", "apellido": "López",
        "nacimiento": "1979-06-01", "sexo": "F", "contacto": "wa:11-5555"}


class TestSolicitudesConHold(ReservasFixtureTestCase):
    def setUp(self):
        super().setUp()
        self._orig_user = booking_requests._get_user
        booking_requests._get_user = lambda h: dict(_USER)

    def tearDown(self):
        booking_requests._get_user = self._orig_user
        super().tearDown()

    def _post_solicitud(self, extra):
        body = {"servicio_id": "cardiologia", "centro_slug": "centro-a"}
        body.update(extra)
        return TestClient(app_module.app).post("/api/requests", json=body)

    def _admin_headers(self):
        return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user())}

    def test_create_toma_y_vincula_el_hold(self):
        dia, hora = _primeros_slots(1)[0]
        h = self._post_solicitud({"slot_dia": dia, "slot_hora": hora})
        self.assertEqual(h.status_code, 200, h.text)
        sid = h.json()["id"]
        activos = [a for a in db_slots.holds_active()
                  if a["servicio_id"] == "cardiologia" and a["dia"] == dia and a["hora"] == hora]
        self.assertTrue(activos)  # sigue vigente (ligado a la solicitud recién creada)
        # otro token YA no puede tomarlo (está ligado, no suelto)
        self.assertFalse(db_slots.hold_acquire("cardiologia", dia, hora, "otro-token"))
        self.assertIsNotNone(db_booking.get_booking_request(sid))

    def test_create_409_si_otro_token_lo_tomo_antes(self):
        dia, hora = _primeros_slots(1)[0]
        self.assertTrue(db_slots.hold_acquire("cardiologia", dia, hora, "token-ajeno"))
        h = self._post_solicitud({"slot_dia": dia, "slot_hora": hora, "hold_token": "mio"})
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.json()["error"], "turno_tomado")
        self.assertEqual(db_booking.list_by_dni(_USER["dni"]), [])  # no se creó nada

    def test_create_sin_slot_no_toca_holds(self):
        # franja-only (sin turno concreto elegido): no hay slot que tomar.
        h = self._post_solicitud({"preferencia": "Cualquier horario"})
        self.assertEqual(h.status_code, 200, h.text)
        self.assertEqual(db_slots.holds_active(), [])

    def test_slot_dia_hora_invalidos_400(self):
        h = self._post_solicitud({"slot_dia": "no-es-fecha", "slot_hora": "9am"})
        self.assertEqual(h.status_code, 400)

    def test_aprobar_libera_el_hold(self):
        dia, hora = _primeros_slots(1)[0]
        h = self._post_solicitud({"slot_dia": dia, "slot_hora": hora})
        sid = h.json()["id"]
        ra = TestClient(app_module.app).post(
            f"/api/admin/requests/{sid}/approve",
            json={"turno_dt": f"{dia}T{hora}:00-03:00"}, headers=self._admin_headers())
        self.assertEqual(ra.status_code, 200, ra.text)
        restante = [a for a in db_slots.holds_active()
                   if a["servicio_id"] == "cardiologia" and a["dia"] == dia and a["hora"] == hora]
        self.assertEqual(restante, [])
        # el turno_dt de la solicitud aprobada toma la posta como bloqueo
        turno_dt = datetime(int(dia[:4]), int(dia[5:7]), int(dia[8:10]),
                            int(hora[:2]), int(hora[3:5]), tzinfo=_ART)
        if turno_dt > datetime.now(_ART):
            self.assertIn(("cardiologia", dia, hora), slot_holds.taken())

    def test_rechazar_libera_el_hold(self):
        dia, hora = _primeros_slots(1)[0]
        h = self._post_solicitud({"slot_dia": dia, "slot_hora": hora})
        sid = h.json()["id"]
        ra = TestClient(app_module.app).post(
            f"/api/admin/requests/{sid}/reject",
            json={"motivo": "La orden está vencida."}, headers=self._admin_headers())
        self.assertEqual(ra.status_code, 200, ra.text)
        restante = [a for a in db_slots.holds_active()
                   if a["servicio_id"] == "cardiologia" and a["dia"] == dia and a["hora"] == hora]
        self.assertEqual(restante, [])
        # y el slot vuelve a estar libre de verdad
        self.assertTrue(db_slots.hold_acquire("cardiologia", dia, hora, "tokNuevo"))

    def _libre(self, dia, hora):
        """Ni un hold vigente ni una aprobada futura tienen ese horario, y otro
        paciente (otro token) lo puede tomar de verdad."""
        slot_holds.invalidate()  # `taken()` cachea 3 s: leer lo que quedó DESPUÉS de cancelar
        self.assertNotIn(("cardiologia", dia, hora), slot_holds.taken())
        self.assertTrue(db_slots.hold_acquire("cardiologia", dia, hora, "otro-paciente"))

    def test_cancelar_pendiente_desde_la_cuenta_libera_el_hold(self):
        # N1 (e2e-journeys): el paciente cancela su pendiente en la cuenta y el
        # horario seguía oculto para todos hasta su hora.
        dia, hora = _primeros_slots(1)[0]
        sid = self._post_solicitud({"slot_dia": dia, "slot_hora": hora}).json()["id"]
        rc = TestClient(app_module.app).post(f"/api/requests/{sid}/cancel")
        self.assertEqual(rc.status_code, 200, rc.text)
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "cancelada")
        self._libre(dia, hora)

    def test_cancelar_aprobada_reprogramada_libera_el_horario_nuevo(self):
        # N1: aprobada → el paciente la mueve (la reprogramación liga un hold
        # nuevo a la solicitud) → la cancela. El hold del horario nuevo no
        # puede sobrevivir a la cancelación.
        (dia1, hora1), (dia2, hora2) = _primeros_slots(2)
        sid = self._post_solicitud({"slot_dia": dia1, "slot_hora": hora1}).json()["id"]
        ra = TestClient(app_module.app).post(
            f"/api/admin/requests/{sid}/approve",
            json={"turno_dt": f"{dia1}T{hora1}:00-03:00"}, headers=self._admin_headers())
        self.assertEqual(ra.status_code, 200, ra.text)
        rr = TestClient(app_module.app).post(
            f"/api/requests/{sid}/reprogramar", json={"dia": dia2, "hora": hora2})
        self.assertEqual(rr.status_code, 200, rr.text)
        self.assertIn(("cardiologia", dia2, hora2), slot_holds.taken())
        rc = TestClient(app_module.app).post(f"/api/requests/{sid}/cancel")
        self.assertEqual(rc.status_code, 200, rc.text)
        self._libre(dia2, hora2)
        self._libre(dia1, hora1)


# ---- one account, two devices (2026-11-15) ----------------------------------

_OTRA = {"dni": "30111222", "nombre": "Bruno", "apellido": "Paz",
         "nacimiento": "1985-02-01", "sexo": "M", "contacto": "wa:11-6666"}


class TestMismaCuentaDosDispositivos(ReservasFixtureTestCase):
    """A patient opened the confirmation on the desktop and then the phone,
    same account: the phone sent them back to step 1 with «El horario que
    habías elegido ya no está disponible». The hold belonged to the browser,
    not to the person — the desktop's hold hid the slot from its own owner."""

    def setUp(self):
        super().setUp()
        self._quien = dict(_USER)
        sesion = patch.object(slot_holds, "_session_user", lambda request: self._quien)
        sesion.start()
        self.addCleanup(sesion.stop)
        self._orig_user = booking_requests._get_user
        booking_requests._get_user = lambda h: self._quien
        self.addCleanup(setattr, booking_requests, "_get_user", self._orig_user)

    def _hold(self, token, dia, hora):
        return TestClient(app_module.app).post(
            "/api/slot-hold", json={"servicio_id": "cardiologia", "dia": dia,
                                    "hora": hora, "token": token})

    def _ve(self, token, dia, hora):
        r = TestClient(app_module.app).get(
            "/api/service-slots/cardiologia", params={"hold": token})
        self.assertEqual(r.status_code, 200, r.text)
        return any(t.get("dia") == dia and t.get("hora") == hora
                   for m in r.json().get("medicos") or [] for t in m.get("turnos") or [])

    def test_el_segundo_dispositivo_de_la_misma_cuenta_lo_toma(self):
        dia, hora = _primeros_slots(1)[0]
        self.assertEqual(self._hold("telefono", dia, hora).status_code, 200)
        r = self._hold("escritorio", dia, hora)
        self.assertEqual(r.status_code, 200, r.text)

    def test_el_primer_dispositivo_sigue_viendo_su_horario(self):
        dia, hora = _primeros_slots(1)[0]
        self.assertEqual(self._hold("telefono", dia, hora).status_code, 200)
        self.assertEqual(self._hold("escritorio", dia, hora).status_code, 200)
        self.assertTrue(self._ve("telefono", dia, hora))

    def test_enviar_desde_el_otro_dispositivo_no_da_409(self):
        dia, hora = _primeros_slots(1)[0]
        self.assertEqual(self._hold("escritorio", dia, hora).status_code, 200)
        r = TestClient(app_module.app).post("/api/requests", json={
            "servicio_id": "cardiologia", "centro_slug": "centro-a",
            "slot_dia": dia, "slot_hora": hora, "hold_token": "telefono"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_otra_cuenta_sigue_recibiendo_409(self):
        dia, hora = _primeros_slots(1)[0]
        self.assertEqual(self._hold("telefono", dia, hora).status_code, 200)
        self._quien = dict(_OTRA)
        r = self._hold("ajeno", dia, hora)
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self._ve("ajeno", dia, hora))

    def test_el_navegador_conserva_su_token(self):
        # The wizard sends the hold token as the booking's idem_key too: an
        # account-wide token there would make the next booking a «repeat».
        dia, hora = _primeros_slots(1)[0]
        r = self._hold("telefono", dia, hora)
        self.assertEqual(r.json()["token"], "telefono")

    def test_soltar_desde_un_dispositivo_libera_el_horario(self):
        dia, hora = _primeros_slots(1)[0]
        self.assertEqual(self._hold("telefono", dia, hora).status_code, 200)
        TestClient(app_module.app).delete("/api/slot-hold/telefono")
        self._quien = dict(_OTRA)
        self.assertEqual(self._hold("ajeno", dia, hora).status_code, 200)

    def test_sin_sesion_cada_navegador_es_su_propio_dueno(self):
        dia, hora = _primeros_slots(1)[0]
        self._quien = None
        self.assertEqual(self._hold("tokA", dia, hora).status_code, 200)
        self.assertEqual(self._hold("tokB", dia, hora).status_code, 409)

    def test_el_horario_de_una_solicitud_pendiente_no_se_vuelve_a_tomar(self):
        # Review 2026-11-15: the account token spans every request of the
        # patient, so a linked hold must not count as the account's free pick
        # nor have its expiry rewritten by the next hold of the same account.
        dia, hora = _primeros_slots(1)[0]
        self.assertEqual(self._hold("telefono", dia, hora).status_code, 200)
        r = TestClient(app_module.app).post("/api/requests", json={
            "servicio_id": "cardiologia", "centro_slug": "centro-a",
            "slot_dia": dia, "slot_hora": hora, "hold_token": "telefono"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self._ve("escritorio", dia, hora))
        antes = self._expira(r.json()["id"])
        self._hold("escritorio", dia, hora)
        self.assertEqual(self._expira(r.json()["id"]), antes)
        self.assertIsNotNone(db_slots.hold_of_booking_request(r.json()["id"]))

    def _expira(self, sid):
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("SELECT expires FROM slot_holds WHERE request_id=%s", (sid,))
                return cur.fetchone()[0]


class TestTokenDeCuenta(unittest.TestCase):
    """The account token is a keyed HMAC of the DNI: never under a key
    anyone can read in the source."""

    def _token(self, **env):
        vals = {"TURNOS_ADMIN_SECRET": "", "TURNOS_ADMIN_PASS": ""}
        vals.update(env)
        with patch.object(config, "get", lambda k, d=None: vals.get(k, d)):
            return slot_holds.owner_token({"dni": "30111222"})

    def test_sin_secretos_no_usa_una_clave_fija(self):
        import hmac as _hmac
        import hashlib as _hashlib
        key = _hashlib.sha256(b"st-slot-hold$human-rounds-slot-hold").digest()
        fija = "acct-" + _hmac.new(key, b"30111222", _hashlib.sha256).hexdigest()[:32]
        self.assertNotEqual(self._token(), fija)
        self.assertEqual(self._token(), self._token())   # stable within the process

    def test_la_clave_cambia_con_el_secreto(self):
        self.assertNotEqual(self._token(TURNOS_ADMIN_SECRET="a"), self._token(TURNOS_ADMIN_SECRET="b"))
        self.assertNotEqual(self._token(TURNOS_ADMIN_PASS="a"), self._token(TURNOS_ADMIN_PASS="b"))


if __name__ == "__main__":
    unittest.main()
