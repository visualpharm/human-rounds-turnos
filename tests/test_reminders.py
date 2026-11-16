"""test_reminders.py — carrera del recordatorio del día antes (data-integrity
guards, 2026-08-05): `reminders.tick()` reclama cada fila vía
`db_booking.claim_reminder` (CAS sobre `reminder_at IS NULL`) ANTES de mandar el
mail, así dos barridos solapados (un tick lento + el siguiente disparando, o
dos procesos del mismo servicio) nunca mandan el mismo recordatorio dos
veces. Los tests puros/de ventana horaria de `tick()` viven en
test_notifications.py (monkeypatchean db.*); este archivo es el único con
Postgres real, mismo patrón que test_slot_holds.py
(`threading.Barrier` + N hilos).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_reminders*.py"
Se saltea entero si no hay Postgres de test (igual que test_waitlist.py).
"""

import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_booking  # noqa: E402
from app.notifications import reminders   # noqa: E402
import _dbtest      # noqa: E402

_ART = timezone(timedelta(hours=-3))

_db = _dbtest.PerModuleDb("sana_turnos_test_reminders")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _turno_aprobado_manana(now_utc):
    # "mañana" en ART, a media mañana (bien adentro de cualquier fecha
    # ART, sin ambigüedad de huso al convertir a UTC).
    manana_art = (now_utc.astimezone(_ART) + timedelta(days=1)).date()
    # Lo que dejó otro test del módulo ya no es candidato: cada test barre sólo su propio turno.
    for row in db_booking.booking_requests_to_remind(manana_art):
        db_booking.mark_reminder(row["id"])
    for row in db_booking.booking_requests_to_remind_interview(manana_art):
        db_booking.mark_interview_reminder(row["id"])
    turno = datetime(manana_art.year, manana_art.month, manana_art.day,
                      12, 0, tzinfo=timezone.utc)  # 09:00 ART
    sid = db_booking.create_booking_request(
        "30111222", "Juana", "Pérez", "1980-01-01", "wa:11",
        "cardiologia", "hospital-pinamar", None, solicitante_dni="30111222")
    db_booking.approve(sid, turno, {"estado": "skip"})
    return sid


