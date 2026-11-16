"""test_intake_consult_admin.py — GET /api/admin/intake/consult/<id>
(modules/intake/consult_admin.py, fase 3).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_intake_consult_admin.py"

Sigue el patrón de test_doctor_appointments.py: base de test dedicada,
router FastAPI real, cookie firmada con admin_auth._mint_token, y un
webdata propio (TURNOS_WEBDATA_DIR) con dos médicos de especialidades
distintas para probar el filtro de catálogos.
"""

import os
import sys
import json
from copy import deepcopy
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.auth import admin_auth       # noqa: E402
from app.patient_web import catalog          # noqa: E402
from app.core import config  # noqa: E402
from app.core import deps as core_deps  # noqa: E402
from app.auth import turnstile          # noqa: E402
from app.modules.intake import consult_admin_router, printable_router  # noqa: E402

turnstile.ENV_PATH = ""  # no leer el .env real (mismo truco que los otros tests)
config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakeconsult", seed=lambda: db_operators.operators_list())


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
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE consultations RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE chat_messages")
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE operator_access")
            cur.execute("SELECT to_regclass('clinical_access_log') IS NOT NULL AS hay")
            if cur.fetchone()[0]:
                cur.execute("TRUNCATE clinical_access_log RESTART IDENTITY")


# ---- médicos de prueba: se mockea catalog.doctors() en vez de armar un
# webdata propio, porque TURNOS_WEBDATA_DIR es un override TOTAL (modo
# legacy, ver sitedata.py/data-packs.md) — pisaría también los catálogos
# reales de intake (packs/global/intake-*.json), que este archivo SÍ quiere
# leer de verdad para probar el filtro por especialidad.

