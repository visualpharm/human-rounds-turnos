"""tests/test_cancel_label.py — hermetic half of TASK-44 (the product owner, 2026-10-26): a cancellation
reads «Cancelada por vos» (the patient cancelled), «Cancelada por el centro» (the desk) or
«Cancelada» (the data does not say), with ONE text in every language — the account page and
the assistant read the same locale keys, the desk email says «por el centro». The
database-backed half (who the events say cancelled) is tests/routes/test_cancel_label.py."""

import hashlib
import json
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core import i18n  # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from app.notifications import send as notifications  # noqa: E402

_LANGS = ("es", "en", "de", "fr", "it", "pt", "ru", "uk", "ja", "zh")
_MACHINE_LANGS = tuple(lang for lang in _LANGS if lang not in ("es", "en"))
_ACCOUNT_KEYS = ("account.mine.requests.status_cancelled",
                 "account.mine.requests.status_cancelled_by_patient",
                 "account.mine.requests.status_cancelled_by_center")
_MAIL_KEYS = ("mail.cancelled.body", "mail.cancelled.body_with_time")


def _catalog(lang, name):
    with open(os.path.join(_ROOT, "locales", lang, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


class TestWhoCancelled(unittest.TestCase):

    def test_patient_actors_are_the_patient(self):
        for actor in ("paciente", "solicitante", " paciente "):
            self.assertEqual(booking_requests.cancelled_by(actor), "paciente", actor)

    def test_a_named_desk_user_is_the_center(self):
        for actor in ("mesa.prueba", "admin", "Jorge"):
            self.assertEqual(booking_requests.cancelled_by(actor), "centro", actor)

    def test_no_actor_is_not_guessed(self):
        for actor in (None, "", "   "):
            self.assertIsNone(booking_requests.cancelled_by(actor), repr(actor))

    def test_the_patient_actor_the_cancel_path_writes_is_a_patient_actor(self):
        self.assertEqual(booking_requests._patient_actor("1", "1"), "paciente")
        self.assertEqual(booking_requests._patient_actor("1", "2"), "solicitante")
        for actor in ("paciente", "solicitante"):
            self.assertIn(actor, booking_requests._PATIENT_ACTORS)


class TestPublicRow(unittest.TestCase):

    def _fila(self, **extra):
        return dict({"id": 7, "estado": "cancelada", "servicio_id": "cardiologia-hospital",
                     "centro_slug": "hospital-pinamar", "dni": "30999000"}, **extra)

    def test_list_rows_carry_the_actor_of_the_event(self):
        with mock.patch.object(booking_requests.store, "cancel_actor") as una_consulta:
            self.assertEqual(booking_requests._public_row(self._fila(cancelled_actor="paciente"))["cancelada_por"], "paciente")
            self.assertEqual(booking_requests._public_row(self._fila(cancelled_actor="mesa.prueba"))["cancelada_por"], "centro")
            self.assertIsNone(booking_requests._public_row(self._fila(cancelled_actor=None))["cancelada_por"])
        una_consulta.assert_not_called()

    def test_a_row_read_on_its_own_asks_for_the_event(self):
        with mock.patch.object(booking_requests.store, "cancel_actor", return_value="solicitante") as pide:
            self.assertEqual(booking_requests._public_row(self._fila())["cancelada_por"], "paciente")
        pide.assert_called_once_with(7)

    def test_an_unreadable_event_degrades_to_not_known(self):
        with mock.patch.object(booking_requests.store, "cancel_actor", side_effect=RuntimeError("db")):
            self.assertIsNone(booking_requests._public_row(self._fila())["cancelada_por"])

    def test_only_cancelled_rows_carry_the_field(self):
        self.assertNotIn("cancelada_por", booking_requests._public_row(self._fila(estado="aprobada")))


class TestAdminRow(unittest.TestCase):
    """The desk's queue row (`_admin_row`) says who cancelled, from the same event as the patient's."""

    def _fila(self, **extra):
        return dict({"id": 7, "estado": "cancelada", "servicio_id": "cardiologia-hospital",
                     "centro_slug": "hospital-pinamar", "dni": "30999000"}, **extra)

    def _admin(self, fila):
        return booking_requests._admin_row(fila)

    def test_queue_rows_say_patient_center_or_nothing(self):
        with mock.patch.object(booking_requests.store, "cancel_actor") as una_consulta:
            self.assertEqual(self._admin(self._fila(cancelled_actor="paciente"))["cancelada_por"], "paciente")
            self.assertEqual(self._admin(self._fila(cancelled_actor="solicitante"))["cancelada_por"], "paciente")
            self.assertEqual(self._admin(self._fila(cancelled_actor="mesa.prueba"))["cancelada_por"], "centro")
            self.assertIsNone(self._admin(self._fila(cancelled_actor=None))["cancelada_por"])
        una_consulta.assert_not_called()

    def test_the_helper_column_does_not_reach_the_panel(self):
        self.assertNotIn("cancelled_actor", self._admin(self._fila(cancelled_actor="paciente")))

    def test_a_row_handed_back_after_an_action_asks_for_the_event(self):
        with mock.patch.object(booking_requests.store, "cancel_actor", return_value="mesa.prueba") as pide:
            self.assertEqual(self._admin(self._fila())["cancelada_por"], "centro")
        pide.assert_called_once_with(7)

    def test_an_unreadable_event_degrades_to_not_known(self):
        with mock.patch.object(booking_requests.store, "cancel_actor", side_effect=RuntimeError("db")):
            self.assertIsNone(self._admin(self._fila())["cancelada_por"])

    def test_only_cancelled_rows_carry_the_field(self):
        for estado in ("pendiente", "aprobada", "rechazada"):
            self.assertNotIn("cancelada_por", self._admin(self._fila(estado=estado)))


class TestAssistantText(unittest.TestCase):

    def _texto(self, lang, por):
        _, token = i18n.bind_lang(lang)
        try:
            return chat_tools._estado_texto("cancelada", por)
        finally:
            i18n.unbind(token)

    def test_spanish(self):
        self.assertEqual(self._texto("es", "paciente"), "Cancelada por vos")
        self.assertEqual(self._texto("es", "centro"), "Cancelada por el centro")
        self.assertEqual(self._texto("es", None), "Cancelada")

    def test_english(self):
        self.assertEqual(self._texto("en", "paciente"), "Cancelled by you")
        self.assertEqual(self._texto("en", "centro"), "Cancelled by the center")
        self.assertEqual(self._texto("en", None), "Cancelled")


class TestDeskEmail(unittest.TestCase):

    def _enviar(self, lang, turno=True):
        from datetime import datetime, timezone
        row = {"estado": "cancelada", "servicio_id": "cardiologia-hospital", "dni": "30999000",
               "turno_dt": datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc) if turno else None}
        with mock.patch.object(notifications, "_destination_email", return_value="nora@example.com"), \
                mock.patch.object(notifications, "_destination_lang", return_value=lang), \
                mock.patch.object(notifications, "_patient_name", return_value="Nora"), \
                mock.patch.object(notifications.mail, "send") as send:
            notifications.notify_cancelled(row)
        return send.call_args.args[2]

    def test_it_says_the_center_cancelled(self):
        self.assertIn("fue cancelado por el centro", self._enviar("es"))
        self.assertIn("fue cancelada por el centro", self._enviar("es", turno=False))
        self.assertIn("cancelled by the center", self._enviar("en"))
        self.assertIn("cancelled by the center", self._enviar("en", turno=False))


class TestCatalogs(unittest.TestCase):

    def test_the_spanish_texts_are_the_ones_ivan_chose(self):
        es = _catalog("es", "account.mine")
        self.assertEqual(es["account.mine.requests.status_cancelled_by_patient"], "Cancelada por vos")
        self.assertEqual(es["account.mine.requests.status_cancelled_by_center"], "Cancelada por el centro")
        self.assertEqual(es["account.mine.requests.status_cancelled"], "Cancelada")

    def test_every_language_has_every_key_with_its_placeholders(self):
        es_mail = _catalog("es", "mail")
        for lang in _LANGS:
            account, mail = _catalog(lang, "account.mine"), _catalog(lang, "mail")
            for key in _ACCOUNT_KEYS:
                self.assertTrue(account.get(key), f"{lang}: {key}")
            for key in _MAIL_KEYS:
                self.assertTrue(mail.get(key), f"{lang}: {key}")
                self.assertEqual(sorted(w for w in ("{service}", "{time}") if w in mail[key]),
                                 sorted(w for w in ("{service}", "{time}") if w in es_mail[key]), f"{lang}: {key}")

    def test_the_three_texts_differ_in_every_language(self):
        for lang in _LANGS:
            account = _catalog(lang, "account.mine")
            self.assertEqual(len({account[k] for k in _ACCOUNT_KEYS}), 3, lang)

    def test_machine_language_sidecars_match_the_spanish_source(self):
        es = {**_catalog("es", "account.mine"), **_catalog("es", "mail")}
        for lang in _MACHINE_LANGS:
            with open(os.path.join(_ROOT, "locales", lang, "_source_hash.json"), encoding="utf-8") as fh:
                hashes = json.load(fh)
            for key in _ACCOUNT_KEYS + _MAIL_KEYS:
                self.assertEqual(hashes.get(key), hashlib.sha256(es[key].encode("utf-8")).hexdigest()[:16],
                                 f"{lang}: {key} would be re-translated")


if __name__ == "__main__":
    unittest.main()
