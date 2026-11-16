"""test_reports.py — pruebas de reports.py (analítica de gestión, rol admin).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_reports -v

Sigue el patrón de test_admin_users.py: base dedicada
`sana_turnos_test_reportes` (conectándose a la maintenance db `postgres`),
router FastAPI real con `TestClient` y las env vars del dueño fijadas ANTES
de importar nada.

Las agregaciones de reports.py cruzan `solicitudes` (Postgres, timestamps
controlados acá con INSERT directo — db_booking.create_booking_request siempre usa now(),
estos tests necesitan `created`/`resuelta_en`/`turno_dt` exactos) contra el
catálogo VIVO (site_catalog._services()/_week_hours()/_cluster_by_id()/
_centers()) — para no depender de webdata/*.json real (que cambia con el
contenido del hospital), los tests de especialidades/CSV monkeypatchean esas
funciones con un catálogo sintético chico y lo restauran en tearDown.
"""

import os
import sys
import csv
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_operators  # noqa: E402
from app.core import db_analytics  # noqa: E402
from app.core import db_reports  # noqa: E402
from app.core import tz  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import reports    # noqa: E402
from app.admin_panel import reports_router  # noqa: E402
from app.patient_web import site_catalog, site_centers
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(reports_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reportes", seed=lambda: db_operators.operators_list())


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
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE searches RESTART IDENTITY")
            cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE dependents RESTART IDENTITY")


# Traducción col_vieja -> col_nueva para los INSERT directos de este archivo
# (los call sites siguen usando los kwargs viejos sin cambios) — copiada de
# the pre-Alembic migration 001's _COLUMN_RENAMES.
_SOLICITUD_COL_MAP = {
    "nombre": "first_name", "apellido": "last_name", "nacimiento": "birth_date",
    "sexo": "sex", "contacto": "contact", "servicio_id": "service_id",
    "centro_slug": "center_slug",
    "estado": "status", "orden_path": "referral_path", "orden_mime": "referral_mime",
    "motivo_rechazo": "rejection_reason", "turno_dt": "appointment_at",
    "hsi_estado": "hsi_status", "asistencia": "attendance",
    "asistencia_actualizada_en": "attendance_updated_at",
    "asistencia_actualizada_por": "attendance_updated_by", "sobreturno": "overbooking",
    "preferencia_horario": "time_preference", "solicitante_dni": "requester_dni",
    "medico_asignado": "assigned_doctor", "medico_asignado_id": "assigned_doctor_id",
    "recordatorio_dt": "reminder_at", "resuelta_por": "resolved_by",
    "resuelta_en": "resolved_at", "confirmacion": "confirmation",
    "confirmacion_en": "confirmation_at", "confirmacion_canal": "confirmation_channel",
    "cancelada_en": "cancelled_at",
}

_USUARIO_COL_MAP = {
    "nombre": "first_name", "apellido": "last_name", "nacimiento": "birth_date",
    "sexo": "sex", "contacto": "contact", "activo": "active", "cobertura": "coverage",
    "domicilio": "address", "avisos": "notifications",
}

_DEPENDIENTE_COL_MAP = {
    "adulto_dni": "adult_dni", "nombre": "first_name", "apellido": "last_name",
    "nacimiento": "birth_date", "sexo": "sex", "vinculo": "relationship",
    "declaracion_jurada_at": "affidavit_at",
}


def _insert_solicitud(**kw):
    """INSERT directo (no vía db_booking.create_booking_request, que siempre usa now()) para
    fijar `created`/`resuelta_en`/`turno_dt` exactos."""
    import psycopg
    kw = {_SOLICITUD_COL_MAP.get(k, k): v for k, v in kw.items()}
    cols = list(kw.keys())
    vals = [kw[c] for c in cols]
    placeholders = ",".join(["%s"] * len(cols))
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO booking_requests ({','.join(cols)}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            sid = cur.fetchone()[0]
        c.commit()
    return sid


def _insert_usuario(**kw):
    """INSERT directo (no vía db_accounts.create_user, que siempre usa now() para
    `created`) para fijar la fecha de alta exacta y escribir cobertura/
    domicilio en la forma cruda que se quiera probar (incluida la forma
    singular pre-2026-07-16, que db_values._coerce_jsonb/db_accounts.create_user ya no
    producen pero que puede seguir viva en filas viejas)."""
    import psycopg
    kw = dict(kw)
    for campo in ("cobertura", "domicilio"):
        if isinstance(kw.get(campo), (dict, list)):
            kw[campo] = json.dumps(kw[campo], ensure_ascii=False)
    kw = {_USUARIO_COL_MAP.get(k, k): v for k, v in kw.items()}
    cols = list(kw.keys())
    vals = [kw[c] for c in cols]
    placeholders = ",".join("%s::jsonb" if c in ("coverage", "address") else "%s" for c in cols)
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders}) RETURNING dni",
                vals,
            )
            dni = cur.fetchone()[0]
        c.commit()
    return dni


