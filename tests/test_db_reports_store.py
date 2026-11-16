"""test_db_reports_store.py — the admin-panel report queries of `core/db.py` against a real Postgres.

Stage 6 of the SQLAlchemy Core move converts the `reports_*` block (the aggregations behind the
panel's «Reportes»). This file pins every one of the 23 functions on a fixture whose answer is known:
medians (`percentile_cont ... WITHIN GROUP`), `COUNT(*) FILTER`, the Buenos Aires day / ISO week /
month grouping (`AT TIME ZONE` inlined as a literal), the period windows (`now() - n * interval '1 day'`,
both sides of the edge), rows older than the window, and an EMPTY database (zeros / None / []).
Calendar groupings are checked against an independent Python oracle over the fixture rows, the rest
against hand-counted constants. It was written and run green on the hand-written SQL first, and passes
unedited on Core.
"""
import datetime as dt
import os
import sys
import unittest
from collections import Counter
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_reports  # noqa: E402
from app.core import db_values  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_reports_store")

UTC = dt.timezone.utc
ART = ZoneInfo(db_values._ART_SQL)
D = dt.timedelta
MIN = D(minutes=1)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _insert(table, **cols):
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    from psycopg.types.json import Jsonb
    vals = [Jsonb(v) if isinstance(v, (dict, list)) else v for v in cols.values()]
    return _sql("INSERT INTO %s (%s) VALUES (%s) RETURNING id" % (table, names, marks), vals)[0]["id"]


def _art_day(ts):
    return ts.astimezone(ART).date()


def _monday(day):
    return day - D(days=day.weekday())


def _first_of_month(day):
    return day.replace(day=1)


def _month_back(day, n):
    idx = day.year * 12 + (day.month - 1) - n
    return dt.date(idx // 12, idx % 12 + 1, 1)


class ReportsCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE slot_holds, booking_requests, users, dependents, appointment_offers, "
             "searches RESTART IDENTITY CASCADE")
        self.now = _sql("SELECT now() AS n")[0]["n"]
        self.session_tz = ZoneInfo(_sql("SHOW timezone")[0]["TimeZone"])


class TestEmptyDatabase(ReportsCase):
    """A report over empty tables never raises: zeros, None, empty lists."""

    def test_kpis(self):
        self.assertEqual(db_reports.reports_kpis(30), {
            "pendientes": 0, "solicitudes_periodo": 0, "pct_aprobadas": None,
            "mediana_horas_resolucion": None, "mediana_dias_turno": None,
            "mediana_horas_pendiente": None, "max_horas_pendiente": None,
            "esperas_pendientes_horas": []})

    def test_lists(self):
        self.assertEqual(db_reports.reports_booking_requests_by_service(30), [])
        self.assertEqual(db_reports.reports_approved_appointment(30), [])
        self.assertEqual(db_reports.reports_rejections(30), [])
        self.assertEqual(db_reports.reports_volume(30), [])
        self.assertEqual(db_reports.reports_export_rows(90), [])
        self.assertEqual(db_reports.reports_patients_volume(30), [])
        self.assertEqual(db_reports.reports_patients_coverage(), [])
        self.assertEqual(db_reports.reports_patients_ages(), [])
        self.assertEqual(db_reports.reports_patients_booking_requests_by_patient(30), [])
        self.assertEqual(db_reports.reports_holds_day_hour(30), [])
        self.assertEqual(db_reports.reports_time_preference(30), [])
        self.assertEqual(db_reports.reports_appointment_offers(30), [])
        self.assertEqual(db_reports.reports_puco_found(30), [])
        self.assertEqual(db_reports.reports_consultations_completed(90), [])
        self.assertEqual(db_reports.reports_booking_requests_by_week(8), [])
        self.assertEqual(db_reports.reports_searches_by_week(8), [])
        self.assertEqual(db_reports.reports_statistics_rows("2026-01-01", "2026-02-01"), [])

    def test_patients_kpis_and_dependents(self):
        self.assertEqual(db_reports.reports_patients_kpis(30), {
            "total_cuentas": 0, "nuevas_periodo": 0, "pct_con_email": None, "pct_con_domicilio": None})
        self.assertEqual(db_reports.reports_patients_dependents(), {"total": 0, "adultos_con_dependientes": 0})

    def test_median_wait_prior_and_funnel(self):
        self.assertIsNone(db_reports.reports_median_wait_prior(30))
        self.assertEqual(db_reports.reports_funnel(30), {
            "solicitudes": 0, "aprobadas": 0, "rechazadas": 0, "canceladas": 0, "pendientes": 0,
            "presente": 0, "ausente": 0, "sin_dato_asistencia": 0, "recordatorios_enviados": 0,
            "holds_creados": 0, "holds_convertidos_en_solicitud": 0})

    def test_active_months_fills_every_month_with_zero(self):
        out = db_reports.reports_patients_active_months(12)
        first = _first_of_month(_art_day(self.now))
        self.assertEqual([r["mes"] for r in out],
                         [_month_back(first, n).isoformat() for n in range(11, -1, -1)])
        self.assertEqual({r["pacientes"] for r in out}, {0})


