"""test_reports_funnel.py — the «Recorrido del paciente» report (admin_panel/reports_funnel.py).

On a seeded database whose answer is counted by hand: how many requests reach each step, why the others
stopped (lost vs still in progress), the biggest loss, the filters (period, center, service, doctor), the
step setting (order, on/off, validation, at least one step on) and that the payload carries counts only —
no DNI, name, contact or request id. The two routes are exercised through the real router with an admin
cookie, and a non-admin is refused.
"""
import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_ADMIN_SECRET = "funnel-admin-" + "pass-1"
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin",
                      TURNOS_ADMIN_PASS=_ADMIN_SECRET)

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import reports, reports_funnel, reports_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reports_funnel", seed=lambda: db_operators.operators_list())

_app = FastAPI()
_app.include_router(reports_router.router)
_CLIENT = TestClient(_app)

D = dt.timedelta
_DNI = "30999111"
_NAME = "Zoraida"
_CONTACT = "wa:1155550000"


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
    return _sql("INSERT INTO %s (%s) VALUES (%s) RETURNING id" % (table, names, marks), list(cols.values()))[0]["id"]


def _admin():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


_NAMES = {
    "centers": {"c1": "Centro Uno", "c2": "Centro Dos"},
    "services": {"s1": {"nombre": "Clinica", "cluster": "x"}, "s2": {"nombre": "Pediatria", "cluster": "y"}},
    "doctors": {"d2": "Dra. Dos", "d9": "Dr. Nueve"},
}


class FunnelCase(unittest.TestCase):
    """Eleven requests, one of them older than the 30-day window:

    tag  status     approved  appointment  confirmation  attendance  center/service/doctor
    r1   pendiente  -         -            -             -           c1 s1
    r2   rechazada  -         -            -             -           c1 s1
    r3   aprobada   yes       future       si            -           c1 s1
    r4   aprobada   yes       past         si            presente    c2 s2 slot d2
    r5   aprobada   yes       past         -             ausente     c1 s1
    r6   aprobada   yes       past         -             -           c1 s1
    r7   cancelada  event     past         -             -           c1 s1
    r8   cancelada  -         -            -             -           c1 s1
    r9   aprobada   yes       past         -             presente    40 days old
    r10  aprobada   yes       past         no            presente    c1 s1 assigned d2 (slot d9)
    r11  aprobada   yes       past         -             ausente     c1 s1
    """

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE appointment_events, slot_holds, booking_requests RESTART IDENTITY CASCADE")
        _sql("DELETE FROM catalog WHERE type = 'config' AND id = %s", (reports_funnel.CONFIG_ID,))
        now = _sql("SELECT now() AS n")[0]["n"]
        past, future = now - D(days=2), now + D(days=5)

        def req(status, created=now - D(days=3), center="c1", service="s1", **kw):
            return _insert("booking_requests", status=status, created=created, center_slug=center,
                           service_id=service, dni=_DNI, first_name=_NAME, contact=_CONTACT, **kw)

        req("pendiente")
        req("rechazada")
        req("aprobada", appointment_at=future, confirmation="si")
        req("aprobada", appointment_at=past, confirmation="si", attendance="presente", center="c2",
            service="s2", slot_doctor_id="d2")
        req("aprobada", appointment_at=past, attendance="ausente")
        req("aprobada", appointment_at=past)
        r7 = req("cancelada", appointment_at=past)
        _insert("appointment_events", request_id=r7, kind="aprobada", status_from="pendiente", status_to="aprobada")
        req("cancelada")
        req("aprobada", created=now - D(days=40), appointment_at=now - D(days=38), attendance="presente")
        req("aprobada", appointment_at=past, confirmation="no", attendance="presente",
            assigned_doctor_id="d2", slot_doctor_id="d9")
        req("aprobada", appointment_at=past, attendance="ausente")

        for target, value in ((reports, "_center_map"), (reports, "_service_map"),
                              (reports_funnel, "_doctor_names")):
            key = {"_center_map": "centers", "_service_map": "services", "_doctor_names": "doctors"}[value]
            patcher = mock.patch.object(target, value, return_value=_NAMES[key])
            patcher.start()
            self.addCleanup(patcher.stop)

    def _save(self, steps):
        return reports_funnel.save_steps(steps)

    @staticmethod
    def _by_key(pasos):
        return {p["key"]: p for p in pasos}