_MEDICOS = [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología", "centro": "centro-a"},
    {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a"},
]


_env.process_set(ENABLED_MODULES="turnos,intake")
_APP = FastAPI()
_APP.add_exception_handler(
    core_deps.AdminGateError, core_deps.render_admin_gate_error)
_APP.include_router(consult_admin_router.router)
_APP.include_router(printable_router.router)
from app.core import deps as _early_deps  # noqa: E402
_early_deps.install_early_response(_APP)  # the app's answer of a dependency
from app.core import api_body as _api_body  # noqa: E402
_api_body.install(_APP)  # the family's 400 to a body that does not match its model, as in the app
_CLIENT = TestClient(_APP)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


class ConsultAdminTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        self._doctors_patch = mock.patch.object(
            catalog, "doctors", return_value=[dict(m) for m in _MEDICOS])
        self._doctors_patch.start()

    def tearDown(self):
        self._doctors_patch.stop()

    def _crear_operador_medico(self, usuario, medico_id):
        return db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)

    def _interview(self, doctor_id=None):
        return db_clinical.interview_create(doctor_id=doctor_id, entry_mode="post_booking",
                                   matched=bool(doctor_id))

    def _get(self, iid, headers):
        return _CLIENT.get(
            f"/api/admin/intake/consult/{iid}", headers=headers), True

    def _post_confirm(self, iid, body, headers):
        # Existing persistence tests model a completed native review. Security
        # regression tests below use _post_raw and supply their own snapshots.
        body = dict(body)
        reviewed = _CLIENT.get(f"/api/admin/intake/consult/{iid}", headers=headers)
        if reviewed.status_code == 200:
            data = reviewed.json()
            body.setdefault("patient_reviewed", {"nombre": data["nombre"], "dni": data["dni"] or ""})
            catalogs = data["catalogos"]
            labels = {}
            for key in ("diagnosticos", "estudios"):
                by_id = {item["id"]: item for item in catalogs[key]}
                selected = [by_id.get(item.get("id"), {}) if item.get("id") and not item.get("custom") else item
                            for item in body.get(key, [])]
                labels[key] = []
                for item in selected:
                    label = str(item.get("label") or "").strip()
                    if not label:
                        continue
                    if key == "diagnosticos" and item.get("codigo"):
                        label += f" ({item.get('sistema') or 'CIE-10'} {item['codigo']})"
                    labels[key].append(label)
            labels["medicacion"] = [" — ".join(str(item.get(key) or "").strip()
                for key in ("nombre", "dosis", "frecuencia", "duracion") if str(item.get(key) or "").strip())
                for item in body.get("medicacion", []) if str(item.get("nombre") or "").strip()]
            labels["notas"] = str(body.get("notas") or "").strip()
            body.setdefault("choices_reviewed", labels)
        return self._post_raw(iid, body, headers), True

    def _post_raw(self, iid, body, headers=None):
        return _CLIENT.post(
            f"/api/admin/intake/consult/{iid}/confirm",
            json=body, headers=_admin_headers() if headers is None else headers)

    # ---- ruteo --------------------------------------------------------

    def test_ruta_ajena_no_se_maneja(self):
        self.assertEqual(_CLIENT.get("/api/admin/otra-cosa").status_code, 404)

    def test_post_a_otra_ruta_no_se_maneja(self):
        response = _CLIENT.post("/api/admin/intake/consult/1/otra-cosa")
        self.assertEqual(response.status_code, 404)

    # ---- auth -----------------------------------------------------------

    def test_sin_sesion_401(self):
        interview = self._interview()
        h, manejado = self._get(interview["id"], headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_entrevista_inexistente_404(self):
        h, manejado = self._get(999999999, headers=_admin_headers())
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)

    # ---- admin ve cualquier entrevista -----------------------------------

    def test_admin_ve_cualquier_entrevista(self):
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["id"], interview["id"])
        self.assertEqual(h.json()["doctor_id"], "med-1")

    # ---- medico: sólo lo propio -------------------------------------------

    def test_medico_ve_su_propia_entrevista(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["id"], interview["id"])

    def test_medico_404_en_entrevista_de_otro_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        interview = self._interview(doctor_id="med-2")  # de OTRO médico
        h, _ = self._get(interview["id"], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    def _rol_propio_con_atencion(self, usuario, medico_id=None):
        """Una cuenta con un rol creado en el panel que tiene «atención médica»."""
        from app.core import permissions
        try:
            slug = permissions.create_role("Guardia", "Atiende la guardia.", ["atencion_medica"])["slug"]
        except permissions.RoleError:
            slug = "guardia"
        db_operators.operator_create(usuario, "clave1234", "admisionista", medico_id=medico_id)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, role_slug) VALUES (%s, %s)",
                        (usuario, slug))
        return _cookie(usuario, slug)

    def test_rol_propio_con_atencion_medica_no_abre_entrevistas_ajenas(self):
        # K10 (2026-10-23): el recorte miraba rol == "medico" y nada más, así
        # que un rol creado en el panel con «atención médica» abría cualquier
        # entrevista de cualquier médico.
        interview = self._interview(doctor_id="med-2")
        h, _ = self._get(interview["id"], headers=self._rol_propio_con_atencion("guardia1"))
        self.assertEqual(h.status_code, 404)

    def test_rol_propio_vinculado_a_un_medico_abre_las_de_ese_medico(self):
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"],
                         headers=self._rol_propio_con_atencion("guardia2", medico_id="med-1"))
        self.assertEqual(h.status_code, 200)

    def test_rol_con_todos_los_pacientes_abre_entrevistas_de_cualquier_medico(self):
        # El responsable del producto 2026-10-23 (q781376, variante B): jefatura/auditoría sin ser admin.
        from app.core import access_log, permissions
        try:
            slug = permissions.create_role("Jefatura", "Jefatura de servicio.",
                                           ["atencion_medica", "todos_los_pacientes"])["slug"]
        except permissions.RoleError:
            slug = "jefatura"
        db_operators.operator_create("jefa", "clave1234", "admisionista")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, role_slug) VALUES ('jefa', %s)",
                        (slug,))
        ajena = db_clinical.interview_create(doctor_id="med-2", entry_mode="post_booking",
                                    matched=True, dni="30111333")
        h, _ = self._get(ajena["id"], headers=_cookie("jefa", slug))
        self.assertEqual(h.status_code, 200)
        self.assertEqual([(r["actor"], r["action"], r["allowed"])
                          for r in access_log.history("30111333")],
                         [("jefa", "entrevista", True)])

    def test_cada_apertura_de_entrevista_queda_registrada(self):
        from app.core import access_log
        self._crear_operador_medico("dr-fernandez", "med-1")
        mia = db_clinical.interview_create(doctor_id="med-1", entry_mode="post_booking",
                                  matched=True, dni="30111222")
        ajena = db_clinical.interview_create(doctor_id="med-2", entry_mode="post_booking",
                                    matched=True, dni="30111222")
        self.assertEqual(self._get(mia["id"], headers=_cookie("dr-fernandez", "medico"))[0].status_code, 200)
        self.assertEqual(self._get(ajena["id"], headers=_cookie("dr-fernandez", "medico"))[0].status_code, 404)
        self.assertEqual(self._get(ajena["id"], headers=_admin_headers())[0].status_code, 200)
        filas = [(r["actor"], r["action"], r["target_id"], r["allowed"])
                 for r in reversed(access_log.history("30111222"))]
        self.assertEqual(filas, [
            ("dr-fernandez", "entrevista", str(mia["id"]), True),
            ("dr-fernandez", "entrevista", str(ajena["id"]), False),
            (admin_auth._admin_user(), "entrevista", str(ajena["id"]), True),
        ])

    def test_medico_sin_medico_id_404(self):
        self._crear_operador_medico("dr-sin-medico", None)
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_cookie("dr-sin-medico", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_medico_404_en_entrevista_sin_medico_asignado(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        interview = self._interview(doctor_id=None)
        h, _ = self._get(interview["id"], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    # ---- forma de la respuesta ---------------------------------------------

    def test_respuesta_no_expone_patient_token(self):
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertNotIn("patient_token", h.json())

    def test_respuesta_incluye_mensajes_y_catalogos(self):
        interview = self._interview(doctor_id="med-1")
        db_chat.chat_save_message("interview", interview["session_key"],
                             {"role": "assistant", "content": "¿Qué te trae hoy?"})
        db_chat.chat_save_message("interview", interview["session_key"],
                             {"role": "user", "content": "Dolor en el pecho."})
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(h.json()["mensajes"]), 2)
        self.assertEqual(h.json()["mensajes"][0]["role"], "assistant")
        self.assertIn("catalogos", h.json())
        for clave in ("estudios", "diagnosticos", "medicamentos", "frecuencias"):
            self.assertIn(clave, h.json()["catalogos"])

    def test_transcript_del_medico_sin_marcadores_internos(self):
        # Launch plan P4 (hospital map, doctor m13): the interview's closing
        # line reached the doctor as «… cuando te atienda. [fin]». The markers
        # are the agent's protocol; the patient never sees them, nor does the doctor.
        interview = self._interview(doctor_id="med-1")
        db_chat.chat_save_message("interview", interview["session_key"],
                             {"role": "assistant",
                              "content": "¿Tenés fiebre? [opciones: Sí | No | No sé]"})
        db_chat.chat_save_message("interview", interview["session_key"],
                             {"role": "user", "content": "Sí"})
        db_chat.chat_save_message("interview", interview["session_key"],
                             {"role": "assistant",
                              "content": "Gracias por contarme. Con esto el médico va a "
                                         "tener un panorama cuando te atienda. [fin]"})
        # A message that was only a marker leaves no empty bubble.
        db_chat.chat_save_message("interview", interview["session_key"],
                             {"role": "assistant", "content": "[fin]"})
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        textos = [m["content"] for m in h.json()["mensajes"]]
        self.assertEqual(textos, [
            "¿Tenés fiebre?",
            "Sí",
            "Gracias por contarme. Con esto el médico va a tener un panorama cuando te atienda.",
        ])

    def test_catalogos_filtrados_por_especialidad_del_medico(self):
        interview_cardio = self._interview(doctor_id="med-1")  # Cardiología
        h, _ = self._get(interview_cardio["id"], headers=_admin_headers())
        estudio_ids = {e["id"] for e in h.json()["catalogos"]["estudios"]}
        self.assertIn("ecg", estudio_ids)  # de cardiología
        self.assertIn("hemograma", estudio_ids)  # genérico, siempre presente

        interview_sin_medico = self._interview(doctor_id=None)
        h2, _ = self._get(interview_sin_medico["id"], headers=_admin_headers())
        estudio_ids2 = {e["id"] for e in h2.json()["catalogos"]["estudios"]}
        self.assertNotIn("ecg", estudio_ids2)  # sin médico -> sólo genéricos
        self.assertIn("hemograma", estudio_ids2)

    def test_incluye_triage_y_analisis_cuando_esta_guardado(self):
        interview = self._interview(doctor_id="med-1")
        db_clinical.interview_update_analysis(
            interview["id"], "ROJO", "resumen de prueba",
            [{"priority": 1, "point": "algo"}], [], ["ecg"],
            [{"code": "dolor_pecho_reposo", "label": "x", "because": "y"}], "fake")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.json()["triage"], "ROJO")
        self.assertEqual(h.json()["summary"], "resumen de prueba")
        self.assertEqual(h.json()["suggested_exams"], ["ecg"])
        self.assertIsNotNone(h.json()["analyzed_at"])

    def test_reopened_at_nulo_si_nunca_se_reabrio(self):
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertIsNone(h.json()["reopened_at"])

    def test_reopened_at_presente_despues_de_actualizar_entrevista(self):
        # "Actualizar entrevista" (2026-08-01): el panel usa este campo para
        # la marca "Actualizada" sobre el resumen (admin-consulta.js).
        interview = self._interview(doctor_id="med-1")
        db_clinical.interview_set_status(interview["id"], "done")
        db_clinical.interview_reopen(interview["id"])
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertIsNotNone(h.json()["reopened_at"])

    # ---- nombre del paciente: nunca "DNI <n>" cuando hay con quién matchear -

    def test_entrevista_matcheada_muestra_el_nombre_de_la_cuenta(self):
        # Camino normal post-reserva (interview_patient._find_or_create_interview):
        # matched=True, entered_name/_surname NULOS -- la cuenta registrada
        # tiene que ganar, nunca "DNI <n>".
        db_accounts.create_user("30111222", "Ana", "Pérez", "1990-01-01", "F", "ana@x.com",
                        "clave1234")
        interview = db_clinical.interview_create(
            doctor_id="med-1", entry_mode="post_booking", matched=True,
            dni="30111222")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.json()["nombre"], "Ana Pérez")

    def test_entrevista_matcheada_sin_cuenta_usa_la_solicitud(self):
        # Sin cuenta registrada todavía -- la solicitud de turno matcheada
        # (request_id) es la segunda fuente, antes de caer a "DNI <n>".
        sid = db_booking.create_booking_request(
            "30222333", "Jorge", "Gómez", "1985-05-05", "juan@x.com",
            "servicio-1", "centro-a")
        interview = db_clinical.interview_create(
            doctor_id="med-1", entry_mode="post_booking", matched=True,
            dni="30222333", request_id=sid)
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.json()["nombre"], "Jorge Gómez")

    def test_nombre_todo_en_mayusculas_se_normaliza(self):
        db_accounts.create_user("30333444", "MARIA JOSE", "VARELA", "1990-01-01",
                        "F", "mj@x.com", "clave1234")
        interview = db_clinical.interview_create(
            doctor_id="med-1", entry_mode="post_booking", matched=True,
            dni="30333444")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.json()["nombre"], "Maria Jose Varela")

    def test_entrevista_qr_sin_identificar_cae_a_dni_sin_romper(self):
        # QR walk-in sin nombre tipeado, sin cuenta, sin solicitud -- el
        # último recurso sigue siendo "DNI <n>", nunca un 500.
        interview = db_clinical.interview_create(
            doctor_id="med-1", entry_mode="qr", matched=False, dni="30999888")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["nombre"], "DNI 30999888")

    def test_entrevista_qr_con_nombre_tipeado_sin_cuenta_ni_solicitud(self):
        interview = db_clinical.interview_create(
            doctor_id="med-1", entry_mode="qr", matched=False,
            entered_name="Luis", entered_surname="Bianchi")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.json()["nombre"], "Luis Bianchi")

    # ---- POST .../confirm (fase 5) -----------------------------------------

    def test_confirm_sin_sesion_401(self):
        interview = self._interview(doctor_id="med-1")
        h, manejado = self._post_confirm(interview["id"], {}, headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_confirm_entrevista_inexistente_404(self):
        h, _ = self._post_confirm(999999999, {}, headers=_admin_headers())
        self.assertEqual(h.status_code, 404)

    def test_confirm_medico_404_en_entrevista_de_otro_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        interview = self._interview(doctor_id="med-2")
        h, _ = self._post_confirm(interview["id"], {}, headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_confirm_sin_medico_asignado_400(self):
        interview = self._interview(doctor_id=None)
        h, _ = self._post_confirm(interview["id"], {}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    def test_confirm_receta_y_orden_llevan_el_nombre_resuelto_no_el_dni(self):
        db_accounts.create_user("30444555", "Ana", "Pérez", "1990-01-01", "F", "ana@x.com",
                        "clave1234")
        interview = db_clinical.interview_create(
            doctor_id="med-1", entry_mode="post_booking", matched=True,
            dni="30444555")
        h, _ = self._post_confirm(interview["id"], {"notas": "x"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(fila["receta_json"]["paciente"]["nombre"], "Ana Pérez")
        self.assertEqual(fila["orden_json"]["paciente"]["nombre"], "Ana Pérez")

    def test_reviewed_draft_never_rewrites_with_ai_even_with_edited_prompts(self):
        interview = db_clinical.interview_create(doctor_id="med-1", dni="99000001",
                                       entered_name="Synthetic", entered_surname="Patient")
        body = {"estudios": [{"label": "Synthetic study", "custom": True}],
                "patient_reviewed": {"nombre": "Synthetic Patient", "dni": "99000001"}}
        with mock.patch("app.modules.intake.consult_compose._modifier_edited", return_value=True), \
             mock.patch("app.modules.intake.consult_compose._ai_rewrite", side_effect=AssertionError("draft must not invoke AI")):
            h, _ = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        saved = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(saved["orden_json"]["estudios"], ["Synthetic study"])
        self.assertEqual(saved["orden_json"]["paciente"], body["patient_reviewed"])
        self.assertIs(saved["orden_json"]["borrador"], True)
        self.assertIs(saved["receta_json"]["borrador"], True)

    def test_stale_reviewed_patient_cannot_create_a_draft(self):
        interview = db_clinical.interview_create(doctor_id="med-1", dni="99000001",
                                       entered_name="Synthetic", entered_surname="Patient")
        with mock.patch("app.modules.intake.consult_compose.compose", side_effect=AssertionError("must reject before composing")):
            h, _ = self._post_confirm(interview["id"], {
                "patient_reviewed": {"nombre": "Other Patient", "dni": "99000002"}
            }, headers=_admin_headers())
        self.assertEqual(h.status_code, 409)
        self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))

    def _review_fixture(self):
        interview = db_clinical.interview_create(doctor_id="med-1", dni="99000001",
                                       entered_name="Synthetic", entered_surname="Patient")
        # Independent literal snapshots: never derive expected review with the
        # production comparison or composer helpers.
        body = {
            "patient_reviewed": {"nombre": "Synthetic Patient", "dni": "99000001"},
            "diagnosticos": [{"label": "Manual diagnosis", "custom": True}],
            "estudios": [{"label": "Manual study", "custom": True}],
            "medicacion": [{"nombre": "Synthetic medicine", "dosis": "fixture dose",
                            "frecuencia": "fixture frequency", "duracion": "fixture duration"}],
            "notas": "Synthetic instructions",
            "choices_reviewed": {
                "diagnosticos": ["Manual diagnosis"], "estudios": ["Manual study"],
                "medicacion": ["Synthetic medicine — fixture dose — fixture frequency — fixture duration"],
                "notas": "Synthetic instructions",
            },
        }
        return interview, body

    def test_http_missing_or_malformed_review_refuses_without_writes(self):
        interview, valid = self._review_fixture()
        for key in ("patient_reviewed", "choices_reviewed"):
            for value in (None, False, "reviewed", [], "missing"):
                with self.subTest(key=key, value=value):
                    body = deepcopy(valid)
                    if value == "missing":
                        del body[key]
                    else:
                        body[key] = value
                    with mock.patch("app.modules.intake.consult_compose.compose", side_effect=AssertionError("must refuse before compose")), \
                         mock.patch("app.core.db_clinical.health_profile_save") as profile_save:
                        response = self._post_raw(interview["id"], body)
                    self.assertEqual(response.status_code, 400)
                    if isinstance(value, (bool, str, list)) and value != "missing":
                        # Not an object: the strict body boundary (TASK-76 item F) refuses it before the domain.
                        self.assertEqual(response.json()["error"], f"JSON inválido: campo {key}: se esperaba un objeto")
                    else:
                        self.assertIn("Recargá", response.json()["error"])
                    self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))
                    profile_save.assert_not_called()

    def test_http_stale_or_partial_choices_refuse_before_compose(self):
        interview, valid = self._review_fixture()
        for key in ("diagnosticos", "estudios", "medicacion", "notas"):
            for mutation in ("changed", "missing"):
                with self.subTest(key=key, mutation=mutation):
                    body = deepcopy(valid)
                    if mutation == "missing":
                        del body["choices_reviewed"][key]
                    else:
                        body["choices_reviewed"][key] = "Different instructions" if key == "notas" else ["Different choice"]
                    with mock.patch("app.modules.intake.consult_compose.compose", side_effect=AssertionError("must refuse before compose")):
                        response = self._post_raw(interview["id"], body)
                    self.assertEqual(response.status_code, 409)
                    self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))

    def test_http_catalog_change_requires_fresh_review_then_accepts_current_values(self):
        interview, body = self._review_fixture()
        body["diagnosticos"] = [{"id": "fixture-dx", "label": "Old diagnosis", "codigo": "OLD"}]
        body["choices_reviewed"]["diagnosticos"] = ["Old diagnosis (CIE-10 OLD)"]
        body["estudios"] = [{"id": "fixture-study", "label": "Old study"}]
        body["choices_reviewed"]["estudios"] = ["Old study"]
        with mock.patch("app.modules.intake.intake_catalog.diagnoses", return_value=[
                {"id": "fixture-dx", "label": "Current diagnosis", "codigo": "NEW", "sistema": "CIE-10"}]), \
             mock.patch("app.modules.intake.intake_catalog.exams", return_value=[
                {"id": "fixture-study", "label": "Current study"}]):
            response = self._post_raw(interview["id"], body)
            self.assertEqual(response.status_code, 409)
            self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))
            body["choices_reviewed"]["diagnosticos"] = ["Current diagnosis (CIE-10 NEW)"]
            body["choices_reviewed"]["estudios"] = ["Current study"]
            response = self._post_raw(interview["id"], body)
        self.assertEqual(response.status_code, 200)
        saved = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(saved["orden_json"]["diagnosticos"], ["Current diagnosis (CIE-10 NEW)"])
        self.assertEqual(saved["orden_json"]["estudios"], ["Current study"])

    def test_http_removed_catalog_choice_cannot_silently_disappear(self):
        interview, body = self._review_fixture()
        body["estudios"] = [{"id": "removed-study", "label": "Reviewed study"}]
        body["choices_reviewed"]["estudios"] = ["Reviewed study"]
        response = self._post_raw(interview["id"], body)
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))

    def test_http_current_stored_patient_identity_wins_over_review(self):
        interview, body = self._review_fixture()
        db_accounts.create_user("99000001", "Current", "Identity", "1990-01-01", "F",
                       "synthetic@example.invalid", "synthetic-test-only")
        response = self._post_raw(interview["id"], body)
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))
        body["patient_reviewed"] = {"nombre": "Current Identity", "dni": "99000001"}
        response = self._post_raw(interview["id"], body)
        self.assertEqual(response.status_code, 200)
        saved = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(saved["orden_json"]["paciente"], body["patient_reviewed"])

    def test_http_valid_manual_review_persists_and_reloads_unsigned_drafts_without_ai(self):
        interview, body = self._review_fixture()
        with mock.patch("app.ai.ai_client.call", side_effect=AssertionError("no external AI")), \
             mock.patch("app.modules.intake.consult_compose._ai_rewrite", side_effect=AssertionError("no rewriting")), \
             mock.patch("app.core.db_clinical.health_profile_save") as profile_save:
            response = self._post_raw(interview["id"], body)
        self.assertEqual(response.status_code, 200)
        profile_save.assert_not_called()
        saved = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(saved["receta_json"]["lineas"], body["choices_reviewed"]["medicacion"])
        self.assertEqual(saved["orden_json"]["diagnosticos"], body["choices_reviewed"]["diagnosticos"])
        self.assertEqual(saved["orden_json"]["estudios"], body["choices_reviewed"]["estudios"])
        self.assertEqual(saved["notes"], body["choices_reviewed"]["notas"])
        for key in ("receta", "orden"):
            self.assertIs(saved[key + "_json"]["borrador"], True)
            for _ in range(2):
                page = _CLIENT.get(response.json()[key + "_url"])
                self.assertEqual(page.status_code, 200)
                self.assertIn("BORRADOR", page.text)
                self.assertIn("Synthetic Patient", page.text)

    def test_http_complete_empty_review_remains_valid_without_ai_choices(self):
        interview, body = self._review_fixture()
        body = {"patient_reviewed": body["patient_reviewed"], "choices_reviewed": {
            "diagnosticos": [], "estudios": [], "medicacion": [], "notas": ""}}
        response = self._post_raw(interview["id"], body)
        self.assertEqual(response.status_code, 200)
        saved = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(saved["diagnoses"], [])
        self.assertEqual(saved["exams"], [])
        self.assertEqual(saved["meds"], [])

    def test_http_review_snapshot_never_grants_permission(self):
        interview, body = self._review_fixture()
        self._crear_operador_medico("other-doctor", "med-2")
        db_operators.operator_create("report-reader", "synthetic-test-only", "reportes")
        for headers, expected in (({}, 401), (_cookie("other-doctor", "medico"), 404),
                                  (_cookie("report-reader", "reportes"), 403)):
            with self.subTest(status=expected):
                response = self._post_raw(interview["id"], body, headers)
                self.assertEqual(response.status_code, expected)
                self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))

    def test_confirm_no_pega_a_la_red_con_fake_ai(self):
        # TURNOS_FAKE_AI=1 está seteado arriba en todo el módulo — si
        # consult_compose llamara a la red igual, este mock lo revienta.
        with mock.patch("app.ai.ai_client.call", side_effect=AssertionError("no debía llamar a la red")):
            interview = self._interview(doctor_id="med-1")
            h, _ = self._post_confirm(interview["id"], {"notas": "x"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)

    def test_confirm_happy_path_persiste_y_devuelve_urls(self):
        interview = self._interview(doctor_id="med-1")
        body = {
            "diagnosticos": [{"id": "hta", "custom": False}],
            "estudios": [{"id": "hemograma", "custom": False}],
            "medicacion": [{"nombre": "Paracetamol", "dosis": "500 mg",
                            "frecuencia": "cada 8 h", "duracion": "5 días"}],
            "notas": "Control en 15 días.",
        }
        h, manejado = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertTrue(h.json()["receta_url"].startswith("/r/"))
        self.assertTrue(h.json()["orden_url"].startswith("/o/"))

        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertIsNotNone(fila)
        self.assertEqual(fila["interview_id"], interview["id"])
        self.assertEqual(len(fila["diagnoses"]), 1)
        self.assertEqual(fila["diagnoses"][0]["label"], "Hipertensión arterial esencial")
        self.assertEqual(fila["receta_json"]["paciente"]["dni"], interview["dni"] or "")
        self.assertIn("Paracetamol", fila["receta_json"]["lineas"][0])
        self.assertIn("Hipertensión arterial esencial", fila["orden_json"]["diagnosticos"][0])

    def test_confirm_coercion_descarta_vacios_e_ids_ajenos(self):
        interview = self._interview(doctor_id="med-1")
        body = {
            "diagnosticos": [{"id": "no-existe-en-el-catalogo", "custom": False},
                             {"label": "   ", "custom": True}],
            "estudios": [{"id": "no-existe-en-el-catalogo", "custom": False}],
            "medicacion": [{"nombre": "   ", "dosis": "x"}],
            "notas": "",
        }
        h, _ = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(fila["diagnoses"], [])
        self.assertEqual(fila["exams"], [])
        self.assertEqual(fila["meds"], [])

    def test_confirm_no_recorta_silenciosamente_40_items_revisados(self):
        interview = self._interview(doctor_id="med-1")
        body = {"medicacion": [{"nombre": f"Droga {i}"} for i in range(40)]}
        h, _ = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertEqual(h.status_code, 409)
        self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))

    def test_confirm_diagnostico_custom_pasa_como_texto_libre(self):
        interview = self._interview(doctor_id="med-1")
        body = {"diagnosticos": [{"label": "Mi diagnóstico a mano", "custom": True}]}
        h, _ = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(fila["diagnoses"][0]["label"], "Mi diagnóstico a mano")
        self.assertTrue(fila["diagnoses"][0]["custom"])

    def test_get_incluye_el_medico_para_la_revision_previa(self):
        # La revisión identifica al profesional responsable: el GET
        # entrega nombre/especialidad/matrícula best-effort (2026-10-21).
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico"]["nombre"], "María José Fernández")
        self.assertEqual(h.json()["medico"]["especialidad"], "Cardiología")
        interview_sin_medico = self._interview(doctor_id=None)
        h2, _ = self._get(interview_sin_medico["id"], headers=_admin_headers())
        self.assertEqual(h2.status_code, 200)
        self.assertEqual(h2.json()["medico"], {})

    def test_confirm_solo_manual_sin_ia_persiste_exactamente_lo_elegido(self):
        # Sin análisis de IA guardado y sin una sola selección del catálogo:
        # la orden/receta salen 1:1 de las entradas a mano del clínico.
        interview = self._interview(doctor_id="med-1")
        body = {
            "diagnosticos": [{"label": "Mi diagnóstico a mano", "custom": True}],
            "estudios": [{"label": "Mi estudio a mano", "custom": True}],
            "medicacion": [{"nombre": "Ibuprofeno", "dosis": "400 mg",
                            "frecuencia": "cada 12 h", "duracion": "3 días"}],
            "notas": "Volver si empeora.",
        }
        h, _ = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(fila["orden_json"]["estudios"], ["Mi estudio a mano"])
        self.assertIn("Mi diagnóstico a mano", fila["orden_json"]["diagnosticos"][0])
        self.assertEqual(fila["orden_json"]["paciente"]["dni"], interview["dni"] or "")
        self.assertIn("Ibuprofeno — 400 mg", fila["receta_json"]["lineas"][0])
        self.assertEqual(fila["notes"], "Volver si empeora.")

    def test_confirm_sin_selecciones_deja_la_orden_vacia_comportamiento_definido(self):
        # "Ninguno seleccionado" es un estado válido y definido: la orden
        # persiste con estudios vacíos, sin inventar nada del catálogo.
        interview = self._interview(doctor_id="med-1")
        h, _ = self._post_confirm(interview["id"], {}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(fila["orden_json"]["estudios"], [])
        self.assertEqual(fila["orden_json"]["diagnosticos"], [])
        self.assertEqual(fila["meds"], [])

    # ---- diagnóstico a mano con código CIE-10 (doctor.json m14, 2026-10-23) ---
    # El catálogo del pack sólo trae cardiología: un médico de otra
    # especialidad escribe el diagnóstico y, si lo sabe, su código.

    def test_diagnostico_a_mano_con_codigo_cie10_normalizado(self):
        interview = self._interview(doctor_id="med-2")
        h, _ = self._post_confirm(interview["id"], {"diagnosticos": [
            {"label": "Bronquitis aguda", "codigo": "J20.9", "custom": True}]},
            headers=_admin_headers())
        self.assertEqual(h.status_code, 200, h.text)
        # mismo normalizado que el panel (admin-consult.mjs:cie10)
        from app.modules.intake import consult_admin
        self.assertEqual(consult_admin._codigo_cie10(" j209 "), "J20.9")
        self.assertEqual(consult_admin._codigo_cie10("I10"), "I10")
        self.assertEqual(consult_admin._codigo_cie10("U07.1"), "U07.1")
        self.assertIsNone(consult_admin._codigo_cie10("bronq"))
        fila = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(fila["diagnoses"][0]["codigo"], "J20.9")
        self.assertEqual(fila["diagnoses"][0]["sistema"], "CIE-10")
        self.assertIn("Bronquitis aguda (CIE-10 J20.9)", fila["orden_json"]["diagnosticos"])

    def test_diagnostico_a_mano_con_codigo_invalido_400_sin_escribir(self):
        interview = self._interview(doctor_id="med-2")
        h = self._post_raw(interview["id"], {
            "diagnosticos": [{"label": "Bronquitis", "codigo": "bronq", "custom": True}],
            "patient_reviewed": {"nombre": "x", "dni": ""}, "choices_reviewed": {}})
        self.assertEqual(h.status_code, 400)
        self.assertIn("CIE-10", h.json()["error"])
        self.assertIsNone(db_clinical.consultation_latest_for_interview(interview["id"]))

    # ---- consulta ya confirmada (problems.md №1, 2026-10-23) ----------------
    # Reabrir una consulta confirmada mostraba el formulario vacío y un segundo
    # confirm creaba otra receta vigente: dos recetas válidas del mismo acto.

    def test_reabrir_consulta_confirmada_devuelve_lo_emitido(self):
        interview = self._interview(doctor_id="med-1")
        body = {"diagnosticos": [{"label": "Bronquitis aguda", "custom": True}],
                "estudios": [{"label": "Hemograma completo", "custom": True}],
                "medicacion": [{"nombre": "Paracetamol 500 mg", "dosis": "1 comprimido",
                                "frecuencia": "cada 8 horas", "duracion": "5 días"}],
                "notas": "Reposo."}
        h1, _ = self._post_confirm(interview["id"], body, headers=_admin_headers())
        self.assertEqual(h1.status_code, 200)
        h, _ = self._get(interview["id"], headers=_admin_headers())
        confirmada = h.json().get("confirmada")
        self.assertIsNotNone(confirmada, "la consulta reabierta tiene que traer lo ya emitido")
        self.assertEqual(confirmada["receta_url"], h1.json()["receta_url"])
        self.assertEqual(confirmada["orden_url"], h1.json()["orden_url"])
        self.assertEqual(confirmada["diagnosticos"], ["Bronquitis aguda"])
        self.assertEqual(confirmada["estudios"], ["Hemograma completo"])
        self.assertEqual(confirmada["medicacion"],
                         ["Paracetamol 500 mg — 1 comprimido — cada 8 horas — 5 días"])
        self.assertEqual(confirmada["notas"], "Reposo.")
        self.assertTrue(confirmada["confirmada_at"])

    def test_sin_consulta_confirmada_el_campo_es_nulo(self):
        interview = self._interview(doctor_id="med-1")
        h, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertIsNone(h.json()["confirmada"])

    def test_segundo_confirm_sin_reemplazar_409_y_no_crea_otra_receta(self):
        interview = self._interview(doctor_id="med-1")
        h1, _ = self._post_confirm(interview["id"], {"notas": "primera"}, headers=_admin_headers())
        self.assertEqual(h1.status_code, 200)
        h2, _ = self._post_confirm(interview["id"], {"notas": "segunda"}, headers=_admin_headers())
        self.assertEqual(h2.status_code, 409)
        self.assertEqual(h2.json()["confirmada"]["receta_url"], h1.json()["receta_url"])
        self.assertEqual(self._consultas(interview["id"]), 1)

    def test_reemplazar_anula_la_receta_vieja_y_deja_una_sola_vigente(self):
        interview = self._interview(doctor_id="med-1")
        h1, _ = self._post_confirm(interview["id"], {"notas": "primera"}, headers=_admin_headers())
        vieja = db_clinical.consultation_by_token(h1.json()["receta_url"].rsplit("/", 1)[-1])
        h2, _ = self._post_confirm(interview["id"], {"notas": "segunda", "reemplaza_id": vieja["id"]},
                                   headers=_admin_headers())
        self.assertEqual(h2.status_code, 200)
        self.assertNotEqual(h1.json()["receta_url"], h2.json()["receta_url"])
        vieja = db_clinical.consultation_by_token(h1.json()["receta_url"].rsplit("/", 1)[-1])
        self.assertIsNotNone(vieja["superseded_at"])
        nueva = db_clinical.consultation_latest_for_interview(interview["id"])
        self.assertEqual(vieja["superseded_by"], nueva["id"])
        self.assertIsNone(nueva["superseded_at"])
        # el link viejo NO se borra (responsable del producto, 2026-10-23): sigue mostrando el texto
        # emitido, marcado ANULADA — REEMPLAZADA, sin botón de imprimir y con
        # el link al documento que lo reemplaza
        for viejo, nuevo in ((h1.json()["receta_url"], h2.json()["receta_url"]),
                             (h1.json()["orden_url"], h2.json()["orden_url"])):
            r = _CLIENT.get(viejo)
            self.assertEqual(r.status_code, 200, viejo)
            self.assertIn("ANULADA — REEMPLAZADA", r.text)
            self.assertIn("primera", r.text)
            self.assertIn('href="%s"' % nuevo, r.text)
            self.assertNotIn("window.print()", r.text)
        r = _CLIENT.get(h2.json()["receta_url"])
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("ANULADA", r.text)
        g, _ = self._get(interview["id"], headers=_admin_headers())
        self.assertEqual(g.json()["confirmada"]["receta_url"], h2.json()["receta_url"])
        self.assertEqual(g.json()["confirmada"]["reemplazos"], 1)

    def test_link_viejo_apunta_a_la_vigente_tras_dos_reemplazos(self):
        interview = self._interview(doctor_id="med-1")
        h1, _ = self._post_confirm(interview["id"], {"notas": "primera"}, headers=_admin_headers())
        primera = db_clinical.consultation_latest_for_interview(interview["id"])
        h2, _ = self._post_confirm(interview["id"], {"notas": "segunda", "reemplaza_id": primera["id"]},
                                   headers=_admin_headers())
        segunda = db_clinical.consultation_latest_for_interview(interview["id"])
        h3, _ = self._post_confirm(interview["id"], {"notas": "tercera", "reemplaza_id": segunda["id"]},
                                   headers=_admin_headers())
        for viejo in (h1.json()["receta_url"], h2.json()["receta_url"]):
            r = _CLIENT.get(viejo)
            self.assertEqual(r.status_code, 200, viejo)
            self.assertIn('href="%s"' % h3.json()["receta_url"], r.text)
        # el texto emitido no se reescribe: la primera sigue diciendo «primera»
        self.assertIn("primera", _CLIENT.get(h1.json()["receta_url"]).text)
        self.assertNotIn("ANULADA", _CLIENT.get(h3.json()["receta_url"]).text)

    def test_reemplazar_con_id_viejo_409(self):
        # Dos pantallas abiertas: la segunda no puede pisar un reemplazo que
        # no vio — reemplaza_id tiene que ser la consulta vigente AHORA.
        interview = self._interview(doctor_id="med-1")
        self._post_confirm(interview["id"], {"notas": "primera"}, headers=_admin_headers())
        primera = db_clinical.consultation_latest_for_interview(interview["id"])
        h2, _ = self._post_confirm(interview["id"], {"notas": "segunda", "reemplaza_id": primera["id"]},
                                   headers=_admin_headers())
        self.assertEqual(h2.status_code, 200)
        h3, _ = self._post_confirm(interview["id"], {"notas": "tercera", "reemplaza_id": primera["id"]},
                                   headers=_admin_headers())
        self.assertEqual(h3.status_code, 409)
        self.assertEqual(self._consultas(interview["id"]), 2)

    def test_dos_confirm_simultaneos_crean_una_sola_receta(self):
        import threading
        interview = self._interview(doctor_id="med-1")
        g, _ = self._get(interview["id"], headers=_admin_headers())
        body = {"patient_reviewed": {"nombre": g.json()["nombre"], "dni": g.json()["dni"] or ""},
                "choices_reviewed": {"diagnosticos": [], "estudios": [], "medicacion": [], "notas": ""}}
        barrera = threading.Barrier(2)
        estados = []
        real = db_clinical.consultation_confirm

        def lento(*a, **kw):
            barrera.wait(timeout=5)
            return real(*a, **kw)

        def enviar():
            estados.append(self._post_raw(interview["id"], body).status_code)

        with mock.patch.object(db_clinical, "consultation_confirm", side_effect=lento):
            hilos = [threading.Thread(target=enviar) for _ in range(2)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(10)
        self.assertEqual(sorted(estados), [200, 409])
        self.assertEqual(self._consultas(interview["id"]), 1)

    def _consultas(self, iid):
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("SELECT count(*) FROM consultations WHERE interview_id=%s", (int(iid),))
                return cur.fetchone()[0]


if __name__ == "__main__":
    unittest.main()
