"""test_intake_settings.py — fase 7 de la entrevista previa: la pantalla
#entrevistas (toggle de sistema + override por médico, solo_admin), el
auto-toggle del propio médico (my-settings) y la CTA de la entrevista en el
email de "turno aprobado" (notifications.notify_approved).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_intake_settings.py"

Mismo patrón que test_intake_qr.py: router FastAPI real, cookie firmada con
admin_auth._mint_token, médicos de prueba insertados de VERDAD en `catalog`
(db_catalog.catalog_upsert) — así intake_admin._write_override, que reescribe ese
mismo overlay, se ejercita de punta a punta en vez de contra un mock que
nunca refleja la escritura.
"""

import os
import sys
import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import data_packs  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import catalog             # noqa: E402
from app.core import config  # noqa: E402
from app.core import deps as core_deps  # noqa: E402
from app.auth import admin_auth         # noqa: E402
from app.auth import turnstile            # noqa: E402
from app.notifications import send as notifications       # noqa: E402
from app.notifications import mailgun as email_mailgun        # noqa: E402
from app.modules.intake import intake_admin, intake_admin_router  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakesettings", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    # Approval mails carry links; without a public origin they are not sent.
    _env.set_env_for_module(TURNOS_PUBLIC_ORIGIN="https://example.health.invalid")
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")
    catalog.invalidate()
    config._reset_cache_para_tests()


_env.process_set(ENABLED_MODULES="turnos,intake")
_APP = FastAPI()
_APP.add_exception_handler(
    core_deps.AdminGateError, core_deps.render_admin_gate_error)
_APP.include_router(intake_admin_router.router)
from app.core import deps as _early_deps  # noqa: E402
_early_deps.install_early_response(_APP)  # the app's answer of a dependency
from app.core import api_body as _api_body  # noqa: E402
_api_body.install(_APP)  # the family's 400 to a body that does not match its model, as in the app
_CLIENT = TestClient(_APP)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


_MED1 = {"id": "set-med-1", "nombre": "María José Fernández",
        "especialidad": "Cardiología", "centro": "centro-a"}
_MED2 = {"id": "set-med-2", "nombre": "Carlos Gómez",
        "especialidad": "Pediatría", "centro": "centro-a"}


def _seed_doctor(doc):
    db_catalog.catalog_upsert("medicos", doc["id"], dict(doc), borrado=False)
    catalog.invalidate()
    return doc


def _doc(medico_id):
    return next(m for m in catalog.doctors() if m["id"] == medico_id)


class BaseSettingsTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def tearDown(self):
        catalog.invalidate()
        config._reset_cache_para_tests()

    def _get(self, path, headers=None):
        return _CLIENT.get(path, headers=headers or {}), True

    def _post(self, path, body=None, headers=None):
        return _CLIENT.post(path, json=body or {}, headers=headers or {}), True

    def _get_settings(self, headers):
        return self._get("/api/admin/intake/settings", headers=headers)

    def _post_settings(self, body, headers):
        return self._post("/api/admin/intake/settings", body=body, headers=headers)

    def _get_my(self, headers):
        return self._get("/api/admin/intake/my-settings", headers=headers)

    def _post_my(self, body, headers):
        return self._post("/api/admin/intake/my-settings", body=body, headers=headers)


# ---------------------------------------------------------------- gates