class Fixture(ReportsCase):
    """booking_requests, users, dependents, holds, offers and searches with a known answer."""

    def setUp(self):
        super().setUp()
        n = self.now
        self.req = {}

        def req(tag, **kw):
            kw.setdefault("center_slug", "c1")
            self.req[tag] = dict(kw)
            self.req[tag]["id"] = _insert("booking_requests", **kw)

        # --- pending (the queue has no period: `c` is 40 days old and still counts) ---
        req("a", status="pendiente", created=n - D(hours=5), service_id="s1", dni="D1",
            requester_dni="D1", first_name="Ana", last_name="Alva")
        req("b", status="pendiente", created=n - D(hours=1), service_id="s2", dni="D2",
            requester_dni="D2", time_preference="tarde", first_name="Beto", last_name="Bono")
        req("c", status="pendiente", created=n - D(days=40), service_id="s1", dni="D6",
            requester_dni="D6", time_preference="noche")
        # --- approved ---
        req("d", status="aprobada", created=n - D(days=10), appointment_at=n - D(days=8),
            resolved_at=n - D(days=10) + D(hours=6), resolved_by="op1", service_id="s1", dni="D3",
            requester_dni="D3", attendance="presente", reminder_at=n - D(days=9),
            time_preference="mañana", first_name="Dora", last_name="Diaz")
        req("e", status="aprobada", created=n - D(days=6), appointment_at=n - D(days=3),
            resolved_at=n - D(days=6) + D(hours=12), service_id="s1", center_slug="c2", dni="D4",
            requester_dni="D3", attendance="ausente")
        req("f", status="aprobada", created=n - D(days=3), appointment_at=n + D(days=2),
            resolved_at=n - D(days=3) + D(hours=3), service_id="s2", dni="D1", requester_dni="D1")
        req("n", status="aprobada", created=n - D(days=20), appointment_at=n - D(days=10),
            resolved_at=n - D(days=20) + D(hours=2), service_id="s2", center_slug="c2", dni="D5",
            requester_dni="D5", attendance="presente", birth_date=dt.date(1975, 6, 7), sex="F")
        # --- rejected ---
        req("g", status="rechazada", created=n - D(days=2), rejection_reason="Falta orden",
            resolved_at=n - D(days=2) + D(hours=1), service_id="s2", dni="D7", requester_dni="D7",
            time_preference="mañana", reminder_at=n - D(days=1))
        req("h", status="rechazada", created=n - D(days=2), rejection_reason="Falta orden",
            service_id="s1", dni="D8", requester_dni="D8")
        req("i", status="rechazada", created=n - D(days=1), service_id="s2", dni="D9")
        # --- cancelled, and the two edges of a 30-day window ---
        req("j", status="cancelada", created=n - D(days=1), service_id="s1", dni="D1", requester_dni="D1")
        req("l", status="cancelada", created=n - D(days=30) + MIN, service_id="s1", dni="D10",
            requester_dni="D10")
        req("m", status="cancelada", created=n - D(days=30) - MIN, service_id="s1", dni="D10",
            requester_dni="D10")
        # --- older than the current window: they feed the previous period / 90 days / months ---
        req("k", status="aprobada", created=n - D(days=45), appointment_at=n - D(days=38),
            service_id="s1", dni="D11", requester_dni="D11", attendance="presente")
        req("o", status="aprobada", created=n - D(days=50), appointment_at=n - D(days=41),
            service_id="s2", dni="D12", requester_dni="D12", attendance="ausente")
        req("r", status="aprobada", created=n - D(days=95), appointment_at=n - D(days=92),
            service_id="s1", dni="D13", requester_dni="D13")
        req("p", status="cancelada", created=n - D(days=200), service_id="s1", dni="D1")
        req("q", status="cancelada", created=n - D(days=500), service_id="s1", dni="D1")
        # --- the edges of an 8-week window ---
        req("w1", status="cancelada", created=n - D(weeks=8) + MIN, service_id="s1", dni="D14")
        req("w2", status="cancelada", created=n - D(weeks=8) - MIN, service_id="s1", dni="D14")

        cov1 = {"obras_sociales": [{"nombre": "OSDE", "origen": "puco"}]}
        cov2 = {"obra_social": {"nombre": "PAMI"}}
        cov3 = {"obras_sociales": []}
        cov4 = {"prepaga": "Medife"}
        self.cov = {"u1": cov1, "u2": cov2, "u3": cov3, "u4": cov4}
        _insert("users", dni="D1", created=n - D(days=5), email="u1@example.test",
                address={"calle": "Mitre 1"}, coverage=cov1, birth_date=dt.date(1980, 5, 1), sex="F")
        _insert("users", dni="D3", created=n - D(days=40), email="", coverage=cov2, sex="M")
        _insert("users", dni="D9", created=n - D(days=2), address={}, coverage=cov3,
                birth_date=dt.date(2001, 2, 3))
        _insert("users", dni="D20", created=n - D(days=100), email="u4@example.test", coverage=cov4,
                birth_date=dt.date(1950, 12, 31), sex="M")
        _insert("users", dni="D5", created=n - D(hours=1))
        self.user_created = [n - D(days=5), n - D(days=40), n - D(days=2), n - D(days=100), n - D(hours=1)]

        for adult, dni in (("D1", "K1"), ("D1", "K2"), ("D3", "K3")):
            _insert("dependents", adult_dni=adult, dni=dni, first_name="K", last_name="K",
                    birth_date=dt.date(2015, 1, 1), sex="F", relationship="hijo")

        def hold(service, day, time, created, request=None, token=None):
            _insert("slot_holds", service_id=service, day=day, time=time, token=token or "h%s%s" % (day, time),
                    request_id=request, expires=n + D(hours=1), created=created)
        hold("s1", dt.date(2026, 11, 3), dt.time(9, 30), n - D(days=1), self.req["d"]["id"])
        hold("s1", dt.date(2026, 11, 3), dt.time(10, 0), n - D(days=2))
        hold("s2", dt.date(2026, 11, 4), dt.time(14, 15), n - D(days=30) + MIN, self.req["f"]["id"])
        hold("s2", dt.date(2026, 11, 5), dt.time(8, 0), n - D(days=30) - MIN)

        def offer(status, token, created, slot_day):
            _insert("appointment_offers", slot_at=dt.datetime(2026, 11, slot_day, 12, tzinfo=UTC),
                    service_id="s1", center_slug="c1", token=token, status=status, created=created)
        offer("ofrecida", "t1", n - D(days=1), 10)
        offer("aceptada", "t2", n - D(days=2), 11)
        offer("aceptada", "t3", n - D(days=3), 12)
        offer("expirada", "t4", n - D(days=4), 13)
        offer("vacante", "t5", n - D(days=30) - MIN, 14)

        self.searches = [n - D(hours=2), n - D(days=3), n - D(days=9), n - D(days=17), n - D(days=17),
                         n - D(weeks=8) + MIN, n - D(weeks=8) - MIN, n - D(days=100)]
        for i, ts in enumerate(self.searches):
            _insert("searches", ts=ts, q="q%d" % i, results=i)

    def window(self, days):
        return [r for r in self.req.values() if r["created"] > self.now - D(days=days)]