class TestClaimCarrera(unittest.TestCase):
    """`db_booking.claim_reminder` bajo concurrencia real, disparado a través de
    `reminders.tick()` (no llamando la función de más abajo directo) — así
    queda probado el camino completo: leer candidatas, reclamar, notificar."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def _turno_aprobado_manana(self, now_utc):
        return _turno_aprobado_manana(now_utc)

    def test_dos_ticks_solapados_mandan_una_sola_vez(self):
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        self._turno_aprobado_manana(now)

        llamadas = []
        lock = threading.Lock()
        barrera = threading.Barrier(2)

        def fake_notify(row):
            with lock:
                llamadas.append(row["id"])

        resultados = [None, None]

        def target(i):
            barrera.wait()  # que los dos ticks lean/reclamen a la vez
            resultados[i] = reminders.tick(now)

        # Un solo patch activo para todo el test (concurrente en dos hilos):
        # mock.patch no es thread-safe para start/stop simultáneos sobre el
        # MISMO atributo, así que el patch se instala una vez afuera y los
        # hilos sólo llaman tick() adentro de él.
        with patch("app.notifications.send.notify_reminder", side_effect=fake_notify):
            hilos = [threading.Thread(target=target, args=(i,)) for i in range(2)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join()

        # Uno de los dos ticks ganó el reclamo (procesó 1), el otro perdió
        # la carrera (procesó 0) — nunca los dos.
        self.assertEqual(sorted(resultados), [0, 1])
        self.assertEqual(len(llamadas), 1)

    def test_reclamo_ganado_no_se_vuelve_a_mandar_en_un_tick_posterior(self):
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        self._turno_aprobado_manana(now)

        llamadas = []
        with patch("app.notifications.send.notify_reminder", side_effect=lambda r: llamadas.append(r["id"])):
            n1 = reminders.tick(now)
            n2 = reminders.tick(now)  # mismo barrido corriendo de nuevo

        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)  # ya reclamada -> no la vuelve a tomar
        self.assertEqual(len(llamadas), 1)


class _ClaimCase(unittest.TestCase):
    """An approved appointment tomorrow, the patient's address and a mail provider as stand-ins."""

    _NOW = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
    _NEXT = _NOW + timedelta(minutes=30)  # the next sweep at the default interval

    def _sweep_the_day(self, tick, up_from=None):
        """`tick` every 30 minutes from 10:00 to 20:30 (ART) of the day before; the provider refuses until
        `up_from` (never when None). Answers what the provider answered, in order."""
        answered = []
        clock = [self._NOW]

        def send(*_args, **_kwargs):
            answered.append(up_from is not None and clock[0] >= up_from)
            return answered[-1]
        self.adapter.send.side_effect = send
        for k in range(22):
            clock[0] = self._NOW + timedelta(minutes=30 * k)
            tick(clock[0])
        return answered

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        from app.core import outbound_guard
        from app.notifications import mail
        from app.notifications import send
        self.mail, self.send = mail, send
        self.adapter = MagicMock()
        self.adapter.missing_config.return_value = []  # a provider with its keys: a False is a refusal
        # Mail on and the outbound kill switch off, whatever a neighbour left in the process (one-process run:
        # tests/_routes_fixture.py defaults MAIL_ENABLED=0 for the whole routes tier, and a refusal then reads as
        # «skipped», never «failed»).
        for target, name, value in ((mail, "enabled", True), (outbound_guard, "armed", False),
                                    (mail, "transport", (self.adapter, "mailgun", "default")),
                                    (mail, "_carries_loopback_link", False),
                                    (send, "_destination_email", "paciente@example.com"),
                                    (send, "_destination_phone", None)):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sid = _turno_aprobado_manana(self._NOW)