class TestStepCounts(FunnelCase):
    def test_default_steps_and_counts(self):
        out = reports_funnel.payload(30)
        self.assertEqual([p["key"] for p in out["pasos"]], ["pedido", "aprobada", "turno_paso", "presente"])
        self.assertEqual([p["n"] for p in out["pasos"]], [10, 7, 5, 2])
        self.assertEqual([p["pct"] for p in out["pasos"]], [100.0, 70.0, 50.0, 20.0])

    def test_reasons_split_losses_from_in_progress(self):
        pasos = self._by_key(reports_funnel.payload(30)["pasos"])
        aprobada = pasos["aprobada"]
        self.assertEqual((aprobada["perdidos"], aprobada["en_curso"]), (2, 1))
        self.assertEqual({m["key"]: m["n"] for m in aprobada["motivos"]},
                         {"rechazada": 1, "cancelada": 1, "pendiente": 1})
        # what may still move on is listed after the real losses
        self.assertEqual(aprobada["motivos"][-1]["key"], "pendiente")
        turno = pasos["turno_paso"]
        self.assertEqual({m["key"]: m["n"] for m in turno["motivos"]}, {"cancelada": 1, "por_venir": 1})
        self.assertEqual((turno["perdidos"], turno["en_curso"]), (1, 1))
        presente = pasos["presente"]
        self.assertEqual({m["key"]: m["n"] for m in presente["motivos"]}, {"ausente": 2, "sin_marcar": 1})

    def test_biggest_loss_and_hero_as_phrases(self):
        out = reports_funnel.payload(30)
        self.assertEqual(out["mayor_perdida"],
                         {"desde": "turno_paso", "hasta": "presente", "n": 2, "pct": 40, "motivo": "ausente"})
        textos = [h["texto"] for h in out["hero"]]
        # out of the 8 whose path is decided: the 2 still on their way are said apart
        self.assertEqual(textos[0], "2 de 8 llegaron hasta «Fueron al turno».")
        # The summary card's big number is this phrase's own share (2 of 8), not the
        # step's share of the first step (2 of 10): one denominator for both.
        self.assertEqual(out["hero"][0]["pct"], 25.0)
        self.assertEqual(out["pasos"][-1]["pct"], 20.0)
        self.assertIn("se quedaron 2, el 40%", textos[1])
        self.assertEqual(textos[2], "Aparte: 2 siguen en curso, entre pendientes y con el turno más adelante.")
        self.assertEqual(len(textos), 4)

    def test_gestion_counts_the_same_steps_as_recorrido(self):
        # Critical review 08.11, E7: the Gestión funnel, its hero and its daily
        # series name and count the booking path exactly as Recorrido does.
        full = reports_funnel.payload(30)
        resumen = reports_funnel.summary(30)
        self.assertEqual(resumen["pasos"], full["pasos"])
        # The same phrases and actions; an action reads as a whole sentence there (it stands alone).
        heros = [h for h in full["hero"] if h["tono"] != "ok"]
        self.assertEqual(resumen["frases"][:len(heros)], heros)
        self.assertEqual([(f["motivo"], f["enlace"]) for f in resumen["frases"][len(heros):]],
                         [(a["motivo"], a["enlace"]) for a in full["acciones"].values()])
        # r7 was approved and later cancelled: it still got a turno.
        self.assertEqual(resumen["aprobadas"], 7)
        gestion = reports._funnel(30)
        self.assertEqual(gestion["con_turno"], self._by_key(full["pasos"])["aprobada"]["n"])
        volumen = reports.store.reports_volume(30)
        self.assertEqual(sum(v["aprobadas"] for v in volumen), 7)
        self.assertEqual(sum(v["total"] for v in volumen), 10)

    def test_gestion_never_sends_the_reader_to_gestion(self):
        # Rejections are the biggest drop: Recorrido points to Gestión for the
        # reasons; Gestión itself keeps the sentence about the drop only.
        rows = [
            {"status": "rechazada", "attendance": None, "confirmation": None, "aprobada": False, "paso": False, "n": 5},
            {"status": "aprobada", "attendance": "presente", "confirmation": "si", "aprobada": True, "paso": True, "n": 5},
        ]
        with mock.patch.object(reports_funnel.db_reports, "reports_patient_path", return_value=rows):
            hero = reports_funnel.payload(30)["hero"]
            frases = reports_funnel.summary(30)["frases"]
        self.assertIn("Los motivos de rechazo están en «Gestión».", [h["texto"] for h in hero])
        self.assertEqual([f["tono"] for f in frases], ["atencion"])
        self.assertIn("«Pidieron turno» y «Les dieron turno» se quedaron 5", frases[0]["texto"])

    def test_biggest_loss_counts_only_real_losses(self):
        # Three more pending requests: «Pidieron turno» 13 → «Les dieron turno» 7 stays 6 behind, but 4
        # of them are still pending: only the 2 rejected or cancelled are lost. The reasons of every step
        # still add up to the whole difference; the hero names the step by what was really lost.
        now = _sql("SELECT now() AS n")[0]["n"]
        for _ in range(3):
            _insert("booking_requests", status="pendiente", created=now - D(days=3), center_slug="c1",
                    service_id="s1", dni=_DNI, first_name=_NAME, contact=_CONTACT)
        out = reports_funnel.payload(30)
        pasos = out["pasos"]
        self.assertEqual([p["n"] for p in pasos], [13, 7, 5, 2])
        for prev, cur in zip(pasos, pasos[1:]):
            self.assertEqual(sum(m["n"] for m in cur["motivos"]), prev["n"] - cur["n"], cur["key"])
        self.assertEqual(out["mayor_perdida"],
                         {"desde": "turno_paso", "hasta": "presente", "n": 2, "pct": 40, "motivo": "ausente"})
        self.assertEqual(out["hero"][2]["texto"],
                         "Aparte: 5 siguen en curso, entre pendientes y con el turno más adelante.")

    def test_turnos_ahead_are_never_the_biggest_loss(self):
        # The demo case: nearly everything has its turno ahead. Nothing was lost there, so the hero names
        # no loss at all and says how many are still on their way.
        rows = [
            {"status": "aprobada", "attendance": None, "confirmation": None, "aprobada": True, "paso": False, "n": 129},
            {"status": "aprobada", "attendance": "presente", "confirmation": "si", "aprobada": True, "paso": True, "n": 20},
        ]
        with mock.patch.object(reports_funnel.db_reports, "reports_patient_path", return_value=rows):
            out = reports_funnel.payload(30)
        self.assertIsNone(out["mayor_perdida"])
        self.assertEqual([h["tono"] for h in out["hero"]], ["ok", "info"])
        self.assertEqual(out["hero"][1]["texto"],
                         "Aparte: 129 siguen en curso, entre pendientes y con el turno más adelante.")

    def test_unconfirmed_turnos_ahead_are_not_losses_with_the_confirmation_step_on(self):
        rows = [
            {"status": "aprobada", "attendance": None, "confirmation": None, "aprobada": True, "paso": False, "n": 50},
            {"status": "aprobada", "attendance": "presente", "confirmation": "si", "aprobada": True, "paso": True, "n": 10},
        ]
        config = [{"key": k, "on": True} for k, _ in reports_funnel.STEPS]
        pasos = reports_funnel.count_steps(rows, config)
        confirmada = [p for p in pasos if p["key"] == "confirmada"][0]
        self.assertEqual({m["key"]: m["n"] for m in confirmada["motivos"]}, {"por_venir": 50})
        self.assertEqual(confirmada["perdidos"], 0)

    def test_stale_pending_requests_have_their_own_reason(self):
        # A pending request older than 30 days is not «still pending»: it hung, and the step says so
        # with its count. It is not a loss either.
        rows = [
            {"status": "pendiente", "attendance": None, "confirmation": None, "aprobada": False, "paso": False,
             "colgada": True, "n": 3},
            {"status": "pendiente", "attendance": None, "confirmation": None, "aprobada": False, "paso": False,
             "colgada": False, "n": 2},
            {"status": "aprobada", "attendance": None, "confirmation": None, "aprobada": True, "paso": True, "n": 5},
        ]
        with mock.patch.object(reports_funnel.db_reports, "reports_patient_path", return_value=rows):
            paso = self._by_key(reports_funnel.payload(90)["pasos"])["aprobada"]
        self.assertEqual({m["key"]: m["n"] for m in paso["motivos"]}, {"colgada": 3, "pendiente": 2})
        self.assertEqual((paso["perdidos"], paso["en_curso"]), (0, 5))
        self.assertEqual([m["texto"] for m in paso["motivos"] if m["key"] == "colgada"],
                         ["3 llevan más de 30 días sin respuesta"])

    def test_the_hung_threshold_is_one_constant_for_the_phrase_and_the_sql(self):
        rows = [{"status": "pendiente", "attendance": None, "confirmation": None, "aprobada": False,
                 "paso": False, "colgada": True, "n": 3},
                {"status": "aprobada", "attendance": None, "confirmation": None, "aprobada": True,
                 "paso": True, "n": 5}]
        with mock.patch.object(reports_funnel.db_reports, "COLGADA_DIAS", 45), \
                mock.patch.object(reports_funnel.db_reports, "reports_patient_path", return_value=rows):
            paso = self._by_key(reports_funnel.payload(90)["pasos"])["aprobada"]
        self.assertEqual([m["texto"] for m in paso["motivos"] if m["key"] == "colgada"],
                         ["3 llevan más de 45 días sin respuesta"])

    def test_an_action_that_counts_requests_needs_its_count(self):
        with self.assertRaises(ValueError):
            reports_funnel._accion("sin_marcar", "es")
        self.assertIn("4", reports_funnel._accion("sin_marcar", "es", 4)["texto"])
        self.assertNotIn("0", reports_funnel._accion("rechazada", "es")["texto"])

    def test_the_database_flags_pending_requests_older_than_30_days(self):
        now = _sql("SELECT now() AS n")[0]["n"]
        _insert("booking_requests", status="pendiente", created=now - D(days=45), center_slug="c1",
                service_id="s1", dni=_DNI, first_name=_NAME, contact=_CONTACT)
        rows = reports_funnel.db_reports.reports_patient_path(60, None, None, None)
        self.assertEqual(sum(r["n"] for r in rows if r["colgada"]), 1)
        self.assertEqual(sum(r["n"] for r in rows if r["status"] == "pendiente" and not r["colgada"]), 1)

    def test_a_turno_rejected_after_approval_never_arrived(self):
        # The EMR conflict resolver rejects a request that was already approved (the slot was taken in the
        # EMR): the request got a turno, the turno did not stand. Counting it under «Les llegó el turno»
        # overstated that step and moved the loss one step too late, as a «rechazada» between having the
        # turno and going to it. Three such requests plus three more with the turno ahead, so every
        # reason of the breakdown has its own count (3 rejected, 4 ahead, 1 cancelled).
        now = _sql("SELECT now() AS n")[0]["n"]
        common = dict(created=now - D(days=3), center_slug="c1", service_id="s1", dni=_DNI,
                      first_name=_NAME, contact=_CONTACT)
        for _ in range(3):
            rid = _insert("booking_requests", status="rechazada", appointment_at=now - D(days=2), **common)
            _insert("appointment_events", request_id=rid, kind="aprobada", status_from="pendiente",
                    status_to="aprobada")
        for _ in range(3):
            _insert("booking_requests", status="aprobada", appointment_at=now + D(days=4), **common)
        pasos = reports_funnel.payload(30)["pasos"]
        self.assertEqual([p["n"] for p in pasos], [16, 13, 5, 2])
        por_paso = self._by_key(pasos)
        self.assertEqual({m["key"]: m["n"] for m in por_paso["turno_paso"]["motivos"]},
                         {"rechazada": 3, "por_venir": 4, "cancelada": 1})
        self.assertEqual({m["key"]: m["n"] for m in por_paso["presente"]["motivos"]},
                         {"ausente": 2, "sin_marcar": 1})
        for prev, cur in zip(pasos, pasos[1:]):
            self.assertEqual(sum(m["n"] for m in cur["motivos"]), prev["n"] - cur["n"], cur["key"])

    _MARCAR = {
        "tono": "info",
        "motivo": "sin_marcar",
        "texto": "1 sin asistencia marcada — se marca en Pacientes de hoy",
        "enlace": {"href": "/admin/cola", "texto": "Pacientes de hoy"},
    }

    def test_unmarked_attendance_sends_the_desk_to_the_screen_where_it_is_marked(self):
        # One turno without attendance, and it is not the biggest loss (2 did not come): the action is
        # offered all the same, next to its step, as a phrase that names the screen and links to it.
        out = reports_funnel.payload(30)
        self.assertEqual(out["mayor_perdida"]["motivo"], "ausente")
        self.assertEqual(out["acciones"], {"presente": self._MARCAR})
        self.assertNotIn(self._MARCAR["texto"], [h["texto"] for h in out["hero"]])
        # Gestión shows the action alone, so it keeps the whole sentence.
        self.assertEqual(reports_funnel.summary(30)["frases"][-1],
                         dict(self._MARCAR, texto="Asistencia sin marcar: 1. Se marca en Pacientes de hoy."))

    def test_the_screen_of_the_action_is_the_one_the_menu_names(self):
        # The link and its words are the menu's own entry; the reports-only role has no such screen (the
        # page shows that role the sentence without the link).
        from app.core import screen_nav
        enlace = self._MARCAR["enlace"]
        menu = {i["href"]: i["label"] for i in screen_nav.nav_items("admin")}
        self.assertEqual(menu.get(enlace["href"]), enlace["texto"])
        self.assertNotIn(enlace["href"], [i["href"] for i in screen_nav.nav_items("reportes")])

    def test_unmarked_attendance_is_its_own_line_never_a_loss(self):
        # Five turnos without attendance against two marked: nobody is known to have been lost. The
        # hero names no loss; a line of its own gives the count and links to where it is marked.
        rows = [
            {"status": "aprobada", "attendance": None, "confirmation": None, "aprobada": True, "paso": True, "n": 5},
            {"status": "aprobada", "attendance": "presente", "confirmation": "si", "aprobada": True, "paso": True, "n": 2},
        ]
        with mock.patch.object(reports_funnel.db_reports, "reports_patient_path", return_value=rows):
            out = reports_funnel.payload(30)
            frases = reports_funnel.summary(30)["frases"]
        marcar = dict(self._MARCAR, texto="5 sin asistencia marcada — se marca en Pacientes de hoy")
        self.assertIsNone(out["mayor_perdida"])
        self.assertEqual(out["acciones"], {"presente": marcar})
        self.assertEqual([f for f in frases if f.get("enlace")],
                         [dict(marcar, texto="Asistencia sin marcar: 5. Se marca en Pacientes de hoy.")])
        self.assertEqual(out["pasos"][1]["perdidos"], 0)

    def test_reason_texts_are_plural_aware(self):
        pasos = self._by_key(reports_funnel.payload(30)["pasos"])
        textos = {m["key"]: m["texto"] for m in pasos["presente"]["motivos"]}
        self.assertEqual(textos["ausente"], "2 no fueron")
        self.assertEqual(textos["sin_marcar"], "1 sin asistencia marcada")

    def test_unmeasured_steps_are_named(self):
        out = reports_funnel.payload(30)
        self.assertEqual([s["key"] for s in out["sin_datos"]], ["visitas", "abandono", "cuenta"])
        self.assertTrue(all(s["label"] for s in out["sin_datos"]))

    def test_period_window(self):
        self.assertEqual(reports_funnel.payload(90)["pasos"][0]["n"], 11)
        self.assertEqual(reports_funnel.payload(7)["pasos"][0]["n"], 10)

    def test_no_personal_data_in_payload(self):
        raw = json.dumps(reports_funnel.payload(90), ensure_ascii=False)
        for secret in (_DNI, _NAME, _CONTACT):
            self.assertNotIn(secret, raw)
        self.assertNotIn('"id": 1', raw)
        self.assertNotIn("request_id", raw)