class TestManagementReports(Fixture):
    def test_kpis_30(self):
        self.assertEqual(db_reports.reports_kpis(30), {
            "pendientes": 3,                  # a, b and the 40-day-old c: the queue has no period
            "solicitudes_periodo": 11,        # l (30d - 1 min) is in, m (30d + 1 min) is out
            "pct_aprobadas": 57.1,            # 4 approved (d, e, f, n) of 7 resolved (+ g, h, i)
            "mediana_horas_resolucion": 3.0,  # resolved_at - created: g 1h, n 2h, f 3h, d 6h, e 12h
            "mediana_dias_turno": 4.0,        # appointment_at - created: d 2d, e 3d, f 5d, n 10d
            "mediana_horas_pendiente": 5.0,
            "max_horas_pendiente": 960.0,
            "esperas_pendientes_horas": [1.0, 5.0, 960.0],
        })

    def test_kpis_period_changes_the_period_but_not_the_queue(self):
        k7 = db_reports.reports_kpis(7)
        self.assertEqual(k7["pendientes"], 3)
        self.assertEqual(k7["esperas_pendientes_horas"], [1.0, 5.0, 960.0])
        # created within 7 days: a, b, e, f, g, h, i, j -> 8; approved e, f; rejected g, h, i
        self.assertEqual(k7["solicitudes_periodo"], 8)
        self.assertEqual(k7["pct_aprobadas"], 40.0)
        self.assertEqual(k7["mediana_horas_resolucion"], 3.0)  # e 12h, f 3h, g 1h
        self.assertEqual(k7["mediana_dias_turno"], 4.0)         # e 3d, f 5d

    def test_hours_to_answer_leaves_out_turnos_that_never_waited(self):
        # A turno loaded by the desk or by the doctor (`mesa:` / `medico:`, admin_appointments) and a
        # request confirmed on the spot (`auto:web`, booking_create) are born approved: nobody waited for
        # an answer. Four of them at zero hours used to drag the median of nine values
        # (0, 0, 0, 0, 1, 2, 3, 6, 12) down to 1.0; the median of the five that did wait stays 3.0.
        n = self.now
        for i, quien in enumerate(("mesa:ana", "mesa:ana", "medico:gonzalez", "auto:web")):
            creada = n - D(days=4, hours=i)
            _insert("booking_requests", status="aprobada", created=creada, resolved_at=creada,
                    resolved_by=quien, appointment_at=n + D(days=1), service_id="s1", center_slug="c1",
                    dni="D3%d" % i)
        k = db_reports.reports_kpis(30)
        self.assertEqual(k["solicitudes_periodo"], 15)
        self.assertEqual(k["mediana_horas_resolucion"], 3.0)
        # An operator whose name merely starts like a marker is a person answering a request.
        _insert("booking_requests", status="rechazada", created=n - D(days=5),
                resolved_at=n - D(days=5) + D(hours=20), resolved_by="mesalina", service_id="s1",
                center_slug="c1", dni="D40")
        self.assertEqual(db_reports.reports_kpis(30)["mediana_horas_resolucion"], 4.5)  # 1 2 3 | 6 12 20

    def test_kpis_window_edge(self):
        self.assertEqual(db_reports.reports_kpis(31)["solicitudes_periodo"], 12)  # + m
        self.assertEqual(db_reports.reports_kpis(31)["solicitudes_periodo"], len(self.window(31)))
        self.assertEqual(db_reports.reports_kpis(100)["solicitudes_periodo"], 18)  # all but p (200d) and q (500d)
        self.assertEqual(db_reports.reports_kpis(100)["solicitudes_periodo"], len(self.window(100)))

    def test_by_service(self):
        got = dict(db_reports.reports_booking_requests_by_service(30))
        want = Counter(r["service_id"] for r in self.window(30))
        self.assertEqual(got, dict(want))
        self.assertEqual(got, {"s1": 6, "s2": 5})
        self.assertEqual(dict(db_reports.reports_booking_requests_by_service(1)), {"s1": 1, "s2": 1})  # a (5h), b (1h)

    def test_approved_appointment(self):
        got = sorted(db_reports.reports_approved_appointment(30), key=lambda t: t[2])
        want = sorted(((r["service_id"], r["created"], r["appointment_at"]) for r in self.window(30)
                       if r["status"] == "aprobada" and r.get("appointment_at")), key=lambda t: t[2])
        self.assertEqual(got, want)
        self.assertEqual(len(got), 4)
        self.assertEqual(db_reports.reports_approved_appointment(5), [("s2", self.req["f"]["created"], self.req["f"]["appointment_at"])])

    def test_rejections(self):
        self.assertEqual(dict(db_reports.reports_rejections(30)), {"Falta orden": 2, None: 1})
        self.assertEqual(dict(db_reports.reports_rejections(1)), {})
        self.assertEqual(dict(db_reports.reports_rejections(2)), {None: 1})  # i; g and h are exactly 2 days old: out
        self.assertEqual(dict(db_reports.reports_rejections(200)), {"Falta orden": 2, None: 1})

    def test_volume(self):
        by_day = {}
        for r in self.window(30):
            by_day.setdefault(_art_day(r["created"]), []).append(r["status"])
        want = [{"fecha": d.isoformat(), "total": len(s), "aprobadas": s.count("aprobada"),
                 "rechazadas": s.count("rechazada"), "pendientes": s.count("pendiente")}
                for d, s in sorted(by_day.items())]
        self.assertEqual(db_reports.reports_volume(30), want)
        self.assertEqual(sum(r["total"] for r in want), 11)
        self.assertEqual(sum(r["aprobadas"] for r in want), 4)
        self.assertEqual(sum(r["rechazadas"] for r in want), 3)
        self.assertEqual(sum(r["pendientes"] for r in want), 2)

    def test_export_rows(self):
        rows = db_reports.reports_export_rows(90)
        want = sorted((r for r in self.window(90)), key=lambda r: r["created"], reverse=True)
        self.assertEqual([r["created"] for r in rows], [r["created"] for r in want])
        self.assertEqual(len(rows), 17)
        self.assertEqual(list(rows[0]), ["created", "servicio_id", "centro_slug", "estado", "motivo_rechazo",
                                         "resuelta_por", "resuelta_en", "turno_dt", "nombre", "apellido"])
        d = [r for r in rows if r["resuelta_por"] == "op1"][0]
        self.assertEqual((d["servicio_id"], d["centro_slug"], d["estado"], d["turno_dt"]),
                         ("s1", "c1", "aprobada", self.req["d"]["appointment_at"]))
        a = [r for r in rows if r["nombre"] == "Ana"][0]
        self.assertEqual((a["apellido"], a["estado"]), ("Alva", "pendiente"))
        self.assertEqual(len(db_reports.reports_export_rows(1)), 2)  # a (5h) and b (1h)

    def test_median_wait_prior(self):
        # previous 30 days = created in (now-60d, now-30d]: k 7d, o 9d (m is cancelled)
        self.assertEqual(db_reports.reports_median_wait_prior(30), 8.0)
        # previous 7 days: only d (created 10d ago, wait 2d)
        self.assertEqual(db_reports.reports_median_wait_prior(7), 2.0)
        self.assertIsNone(db_reports.reports_median_wait_prior(100))

    def test_holds_day_hour(self):
        self.assertEqual(sorted(db_reports.reports_holds_day_hour(30)),
                         [("2026-11-03", "09:30"), ("2026-11-03", "10:00"), ("2026-11-04", "14:15")])
        self.assertEqual(db_reports.reports_holds_day_hour(1), [])
        self.assertEqual(db_reports.reports_holds_day_hour(2), [("2026-11-03", "09:30")])
        self.assertEqual(len(db_reports.reports_holds_day_hour(60)), 4)

    def test_time_preference(self):
        # declared a preference, no appointment_at, created in the window: b and g (d has an appointment, c is old)
        rows = db_reports.reports_time_preference(30)
        self.assertEqual(sorted((p, c) for p, c in rows),
                         sorted([("tarde", self.req["b"]["created"]), ("mañana", self.req["g"]["created"])]))
        self.assertEqual(sorted(p for p, _ in db_reports.reports_time_preference(60)), ["mañana", "noche", "tarde"])

    def test_funnel(self):
        self.assertEqual(db_reports.reports_funnel(30), {
            "solicitudes": 11, "aprobadas": 4, "rechazadas": 3, "canceladas": 2, "pendientes": 2,
            "presente": 2, "ausente": 1, "sin_dato_asistencia": 1, "recordatorios_enviados": 2,
            "holds_creados": 3, "holds_convertidos_en_solicitud": 2})
        short = db_reports.reports_funnel(2)
        self.assertEqual((short["solicitudes"], short["holds_creados"], short["holds_convertidos_en_solicitud"]),
                         (4, 1, 1))  # a, b, i, j; the hold of d

    def test_appointment_offers(self):
        self.assertEqual(dict(db_reports.reports_appointment_offers(30)), {"ofrecida": 1, "aceptada": 2, "expirada": 1})
        self.assertEqual(dict(db_reports.reports_appointment_offers(2)), {"ofrecida": 1})  # the others are 2d+ old
        self.assertEqual(dict(db_reports.reports_appointment_offers(60))["vacante"], 1)

    def test_consultations_completed(self):
        rows = db_reports.reports_consultations_completed(90)
        # newest appointment first: e (3d ago), d (8d), n (10d), k (38d), o (41d); f is in the future, r is 92d old
        self.assertEqual([r["id"] for r in rows], [self.req[t]["id"] for t in ("e", "d", "n", "k", "o")])
        self.assertEqual(list(rows[0]), ["id", "created", "dni", "nombre", "apellido", "servicio_id", "centro_slug",
                                         "turno_dt", "asistencia", "nacimiento", "cobertura"])
        e, d, n_ = rows[0], rows[1], rows[2]
        self.assertEqual((e["dni"], e["centro_slug"], e["asistencia"], e["cobertura"]), ("D4", "c2", "ausente", None))
        self.assertEqual((d["nombre"], d["apellido"], d["cobertura"]), ("Dora", "Diaz", self.cov["u2"]))
        self.assertEqual((n_["nacimiento"], n_["cobertura"]), (dt.date(1975, 6, 7), None))  # user D5 has none
        self.assertEqual([r["id"] for r in db_reports.reports_consultations_completed(5)], [self.req["e"]["id"]])
        self.assertEqual(len(db_reports.reports_consultations_completed(100)), 6)  # + r

    def test_consultations_completed_excludes_not_yet_held(self):
        _insert("booking_requests", status="aprobada", created=self.now - D(days=1),
                appointment_at=self.now + MIN, service_id="s9", dni="D50")
        _insert("booking_requests", status="pendiente", created=self.now - D(days=1),
                appointment_at=self.now - D(days=1), service_id="s9", dni="D51")
        ids = {r["servicio_id"] for r in db_reports.reports_consultations_completed(90)}
        self.assertNotIn("s9", ids)

    def test_by_week(self):
        want = {}
        for r in self.window(56):
            want[_monday(_art_day(r["created"]))] = want.get(_monday(_art_day(r["created"])), 0) + 1
        got = db_reports.reports_booking_requests_by_week(8)
        self.assertEqual(got, [(d.isoformat(), c) for d, c in sorted(want.items())])
        self.assertEqual(sum(c for _, c in got), 16)  # w2 (8 weeks + 1 min) is out, c (40d) / k / o are in
        self.assertTrue(all(dt.date.fromisoformat(s).weekday() == 0 for s, _ in got))
        self.assertEqual(sum(c for _, c in db_reports.reports_booking_requests_by_week(1)), len(self.window(7)))

    def test_searches_by_week(self):
        want = Counter(_monday(_art_day(ts)) for ts in self.searches if ts > self.now - D(weeks=8))
        got = db_reports.reports_searches_by_week(8)
        self.assertEqual(got, [(d.isoformat(), c) for d, c in sorted(want.items())])
        self.assertEqual(sum(want.values()), 6)
        self.assertEqual(sum(c for _, c in db_reports.reports_searches_by_week(20)), 8)