def _insert_dependiente(**kw):
    import psycopg
    kw = {_DEPENDIENTE_COL_MAP.get(k, k): v for k, v in kw.items()}
    cols = list(kw.keys())
    vals = [kw[c] for c in cols]
    placeholders = ",".join(["%s"] * len(cols))
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO dependents ({','.join(cols)}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            did = cur.fetchone()[0]
        c.commit()
    return did


# Relative-period queries use PostgreSQL `now()`: keeping the fixture clock
# live prevents a 7-day test from silently aging out as the suite is rerun.
_NOW = datetime.now(timezone.utc)


def _seed_usuario(dni, dias_atras=1, email=None, nacimiento=None, cobertura=None, domicilio=None,
                   nombre="Test", apellido="Usuario"):
    created = _NOW - timedelta(days=dias_atras)
    return _insert_usuario(
        dni=dni, nombre=nombre, apellido=apellido, nacimiento=nacimiento, sexo="F",
        contacto="wa:11", email=email, pass_hash="x", created=created,
        cobertura=cobertura, domicilio=domicilio,
    )


def _seed(servicio_id, centro_slug="centro-test", estado="pendiente", dias_atras=1,
          resuelta_horas_despues=None, turno_dias_despues=None, motivo=None,
          resuelta_por=None, dni="30111222", nombre="Ana", apellido="Lopez"):
    created = _NOW - timedelta(days=dias_atras)
    resuelta_en = created + timedelta(hours=resuelta_horas_despues) if resuelta_horas_despues is not None else None
    turno_dt = created + timedelta(days=turno_dias_despues) if turno_dias_despues is not None else None
    return _insert_solicitud(
        dni=dni, nombre=nombre, apellido=apellido, nacimiento=date(1990, 1, 1), sexo="F",
        contacto="wa:11", servicio_id=servicio_id, centro_slug=centro_slug,
        estado=estado, created=created, resuelta_en=resuelta_en, turno_dt=turno_dt,
        motivo_rechazo=motivo, resuelta_por=resuelta_por, solicitante_dni=dni,
    )


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


# ---- catálogo sintético para las pruebas de "especialidades" + CSV ---------
# Clusters con sufijo "-test" para no colisionar con clusters reales — así
# site_availability._week_hours() cae en 0 al hacer fallback sobre los médicos
# reales (ninguno pertenece a un cluster "*-test").
_STUB_SERVICIOS = [
    {"id": "svc-cardio", "nombre": "Cardiología test", "top_label": "Cardiología test",
     "cluster": "cardiologia-test", "centro": "centro-test",
     "agenda": {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00", "cada_min": 20}},  # 4h*5d = 20h/sem
    {"id": "svc-diag", "nombre": "Diagnóstico test", "top_label": "Diagnóstico test",
     "cluster": "diagnostico-test", "centro": "centro-test",
     "agenda": {"dias": [1, 2, 3], "desde": "08:00", "hasta": "10:00", "cada_min": 30}},  # 2h*3d = 6h/sem
    {"id": "svc-sinoferta", "nombre": "Sin oferta test", "top_label": "Sin oferta test",
     "cluster": "sinoferta-test", "centro": "centro-test", "agenda": {}},  # 0h/sem
]
_STUB_CLUSTERS = {
    "cardiologia-test": {"id": "cardiologia-test", "nombre": "Cardiología (test)"},
    "diagnostico-test": {"id": "diagnostico-test", "nombre": "Diagnóstico (test)"},
    "sinoferta-test": {"id": "sinoferta-test", "nombre": "Sin oferta (test)"},
}
_STUB_CENTROS = [{"slug": "centro-test", "name": "Centro de Prueba"}]


class _CatalogoStubMixin:
    """Monkeypatchea el catálogo vivo que reports.py consulta vía patient_web/site_*.py.*
    con datos sintéticos deterministas, y lo restaura al terminar."""

    def _stub_catalogo(self):
        self._orig_servicios = site_catalog._services
        self._orig_cluster_by_id = site_catalog._cluster_by_id
        self._orig_centros = site_centers._centers
        site_catalog._services = lambda: _STUB_SERVICIOS
        site_catalog._cluster_by_id = lambda cid: _STUB_CLUSTERS.get(cid)
        site_centers._centers = lambda: _STUB_CENTROS
        self.addCleanup(self._unstub_catalogo)

    def _unstub_catalogo(self):
        site_catalog._services = self._orig_servicios
        site_catalog._cluster_by_id = self._orig_cluster_by_id
        site_centers._centers = self._orig_centros


class ReportesKpisTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_kpis_pendientes_periodo_pct_y_medianas(self):
        # 2 pendientes (no cuentan para % aprobadas ni medianas)
        _seed("svc-x", estado="pendiente", dias_atras=2)
        _seed("svc-x", estado="pendiente", dias_atras=3)
        # 2 aprobadas resueltas en 2h y 4h; turno a 4 y 8 días -> mediana días = 6.0
        _seed("svc-x", estado="aprobada", dias_atras=5, resuelta_horas_despues=2, turno_dias_despues=4,
              resuelta_por="admin")
        _seed("svc-x", estado="aprobada", dias_atras=5, resuelta_horas_despues=4, turno_dias_despues=8,
              resuelta_por="admin")
        # 1 rechazada resuelta en 6h — la mediana de horas-a-resolución mezcla
        # aprobadas Y rechazadas (es "tiempo hasta resolver", no "tiempo hasta
        # aprobar"): [2,4,6] -> mediana 4.0
        _seed("svc-x", estado="rechazada", dias_atras=5, resuelta_horas_despues=6,
              motivo="La orden está vencida.", resuelta_por="admin")

        k = db_reports.reports_kpis(30)
        self.assertEqual(k["pendientes"], 2)
        self.assertEqual(k["solicitudes_periodo"], 5)
        self.assertAlmostEqual(k["pct_aprobadas"], 66.7, places=1)
        self.assertAlmostEqual(k["mediana_horas_resolucion"], 4.0, places=1)
        self.assertAlmostEqual(k["mediana_dias_turno"], 6.0, places=1)

    def test_pendientes_no_depende_del_periodo(self):
        _seed("svc-x", estado="pendiente", dias_atras=100)  # fuera de cualquier período
        k7 = db_reports.reports_kpis(7)
        self.assertEqual(k7["pendientes"], 1)
        self.assertEqual(k7["solicitudes_periodo"], 0)  # la solicitud queda fuera del período

    def test_resueltas_sin_resuelta_en_no_entran_a_la_mediana_de_horas(self):
        # fila "vieja" (previa a la columna resuelta_en): aprobada sin resuelta_en.
        _seed("svc-x", estado="aprobada", dias_atras=2, turno_dias_despues=3, resuelta_por="admin")
        k = db_reports.reports_kpis(30)
        self.assertIsNone(k["mediana_horas_resolucion"])
        self.assertAlmostEqual(k["mediana_dias_turno"], 3.0, places=1)

    def test_db_vacia_estructura_con_ceros(self):
        h = _CLIENT.request("GET", "/api/admin/reports", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["kpis"]["pendientes"], 0)
        self.assertEqual(h.json()["kpis"]["solicitudes_periodo"], 0)
        self.assertIsNone(h.json()["kpis"]["pct_aprobadas"])
        self.assertIsNone(h.json()["kpis"]["mediana_horas_resolucion"])
        self.assertIsNone(h.json()["kpis"]["mediana_dias_turno"])
        self.assertEqual(h.json()["rechazos"], [])
        self.assertEqual(h.json()["volumen"], [])
        self.assertNotIn("busquedas", h.json())  # se mudó a su propio sub-reporte, ver ReportesBusquedasTestCase
        # especialidades: puede traer clusters reales del catálogo vivo con
        # horas>0 pero 0 solicitudes — nunca levanta, es lo importante acá.
        self.assertIsInstance(h.json()["especialidades"], list)


class ReportesEspecialidadesTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()

    def test_ratio_y_orden_sobre_demanda(self):
        for _ in range(10):
            _seed("svc-cardio", dias_atras=3)      # 10 solicitudes / 20h = 0.5
        for _ in range(12):
            _seed("svc-diag", dias_atras=3)        # 12 solicitudes / 6h = 2.0
        for _ in range(3):
            _seed("svc-sinoferta", dias_atras=3)   # 3 solicitudes / 0h = sin oferta

        out = reports._specialties(30)
        by_cluster = {r["cluster"]: r for r in out}

        self.assertAlmostEqual(by_cluster["cardiologia-test"]["horas_semana"], 20.0, places=1)
        self.assertAlmostEqual(by_cluster["cardiologia-test"]["ratio"], 0.5, places=2)
        self.assertFalse(by_cluster["cardiologia-test"]["sin_oferta"])

        self.assertAlmostEqual(by_cluster["diagnostico-test"]["horas_semana"], 6.0, places=1)
        self.assertAlmostEqual(by_cluster["diagnostico-test"]["ratio"], 2.0, places=2)

        self.assertTrue(by_cluster["sinoferta-test"]["sin_oferta"])
        self.assertIsNone(by_cluster["sinoferta-test"]["ratio"])
        self.assertEqual(by_cluster["sinoferta-test"]["solicitudes"], 3)

        # orden: sin oferta primero (demanda sin ninguna oferta registrada),
        # después por ratio desc.
        clusters_en_orden = [r["cluster"] for r in out if r["cluster"] in by_cluster]
        idx = {c: i for i, c in enumerate(clusters_en_orden)}
        self.assertLess(idx["sinoferta-test"], idx["diagnostico-test"])
        self.assertLess(idx["diagnostico-test"], idx["cardiologia-test"])

        self.assertEqual(by_cluster["cardiologia-test"]["nombre"], "Cardiología (test)")

    def test_espera_mediana_por_especialidad(self):
        _seed("svc-diag", estado="aprobada", dias_atras=5, turno_dias_despues=2, resuelta_por="admin")
        _seed("svc-diag", estado="aprobada", dias_atras=5, turno_dias_despues=4, resuelta_por="admin")
        _seed("svc-cardio", estado="aprobada", dias_atras=5, turno_dias_despues=10, resuelta_por="admin")
        # una aprobada SIN turno_dt no debe contarse
        _seed("svc-cardio", estado="aprobada", dias_atras=5, resuelta_por="admin")
        # sin oferta, pero CON demanda (pendiente, sin turno todavía) — aparece
        # en el reporte con sin_oferta=True y espera_mediana_dias=None.
        _seed("svc-sinoferta", estado="pendiente", dias_atras=1)

        out = reports._specialties(30)
        by_cluster = {r["cluster"]: r for r in out}
        self.assertAlmostEqual(by_cluster["diagnostico-test"]["espera_mediana_dias"], 3.0, places=1)
        self.assertAlmostEqual(by_cluster["cardiologia-test"]["espera_mediana_dias"], 10.0, places=1)
        self.assertIsNone(by_cluster["sinoferta-test"]["espera_mediana_dias"])

    def test_endpoint_devuelve_especialidades(self):
        _seed("svc-cardio", dias_atras=2)
        h = _CLIENT.request("GET", "/api/admin/reports?days=30", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        clusters = [r["cluster"] for r in h.json()["especialidades"]]
        self.assertIn("cardiologia-test", clusters)


class ReportesRechazosVolumenTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_rechazos_agrupa_motivo_libre_como_otro(self):
        _seed("svc-x", estado="rechazada", dias_atras=2, motivo="La orden está vencida.", resuelta_por="admin")
        _seed("svc-x", estado="rechazada", dias_atras=2, motivo="La orden está vencida.", resuelta_por="admin")
        _seed("svc-x", estado="rechazada", dias_atras=2, motivo="no tiene turno para esa fecha", resuelta_por="admin")

        out = reports._rejections(30)
        by_motivo = {r["motivo"]: r["cantidad"] for r in out}
        self.assertEqual(by_motivo["La orden está vencida."], 2)
        self.assertEqual(by_motivo["Otro motivo"], 1)
        self.assertEqual(out[0]["motivo"], "La orden está vencida.")  # ordenado desc

    def test_volumen_por_dia(self):
        _seed("svc-x", estado="aprobada", dias_atras=0, resuelta_por="admin")
        _seed("svc-x", estado="rechazada", dias_atras=0, resuelta_por="admin")
        _seed("svc-x", estado="pendiente", dias_atras=1)

        out = db_reports.reports_volume(30)
        self.assertEqual(len(out), 2)
        by_total = sum(r["total"] for r in out)
        self.assertEqual(by_total, 3)
        hoy = [r for r in out if r["aprobadas"] == 1][0]
        self.assertEqual(hoy["total"], 2)
        self.assertEqual(hoy["rechazadas"], 1)
        ayer = [r for r in out if r["pendientes"] == 1][0]
        self.assertEqual(ayer["total"], 1)


class ReportesBusquedasTestCase(unittest.TestCase):
    """Sub-reporte "Búsquedas" (2026-07-22): el endpoint sólo sirve el log
    crudo (q/veces/con_resultados) — el clustering por intención corre
    client-side en admin-reports.js, ver ese archivo."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_top_agrupado_y_ordenado(self):
        db_analytics.log_search("cardiologia", 5, origen="home")
        db_analytics.log_search("cardiologia", 3, origen="home")
        db_analytics.log_search("cardiologia", 2, origen="home")
        db_analytics.log_search("pediatra fantasma", 0, origen="home")
        db_analytics.log_search("pediatra fantasma", 0, origen="home")

        out = reports._searches_payload(30)
        by_q = {r["q"]: r for r in out}
        self.assertEqual(by_q["cardiologia"]["veces"], 3)
        self.assertEqual(by_q["cardiologia"]["con_resultados"], 3)
        self.assertEqual(by_q["pediatra fantasma"]["veces"], 2)
        self.assertEqual(by_q["pediatra fantasma"]["con_resultados"], 0)
        # ordenado por veces desc
        self.assertEqual(out[0]["q"], "cardiologia")

    def test_endpoint_requiere_admin_y_respeta_periodo(self):
        db_operators.operator_create("op-busq", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/reports/searches", headers=_cookie("op-busq", "admisionista"))
        self.assertEqual(h.status_code, 403)

        db_analytics.log_search("cardiologia", 1, origen="home")
        h = _CLIENT.request("GET", "/api/admin/reports/searches?days=7", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["dias"], 7)
        self.assertEqual([r["q"] for r in h.json()["top"]], ["cardiologia"])

    def test_endpoint_sobre_base_vacia(self):
        h = _CLIENT.request("GET", "/api/admin/reports/searches", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["top"], [])


class ReportesPermisosTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_operador_403_admin_200(self):
        db_operators.operator_create("op-reportes", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/reports", headers=_cookie("op-reportes", "admisionista"))
        self.assertEqual(h.status_code, 403)

        h = _CLIENT.request("GET", "/api/admin/reports", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)

    def test_operador_admin_si_entra(self):
        db_operators.operator_create("op-reportes2", "clave1234", "admin")
        h = _CLIENT.request("GET", "/api/admin/reports", headers=_cookie("op-reportes2", "admin"))
        self.assertEqual(h.status_code, 200)

    def test_sin_sesion_401(self):
        h = _CLIENT.request("GET", "/api/admin/reports")
        self.assertEqual(h.status_code, 401)

    def test_dias_invalido_cae_al_default(self):
        h = _CLIENT.request("GET", "/api/admin/reports?days=15", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["dias"], 30)

    def test_csv_requiere_admin(self):
        db_operators.operator_create("op-csv", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/reports/requests.csv", headers=_cookie("op-csv", "admisionista"))
        self.assertEqual(h.status_code, 403)


class ReportesCsvTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()

    def test_csv_sin_pii_de_paciente(self):
        _seed("svc-cardio", estado="aprobada", dias_atras=2, resuelta_horas_despues=1,
              turno_dias_despues=3, resuelta_por="admin", dni="40222333",
              nombre="Marta", apellido="Diaz")

        h = _CLIENT.request("GET", "/api/admin/reports/requests.csv", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIn("attachment", h.headers.get("Content-Disposition", ""))
        self.assertEqual(h.headers.get("Content-Type"), "text/csv; charset=utf-8")

        body = h.content.decode("utf-8-sig")
        self.assertNotIn("Marta", body)
        self.assertNotIn("Diaz", body)
        self.assertNotIn("40222333", body)
        self.assertIn("M.D.", body)  # iniciales

        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0][:6], ["fecha", "paciente", "servicio", "especialidad", "centro", "estado"])
        data_row = rows[1]
        self.assertEqual(data_row[1], "M.D.")
        self.assertEqual(data_row[2], "Cardiología test")
        self.assertEqual(data_row[3], "Cardiología (test)")
        self.assertEqual(data_row[4], "Centro de Prueba")
        self.assertEqual(data_row[5], "aprobada")

    def test_csv_una_celda_que_parece_formula_sale_con_apostrofe(self):
        # Security review 2026-11: a name typed as a formula ran in the reader's spreadsheet.
        body = reports._csv_bytes(["=cmd", "ok"], [['=HYPERLINK("http://x","a")', "+1+1"],
                                                   ["@SUM(A1)", "-5"], ["Marta", "12"]]).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0], ["'=cmd", "ok"])
        self.assertEqual(rows[1], ["'=HYPERLINK(\"http://x\",\"a\")", "'+1+1"])
        self.assertEqual(rows[2], ["'@SUM(A1)", "-5"])
        self.assertEqual(rows[3], ["Marta", "12"])


# ============================================================================
# GET /api/admin/reports/patients — sub-reporte "Pacientes"
# ============================================================================

class ReportesPacientesPermisosTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_sin_sesion_401(self):
        h = _CLIENT.request("GET", "/api/admin/reports/patients")
        self.assertEqual(h.status_code, 401)

    def test_operador_403_admin_200(self):
        db_operators.operator_create("op-pac", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_cookie("op-pac", "admisionista"))
        self.assertEqual(h.status_code, 403)

        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)

    def test_dias_invalido_cae_al_default(self):
        h = _CLIENT.request("GET", "/api/admin/reports/patients?days=15", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["dias"], 30)


class ReportesPacientesVacioTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_db_vacia_estructura_con_ceros(self):
        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        j = h.json()
        self.assertEqual(j["kpis"]["total_cuentas"], 0)
        self.assertEqual(j["kpis"]["nuevas_periodo"], 0)
        self.assertIsNone(j["kpis"]["pct_con_email"])
        self.assertIsNone(j["kpis"]["pct_con_domicilio"])
        self.assertIsNone(j["cobertura"]["pct_con_obra_social"])
        self.assertIsNone(j["cobertura"]["pct_con_prepaga"])
        self.assertEqual(j["cobertura"]["top_obras_sociales"], [])
        self.assertEqual(j["volumen"], [])
        self.assertEqual(len(j["actividad_mensual"]), 12)
        self.assertTrue(all(r["pacientes"] == 0 for r in j["actividad_mensual"]))
        self.assertEqual(j["dependientes"], {"total": 0, "adultos_con_dependientes": 0})
        self.assertEqual(j["actividad"], {
            "pacientes_con_solicitud": 0, "solicitudes_totales": 0, "promedio_por_paciente": None,
        })
        # buckets etarios fijos, siempre los seis, en cero.
        self.assertEqual(len(j["edades"]), 6)
        self.assertTrue(all(r["cantidad"] == 0 for r in j["edades"]))


class ReportesPacientesKpisTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_total_nuevas_email_domicilio(self):
        _seed_usuario("30111111", dias_atras=1, email="a@x.com", domicilio={"texto": "Calle 1"})
        _seed_usuario("30222222", dias_atras=2, email=None, domicilio=None)
        _seed_usuario("30333333", dias_atras=100, email="c@x.com", domicilio={"texto": "Calle 3"})  # fuera del período de 30 días

        h = _CLIENT.request("GET", "/api/admin/reports/patients?days=30", headers=_admin_headers())
        k = h.json()["kpis"]
        self.assertEqual(k["total_cuentas"], 3)
        self.assertEqual(k["nuevas_periodo"], 2)  # la de hace 100 días queda afuera
        self.assertAlmostEqual(k["pct_con_email"], 66.7, places=1)
        self.assertAlmostEqual(k["pct_con_domicilio"], 66.7, places=1)

    def test_volumen_agrupa_por_dia(self):
        _seed_usuario("30111111", dias_atras=1)
        _seed_usuario("30222222", dias_atras=1)
        _seed_usuario("30333333", dias_atras=2)

        out = db_reports.reports_patients_volume(30)
        self.assertEqual(len(out), 2)
        by_total = sum(r["total"] for r in out)
        self.assertEqual(by_total, 3)
        self.assertEqual(max(r["total"] for r in out), 2)

    def test_actividad_mensual_cuenta_pacientes_distintos(self):
        _seed("svc-cardio", dni="30111111", dias_atras=1)
        _seed("svc-cardio", dni="30111111", dias_atras=2)
        _seed("svc-cardio", dni="30222222", dias_atras=2)
        out = db_reports.reports_patients_active_months(12)
        self.assertEqual(len(out), 12)
        # El mes de hace 2 días SIEMPRE tiene a los dos pacientes (aunque
        # hoy sea día 1 o 2 y "hace 1 día" caiga en otro mes): out[-1] a
        # secas se pudría el primer día de cada mes.
        # The query groups in Buenos Aires time. CI runs on UTC, where the
        # calendar can already be tomorrow during the Argentine evening; using
        # date.today() then selects the wrong month at that boundary.
        mes_seed = (_NOW.astimezone(tz.tzinfo()).date() - timedelta(days=2)).replace(day=1).isoformat()
        fila = next(r for r in out if str(r["mes"]) == mes_seed)
        self.assertEqual(fila["pacientes"], 2)


class ReportesPacientesCoberturaTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_tolera_forma_nueva_y_vieja(self):
        # forma nueva (2026-07-16+): obras_sociales lista + prepaga dict.
        _seed_usuario("30111111", cobertura={
            "obras_sociales": [{"nombre": "IOMA", "origen": "puco"}, {"nombre": "OSDE", "origen": "declarada"}],
            "prepaga": {"nombre": "Swiss Medical"},
        })
        # forma vieja (pre-2026-07-16): obra_social singular + prepaga string —
        # nunca se migró en el lugar, sigue viva tal cual en filas de esa época.
        _seed_usuario("30222222", cobertura={"obra_social": {"nombre": "IOMA"}, "prepaga": "OSDE 210"})
        # sin cobertura
        _seed_usuario("30333333", cobertura=None)

        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_admin_headers())
        c = h.json()["cobertura"]
        self.assertAlmostEqual(c["pct_con_obra_social"], 66.7, places=1)  # 2 de 3
        self.assertAlmostEqual(c["pct_con_prepaga"], 66.7, places=1)      # 2 de 3
        top = {r["nombre"]: r["cantidad"] for r in c["top_obras_sociales"]}
        self.assertEqual(top.get("IOMA"), 2)  # una de cada forma
        self.assertEqual(top.get("OSDE"), 1)

    def test_top_obras_sociales_ordenado_desc(self):
        for i in range(3):
            _seed_usuario(f"301111{i}", cobertura={"obras_sociales": [{"nombre": "IOMA"}]})
        for i in range(1):
            _seed_usuario(f"302222{i}", cobertura={"obras_sociales": [{"nombre": "PAMI"}]})

        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_admin_headers())
        top = h.json()["cobertura"]["top_obras_sociales"]
        self.assertEqual(top[0]["nombre"], "IOMA")
        self.assertEqual(top[0]["cantidad"], 3)


class ReportesPacientesEdadesTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_distribucion_por_bucket(self):
        hoy = datetime.now(tz.tzinfo()).date()

        def hace(anios):
            return date(hoy.year - anios, hoy.month, hoy.day if hoy.day <= 28 else 28)

        _seed_usuario("30111111", nacimiento=hace(5))    # 0–17
        _seed_usuario("30222222", nacimiento=hace(25))   # 18–29
        _seed_usuario("30333333", nacimiento=hace(80))   # 75+
        _seed_usuario("30444444", nacimiento=None)       # sin dato, no cuenta

        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_admin_headers())
        by_rango = {r["rango"]: r["cantidad"] for r in h.json()["edades"]}
        self.assertEqual(by_rango["0–17"], 1)
        self.assertEqual(by_rango["18–29"], 1)
        self.assertEqual(by_rango["75+"], 1)
        self.assertEqual(sum(by_rango.values()), 3)  # el sin nacimiento no entra a ningún bucket


class ReportesPacientesDependientesTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_total_y_adultos_con_dependientes(self):
        _seed_usuario("30111111")
        _insert_dependiente(adulto_dni="30111111", dni="40111111", nombre="Hijo", apellido="Uno",
                             nacimiento=date(2018, 1, 1), sexo="M", vinculo="hijo/a")
        _insert_dependiente(adulto_dni="30111111", dni="40222222", nombre="Hijo", apellido="Dos",
                             nacimiento=date(2020, 1, 1), sexo="F", vinculo="hijo/a")

        h = _CLIENT.request("GET", "/api/admin/reports/patients", headers=_admin_headers())
        dep = h.json()["dependientes"]
        self.assertEqual(dep["total"], 2)
        self.assertEqual(dep["adultos_con_dependientes"], 1)


class ReportesPacientesActividadTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_promedio_por_paciente(self):
        # paciente A pide 2 turnos, paciente B pide 1, en el período.
        _seed("svc-x", dias_atras=1, dni="30111111")
        _seed("svc-x", dias_atras=2, dni="30111111")
        _seed("svc-x", dias_atras=1, dni="30222222")
        # fuera del período de 7 días — no debe contar.
        _seed("svc-x", dias_atras=100, dni="30333333")

        h = _CLIENT.request("GET", "/api/admin/reports/patients?days=7", headers=_admin_headers())
        act = h.json()["actividad"]
        self.assertEqual(act["pacientes_con_solicitud"], 2)
        self.assertEqual(act["solicitudes_totales"], 3)
        self.assertAlmostEqual(act["promedio_por_paciente"], 1.5, places=1)


class ReportesPacientesSinPiiTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_respuesta_nunca_trae_nombre_ni_dni(self):
        _seed_usuario("30999888", email="marta.diaz@example.com", nombre="Marta", apellido="Diaz",
                      nacimiento=date(1970, 5, 4), domicilio={"texto": "Av. Siempreviva 742"},
                      cobertura={"obras_sociales": [{"nombre": "IOMA"}]})
        _seed("svc-x", dni="30999888", dias_atras=1)

        h = _CLIENT.request("GET", "/api/admin/reports/patients?days=30", headers=_admin_headers())
        body = json.dumps(h.json(), ensure_ascii=False)
        self.assertNotIn("Marta", body)
        self.assertNotIn("Diaz", body)
        self.assertNotIn("30999888", body)
        self.assertNotIn("marta.diaz@example.com", body)
        self.assertNotIn("Av. Siempreviva 742", body)


if __name__ == "__main__":
    unittest.main()
