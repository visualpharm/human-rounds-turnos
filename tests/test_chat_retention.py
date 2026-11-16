"""test_chat_retention.py — problems.md №12: a retention period for the chat.

Until now the chat kept everything: `chat_summaries` were never deleted, and old
`chat_messages` went only when a summary of the SAME patient key covered them,
so WhatsApp (key = phone) and no-login conversations stayed forever. The period
is the hospital's decision, so the mechanism ships OFF: with no setting nothing
is deleted. With `TURNOS_CHAT_RETENTION_DAYS=N` the housekeeping sweep deletes
the messages of every conversation channel older than N days — messages first,
then the summaries and compactions (`TURNOS_CHAT_SUMMARY_RETENTION_DAYS`,
default the same N). The pre-consultation interview transcript is clinical
record and is never touched. `scripts/fixes/report_chat_retention.py` previews,
read-only, what a given period would delete.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
         -m unittest discover -s tests -p "test_chat_retention.py"
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_chat  # noqa: E402
config.ENV_PATH = ""
from app.ops import housekeeping  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_chat_retention", seed=lambda: db_operators.operators_list())

_NOW = datetime.now(timezone.utc)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(query, params=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall() if cur.description else None


def _mensaje(canal, clave, dias):
    db_chat.chat_save_message(canal, clave, {"role": "user", "content": "texto de prueba"})
    _sql("UPDATE chat_messages SET created = now() - %s * interval '1 day' "
         "WHERE id = (SELECT max(id) FROM chat_messages)", (dias,))


def _resumen(clave, dias):
    db_chat.chat_summary_save(clave, "resumen de prueba", _NOW - timedelta(days=dias))


def _quedan_mensajes():
    return sorted((r["channel"], r["key"]) for r in _sql("SELECT channel, key FROM chat_messages"))


class TestChatRetention(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE chat_messages, chat_summaries, chat_compactions RESTART IDENTITY")
        _mensaje("web", "dni:20111001", 800)      # paciente con sesión, viejo
        _mensaje("web", "ip:203.0.113.5", 800)    # chat sin sesión, viejo
        _mensaje("whatsapp", "tel:5492254550001", 800)  # WhatsApp, viejo
        _mensaje("whatsapp", "tel:5492254550002", 10)   # WhatsApp, reciente
        _mensaje("web", "dni:20111001", 10)       # reciente
        _mensaje("interview", "interview:7", 900)  # entrevista previa: historia clínica
        _resumen("dni:20111001", 800)
        _resumen("dni:20111001", 100)
        db_chat.chat_compaction_save("dni:20111002", "compactación vieja", _NOW - timedelta(days=800))

    def _barrer(self, **env):
        with _env.patched_env(env, clear=False):
            return housekeeping.tick_purges()

    def test_sin_configurar_no_se_borra_nada_de_lo_que_no_cubre_un_resumen(self):
        with _env.patched_env(TURNOS_CHAT_RETENTION_DAYS=None, TURNOS_CHAT_SUMMARY_RETENTION_DAYS=None):
            salida = housekeeping.tick_purges()
        self.assertEqual(salida["chat_retention_messages"], 0)
        self.assertEqual(salida["chat_retention_summaries"], 0)
        self.assertEqual(len(_quedan_mensajes()), 6 - 1)   # el único que se va es el ya cubierto por su resumen
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_summaries")[0]["n"], 2)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_compactions")[0]["n"], 1)

    def test_con_un_plazo_se_borran_los_mensajes_de_todos_los_canales_menos_la_entrevista(self):
        salida = self._barrer(TURNOS_CHAT_RETENTION_DAYS="365")
        self.assertEqual(_quedan_mensajes(), [
            ("interview", "interview:7"),            # historia clínica: nunca por este plazo
            ("web", "dni:20111001"),                 # el reciente
            ("whatsapp", "tel:5492254550002"),       # el reciente
        ])
        self.assertEqual(salida["chat_retention_messages"] + salida["chat_messages"], 3)

    def test_los_resumenes_y_compactaciones_siguen_el_mismo_plazo_si_no_se_dice_otro(self):
        salida = self._barrer(TURNOS_CHAT_RETENTION_DAYS="365")
        self.assertEqual(salida["chat_retention_summaries"], 2)   # un resumen viejo y la compactación
        restantes = _sql("SELECT covers_until FROM chat_summaries")
        self.assertEqual(len(restantes), 1)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_compactions")[0]["n"], 0)

    def test_los_resumenes_pueden_tener_su_propio_plazo(self):
        self._barrer(TURNOS_CHAT_RETENTION_DAYS="365", TURNOS_CHAT_SUMMARY_RETENTION_DAYS="1000")
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_summaries")[0]["n"], 2)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_compactions")[0]["n"], 1)
        self.assertEqual(len(_quedan_mensajes()), 3)

    def test_un_plazo_absurdo_o_ilegible_deja_el_mecanismo_apagado(self):
        for valor in ("3", "0", "-5", "abc", ""):
            with self.subTest(valor=valor):
                salida = self._barrer(TURNOS_CHAT_RETENTION_DAYS=valor)
                self.assertEqual(salida["chat_retention_messages"], 0, valor)
                self.assertEqual(salida["chat_retention_summaries"], 0, valor)
        self.assertEqual(len(_quedan_mensajes()), 6 - 1)

    def test_un_plazo_de_resumenes_mal_escrito_no_hereda_el_de_los_mensajes(self):
        for valor in ("0", "abc", "7"):
            with self.subTest(valor=valor):
                salida = self._barrer(TURNOS_CHAT_RETENTION_DAYS="365", TURNOS_CHAT_SUMMARY_RETENTION_DAYS=valor)
                self.assertEqual(salida["chat_retention_summaries"], 0, valor)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_summaries")[0]["n"], 2)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_compactions")[0]["n"], 1)

    def test_solo_resumenes_con_plazo_no_toca_los_mensajes_por_fecha(self):
        salida = self._barrer(TURNOS_CHAT_SUMMARY_RETENTION_DAYS="365")
        self.assertEqual(salida["chat_retention_messages"], 0)
        self.assertEqual(salida["chat_retention_summaries"], 2)
        # los únicos mensajes que se fueron son los de la regla vieja (cubiertos por un resumen)
        self.assertEqual(len(_quedan_mensajes()), 6 - 1)

    def test_la_regla_vieja_tampoco_toca_la_entrevista_aunque_un_resumen_la_cubra(self):
        _resumen("interview:7", 800)
        borrados = db_slots.purge_summarized_chat_messages(days=90, limit=100)
        self.assertEqual(borrados, 1)   # sólo el de dni:20111001
        self.assertIn(("interview", "interview:7"), _quedan_mensajes())

    def test_primero_los_mensajes_y_despues_los_resumenes(self):
        orden = []
        real_mensajes, real_resumenes = db_chat.purge_chat_by_age, db_chat.purge_chat_summaries_by_age

        def mensajes(*a, **k):
            orden.append("mensajes")
            return real_mensajes(*a, **k)

        def resumenes(*a, **k):
            orden.append("resumenes")
            return real_resumenes(*a, **k)

        with mock.patch.object(db_chat, "purge_chat_by_age", mensajes), \
                mock.patch.object(db_chat, "purge_chat_summaries_by_age", resumenes):
            self._barrer(TURNOS_CHAT_RETENTION_DAYS="365")
        self.assertEqual(orden, ["mensajes", "resumenes"])

    def test_un_atraso_grande_se_va_en_varias_tandas_del_mismo_barrido(self):
        for i in range(30):
            _mensaje("whatsapp", f"tel:54911000{i:04d}", 800)
        with mock.patch.object(housekeeping, "_CHAT_RETENTION_BATCH", 10):
            salida = self._barrer(TURNOS_CHAT_RETENTION_DAYS="365")
        self.assertEqual(salida["chat_retention_messages"] + salida["chat_messages"], 3 + 30)
        self.assertEqual(len(_quedan_mensajes()), 3)

    def test_la_vista_previa_cuenta_sin_borrar_ni_mostrar_contenido(self):
        vista = db_chat.chat_retention_preview(365, 365)
        self.assertEqual(vista["mensajes"], 3)
        self.assertEqual(vista["conversaciones"], 3)
        self.assertEqual(vista["por_canal"], {"web": 2, "whatsapp": 1})
        self.assertEqual(vista["resumenes"], 1)
        self.assertEqual(vista["compactaciones"], 1)
        self.assertEqual(vista["entrevista_exenta"], 1)
        self.assertEqual(len(_quedan_mensajes()), 6)
        self.assertNotIn("texto de prueba", json.dumps(vista))

    def test_el_informe_de_solo_lectura_lista_varios_plazos_y_no_toca_nada(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))
        try:
            import report_chat_retention as informe
        finally:
            sys.path.pop(0)
        salida = io.StringIO()
        with redirect_stdout(salida), \
                _env.patched_env(TURNOS_CHAT_RETENTION_DAYS=None, TURNOS_CHAT_SUMMARY_RETENTION_DAYS=None), \
                mock.patch.object(db, "_connect", side_effect=AssertionError("el informe no pasa por _connect")):
            codigo = informe.main(["--days", "365", "--json"])
        self.assertEqual(codigo, 0)
        datos = json.loads(salida.getvalue())
        self.assertFalse(datos["configurado"])
        self.assertEqual(datos["plazos"]["365"]["mensajes"], 3)
        self.assertEqual(datos["total"]["mensajes"], 6)
        self.assertEqual(len(_quedan_mensajes()), 6)

    def test_el_informe_refleja_el_plan_configurado_con_plazos_independientes(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))
        try:
            import report_chat_retention as informe
        finally:
            sys.path.pop(0)
        salida = io.StringIO()
        with redirect_stdout(salida), _env.patched_env({
                "TURNOS_CHAT_RETENTION_DAYS": "365", "TURNOS_CHAT_SUMMARY_RETENTION_DAYS": "1000"}):
            informe.main(["--days", "365", "--summary-days", "1000", "--json"])
        datos = json.loads(salida.getvalue())
        self.assertTrue(datos["configurado"])
        self.assertEqual((datos["configurado_ahora"]["mensajes"], datos["configurado_ahora"]["resumenes"]), (3, 0))
        self.assertEqual(datos["plazos"]["365"]["resumenes"], 0)


class TestChatRetentionQueries(unittest.TestCase):
    """The four retention statements of `core/db.py` row for row (pinned on the hand-written SQL
    before they moved to SQLAlchemy Core): limits, the «not touched» plazos and the totals."""

    setUp = TestChatRetention.setUp

    def _oldest(self, query):
        return _sql(query)[0]["v"]

    def test_purge_by_age_respects_the_limit_and_never_the_interview(self):
        self.assertEqual(db_chat.purge_chat_by_age(365, limit=2), 2)
        self.assertEqual(len(_quedan_mensajes()), 4)
        self.assertEqual(db_chat.purge_chat_by_age(365, limit=100), 1)
        self.assertEqual(db_chat.purge_chat_by_age(365, limit=100), 0)
        self.assertIn(("interview", "interview:7"), _quedan_mensajes())
        self.assertEqual(db_chat.purge_chat_by_age(5, limit=100), 2)   # the recent ones, older than 5 days
        self.assertEqual(_quedan_mensajes(), [("interview", "interview:7")])

    def test_purge_summaries_by_age_counts_both_tables_and_respects_the_limit(self):
        db_chat.chat_summary_save("dni:20111003", "otro viejo", _NOW - timedelta(days=700))
        self.assertEqual(db_chat.purge_chat_summaries_by_age(365, limit=1), 2)   # one summary + one compaction
        self.assertEqual(db_chat.purge_chat_summaries_by_age(365, limit=1), 1)
        self.assertEqual(db_chat.purge_chat_summaries_by_age(365, limit=1), 0)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_summaries")[0]["n"], 1)
        self.assertEqual(db_chat.purge_chat_summaries_by_age(50, limit=10), 1)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_summaries")[0]["n"], 0)

    def test_preview_with_a_plazo_left_untouched(self):
        self.assertEqual(db_chat.chat_retention_preview(None, 365), {
            "dias": None, "dias_resumenes": 365, "mensajes": 0, "conversaciones": 0, "por_canal": {},
            "entrevista_exenta": 0, "resumenes": 1, "compactaciones": 1})
        self.assertEqual(db_chat.chat_retention_preview(365, None), {
            "dias": 365, "dias_resumenes": None, "mensajes": 3, "conversaciones": 3,
            "por_canal": {"web": 2, "whatsapp": 1}, "entrevista_exenta": 1, "resumenes": 0, "compactaciones": 0})
        self.assertEqual(db_chat.chat_retention_preview(5), {
            "dias": 5, "dias_resumenes": 5, "mensajes": 5, "conversaciones": 4,
            "por_canal": {"web": 3, "whatsapp": 2}, "entrevista_exenta": 1, "resumenes": 2, "compactaciones": 1})
        self.assertEqual(db_chat.chat_retention_preview(None, None)["mensajes"], 0)

    def test_preview_and_totals_on_a_connection_of_their_own(self):
        with db._connect() as conn:
            self.assertEqual(db_chat.chat_retention_preview(365, 365, connection=conn)["mensajes"], 3)
            self.assertEqual(db_chat.chat_totals(connection=conn)["mensajes"], 6)

    def test_totals_count_every_table_and_name_the_oldest_row(self):
        totals = db_chat.chat_totals()
        oldest = {r["channel"]: r["v"] for r in _sql(
            "SELECT channel, min(created) AS v FROM chat_messages GROUP BY channel")}
        self.assertEqual(totals, {
            "mensajes": 6,
            "por_canal": {"interview": {"n": 1, "mas_viejo": oldest["interview"]},
                          "web": {"n": 3, "mas_viejo": oldest["web"]},
                          "whatsapp": {"n": 2, "mas_viejo": oldest["whatsapp"]}},
            "resumenes": 2, "resumenes_mas_viejo": self._oldest("SELECT min(covers_until) AS v FROM chat_summaries"),
            "compactaciones": 1,
            "compactaciones_mas_viejo": self._oldest("SELECT min(covers_until) AS v FROM chat_compactions")})
        self.assertEqual(list(totals["por_canal"]), ["interview", "web", "whatsapp"])   # ORDER BY channel
        _sql("TRUNCATE chat_messages, chat_summaries, chat_compactions RESTART IDENTITY")
        self.assertEqual(db_chat.chat_totals(), {
            "mensajes": 0, "por_canal": {}, "resumenes": 0, "resumenes_mas_viejo": None,
            "compactaciones": 0, "compactaciones_mas_viejo": None})


if __name__ == "__main__":
    unittest.main()
