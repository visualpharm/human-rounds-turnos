"""test_reports_insights.py — pruebas de las claves NUEVAS de
GET /api/admin/reports (hero, oferta_demanda, heatmap, palancas, funnel,
lista_espera, ahorro) y de GET /api/admin/reports/reviews (rework
story-driven de "Reportes", 2026-07-20 — ver el docstring de reports.py
para el contrato completo de cada payload).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_reports_insights -v

Base dedicada propia (`sana_turnos_test_reportes_insights`, distinta de la de
test_reports.py) para poder correr en paralelo sin pisarse — mismo patrón de
router FastAPI real con `TestClient` + inserts directos que test_reports.py."""

import os
import sys
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

from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_reports  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import reports, reports_router  # noqa: E402
from app.patient_web import site_catalog, site_centers
from app.core import sitedata  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(reports_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reportes_insights", seed=lambda: db_operators.operators_list())


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
            for t in ("booking_requests", "operators", "searches", "slot_holds",
                      "ai_costs", "appointment_offers", "waitlist"):
                cur.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")


# tabla_vieja -> tabla_nueva, y tabla_nueva -> {col_vieja: col_nueva} — de
# the pre-Alembic migration 001's _TABLE_RENAMES / _COLUMN_RENAMES,
# recortado a las tablas que este archivo siembra directo. Los call sites de
# _insert()/_seed()/etc. siguen usando los kwargs viejos sin cambios.
_TABLE_MAP = {
    "solicitudes": "booking_requests",
    "turno_holds": "slot_holds",
    "costos_ia": "ai_costs",
    "ofertas_turno": "appointment_offers",
    "usuarios": "users",
}
_COL_MAPS = {
    "booking_requests": {
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
    },
    "slot_holds": {
        "expira": "expires", "servicio_id": "service_id", "dia": "day",
        "hora": "time", "solicitud_id": "request_id",
    },
    "ai_costs": {
        "operacion": "operation", "costo_usd": "cost_usd", "usuario_dni": "user_dni",
        "modelo": "model", "reconocido": "recognized",
    },
    "appointment_offers": {
        "slot_dt": "slot_at", "servicio_id": "service_id", "centro_slug": "center_slug",
        "lista_espera_id": "waitlist_id", "estado": "status",
    },
    "users": {
        "nombre": "first_name", "apellido": "last_name", "nacimiento": "birth_date",
        "sexo": "sex", "contacto": "contact", "activo": "active", "cobertura": "coverage",
        "domicilio": "address", "avisos": "notifications",
    },
}


def _insert(table, **kw):
    import psycopg
    table = _TABLE_MAP.get(table, table)
    col_map = _COL_MAPS.get(table, {})
    kw = {col_map.get(k, k): v for k, v in kw.items()}
    cols = list(kw.keys())
    vals = [kw[c] for c in cols]
    placeholders = ",".join(["%s"] * len(cols))
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            rid = cur.fetchone()[0]
        c.commit()
    return rid


def _insert_usuario(**kw):
    import psycopg
    kw = dict(kw)
    if isinstance(kw.get("cobertura"), (dict, list)):
        kw["cobertura"] = json.dumps(kw["cobertura"], ensure_ascii=False)
    kw = {_COL_MAPS["users"].get(k, k): v for k, v in kw.items()}
    cols = list(kw.keys())
    vals = [kw[c] for c in cols]
    placeholders = ",".join("%s::jsonb" if c == "coverage" else "%s" for c in cols)
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders}) RETURNING dni",
                vals,
            )
            dni = cur.fetchone()[0]
        c.commit()
    return dni


# Anchored to the real UTC clock (same approach as test_reports.py /
# test_reports_nuevos.py): the period queries (reports_approved_appointment/holds/
# time_preference) filter on `created > now() - N` using PostgreSQL's now(), which the
# test cannot freeze, so a frozen _NOW expires as the real date advances and the seed
# ages out of the window (regression seen 2026-08-10: July's dias_atras=5 seed was
# already older than now()-30d). No assertion couples to the absolute date — grid cells
# key off literal `dia` strings or each row's own `turno_dt`.
_NOW = datetime.now(timezone.utc)