class TestFilters(FunnelCase):
    def test_center(self):
        out = reports_funnel.payload(30, centro="c2")
        self.assertEqual([p["n"] for p in out["pasos"]], [1, 1, 1, 1])
        self.assertEqual(out["filtros"], {"centro": "c2", "servicio": None, "medico": None})

    def test_service(self):
        self.assertEqual(reports_funnel.payload(30, servicio="s1")["pasos"][0]["n"], 9)

    def test_doctor_is_assigned_else_slot(self):
        out = reports_funnel.payload(30, medico="d2")
        self.assertEqual([p["n"] for p in out["pasos"]], [2, 2, 2, 2])
        self.assertEqual(reports_funnel.payload(30, medico="d9")["pasos"][0]["n"], 0)

    def test_options_named_and_by_count(self):
        op = reports_funnel.payload(30)["opciones"]
        self.assertEqual(op["centros"], [{"id": "c1", "nombre": "Centro Uno", "n": 9},
                                         {"id": "c2", "nombre": "Centro Dos", "n": 1}])
        self.assertEqual([s["id"] for s in op["servicios"]], ["s1", "s2"])
        self.assertEqual(op["medicos"], [{"id": "d2", "nombre": "Dra. Dos", "n": 2}])

    def test_chosen_value_stays_in_its_list(self):
        op = reports_funnel.payload(7, medico="d9")["opciones"]
        self.assertIn("d9", [m["id"] for m in op["medicos"]])

    def test_empty_filter_has_no_hero(self):
        out = reports_funnel.payload(30, centro="nowhere")
        self.assertEqual(out["pasos"][0]["n"], 0)
        self.assertEqual(out["hero"], [])
        self.assertIsNone(out["mayor_perdida"])


