"""test_housekeeping_disk.py — pruebas del chequeo de espacio libre agregado
a housekeeping.py (2026-08-06): reusa system_status.medir_disco(), loguea
una línea por día bajo el umbral de warn, y manda UN email/día bajo el
umbral de alert.

Corre:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_housekeeping_disk.py"

No toca disco real, Mailgun real ni Postgres real: `system_status.medir_disco`,
`admin_auth.owner_email` y `email_mailgun.send` se mockean; el claim
at-most-once (db_analytics.claim_daily_alert) se fakea en memoria por test — la
semántica REAL del claim contra Postgres se prueba en
test_queue_worker.py (unidad + contención de dos workers).
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.ops import housekeeping  # noqa: E402
import _env  # noqa: E402


def _medida(libre_pct, libre_gb=5.0, total_gb=20.0):
    return {"libre_pct": libre_pct, "libre_gb": libre_gb, "total_gb": total_gb,
            "usado_pct": 100 - libre_pct}


class TestCheckDisk(unittest.TestCase):
    def setUp(self):
        # The dedup is no longer a local JSON: it is an at-most-once
        # claim in Postgres (db_analytics.claim_daily_alert). Faked here with an
        # in-memory set preserving the semantics (first call True, then
        # False per (key, day)) so no database is needed.
        self._claims = set()

        def _fake_claim(clave, dia):
            if (clave, dia) in self._claims:
                return False
            self._claims.add((clave, dia))
            return True

        import tempfile
        self._datadir = tempfile.TemporaryDirectory()
        self.addCleanup(self._datadir.cleanup)
        self._patches = [
            mock.patch("app.ops.housekeeping._claim_day", side_effect=_fake_claim),
            mock.patch("app.ops.housekeeping._hoy", return_value="2026-08-06"),
            mock.patch("app.auth.admin_auth.owner_email", return_value="dueno@example.com"),
            # An empty data dir so _migrate_legacy_disk_state never reads
            # the developer checkout's real data/ (the two legacy-JSON
            # tests override this with their own seeded dir).
            mock.patch("app.admin_panel.system_status.data_dir",
                       return_value=self._datadir.name),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_espacio_suficiente_no_hace_nada(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(80.0)), \
             mock.patch("app.notifications.mailgun.send") as send:
            out = housekeeping.check_disk()
        self.assertEqual(out["logged"], False)
        self.assertEqual(out["emailed"], False)
        send.assert_not_called()

    def test_bajo_warn_loguea_pero_no_manda_mail(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(20.0)), \
             mock.patch("app.notifications.mailgun.send") as send:
            out = housekeeping.check_disk()
        self.assertTrue(out["logged"])
        self.assertFalse(out["emailed"])
        send.assert_not_called()

    def test_bajo_alert_loguea_y_manda_un_mail(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=True) as send:
            out = housekeeping.check_disk()
        self.assertTrue(out["logged"])
        self.assertTrue(out["emailed"])
        send.assert_called_once()
        to, asunto, cuerpo = send.call_args[0]
        self.assertEqual(to, "dueno@example.com")
        self.assertIn("espacio", cuerpo.lower())

    def test_dedup_un_mail_por_dia(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=True) as send:
            housekeeping.check_disk()
            out2 = housekeeping.check_disk()
        # Mismo día ("2026-08-06" fijo por el mock de _hoy) -> segundo pase
        # no vuelve a loguear ni a mandar mail.
        self.assertFalse(out2["logged"])
        self.assertFalse(out2["emailed"])
        send.assert_called_once()

    def test_dedup_se_resetea_al_dia_siguiente(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=True) as send:
            housekeeping.check_disk()
            with mock.patch("app.ops.housekeeping._hoy", return_value="2026-08-07"):
                out2 = housekeeping.check_disk()
        self.assertTrue(out2["logged"])
        self.assertTrue(out2["emailed"])
        self.assertEqual(send.call_count, 2)

    def test_legacy_json_same_day_marks_migrate_to_consumed_claims(self):
        # Rollout-day bridge (Codex gate round 5): a pre-claim version's
        # JSON state saying TODAY already logged+emailed must pre-consume
        # the claims — the first DB-backed pass cannot duplicate.
        import json
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "private"), exist_ok=True)
        with open(os.path.join(tmp.name, "private",
                               "housekeeping-disk-alert.json"), "w") as f:
            json.dump({"last_log_date": "2026-08-06",
                       "last_email_date": "2026-08-06"}, f)
        with mock.patch("app.admin_panel.system_status.data_dir", return_value=tmp.name), \
             mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=True) as send:
            out = housekeeping.check_disk()
        self.assertFalse(out["logged"])
        self.assertFalse(out["emailed"])
        send.assert_not_called()

    def test_legacy_json_from_a_past_day_is_inert(self):
        import json
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "private"), exist_ok=True)
        with open(os.path.join(tmp.name, "private",
                               "housekeeping-disk-alert.json"), "w") as f:
            json.dump({"last_log_date": "2026-08-05",
                       "last_email_date": "2026-08-05"}, f)
        with mock.patch("app.admin_panel.system_status.data_dir", return_value=tmp.name), \
             mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=True) as send:
            out = housekeeping.check_disk()
        self.assertTrue(out["logged"])
        self.assertTrue(out["emailed"])
        send.assert_called_once()

    def test_envio_fallido_consume_el_claim_at_most_once(self):
        # Codex gate 2026-08-08: the claim is taken BEFORE the send. A
        # failed send (Mailgun down, outbound kill switch) consumes the
        # day's claim — zero or ONE email, never two, and never a
        # same-day retry that could duplicate after a SIGKILL.
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=False) as send:
            out1 = housekeeping.check_disk()
            out2 = housekeeping.check_disk()
        self.assertFalse(out1["emailed"])
        self.assertFalse(out2["emailed"])
        send.assert_called_once()  # the second pass never even tries: claim taken

    def test_sin_email_de_dueno_no_falla(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(10.0)), \
             mock.patch("app.auth.admin_auth.owner_email", return_value=None), \
             mock.patch("app.notifications.mailgun.send") as send:
            out = housekeeping.check_disk()
        self.assertTrue(out["logged"])
        self.assertFalse(out["emailed"])
        send.assert_not_called()

    def test_umbrales_configurables_por_env(self):
        with _env.patched_env({"TURNOS_DISK_WARN_PCT": "50",
                                           "TURNOS_DISK_ALERT_PCT": "40"}), \
             mock.patch("app.admin_panel.system_status.medir_disco", return_value=_medida(45.0)), \
             mock.patch("app.notifications.mailgun.send", return_value=True) as send:
            out = housekeeping.check_disk()
        # 45% libre está bajo el warn=50 pisado por env, pero arriba del
        # alert=40 pisado por env -> loguea, no manda mail.
        self.assertTrue(out["logged"])
        self.assertFalse(out["emailed"])
        send.assert_not_called()

    def test_disco_no_medible_no_falla(self):
        with mock.patch("app.admin_panel.system_status.medir_disco", return_value=None), \
             mock.patch("app.notifications.mailgun.send") as send:
            out = housekeeping.check_disk()
        self.assertIsNone(out)
        send.assert_not_called()

    def test_tick_purges_never_touches_the_disk_check(self):
        # Round-8 gate: the queue task body is tick_purges(), which must
        # be genuinely DB-only — no disk measurement, no alert path.
        with mock.patch("app.core.db_accounts.purge_idle_sessions", return_value=0), \
             mock.patch("app.core.db_analytics.purge_old_searches", return_value=0), \
             mock.patch("app.core.db_analytics.purge_old_login_panel_events", return_value=0), \
             mock.patch("app.core.db_slots.purge_summarized_chat_messages", return_value=0), \
             mock.patch("app.core.db_analytics.purge_old_alert_claims", return_value=0), \
             mock.patch("app.ops.housekeeping.check_disk") as cd:
            out = housekeeping.tick_purges()
        cd.assert_not_called()
        self.assertEqual(sum(out.values()), 0)

    def test_tick_nunca_levanta_si_check_disk_explota(self):
        with mock.patch("app.core.db_accounts.purge_idle_sessions", return_value=0), \
             mock.patch("app.core.db_analytics.purge_old_searches", return_value=0), \
             mock.patch("app.core.db_analytics.purge_old_login_panel_events", return_value=0), \
             mock.patch("app.core.db_slots.purge_summarized_chat_messages", return_value=0), \
             mock.patch("app.ops.housekeeping.check_disk", side_effect=RuntimeError("boom")):
            out = housekeeping.tick()
        self.assertIsInstance(out, dict)
        self.assertEqual(sum(out.values()), 0)


if __name__ == "__main__":
    unittest.main()