class TestSendFailureReleasesTheClaim(_ClaimCase):
    """The claim is taken before the send so two sweeps never send twice; a send that FAILS gives it back
    (the product owner's hub, 2026-11-10: «the day-before reminder claimed before send is a real loss»). The real
    `notify_reminder` runs; only the patient's address and the provider are stand-ins. A retry waits longer
    after every failed try — 30 minutes, then 1 h, 2 h, 4 h — within the sweep's window: the row is a candidate
    only on the day before, between 09:00 and 21:00 (the product owner's hub review, 2026-11-10)."""

    def _claimed(self):
        return db_booking.get_booking_request(self.sid)["recordatorio_dt"] is not None

    def test_a_refused_send_leaves_the_reminder_for_the_next_sweep(self):
        self.adapter.send.return_value = False
        with patch("app.core.launch_signals.handled") as handled:
            reminders.tick(self._NOW)
        self.assertFalse(self._claimed(), "a reminder the provider refused must not stay marked as sent")
        self.assertIn(("reminder_failed",), [c.args for c in handled.call_args_list])
        self.adapter.send.return_value = True
        self.assertEqual(reminders.tick(self._NEXT), 1)
        self.assertEqual(self.adapter.send.call_count, 2, "the next sweep tries the same reminder again")
        self.assertTrue(self._claimed())
        self.assertEqual(reminders.tick(self._NEXT), 0, "once sent, it is never sent again")
        self.assertEqual(self.adapter.send.call_count, 2)

    def test_a_reminder_that_cannot_be_built_is_released_and_reported(self):
        with patch.object(self.send, "_friendly_appointment_time", side_effect=RuntimeError("template")), \
                patch("app.core.launch_signals.handled") as handled:
            reminders.tick(self._NOW)
        self.assertFalse(self._claimed())
        self.assertIn(("reminder_failed",), [c.args for c in handled.call_args_list])
        self.adapter.send.assert_not_called()

    def test_an_accepted_send_keeps_the_claim(self):
        self.adapter.send.return_value = True
        with patch("app.core.launch_signals.handled") as handled:
            self.assertEqual(reminders.tick(self._NOW), 1)
        self.assertTrue(self._claimed())
        handled.assert_not_called()

    def test_what_the_reminder_answers(self):
        # One of the three words of `mail.deliver`: a channel accepted it, nothing to send, or it failed.
        manana = (self._NOW.astimezone(_ART) + timedelta(days=1)).date()
        row = next(r for r in db_booking.booking_requests_to_remind(manana) if r["id"] == self.sid)
        self.adapter.send.return_value = True
        self.assertEqual("sent", self.send.notify_reminder(dict(row)))
        self.adapter.send.return_value = False
        with patch("app.core.launch_signals.handled"):
            self.assertEqual("failed", self.send.notify_reminder(dict(row)))
        with patch.object(self.send, "_destination_email", return_value=""):
            self.assertEqual("skipped", self.send.notify_reminder(dict(row)))

    def test_nothing_to_send_is_not_counted_as_sent(self):
        # No address and no phone (or mail off by configuration): the reminder did not go out, so the hospital's
        # report does not count it in its «ahorro» (the product owner's hub, 2026-11-10); it is not a failure either — no
        # signal — and it keeps its claim, so no later sweep builds it again.
        from app.core import db_reports
        before = db_reports.reports_funnel()["recordatorios_enviados"]
        with patch.object(self.send, "_destination_email", return_value=""), \
                patch.object(self.send, "notify_reminder", wraps=self.send.notify_reminder) as notify, \
                patch("app.core.launch_signals.handled") as handled:
            self.assertEqual(reminders.tick(self._NOW), 0)
            self.assertEqual(reminders.tick(self._NEXT), 0)
        self.assertTrue(self._claimed(), "the claim stays: no sweep takes the row again")
        self.assertTrue(db_booking.get_booking_request(self.sid)["reminder_skipped"])
        self.assertEqual(before, db_reports.reports_funnel()["recordatorios_enviados"],
                         "a reminder with nothing to send is not counted as sent")
        self.assertEqual(notify.call_count, 1, "not picked up again")
        handled.assert_not_called()
        self.adapter.send.assert_not_called()

    def test_a_whatsapp_that_went_out_keeps_the_claim_even_when_the_mail_failed(self):
        # Retrying would send the WhatsApp twice: one channel delivered is a delivered reminder.
        from app.notifications import otp_send
        self.adapter.send.return_value = False
        with patch.object(self.send, "_destination_phone", return_value="2254000000"), \
                patch.object(otp_send, "notificacion_permitida", return_value=True), \
                patch.object(otp_send, "wa_provider", return_value="meta"), \
                patch.object(otp_send, "enviar_notificacion", return_value=True) as wa:
            reminders.tick(self._NOW)
        wa.assert_called_once()
        self.assertTrue(self._claimed())

    def test_a_provider_down_for_an_hour_and_a_half_does_not_burn_the_reminder(self):
        # The product owner's hub review, 2026-11-10: three tries one sweep apart used up every reminder of the next day in a
        # provider outage of about an hour and a half. The waits grow: refused at 10:00 and 10:30, the 11:00
        # sweep leaves it, and at 11:30 the provider is back and the reminder goes out.
        with patch("app.core.launch_signals.handled"):
            answered = self._sweep_the_day(reminders.tick, up_from=self._NOW + timedelta(minutes=90))
        self.assertEqual([False, False, True], answered)
        self.assertTrue(self._claimed())

    def test_a_reminder_the_provider_keeps_refusing_is_given_up_unsent(self):
        # A permanent refusal (a hard bounce, a 4xx) is tried at 10:00, 10:30, 11:30, 13:30 and 17:30, each with
        # its reminder_failed, not every sweep of the day; the next try would be after 21:00, so it is given up.
        # Its column stays empty: the hospital's report does not count a reminder that never went out as sent
        # (the product owner's hub review, 2026-11-10: the abandoned claim inflated the report's «ahorro»).
        from app.core import db_reports
        before = db_reports.reports_funnel()["recordatorios_enviados"]
        with patch("app.core.launch_signals.handled") as handled:
            answered = self._sweep_the_day(reminders.tick)
        self.assertEqual([False] * 5, answered)
        self.assertEqual([c.args for c in handled.call_args_list].count(("reminder_failed",)), 5)
        self.assertFalse(self._claimed(), "a reminder that never went out is not marked as sent")
        self.assertEqual(before, db_reports.reports_funnel()["recordatorios_enviados"])