class TestStepSetting(FunnelCase):
    def test_default_order(self):
        cfg = reports_funnel.payload(30)["pasos_config"]
        self.assertEqual([(s["key"], s["on"]) for s in cfg], [
            ("pedido", True), ("aprobada", True), ("confirmada", False), ("turno_paso", True), ("presente", True)])
        self.assertTrue(all(s["label"] for s in cfg))

    def test_turning_a_step_on(self):
        self._save([{"key": k, "on": True} for k, _ in reports_funnel.STEPS])
        pasos = self._by_key(reports_funnel.payload(30)["pasos"])
        self.assertEqual(pasos["confirmada"]["n"], 2)
        self.assertEqual({m["key"]: m["n"] for m in pasos["confirmada"]["motivos"]},
                         {"sin_respuesta": 3, "cancelada": 1, "dijo_que_no": 1})
        self.assertEqual((pasos["turno_paso"]["n"], pasos["presente"]["n"]), (1, 1))

    def test_order_changes_the_count(self):
        self._save([{"key": "presente", "on": True}, {"key": "pedido", "on": True},
                    {"key": "aprobada", "on": False}, {"key": "confirmada", "on": False},
                    {"key": "turno_paso", "on": False}])
        out = reports_funnel.payload(30)
        self.assertEqual([(p["key"], p["n"]) for p in out["pasos"]], [("presente", 2), ("pedido", 2)])
        self.assertEqual([s["key"] for s in out["pasos_config"]][:2], ["presente", "pedido"])

    def test_single_step(self):
        self._save([{"key": "pedido", "on": True}, {"key": "aprobada", "on": False}, {"key": "confirmada", "on": False},
                    {"key": "turno_paso", "on": False}, {"key": "presente", "on": False}])
        out = reports_funnel.payload(30)
        self.assertEqual([p["key"] for p in out["pasos"]], ["pedido"])
        self.assertEqual([h["texto"] for h in out["hero"]], ["10 solicitudes en el período."])

    def test_missing_steps_are_appended_with_their_default(self):
        saved = self._save([{"key": "presente", "on": True}])
        self.assertEqual([s["key"] for s in saved],
                         ["presente", "pedido", "aprobada", "confirmada", "turno_paso"])

    def test_validation(self):
        for bad in (None, [], "x", [{"key": "nope", "on": True}],
                    [{"key": "pedido", "on": True}, {"key": "pedido", "on": False}],
                    [{"key": k, "on": False} for k, _ in reports_funnel.STEPS]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self._save(bad)
        # nothing was stored: the default order is still in force
        self.assertEqual(reports_funnel.steps_config()[2], {"key": "confirmada", "on": False})


class TestRoutes(FunnelCase):
    def test_get(self):
        r = _CLIENT.get("/api/admin/reports/funnel?days=30&center=c2", headers=_admin())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["pasos"][0]["n"], 1)

    def test_post_saves_and_get_follows(self):
        steps = [{"key": k, "on": k != "turno_paso"} for k, _ in reports_funnel.STEPS]
        r = _CLIENT.post("/api/admin/reports/funnel/steps", json={"steps": steps}, headers=_admin())
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["key"] for s in r.json()["pasos_config"] if s["on"]],
                         ["pedido", "aprobada", "confirmada", "presente"])
        g = _CLIENT.get("/api/admin/reports/funnel?days=30", headers=_admin()).json()
        self.assertEqual([p["key"] for p in g["pasos"]], ["pedido", "aprobada", "confirmada", "presente"])

    def test_post_rejects_bad_body(self):
        for body in ({"steps": [{"key": "nope", "on": True}]}, {}, {"steps": []}):
            with self.subTest(body=body):
                r = _CLIENT.post("/api/admin/reports/funnel/steps", json=body, headers=_admin())
                self.assertEqual(r.status_code, 400)
                self.assertTrue(r.json()["error"])

    def test_non_admin_is_refused(self):
        self.assertEqual(_CLIENT.get("/api/admin/reports/funnel").status_code, 401)
        db_operators.operator_create("op-funnel", "clave-" + "1234", "admisionista")
        cookie = {"Cookie": "st_admin=" + admin_auth._mint_token("op-funnel", "admisionista")}
        self.assertEqual(_CLIENT.get("/api/admin/reports/funnel", headers=cookie).status_code, 403)
        r = _CLIENT.post("/api/admin/reports/funnel/steps", json={"steps": []}, headers=cookie)
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