def _seed(servicio_id="svc-x", centro_slug="centro-test", estado="pendiente", dias_atras=1,
          resuelta_horas_despues=None, turno_dias_despues=None, motivo=None,
          resuelta_por=None, dni="30111222", nombre="Ana", apellido="Lopez",
          preferencia_horario=None, recordatorio=False, asistencia=None,
          solicitante_dni=None):
    created = _NOW - timedelta(days=dias_atras)
    resuelta_en = created + timedelta(hours=resuelta_horas_despues) if resuelta_horas_despues is not None else None
    turno_dt = created + timedelta(days=turno_dias_despues) if turno_dias_despues is not None else None
    kw = dict(
        dni=dni, nombre=nombre, apellido=apellido, nacimiento=date(1990, 1, 1), sexo="F",
        contacto="wa:11", servicio_id=servicio_id, centro_slug=centro_slug,
        estado=estado, created=created, resuelta_en=resuelta_en, turno_dt=turno_dt,
        motivo_rechazo=motivo, resuelta_por=resuelta_por, solicitante_dni=solicitante_dni or dni,
        preferencia_horario=preferencia_horario,
    )
    if recordatorio:
        kw["recordatorio_dt"] = created
    if asistencia:
        kw["asistencia"] = asistencia
    return _insert("solicitudes", **kw)


def _seed_hold(servicio_id="svc-x", dia="2026-07-21", hora="14:00", dias_atras=1, solicitud_id=None):
    created = _NOW - timedelta(days=dias_atras)
    return _insert("turno_holds", servicio_id=servicio_id, dia=dia, hora=hora,
                   token=f"tok-{dia}-{hora}-{dias_atras}-{servicio_id}", expira=created + timedelta(minutes=15),
                   created=created, solicitud_id=solicitud_id)


def _seed_costo(operacion="analizar_orden", costo_usd=0.002, reconocido=True, dias_atras=1, ip="1.2.3.4"):
    ts = _NOW - timedelta(days=dias_atras)
    return _insert("costos_ia", operacion=operacion, costo_usd=costo_usd, ip=ip,
                   modelo="test-model", reconocido=reconocido, ts=ts)


_oferta_seq = [0]


def _seed_oferta(estado="ofrecida", dias_atras=1):
    created = _NOW - timedelta(days=dias_atras)
    _oferta_seq[0] += 1
    return _insert("ofertas_turno", estado=estado, created=created, sent_at=created,
                   expires_at=created + timedelta(minutes=30),
                   token=f"of-{_oferta_seq[0]}-{created.timestamp()}-{estado}")


def _admin_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


# ---- catálogo sintético (mismo patrón que test_reports.py) ----------------

