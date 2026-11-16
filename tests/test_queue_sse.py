"""Behavioral proof for the durable, cross-worker day-queue event feed."""

import asyncio
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import tz as _tz  # noqa: E402
from app.modules.turnos import queue_events  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_queue_sse",
    seed=lambda: db_booking.create_booking_request(
        "00000000", "Semilla", "Eventos", "1990-01-01", None,
        "cardiologia", "hospital-pinamar", None),
)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _truncate():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE day_queue_events, passkey_challenges, attendance_events, booking_requests CASCADE")


def _approved_request(center="centro-a", doctor_id="med-1", doctor_name="Ana Medica"):
    sid = db_booking.create_booking_request(
        "30000001", "Paciente", "Prueba", "1990-01-01", None,
        "cardiologia", center, None)
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE booking_requests
               SET status='aprobada', appointment_at=%s,
                   assigned_doctor_id=%s, assigned_doctor=%s
               WHERE id=%s""",
            (datetime.now(_tz.tzinfo()) - timedelta(minutes=5), doctor_id,
             doctor_name, sid),
        )
    return sid


class QueueSsePureContractTest(unittest.TestCase):
    def test_frame_uses_reconnect_cursor_and_never_contains_scope_metadata(self):
        frame = queue_events._frame("queue-update", {"day": "2026-09-03"}, 17)
        self.assertEqual(
            frame,
            b'id: 17\nevent: queue-update\ndata: {"day":"2026-09-03"}\n\n',
        )
        self.assertNotIn(b"centro", frame)
        self.assertNotIn(b"medico", frame)

    def test_center_and_legacy_name_only_doctor_scope_use_the_domain_rule(self):
        name_only = {
            "center_slug": "centro-a",
            "assigned_doctor_id": None,
            "assigned_doctor": "Medica Ana",
        }
        self.assertTrue(queue_events._visible(
            {"rol": "medico", "medico_id": "med-1", "medico_nombre": "Ana Medica",
             "centros_permitidos": {"centro-a"}}, name_only))
        self.assertFalse(queue_events._visible(
            {"rol": "medico", "medico_id": "med-1", "medico_nombre": "Ana Medica",
             "centros_permitidos": {"centro-b"}}, name_only))
        self.assertFalse(queue_events._visible(
            {"rol": "medico", "medico_id": "med-2", "medico_nombre": "Bruno Medico",
             "centros_permitidos": None}, name_only))


class QueueSseListenerFailureTest(unittest.TestCase):
    """The LISTEN connection is its own non-pooled connection; its failures are the repo's
    «database unavailable», never a traceback from inside the stream."""

    def _open_with(self, error):
        import psycopg
        from unittest import mock

        async def refuse(*_a, **_k):
            raise error

        with mock.patch.object(db, "_db_url", return_value="postgresql://x@127.0.0.1:1/x"), \
                mock.patch.object(psycopg.AsyncConnection, "connect", side_effect=refuse):
            return asyncio.run(queue_events._Listener.open())

    def test_a_connect_timeout_is_db_unavailable(self):
        import psycopg
        with self.assertRaises(db.DbUnavailable):
            self._open_with(psycopg.errors.ConnectionTimeout("connection timeout expired"))

    def test_a_refused_connection_is_db_unavailable(self):
        import psycopg
        with self.assertRaises(db.DbUnavailable):
            self._open_with(psycopg.OperationalError("connection refused"))

    def test_an_error_that_is_not_an_outage_is_not_swallowed(self):
        with self.assertRaises(ZeroDivisionError):
            self._open_with(ZeroDivisionError("bug"))

    def test_a_close_that_fails_on_the_error_path_does_not_replace_the_error(self):
        # LISTEN fails (the connection is lost) and closing the half-open connection fails too:
        # the answer is still the 503 of the first failure, not whatever the close raised.
        import psycopg
        from unittest import mock

        class _Conn:
            async def close(self):
                raise RuntimeError("close blew up")

        async def connect(*_a, **_k):
            return _Conn()

        async def lost(*_a, **_k):
            raise psycopg.OperationalError("server closed the connection unexpectedly")

        with mock.patch.object(db, "_db_url", return_value="postgresql://x@127.0.0.1:1/x"), \
                mock.patch.object(psycopg.AsyncConnection, "connect", side_effect=connect), \
                mock.patch.object(queue_events.sqlcore, "run_async", side_effect=lost), \
                self.assertLogs("uvicorn.error", level="WARNING") as logged:
            with self.assertRaises(db.DbUnavailable):
                asyncio.run(queue_events._Listener.open())
        self.assertIn("RuntimeError", "\n".join(logged.output))
        self.assertNotIn("close blew up", "\n".join(logged.output))

    def test_a_close_that_fails_while_the_cursor_read_fails_keeps_the_503(self):
        from unittest import mock

        class _Listener:
            async def close(self):
                raise RuntimeError("close blew up")

        async def opened():
            return _Listener()

        with mock.patch.object(queue_events._Listener, "open", side_effect=opened), \
                mock.patch.object(db_slots, "day_queue_event_high_water",
                                  side_effect=db.DbUnavailable("pool agotado")), \
                self.assertLogs("uvicorn.error", level="WARNING"):
            with self.assertRaises(db.DbUnavailable):
                asyncio.run(queue_events.open_feed(None))

    def _drain(self, error, cursor=5):
        from unittest import mock

        class _Listener:
            closed = False

            async def wait(self, timeout):
                raise error

            async def close(self):
                self.closed = True

        async def never():
            return False

        async def drain():
            listener = _Listener()
            self.listener = listener
            frames = []
            with mock.patch.object(db_slots, "day_queue_events_after", return_value=[]):
                async for frame in queue_events.stream(listener, cursor, {}, never):
                    frames.append(frame)
            return frames

        return asyncio.run(drain())

    def test_a_database_lost_after_the_stream_began_ends_it_cleanly(self):
        import psycopg

        with self.assertLogs("uvicorn.error", level="WARNING") as logged:
            frames = self._drain(psycopg.OperationalError("server closed the connection unexpectedly"))
        self.assertEqual(len(frames), 1)      # only `ready`; no exception escapes
        self.assertTrue(self.listener.closed)
        self.assertIn("day-queue", "\n".join(logged.output))

    def test_an_error_that_is_not_an_outage_keeps_its_normal_path(self):
        # A deadlock or a protocol violation is the database answering, not the database gone.
        import psycopg

        for error in (psycopg.errors.DeadlockDetected("deadlock detected"),
                      psycopg.errors.ProtocolViolation("bad message")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(psycopg.OperationalError):
                    self._drain(error)
                self.assertTrue(self.listener.closed)

    def test_a_pool_outage_in_a_replay_read_ends_the_stream_cleanly(self):
        from unittest import mock

        class _Listener:
            async def wait(self, timeout):
                return None

            async def close(self):
                pass

        async def never():
            return False

        async def drain():
            frames = []
            with mock.patch.object(db_slots, "day_queue_events_after",
                                   side_effect=db.DbUnavailable("pool agotado")):
                async for frame in queue_events.stream(_Listener(), 5, {}, never):
                    frames.append(frame)
            return frames

        with self.assertLogs("uvicorn.error", level="WARNING"):
            self.assertEqual(len(asyncio.run(drain())), 1)

    def test_the_first_read_happens_before_the_response_starts(self):
        # The database dropping between the LISTEN connect and the first read is a 503 too: the
        # read that sets the cursor is part of opening the feed, and the listener is not leaked.
        from unittest import mock

        class _Listener:
            closed = False

            async def close(self):
                self.closed = True

        listener = _Listener()

        async def opened():
            return listener

        with mock.patch.object(queue_events._Listener, "open", side_effect=opened), \
                mock.patch.object(db_slots, "day_queue_event_high_water",
                                  side_effect=db.DbUnavailable("pool agotado")):
            with self.assertRaises(db.DbUnavailable):
                asyncio.run(queue_events.open_feed(None))
        self.assertTrue(listener.closed)

    def test_a_reconnect_cursor_needs_no_read_to_open_the_feed(self):
        from unittest import mock

        class _Listener:
            async def close(self):
                pass

        async def opened():
            return _Listener()

        with mock.patch.object(queue_events._Listener, "open", side_effect=opened), \
                mock.patch.object(db_slots, "day_queue_event_high_water",
                                  side_effect=AssertionError("must not read")):
            _, cursor = asyncio.run(queue_events.open_feed("41"))
        self.assertEqual(cursor, 41)


class QueueSseDatabaseTest(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres unavailable")
        _truncate()

    def tearDown(self):
        if HAVE_DB:
            _truncate()

    def test_attendance_commits_a_non_phi_replay_row_and_post_commit_notify(self):
        sid = _approved_request()
        async def exercise():
            listener = await queue_events._Listener.open()
            try:
                self.assertEqual(db_slots.day_queue_event_high_water(), 0)
                row = await asyncio.to_thread(
                    db_booking.mark_attendance, sid, "presente", "operador-prueba",
                )
                event = db_slots.day_queue_events_after(0)[0]
                notification = await listener.wait(2)
                return row, event, notification
            finally:
                await listener.close()

        row, event, notification = asyncio.run(exercise())

        self.assertEqual(row["id"], sid)
        self.assertEqual(event["center_slug"], "centro-a")
        self.assertEqual(event["assigned_doctor_id"], "med-1")
        self.assertEqual(event["assigned_doctor"], "Ana Medica")
        self.assertEqual(notification.channel, "turnos_day_queue")
        self.assertEqual(int(notification.payload), event["id"])
        self.assertNotIn("dni", event)
        self.assertNotIn("attendance", event)
        self.assertNotIn("operator", event)

    def test_second_process_replays_the_same_committed_event(self):
        sid = _approved_request(center="centro-b", doctor_id="med-2", doctor_name="Bruno Medico")
        db_booking.mark_attendance(sid, "ausente", "operador-prueba")
        env = dict(os.environ)
        env["DATABASE_URL"] = _db.test_url
        script = (
            "import json; from app.core import db_slots; "
            "rows=db_slots.day_queue_events_after(0); "
            "print(json.dumps([{'id':r['id'],"
            "'center_slug':r['center_slug'],'assigned_doctor_id':r['assigned_doctor_id']} for r in rows]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=20, check=True)
        replayed = json.loads(result.stdout)
        self.assertEqual(replayed, [{
            "id": db_slots.day_queue_event_high_water(),
            "center_slug": "centro-b", "assigned_doctor_id": "med-2",
        }])

    def test_queue_migration_downgrades_and_upgrades_its_owned_table(self):
        from alembic import command
        from alembic.script import ScriptDirectory
        import psycopg

        # Exercise 0009 at its real historical revision in an isolated DB.
        # Later forward-only revisions must not be downgraded or stamped away
        # merely to reach this table's deliberately reversible migration.
        name = _dbtest.run_scoped("sana_turnos_test_queue_migration")

        def drop():
            with psycopg.connect(_db.maint_url, autocommit=True) as conn:
                conn.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)))

        with psycopg.connect(_db.maint_url, autocommit=True) as conn:
            conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(
                psycopg.sql.Identifier(name)))
        self.addCleanup(drop)
        parts = urlsplit(_db.test_url)
        url = urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))
        cfg = db_schema._alembic_config(url)
        revision = "0009_day_queue_event_outbox"
        command.upgrade(cfg, revision)
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO booking_requests (dni, first_name, last_name)"
                        " VALUES ('30000001', 'Synthetic', 'Migration') RETURNING id")
            sid = cur.fetchone()[0]
            cur.execute("SELECT row_to_json(b) FROM booking_requests b WHERE id=%s", (sid,))
            original = cur.fetchone()[0]

        command.downgrade(cfg, "0008_normalized_emr_observations")
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.day_queue_events')")
            self.assertIsNone(cur.fetchone()[0])
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0008_normalized_emr_observations")
            cur.execute("SELECT row_to_json(b) FROM booking_requests b WHERE id=%s", (sid,))
            self.assertEqual(cur.fetchone()[0], original)

        command.upgrade(cfg, revision)
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.day_queue_events')")
            self.assertEqual(cur.fetchone()[0], "day_queue_events")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], revision)
            cur.execute("SELECT row_to_json(b) FROM booking_requests b WHERE id=%s", (sid,))
            self.assertEqual(cur.fetchone()[0], original)
            cur.execute("INSERT INTO day_queue_events (appointment_day) VALUES ('2026-09-14') RETURNING id")
            self.assertGreater(cur.fetchone()[0], 0)

    def test_refused_downgrade_through_forward_only_migration_leaves_head_intact(self):
        # env.py wraps the WHOLE command in one transaction (context.
        # begin_transaction(), no transaction_per_migration) and Postgres DDL
        # is transactional, so 0008's refusing downgrade must roll back the
        # 0010/0009 table drops and the version rewrite together.
        from alembic import command
        from alembic.script import ScriptDirectory
        import psycopg

        cfg = db_schema._alembic_config(_db.test_url)
        head = ScriptDirectory.from_config(cfg).get_current_head()
        sid = _approved_request()
        self.addCleanup(command.upgrade, cfg, "head")
        # Non-empty owned tables are essential: recreating empty tables after
        # a partial commit must not masquerade as atomic rollback. A consumed
        # synthetic challenge also protects persisted replay state, not only
        # the existence of the table.
        with psycopg.connect(_db.test_url) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO day_queue_events (appointment_day, center_slug)"
                " VALUES ('2026-09-10', 'synthetic-rollback-center')")
            cur.execute(
                "INSERT INTO passkey_challenges"
                " (challenge_digest, purpose, expires_at, consumed_at)"
                " VALUES (%s, 'login', '2026-09-11T00:00:00Z', '2026-09-10T00:00:00Z')",
                (b'rollback-proof-fixture-digest-0001',))
            cur.execute("SELECT row_to_json(b) FROM booking_requests b WHERE id=%s", (sid,))
            original_booking = cur.fetchone()[0]
            cur.execute("SELECT row_to_json(e) FROM day_queue_events e ORDER BY id")
            original_events = cur.fetchall()
            cur.execute("SELECT row_to_json(c) FROM passkey_challenges c ORDER BY challenge_digest")
            original_challenges = cur.fetchall()
            self.assertTrue(original_events)
            self.assertTrue(original_challenges)

        with self.assertRaises(NotImplementedError):
            command.downgrade(cfg, "0007_existing_install_shape")

        with psycopg.connect(_db.test_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], head)
            cur.execute("SELECT to_regclass(%s)", ("public.day_queue_events",))
            self.assertEqual(cur.fetchone()[0], "day_queue_events")
            cur.execute("SELECT to_regclass(%s)", ("public.passkey_challenges",))
            self.assertEqual(cur.fetchone()[0], "passkey_challenges")
            cur.execute("SELECT row_to_json(b) FROM booking_requests b WHERE id=%s", (sid,))
            self.assertEqual(cur.fetchone()[0], original_booking)
            cur.execute("SELECT row_to_json(e) FROM day_queue_events e ORDER BY id")
            self.assertEqual(cur.fetchall(), original_events)
            cur.execute("SELECT row_to_json(c) FROM passkey_challenges c ORDER BY challenge_digest")
            self.assertEqual(cur.fetchall(), original_challenges)
