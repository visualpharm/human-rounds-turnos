"""test_seed_demo_store.py — the database statements of scripts/seed_demo_all.py.

The demo seeder runs on EVERY deploy of demo.humanrounds.org (`manage.py demo seed`, the daily
`demo_refresh`), so what it deletes and what it writes is pinned here row by row: the
marker-scoped delete (children before parents, foreign rows untouched), `reset()` (every
transactional table emptied before the reseed), `dia_cubierto`, `referrals_rotas`,
`_adjuntar_orden`, `_insert_request`, `_seed_searches` and a whole `seed(force=True)` at a frozen
clock: the same rows twice, nothing of a visitor's lost to the marker-scoped delete.

Written and green on the hand-written SQL first; the Core rewrite has to pass it unchanged.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _clock  # noqa: E402
import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_DEMO_SEED_REAL_AI="0")

from app.core import db, tz  # noqa: E402
from app.core import db_catalog  # noqa: E402
import scripts.seed_demo_all as sd  # noqa: E402

import _dbtest  # noqa: E402
import _password_hash  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_seed_demo_store",
                          seed=lambda: db_catalog.catalog_list("centros"))

FROZEN = datetime(2026, 10, 14, 15, 7, 0, tzinfo=tz.tzinfo())
MARCA = sd.MARCA
OTHER = "@visitor.example.test"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _password_hash.reuse_hashes()
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _q(sql, params=()):
    """One statement on a fresh connection; the rows (if any) as dicts."""
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


def _n(table, where="true", params=()):
    return _q("SELECT count(*) AS n FROM " + table + " WHERE " + where, params)[0]["n"]


def _wipe():
    """Empties every table the seeder touches (children before parents)."""
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        for t in sd._RESET_TABLES:
            cur.execute("DELETE FROM " + t)
        conn.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres no disponible")
        _wipe()
        self.enterContext(_env.patched_env(TURNOS_DEMO_MODE="1"))
        c = mock.patch.object(sd, "_now", lambda: FROZEN)
        c.start()
        self.addCleanup(c.stop)
        # ~21 000 agenda rows a seed: only the tests about the agendas book them
        self.enterContext(mock.patch.object(sd, "LLENAR_AGENDAS", self.agendas))

    agendas = False


def _user(dni, contact, email=None):
    _q("INSERT INTO users (dni, first_name, last_name, contact, email) VALUES (%s,'N','A',%s,%s)",
       (dni, contact, email))
    _q("INSERT INTO sessions (token, dni) VALUES (%s,%s)", ("s-" + dni, dni))
    _q("INSERT INTO reset_tokens (token, dni, expires) VALUES (%s,%s,now() + interval '1 hour')",
       ("r-" + dni, dni))
    _q("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, relationship) "
       "VALUES (%s,%s,'H','J','2015-01-01','F','hija')", (dni, "9" + dni))
    _q("INSERT INTO patient_lab_uploads (dni, uploaded_by_dni, filename) VALUES (%s,%s,'x.pdf')", (dni, dni))


def _request(contact):
    rid = _q("INSERT INTO booking_requests (dni, contact, service_id, status) "
             "VALUES ('70010000',%s,'svc','aprobada') RETURNING id", (contact,))[0]["id"]
    _q("INSERT INTO attendance_events (request_id, dni, attendance_to, operator) "
       "VALUES (%s,'70010000','presente','op')", (rid,))
    _q("INSERT INTO appointment_events (request_id, kind) VALUES (%s,'created')", (rid,))
    _q("INSERT INTO slot_holds (service_id, day, time, token, request_id, expires) "
       "VALUES ('svc','2026-10-20', make_time(8 + %s, 0, 0),%s,%s,now() + interval '1 hour')",
       (rid % 10, "h-%d" % rid, rid))
    return rid


def _interview(key, dni, request_id):
    iid = _q("INSERT INTO interviews (session_key, dni, request_id) VALUES (%s,%s,%s) RETURNING id",
             (key, dni, request_id))[0]["id"]
    _q("INSERT INTO chat_messages (channel, key, role, content) VALUES ('interview',%s,'user','\"hola\"')",
       (key,))
    _q("INSERT INTO consultations (interview_id, doctor_id) VALUES (%s,'dr')", (iid,))
    return iid


def _waitlist(request_id, email):
    wid = _q("INSERT INTO waitlist (request_id, dni, email, service_id) VALUES (%s,'70010000',%s,'svc') "
             "RETURNING id", (request_id, email))[0]["id"]
    _q("INSERT INTO appointment_offers (waitlist_id, token, status) VALUES (%s,%s,'ofrecida')",
       (wid, "o-%d" % wid))
    return wid


class TestDeleteMarked(_Base):
    """`_delete_marked` removes what a previous seed created — and only that."""

    def _populate(self):
        demo = sd.DEMO_PATIENT["dni"]
        _user("70000001", "a" + MARCA)
        _user("70000002", "b" + OTHER)
        _user("70000004", "+5492254410004", "d" + MARCA)  # a seeded account: phone in contact, marker in email
        b1 = _request("p1" + MARCA)
        b2 = _request("p2" + OTHER)
        w1 = _waitlist(b1, "someone" + OTHER)      # reached through the marked request
        w2 = _waitlist(b2, "someone" + OTHER)      # foreign request, foreign email: stays
        w3 = _waitlist(None, "w3" + MARCA)         # marked email, no request
        i1 = _interview("k-i1", "70000003", b1)    # foreign dni, marked request
        i2 = _interview("k-i2", "70000005", b2)    # foreign everything: stays
        i3 = _interview("k-i3", demo, None)        # the featured patient's own
        # a message with the SAME key but another channel is not an interview's
        _q("INSERT INTO chat_messages (channel, key, role, content) VALUES ('web','k-i1','user','\"x\"')")
        _q("INSERT INTO patient_summaries (dni, doctor_id, resumen) VALUES (%s,'dr','{}')", (demo,))
        _q("INSERT INTO patient_summaries (dni, doctor_id, resumen) VALUES ('70000009','dr','{}')")
        _q("INSERT INTO patient_health_profile (dni, source_interview_id) VALUES (%s,NULL)", (demo,))
        _q("INSERT INTO patient_health_profile (dni, source_interview_id) VALUES ('70000006',%s)", (i1,))
        _q("INSERT INTO patient_health_profile (dni, source_interview_id) VALUES ('70000007',%s)", (i2,))
        _q("INSERT INTO searches (q, results, source) VALUES ('a',1,%s)", (sd.SEARCH_SOURCE,))
        _q("INSERT INTO searches (q, results, source) VALUES ('b',1,%s)", (sd.SEARCH_SOURCE,))
        _q("INSERT INTO searches (q, results, source) VALUES ('c',1,'web')")
        _q("INSERT INTO searches (q, results, source) VALUES ('d',1,NULL)")
        return dict(b1=b1, b2=b2, w1=w1, w2=w2, w3=w3, i1=i1, i2=i2, i3=i3)

    def _delete(self):
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            sd._delete_marked(cur)
            conn.commit()

    def test_deletes_the_marked_graph_and_keeps_the_rest(self):
        ids = self._populate()
        self._delete()
        ids_of = lambda t, col="id": sorted(r["id"] for r in _q("SELECT " + col + " AS id FROM " + t))
        self.assertEqual(ids_of("booking_requests"), [ids["b2"]])
        self.assertEqual(ids_of("waitlist"), [ids["w2"]])
        self.assertEqual(ids_of("appointment_offers", "waitlist_id"), [ids["w2"]])
        self.assertEqual(ids_of("interviews"), [ids["i2"]])
        self.assertEqual(ids_of("consultations", "interview_id"), [ids["i2"]])
        self.assertEqual(ids_of("attendance_events", "request_id"), [ids["b2"]])
        self.assertEqual(ids_of("appointment_events", "request_id"), [ids["b2"]])
        self.assertEqual(ids_of("slot_holds", "request_id"), [ids["b2"]])
        self.assertEqual(ids_of("users", "dni"), ["70000002"])
        self.assertEqual(ids_of("sessions", "dni"), ["70000002"])
        self.assertEqual(ids_of("reset_tokens", "dni"), ["70000002"])
        self.assertEqual(ids_of("dependents", "adult_dni"), ["70000002"])
        self.assertEqual(ids_of("patient_lab_uploads", "uploaded_by_dni"), ["70000002"])
        self.assertEqual(ids_of("patient_summaries", "dni"), ["70000009"])
        self.assertEqual(ids_of("patient_health_profile", "dni"), ["70000007"])
        self.assertEqual(sorted(r["q"] for r in _q("SELECT q FROM searches")), ["c", "d"])
        # interview messages: i2's survives, the other-channel message with i1's key survives
        msgs = sorted((r["channel"], r["key"]) for r in _q("SELECT channel, key FROM chat_messages"))
        self.assertEqual(msgs, [("interview", "k-i2"), ("web", "k-i1")])

    def test_a_visitors_requests_as_the_featured_patient_go_too(self):
        # Every visitor of /account/demo-access is the same fictional patient:
        # what they booked or cancelled carries THEIR contact, not the marker,
        # and used to pile up in her «Mis turnos» for weeks (review 2026-11-15:
        # two identical «Medicina general · Cancelada por vos»).
        demo = sd.DEMO_PATIENT["dni"]
        own = _q("INSERT INTO booking_requests (dni, contact, service_id, status) "
                 "VALUES (%s,'visitor@example.com','svc','cancelada') RETURNING id", (demo,))[0]["id"]
        _q("INSERT INTO appointment_events (request_id, kind) VALUES (%s,'created')", (own,))
        for_dep = _q("INSERT INTO booking_requests (dni, requester_dni, contact, service_id, status) "
                     "VALUES ('70020000',%s,'visitor@example.com','svc','pendiente') RETURNING id",
                     (demo,))[0]["id"]
        foreign = _request("p" + OTHER)
        self._delete()
        left = sorted(r["id"] for r in _q("SELECT id FROM booking_requests"))
        self.assertEqual(left, [foreign])
        self.assertNotIn(own, left)
        self.assertNotIn(for_dep, left)

    def test_second_run_is_a_noop(self):
        self._populate()
        self._delete()
        antes = {t: _n(t) for t in sd._RESET_TABLES}
        self._delete()
        self.assertEqual({t: _n(t) for t in sd._RESET_TABLES}, antes)

    def test_empty_database(self):
        self._delete()
        self.assertEqual(sum(_n(t) for t in sd._RESET_TABLES), 0)

    def test_the_marker_is_a_suffix_match_not_a_substring_of_the_whole_value(self):
        _user("70000010", "x" + MARCA + ".evil.example")   # marker in the middle: NOT the demo's
        _request("y" + MARCA + "x")
        self._delete()
        self.assertEqual(_n("users"), 1)
        self.assertEqual(_n("booking_requests"), 1)


class TestReset(_Base):
    def test_reset_empties_every_transactional_table_then_reseeds(self):
        _user("70000002", "b" + OTHER)
        b2 = _request("p2" + OTHER)
        _waitlist(b2, "someone" + OTHER)
        _interview("k-i2", "70000005", b2)
        _q("INSERT INTO searches (q, results, source) VALUES ('c',1,'web')")
        _q("INSERT INTO chat_handoffs (code, web_key, expires) VALUES ('c1','k',now() + interval '1 hour')")
        _q("INSERT INTO clinical_history_records (dni, source_system, record_hash, record) "
           "VALUES ('70000011','alepo','h','{}')")
        seen = {}

        def fake_seed(force=False):
            seen.update({t: _n(t) for t in sd._RESET_TABLES})

        with mock.patch.object(sd, "seed", fake_seed):
            sd.reset()
        self.assertEqual(set(seen), set(sd._RESET_TABLES))
        self.assertEqual({t: n for t, n in seen.items() if n}, {})

    def test_reset_outside_demo_mode_touches_nothing(self):
        _user("70000002", "b" + OTHER)
        with _env.patched_env({"TURNOS_DEMO_MODE": "0"}, clear=False):
            sd.reset()
        self.assertEqual(_n("users"), 1)


class TestDiaCubierto(_Base):
    def _turno(self, contact, at):
        _q("INSERT INTO booking_requests (dni, contact, service_id, status, appointment_at) "
           "VALUES ('70010000',%s,'svc','aprobada',%s)", (contact, at))

    def test_false_on_an_empty_database(self):
        self.assertFalse(sd.dia_cubierto())

    def test_true_when_a_marked_appointment_falls_on_the_day(self):
        self._turno("p" + MARCA, FROZEN)
        self.assertTrue(sd.dia_cubierto())
        self.assertTrue(sd.dia_cubierto(FROZEN.date()))

    def test_false_for_another_day(self):
        self._turno("p" + MARCA, FROZEN)
        self.assertFalse(sd.dia_cubierto(FROZEN.date() + timedelta(days=1)))
        self.assertFalse(sd.dia_cubierto(FROZEN.date() - timedelta(days=1)))

    def test_a_foreign_appointment_does_not_cover_the_day(self):
        self._turno("p" + OTHER, FROZEN)
        self.assertFalse(sd.dia_cubierto())

    def test_the_featured_patients_appointment_does_not_cover_the_day(self):
        _q("INSERT INTO booking_requests (dni, contact, service_id, status, appointment_at) "
           "VALUES (%s,%s,'svc','aprobada',%s)", (sd.DEMO_PATIENT["dni"], sd.DEMO_PATIENT["contacto"], FROZEN))
        self.assertFalse(sd.dia_cubierto())

    def test_a_cancelled_appointment_does_not_cover_the_day(self):
        _q("INSERT INTO booking_requests (dni, contact, service_id, status, appointment_at) "
           "VALUES ('70010000',%s,'svc','cancelada',%s)", ("p" + MARCA, FROZEN))
        self.assertFalse(sd.dia_cubierto())

    def test_a_request_without_appointment_does_not_cover_the_day(self):
        _request("p" + MARCA)   # approved, but appointment_at NULL
        self.assertFalse(sd.dia_cubierto())

    def test_the_day_is_the_local_date_of_the_installation(self):
        # 22:30 in Buenos Aires is already the next day in UTC: the cast to date runs in the
        # session's zone, so what counts is what the database does with it — pinned as it is.
        late = datetime(2026, 10, 14, 22, 30, tzinfo=tz.tzinfo())
        self._turno("p" + MARCA, late)
        covered = {d: sd.dia_cubierto(d) for d in (date(2026, 10, 14), date(2026, 10, 15))}
        self.assertEqual(sum(covered.values()), 1)


class TestReferralsRotas(_Base):
    def _con_orden(self, contact, path):
        _q("INSERT INTO booking_requests (dni, contact, service_id, referral_path) "
           "VALUES ('70010000',%s,'svc',%s)", (contact, path))

    def test_false_when_empty(self):
        self.assertFalse(sd.referrals_rotas())

    def test_true_when_a_marked_row_points_at_a_missing_file(self):
        self._con_orden("p" + MARCA, "/nonexistent/orden.jpg")
        self.assertTrue(sd.referrals_rotas())

    def test_false_when_the_file_exists(self):
        with tempfile.NamedTemporaryFile() as f:
            self._con_orden("p" + MARCA, f.name)
            self.assertFalse(sd.referrals_rotas())

    def test_true_when_only_one_of_two_is_missing(self):
        with tempfile.NamedTemporaryFile() as f:
            self._con_orden("p" + MARCA, f.name)
            self._con_orden("q" + MARCA, "/nonexistent/otra.jpg")
            self.assertTrue(sd.referrals_rotas())

    def test_ignores_foreign_rows_and_rows_without_a_path(self):
        self._con_orden("p" + OTHER, "/nonexistent/orden.jpg")
        _request("p" + MARCA)   # referral_path NULL
        self.assertFalse(sd.referrals_rotas())


class TestInsertAndAttach(_Base):
    PAC = dict(dni="70020000", nombre="Ana", apellido="Prueba", nacimiento=date(1980, 5, 6),
               sexo="F", contacto="ana.prueba" + MARCA)

    def _insert(self, **kw):
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            sid = sd._insert_request(cur, self.PAC, "svc-x", "centro-x", FROZEN, "aprobada", **kw)
            conn.commit()
        return sid

    def test_insert_request_minimal(self):
        sid = self._insert()
        row = _q("SELECT * FROM booking_requests WHERE id=%s", (sid,))[0]
        self.assertEqual((row["dni"], row["first_name"], row["last_name"], row["birth_date"], row["sex"],
                          row["contact"], row["service_id"], row["center_slug"], row["status"]),
                         ("70020000", "Ana", "Prueba", date(1980, 5, 6), "F", "ana.prueba" + MARCA,
                          "svc-x", "centro-x", "aprobada"))
        self.assertEqual(row["requester_dni"], "70020000")
        self.assertEqual(row["created"], FROZEN)
        for col in ("appointment_at", "assigned_doctor", "assigned_doctor_id", "attendance",
                    "attendance_updated_at", "attendance_updated_by", "rejection_reason",
                    "resolved_by", "resolved_at", "cancelled_at", "time_preference"):
            self.assertIsNone(row[col], col)

    def test_insert_request_full(self):
        t = FROZEN + timedelta(days=2)
        sid = self._insert(turno=t, medico="Dr X", medico_id="x-dr", asistencia="presente",
                           asistencia_en=t, motivo_rechazo="motivo", resuelto_en=FROZEN,
                           cancelado_en=t, preferencia="mañana")
        row = _q("SELECT * FROM booking_requests WHERE id=%s", (sid,))[0]
        self.assertEqual((row["appointment_at"], row["assigned_doctor"], row["assigned_doctor_id"],
                          row["attendance"], row["attendance_updated_at"], row["attendance_updated_by"],
                          row["rejection_reason"], row["resolved_by"], row["resolved_at"],
                          row["cancelled_at"], row["time_preference"]),
                         (t, "Dr X", "x-dr", "presente", t, "demo-seed", "motivo", "demo-seed",
                          FROZEN, t, "mañana"))

    def test_resolved_by_only_when_resolved_and_attendance_by_only_when_marked(self):
        sid = self._insert(medico="Dr X", medico_id="x-dr")
        row = _q("SELECT resolved_by, attendance_updated_by FROM booking_requests WHERE id=%s", (sid,))[0]
        self.assertEqual((row["resolved_by"], row["attendance_updated_by"]), (None, None))

    def test_ids_are_sequential_and_returned(self):
        a, b = self._insert(), self._insert()
        self.assertEqual(b, a + 1)

    def test_adjuntar_orden_writes_path_mime_and_the_reading_on_that_row_only(self):
        perfil = sd._ORDENES[0]
        sid = self._insert()
        other = self._insert()
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            self.assertTrue(sd._adjuntar_orden(cur, sid, perfil, self.PAC, 3))
            conn.commit()
        row = _q("SELECT referral_path, referral_mime, ai_json FROM booking_requests WHERE id=%s", (sid,))[0]
        self.assertEqual(row["referral_path"], os.path.join(sd._ORDENES_DEMO_DIR, perfil["fname"]))
        self.assertEqual(row["referral_mime"], "image/jpeg")
        self.assertIsInstance(row["ai_json"], dict)
        self.assertEqual(row["ai_json"], sd._ai_json_orden(perfil, self.PAC, 3))
        untouched = _q("SELECT referral_path, referral_mime, ai_json FROM booking_requests WHERE id=%s",
                       (other,))[0]
        self.assertEqual(untouched, {"referral_path": None, "referral_mime": None, "ai_json": None})

    def test_adjuntar_orden_with_a_missing_sample_file_writes_nothing(self):
        sid = self._insert()
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            self.assertFalse(sd._adjuntar_orden(cur, sid, dict(sd._ORDENES[0], fname="no-existe.jpg"),
                                                self.PAC, 0))
            conn.commit()
        self.assertIsNone(_q("SELECT referral_path FROM booking_requests WHERE id=%s", (sid,))[0]["referral_path"])


class TestSeedSearches(_Base):
    def test_one_row_per_query_with_the_marker_and_a_spread_of_dates(self):
        sd._seed_searches()
        rows = _q("SELECT q, results, source, ts FROM searches ORDER BY q")
        self.assertEqual(len(rows), len(sd._QUERIES))
        self.assertEqual({r["source"] for r in rows}, {sd.SEARCH_SOURCE})
        self.assertEqual(sorted((r["q"], r["results"]) for r in rows), sorted(sd._QUERIES))
        n = len(sd._QUERIES)
        expected = {q: FROZEN - timedelta(days=2 + (i * 85 // n), hours=i % 20)
                    for i, (q, _r) in enumerate(sd._QUERIES)}
        self.assertEqual({r["q"]: r["ts"] for r in rows}, expected)

    def test_it_appends_and_the_marker_delete_takes_them_all_back(self):
        _q("INSERT INTO searches (q, results, source) VALUES ('visitor',1,'web')")
        sd._seed_searches()
        sd._seed_searches()
        self.assertEqual(_n("searches", "source=%s", (sd.SEARCH_SOURCE,)), 2 * len(sd._QUERIES))
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            sd._delete_marked(cur)
            conn.commit()
        self.assertEqual(_q("SELECT q FROM searches"), [{"q": "visitor"}])


def _content(table, cols, where="true", params=()):
    return sorted(tuple(str(v) for v in (r[c] for c in cols))
                  for r in _q("SELECT " + ", ".join(cols) + " FROM " + table + " WHERE " + where, params))


class TestSeedWhole(_Base):
    """seed(force=True) at a frozen clock."""

    def _seed(self):
        sd.seed(force=True)

    def _snapshot(self):
        return {
            "requests": _content(
                "booking_requests",
                ("dni", "first_name", "last_name", "birth_date", "sex", "contact", "service_id",
                 "center_slug", "status", "referral_path", "referral_mime", "ai_json", "rejection_reason",
                 "appointment_at", "attendance", "attendance_updated_at", "attendance_updated_by",
                 "created", "time_preference", "requester_dni", "assigned_doctor", "assigned_doctor_id",
                 "resolved_by", "resolved_at", "cancelled_at"),
                "contact LIKE %s", ("%" + MARCA,)),
            # `waitlist.created` is the database's now(): the one column the frozen clock cannot reach
            "waitlist": _content("waitlist", ("dni", "email", "service_id", "center_slug", "appointment_at",
                                              "active"), "email LIKE %s", ("%" + MARCA,)),
            "searches": _content("searches", ("ts", "q", "results", "source"), "source=%s",
                                 (sd.SEARCH_SOURCE,)),
            "users": _content("users", ("dni", "first_name", "last_name", "birth_date", "sex", "contact",
                                        "email"), "email LIKE %s", ("%" + MARCA,)),
        }

    def test_sizes_that_do_not_depend_on_the_catalog(self):
        self._seed()
        self.assertEqual(_n("searches", "source=%s", (sd.SEARCH_SOURCE,)), len(sd._QUERIES))
        # every fictional patient has an account, not only the regulars (TASK-54);
        # the order images' own holders are accounts too, so their history rows
        # read as real people (2026-11-10) — the ones that are not already one
        # of the seed's patients
        self.assertEqual(_n("users", "email LIKE %s", ("%" + MARCA,)),
                         len(sd.PACIENTES) + 1 + len(sd._ORDEN_HOLDERS_NUEVOS))
        self.assertEqual(_n("users", "contact LIKE %s", ("%" + MARCA,)), 0)
        # «Cuentas nuevas por día» drew every account at «Hoy»: sign-ups are
        # spread over past days, most of them before the 30-day window.
        dias = _q("SELECT count(DISTINCT created::date) AS n FROM users WHERE email LIKE %s", ("%" + MARCA,))[0]["n"]
        self.assertGreater(dias, 30)
        self.assertLess(_n("users", "email LIKE %s AND created > now() - interval '30 days'", ("%" + MARCA,)),
                        _n("users", "email LIKE %s", ("%" + MARCA,)) / 2)
        # the order profile now follows the service (TASK-58) and can move a request's state,
        # so how many past approvals feed the waitlist follows the catalog: a floor, not a count
        self.assertGreater(_n("waitlist", "email LIKE %s", ("%" + MARCA,)), 9)
        self.assertGreater(_n("booking_requests", "contact LIKE %s", ("%" + MARCA,)), 180)
        self.assertEqual(_n("operators", "username=%s", ("gonzalez",)), 1)
        self.assertEqual(_n("interviews", "dni=%s", (sd.DEMO_PATIENT["dni"],)), 1)

    def test_the_demo_doctor_is_a_doctor_at_work_never_an_invited_one(self):
        # audit 2026-11-05: the admin's Médicos list read «Invitado» for Juan González
        from app.core import db_operators
        self._seed()
        self.assertEqual(db_operators.operator_get_public("gonzalez")["cuenta_estado"], "perfil_completo")
        completed = _q("SELECT onboarding_completed_at FROM operators WHERE username=%s",
                       ("gonzalez",))[0]["onboarding_completed_at"]
        self._seed()
        self.assertEqual(_q("SELECT onboarding_completed_at FROM operators WHERE username=%s",
                            ("gonzalez",))[0]["onboarding_completed_at"], completed)

    def test_every_historical_request_sits_in_a_state_with_its_fields(self):
        self._seed()
        rows = _q("SELECT status, appointment_at, rejection_reason, cancelled_at, resolved_at, "
                  "referral_path, ai_json FROM booking_requests WHERE contact LIKE %s",
                  ("%" + MARCA,))
        self.assertEqual({r["status"] for r in rows} - {"aprobada", "pendiente", "rechazada", "cancelada"},
                         set())
        for r in rows:
            if r["status"] == "rechazada":
                self.assertTrue(r["rejection_reason"])
                self.assertIsNotNone(r["resolved_at"])
            if r["status"] == "cancelada":
                self.assertIsNotNone(r["cancelled_at"])
            if r["status"] == "aprobada":
                self.assertIsNotNone(r["appointment_at"])
            if r["referral_path"]:
                self.assertIsInstance(r["ai_json"], dict)

    def test_the_same_content_twice_and_no_duplicates(self):
        self._seed()
        first = self._snapshot()
        self._seed()
        self.assertEqual(self._snapshot(), first)

    def test_visitor_rows_survive_a_seed_but_not_a_reset(self):
        _user("70000002", "b" + OTHER)
        b2 = _request("p2" + OTHER)
        _q("INSERT INTO searches (q, results, source) VALUES ('c',1,'web')")
        self._seed()
        self.assertEqual(_n("users", "contact LIKE %s", ("%" + OTHER,)), 1)
        self.assertEqual(_n("booking_requests", "id=%s", (b2,)), 1)
        self.assertEqual(_n("searches", "source='web'"), 1)
        sd.reset()
        self.assertEqual(_n("users", "contact LIKE %s", ("%" + OTHER,)), 0)
        self.assertEqual(_n("booking_requests", "id=%s", (b2,)), 0)
        self.assertEqual(_n("searches", "source='web'"), 0)
        self.assertEqual(_n("searches", "source=%s", (sd.SEARCH_SOURCE,)), len(sd._QUERIES))

    def test_reset_leaves_the_seeded_state_of_a_seed(self):
        self._seed()
        first = self._snapshot()
        sd.reset()
        self.assertEqual(self._snapshot(), first)

    def test_dia_cubierto_after_a_seed_and_seed_without_force_skips(self):
        self._seed()
        self.assertTrue(sd.dia_cubierto())
        antes = _q("SELECT max(id) AS m FROM booking_requests")[0]["m"]
        sd.seed()   # same day: photos + language only
        self.assertEqual(_q("SELECT max(id) AS m FROM booking_requests")[0]["m"], antes)

    def test_referrals_of_a_seed_point_at_the_pack_and_exist(self):
        self._seed()
        self.assertFalse(sd.referrals_rotas())
        # an order goes only to its own specialty's service (TASK-58), so fewer requests carry one
        self.assertGreater(_n("booking_requests", "referral_path LIKE %s", (sd._ORDENES_DEMO_DIR + "%",)), 60)


SUNDAY = datetime(2026, 10, 18, 10, 0, 0, tzinfo=tz.tzinfo())


class TestSeedInsideTheAgenda(_Base):
    """Every appointment the demo seeds with a doctor sits inside that
    doctor's agenda on its day, as the printed day sheet reads it
    (`agenda_print.day_windows`), and no two share a slot: the demo has no
    «Turnos fuera de la agenda» (decision 2026-11-08). The doctors' booked
    agendas included (2026-11-15). It also sits inside the hours of its
    own SERVICE: the demo patient's Cardiología came at 07:00, in the
    ECG's window of the same cardiologist, while the service said
    «8 a 12» (15.11)."""

    agendas = True

    def _outside(self):
        from app.modules.turnos import agenda_print
        from app.patient_web import catalog
        medicos = {m.get("id"): m for m in catalog.doctors() or []}
        servicios = {s.get("id"): s for s in catalog.services() or []}

        def en_servicio(sid, cuando, hora):
            agenda = (servicios.get(sid) or {}).get("agenda") or {}
            bandas = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
            if not bandas:
                return True
            semana = cuando.isoweekday() % 7
            return any(semana in b["dias"] and b["desde"] <= hora < b["hasta"] for b in bandas)

        rows = _q("SELECT id, status, service_id, assigned_doctor_id AS medico, appointment_at "
                  "FROM booking_requests "
                  "WHERE contact LIKE %s AND assigned_doctor_id IS NOT NULL AND appointment_at IS NOT NULL",
                  ("%" + MARCA,))
        self.assertGreater(len(rows), 50)
        fuera, ocupados, dobles = [], set(), []
        for r in rows:
            cuando = r["appointment_at"].astimezone(tz.tzinfo())
            hora = cuando.strftime("%H:%M")
            ventanas = agenda_print.day_windows(medicos.get(r["medico"]), cuando.date())
            if not any(v["desde"] <= hora < v["hasta"] for v in ventanas) \
                    or not en_servicio(r["service_id"], cuando, hora):
                fuera.append((r["medico"], r["service_id"], cuando.isoformat(), r["status"]))
            if r["status"] == "aprobada":
                clave = (r["medico"], cuando)
                if clave in ocupados:
                    dobles.append(clave)
                ocupados.add(clave)
        return fuera, dobles

    def test_a_weekday_seed_sits_inside_every_doctors_agenda(self):
        sd.seed(force=True)
        fuera, dobles = self._outside()
        self.assertEqual(fuera, [])
        self.assertEqual(dobles, [])

    def test_a_sunday_seed_keeps_the_walkthrough_on_sunday(self):
        # 2026-11-08: on a Sunday the demo doctor had no agenda, and Sofía's
        # «tomorrow» appointment landed at 18:02 — the only row of the print
        # sheet off its agenda. 2026-11-10: the demo service works EVERY day
        # (8-20, the seed's own overlay), so Sunday keeps a same-day slot for
        # her too, plus her second future appointment elsewhere.
        with mock.patch.object(sd, "_now", lambda: SUNDAY):
            sd.seed(force=True)
            fuera, dobles = self._outside()
        self.assertEqual(fuera, [])
        self.assertEqual(dobles, [])
        proximos = _q("SELECT appointment_at FROM booking_requests WHERE dni=%s AND appointment_at > %s",
                      (sd.DEMO_PATIENT["dni"], SUNDAY))
        self.assertEqual(len(proximos), 2)
        dias = sorted(r["appointment_at"].astimezone(tz.tzinfo()).date() for r in proximos)
        self.assertEqual(dias[0], SUNDAY.date(), "the same-day walkthrough exists on a Sunday too")
        self.assertGreater(dias[-1], SUNDAY.date())

    def test_a_sunday_seed_does_not_cover_monday(self):
        """09.11: a Sunday seed once left Sofía's next visit as Monday's only
        row, and both «day already seeded» checks took it for Monday's queue —
        the Monday refresh skipped the reseed and the queue stayed empty. Her
        appointments sit on Sunday itself and days out now, but the guard this
        test pins is the same one: a day is covered only by its own QUEUE."""
        from app.ops import demo_refresh_store
        lunes = SUNDAY.date() + timedelta(days=1)
        with mock.patch.object(sd, "_now", lambda: SUNDAY):
            sd.seed(force=True)
        sofia = _q("SELECT count(*) AS n FROM booking_requests WHERE dni=%s "
                   "AND (appointment_at AT TIME ZONE %s)::date = %s",
                   (sd.DEMO_PATIENT["dni"], tz.tz_name(), SUNDAY.date()))[0]["n"]
        self.assertEqual(sofia, 1, "her same-day walkthrough is ON Sunday")
        self.assertFalse(sd.dia_cubierto(lunes))
        self.assertFalse(demo_refresh_store.day_already_seeded(lunes))
        self.assertEqual(demo_refresh_store.day_queue_size(lunes), 0)
        self.assertTrue(sd.dia_cubierto(SUNDAY.date()))
        self.assertTrue(demo_refresh_store.day_already_seeded(SUNDAY.date()))
        self.assertGreater(demo_refresh_store.day_queue_size(SUNDAY.date()), 0)

    def _hoy_por_servicio(self, dia):
        rows = _q("SELECT service_id, count(*) AS n FROM booking_requests WHERE contact LIKE %s "
                  "AND (appointment_at AT TIME ZONE %s)::date = %s GROUP BY service_id",
                  ("%" + MARCA, tz.tz_name(), dia))
        return {r["service_id"]: r["n"] for r in rows}

    def test_a_service_open_monday_to_friday_has_no_appointments_on_a_sunday(self):
        """Laboratorio and Radiografía have no agenda; their horario is
        «Lunes a viernes». A Sunday's day view had both, with patients
        (08.11) — a service without hours that day gets no appointment."""
        with mock.patch.object(sd, "_now", lambda: SUNDAY):
            sd.seed(force=True)
        domingo = self._hoy_por_servicio(SUNDAY.date())
        self.assertNotIn("laboratorio-anexo", domingo)
        self.assertNotIn("radiografia-anexo", domingo)
        self.assertTrue(domingo, "a Sunday of the demo has doctors at work")

    def test_the_same_services_are_there_on_a_weekday(self):
        sd.seed(force=True)
        miercoles = self._hoy_por_servicio(FROZEN.date())
        self.assertIn("laboratorio-anexo", miercoles)
        self.assertIn("radiografia-anexo", miercoles)


class TestAgendasBooked(_Base):
    """2026-11-15: the demo read as dead — 12 appointments on 8 552 slots of
    the next two weeks, one doctor of 68 with an occupancy, every report
    point at zero. Every doctor's agenda is booked now: its own share of
    30-65 % of the next 14 days, eight weeks of past visits, never a full
    day, each slot at the center whose window it falls in."""

    agendas = True
    _AGENDA = "%" + sd.MARCA_AGENDA

    def _futuros(self):
        from app.patient_web import catalog
        agendas = sd.AgendaDemo()
        hoy = FROZEN.date()
        rows = _q("SELECT assigned_doctor_id AS medico, appointment_at FROM booking_requests "
                  "WHERE status='aprobada' AND assigned_doctor_id IS NOT NULL "
                  "AND appointment_at > %s", (FROZEN,))
        ocupados = {}
        for r in rows:
            dia = r["appointment_at"].astimezone(tz.tzinfo()).date()
            ocupados[(r["medico"], dia)] = ocupados.get((r["medico"], dia), 0) + 1
        total = turnos = 0
        llenos = []
        for m in catalog.doctors() or []:
            for d in range(1, 15):
                dia = hoy + timedelta(days=d)
                n = len(agendas.horas(m["id"], dia))
                k = ocupados.get((m["id"], dia), 0)
                total += n
                turnos += k
                if n and k >= n:
                    llenos.append((m["id"], dia.isoformat()))
        return turnos, total, llenos

    def test_the_next_two_weeks_are_booked_but_never_full(self):
        sd.seed(force=True)
        turnos, total, llenos = self._futuros()
        self.assertGreater(total, 0)
        self.assertTrue(0.30 <= turnos / total <= 0.65, f"{turnos} of {total}")
        self.assertEqual(llenos, [], "a doctor's day with no free slot left")

    def test_the_last_week_asks_for_no_more_visits_than_the_ones_before(self):
        # The live demo: 3 242 requests made in the current week against
        # ~2 000 in every other — the future agenda was booked in its last days.
        sd.seed(force=True)
        semanas = []
        for k in range(4):
            hasta = FROZEN - timedelta(days=7 * k)
            semanas.append(_n("booking_requests", "contact LIKE %s AND created > %s AND created <= %s",
                              (self._AGENDA, hasta - timedelta(days=7), hasta)))
        # 15.11, after the first fix: the last two weeks still ~20 % above
        # (tomorrow booked at 130 %, a past day at 90 %) — level now.
        ultima, previas = semanas[0], semanas[1:]
        self.assertGreater(min(previas), 0)
        self.assertLessEqual(ultima, sum(previas) / len(previas) * 1.1, semanas)
        self.assertEqual(_n("booking_requests", "contact LIKE %s AND created > %s",
                            (self._AGENDA, FROZEN)), 0, "a request made in the future")

    def test_the_past_holds_attended_missed_and_cancelled_visits_of_many_doctors(self):
        sd.seed(force=True)
        rows = _q("SELECT status, attendance, assigned_doctor_id AS medico FROM booking_requests "
                  "WHERE contact LIKE %s AND appointment_at < %s",
                  (self._AGENDA, FROZEN - timedelta(days=1)))
        estados = {(r["status"], r["attendance"]) for r in rows}
        self.assertIn(("aprobada", "presente"), estados)
        self.assertIn(("aprobada", "ausente"), estados)
        self.assertIn(("cancelada", None), estados)
        self.assertGreater(len({r["medico"] for r in rows if r["medico"]}), 20)

    def test_every_agenda_slot_sits_in_a_window_of_its_own_center(self):
        from app.modules.turnos import doctor_windows
        from app.patient_web import catalog
        sd.seed(force=True)
        servicios = catalog.services() or []
        medicos = {m["id"]: m for m in catalog.doctors() or []}
        ventanas = {}
        fuera = []
        for r in _q("SELECT assigned_doctor_id AS medico, center_slug, appointment_at "
                    "FROM booking_requests WHERE contact LIKE %s", (self._AGENDA,)):
            cuando = r["appointment_at"].astimezone(tz.tzinfo())
            clave = (r["medico"], cuando.date())
            if clave not in ventanas:
                fuentes, _ = doctor_windows.sources(medicos[r["medico"]], servicios)
                ventanas[clave] = doctor_windows.windows_of_day(fuentes, cuando.date())
            hora = cuando.strftime("%H:%M")
            if not any(v["centro"] == r["center_slug"] and v["desde"] <= hora < v["hasta"]
                       for v in ventanas[clave]):
                fuera.append((r["medico"], cuando.isoformat(), r["center_slug"]))
        self.assertEqual(fuera[:5], [], f"{len(fuera)} slots outside their center's window")

    def test_a_visitors_booking_keeps_its_slot_through_a_reseed(self):
        sd.seed(force=True)
        fila = _q("SELECT id, assigned_doctor_id AS medico, service_id, appointment_at "
                  "FROM booking_requests WHERE contact LIKE %s AND status='aprobada' "
                  "AND appointment_at > %s ORDER BY appointment_at, assigned_doctor_id LIMIT 1",
                  (self._AGENDA, FROZEN))[0]
        # a visitor booked the very slot the seed books: the reseed must go around it
        _q("DELETE FROM booking_requests WHERE id=%s", (fila["id"],))
        _q("INSERT INTO booking_requests (dni, contact, service_id, status, assigned_doctor_id, "
           "appointment_at) VALUES ('70020000', %s, %s, 'aprobada', %s, %s)",
           ("v" + OTHER, fila["service_id"], fila["medico"], fila["appointment_at"]))
        sd.seed(force=True)
        rows = _q("SELECT contact FROM booking_requests WHERE status='aprobada' "
                  "AND assigned_doctor_id=%s AND appointment_at=%s",
                  (fila["medico"], fila["appointment_at"]))
        self.assertEqual([r["contact"] for r in rows], ["v" + OTHER])

    def test_two_seeds_book_the_same_agendas(self):
        cols = ("dni", "service_id", "center_slug", "status", "assigned_doctor_id",
                "appointment_at", "attendance", "created")
        sd.seed(force=True)
        antes = _content("booking_requests", cols, "contact LIKE %s", (self._AGENDA,))
        sd.seed(force=True)
        self.assertGreater(len(antes), 1000)
        self.assertEqual(_content("booking_requests", cols, "contact LIKE %s", (self._AGENDA,)), antes)

    def test_tomorrows_agenda_is_not_tomorrows_queue(self):
        from app.ops import demo_refresh_store
        sd.seed(force=True)
        manana = FROZEN.date() + timedelta(days=1)
        self.assertGreater(_n("booking_requests", "contact LIKE %s", (self._AGENDA,)), 0)
        self.assertFalse(sd.dia_cubierto(manana))
        self.assertEqual(demo_refresh_store.day_queue_size(manana), 0)

    def test_some_doctors_have_an_account_at_every_stage_and_some_none(self):
        from app.core import db_operators
        from app.patient_web import catalog
        sd.seed(force=True)
        ids = {m["id"] for m in catalog.doctors() or []}
        con = db_operators.operator_doctor_ids() & ids
        self.assertTrue(0 < len(con) < len(ids), f"{len(con)} of {len(ids)}")
        antes = _n("operators")
        sd.seed(force=True)
        self.assertEqual(_n("operators"), antes, "a reseed adds no second account")


class TestOrderHolders(_Base):
    """A row that carries a sample order image belongs to the person ON the
    image (2026-11-10, plan slice 1): the panel shows the image beside its
    reading, so the two must agree — the row, the AI reading and the account
    all name the same person."""

    def test_every_order_row_names_its_own_holder(self):
        sd.seed(force=True)
        rows = _q("SELECT referral_path, first_name, last_name, dni, birth_date, sex, ai_json "
                  "FROM booking_requests WHERE contact LIKE %s AND referral_path IS NOT NULL",
                  ("%" + MARCA,))
        self.assertGreater(len(rows), 40)
        con_titular = 0
        for r in rows:
            fname = os.path.basename(r["referral_path"])
            titular = sd._ORDEN_HOLDERS.get(fname)
            if titular is None:
                continue  # wrong-document rows belong to nobody on the image
            con_titular += 1
            self.assertEqual((r["first_name"], r["last_name"], r["dni"], r["birth_date"], r["sex"]),
                             (titular["nombre"], titular["apellido"], titular["dni"],
                              titular["nacimiento"], titular["sexo"]), fname)
            campos = r["ai_json"]["campos"]
            self.assertEqual(campos["paciente"]["valor"],
                             f"{titular['nombre']} {titular['apellido']}", fname)
            self.assertEqual(campos["dni"]["valor"], titular.get("dni_orden"), fname)
            user = _q("SELECT first_name, last_name FROM users WHERE dni=%s", (titular["dni"],))
            self.assertEqual(len(user), 1, fname)
        self.assertGreater(con_titular, 5, "the sample orders that name a person all match her")

    def test_a_holder_who_is_a_seed_patient_is_that_one_person(self):
        # two sample orders name the seed's own first patients: «Pacientes»
        # must not list two Camila Fernandez with one email
        self.assertEqual(len(sd._ORDEN_HOLDERS) - len(sd._ORDEN_HOLDERS_NUEVOS), 2)
        camila = sd._ORDEN_HOLDERS["bad-handwriting-cardiologia-1.jpg"]
        self.assertEqual(camila["dni"], sd.PACIENTES[0]["dni"])
        self.assertEqual(camila["contacto"], sd.PACIENTES[0]["contacto"])
        self.assertEqual(sd.PACIENTES[0]["nacimiento"], date(1990, 6, 5),
                         "the patient has the birth date the image prints")
        self.assertEqual(sd._ORDEN_HOLDERS["bad-handwriting-laboratorio-2.jpg"]["dni"],
                         sd.PACIENTES[1]["dni"])
        sd.seed(force=True)
        repetidos = _q("SELECT first_name, last_name, count(*) AS n FROM users WHERE email LIKE %s "
                       "GROUP BY first_name, last_name HAVING count(*) > 1", ("%" + MARCA,))
        self.assertEqual(repetidos, [])
        mails = _q("SELECT email, count(*) AS n FROM users WHERE email LIKE %s "
                   "GROUP BY email HAVING count(*) > 1", ("%" + MARCA,))
        self.assertEqual(mails, [])

    def test_a_missing_sample_image_gives_its_holder_one_row_per_service(self):
        # an order that cannot be attached must not hand the service's whole
        # day to the image's holder: one try per service
        intentos = []

        def sin_imagen(_cur, sid, *_resto):
            intentos.append(sid)
            return False

        with mock.patch.object(sd, "_adjuntar_orden", side_effect=sin_imagen):
            sd.seed(force=True)
        rows = _q("SELECT service_id, count(*) AS n FROM booking_requests WHERE id = ANY(%s) "
                  "AND (appointment_at AT TIME ZONE %s)::date = %s GROUP BY service_id",
                  (intentos, tz.tz_name(), FROZEN.date()))
        self.assertTrue(rows, "today's queue did try to attach an order")
        self.assertEqual([r for r in rows if r["n"] > 1], [])

    def test_a_reading_without_a_printed_dni_closes_on_the_name(self):
        # english-referral prints no DNI of its own: the reading says the NAME
        # matches — the exact phrase the real reader uses (ai_referral.py)
        perfil = next(p for p in sd._ORDENES if p["fname"] == "english-referral-1.jpg")
        blob = sd._ai_json_orden(perfil, sd.PACIENTES[0], 2)
        self.assertEqual(blob["campos"]["paciente"]["valor"], "John Miller")
        self.assertIsNone(blob["campos"]["dni"]["valor"])
        self.assertIn("el nombre del paciente coincide", blob["resumen"])
        self.assertNotIn("DNI coincide", blob["resumen"])
        # and a handwritten order with no DNI still names its holder, not the
        # patient the caller passed in
        perfil = next(p for p in sd._ORDENES if p["fname"] == "bad-handwriting-cardiologia-1.jpg")
        blob = sd._ai_json_orden(perfil, sd.PACIENTES[0], 3)
        self.assertEqual(blob["campos"]["paciente"]["valor"], "Camila Fernández")
        self.assertIsNone(blob["campos"]["dni"]["valor"])


class TestSiteTraffic(_Base):
    """«Visitas» showed 6 visits in 30 days next to a thousand patients with
    an appointment: the seed wrote no anonymous visit stream at all."""

    def test_the_report_sees_daily_visits_and_a_funnel_down_to_done(self):
        from app.modules.turnos import analytics_report
        sd.seed(force=True)
        _clock.freeze(self, FROZEN)
        rep = analytics_report.payload(30)
        self.assertGreaterEqual(rep["visitas"], 30 * sd._TRAFICO_MIN_VISITAS * 0.9)
        dias_sin = [d["day"] for d in rep["serie"][:-1] if d["visitas"] == 0]
        self.assertEqual(dias_sin, [], "a past day without visits")
        n = {p["paso"]: p["n"] for p in rep["embudo"]}
        camino = [n[k] for k in ("visit", "profile", "slots_shown", "submit", "done")]
        self.assertEqual(camino, sorted(camino, reverse=True), rep["embudo"])
        self.assertGreater(n["search"], 0)
        self.assertGreater(n["done"], 0)
        self.assertLess(n["done"], n["visit"] / 3)

    def test_a_reseed_replaces_the_stream_and_keeps_real_events(self):
        _q("INSERT INTO analytics_events (id, ts, type, route, jid) VALUES ('0190000000000abcdef0123', now(), "
           "'page_view', '/', 'demo0k2x9q1z7a')")
        self.addCleanup(_q, "DELETE FROM analytics_events WHERE id = %s", ("0190000000000abcdef0123",))
        sd.seed(force=True)
        antes = _n("analytics_events")
        # The export pages on ids that sort by time: the seed keeps that contract.
        self.assertEqual(_n("analytics_events", "id !~ '^[0-9a-f]{23}$'"), 0)
        self.assertEqual(_n("analytics_events", "ts > now() + interval '1 minute'"), 0)
        sd.seed(force=True)
        self.assertEqual(_n("analytics_events"), antes, "the second seed doubled the visits")
        self.assertEqual(_n("analytics_events", "id = %s", ("0190000000000abcdef0123",)), 1,
                         "a real visitor's event is never deleted")


class TestDemoCatalogOverlay(_Base):
    """The demo's own catalog overlay (2026-11-10, plan slice 1): the demo
    service open every day 8-20 and symptom capture on for the demo doctor —
    written by the seed, always rebuilt from the pack, idempotent."""

    def test_the_seed_writes_the_overlay_and_the_docs_validate(self):
        from app.patient_web import catalog
        sd.seed(force=True)
        svc = db_catalog.catalog_get("servicios", sd._SERVICIO_DEMO)
        med = db_catalog.catalog_get("medicos", sd._MEDICO_DESTACADO)
        self.assertIsNotNone(svc, "the demo service has its overlay row")
        self.assertIsNotNone(med, "the demo doctor has his overlay row")
        self.assertEqual(svc["doc"]["agenda"], sd._AGENDA_DEMO)
        self.assertEqual(svc["doc"]["horario"], sd._HORARIO_DEMO)
        self.assertIs(med["doc"].get("entrevista_habilitada"), True)
        for tipo, fila in (("servicios", svc), ("medicos", med)):
            _norm, err = catalog.validate(tipo, fila["doc"], is_new=False)
            self.assertIsNone(err, tipo)

    def test_the_overlay_follows_the_pack(self):
        from app.patient_web import catalog
        sd._asegurar_catalogo_demo()
        reales = [dict(it) for it in catalog.base_items("servicios")
                  if it.get(catalog.id_key("servicios")) == sd._SERVICIO_DEMO]
        self.assertEqual(len(reales), 1)
        orig = catalog.base_items
        editados = [dict(reales[0], nombre="Nuevo nombre del pack")]

        def fake_base(tipo):
            return editados if tipo == "servicios" else orig(tipo)

        with mock.patch.object(catalog, "base_items", side_effect=fake_base):
            self.assertTrue(sd._asegurar_catalogo_demo())
        fila = db_catalog.catalog_get("servicios", sd._SERVICIO_DEMO)
        self.assertEqual(fila["doc"]["nombre"], "Nuevo nombre del pack")
        self.assertEqual(fila["doc"]["agenda"], sd._AGENDA_DEMO,
                         "the demo's own edits survive a pack change")
        sd._asegurar_catalogo_demo()  # leave the row as the pack has it

    def test_a_second_pass_writes_nothing(self):
        sd._asegurar_catalogo_demo()
        with mock.patch.object(sd.db_catalog, "catalog_upsert") as up:
            self.assertFalse(sd._asegurar_catalogo_demo())
        up.assert_not_called()

    def test_a_doctor_overlay_on_an_unknown_center_goes_back_to_the_pack(self):
        # The live demo kept Carlos Wu on Pinamar's «caps-sur» from a
        # September edit: the panel showed «Centro sin nombre».
        from app.patient_web import catalog
        medico = next(m for m in catalog.base_items("medicos")
                      if m.get("id") != sd._MEDICO_DESTACADO and m.get("centro"))
        viejo = dict(medico, centro="caps-sur",
                     centros=[{"centro": "caps-sur", "franjas": [], "excepciones": []}])
        db_catalog.catalog_upsert("medicos", medico["id"], viejo)
        solo_panel = {"id": "panel-only-doc", "centro": "caps-sur"}
        db_catalog.catalog_upsert("medicos", "panel-only-doc", solo_panel)
        catalog.invalidate()
        self.addCleanup(db_catalog.catalog_delete_row, "medicos", "panel-only-doc")
        self.assertTrue(sd._asegurar_catalogo_demo())
        self.assertIsNone(db_catalog.catalog_get("medicos", medico["id"]),
                          "the stale overlay is dropped; the pack's row shows again")
        self.assertIsNotNone(db_catalog.catalog_get("medicos", "panel-only-doc"),
                             "a doctor that exists only in the panel is never deleted")
        self.assertIsNotNone(db_catalog.catalog_get("medicos", sd._MEDICO_DESTACADO))

    def test_a_failed_overlay_does_not_stop_the_seed(self):
        with mock.patch.object(sd, "_asegurar_catalogo_demo", side_effect=RuntimeError("catalog down")):
            sd.seed(force=True)
        self.assertTrue(sd.dia_cubierto(), "the day is seeded without the overlay")


class TestFeaturedAllDay(_Base):
    """The walkthrough never closes for the day (2026-11-10, plan slice 1):
    the featured patient always has a same-day appointment with the demo
    doctor after the current hour, plus a second approved one days out."""

    def _sofia_hoy(self):
        return _q("SELECT id, appointment_at, status, cancelled_at, assigned_doctor_id "
                  "FROM booking_requests WHERE dni=%s AND assigned_doctor_id=%s "
                  "AND (appointment_at AT TIME ZONE %s)::date = %s",
                  (sd.DEMO_PATIENT["dni"], sd._MEDICO_DESTACADO, tz.tz_name(), FROZEN.date()))

    def test_the_seed_leaves_her_day_open(self):
        sd.seed(force=True)
        hoy = self._sofia_hoy()
        self.assertEqual(len(hoy), 1)
        self.assertEqual(hoy[0]["status"], "aprobada")
        self.assertIsNone(hoy[0]["cancelled_at"])
        self.assertGreater(hoy[0]["appointment_at"], FROZEN)
        futuros = _q("SELECT count(*) AS n FROM booking_requests WHERE dni=%s "
                     "AND status='aprobada' AND appointment_at > %s AND cancelled_at IS NULL",
                     (sd.DEMO_PATIENT["dni"], FROZEN))[0]["n"]
        self.assertGreaterEqual(futuros, 2, "her account always has a future visit")

    def test_a_lapsed_slot_moves_to_the_next_free_agenda_slot(self):
        sd.seed(force=True)
        row = self._sofia_hoy()[0]
        _q("UPDATE booking_requests SET appointment_at=%s WHERE id=%s",
           (FROZEN - timedelta(hours=1), row["id"]))
        self.assertTrue(sd._asegurar_turno_destacado())
        moved = _q("SELECT appointment_at, status FROM booking_requests WHERE id=%s",
                   (row["id"],))[0]
        cuando = moved["appointment_at"].astimezone(tz.tzinfo())
        self.assertEqual(moved["status"], "aprobada")
        self.assertGreaterEqual(cuando, FROZEN + timedelta(minutes=10))
        self.assertEqual((cuando.hour * 60 + cuando.minute) % sd._AGENDA_DEMO["cada_min"], 0,
                         "an agenda slot")
        self.assertGreaterEqual(cuando.strftime("%H:%M"), "08:00")
        self.assertLess(cuando.strftime("%H:%M"), "20:00")

    def _mover(self, sid, cuando):
        """Put her appointment on that slot, parking whoever holds it (the
        UNIQUE (doctor, slot) index is exactly what the move would trip)."""
        _q("UPDATE booking_requests SET appointment_at=NULL "
           "WHERE assigned_doctor_id=%s AND appointment_at=%s AND id<>%s",
           (sd._MEDICO_DESTACADO, cuando, sid))
        _q("UPDATE booking_requests SET appointment_at=%s WHERE id=%s", (cuando, sid))

    def test_a_cancelled_appointment_is_restored_in_place(self):
        sd.seed(force=True)
        hoy = self._sofia_hoy()[0]
        # late in the day, with free slots before it: «in place» is her own
        # slot, not the first free one after the clock
        tarde = FROZEN.replace(hour=18, minute=20, second=0)
        self._mover(hoy["id"], tarde)
        hoy = dict(hoy, appointment_at=tarde)
        cardio = _q("SELECT id, appointment_at FROM booking_requests WHERE dni=%s "
                    "AND assigned_doctor_id='ruiz-sergio' AND appointment_at > %s",
                    (sd.DEMO_PATIENT["dni"], FROZEN))
        self.assertEqual(len(cardio), 1, "her second future visit, days out")
        _q("UPDATE booking_requests SET status='cancelada', cancelled_at=%s WHERE id IN (%s,%s)",
           (FROZEN, hoy["id"], cardio[0]["id"]))
        self.assertTrue(sd._asegurar_turno_destacado())
        for antes in (hoy, cardio[0]):
            after = _q("SELECT appointment_at, status, cancelled_at FROM booking_requests "
                       "WHERE id=%s", (antes["id"],))[0]
            self.assertEqual((after["status"], after["cancelled_at"]), ("aprobada", None))
            self.assertEqual(after["appointment_at"], antes["appointment_at"])

    def test_after_the_last_slot_she_stays_on_todays_list(self):
        # once the day's agenda is over she is neither moved to tomorrow (the
        # doctor lands on «Hoy») nor given a slot outside the agenda (decision
        # 2026-11-08): the appointment stays where it was, approved
        sd.seed(force=True)
        row = self._sofia_hoy()[0]
        noche = FROZEN.replace(hour=19, minute=50)
        ultimo = FROZEN.replace(hour=19, minute=40, second=0)
        self._mover(row["id"], ultimo)
        with mock.patch.object(sd, "_now", lambda: noche):
            self.assertFalse(sd._asegurar_turno_destacado(), "nothing to write")
            _q("UPDATE booking_requests SET status='cancelada', cancelled_at=%s WHERE id=%s",
               (noche, row["id"]))
            self.assertTrue(sd._asegurar_turno_destacado())
        after = _q("SELECT appointment_at, status, cancelled_at FROM booking_requests WHERE id=%s",
                   (row["id"],))[0]
        self.assertEqual((after["status"], after["cancelled_at"]), ("aprobada", None))
        self.assertEqual(after["appointment_at"], ultimo)

    def test_an_evening_seed_keeps_her_on_todays_list_inside_the_agenda(self):
        noche = FROZEN.replace(hour=21, minute=30)
        with mock.patch.object(sd, "_now", lambda: noche):
            sd.seed(force=True)
        hoy = self._sofia_hoy()
        self.assertEqual(len(hoy), 1)
        hora = hoy[0]["appointment_at"].astimezone(tz.tzinfo()).strftime("%H:%M")
        self.assertLess(hora, sd._AGENDA_DEMO["hasta"])
        self.assertGreaterEqual(hora, sd._AGENDA_DEMO["desde"])
        futuros = _q("SELECT count(*) AS n FROM booking_requests WHERE dni=%s "
                     "AND status='aprobada' AND appointment_at > %s",
                     (sd.DEMO_PATIENT["dni"], noche))[0]["n"]
        self.assertEqual(futuros, 1, "her account still has a future visit to cancel")

    def test_a_taken_slot_of_another_service_does_not_stop_todays_repair(self):
        # each row is its own transaction: her cardiology visit cannot come
        # back (somebody holds its slot now), today's still does
        sd.seed(force=True)
        hoy = self._sofia_hoy()[0]
        cardio = _q("SELECT id, appointment_at FROM booking_requests WHERE dni=%s "
                    "AND assigned_doctor_id='ruiz-sergio' AND appointment_at > %s",
                    (sd.DEMO_PATIENT["dni"], FROZEN))[0]
        _q("UPDATE booking_requests SET status='cancelada', cancelled_at=%s WHERE id IN (%s,%s)",
           (FROZEN, hoy["id"], cardio["id"]))
        _q("INSERT INTO booking_requests (dni, contact, service_id, status, assigned_doctor_id, "
           "appointment_at) VALUES ('70010000',%s,'cardiologia-sampleton-hospital','aprobada',"
           "'ruiz-sergio',%s)", ("p" + OTHER, cardio["appointment_at"]))
        self.assertTrue(sd._asegurar_turno_destacado())
        estados = {r["id"]: r["status"] for r in _q(
            "SELECT id, status FROM booking_requests WHERE id IN (%s,%s)", (hoy["id"], cardio["id"]))}
        self.assertEqual(estados, {hoy["id"]: "aprobada", cardio["id"]: "cancelada"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