class TestPatientReports(Fixture):
    def test_patients_kpis(self):
        self.assertEqual(db_reports.reports_patients_kpis(30), {
            "total_cuentas": 5, "nuevas_periodo": 3, "pct_con_email": 40.0, "pct_con_domicilio": 40.0})
        self.assertEqual(db_reports.reports_patients_kpis(3)["nuevas_periodo"], 2)
        self.assertEqual(db_reports.reports_patients_kpis(200)["nuevas_periodo"], 5)

    def test_patients_volume(self):
        want = Counter(_art_day(ts) for ts in self.user_created if ts > self.now - D(days=30))
        self.assertEqual(db_reports.reports_patients_volume(30),
                         [{"fecha": d.isoformat(), "total": c} for d, c in sorted(want.items())])
        self.assertEqual(sum(want.values()), 3)

    def test_active_months(self):
        for months, session_tz in ((12, self.session_tz), (3, self.session_tz), (1, self.session_tz)):
            first = _first_of_month(_art_day(self.now))
            start = _month_back(first, months - 1)
            # `created >= <naive local month start>`: the naive timestamp is read in the session time zone
            start_ts = dt.datetime(start.year, start.month, 1, tzinfo=session_tz)
            seen = {}
            for r in self.req.values():
                if r["created"] >= start_ts:
                    seen.setdefault(_first_of_month(_art_day(r["created"])), set()).add(r["dni"])
            want = [{"mes": _month_back(first, n).isoformat(),
                     "pacientes": len(seen.get(_month_back(first, n), ()))} for n in range(months - 1, -1, -1)]
            self.assertEqual(db_reports.reports_patients_active_months(months), want, months)
        out = db_reports.reports_patients_active_months(12)
        self.assertEqual(len(out), 12)
        self.assertGreater(sum(r["pacientes"] for r in out), 0)

    def test_coverage_is_every_stored_document(self):
        self.assertEqual(sorted(db_reports.reports_patients_coverage(), key=repr),
                         sorted(self.cov.values(), key=repr))

    def test_puco_found_only_new_accounts_with_coverage(self):
        self.assertEqual(sorted(db_reports.reports_puco_found(30), key=repr),
                         sorted([self.cov["u1"], self.cov["u3"]], key=repr))
        self.assertEqual(db_reports.reports_puco_found(3), [self.cov["u3"]])
        self.assertEqual(len(db_reports.reports_puco_found(200)), 4)

    def test_ages(self):
        self.assertEqual(sorted(db_reports.reports_patients_ages(), key=repr), sorted(
            [(dt.date(1980, 5, 1), "F"), (dt.date(2001, 2, 3), None), (dt.date(1950, 12, 31), "M")], key=repr))

    def test_dependents(self):
        self.assertEqual(db_reports.reports_patients_dependents(), {"total": 3, "adultos_con_dependientes": 2})

    def test_requests_by_patient(self):
        # window 30: requester D1 (a, f, j) 3, D2 1, D3 (d, e) 2, D5 1, D7 1, D8 1, D10 (l) 1; i has no requester
        self.assertEqual(sorted(db_reports.reports_patients_booking_requests_by_patient(30)), [1, 1, 1, 1, 1, 2, 3])
        self.assertEqual(sorted(db_reports.reports_patients_booking_requests_by_patient(2)), [1, 2])  # D2 (b), D1 (a, j)


