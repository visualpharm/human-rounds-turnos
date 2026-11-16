"""test_admin_patients.py — pacientes como entidad admin (2026-07-24).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_admin_patients -v

Base dedicada `sana_turnos_test_pac` (mismo patrón que test_cola_dia) para
poder correr en paralelo con las otras suites. Cubre: CRUD + archivo
(activo=false corta login y sesiones vivas) + permisos por rol.
"""

import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.auth import admin_auth        # noqa: E402
from app.admin_panel import admin_patients, admin_patients_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_patients_router.router)
from app.core import api_body as _api_body  # noqa: E402
_api_body.install(_app)  # the family's 400 to a body that does not match its model, as in the app
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_pac", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


class AdminPacientesTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                # `solicitudes` entró acá cuando el ranking de asistencia empezó a
                # leerla: sin truncarla, el historial de un test se le colaba al
                # siguiente y el puntaje dejaba de ser reproducible.
                cur.execute("TRUNCATE users, sessions, dependents, booking_requests,"
                            " attendance_events, interviews RESTART IDENTITY CASCADE")
        admin_auth._ATTEMPTS.clear()

    def _req(self, method, path, body=None, headers=None, completar=True):
        # Staff alta asks for birth date and sex (HSI needs them). The cases
        # below are about something else, so the fixture fills them in.
        if completar and method == "POST" and path == "/api/admin/patients" and isinstance(body, dict):
            body = dict({"nacimiento": "1980-01-01", "sexo": "F"}, **body)
        return _CLIENT.request(method, path, json=body, headers=headers or _admin_headers())

    def test_una_persona_a_cargo_no_se_guarda_sin_sexo(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        h = self._req("POST", "/api/admin/patients/30111222/dependents",
                      body={"dni": "55112299", "nombre": "Tomi", "apellido": "Pérez",
                            "nacimiento": "2015-03-14", "vinculo": "hijo"})
        self.assertEqual(h.status_code, 400, h.json())
        self.assertEqual(h.json(), {"error": "Falta el sexo."})

    def test_el_alta_pide_nacimiento_y_sexo(self):
        base = {"dni": "30111299", "nombre": "Juana", "apellido": "Pérez"}
        for falta in ({}, {"nacimiento": "1985-03-20"}, {"sexo": "F"}):
            h = self._req("POST", "/api/admin/patients", body=dict(base, **falta), completar=False)
            self.assertEqual(h.status_code, 400, h.json())
            self.assertEqual(h.json(), {"error": "Faltan fecha de nacimiento y sexo."})
        h = self._req("POST", "/api/admin/patients", completar=False,
                      body=dict(base, nacimiento="1985-03-20", sexo="F"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["item"]["sexo"], "F")

    # ---- CRUD ---------------------------------------------------------

    def test_alta_edicion_y_busqueda(self):
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez",
                            "nacimiento": "1985-03-20", "contacto": "2254-555555"})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["ok"])
        # sin password → viene una temporal, UNA vez
        self.assertGreaterEqual(len(h.json()["password_temporal"]), 8)
        self.assertNotIn("pass_hash", h.json()["item"])

        h = self._req("GET", "/api/admin/patients?q=Pérez")
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111222"])

        h = self._req("PUT", "/api/admin/patients/30111222",
                      body={"contacto": "2254-000000"})
        self.assertEqual(h.status_code, 200)
        # la mesa carga el teléfono como quiera; se guarda canónico, igual
        # que el que carga el paciente (accounts._norm_phone)
        self.assertEqual(h.json()["item"]["contacto"], "+542254000000")
        self.assertEqual(h.json()["item"]["nombre"], "Juana")  # parcial: lo demás queda

    def test_un_email_en_telefono_se_rechaza_y_no_borra_el_dato(self):
        # 25/10 (demo): un email en «Teléfono» se normalizaba a nada y el
        # teléfono que había quedaba borrado en silencio.
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez",
                            "nacimiento": "1985-03-20", "sexo": "F",
                            "contacto": "juana.perez@example.test"})
        self.assertEqual(h.status_code, 400, h.json())
        self.assertIn("teléfono", h.json()["error"])
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez",
                            "nacimiento": "1985-03-20", "sexo": "F",
                            "contacto": "2254 55-5555"})
        self.assertEqual(h.status_code, 200, h.json())
        guardado = h.json()["item"]["contacto"]
        self.assertTrue(guardado.startswith("+54"), guardado)
        for malo in ("juana.perez@example.test", "llamar a la tarde", "12"):
            h = self._req("PUT", "/api/admin/patients/30111222", body={"contacto": malo})
            self.assertEqual(h.status_code, 400, malo)
        h = self._req("GET", "/api/admin/patients/30111222")
        self.assertEqual(h.json()["item"]["contacto"], guardado)
        # vacío sigue valiendo: el teléfono no es obligatorio
        h = self._req("PUT", "/api/admin/patients/30111222", body={"contacto": ""})
        self.assertEqual(h.status_code, 200, h.json())

    def test_alta_dni_duplicado_409_e_invalido_400(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "30111222", "nombre": "Otra", "apellido": "Persona"})
        self.assertEqual(h.status_code, 409)
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "12", "nombre": "X", "apellido": "Y"})
        self.assertEqual(h.status_code, 400)

    def test_password_propia_corta_400(self):
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "30111223", "nombre": "A", "apellido": "B",
                            "password": "corta"})
        self.assertEqual(h.status_code, 400)

    # ---- paginación (auditoría 2026-08-06: 200 filas sin OFFSET ni total) --

    def test_lista_pagina_con_offset_y_reporta_total(self):
        # Antes la lista se cortaba en 200 filas SIEMPRE, sin OFFSET y sin
        # ningún rastro de que hubiera más — acá con 3 filas y limit=2 alcanza
        # para probar las dos mitades del contrato: la página Y el total real.
        for i, dni in enumerate(("30111220", "30111221", "30111222")):
            self._req("POST", "/api/admin/patients",
                      body={"dni": dni, "nombre": "Paciente%d" % i, "apellido": "Apellido"})
        h = self._req("GET", "/api/admin/patients?limit=2")
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(len(h.json()["items"]), 2)
        self.assertEqual(h.json()["total"], 3)
        self.assertEqual(h.json()["offset"], 0)
        self.assertEqual(h.json()["limit"], 2)

        h = self._req("GET", "/api/admin/patients?limit=2&offset=2")
        self.assertEqual(len(h.json()["items"]), 1)
        self.assertEqual(h.json()["total"], 3)
        self.assertEqual(h.json()["offset"], 2)

        # sin ningún query param: sigue devolviendo total (antes no existía).
        h = self._req("GET", "/api/admin/patients")
        self.assertEqual(len(h.json()["items"]), 3)
        self.assertEqual(h.json()["total"], 3)

    def test_contrasena_temporal_desde_la_mesa_entra_y_la_vieja_no(self):
        """TASK-50 (26.10): quien no tiene email y se olvidó la contraseña
        vuelve a entrar con una temporal que le da la mesa. La anterior deja
        de valer y sus sesiones abiertas se cierran."""
        db_accounts.create_user("30111250", "Norma", "Sinmail", None, None, None, "vieja-clave-1")
        sesion = db_accounts.create_session("30111250")
        h = self._req("POST", "/api/admin/patients/30111250/temp-password")
        self.assertEqual(h.status_code, 200, h.text)
        temp = h.json()["password_temporal"]
        self.assertTrue(db_accounts.verify_password("30111250", temp))
        self.assertFalse(db_accounts.verify_password("30111250", "vieja-clave-1"))
        self.assertFalse(db_accounts.get_session(sesion), "the open session was closed")

    def test_db_users_admin_list_devuelve_items_y_total(self):
        # Boundary contract: the native router's domain read remains
        # `(items, total)`, not a bare list.
        for dni in ("30111220", "30111221", "30111222"):
            db_accounts.create_user(dni, "N", "A", None, None, None, "pass12345")
        items, total = db_accounts.users_admin_list(limit=2, offset=1)
        self.assertEqual(total, 3)
        self.assertEqual(len(items), 2)

    def test_patients_of_doctor_recorta_a_12_meses_por_default(self):
        # «Mis pacientes» venía trayendo TODO el historial, sin límite de
        # tiempo (auditoría 2026-08-06). Acá una solicitud reciente y una de
        # hace más de un año, referidas al mismo médico por slot_doctor_id.
        db_accounts.create_user("30111220", "Reciente", "Paciente", None, None, None, "pass12345")
        db_accounts.create_user("30111221", "Vieja", "Paciente", None, None, None, "pass12345")
        db_booking.create_booking_request(
            "30111220", "Reciente", "Paciente", None, None,
            "serv-x", "centro-x", slot_medico_id="med-1")
        sid_vieja = db_booking.create_booking_request(
            "30111221", "Vieja", "Paciente", None, None,
            "serv-x", "centro-x", slot_medico_id="med-1")
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("UPDATE booking_requests SET created=now() - interval '400 days' WHERE id=%s",
                            (sid_vieja,))

        recortado = db_clinical.patients_of_doctor("med-1")
        self.assertEqual([r["dni"] for r in recortado], ["30111220"])

        completo = db_clinical.patients_of_doctor("med-1", completo=True)
        self.assertEqual(sorted(r["dni"] for r in completo), ["30111220", "30111221"])

    def test_patients_of_doctor_limit_y_cursor_paginan(self):
        for i in range(3):
            dni = "3011122%d" % i
            db_accounts.create_user(dni, "P%d" % i, "Apellido", None, None, None, "pass12345")
            db_booking.create_booking_request(dni, "P%d" % i, "Apellido", None, None,
                                      "serv-x", "centro-x", slot_medico_id="med-2")
        pagina1 = db_clinical.patients_of_doctor("med-2", completo=True, limit=2)
        self.assertEqual(len(pagina1), 2)
        ultimo = pagina1[-1]
        cursor = (ultimo["created"], ultimo["id"])
        pagina2 = db_clinical.patients_of_doctor("med-2", completo=True, limit=2, cursor=cursor)
        self.assertEqual(len(pagina2), 1)
        # sin superposición entre páginas
        self.assertFalse({p["id"] for p in pagina1} & {p["id"] for p in pagina2})

    def test_interviews_of_doctor_recorta_a_12_meses_por_default(self):
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO interviews (doctor_id, dni, session_key, status, created) "
                    "VALUES (%s,%s,%s,'active', now()) RETURNING id",
                    ("med-3", "30111220", "sk-reciente"))
                cur.execute(
                    "INSERT INTO interviews (doctor_id, dni, session_key, status, created) "
                    "VALUES (%s,%s,%s,'active', now() - interval '400 days') RETURNING id",
                    ("med-3", "30111221", "sk-vieja"))
        recortado = db_clinical.interviews_of_doctor("med-3")
        self.assertEqual([r["dni"] for r in recortado], ["30111220"])
        completo = db_clinical.interviews_of_doctor("med-3", completo=True)
        self.assertEqual(sorted(r["dni"] for r in completo), ["30111220", "30111221"])

    # ---- archivo ------------------------------------------------------

    def test_archivar_corta_login_y_sesiones(self):
        h = self._req("POST", "/api/admin/patients",
                      body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez",
                            "password": "clave-segura-1"})
        self.assertEqual(h.status_code, 200)
        # login y sesión funcionan antes del archivo
        self.assertIsNotNone(db_accounts.verify_password("30111222", "clave-segura-1"))
        token = db_accounts.create_session("30111222")
        self.assertIsNotNone(db_accounts.get_session(token))

        h = self._req("DELETE", "/api/admin/patients/30111222")
        self.assertEqual(h.status_code, 200)
        self.assertIs(h.json()["item"]["activo"], False)
        # archivada: ni login ni la sesión que ya estaba viva
        self.assertIsNone(db_accounts.verify_password("30111222", "clave-segura-1"))
        self.assertIsNone(db_accounts.get_session(token))

        # restaurar = PUT activo=true
        h = self._req("PUT", "/api/admin/patients/30111222", body={"activo": True})
        self.assertIs(h.json()["item"]["activo"], True)
        self.assertIsNotNone(db_accounts.verify_password("30111222", "clave-segura-1"))

    def test_archivar_inexistente_404(self):
        h = self._req("DELETE", "/api/admin/patients/99999999")
        self.assertEqual(h.status_code, 404)

    # ---- historia clínica digitalizada (seeding vía HTTP, 2026-08-03) ---

    def test_clinical_history_reemplazo_atomico(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        registros = [{"fecha": "2024-02-15", "tipo": "laboratorio",
                      "servicio": "Laboratorio Central",
                      "valores": {"glucemia_mg_dl": 138}}]
        h = self._req("POST", "/api/admin/patients/30111222/clinical-history",
                      body={"records": registros, "source_system": "local_history"})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["ok"])
        self.assertEqual(h.json()["count"], 1)
        guardados = db_clinical.clinical_history_by_dni("30111222")
        self.assertEqual(len(guardados), 1)
        self.assertEqual(guardados[0]["tipo"], "laboratorio")

        # un segundo POST reemplaza, no acumula (mismo contrato que
        # db_clinical.clinical_history_replace / scripts/import_clinical_history.py)
        h = self._req("POST", "/api/admin/patients/30111222/clinical-history",
                      body={"records": [], "source_system": "local_history"})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["count"], 0)
        self.assertEqual(db_clinical.clinical_history_by_dni("30111222"), [])

    def test_clinical_history_dni_inexistente_404(self):
        h = self._req("POST", "/api/admin/patients/99999999/clinical-history",
                      body={"records": []})
        self.assertEqual(h.status_code, 404)

    def test_clinical_history_records_no_lista_400(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        h = self._req("POST", "/api/admin/patients/30111222/clinical-history",
                      body={"records": "no-es-una-lista"})
        self.assertEqual(h.status_code, 400)

    def test_clinical_history_solo_admin(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        h = self._req("POST", "/api/admin/patients/30111222/clinical-history",
                      body={"records": []}, headers=_cookie("mesa-1", "admisionista"))
        self.assertEqual(h.status_code, 403)
        h = self._req("POST", "/api/admin/patients/30111222/clinical-history",
                      body={"records": []}, headers=_cookie("dr-1", "medico"))
        self.assertEqual(h.status_code, 403)

    # ---- ficha completa: cobertura y personas a cargo -------------------

    def test_la_ficha_trae_lo_que_la_lista_no(self):
        # La lista NO trae cobertura (son 200 filas); la ficha sí. Editar
        # desde la lista guardaba la cobertura vacía — de ahí este endpoint.
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Ana", "apellido": "Diaz",
                        "cobertura": {"obras_sociales": [{"nombre": "OSECAC"}],
                                      "prepaga": {"nombre": "Swiss Medical"}}})
        lista = self._req("GET", "/api/admin/patients")
        self.assertNotIn("cobertura", lista.json()["items"][0])
        # …pero sí el NOMBRE de la principal, que es una columna y un filtro
        # de la lista. El objeto entero sigue siendo de la ficha: mandarlo en
        # 200 filas fue exactamente lo que hizo que el formulario guardara la
        # cobertura a medio cargar.
        self.assertEqual(lista.json()["items"][0]["cobertura_nombre"], "Swiss Medical")

        h = self._req("GET", "/api/admin/patients/30111222")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["cobertura"]["prepaga"]["nombre"], "Swiss Medical")
        # y ya viene resuelto cuál manda (el cliente no rehace el ranking)
        self.assertEqual(h.json()["cobertura_principal"]["clave"], "prepaga")
        self.assertEqual(h.json()["dependientes"], [])

    def test_ficha_inexistente_404(self):
        h = self._req("GET", "/api/admin/patients/99999999")
        self.assertEqual(h.status_code, 404)

    def test_la_cobertura_se_edita_y_la_key_ausente_no_la_pisa(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Ana", "apellido": "Diaz",
                        "cobertura": {"obras_sociales": [{"nombre": "OSECAC", "afiliado": "777"}]}})
        # un PUT que ni menciona cobertura la conserva
        self._req("PUT", "/api/admin/patients/30111222", body={"contacto": "2254550000"})
        h = self._req("GET", "/api/admin/patients/30111222")
        self.assertEqual(h.json()["item"]["cobertura"]["obras_sociales"][0]["afiliado"], "777")
        # mandarla vacía SÍ la borra (es un borrado deliberado)
        self._req("PUT", "/api/admin/patients/30111222", body={"cobertura": {"obras_sociales": []}})
        h = self._req("GET", "/api/admin/patients/30111222")
        self.assertIsNone(h.json()["item"]["cobertura"])

    def test_un_chico_puede_estar_a_cargo_de_dos_adultos(self):
        # The product owner: "kids could be linked to one or two parents". Es la misma
        # ficha del chico cargada desde las dos fichas de los adultos.
        for dni, nom in (("30111222", "Ana"), ("30999888", "Beto")):
            self._req("POST", "/api/admin/patients",
                      body={"dni": dni, "nombre": nom, "apellido": "Diaz"})
        chico = {"dni": "55112233", "nombre": "Mateo", "apellido": "Diaz",
                 "nacimiento": "2015-03-14", "sexo": "M", "vinculo": "hijo"}
        for dni in ("30111222", "30999888"):
            h = self._req("POST", "/api/admin/patients/%s/dependents" % dni, body=chico)
            self.assertEqual(h.status_code, 200, h.json())
        h = self._req("GET", "/api/admin/patients/30111222")
        self.assertEqual(len(h.json()["dependientes"]), 1)
        self.assertEqual(sorted(a["dni"] for a in h.json()["dependientes"][0]["adultos"]),
                         ["30111222", "30999888"])
        # y desde la ficha del CHICO se ve de quiénes está a cargo
        h = self._req("GET", "/api/admin/patients/55112233")
        self.assertEqual(h.status_code, 404)   # el chico no tiene cuenta propia

    def test_sacarlo_de_un_adulto_lo_deja_a_cargo_del_otro(self):
        for dni, nom in (("30111222", "Ana"), ("30999888", "Beto")):
            self._req("POST", "/api/admin/patients",
                      body={"dni": dni, "nombre": nom, "apellido": "Diaz"})
        chico = {"dni": "55112233", "nombre": "Mateo", "apellido": "Diaz",
                 "nacimiento": "2015-03-14", "sexo": "M", "vinculo": "hijo"}
        for dni in ("30111222", "30999888"):
            self._req("POST", "/api/admin/patients/%s/dependents" % dni, body=chico)
        h = self._req("DELETE", "/api/admin/patients/30111222/dependents/55112233")
        self.assertEqual(h.status_code, 200)
        # la respuesta dice con quién quedó, para poder decirlo en pantalla
        self.assertEqual([a["dni"] for a in h.json()["adultos"]], ["30999888"])
        self.assertEqual(self._req("GET", "/api/admin/patients/30111222").json()["dependientes"], [])
        self.assertEqual(len(self._req("GET", "/api/admin/patients/30999888").json()["dependientes"]), 1)

    def test_el_mismo_post_corrige_al_chico_en_vez_de_duplicarlo(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Ana", "apellido": "Diaz"})
        base = {"dni": "55112233", "nombre": "Mateo", "apellido": "Diaz",
                "nacimiento": "2015-03-14", "sexo": "M", "vinculo": "hijo"}
        self._req("POST", "/api/admin/patients/30111222/dependents", body=base)
        self._req("POST", "/api/admin/patients/30111222/dependents",
                  body=dict(base, nombre="Mateo Ariel"))
        deps = self._req("GET", "/api/admin/patients/30111222").json()["dependientes"]
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]["nombre"], "Mateo Ariel")

    def test_un_chico_a_cargo_necesita_fecha_de_nacimiento(self):
        # sin fecha no hay puerta de edad posible (age.mjs del lado del paciente)
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Ana", "apellido": "Diaz"})
        h = self._req("POST", "/api/admin/patients/30111222/dependents",
                      body={"dni": "55112233", "nombre": "Mateo", "apellido": "Diaz"})
        self.assertEqual(h.status_code, 400)

    def test_nadie_esta_a_cargo_de_si_mismo(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Ana", "apellido": "Diaz"})
        h = self._req("POST", "/api/admin/patients/30111222/dependents",
                      body={"dni": "30111222", "nombre": "Ana", "apellido": "Diaz",
                            "nacimiento": "1990-01-01"})
        self.assertEqual(h.status_code, 400)

    # ---- ranking de asistencia (patient_ranking.py) ------------------

    def _turno(self, dni, dias_atras, asistencia=None, estado="aprobada",
               servicio="cardiologia", cancelada_en=None):
        """Un turno pasado, escrito derecho en la tabla: lo que importa acá es
        que el endpoint LEA de la base, no cómo se creó la solicitud."""
        import psycopg
        from datetime import datetime, timedelta, timezone
        cuando = datetime.now(timezone.utc) - timedelta(days=dias_atras)
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO booking_requests
                       (dni, first_name, last_name, service_id, center_slug, status,
                        appointment_at, attendance, cancelled_at)
                       VALUES (%s,'X','Y',%s,'hospital-pinamar',%s,%s,%s,%s)""",
                    (dni, servicio, estado, cuando, asistencia, cancelada_en))

    def test_la_lista_trae_el_ranking_de_cada_paciente(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        h = self._req("GET", "/api/admin/patients")
        r = h.json()["items"][0]["ranking"]
        # Sin historia: el promedio del hospital, nunca 0 — un 0 en la columna
        # se leería como "esta falta siempre".
        self.assertTrue(r["sin_historial"])
        self.assertGreater(r["puntaje"], 0)

    def test_la_lista_trae_con_que_filtrar_sin_pedir_la_ficha(self):
        """Cobertura y localidad viajan como texto: son una columna y un
        filtro cada una (responsable del producto, 2026-07-28: "patients have no filters")."""
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez",
                        "cobertura": {"obras_sociales": [{"nombre": "IOMA"}]}})
        db_accounts.user_admin_update("30111222", {})   # no toca nada
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("UPDATE users SET address=%s WHERE dni=%s",
                            ('{"localidad": "Ostende"}', "30111222"))
        it = self._req("GET", "/api/admin/patients").json()["items"][0]
        self.assertEqual(it["cobertura_nombre"], "IOMA")
        self.assertEqual(it["localidad"], "Ostende")
        # el domicilio entero tampoco viaja en la lista
        self.assertNotIn("domicilio", it)

    def test_sin_cobertura_la_lista_lo_dice_con_un_texto_vacio(self):
        # "" y no None: el filtro "Sin cobertura" pregunta por la ausencia, y
        # un null obligaría a cada consumidor a normalizarlo de nuevo.
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        it = self._req("GET", "/api/admin/patients").json()["items"][0]
        self.assertEqual(it["cobertura_nombre"], "")
        self.assertEqual(it["localidad"], "")

    def test_el_ranking_dice_cuando_fue_el_ultimo_turno(self):
        """La columna "Último turno" sale del mismo cálculo, sin otra
        consulta: es lo que dice si una ficha sigue viva."""
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self._turno("30111222", 200, "presente", servicio="svc-viejo")
        self._turno("30111222", 12, "presente", servicio="svc-nuevo")
        # La fecha se saca SIEMPRE en ART, no en el huso con el que la
        # devuelva Postgres: si no, un turno de las 21:00 aparece el día
        # anterior (y este test pasaría o fallaría según la hora del día).
        from datetime import datetime, timedelta, timezone
        art = timezone(timedelta(hours=-3))
        esperado = (datetime.now(timezone.utc) - timedelta(days=12)).astimezone(art).date().isoformat()
        r = self._req("GET", "/api/admin/patients").json()["items"][0]["ranking"]
        self.assertEqual(r["ultimo"], esperado)
        self.assertEqual(r["hechos"], 2)

    def test_sin_historial_no_hay_ultimo_turno(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        r = self._req("GET", "/api/admin/patients").json()["items"][0]["ranking"]
        self.assertIsNone(r["ultimo"])

    # ---- turnos otorgados (Salud, 30.10: «no informa qué turnos se le
    # otorgaron») ---------------------------------------------------------

    def test_la_lista_cuenta_los_turnos_otorgados_tambien_los_que_vienen(self):
        """Antes «Turnos» contaba solo los que dejaron huella de asistencia: el
        paciente con turno mañana figuraba con «—»."""
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self._turno("30111222", -3, servicio="svc-futuro")           # aprobado, por venir
        self._turno("30111222", 10, servicio="svc-pasado")           # aprobado, ya fue
        self._turno("30111222", -5, estado="pendiente", servicio="svc-espera")
        self._turno("30111222", 20, estado="cancelada", servicio="svc-cancelado")
        it = self._req("GET", "/api/admin/patients").json()["items"][0]
        self.assertEqual(it["turnos"]["otorgados"], 2)
        from datetime import datetime, timedelta, timezone
        art = timezone(timedelta(hours=-3))
        esperado = (datetime.now(timezone.utc) + timedelta(days=3)).astimezone(art).date().isoformat()
        self.assertEqual(it["turnos"]["proximo"], esperado)

    def test_sin_turnos_la_lista_dice_cero_y_ningun_proximo(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        it = self._req("GET", "/api/admin/patients").json()["items"][0]
        self.assertEqual(it["turnos"], {"otorgados": 0, "proximo": None})

    # ---- servicio principal (the product owner, 2026-11-04: columna que reemplaza a
    # Cobertura en la lista) -------------------------------------------------

    def test_el_servicio_principal_exige_dos_turnos_y_la_mitad(self):
        f = admin_patients.main_service_id
        self.assertEqual(f([]), "")
        self.assertEqual(f([("cardiologia", 1)]), "", "un solo turno no hace principal")
        self.assertEqual(f([("cardiologia", 2)]), "cardiologia")
        self.assertEqual(f([("cardiologia", 2), ("kinesiologia", 1), ("clinica", 1)]), "cardiologia",
                         "justo la mitad (2 de 4) alcanza")
        self.assertEqual(f([("cardiologia", 2), ("kinesiologia", 1), ("clinica", 1), ("pediatria", 1)]), "",
                         "2 de 5 es menos de la mitad: nadie lidera")
        self.assertEqual(f([("cardiologia", 2), ("kinesiologia", 2)]), "", "un empate no es un líder")

    def test_la_lista_trae_el_servicio_principal_derivado_de_los_turnos(self):
        from app.patient_web import catalog
        for dni, n in (("30111222", "Juana"), ("30111333", "Rosa"), ("30111444", "Sara")):
            self._req("POST", "/api/admin/patients", body={"dni": dni, "nombre": n, "apellido": "Pérez"})
        self._turno("30111222", 3, servicio="cardiologia")
        self._turno("30111222", 9, servicio="cardiologia")
        self._turno("30111222", 20, servicio="kinesiologia")
        self._turno("30111222", 4, servicio="cardiologia", estado="cancelada")  # no cuenta
        self._turno("30111333", 3, servicio="cardiologia")                     # uno solo
        items = {i["dni"]: i for i in self._req("GET", "/api/admin/patients").json()["items"]}
        self.assertEqual(items["30111222"]["servicio"], catalog.service_name("cardiologia") or "cardiologia")
        self.assertEqual(items["30111333"]["servicio"], "", "sin líder claro, la celda queda vacía")
        self.assertEqual(items["30111444"]["servicio"], "", "sin turnos, vacía")

    def test_la_ficha_lista_todos_los_turnos_el_mas_nuevo_primero(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self._turno("30111222", 10, "presente", servicio="svc-pasado")
        self._turno("30111222", -3, servicio="svc-futuro")
        self._turno("30111222", 20, estado="cancelada", servicio="svc-cancelado")
        turnos = self._req("GET", "/api/admin/patients/30111222").json()["turnos"]
        self.assertEqual([t["estado"] for t in turnos], ["aprobada", "aprobada", "cancelada"])
        self.assertEqual(turnos[1]["asistencia"], "presente")
        for t in turnos:
            self.assertRegex(t["dia"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(t["hora"], r"^\d{2}:\d{2}$")
            self.assertFalse(t["pedido"])

    def test_la_ficha_pone_arriba_lo_que_se_puede_atender_el_mas_cercano_primero(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self._turno("30111222", 10, "presente", servicio="svc-pasado")
        self._turno("30111222", -30, servicio="svc-lejos")
        self._turno("30111222", -2, servicio="svc-cerca")
        self._turno("30111222", -9, estado="cancelada", servicio="svc-cancelado")
        self._turno("30111222", 40, estado="pendiente", servicio="svc-pedido")
        servicios = [t["servicio"] for t in self._req("GET", "/api/admin/patients/30111222").json()["turnos"]]
        # The ficha gets the service's display name, not its id.
        self.assertEqual(servicios, ["Svc pedido", "Svc cerca", "Svc lejos", "Svc cancelado", "Svc pasado"],
                         "pendiente y por venir arriba, del más cercano; lo demás, el más nuevo primero")

    def test_un_pedido_sin_horario_muestra_la_fecha_que_se_pidio(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        from datetime import datetime, timedelta, timezone
        import psycopg
        sid = db_booking.create_booking_request(
            "30111222", "Juana", "Pérez", None, None, "cardiologia", "hospital-pinamar")
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("UPDATE booking_requests SET requested_at=%s WHERE id=%s",
                            (datetime.now(timezone.utc) + timedelta(days=4), sid))
        turnos = self._req("GET", "/api/admin/patients/30111222").json()["turnos"]
        self.assertEqual(len(turnos), 1)
        self.assertEqual(turnos[0]["estado"], "pendiente")
        self.assertTrue(turnos[0]["pedido"])
        self.assertIsNotNone(turnos[0]["dia"])

    def test_la_ficha_de_alguien_sin_turnos_trae_la_lista_vacia(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self.assertEqual(self._req("GET", "/api/admin/patients/30111222").json()["turnos"], [])

    def test_un_medico_ve_en_la_ficha_solo_los_turnos_que_le_tocan(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self._turno("30111222", 10, "presente")
        ident = {"usuario": "dr-nadie", "rol": "medico"}
        self.assertEqual(admin_patients.patient_turnos("30111222", ident), [])
        self.assertEqual(len(admin_patients.patient_turnos(
            "30111222", {"usuario": "admin", "rol": "admin"})), 1)

    # ---- cuentas sin nombre (Salud, 30.10: «hay gente registrada sin nombre,
    # es mucha») -----------------------------------------------------------

    def _sin_nombre(self, dni):
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("UPDATE users SET first_name=NULL, last_name='  ' WHERE dni=%s", (dni,))

    def test_recientes_esconde_las_cuentas_sin_nombre_y_sin_pedidos(self):
        for dni, nom, ape in (("30111222", "Juana", "Pérez"), ("30222333", "x", "x"),
                              ("30333444", "y", "y")):
            self._req("POST", "/api/admin/patients", body={"dni": dni, "nombre": nom, "apellido": ape})
        self._sin_nombre("30222333")
        self._sin_nombre("30333444")
        self._turno("30333444", 5)          # ésta pidió un turno: se queda
        r = self._req("GET", "/api/admin/patients").json()
        self.assertEqual(sorted(i["dni"] for i in r["items"]), ["30111222", "30333444"])
        self.assertEqual(r["total"], 2)

    def test_buscando_se_encuentra_la_cuenta_sin_nombre_por_dni(self):
        self._req("POST", "/api/admin/patients", body={"dni": "30222333", "nombre": "x", "apellido": "x"})
        self._sin_nombre("30222333")
        r = self._req("GET", "/api/admin/patients?q=30222333").json()
        self.assertEqual([i["dni"] for i in r["items"]], ["30222333"])

    def test_en_orden_alfabetico_las_cuentas_sin_nombre_van_al_final(self):
        for dni, nom, ape in (("30222333", "x", "x"), ("30111222", "Zoe", "Zárate"),
                              ("30333444", "Ana", "Álvarez")):
            self._req("POST", "/api/admin/patients", body={"dni": dni, "nombre": nom, "apellido": ape})
        self._sin_nombre("30222333")
        items, total = db_accounts.users_admin_list("", recientes=False)
        self.assertEqual([i["dni"] for i in items], ["30333444", "30111222", "30222333"])
        self.assertEqual(total, 3)

    def test_el_orden_alfabetico_no_depende_de_la_sortacion_del_servidor(self):
        # postgres:16-alpine ordena por bytes (sortación C): sin la clave
        # plegada «Álvarez» caía después de «Zárate» y «de la Torre» después
        # de todas las mayúsculas. La clave se ordena aquí COLLATE "C" a propósito.
        import sqlalchemy as sa
        from app.core import db, sqlcore
        nombres = ["Zárate", "de la Torre", "Álvarez", "Pérez", "Ávila", "alonso"]
        v = sa.values(sa.column("n", sa.Text), name="nombres").data([(x,) for x in nombres])
        stmt = sa.select(v.c.n).order_by(db_accounts._clave_alfabetica(v.c.n).collate("C"))
        with db._connect() as conn, conn.cursor() as cur:
            orden = [r["n"] for r in sqlcore.run(cur, stmt).fetchall()]
        self.assertEqual(orden, ["alonso", "Álvarez", "Ávila", "de la Torre", "Pérez", "Zárate"])

    def test_las_ausencias_bajan_el_puntaje_y_dicen_por_que(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        for d in (10, 20, 30):
            self._turno("30111222", d, "ausente", servicio="svc-%d" % d)
        self._turno("30111222", 40, "presente", servicio="svc-40")

        h = self._req("GET", "/api/admin/patients")
        lista = h.json()["items"][0]["ranking"]
        self.assertFalse(lista["sin_historial"])
        self.assertEqual((lista["falto"], lista["vino"]), (3, 1))
        self.assertLess(lista["puntaje"], 60)
        self.assertTrue(any("ausencia" in m for m in lista["motivos"]))

        # La ficha dice lo MISMO que la lista: dos números distintos para el
        # mismo paciente en dos pantallas no los cree nadie.
        h = self._req("GET", "/api/admin/patients/30111222")
        self.assertEqual(h.json()["ranking"]["puntaje"], lista["puntaje"])

    def test_cancelar_deja_sellada_la_hora_y_avisar_a_tiempo_no_castiga(self):
        """`cancelada_en` es lo único que distingue avisar con tres días de
        avisar con veinte minutos."""
        from datetime import datetime, timedelta, timezone
        from app.modules.turnos import booking_requests  # noqa: F401 — asegura el esquema cargado
        futuro = datetime.now(timezone.utc) + timedelta(days=3)
        sid = db_booking.create_booking_request("30111222", "J", "P", None, None,
                                 "cardiologia", "hospital-pinamar")
        db_booking.approve(sid, futuro)
        cancelada = db_booking.cancel(sid)
        self.assertEqual(cancelada["estado"], "cancelada")
        self.assertIsNotNone(cancelada["cancelada_en"])

        from app.modules.turnos import patient_ranking
        r = patient_ranking.by_dni("30111222")
        # Avisó con tres días: no cuenta como que vino, pero tampoco como
        # ausencia. Si castigara igual que faltar, entrenaríamos a no avisar.
        self.assertEqual(r["falto"], 0)

    # ---- permisos -----------------------------------------------------

    def test_admisionista_ve_el_padron_entero(self):
        h = self._req("GET", "/api/admin/patients", headers=_cookie("mesa-1", "admisionista"))
        self.assertEqual(h.status_code, 200)

    # ---- lo que ve un médico buscando (responsable del producto, 2026-08-05) ----------------
    # Desde que el médico puede dar un hueco de su propia agenda, necesita
    # encontrar a quien tiene enfrente. Lo que NO se le abre es buscar por
    # apellido en el padrón entero del hospital: eso sigue siendo del
    # mostrador (admin_patients._scope_medico).

    def _dos_pacientes(self):
        for dni, ape in (("30111222", "Pérez"), ("30111333", "Gómez")):
            self._req("POST", "/api/admin/patients",
                      body={"dni": dni, "nombre": "Juana", "apellido": ape})

    def test_un_medico_encuentra_por_dni_exacto(self):
        self._dos_pacientes()
        h = self._req("GET", "/api/admin/patients?q=30111222",
                      headers=_cookie("dr-1", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111222"])

    def test_el_dni_exacto_de_un_paciente_ajeno_no_trae_su_ranking_ni_sus_turnos(self):
        # Security review 2026-11: the exact DNI of someone else's patient added services, doctors,
        # appointments and the attendance ranking without any relation to that patient.
        from unittest import mock
        from app.modules.turnos import patient_ranking
        self._dos_pacientes()
        marcado = dict(patient_ranking.neutral(), _marca="x")
        with mock.patch.object(patient_ranking, "by_dnis", return_value={"30111222": marcado}), \
             mock.patch.object(db_booking, "granted_counts_by_dnis",
                               return_value={"30111222": {"otorgados": 9, "proximo": None}}):
            h = self._req("GET", "/api/admin/patients?q=30111222", headers=_cookie("dr-1", "medico"))
            mesa = self._req("GET", "/api/admin/patients?q=30111222", headers=_cookie("mesa-1", "admisionista"))
        item = h.json()["items"][0]
        self.assertEqual(item["turnos"]["otorgados"], 0)
        self.assertNotIn("_marca", item["ranking"])
        self.assertEqual(mesa.json()["items"][0]["turnos"]["otorgados"], 9)

    def test_la_lista_de_pacientes_deja_registro_de_acceso_y_no_se_muestra_sin_el(self):
        from unittest import mock
        from app.core import access_log
        self._dos_pacientes()
        access_log._seen.clear()
        with mock.patch.object(db_clinical, "insert_clinical_access_many") as log:
            h = self._req("GET", "/api/admin/patients?q=Juana", headers=_cookie("mesa-1", "admisionista"))
        self.assertEqual(h.status_code, 200, h.text)
        actor, rol, action, rows = log.call_args.args
        self.assertEqual({r[0] for r in rows},
                         {access_log.dni_hash("30111222"), access_log.dni_hash("30111333")})
        access_log._seen.clear()
        with mock.patch.object(db_clinical, "insert_clinical_access_many",
                               side_effect=RuntimeError("down")):
            with self.assertRaises(access_log.AccessLogFailed):
                self._req("GET", "/api/admin/patients?q=Juana", headers=_cookie("mesa-1", "admisionista"))

    def test_el_dni_con_puntos_encuentra_la_ficha_para_la_mesa_y_el_medico(self):
        # La cola del día lo muestra «30.111.222» y así se copia (demo 2026-10-26).
        self._dos_pacientes()
        for headers in (None, _cookie("dr-1", "medico")):
            h = self._req("GET", "/api/admin/patients?q=30.111.222", headers=headers)
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111222"], headers)

    def test_un_medico_no_barre_el_padron_por_apellido(self):
        self._dos_pacientes()
        h = self._req("GET", "/api/admin/patients?q=Juana",
                      headers=_cookie("dr-1", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        # ninguno pasó por su agenda todavía
        self.assertEqual(h.json()["items"], [])

    def test_un_medico_sin_busqueda_no_recibe_la_lista_completa(self):
        self._dos_pacientes()
        h = self._req("GET", "/api/admin/patients", headers=_cookie("dr-1", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["items"], [])

    def test_sin_sesion_401(self):
        h = _CLIENT.request("GET", "/api/admin/patients")
        self.assertEqual(h.status_code, 401)

    # ---- buscar por médico desde el mismo buscador (responsable del producto, 2026-11-02:
    # «no hace falta un filtro de médicos, que lo escriban en la búsqueda») ----

    def _con_medicos(self, *pares):
        from unittest import mock
        from app.admin_panel import doctor_patients
        patcher = mock.patch.object(
            doctor_patients, "_medicos_opciones",
            return_value=[{"id": i, "nombre": n} for i, n in pares])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_la_lista_ya_no_trae_medicos_ni_acepta_el_filtro_por_medico(self):
        self._dos_pacientes()
        db_booking.create_booking_request("30111222", "Juana", "Pérez", None, None,
                                          "cardiologia", "hospital-pinamar", slot_medico_id="med-1")
        for headers in (None, _cookie("mesa-1", "admisionista"), _cookie("dr-1", "medico")):
            h = self._req("GET", "/api/admin/patients", headers=headers)
            self.assertNotIn("medicos", h.json())
        # ?medico= es un parámetro que ya no existe: no recorta nada.
        h = self._req("GET", "/api/admin/patients?medico=med-1")
        self.assertEqual(sorted(it["dni"] for it in h.json()["items"]), ["30111222", "30111333"])

    def test_escribir_el_nombre_del_medico_trae_sus_pacientes(self):
        self._dos_pacientes()
        self._con_medicos(("med-1", "Dra. Carla Ruiz"), ("med-2", "Dr. Pablo Solá"))
        db_booking.create_booking_request("30111222", "Juana", "Pérez", None, None,
                                          "cardiologia", "hospital-pinamar", slot_medico_id="med-1")
        h = self._req("GET", "/api/admin/patients?q=ruiz")
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111222"])
        self.assertEqual(h.json()["total"], 1)
        self.assertEqual(h.json()["modo"], "busqueda")

    def test_buscar_por_medico_incluye_via_asignacion_aprobada(self):
        from datetime import datetime, timedelta, timezone
        from app.modules.turnos import booking_requests  # noqa: F401 — asegura el esquema cargado
        self._dos_pacientes()
        self._con_medicos(("med-2", "Dr. Pablo Solá"))
        futuro = datetime.now(timezone.utc) + timedelta(days=3)
        sid = db_booking.create_booking_request("30111333", "Juana", "Gómez", None, None,
                                                "cardiologia", "hospital-pinamar")
        db_booking.approve(sid, futuro, medico_asignado_id="med-2", resuelta_por="admin")
        h = self._req("GET", "/api/admin/patients?q=sola")
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111333"])

    def test_buscar_por_medico_se_suma_a_las_coincidencias_por_nombre_sin_repetir(self):
        self._dos_pacientes()
        self._con_medicos(("med-1", "Dra. Juana Ruiz"))
        db_booking.create_booking_request("30111222", "Juana", "Pérez", None, None,
                                          "cardiologia", "hospital-pinamar", slot_medico_id="med-1")
        # «juana» es el nombre de las dos pacientes y de la médica: salen las
        # dos, la de la médica una sola vez, por apellido.
        h = self._req("GET", "/api/admin/patients?q=juana")
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111333", "30111222"])

    def test_una_busqueda_de_numeros_es_un_dni_no_un_medico(self):
        self._dos_pacientes()
        self._con_medicos(("med-1", "Dra. Carla Ruiz"))
        db_booking.create_booking_request("30111333", "Juana", "Gómez", None, None,
                                          "cardiologia", "hospital-pinamar", slot_medico_id="med-1")
        h = self._req("GET", "/api/admin/patients?q=30111222")
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111222"])

    def test_medico_no_amplia_su_universo_escribiendo_otro_medico(self):
        # rol medico sigue scoped a lo suyo (_scope_medico): escribir el nombre
        # de otro médico no le abre sus pacientes.
        self._dos_pacientes()
        self._con_medicos(("med-2", "Dr. Pablo Solá"))
        db_booking.create_booking_request("30111333", "Juana", "Gómez", None, None,
                                          "cardiologia", "hospital-pinamar", slot_medico_id="med-2")
        h = self._req("GET", "/api/admin/patients?q=sola", headers=_cookie("dr-1", "medico"))
        self.assertEqual(h.json()["items"], [])

    # ---- columna «Médico» y búsqueda por especialidad (responsable del producto, 2026-11-03:
    # «a qué médico está anotado; el más frecuente, +1, +2») ----

    def _catalogo_medicos(self, *ternas):
        """(id, nombre, especialidad) del catálogo, sin depender del pack."""
        from unittest import mock
        from app.patient_web import catalog
        docs = {i: {"id": i, "nombre": n, "especialidad": e} for i, n, e in ternas}
        for objetivo in (
                mock.patch.object(catalog, "doctors", return_value=list(docs.values())),
                mock.patch.object(catalog, "doctor_doc",
                                  side_effect=lambda mid, **kw: docs.get(mid))):
            objetivo.start()
            self.addCleanup(objetivo.stop)
        self._con_medicos(*[(i, n) for i, n, _ in ternas])

    def test_la_lista_dice_con_que_medicos_tiene_turnos_el_mas_frecuente_primero(self):
        self._dos_pacientes()
        self._catalogo_medicos(("med-1", "Dra. Carla Ruiz", "Cardiología"),
                               ("med-2", "Dr. Pablo Solá", "Clínica médica"))
        for medico in ("med-2", "med-1", "med-2"):
            db_booking.create_booking_request("30111222", "Juana", "Pérez", None, None,
                                              "cardiologia", "hospital-pinamar", slot_medico_id=medico)
        items = {it["dni"]: it for it in self._req("GET", "/api/admin/patients").json()["items"]}
        self.assertEqual([(m["nombre"], m["n"]) for m in items["30111222"]["medicos"]],
                         [("Dr. Pablo Solá", 2), ("Dra. Carla Ruiz", 1)])
        self.assertEqual(items["30111333"]["medicos"], [])

    def test_escribir_la_especialidad_trae_a_los_pacientes_de_esos_medicos(self):
        self._dos_pacientes()
        self._catalogo_medicos(("med-1", "Dra. Carla Ruiz", "Cardiología"),
                               ("med-2", "Dr. Pablo Solá", "Clínica médica"))
        db_booking.create_booking_request("30111222", "Juana", "Pérez", None, None,
                                          "cardiologia", "hospital-pinamar", slot_medico_id="med-1")
        db_booking.create_booking_request("30111333", "Juana", "Gómez", None, None,
                                          "clinica", "hospital-pinamar", slot_medico_id="med-2")
        h = self._req("GET", "/api/admin/patients?q=cardiologia")
        self.assertEqual([it["dni"] for it in h.json()["items"]], ["30111222"])

    def test_la_ficha_marca_que_turno_esta_por_venir(self):
        self._req("POST", "/api/admin/patients",
                  body={"dni": "30111222", "nombre": "Juana", "apellido": "Pérez"})
        self._turno("30111222", 10, "presente", servicio="svc-pasado")
        self._turno("30111222", -3, servicio="svc-futuro")
        turnos = self._req("GET", "/api/admin/patients/30111222").json()["turnos"]
        self.assertEqual([t["futuro"] for t in turnos], [True, False])


if __name__ == "__main__":
    unittest.main()