class TestSettingsGates(BaseSettingsTestCase):
    def test_get_settings_sin_sesion_401(self):
        h, manejado = self._get_settings(headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_get_settings_medico_403(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._get_settings(headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 403)

    def test_post_settings_medico_403(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._post_settings({"sistema": False}, headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 403)

    def test_get_settings_admin_200(self):
        _seed_doctor(_MED1)
        h, manejado = self._get_settings(headers=_admin_headers())
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        # Default heredado OFF desde 2026-10-21: sin una decisión explícita
        # (toggle Sistema o override true por médico) la entrevista no se
        # ofrece en ninguna instalación nueva.
        self.assertFalse(h.json()["enabled_system"])
        ids = [m["id"] for m in h.json()["medicos"]]
        self.assertIn(_MED1["id"], ids)

    def test_get_my_settings_sin_sesion_401(self):
        h, manejado = self._get_my(headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_get_my_settings_admisionista_403(self):
        db_operators.operator_create("mesa", "clave1234", "admisionista")
        h, _ = self._get_my(headers=_cookie("mesa", "admisionista"))
        self.assertEqual(h.status_code, 403)

    def test_get_my_settings_admin_sin_medico_vinculado_404(self):
        h, _ = self._get_my(headers=_admin_headers())
        self.assertEqual(h.status_code, 404)


# ---------------------------------------------------------------- toggle de sistema

class TestToggleSistema(BaseSettingsTestCase):
    def test_round_trip(self):
        h, _ = self._post_settings({"sistema": False}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(str(config.get("INTAKE_ENABLED")), "0")
        h, _ = self._get_settings(headers=_admin_headers())
        self.assertFalse(h.json()["enabled_system"])

        h, _ = self._post_settings({"sistema": True}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(str(config.get("INTAKE_ENABLED")), "1")
        h, _ = self._get_settings(headers=_admin_headers())
        self.assertTrue(h.json()["enabled_system"])


# ---------------------------------------------------------------- override por médico

class TestOverridePorMedico(BaseSettingsTestCase):
    def test_medico_inexistente_404(self):
        h, _ = self._post_settings({"medico_id": "no-existe", "override": True},
                                   headers=_admin_headers())
        self.assertEqual(h.status_code, 404)

    def test_override_invalido_400(self):
        _seed_doctor(_MED1)
        h, _ = self._post_settings({"medico_id": _MED1["id"], "override": "si"},
                                   headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    def test_falta_sistema_y_medico_id_400(self):
        h, _ = self._post_settings({}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    def test_write_true_false_y_null_borra_hereda(self):
        _seed_doctor(_MED1)

        h, _ = self._post_settings({"medico_id": _MED1["id"], "override": True},
                                   headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIs(_doc(_MED1["id"]).get("entrevista_habilitada"), True)

        h, _ = self._post_settings({"medico_id": _MED1["id"], "override": False},
                                   headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIs(_doc(_MED1["id"]).get("entrevista_habilitada"), False)

        h, _ = self._post_settings({"medico_id": _MED1["id"], "override": None},
                                   headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertNotIn("entrevista_habilitada", _doc(_MED1["id"]))

    def test_settings_get_refleja_el_override(self):
        _seed_doctor(_MED1)
        self._post_settings({"medico_id": _MED1["id"], "override": False}, headers=_admin_headers())
        h, _ = self._get_settings(headers=_admin_headers())
        fila = next(m for m in h.json()["medicos"] if m["id"] == _MED1["id"])
        self.assertEqual(fila["override"], False)
        self.assertEqual(fila["efectivo"], False)

    def test_columnas_del_medico_se_guardan_en_su_cuenta(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        columnas = ["confirmo", "entrevista", "cobertura"]
        h, _ = self._post_settings({"medico_id": _MED1["id"], "columnas": columnas},
                                   headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(db_auth.admin_preferences("dr-fernandez")["day_queue.columns"], columnas)
        h, _ = self._get_settings(headers=_admin_headers())
        fila = next(m for m in h.json()["medicos"] if m["id"] == _MED1["id"])
        self.assertEqual(fila["columnas"], columnas)
        self.assertEqual(fila["cuenta"], "dr-fernandez")

    def test_columnas_que_pasaron_a_la_ficha_no_se_ofrecen(self):
        # The product owner 2026-11-09: DNI, service, referral and history are part of the
        # row or the appointment card, not columns to tick; a list saved
        # before still reads, minus those keys.
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        db_auth.admin_preference_save("dr-fernandez", "day_queue.columns", ["dni", "servicio", "cobertura"])
        h, _ = self._get_settings(headers=_admin_headers())
        self.assertEqual([c["key"] for c in h.json()["columnas_disponibles"]],
                         ["confirmo", "solicitado", "cobertura", "entrevista"])
        fila = next(m for m in h.json()["medicos"] if m["id"] == _MED1["id"])
        self.assertEqual(fila["columnas"], ["cobertura"])

    def test_columnas_requieren_cuenta_medica_vinculada(self):
        _seed_doctor(_MED1)
        h, _ = self._post_settings({"medico_id": _MED1["id"],
                                    "columnas": ["entrevista"]}, headers=_admin_headers())
        self.assertEqual(h.status_code, 409)

    def test_columna_desconocida_se_rechaza(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._post_settings({"medico_id": _MED1["id"],
                                    "columnas": ["entrevista", "secreto"]}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    def test_batch_sets_only_the_named_doctors(self):
        _seed_doctor(dict(_MED1, entrevista_qr_token="keep-token", horario={"lunes": ["09:00"]}))
        _seed_doctor(_MED2)
        h, _ = self._post_settings(
            {"medico_ids": [_MED1["id"], _MED2["id"]], "enabled": False},
            headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIs(_doc(_MED1["id"])["entrevista_habilitada"], False)
        self.assertIs(_doc(_MED2["id"])["entrevista_habilitada"], False)
        self.assertEqual(_doc(_MED1["id"])["entrevista_qr_token"], "keep-token")
        self.assertEqual(_doc(_MED1["id"])["horario"], {"lunes": ["09:00"]})

    def test_batch_skips_a_doctor_outside_the_catalog_as_pending(self):
        # Form audit 2026-10-25 (decided by pinamar-turnos): an incomplete doctor (no
        # center, so not in the intake catalog) among the ticked rows no longer fails the
        # whole batch with 404 — it counts as «con datos pendientes», like the other bulk
        # actions, and the complete doctors are saved.
        _seed_doctor(_MED1)
        h, _ = self._post_settings(
            {"medico_ids": [_MED1["id"], "no-existe"], "enabled": False},
            headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        body = h.json()
        self.assertEqual(body["medico_ids"], [_MED1["id"]])
        self.assertEqual(body["errores"], 1)
        self.assertIs(_doc(_MED1["id"])["entrevista_habilitada"], False)

    def test_batch_with_no_doctor_in_the_catalog_is_404_and_writes_nothing(self):
        _seed_doctor(_MED1)
        h, _ = self._post_settings(
            {"medico_ids": ["no-existe", "tampoco"], "enabled": False},
            headers=_admin_headers())
        self.assertEqual(h.status_code, 404)
        self.assertNotIn("entrevista_habilitada", _doc(_MED1["id"]))

    def test_batch_rolls_back_if_a_later_doctor_was_deleted(self):
        _seed_doctor(_MED1)
        db_catalog.catalog_upsert("medicos", _MED2["id"], dict(_MED2), borrado=True)
        with self.assertRaises(LookupError):
            db_catalog.catalog_set_boolean_many("medicos", [(_MED1["id"], _MED1), (_MED2["id"], _MED2)],
                                        "entrevista_habilitada", False)
        catalog.invalidate()
        self.assertNotIn("entrevista_habilitada", _doc(_MED1["id"]))
        self.assertFalse(any(doc["id"] == _MED2["id"] for doc in catalog.doctors()))


class TestActivePosterPdf(BaseSettingsTestCase):
    def test_active_poster_is_a_downloadable_pdf_and_keeps_token(self):
        _seed_doctor(dict(_MED1, entrevista_habilitada=True, entrevista_qr_token="pdf-fixture"))
        response = _CLIENT.get(f'/api/admin/intake/door/{_MED1["id"]}?format=pdf', headers=_admin_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertGreater(len(response.content), 5000)
        self.assertEqual(_doc(_MED1["id"])["entrevista_qr_token"], "pdf-fixture")

    def test_inactive_pdf_is_absent_without_creating_a_token(self):
        _seed_doctor(dict(_MED1, entrevista_habilitada=False))
        response = _CLIENT.get(f'/api/admin/intake/door/{_MED1["id"]}?format=pdf', headers=_admin_headers())
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("entrevista_qr_token", _doc(_MED1["id"]))

    def test_pdf_still_requires_staff_authorization(self):
        _seed_doctor(dict(_MED1, entrevista_habilitada=True))
        response = _CLIENT.get(f'/api/admin/intake/door/{_MED1["id"]}?format=pdf')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("entrevista_qr_token", _doc(_MED1["id"]))

    def test_doctor_cannot_download_another_doctors_poster(self):
        _seed_doctor(dict(_MED1, entrevista_habilitada=True))
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED2["id"])
        response = _CLIENT.get(f'/api/admin/intake/door/{_MED1["id"]}?format=pdf',
                               headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("entrevista_qr_token", _doc(_MED1["id"]))


# ---------------------------------------------------------------- matriz de efectivo

class TestMatrizEfectivo(BaseSettingsTestCase):
    def test_sistema_x_override(self):
        _seed_doctor(_MED1)
        casos = [
            (True, None, True), (True, True, True), (True, False, False),
            (False, None, False), (False, True, True), (False, False, False),
        ]
        for sistema, override, esperado in casos:
            self._post_settings({"sistema": sistema}, headers=_admin_headers())
            self._post_settings({"medico_id": _MED1["id"], "override": override},
                                headers=_admin_headers())
            efectivo = intake_admin.enabled_for(_doc(_MED1["id"]))
            self.assertEqual(efectivo, esperado, (sistema, override))


# ---------------------------------------------------------------- my-settings

class TestMySettings(BaseSettingsTestCase):
    def test_get_propio(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        # Sin override y con el default heredado OFF, el médico NO la tiene
        # efectiva: hace falta una decisión explícita.
        self.assertFalse(intake_admin.enabled_for(_doc(_MED1["id"])))
        config.guardar({"INTAKE_ENABLED": "1"})
        h, _ = self._get_my(headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertIsNone(h.json()["override"])
        self.assertTrue(h.json()["efectivo"])
        self.assertIn(_MED1["id"], h.json()["cartel_url"])
        self.assertIn("format=pdf", h.json()["pdf_url"])

    def test_post_escribe_solo_el_propio_registro(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])

        # intenta colar el id de OTRO médico en el body — se ignora del todo,
        # sólo el registro de la sesión (MED1) puede cambiar.
        h, _ = self._post_my({"override": False, "medico_id": _MED2["id"]},
                             headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertIs(_doc(_MED1["id"]).get("entrevista_habilitada"), False)
        self.assertNotIn("entrevista_habilitada", _doc(_MED2["id"]))

    def test_admin_sin_medico_vinculado_404(self):
        h, _ = self._post_my({"override": True}, headers=_admin_headers())
        self.assertEqual(h.status_code, 404)

    def test_override_invalido_400(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._post_my({"override": "si"}, headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 400)


# ---------------------------------------------------------------- CTA en el email de aprobación

class _CapturedSend:
    """Reemplaza email_mailgun.send: guarda cada llamada, no pega a la red
    (mismo patrón que test_notifications.py). `html=None` (2026-08-01):
    email_mailgun.send ahora manda también la parte HTML — este fake tiene
    que aceptar el kwarg aunque estas pruebas sigan mirando sólo el texto."""

    def __init__(self):
        self.calls = []

    def __call__(self, to, subject, text, html=None):
        self.calls.append((to, subject, text))
        return True


class TestEntrevistaEnEmailAprobado(BaseSettingsTestCase):
    def setUp(self):
        super().setUp()
        self._orig_send = email_mailgun.send
        self.sent = _CapturedSend()
        email_mailgun.send = self.sent
        self._orig_get_user = db_accounts.get_user
        db_accounts.get_user = lambda dni: {"dni": dni, "email": "paciente@example.com"}

    def tearDown(self):
        email_mailgun.send = self._orig_send
        db_accounts.get_user = self._orig_get_user
        super().tearDown()

    def _row(self, medico_id):
        return {
            "id": 4242, "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar", "medico_asignado_id": medico_id,
        }

    def test_incluye_el_link_con_el_medico_habilitado(self):
        # Opt-in explícito del médico: con el default heredado OFF
        # (2026-10-21), sólo un override true del médico prende el link.
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = True
        _seed_doctor(doc)
        notifications.notify_approved(self._row(_MED1["id"]))
        self.assertEqual(len(self.sent.calls), 1)
        _, _, body = self.sent.calls[0]
        self.assertIn("/entrevista/4242", body)

    def test_omite_el_link_con_el_medico_deshabilitado(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        notifications.notify_approved(self._row(_MED1["id"]))
        self.assertEqual(len(self.sent.calls), 1)
        _, _, body = self.sent.calls[0]
        self.assertNotIn("/entrevista/", body)

    def test_omite_el_link_con_el_sistema_apagado(self):
        _seed_doctor(_MED1)
        config.guardar({"INTAKE_ENABLED": "0"})
        notifications.notify_approved(self._row(_MED1["id"]))
        self.assertEqual(len(self.sent.calls), 1)
        _, _, body = self.sent.calls[0]
        self.assertNotIn("/entrevista/", body)

    def test_sin_medico_no_invita_aunque_el_sistema_este_encendido(self):
        # El caso del hospital (2026-11-10): la entrevista apagada en TODOS
        # los médicos desde el panel, pero el default del sistema (que el
        # panel no muestra) seguía encendido, y un turno sin médico asignado
        # recibía la invitación igual. Sin médico conocido nadie lee esa
        # entrevista: el mail no invita y no rompe.
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        config.guardar({"INTAKE_ENABLED": "1"})
        notifications.notify_approved(self._row(None))
        self.assertEqual(len(self.sent.calls), 1)
        _, _, body = self.sent.calls[0]
        self.assertNotIn("/entrevista/", body)

    def test_medico_desconocido_no_invita(self):
        config.guardar({"INTAKE_ENABLED": "1"})
        notifications.notify_approved(self._row("no-existe"))
        self.assertEqual(len(self.sent.calls), 1)
        _, _, body = self.sent.calls[0]
        self.assertNotIn("/entrevista/", body)

    def test_aprobado_decide_solo_el_medico_asignado(self):
        # Turno aprobado: decide el médico asignado. El del bloque que el
        # paciente había elegido ya no cuenta si la mesa no asignó a nadie.
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = True
        _seed_doctor(doc)
        row = self._row(None)
        row["slot_medico_id"] = _MED1["id"]
        notifications.notify_approved(row)
        _, _, body = self.sent.calls[0]
        self.assertNotIn("/entrevista/", body)

    def test_el_mail_de_recibimos_sigue_al_medico_del_bloque_elegido(self):
        # Solicitud pendiente: booking_create pasa el médico del bloque
        # elegido en medico_asignado_id. Apagado en ese médico -> no invita
        # aunque el sistema esté encendido; sin médico tampoco; con el
        # médico habilitado sí.
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        config.guardar({"INTAKE_ENABLED": "1"})
        notifications.notify_received(self._row(_MED1["id"]))
        notifications.notify_received(self._row(None))
        self.assertEqual(len(self.sent.calls), 2)
        for _, _, body in self.sent.calls:
            self.assertNotIn("/entrevista/", body)

        doc["entrevista_habilitada"] = True
        _seed_doctor(doc)
        config.guardar({"INTAKE_ENABLED": "0"})
        notifications.notify_received(self._row(_MED1["id"]))
        _, _, body = self.sent.calls[2]
        self.assertIn("/entrevista/4242", body)

    def test_el_recordatorio_no_invita_sin_medico_ni_con_el_medico_apagado(self):
        # El recordatorio de la víspera pasa por la misma decisión
        # (_interview_pending -> _entrevista_cta).
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        config.guardar({"INTAKE_ENABLED": "1"})
        self.assertIsNone(notifications._interview_pending(self._row(None)))
        self.assertIsNone(notifications._interview_pending(self._row(_MED1["id"])))
        doc["entrevista_habilitada"] = True
        _seed_doctor(doc)
        cta = notifications._interview_pending(self._row(_MED1["id"]))
        self.assertTrue(cta and cta["url"].endswith("/entrevista/4242"))

    def test_con_el_modulo_apagado_no_invita(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = True
        _seed_doctor(doc)
        from unittest import mock
        from app.core import app_modules
        with mock.patch.object(app_modules, "is_enabled",
                               side_effect=lambda mid: mid != "intake"):
            notifications.notify_approved(self._row(_MED1["id"]))
        self.assertEqual(len(self.sent.calls), 1)
        _, _, body = self.sent.calls[0]
        self.assertNotIn("/entrevista/", body)


class TestEntrevistaFlagEnDetalle(BaseSettingsTestCase):
    """Deliverable 4 (2026-08-01): el detalle de una solicitud
    (booking_patient._public_detail, vía _entrevista_field(incluir_estado=True))
    expone entrevista.ofrecer + entrevista.completada — la señal que
    my-requests.js usa para mostrar u ocultar el botón "Completá la
    entrevista previa" en la tarjeta. Se prueba _entrevista_field directo (misma función
    que _public_detail llama) para no tener que armar una solicitud real en
    la base — interviews.request_id es una FK (Alembic 0032): el test que crea
    una entrevista siembra antes su solicitud."""

    def setUp(self):
        super().setUp()
        if not HAVE_DB:
            return
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")

    def test_ofrece_y_no_completada_sin_entrevista_creada(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = True  # opt-in explícito (default OFF)
        _seed_doctor(doc)
        from app.modules.turnos import booking_requests
        campo = booking_requests._entrevista_field(9001, _MED1["id"], incluir_estado=True)
        self.assertIsNotNone(campo)
        self.assertTrue(campo["ofrecer"])
        self.assertFalse(campo["completada"])

    def test_completada_pasa_a_true_cuando_termina_la_entrevista(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = True  # opt-in explícito (default OFF)
        _seed_doctor(doc)
        from app.modules.turnos import booking_requests
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            c.execute("INSERT INTO booking_requests (id, dni, first_name, status) "
                      "VALUES (9002, '30111009', 'Ana', 'pendiente') ON CONFLICT (id) DO NOTHING")
        iv = db_clinical.interview_create(doctor_id=_MED1["id"], entry_mode="post_booking", request_id=9002)
        campo = booking_requests._entrevista_field(9002, _MED1["id"], incluir_estado=True)
        self.assertFalse(campo["completada"])  # todavía activa
        db_clinical.interview_set_status(iv["id"], "done")
        campo = booking_requests._entrevista_field(9002, _MED1["id"], incluir_estado=True)
        self.assertTrue(campo["completada"])

    def test_medico_deshabilitado_no_ofrece_nada(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        from app.modules.turnos import booking_requests
        campo = booking_requests._entrevista_field(9003, _MED1["id"], incluir_estado=True)
        self.assertIsNone(campo)


class TestEntrevistaConMedicoDelSlot(BaseSettingsTestCase):
    """La CTA post-reserva del wizard: el POST de creación pasa el médico
    del bloque elegido (slot_medico_id) a _entrevista_field, así el override
    del médico decide aunque el default del sistema esté apagado (responsable del producto,
    2026-08-01 — kinesiología con un solo médico habilitado no mostraba la
    entrevista en "Solicitud enviada")."""

    def test_override_del_medico_gana_con_el_sistema_apagado(self):
        from app.modules.turnos import booking_requests
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = True
        _seed_doctor(doc)
        config.guardar({"INTAKE_ENABLED": "0"})
        campo = booking_requests._entrevista_field(4242, _MED1["id"])
        self.assertIsNotNone(campo)
        self.assertTrue(campo["ofrecer"])
        self.assertEqual(campo["url"], "/entrevista/4242")

    def test_sin_medico_conocido_no_se_ofrece_con_ningun_default(self):
        from app.modules.turnos import booking_requests
        _seed_doctor(_MED1)
        for sistema in ("0", "1"):
            config.guardar({"INTAKE_ENABLED": sistema})
            self.assertIsNone(booking_requests._entrevista_field(4242))
            # id desconocido = igual que sin médico, nunca rompe
            self.assertIsNone(booking_requests._entrevista_field(4242, "no-existe"))
        # El default encendido sigue valiendo para el médico que lo hereda.
        self.assertTrue(booking_requests._entrevista_field(4242, _MED1["id"])["ofrecer"])

    def test_create_alimenta_las_dos_respuestas_con_el_slot_medico(self):
        # Pin de contrato: TODA respuesta de éxito de _create llama
        # _entrevista_field con el slot_medico_id del body — era exactamente
        # el hueco que dejaba la CTA sin aparecer. Cuatro sitios desde
        # 2026-08-05 (data-integrity guards): la aprobada y la pendiente
        # originales, más las dos que agregó la idempotencia de Confirmá —
        # el repeat de un idem_key ya resuelto (`ya_resuelta`) y la carrera
        # rarísima donde el auto-confirm choca contra `AlreadyProcessed`/
        # `SlotConflict` — son la MISMA clase de respuesta de éxito y les
        # aplica el mismo contrato.
        import inspect
        from app.modules.turnos import booking_create
        src = inspect.getsource(booking_create)
        # Desde el corte de create_request en pasos (2026-10-30) el campo lo
        # pone un único sitio por forma de respuesta: `_respuesta_idem_existente`
        # (un idem_key ya resuelto, dos pasos) y `_with_interview` (la aprobada
        # automática, el choque auto-confirm y la pendiente). Ningún otro sitio
        # arma la respuesta de éxito a mano.
        self.assertEqual(src.count("_entrevista_field(sid, slot_medico_id)"), 2)
        self.assertEqual(src.count("_respuesta_idem_existente(existente, slot_medico_id)"), 1)
        self.assertEqual(src.count("_respuesta_idem_existente(b.fila, b.slot_medico_id)"), 1)
        self.assertEqual(src.count("return 200, _with_interview(out, "), 3)
        self.assertIn('body.get("slot_medico_id")', src)


class PinamarPackScopingTestCase(unittest.TestCase):
    """Pinamar's doctor-level interview override stays externally scoped."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.pinamar_pack = data_packs.private_pack_dir("ar-b-pinamar")
        except data_packs.RequiredPackError:
            raise unittest.SkipTest(
                "external private Pinamar pack is not configured")

    def test_solo_un_medico_tiene_la_entrevista_habilitada_en_pinamar(self):
        path = os.path.join(self.pinamar_pack, "medicos.json")
        with open(path, encoding="utf-8") as f:
            medicos = json.load(f)["medicos"]
        habilitados = [m["id"] for m in medicos if m.get("entrevista_habilitada") is True]
        self.assertEqual(habilitados, ["diego-coronel"])
        # El resto tiene que traer el override explícito en False, no
        # ausente — ausente heredaría el default del sistema (OFF desde
        # 2026-10-21), pero el explícito documenta la decisión del hospital.
        deshabilitados = [m["id"] for m in medicos if m["id"] != "diego-coronel"]
        for mid in deshabilitados:
            doc = next(m for m in medicos if m["id"] == mid)
            self.assertIs(doc.get("entrevista_habilitada"), False, mid)


class ExampleTownPackScopingTestCase(unittest.TestCase):
    def test_demo_does_not_manufacture_clinician_opt_ins(self):
        with open(os.path.join(ROOT, "packs", "example-town", "medicos.json"), encoding="utf-8") as stream:
            medicos = json.load(stream)["medicos"]
        self.assertTrue(medicos)
        self.assertTrue(all("entrevista_habilitada" not in doctor for doctor in medicos))


if __name__ == "__main__":
    unittest.main()