class TestInterviewSendFailureReleasesTheClaim(_ClaimCase):
    """The same contract for the second day-before sweep, the pre-consult interview invitation
    (`tick_interview`, column `interview_reminder_at`; the product owner's hub, 2026-11-10). The real intake hook and the
    real `notify_interview_reminder` run; the pending interview's link is a stand-in."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(self.send, "_interview_pending", return_value={"url": "https://example.org/e/1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _claimed(self):
        return db_booking.get_booking_request(self.sid)["entrevista_recordatorio_dt"] is not None

    def test_a_refused_send_leaves_the_reminder_for_the_next_sweep(self):
        self.adapter.send.return_value = False
        with patch("app.core.launch_signals.handled") as handled:
            reminders.tick_interview(self._NOW)
        self.assertFalse(self._claimed(), "an invitation the provider refused must not stay marked as sent")
        self.assertIn(("interview_reminder_failed",), [c.args for c in handled.call_args_list])
        self.adapter.send.return_value = True
        self.assertEqual(reminders.tick_interview(self._NEXT), 1)
        self.assertEqual(self.adapter.send.call_count, 2, "the next sweep tries the same invitation again")
        self.assertTrue(self._claimed())
        self.assertEqual(reminders.tick_interview(self._NEXT), 0, "once sent, it is never sent again")
        self.assertEqual(self.adapter.send.call_count, 2)

    def test_a_reminder_that_cannot_be_built_is_released_and_reported(self):
        with patch.object(self.send, "_friendly_appointment_time", side_effect=RuntimeError("template")), \
                patch("app.core.launch_signals.handled") as handled:
            reminders.tick_interview(self._NOW)
        self.assertFalse(self._claimed())
        self.assertIn(("interview_reminder_failed",), [c.args for c in handled.call_args_list])
        self.adapter.send.assert_not_called()

    def test_an_accepted_send_keeps_the_claim(self):
        self.adapter.send.return_value = True
        with patch("app.core.launch_signals.handled") as handled:
            self.assertEqual(reminders.tick_interview(self._NOW), 1)
        self.assertTrue(self._claimed())
        handled.assert_not_called()

    def test_nothing_to_send_keeps_the_claim(self):
        # The interview is done (or intake is off for this doctor), or there is no address: nothing to retry.
        for name, value in (("_interview_pending", None), ("_destination_email", "")):
            sid = self.sid if name == "_interview_pending" else _turno_aprobado_manana(self._NOW)
            self.sid = sid
            with patch.object(self.send, name, return_value=value):
                reminders.tick_interview(self._NOW)
            self.assertTrue(self._claimed(), name)
        self.adapter.send.assert_not_called()

    def test_a_provider_down_for_an_hour_and_a_half_does_not_burn_the_invitation(self):
        with patch("app.core.launch_signals.handled"):
            answered = self._sweep_the_day(reminders.tick_interview, up_from=self._NOW + timedelta(minutes=90))
        self.assertEqual([False, False, True], answered)
        self.assertTrue(self._claimed())

    def test_an_invitation_the_provider_keeps_refusing_is_given_up_unsent(self):
        with patch("app.core.launch_signals.handled") as handled:
            answered = self._sweep_the_day(reminders.tick_interview)
        self.assertEqual([False] * 5, answered)
        self.assertEqual([c.args for c in handled.call_args_list].count(("interview_reminder_failed",)), 5)
        self.assertFalse(self._claimed(), "an invitation that never went out is not marked as sent")


if __name__ == "__main__":
    unittest.main()
