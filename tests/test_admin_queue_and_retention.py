"""test_admin_queue_and_retention.py — audit 2026-08-06, three defects:

1. Solicitudes admin queue unbounded (db_booking.list_admin/count_admin now bound to
   pendiente + a recent window of resolved, or an explicit single status).
2. Referral file retention (booking_referrals.sweep_referrals, driven by
   db_booking.referrals_to_purge/clear_referral_path).
3. DB hygiene purges (db_accounts.purge_idle_sessions/purge_old_searches/
   purge_old_login_panel_events/purge_summarized_chat_messages, wired by
   housekeeping.tick()) + the whatsapp merge index on chat_messages.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
         -m unittest discover -s tests -p "test_admin_queue_and_retention.py"
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.core import db_analytics  # noqa: E402
from app.core import config  # noqa: E402
from app.auth import turnstile          # noqa: E402
turnstile.ENV_PATH = ""
config.ENV_PATH = ""
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests   # noqa: E402
from app.ops import housekeeping       # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_housekeeping",
    seed=lambda: db_booking.create_booking_request(
        "00000000", "Semilla", "Esquema", "1990-01-01", None,
        "cardiologia", "hospital-pinamar", None))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _truncate(*tablas):
    with db._connect() as conn, conn.cursor() as cur:
        for t in tablas:
            cur.execute(f"TRUNCATE {t} CASCADE")


class BaseDbTest(unittest.TestCase):
    tablas = ("booking_requests",)

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate(*self.tablas)

    def tearDown(self):
        _truncate(*self.tablas)


# ---- defecto 1: cola de Solicitudes acotada --------------------------------

class TestListAdminBounded(BaseDbTest):

    def test_pendientes_siempre_aparecen_sin_estado(self):
        sid = db_booking.create_booking_request(
            "20111222", "Pendiente", "Test", "1980-01-01", None,
            "cardiologia", "hospital-pinamar", None)
        ids = {r["id"] for r in db_booking.list_admin()}
        self.assertIn(sid, ids)

    def test_resuelta_vieja_fuera_de_ventana_no_aparece_sin_estado(self):
        sid = db_booking.create_booking_request(
            "20111223", "Rechazada", "Vieja", "1980-01-01", None,
            "cardiologia", "hospital-pinamar", None)
        db_booking.reject(sid, "motivo de prueba")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE booking_requests SET created = now() - interval '60 days', "
                "resolved_at = now() - interval '60 days' WHERE id=%s", (sid,))
        # Sin estado (vista por defecto, ventana de 30 días): no aparece.
        ids_default = {r["id"] for r in db_booking.list_admin()}
        self.assertNotIn(sid, ids_default)
        # Con estado explícito ("rechazada"), la ventana no aplica: sí aparece.
        ids_explicit = {r["id"] for r in db_booking.list_admin("rechazada")}
        self.assertIn(sid, ids_explicit)

    def test_resuelta_reciente_aparece_sin_estado(self):
        sid = db_booking.create_booking_request(
            "20111224", "Rechazada", "Reciente", "1980-01-01", None,
            "cardiologia", "hospital-pinamar", None)
        db_booking.reject(sid, "motivo de prueba")
        ids = {r["id"] for r in db_booking.list_admin()}
        self.assertIn(sid, ids)

    def test_estado_explicito_respeta_limit_y_offset(self):
        for i in range(5):
            sid = db_booking.create_booking_request(
                f"2011130{i}", "Rechazada", f"Num{i}", "1980-01-01", None,
                "cardiologia", "hospital-pinamar", None)
            db_booking.reject(sid, "motivo de prueba")
        pagina1 = db_booking.list_admin("rechazada", limit=2, offset=0)
        pagina2 = db_booking.list_admin("rechazada", limit=2, offset=2)
        self.assertEqual(len(pagina1), 2)
        self.assertEqual(len(pagina2), 2)
        self.assertEqual(db_booking.count_admin("rechazada"), 5)
        # Las dos páginas no se pisan.
        self.assertEqual(set(), {r["id"] for r in pagina1} & {r["id"] for r in pagina2})

    def test_count_admin_sin_estado_ignora_resueltas_viejas(self):
        sid_pend = db_booking.create_booking_request(
            "20111400", "Pendiente", "Cuenta", "1980-01-01", None,
            "cardiologia", "hospital-pinamar", None)
        sid_vieja = db_booking.create_booking_request(
            "20111401", "Vieja", "Cuenta", "1980-01-01", None,
            "cardiologia", "hospital-pinamar", None)
        db_booking.reject(sid_vieja, "motivo")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE booking_requests SET created = now() - interval '60 days', "
                "resolved_at = now() - interval '60 days' WHERE id=%s", (sid_vieja,))
        total = db_booking.count_admin()
        ids_default = {r["id"] for r in db_booking.list_admin()}
        self.assertIn(sid_pend, ids_default)
        self.assertNotIn(sid_vieja, ids_default)
        self.assertEqual(total, len(ids_default))


# ---- defecto 2: retención de archivos de orden -----------------------------

class TestReferralRetention(BaseDbTest):

    def _con_archivo(self, dni, sufijo):
        sid = db_booking.create_booking_request(
            dni, "Orden", sufijo, "1980-01-01", None,
            "cardiologia", "hospital-pinamar", "image/png")
        path = os.path.join(booking_requests.REFERRALS_DIR, f"test-{sid}.png")
        os.makedirs(booking_requests.REFERRALS_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"fake-referral-bytes")
        db_booking.set_referral(sid, path, "image/png")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return sid, path

    def test_pendiente_nunca_es_candidata(self):
        sid, path = self._con_archivo("20111500", "Pendiente")
        candidatos = {r["id"] for r in db_booking.referrals_to_purge(retention_days=0, limit=50)}
        self.assertNotIn(sid, candidatos)
        self.assertTrue(os.path.exists(path))

    def test_aprobada_con_turno_futuro_nunca_es_candidata(self):
        sid, path = self._con_archivo("20111501", "Futura")
        futuro = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M")
        db_booking.approve(sid, futuro, {"estado": "skip"})
        candidatos = {r["id"] for r in db_booking.referrals_to_purge(retention_days=0, limit=50)}
        self.assertNotIn(sid, candidatos)
        self.assertTrue(os.path.exists(path))

    def test_rechazada_vieja_se_purga_y_limpia_la_fila(self):
        sid, path = self._con_archivo("20111502", "Rechazada")
        db_booking.reject(sid, "no corresponde")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE booking_requests SET resolved_at = now() - interval '200 days' "
                "WHERE id=%s", (sid,))
        borrados = booking_referrals.sweep_referrals(retention_days=180, limit=50)
        self.assertGreaterEqual(borrados, 1)
        self.assertFalse(os.path.exists(path))
        rec = db_booking.get_booking_request(sid)
        self.assertIsNone(rec.get("orden_path"))

    def test_rechazada_reciente_no_se_purga(self):
        sid, path = self._con_archivo("20111503", "RechazadaReciente")
        db_booking.reject(sid, "no corresponde")
        borrados = booking_referrals.sweep_referrals(retention_days=180, limit=50)
        self.assertEqual(borrados, 0)
        self.assertTrue(os.path.exists(path))

    def test_retention_days_cero_desactiva_el_barrido(self):
        sid, path = self._con_archivo("20111504", "Desactivado")
        db_booking.reject(sid, "no corresponde")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE booking_requests SET resolved_at = now() - interval '9999 days' "
                "WHERE id=%s", (sid,))
        borrados = booking_referrals.sweep_referrals(retention_days=0, limit=50)
        self.assertEqual(borrados, 0)
        self.assertTrue(os.path.exists(path))


# ---- defecto 3: higiene de tablas -------------------------------------------

class TestHousekeepingPurges(BaseDbTest):
    tablas = ("booking_requests", "sessions", "searches", "login_panel_events",
              "chat_messages", "chat_summaries")

    def test_purge_idle_sessions(self):
        for dni in ("20111600", "20111601"):
            _dbtest.ensure_user(dni)
            db_accounts.create_session(dni)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET last_seen = now() - interval '40 days' "
                "WHERE dni='20111600'")
        borradas = db_accounts.purge_idle_sessions(idle_days=30, limit=100)
        self.assertEqual(borradas, 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT dni FROM sessions")
            restantes = {r["dni"] for r in cur.fetchall()}
        self.assertEqual(restantes, {"20111601"})

    def test_purge_old_searches(self):
        db_analytics.log_search("consulta vieja", 1)
        db_analytics.log_search("consulta nueva", 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE searches SET ts = now() - interval '200 days' "
                "WHERE q='consulta vieja'")
        borradas = db_analytics.purge_old_searches(days=180, limit=100)
        self.assertEqual(borradas, 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT q FROM searches")
            qs = {r["q"] for r in cur.fetchall()}
        self.assertEqual(qs, {"consulta nueva"})

    def test_the_sweep_blanks_the_ip_of_old_ai_costs(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ai_costs (ts, operation, cost_usd, ip) VALUES "
                        "(now() - interval '200 days', 'ocr', 1, '1.1.1.1'), "
                        "(now() - interval '5 days', 'ocr', 1, '2.2.2.2')")
        self.assertEqual(housekeeping.tick_purges()["ai_costs_ip"], 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT ip FROM ai_costs ORDER BY ts")
            self.assertEqual([r["ip"] for r in cur.fetchall()], [None, "2.2.2.2"])

    def test_purge_old_login_panel_events(self):
        db_analytics.log_panel_login("sid-1", "a", "exposicion")
        db_analytics.log_panel_login("sid-2", "b", "exposicion")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE login_panel_events SET ts = now() - interval '200 days' "
                "WHERE sid='sid-1'")
        borrados = db_analytics.purge_old_login_panel_events(days=180, limit=100)
        self.assertEqual(borrados, 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT sid FROM login_panel_events")
            sids = {r["sid"] for r in cur.fetchall()}
        self.assertEqual(sids, {"sid-2"})

    def test_purge_chat_messages_solo_si_ya_esta_resumido(self):
        clave = "dni:20111700"
        db_chat.chat_save_message("web", clave, {"role": "user", "content": "hola"})
        db_chat.chat_save_message("web", "dni:20111701", {"role": "user", "content": "sin resumen"})
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_messages SET created = now() - interval '100 days'")
        # Sólo la primera conversación tiene un resumen que la cubre.
        db_chat.chat_summary_save(clave, "resumen de prueba",
                              datetime.now(timezone.utc))
        borrados = db_slots.purge_summarized_chat_messages(days=90, limit=100)
        self.assertEqual(borrados, 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT key FROM chat_messages")
            claves = {r["key"] for r in cur.fetchall()}
        self.assertEqual(claves, {"dni:20111701"})

    def test_tick_agrega_los_cuatro_barridos_y_no_lanza(self):
        _dbtest.ensure_user("20111800")
        db_accounts.create_session("20111800")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET last_seen = now() - interval '40 days' "
                "WHERE dni='20111800'")
        resultado = housekeeping.tick()
        self.assertEqual(resultado.get("sessions"), 1)
        self.assertIn("searches", resultado)
        self.assertIn("login_panel_events", resultado)
        self.assertIn("chat_messages", resultado)


if __name__ == "__main__":
    unittest.main()
