"""test_reports_nuevos.py — pruebas de los cuatro sub-reportes nuevos de
reports.py (Phase 2b, 2026-07-24: recupero de costos, SUMAR+, estadística
estilo C2, capacidad estacional).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_reports_nuevos -v

Sigue el patrón de test_reports.py: base dedicada
`sana_turnos_test_reportes_nuevos` (conectándose a la maintenance db
`postgres`), router FastAPI real con `TestClient`, INSERT directo para controlar `created`/
`turno_dt`/`nacimiento` exactos, y un `_CatalogoStubMixin` propio (clusters
"-test" salvo "gineco"/"pediatria", que son los ids REALES del catálogo —
las trazadoras de SUMAR+ los usan tal cual, así que el test tiene que
ejercitar esos ids de verdad, no un doble).
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
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import reports, reports_router  # noqa: E402
from app.patient_web import site_catalog, site_centers
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(reports_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reportes_nuevos", seed=lambda: db_operators.operators_list())

_ART = timezone(timedelta(hours=-3))


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


def _insert_solicitud(**kw):
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


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _csv_text(h):
    """Decode the native CSV response body and report its UTF-8 BOM."""
    raw = h.content
    tiene_bom = raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig"), tiene_bom


# ---- catálogo sintético ------------------------------------------------
#
# "gineco"/"pediatria" son los ids REALES del catálogo (webdata/clusters.json)
# — SUMAR+ los usa tal cual (reports._CLUSTER_PREGNANCY/_CLUSTER_NINO_SANO),
# así que el test tiene que ejercitar esos ids de verdad. El resto usa
# sufijo "-test" para no interferir con nada real.
_STUB_SERVICIOS = [
    {"id": "svc-cardio", "nombre": "Cardiología test", "cluster": "cardiologia-test", "centro": "centro-test",
     "agenda": {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00", "cada_min": 20}},  # 20h/sem, 12 slots/día
    {"id": "svc-preg", "nombre": "Control de embarazo", "cluster": "gineco", "centro": "centro-test",
     "agenda": {}},
    {"id": "svc-pedia", "nombre": "Control pediátrico", "cluster": "pediatria", "centro": "centro-test",
     "agenda": {}},
    {"id": "svc-lab", "nombre": "Laboratorio test", "cluster": "diagnostico-test", "centro": "centro-test",
     "agenda": {}},
    {"id": "svc-sinoferta", "nombre": "Sin oferta test", "cluster": "sinoferta-test", "centro": "centro-test",
     "agenda": {}},
]
_STUB_CLUSTERS = {
    "cardiologia-test": {"id": "cardiologia-test", "nombre": "Cardiología (test)"},
    "gineco": {"id": "gineco", "nombre": "Ginecología y obstetricia"},
    "pediatria": {"id": "pediatria", "nombre": "Pediatría"},
    "diagnostico-test": {"id": "diagnostico-test", "nombre": "Diagnóstico (test)"},
    "sinoferta-test": {"id": "sinoferta-test", "nombre": "Sin oferta (test)"},
}
_STUB_CENTROS = [{"slug": "centro-test", "name": "Centro de Prueba"}]
_STUB_MEDICOS = []  # svc-cardio tiene agenda propia; no hace falta fallback de médicos para estos tests.


class _CatalogoStubMixin:
    def _stub_catalogo(self):
        self._orig_servicios = site_catalog._services
        self._orig_cluster_by_id = site_catalog._cluster_by_id
        self._orig_centros = site_centers._centers
        self._orig_medicos = site_catalog._doctors
        site_catalog._services = lambda: _STUB_SERVICIOS
        site_catalog._cluster_by_id = lambda cid: _STUB_CLUSTERS.get(cid)
        site_centers._centers = lambda: _STUB_CENTROS
        site_catalog._doctors = lambda: _STUB_MEDICOS
        self.addCleanup(self._unstub_catalogo)

    def _unstub_catalogo(self):
        site_catalog._services = self._orig_servicios
        site_catalog._cluster_by_id = self._orig_cluster_by_id
        site_centers._centers = self._orig_centros
        site_catalog._doctors = self._orig_medicos


# Relative-period queries use PostgreSQL `now()`: keeping the fixture clock
# live prevents a "días atrás" test from silently aging out as the suite is
# rerun.
_NOW = datetime.now(timezone.utc)


def _seed(servicio_id, estado="aprobada", dni="30111222", nombre="Ana", apellido="Lopez",
          centro_slug="centro-test", turno_dias_atras=None, asistencia=None, nacimiento=None,
          sexo="F", solicitante_dni=None, created_dias_atras=5):
    """Solicitud aprobada con turno_dt en el PASADO (turno_dias_atras días
    atrás de _NOW) — el universo de recupero/SUMAR+/estadística es
    "consultas ya realizadas", no "solicitudes creadas"."""
    created = _NOW - timedelta(days=created_dias_atras)
    turno_dt = (_NOW - timedelta(days=turno_dias_atras)) if turno_dias_atras is not None else None
    return _insert_solicitud(
        dni=dni, nombre=nombre, apellido=apellido, nacimiento=nacimiento, sexo=sexo,
        contacto="wa:11", servicio_id=servicio_id, centro_slug=centro_slug,
        estado=estado, created=created, turno_dt=turno_dt, asistencia=asistencia,
        solicitante_dni=solicitante_dni or dni,
    )


def _seed_usuario(dni, cobertura=None):
    return _insert_usuario(
        dni=dni, nombre="Cuenta", apellido="Test", nacimiento=date(1990, 1, 1), sexo="F",
        contacto="wa:11", pass_hash="x", created=_NOW - timedelta(days=30), cobertura=cobertura,
    )


# =========================================================================
# Auth (401/403) — las cuatro rutas nuevas + sus CSV hermanos
# =========================================================================

class AuthTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    _RUTAS = (
        "/api/admin/reports/cost-recovery",
        "/api/admin/reports/cost-recovery.csv",
        "/api/admin/reports/sumar",
        "/api/admin/reports/sumar.csv",
        "/api/admin/reports/statistics",
        "/api/admin/reports/statistics.csv",
        "/api/admin/reports/capacity",
    )

    def test_401_sin_sesion(self):
        for ruta in self._RUTAS:
            with self.subTest(ruta=ruta):
                h = _CLIENT.request("GET", ruta)
                self.assertEqual(h.status_code, 401)

    def test_403_operador(self):
        db_operators.operator_create("op-nuevos", "clave1234", "admisionista")
        for ruta in self._RUTAS:
            with self.subTest(ruta=ruta):
                h = _CLIENT.request("GET", ruta, headers=_cookie("op-nuevos", "admisionista"))
                self.assertEqual(h.status_code, 403)

    def test_200_admin(self):
        for ruta in self._RUTAS:
            with self.subTest(ruta=ruta):
                h = _CLIENT.request("GET", ruta, headers=_admin_headers())
                self.assertEqual(h.status_code, 200)


# =========================================================================
# Base vacía — todas las claves presentes, nunca 500
# =========================================================================

class DbVaciaTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()
        _env.set_env(self, TURNOS_SEASON_FACTOR=None)

    def test_recupero_vacio(self):
        h = _CLIENT.request("GET", "/api/admin/reports/cost-recovery", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        j = h.json()
        self.assertEqual(j["kpis"]["consultas_totales"], 0)
        self.assertIsNone(j["kpis"]["pct_del_total"])
        self.assertEqual(j["por_obra_social"], [])
        self.assertEqual(j["tendencia"], [])
        self.assertEqual(j["sin_dato"], 0)
        self.assertIsInstance(j["hero"], list)
        self.assertGreaterEqual(len(j["hero"]), 1)

    def test_sumar_vacio(self):
        h = _CLIENT.request("GET", "/api/admin/reports/sumar", headers=_admin_headers())
        j = h.json()
        self.assertEqual(j["kpis"]["prestaciones"], 0)
        self.assertEqual(j["kpis"]["pacientes"], 0)
        self.assertIsNone(j["kpis"]["pct_del_total"])
        self.assertEqual([t["clave"] for t in j["trazadoras"]], ["embarazo", "nino_sano", "otros"])
        self.assertTrue(all(t["consultas"] == 0 for t in j["trazadoras"]))
        self.assertEqual(j["tendencia"], [])

    def test_estadistica_vacio(self):
        h = _CLIENT.request("GET", "/api/admin/reports/statistics?mes=2026-02", headers=_admin_headers())
        j = h.json()
        self.assertEqual(j["mes"], "2026-02")
        self.assertEqual(j["kpis"]["consultas_totales"], 0)
        self.assertIsNone(j["kpis"]["pct_asistencia"])
        self.assertEqual(j["por_especialidad"], [])
        self.assertEqual(j["por_centro"], [])
        self.assertEqual(len(j["edades"]), 6)  # _EDAD_BUCKETS

    def test_capacidad_vacio(self):
        h = _CLIENT.request("GET", "/api/admin/reports/capacity", headers=_admin_headers())
        j = h.json()
        self.assertEqual(j["sin_oferta"], [])
        self.assertEqual(j["demanda_semanal"], [])
        self.assertEqual(j["temporada"], {"disponible": False})
        self.assertIsInstance(j["ocupacion"], list)

    def test_csv_vacios_tienen_bom_y_headers(self):
        for ruta, header in (
            ("/api/admin/reports/cost-recovery.csv",
             "fecha,dni,paciente,obra_social,nro_afiliado,servicio,especialidad,centro,turno,asistencia"),
            ("/api/admin/reports/sumar.csv",
             "fecha,dni,paciente,servicio,especialidad,centro,turno,asistencia,trazadora"),
            ("/api/admin/reports/statistics.csv?mes=2026-02",
             "especialidad,consultas,presentes,ausentes,sin_marca,horas_ofrecidas,consultas_por_hora"),
        ):
            with self.subTest(ruta=ruta):
                h = _CLIENT.request("GET", ruta, headers=_admin_headers())
                texto, bom = _csv_text(h)
                self.assertTrue(bom)
                self.assertEqual(texto.splitlines()[0], header)


# =========================================================================
# Recupero de costos
# =========================================================================

class RecuperoTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()

    def test_por_obra_social_incluye_forma_nueva_y_legacy_singular(self):
        # Cuenta con la forma NUEVA (lista de obras_sociales).
        _seed_usuario("30000001", cobertura={"obras_sociales": [{"nombre": "IOMA", "origen": "puco", "afiliado": "1"}]})
        _seed("svc-cardio", dni="30000001", turno_dias_atras=3, asistencia="presente")

        # Cuenta con la forma LEGACY singular (dict).
        _seed_usuario("30000002", cobertura={"obra_social": {"nombre": "OSDE", "afiliado": "62-000"}})
        _seed("svc-cardio", dni="30000002", turno_dias_atras=2, asistencia="ausente")

        # Cuenta con la forma LEGACY singular (string plano, sin dict).
        _seed_usuario("30000003", cobertura={"obra_social": "PAMI"})
        _seed("svc-lab", dni="30000003", turno_dias_atras=1)

        # Un segundo paciente con IOMA -- para el conteo de "pacientes".
        _seed_usuario("30000004", cobertura={"obras_sociales": [{"nombre": "IOMA"}]})
        _seed("svc-cardio", dni="30000004", turno_dias_atras=1, asistencia="presente")

        # Sin cobertura -- no debe aparecer en por_obra_social.
        _seed("svc-cardio", dni="30000005", turno_dias_atras=1)

        h = _CLIENT.request("GET", "/api/admin/reports/cost-recovery?days=90", headers=_admin_headers())
        j = h.json()
        by_nombre = {r["nombre"]: r for r in j["por_obra_social"]}

        self.assertEqual(by_nombre["IOMA"]["consultas"], 2)
        self.assertEqual(by_nombre["IOMA"]["pacientes"], 2)
        self.assertEqual(by_nombre["IOMA"]["presentes"], 2)

        self.assertEqual(by_nombre["OSDE"]["consultas"], 1)
        self.assertEqual(by_nombre["OSDE"]["presentes"], 0)

        self.assertEqual(by_nombre["PAMI"]["consultas"], 1)

        self.assertEqual(j["kpis"]["consultas_totales"], 5)
        self.assertEqual(j["kpis"]["consultas_con_cobertura"], 4)
        self.assertEqual(j["sin_dato"], 1)
        self.assertAlmostEqual(j["kpis"]["pct_del_total"], 80.0, places=1)

    def test_solo_futuro_o_no_aprobada_no_cuenta(self):
        _seed_usuario("30000010", cobertura={"obras_sociales": [{"nombre": "IOMA"}]})
        # turno en el FUTURO: no es una consulta "ya realizada" -- no debe entrar.
        _insert_solicitud(
            dni="30000010", nombre="A", apellido="B", contacto="wa:1", servicio_id="svc-cardio",
            centro_slug="centro-test", estado="aprobada", created=_NOW - timedelta(days=1),
            turno_dt=_NOW + timedelta(days=5), solicitante_dni="30000010",
        )
        # pendiente con turno pasado (no debería existir en la práctica, pero si existiera no cuenta):
        _seed("svc-cardio", dni="30000010", estado="pendiente", turno_dias_atras=None)

        h = _CLIENT.request("GET", "/api/admin/reports/cost-recovery", headers=_admin_headers())
        self.assertEqual(h.json()["kpis"]["consultas_totales"], 0)

    def test_csv_contiene_dni_y_datos_de_facturacion(self):
        _seed_usuario("30222333", cobertura={"obras_sociales": [{"nombre": "IOMA", "afiliado": "555-1"}]})
        _seed("svc-cardio", dni="30222333", nombre="Marta", apellido="Ruiz", turno_dias_atras=2,
              asistencia="presente")

        h = _CLIENT.request("GET", "/api/admin/reports/cost-recovery.csv", headers=_admin_headers())
        texto, bom = _csv_text(h)
        self.assertTrue(bom)
        rows = list(csv.reader(io.StringIO(texto)))
        self.assertEqual(rows[0][1], "dni")
        cuerpo = rows[1:]
        self.assertTrue(any(r[1] == "30222333" for r in cuerpo))
        fila = next(r for r in cuerpo if r[1] == "30222333")
        self.assertEqual(fila[2], "Marta Ruiz")
        self.assertEqual(fila[3], "IOMA")
        self.assertEqual(fila[4], "555-1")


# =========================================================================
# SUMAR+
# =========================================================================

class SumarTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()

    def test_excluye_pacientes_con_obra_social_o_prepaga(self):
        _seed_usuario("40000001", cobertura={"obras_sociales": [{"nombre": "IOMA"}]})
        _seed("svc-lab", dni="40000001", turno_dias_atras=1)

        _seed_usuario("40000002", cobertura={"prepaga": {"nombre": "Swiss Medical"}})
        _seed("svc-lab", dni="40000002", turno_dias_atras=1)

        _seed_usuario("40000003", cobertura={})  # cuenta registrada, cobertura vacía -> SÍ cuenta
        _seed("svc-lab", dni="40000003", turno_dias_atras=1)

        _seed("svc-lab", dni="40000004", turno_dias_atras=1)  # sin cuenta -> SÍ cuenta ("sin_cuenta")

        h = _CLIENT.request("GET", "/api/admin/reports/sumar", headers=_admin_headers())
        j = h.json()
        self.assertEqual(j["kpis"]["prestaciones"], 2)
        self.assertEqual(j["kpis"]["pacientes"], 2)
        self.assertAlmostEqual(j["kpis"]["pct_del_total"], 50.0, places=1)

    def test_trazadoras_embarazo_nino_sano_por_cluster_y_por_edad(self):
        hoy_art = _NOW.astimezone(_ART).date()

        # embarazo: cluster "gineco", sin importar edad.
        _seed("svc-preg", dni="40100001", turno_dias_atras=1,
              nacimiento=date(1990, 1, 1))

        # nino_sano por CLUSTER pediátrico.
        _seed("svc-pedia", dni="40100002", turno_dias_atras=1,
              nacimiento=date(hoy_art.year - 5, 6, 1))

        # nino_sano por EDAD (<10), en un servicio NO pediátrico (laboratorio
        # pedido para un chico).
        nacimiento_chico = date(hoy_art.year - 8, hoy_art.month, 1)
        _seed("svc-lab", dni="40100003", turno_dias_atras=1, nacimiento=nacimiento_chico)

        # otros: adulto en un servicio no pediátrico/no gineco.
        _seed("svc-cardio", dni="40100004", turno_dias_atras=1, nacimiento=date(1980, 1, 1))

        h = _CLIENT.request("GET", "/api/admin/reports/sumar", headers=_admin_headers())
        by_clave = {t["clave"]: t["consultas"] for t in h.json()["trazadoras"]}
        self.assertEqual(by_clave["embarazo"], 1)
        self.assertEqual(by_clave["nino_sano"], 2)
        self.assertEqual(by_clave["otros"], 1)

    def test_csv_contiene_dni_y_trazadora(self):
        _seed("svc-preg", dni="40200001", nombre="Lucia", apellido="Diaz", turno_dias_atras=1,
              nacimiento=date(1995, 5, 5))
        h = _CLIENT.request("GET", "/api/admin/reports/sumar.csv", headers=_admin_headers())
        texto, bom = _csv_text(h)
        self.assertTrue(bom)
        rows = list(csv.reader(io.StringIO(texto)))
        fila = next(r for r in rows[1:] if r[1] == "40200001")
        self.assertEqual(fila[2], "Lucia Diaz")
        self.assertEqual(fila[-1], "Embarazo")


# =========================================================================
# Estadística (estilo C2)
# =========================================================================

class EstadisticaTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()

    def _seed_en(self, servicio_id, turno_dt, **kw):
        return _insert_solicitud(
            dni=kw.get("dni", "50000000"), nombre="X", apellido="Y",
            nacimiento=kw.get("nacimiento"), sexo=kw.get("sexo", "F"),
            contacto="wa:1", servicio_id=servicio_id, centro_slug="centro-test",
            estado="aprobada", created=turno_dt - timedelta(days=1), turno_dt=turno_dt,
            asistencia=kw.get("asistencia"), solicitante_dni=kw.get("dni", "50000000"),
        )

    def test_bordes_del_mes_ART(self):
        # Febrero 2026 (no bisiesto: 28 días) -- ART es UTC-3 fijo.
        primer_instante = datetime(2026, 2, 1, 3, 0, tzinfo=timezone.utc)     # 2026-02-01 00:00 ART -> DENTRO
        ultimo_instante = datetime(2026, 3, 1, 2, 59, tzinfo=timezone.utc)   # 2026-02-28 23:59 ART -> DENTRO
        antes_del_mes = datetime(2026, 2, 1, 2, 59, tzinfo=timezone.utc)     # 2026-01-31 23:59 ART -> FUERA
        despues_del_mes = datetime(2026, 3, 1, 3, 0, tzinfo=timezone.utc)    # 2026-03-01 00:00 ART -> FUERA

        self._seed_en("svc-cardio", primer_instante, dni="50000001")
        self._seed_en("svc-cardio", ultimo_instante, dni="50000002")
        self._seed_en("svc-cardio", antes_del_mes, dni="50000003")
        self._seed_en("svc-cardio", despues_del_mes, dni="50000004")

        h = _CLIENT.request("GET", "/api/admin/reports/statistics?mes=2026-02", headers=_admin_headers())
        self.assertEqual(h.json()["kpis"]["consultas_totales"], 2)

    def test_consultas_por_hora_y_asistencia(self):
        # cardiologia-test: 20h/sem (agenda 08-12, L-V). Febrero 2026 = 28
        # días = 4 semanas exactas -> horas_ofrecidas = 80.0.
        base = datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc)
        for i, asistencia in enumerate((["presente"] * 6) + (["ausente"] * 2)):
            self._seed_en("svc-cardio", base, dni=f"5010{i:04d}", asistencia=asistencia)
        # 2 más sin marca.
        self._seed_en("svc-cardio", base, dni="50109000", asistencia=None)
        self._seed_en("svc-cardio", base, dni="50109001", asistencia=None)

        h = _CLIENT.request("GET", "/api/admin/reports/statistics?mes=2026-02", headers=_admin_headers())
        j = h.json()
        fila = next(r for r in j["por_especialidad"] if r["cluster"] == "cardiologia-test")
        self.assertEqual(fila["consultas"], 10)
        self.assertEqual(fila["presentes"], 6)
        self.assertEqual(fila["ausentes"], 2)
        self.assertEqual(fila["sin_marca"], 2)
        self.assertAlmostEqual(fila["horas_ofrecidas"], 80.0, places=1)
        self.assertAlmostEqual(fila["consultas_por_hora"], round(10 / 80.0, 2), places=3)
        self.assertAlmostEqual(j["kpis"]["pct_asistencia"], 75.0, places=1)  # 6/(6+2)

    def test_csv_no_lleva_dni_ni_nombre_seedeado(self):
        base = datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc)
        self._seed_en("svc-cardio", base, dni="50999999", nombre="Secreto", apellido="Paciente")

        h = _CLIENT.request("GET", "/api/admin/reports/statistics.csv?mes=2026-02", headers=_admin_headers())
        texto, bom = _csv_text(h)
        self.assertTrue(bom)
        self.assertNotIn("50999999", texto)
        self.assertNotIn("Secreto", texto)
        self.assertNotIn("Paciente", texto)
        # También la respuesta JSON de la página es agregada, nunca PII:
        h2 = _CLIENT.request("GET", "/api/admin/reports/statistics?mes=2026-02", headers=_admin_headers())
        self.assertNotIn("50999999", json.dumps(h2.json()))


# =========================================================================
# Capacidad estacional
# =========================================================================

class CapacidadTestCase(_CatalogoStubMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._stub_catalogo()
        _env.set_env(self, TURNOS_SEASON_FACTOR=None)

    def test_sin_oferta_detecta_cluster_con_demanda_y_cero_horas(self):
        # svc-sinoferta: agenda vacía -> 0h ofrecidas, pero con demanda reciente.
        _seed("svc-sinoferta", dni="60000001", created_dias_atras=5, turno_dias_atras=None)
        _seed("svc-sinoferta", dni="60000002", created_dias_atras=10, turno_dias_atras=None)
        _seed("svc-sinoferta", dni="60000003", created_dias_atras=20, turno_dias_atras=None)

        h = _CLIENT.request("GET", "/api/admin/reports/capacity", headers=_admin_headers())
        j = h.json()
        clusters_sin_oferta = {r["cluster"] for r in j["sin_oferta"]}
        self.assertIn("sinoferta-test", clusters_sin_oferta)
        fila = next(r for r in j["sin_oferta"] if r["cluster"] == "sinoferta-test")
        self.assertEqual(fila["solicitudes"], 3)

    def test_ocupacion_cuenta_aprobadas_de_los_proximos_14_dias(self):
        # svc-cardio: agenda 08-12 L-V, cada_min=20 -> 12 slots/día; en
        # cualquier ventana de 14 días caen exactamente 2 ocurrencias de cada
        # día de semana (14 = 2*7) -> 10 días hábiles * 12 = 120 slots.
        _insert_solicitud(
            dni="60100001", nombre="A", apellido="B", contacto="wa:1", servicio_id="svc-cardio",
            centro_slug="centro-test", estado="aprobada", created=_NOW - timedelta(days=1),
            turno_dt=_NOW + timedelta(days=2), solicitante_dni="60100001",
        )
        _insert_solicitud(
            dni="60100002", nombre="A", apellido="B", contacto="wa:1", servicio_id="svc-cardio",
            centro_slug="centro-test", estado="aprobada", created=_NOW - timedelta(days=1),
            turno_dt=_NOW + timedelta(days=10), solicitante_dni="60100002",
        )
        # Fuera de la ventana de 14 días -- no debe contarse.
        _insert_solicitud(
            dni="60100003", nombre="A", apellido="B", contacto="wa:1", servicio_id="svc-cardio",
            centro_slug="centro-test", estado="aprobada", created=_NOW - timedelta(days=1),
            turno_dt=_NOW + timedelta(days=20), solicitante_dni="60100003",
        )

        h = _CLIENT.request("GET", "/api/admin/reports/capacity", headers=_admin_headers())
        fila = next(r for r in h.json()["ocupacion"] if r["cluster"] == "cardiologia-test")
        self.assertEqual(fila["capacidad_14d"], 120)
        self.assertEqual(fila["ocupados_14d"], 2)
        self.assertAlmostEqual(fila["pct"], round(100.0 * 2 / 120, 1), places=1)

    def test_agenda_propia_cuenta_una_vez_por_medico_que_la_atiende(self):
        # svc-cardio (120 slots en 14 dias) la atienden dos medicos de su
        # cluster en su centro: la agenda vale para cada uno -> 240. Un
        # tercero de otro cluster no suma.
        medicos = [
            {"id": "m-cardio-1", "nombre": "Uno", "cluster": "cardiologia-test", "especialidad": "Cardiologia",
             "centro": "centro-test", "centros": [{"centro": "centro-test", "franjas": []}]},
            {"id": "m-cardio-2", "nombre": "Dos", "cluster": "cardiologia-test", "especialidad": "Cardiologia",
             "centro": "centro-test", "centros": [{"centro": "centro-test", "franjas": []}]},
            {"id": "m-otro", "nombre": "Tres", "cluster": "diagnostico-test", "especialidad": "Laboratorio",
             "centro": "centro-test", "centros": [{"centro": "centro-test", "franjas": []}]},
        ]
        site_catalog._doctors = lambda: medicos
        h = _CLIENT.request("GET", "/api/admin/reports/capacity", headers=_admin_headers())
        fila = next(r for r in h.json()["ocupacion"] if r["cluster"] == "cardiologia-test")
        self.assertEqual(fila["capacidad_14d"], 240)

    def test_temporada_disponible_false_por_default_y_true_con_env(self):
        h = _CLIENT.request("GET", "/api/admin/reports/capacity", headers=_admin_headers())
        self.assertEqual(h.json()["temporada"], {"disponible": False})

        _env.set_env(self, TURNOS_SEASON_FACTOR="1.5")
        h2 = _CLIENT.request("GET", "/api/admin/reports/capacity", headers=_admin_headers())
        t = h2.json()["temporada"]
        self.assertTrue(t["disponible"])
        self.assertAlmostEqual(t["factor"], 1.5, places=2)
        self.assertIn("supuesto", t)
        self.assertIsInstance(t["supuesto"], str)
        self.assertGreater(len(t["supuesto"]), 10)


if __name__ == "__main__":
    unittest.main()