_STUB_SERVICIOS = [
    {"id": "svc-cardio", "nombre": "Cardiología test", "top_label": "Cardiología test",
     "cluster": "cardiologia-test", "centro": "centro-test",
     "agenda": {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00", "cada_min": 20}},  # 20h/sem
    {"id": "svc-diag", "nombre": "Diagnóstico test", "top_label": "Diagnóstico test",
     "cluster": "diagnostico-test", "centro": "centro-test",
     "agenda": {"dias": [1, 2, 3], "desde": "08:00", "hasta": "10:00", "cada_min": 30}},  # 6h/sem
    {"id": "svc-sinoferta", "nombre": "Sin oferta test", "top_label": "Sin oferta test",
     "cluster": "sinoferta-test", "centro": "centro-test", "agenda": {}},
]
_STUB_CLUSTERS = {
    "cardiologia-test": {"id": "cardiologia-test", "nombre": "Cardiología (test)"},
    "diagnostico-test": {"id": "diagnostico-test", "nombre": "Diagnóstico (test)"},
    "sinoferta-test": {"id": "sinoferta-test", "nombre": "Sin oferta (test)"},
}
_STUB_CENTROS = [{"slug": "centro-test", "name": "Centro de Prueba"}]
_STUB_MEDICOS = []


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


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()


# ============================================================================
# Degradación sobre base vacía — CADA clave nueva sobrevive sin datos.
# ============================================================================

class DegradacionVaciaTestCase(_DbTestCase):
    def test_todas_las_claves_nuevas_presentes_y_sin_reventar(self):
        h = _CLIENT.request("GET", "/api/admin/reports", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        j = h.json()

        # hero: siempre 1-3 frases, nunca vacío.
        self.assertIsInstance(j["hero"], list)
        self.assertTrue(1 <= len(j["hero"]) <= 3)
        for frase in j["hero"]:
            self.assertIn("texto", frase)
            self.assertIn(frase["tono"], ("ok", "atencion", "critico"))

        # oferta_demanda: shape presente, servicios vacío (nadie pidió nada).
        od = j["oferta_demanda"]
        self.assertIn("clusters", od)
        self.assertIn("servicios", od)
        self.assertEqual(od["servicios"], [])
        self.assertIsInstance(od["clusters"], list)

        # heatmap: grillas de 7 días x 15 horas, desajuste vacío sin demanda.
        hm = j["heatmap"]
        self.assertEqual(len(hm["dias"]), 7)
        self.assertEqual(hm["horas"], list(range(7, 22)))
        self.assertEqual(len(hm["oferta"]), 7)
        self.assertEqual(len(hm["oferta"][0]), 15)
        self.assertEqual(len(hm["demanda"]), 7)
        self.assertEqual(hm["desajuste"], [])  # sin demanda registrada -> nunca supera la oferta

        # palancas: sin rechazos, hero neutro, 3 grupos siempre presentes en 0.
        pal = j["palancas"]
        self.assertEqual(len(pal["grupos"]), 3)
        self.assertTrue(all(g["n"] == 0 for g in pal["grupos"]))
        self.assertTrue(all(g["pct"] is None for g in pal["grupos"]))
        self.assertIn("Todavía no hubo rechazos", pal["hero"])

        # funnel: todo en cero, sin división por cero.
        fu = j["funnel"]
        self.assertEqual(fu["solicitudes"], 0)
        self.assertEqual(fu["asistencia"], {"presente": 0, "ausente": 0, "sin_dato": 0})
        self.assertEqual(fu["holds_creados"], 0)
        self.assertEqual(fu["holds_convertidos_en_solicitud"], 0)

        # lista_espera: todo en cero, rechazadas siempre None (no trackable).
        le = j["lista_espera"]
        self.assertEqual(le["ofertas_enviadas"], 0)
        self.assertEqual(le["aceptadas"], 0)
        self.assertIsNone(le["rechazadas"])
        self.assertEqual(le["turnos_recuperados"], 0)

        # ahorro: costos_ia vacía -> degrada a disponible:false, nada más.
        self.assertEqual(j["ahorro"], {"disponible": False})

    def test_resenas_sin_archivos_no_revienta(self):
        # webdata real del repo trae centros-resenas.json real -> no vacío
        # necesariamente, pero el contrato (fuente + centros) debe cumplirse
        # siempre y nunca tirar 500 incluso si faltaran los dos archivos.
        h = _CLIENT.request("GET", "/api/admin/reports/reviews", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIn("fuente", h.json())
        self.assertIn("centers", h.json())


class ResenasFallbackTestCase(_DbTestCase):
    """Fuerza los tres escalones de fallback con TURNOS_WEBDATA_DIR apuntando
    a un directorio temporal controlado por el test."""

    def setUp(self):
        super().setUp()
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="sana-resenas-test-")
        _env.set_env(self, TURNOS_WEBDATA_DIR=self._tmp)

    def test_ninguno_de_los_dos_archivos_existe(self):
        h = _CLIENT.request("GET", "/api/admin/reports/reviews", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIsNone(h.json()["fuente"])
        self.assertEqual(h.json()["centers"], {})

    def test_fallback_a_curado_sin_analisis(self):
        with open(sitedata.path("centros-resenas.json"), "w", encoding="utf-8") as f:
            json.dump({"_note": "curado a mano", "hospital-pinamar": {"rating": 4.2}}, f)
        h = _CLIENT.request("GET", "/api/admin/reports/reviews", headers=_admin_headers())
        self.assertEqual(h.json()["fuente"], "curado")
        self.assertNotIn("_note", h.json()["centers"])
        self.assertEqual(h.json()["centers"]["hospital-pinamar"]["rating"], 4.2)

    def test_prioriza_analisis_sobre_curado(self):
        with open(sitedata.path("centros-resenas.json"), "w", encoding="utf-8") as f:
            json.dump({"hospital-pinamar": {"rating": 4.2}}, f)
        with open(sitedata.path("resenas-analisis.json"), "w", encoding="utf-8") as f:
            json.dump({"centers": {"hospital-pinamar": {"rating": 4.7, "themes": []}}}, f)
        h = _CLIENT.request("GET", "/api/admin/reports/reviews", headers=_admin_headers())
        self.assertEqual(h.json()["fuente"], "analisis")
        self.assertEqual(h.json()["centers"]["hospital-pinamar"]["rating"], 4.7)

    def test_operador_403_admin_200(self):
        db_operators.operator_create("op-resenas", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/reports/reviews", headers={"Cookie": "st_admin=" + admin_auth._mint_token("op-resenas", "admisionista")})
        self.assertEqual(h.status_code, 403)


# ============================================================================
# oferta_demanda: sugerencia_horas + equilibrio
# ============================================================================

class OfertaDemandaTestCase(_CatalogoStubMixin, _DbTestCase):
    def setUp(self):
        super().setUp()
        self._stub_catalogo()

    def test_sugerencia_horas_lleva_al_equilibrio(self):
        # svc-cardio: 20h/sem, 10 solicitudes -> 0.5/h
        for _ in range(10):
            _seed("svc-cardio", dias_atras=3)
        # svc-diag: 6h/sem, 24 solicitudes -> 4.0/h (el más exigido; con menos de 20 no se aconseja)
        for _ in range(24):
            _seed("svc-diag", dias_atras=3)
        # mediana de razones: [0.5, 4.0] -> equilibrio 2.25

        out = reports._supply_demand(30)
        self.assertAlmostEqual(out["equilibrio"], 2.25, places=2)

        by_id = {r["id"]: r for r in out["servicios"]}
        # cardio ya está POR DEBAJO del equilibrio -> no hay sugerencia.
        self.assertIsNone(by_id["svc-cardio"]["sugerencia_horas"])
        # diag: para bajar a 2.25/h con 24 solicitudes hacen falta 24/2.25=10.67h,
        # ya tiene 6h -> sugerencia = 4.7h extra.
        self.assertAlmostEqual(by_id["svc-diag"]["sugerencia_horas"], 4.7, places=1)

    def test_sin_oferta_tambien_recibe_sugerencia(self):
        for _ in range(10):
            _seed("svc-cardio", dias_atras=3)   # 0.5/h -> ancla el equilibrio
        for _ in range(20):
            _seed("svc-sinoferta", dias_atras=3)  # 0h/sem, 20 solicitudes (el mínimo para aconsejar)

        out = reports._supply_demand(30)
        by_id = {r["id"]: r for r in out["servicios"]}
        fila = by_id["svc-sinoferta"]
        self.assertTrue(fila["sin_oferta"])
        self.assertIsNotNone(fila["sugerencia_horas"])
        self.assertGreater(fila["sugerencia_horas"], 0)

    def test_servicios_sin_solicitud_no_aparecen(self):
        _seed("svc-cardio", dias_atras=3)
        out = reports._supply_demand(30)
        ids = {r["id"] for r in out["servicios"]}
        self.assertIn("svc-cardio", ids)
        self.assertNotIn("svc-diag", ids)       # sin solicitudes en el período
        self.assertNotIn("svc-sinoferta", ids)

    def test_centro_presente_en_servicios(self):
        _seed("svc-cardio", dias_atras=1)
        out = reports._supply_demand(30)
        fila = next(r for r in out["servicios"] if r["id"] == "svc-cardio")
        self.assertEqual(fila["centro"], "Centro de Prueba")


# ============================================================================
# palancas
# ============================================================================

class PalancasTestCase(_DbTestCase):
    def test_agrupa_prevenible_capacidad_otro(self):
        _seed(estado="rechazada", dias_atras=1, motivo="La orden está vencida.", resuelta_por="admin")
        _seed(estado="rechazada", dias_atras=1, motivo="El DNI de la orden no coincide con el de la cuenta.",
              resuelta_por="admin")
        _seed(estado="rechazada", dias_atras=1, motivo="No hay turnos disponibles para esa especialidad.",
              resuelta_por="admin")
        _seed(estado="rechazada", dias_atras=1, motivo="el paciente se arrepintió", resuelta_por="admin")

        out = reports._levers(30)
        by_id = {g["id"]: g for g in out["grupos"]}
        self.assertEqual(by_id["prevenible_antes"]["n"], 2)
        self.assertEqual(by_id["capacidad"]["n"], 1)
        self.assertEqual(by_id["otro"]["n"], 1)
        self.assertAlmostEqual(by_id["prevenible_antes"]["pct"], 50.0, places=1)
        self.assertIn("50%", out["hero"])

    def test_hero_neutro_sin_rechazos(self):
        out = reports._levers(30)
        self.assertEqual(out["grupos"][0]["n"], 0)
        self.assertIn("Todavía no hubo rechazos", out["hero"])

    def test_hero_cuando_no_predomina_lo_evitable(self):
        _seed(estado="rechazada", dias_atras=1, motivo="el paciente se arrepintió", resuelta_por="admin")
        _seed(estado="rechazada", dias_atras=1, motivo="ya no lo necesita", resuelta_por="admin")
        out = reports._levers(30)
        by_id = {g["id"]: g for g in out["grupos"]}
        self.assertEqual(by_id["prevenible_antes"]["n"], 0)
        self.assertNotIn("se podía evitar", out["hero"])


# ============================================================================
# heatmap: convención de día (0=domingo JS) + desajuste
# ============================================================================

class HeatmapTestCase(_CatalogoStubMixin, _DbTestCase):
    def setUp(self):
        super().setUp()
        self._stub_catalogo()

    def test_convencion_dia_0_domingo(self):
        # 2026-07-21 es un martes -> día JS 2.
        self.assertEqual(reports._weekday_js_from_iso("2026-07-21"), 2)
        # 2026-07-19 es un domingo -> día JS 0.
        self.assertEqual(reports._weekday_js_from_iso("2026-07-19"), 0)
        # 2026-07-25 es un sábado -> día JS 6.
        self.assertEqual(reports._weekday_js_from_iso("2026-07-25"), 6)

    def test_demanda_desde_turno_holds_cae_en_su_dia_hora(self):
        _seed_hold(dia="2026-07-21", hora="14:00", dias_atras=1)  # martes 14h
        grid = reports._demand_grid(30)
        self.assertEqual(grid[2][14], 1.0)

    def test_demanda_desde_aprobada_usa_turno_dt_en_art(self):
        # turno_dt guardado en UTC; en ART (-3) cae una hora antes y puede
        # cruzar de día — se verifica indirectamente vía el propio cómputo.
        sid = _seed("svc-x", estado="aprobada", dias_atras=5, turno_dias_despues=2, resuelta_por="admin")
        row = db_booking.get_booking_request(sid)
        wd, hh = reports._weekday_hour_art(row["turno_dt"])
        grid = reports._demand_grid(30)
        self.assertEqual(grid[wd][hh], 1.0)

    def test_desajuste_solo_celdas_positivas_y_top5(self):
        # sin ninguna oferta real (catálogo stub sin médicos ni agenda con
        # franjas fuera de las de _STUB_SERVICIOS) y con holds en varias
        # horas, todas las celdas con demanda deberían quedar en desajuste.
        for h in ("08:00", "09:00", "10:00", "11:00", "12:00", "13:00"):
            _seed_hold(dia="2026-07-20", hora=h, dias_atras=1)  # lunes
        out = reports._heatmap(30)
        self.assertLessEqual(len(out["desajuste"]), 5)
        for celda in out["desajuste"]:
            self.assertGreater(celda["diferencia"], 0)
            self.assertIn("label", celda)
            self.assertTrue(celda["label"][0].isupper())

    def test_preferencia_horario_se_reparte_en_dias_habiles(self):
        _seed("svc-x", dias_atras=1, preferencia_horario="A la tarde · desde mañana")
        grid = reports._demand_grid(30)
        # lunes(1) a viernes(5), horas 14-17: cada celda recibe 1/(5*4).
        peso_esperado = 1.0 / (5 * 4)
        for d in (1, 2, 3, 4, 5):
            for h in (14, 15, 16, 17):
                self.assertAlmostEqual(grid[d][h], peso_esperado, places=6)
        # nunca cae fuera de la banda ni fin de semana.
        self.assertEqual(grid[0][14], 0.0)
        self.assertEqual(grid[6][14], 0.0)
        self.assertEqual(grid[1][8], 0.0)

    def test_preferencia_con_turno_dt_no_se_cuenta_dos_veces(self):
        # una aprobada con turno_dt YA cuenta por la fuente 1 (turno_dt); si
        # tuviera preferencia_horario igual, reportes_preferencia_horario no
        # debe devolverla (turno_dt IS NULL es parte del filtro).
        _seed("svc-x", estado="aprobada", dias_atras=5, turno_dias_despues=2,
              resuelta_por="admin", preferencia_horario="A la tarde")
        rows = db_reports.reports_time_preference(30)
        self.assertEqual(rows, [])


# ============================================================================
# funnel
# ============================================================================

class FunnelTestCase(_DbTestCase):
    def test_cuenta_estados_asistencia_y_recordatorios(self):
        _seed(estado="aprobada", dias_atras=1, resuelta_por="admin", asistencia="presente")
        _seed(estado="aprobada", dias_atras=1, resuelta_por="admin", asistencia="ausente")
        _seed(estado="aprobada", dias_atras=1, resuelta_por="admin")  # sin dato
        _seed(estado="rechazada", dias_atras=1, motivo="La orden está vencida.", resuelta_por="admin")
        _seed(estado="cancelada", dias_atras=1)
        _seed(estado="pendiente", dias_atras=1)
        _seed(estado="aprobada", dias_atras=1, resuelta_por="admin", recordatorio=True)

        out = reports._funnel(30)
        self.assertEqual(out["solicitudes"], 7)
        self.assertEqual(out["aprobadas"], 4)
        self.assertEqual(out["rechazadas"], 1)
        self.assertEqual(out["canceladas"], 1)
        self.assertEqual(out["pendientes"], 1)
        self.assertEqual(out["asistencia"], {"presente": 1, "ausente": 1, "sin_dato": 2})
        self.assertEqual(out["recordatorios_enviados"], 1)

    def test_holds_creados_y_convertidos(self):
        sid = _seed(dias_atras=1)
        _seed_hold(dia="2026-07-20", hora="09:00", dias_atras=1, solicitud_id=sid)
        _seed_hold(dia="2026-07-20", hora="10:00", dias_atras=1, solicitud_id=None)
        _seed_hold(dia="2026-07-20", hora="11:00", dias_atras=100, solicitud_id=None)  # fuera de período

        out = reports._funnel(30)
        self.assertEqual(out["holds_creados"], 2)
        self.assertEqual(out["holds_convertidos_en_solicitud"], 1)


# ============================================================================
# lista_espera
# ============================================================================

class ListaEsperaTestCase(_DbTestCase):
    def test_cuenta_por_estado_y_rechazadas_es_none(self):
        _seed_oferta("ofrecida", dias_atras=1)
        _seed_oferta("aceptada", dias_atras=1)
        _seed_oferta("aceptada", dias_atras=1)
        _seed_oferta("expirada", dias_atras=1)
        _seed_oferta("vacante", dias_atras=1)

        out = reports._waitlist_report(30)
        self.assertEqual(out["ofertas_enviadas"], 5)
        self.assertEqual(out["aceptadas"], 2)
        self.assertEqual(out["expiradas"], 1)
        self.assertIsNone(out["rechazadas"])
        self.assertEqual(out["turnos_recuperados"], 2)


# ============================================================================
# ahorro
# ============================================================================

class AhorroTestCase(_DbTestCase):
    def test_disponible_false_sin_ninguna_fila_historica(self):
        out = reports._savings(30, recordatorios_enviados=0)
        self.assertEqual(out, {"disponible": False})

    def test_calculo_minutos_y_supuestos(self):
        _seed_costo("analizar_orden", costo_usd=0.003, reconocido=True, dias_atras=1)
        _seed_costo("analizar_orden", costo_usd=0.003, reconocido=False, dias_atras=1)  # no ahorra tiempo
        _seed_costo("ocr_frente", costo_usd=0.001, reconocido=True, dias_atras=1)
        _seed_costo("ocr_dorso", costo_usd=0.001, reconocido=True, dias_atras=1)

        out = reports._savings(30, recordatorios_enviados=2)
        self.assertTrue(out["disponible"])
        # 1 analizar_orden reconocida * 3.5 + 2 ocr reconocidos * 2 + 0 puco + 2 recordatorios * 3
        esperado = 3.5 * 1 + 2.0 * 2 + 3.0 * 2
        self.assertAlmostEqual(out["minutos_ahorrados"], esperado, places=1)
        # horas_operador se redondea a 1 decimal en el payload (precisión honesta,
        # no falsa exactitud) — comparar contra ESE mismo redondeo, no el valor crudo.
        self.assertAlmostEqual(out["horas_operador"], round(esperado / 60.0, 1), places=6)
        self.assertGreater(out["ahorro_ars"], 0)
        self.assertIsInstance(out["supuestos"], list)
        self.assertGreaterEqual(len(out["supuestos"]), 5)
        # el gasto en USD viene de los 4 registros insertados (todos ts=ahora-1d).
        self.assertAlmostEqual(out["gasto_usd"], 0.003 + 0.003 + 0.001 + 0.001, places=4)
        self.assertIsNotNone(out["roi"])  # hay gasto > 0 -> roi calculable

    def test_costo_hora_y_dolar_son_env_overridable(self):
        _seed_costo("analizar_orden", costo_usd=0.003, reconocido=True, dias_atras=1)
        with _env.patched_env(TURNOS_COSTO_HORA_OPERADOR="9999", TURNOS_DOLAR_BLUE_REPORTE="2000"):
            out = reports._savings(30, recordatorios_enviados=0)
        self.assertEqual(out["costo_hora_ars"], 9999.0)
        self.assertIn("9.999", out["supuestos"][4].replace(",", "."))  # AR$ 9.999 formateado con miles
        # The reader sees the numbers, never the name of the setting behind
        # them (critical review 08.11, E8).
        for line in out["supuestos"]:
            self.assertNotIn("TURNOS_", line)

    def test_puco_encontradas_cuenta_solo_origen_puco(self):
        _seed_costo("analizar_orden", costo_usd=0.001, reconocido=True, dias_atras=1)
        _insert_usuario(dni="30111111", nombre="A", apellido="B", contacto="wa:1", pass_hash="x",
                        created=_NOW - timedelta(days=1),
                        cobertura={"obras_sociales": [{"nombre": "IOMA", "origen": "puco"}]})
        _insert_usuario(dni="30222222", nombre="C", apellido="D", contacto="wa:2", pass_hash="x",
                        created=_NOW - timedelta(days=1),
                        cobertura={"obras_sociales": [{"nombre": "OSDE", "origen": "declarada"}]})
        out = reports._savings(30, recordatorios_enviados=0)
        # 1 analizar_orden reconocida (3.5) + 1 puco encontrada (2.0) = 5.5
        self.assertAlmostEqual(out["minutos_ahorrados"], 5.5, places=1)
        self.assertEqual(out["operaciones"], 1 + 1 + 0)  # 1 op de costos_ia + 1 puco + 0 recordatorios


# ============================================================================
# permisos sobre las claves nuevas (comparten el mismo endpoint que las viejas,
# pero se verifica igual que el 403/401 no dejen pasar payload parcial)
# ============================================================================

class PermisosTestCase(_DbTestCase):
    def test_operador_403_no_ve_ninguna_clave_nueva(self):
        db_operators.operator_create("op-insights", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/reports", headers={"Cookie": "st_admin=" + admin_auth._mint_token("op-insights", "admisionista")})
        self.assertEqual(h.status_code, 403)
        # The native dependency rejects before any report payload is built;
        # only FastAPI's structured auth error is present.
        self.assertEqual(h.json(), {"detail": {"error": "solo_admin"}})


class DemandaSemanalTestCase(unittest.TestCase):
    """The 8-week demand line starts on a whole week: the store's 56-day
    window leaves only the tail of its oldest Monday, which drew as a fall
    to zero (filled demo, 15.11)."""

    def test_la_semana_cortada_del_principio_no_entra(self):
        from unittest import mock
        hoy = datetime.now(reports._ART).date()
        lunes = hoy - timedelta(days=hoy.weekday())
        semanas = [(lunes - timedelta(weeks=k)).isoformat() for k in range(8, -1, -1)]
        sol = [(s, 0 if i == 0 else 2000) for i, s in enumerate(semanas)]
        bus = [(semanas[0], 3), (semanas[-1], 10)]
        with mock.patch.object(reports.store, "reports_booking_requests_by_week", return_value=sol), \
                mock.patch.object(reports.store, "reports_searches_by_week", return_value=bus):
            out = reports._weekly_demand()
        self.assertEqual([r["semana"] for r in out], semanas[1:])
        self.assertEqual(len(out), reports._CAPACITY_WEEKS)
        self.assertEqual(out[-1], {"semana": semanas[-1], "solicitudes": 2000, "busquedas": 10})


class HeroCapacidadTestCase(unittest.TestCase):
    """The capacity hero counts requests in Spanish: one is «1 solicitud»."""

    def test_una_solicitud_en_singular(self):
        out = reports._hero_capacity([], [{"label": "Cardiología", "solicitudes": 1}])
        self.assertIn("Cardiología concentró 1 solicitud en los últimos", out[0]["texto"])
        self.assertNotIn("solicitudes", out[0]["texto"])
        self.assertEqual(out[0]["tono"], "critico")

    def test_varias_solicitudes_en_plural(self):
        out = reports._hero_capacity([], [{"label": "Cardiología", "solicitudes": 1},
                                           {"label": "Pediatría", "solicitudes": 4}])
        self.assertIn("Pediatría concentró 4 solicitudes en los últimos", out[0]["texto"])

    def test_sin_oferta_de_peso_minimo_no_es_el_titular(self):
        # 1 request without hours among 10 000: the headline is the next
        # problem by weight (here a specialty at 93 %), not that one request.
        ocupacion = [{"label": "Salud mental", "pct": 93.0, "ocupados_14d": 837, "capacidad_14d": 900}]
        sin = [{"label": "Vacunas y prevención", "solicitudes": 1}]
        out = reports._hero_capacity(ocupacion, sin, 10000)
        self.assertNotIn("Vacunas", out[0]["texto"])
        self.assertIn("Salud mental", out[0]["texto"])
        self.assertEqual(out[0]["tono"], "atencion")
        # The same request is the headline when it is a real share of demand.
        out = reports._hero_capacity(ocupacion, [{"label": "Vacunas y prevención", "solicitudes": 300}], 10000)
        self.assertIn("Vacunas y prevención concentró 300 solicitudes", out[0]["texto"])
        # Small one by one, a real share together: still the headline.
        muchas = [{"label": "Esp %d" % i, "solicitudes": 150 - i} for i in range(10)]
        out = reports._hero_capacity(ocupacion, muchas, 10000)
        self.assertIn("Esp 0 concentró 150 solicitudes", out[0]["texto"])
        # With nothing fuller to name, the single request still is no headline.
        out = reports._hero_capacity([{"label": "Clínica", "pct": 70.0, "ocupados_14d": 70, "capacidad_14d": 100}],
                                     sin, 10000)
        self.assertNotIn("Vacunas", out[0]["texto"])
        self.assertEqual(out[0]["tono"], "ok")

    def test_el_titular_lleva_la_regla_que_sigue_la_tarjeta(self):
        # One rule for the page and its Reportes card: the headline names it
        # (`regla`), the card follows it (tests/admin-reports-disclosure).
        llena = [{"label": "Salud mental", "pct": 93.0, "ocupados_14d": 837, "capacidad_14d": 900}]
        media = [{"label": "Clínica", "pct": 40.0, "ocupados_14d": 40, "capacidad_14d": 100}]
        una = [{"label": "Vacunas y prevención", "solicitudes": 1}]
        casos = [
            (media, una, 10000, "ok", "cubre la demanda"),
            (llena, una, 10000, "atencion", "Salud mental está al 93%"),
            (media, [{"label": "Vacunas y prevención", "solicitudes": 300}], 10000, "sin_horas", "Vacunas y prevención"),
            ([], una, 10000, "sin_medida", "no hay capacidad medible"),
            ([{"label": "Clínica", "pct": None, "ocupados_14d": 3, "capacidad_14d": 0}], [], None, "sin_medida",
             "no hay capacidad medible"),
        ]
        for ocupacion, sin, total, regla, texto in casos:
            with self.subTest(regla=regla, texto=texto):
                self.assertEqual(reports._capacity_rule(ocupacion, sin, total)[0], regla)
                out = reports._hero_capacity(ocupacion, sin, total)
                self.assertEqual([h["regla"] for h in out], [regla])
                self.assertIn(texto, out[0]["texto"])


if __name__ == "__main__":
    unittest.main()