class TestStatisticsRows(ReportsCase):
    def _approved(self, appointment, **kw):
        kw.setdefault("service_id", "s1")
        kw.setdefault("center_slug", "c1")
        _insert("booking_requests", status=kw.pop("status", "aprobada"), created=kw.pop("created", self.now),
                appointment_at=appointment, dni="D1", **kw)

    def test_range_is_art_dates_upper_exclusive(self):
        A = dt.datetime
        self._approved(A(2026, 3, 1, 0, 0, tzinfo=ART), service_id="first", attendance="presente",
                       birth_date=dt.date(1990, 1, 1), sex="F")
        self._approved(A(2026, 3, 31, 23, 59, tzinfo=ART), service_id="last")
        self._approved(A(2026, 4, 1, 2, 30, tzinfo=UTC), service_id="utc-evening-of-31")  # 31st 23:30 in ART
        self._approved(A(2026, 4, 1, 0, 0, tzinfo=ART), service_id="next-month")           # excluded
        self._approved(A(2026, 2, 28, 23, 59, 59, tzinfo=ART), service_id="prev-month")    # excluded
        self._approved(A(2026, 3, 1, 2, 30, tzinfo=UTC), service_id="utc-evening-of-28")   # Feb 28 23:30 ART: excluded
        rows = db_reports.reports_statistics_rows("2026-03-01", "2026-04-01")
        self.assertEqual(sorted(r[0] for r in rows), ["first", "last", "utc-evening-of-31"])
        first = [r for r in rows if r[0] == "first"][0]
        self.assertEqual(first, ("first", "c1", "presente", dt.date(1990, 1, 1), "F"))
        # a date object is accepted as well as an ISO string
        self.assertEqual(sorted(r[0] for r in db_reports.reports_statistics_rows(dt.date(2026, 3, 1), dt.date(2026, 4, 1))),
                         ["first", "last", "utc-evening-of-31"])
        # empty range
        self.assertEqual(db_reports.reports_statistics_rows("2026-03-01", "2026-03-01"), [])

    def test_only_approved_with_an_appointment(self):
        t = dt.datetime(2026, 3, 10, 12, tzinfo=ART)
        self._approved(t, service_id="ok")
        self._approved(t, service_id="pending", status="pendiente")
        self._approved(t, service_id="rejected", status="rechazada")
        self._approved(None, service_id="no-appointment")
        self.assertEqual([r[0] for r in db_reports.reports_statistics_rows("2026-03-01", "2026-04-01")], ["ok"])
        self.assertEqual(list(db_reports.reports_statistics_rows("2026-03-01", "2026-04-01")[0]), ["ok", "c1", None, None, None])


if __name__ == "__main__":
    unittest.main()
