"""test_db_chat_store.py — the chat statements of `core/db.py` against a real Postgres.

Stage 4 of the SQLAlchemy Core move converts the chat group of `core/db.py`: the multi-turn
messages (`chat_save_message`, `chat_history`, `chat_history_multi`, the first-message and
new-session probes, the spend-guard count, the admin «Chats» list and the nightly export),
the proposal / disclosure metadata, the cross-session memory (`chat_summary_*`,
`chat_compaction_*`), the web -> WhatsApp hand-off codes (`chat_handoff_*`) and the phone ->
account resolution (`user_by_phone`, `user_by_handoff_phone`, `linked_whatsapp_claves`,
`web_claves_for_dni`). This file pins each of those functions row for row — returned shapes,
ordering, windows, every branch, the advisory lock of the proposal — so the conversion is
proven to change nothing. It was written and run green on the hand-written SQL first, and
passes unedited on Core.
"""
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_chat  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_chat_store")

UTC = timezone.utc
NOW = datetime.now(UTC)


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


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE chat_messages, chat_summaries, chat_compactions, chat_handoffs "
                        "RESTART IDENTITY")
            cur.execute("DELETE FROM users")


def _say(canal, clave, role, text, ago_minutes=0, now=None, **extra):
    """One stored message `ago_minutes` before `now` (default NOW, the import time); returns its id.

    A window the database measures from its own now() (`hours=`) needs `now=datetime.now(UTC)`: in
    the full run more than half an hour can pass between importing this module and the test."""
    message = {"role": role, "content": text, **extra}
    db_chat.chat_save_message(canal, clave, message)
    row = _sql("SELECT max(id) AS id FROM chat_messages")[0]
    _sql("UPDATE chat_messages SET created=%s WHERE id=%s",
         ((now or NOW) - timedelta(minutes=ago_minutes), row["id"]))
    return row["id"]


def _roles(canal, clave):
    return [r["role"] for r in _sql("SELECT role FROM chat_messages WHERE channel=%s AND key=%s "
                                    "ORDER BY id", (canal, clave))]


def _user(dni, contact):
    _dbtest.ensure_user(dni)
    _sql("UPDATE users SET contact=%s WHERE dni=%s", (contact, dni))


# ---- messages --------------------------------------------------------------------

class TestMessages(DbCase):
    def test_save_stores_role_and_the_whole_message(self):
        message = {"role": "assistant", "content": "¿Qué día te queda bien? ñandú",
                   "tool_calls": [{"id": "1", "n": 2}]}
        self.assertIsNone(db_chat.chat_save_message("web", "dni:30111222", message))
        row = _sql("SELECT channel, key, role, content, created FROM chat_messages")[0]
        self.assertEqual((row["channel"], row["key"], row["role"]), ("web", "dni:30111222", "assistant"))
        self.assertEqual(row["content"], message)
        self.assertIsInstance(row["created"], datetime)

    def test_save_without_a_role_stores_an_empty_one(self):
        db_chat.chat_save_message("web", "k", {"content": "x"})
        db_chat.chat_save_message("web", "k", {"role": None, "content": "y"})
        self.assertEqual(_roles("web", "k"), ["", ""])

    def test_history_is_chronological_and_keeps_the_conversation_roles_only(self):
        _say("web", "k", "user", "uno", 50)
        _say("web", "k", "assistant", "dos", 40)
        _say("web", "k", "tool", "tres", 30)
        _say("web", "k", "system", "cuatro", 20)
        _say("web", "k", "booking_proposal", "propuesta", 15)
        _say("web", "k", "ai_disclosure", "aviso", 14)
        _say("web", "k", "system", "AI disclosure delivered v1", 13)
        _say("web", "k", "user", "cinco", 10)
        _say("web", "other", "user", "ajeno", 5)
        _say("whatsapp", "k", "user", "otro canal", 5)
        got = db_chat.chat_history("web", "k")
        self.assertEqual([m["content"] for m in got], ["uno", "dos", "tres", "cuatro", "cinco"])
        self.assertEqual(got[0], {"role": "user", "content": "uno"})
        self.assertEqual([m["content"] for m in db_chat.chat_history("web", "k", limit=2)], ["cuatro", "cinco"])
        self.assertEqual(db_chat.chat_history("web", "nobody"), [])

    def test_history_multi_merges_pairs_chronologically(self):
        _say("web", "dni:1", "user", "w1", 50)
        _say("whatsapp", "tel:1", "assistant", "t1", 40)
        _say("web", "dni:1", "assistant", "w2", 30)
        _say("web", "dni:1", "ai_disclosure", "hidden", 25)
        _say("whatsapp", "tel:1", "system", "AI disclosure delivered v1", 20)
        _say("web", "dni:2", "user", "ajeno", 10)
        pairs = [("web", "dni:1"), ("whatsapp", "tel:1"), ("web", "dni:1"), None, ()]
        rows = db_chat.chat_history_multi(pairs)
        self.assertEqual([(r["canal"], r["contenido"]["content"]) for r in rows],
                         [("web", "w1"), ("whatsapp", "t1"), ("web", "w2")])
        self.assertEqual(set(rows[0]), {"canal", "contenido", "created"})
        self.assertIsInstance(rows[0]["created"], datetime)
        self.assertEqual([r["contenido"]["content"] for r in db_chat.chat_history_multi(pairs, limit=2)],
                         ["t1", "w2"])
        self.assertEqual(db_chat.chat_history_multi([]), [])
        self.assertEqual(db_chat.chat_history_multi(None), [])
        self.assertEqual(db_chat.chat_history_multi([None, ()]), [])

    def test_metadata_returns_the_latest_of_its_kind(self):
        self.assertIsNone(db_chat.chat_metadata_get("web", "k", "booking_proposal"))
        _say("web", "k", "booking_proposal", "first")
        _say("web", "k", "booking_proposal", "second")
        _say("web", "k", "ai_disclosure", "d")
        _say("web", "other", "booking_proposal", "ajeno")
        self.assertEqual(db_chat.chat_metadata_get("web", "k", "booking_proposal")["content"], "second")
        self.assertEqual(db_chat.chat_metadata_get("web", "k", "ai_disclosure")["content"], "d")
        self.assertIsNone(db_chat.chat_metadata_get("web", "k", "health_profile_proposal"))
        with self.assertRaises(ValueError):
            db_chat.chat_metadata_get("web", "k", "user")

    def test_disclosure_sent_recognizes_both_markers(self):
        self.assertIs(db_chat.chat_disclosure_sent("web", "k"), False)
        _say("web", "k", "user", "AI disclosure delivered v1")  # a patient typing it is not the marker
        _say("web", "k", "system", "otra cosa")
        self.assertIs(db_chat.chat_disclosure_sent("web", "k"), False)
        _say("web", "k", "system", "AI disclosure delivered v1")
        self.assertIs(db_chat.chat_disclosure_sent("web", "k"), True)
        self.assertIs(db_chat.chat_disclosure_sent("web", "other"), False)
        _say("web", "other", "ai_disclosure", "d")
        self.assertIs(db_chat.chat_disclosure_sent("web", "other"), True)
        self.assertIs(db_chat.chat_disclosure_sent("whatsapp", "other"), False)

    def test_new_session_marker_is_the_latest_marker_time(self):
        self.assertIsNone(db_chat.chat_new_session_marker_at("web", "k"))
        _say("web", "k", "system", "x", 60, marker="new_session")
        newest = _say("web", "k", "system", "y", 30, marker="new_session")
        _say("web", "k", "system", "z", 5, marker="something_else")
        _say("web", "other", "system", "w", 1, marker="new_session")
        at = db_chat.chat_new_session_marker_at("web", "k")
        self.assertEqual(at, _sql("SELECT created FROM chat_messages WHERE id=%s", (newest,))[0]["created"])

    def test_first_user_message_and_the_restart_boundary(self):
        self.assertEqual(db_chat.chat_first_user_message("web", "k"), "")
        _say("web", "k", "assistant", "hola", 100)
        _say("web", "k", "user", "  Quiero turno  ", 90)
        _say("web", "k", "user", "segundo", 80)
        _say("web", "k", "user", "tercero", 20)
        self.assertEqual(db_chat.chat_first_user_message("web", "k"), "Quiero turno")
        since = NOW - timedelta(minutes=85)
        self.assertEqual(db_chat.chat_first_user_message("web", "k", since=since), "segundo")
        self.assertEqual(db_chat.chat_first_user_message("web", "k", since=NOW), "")
        self.assertEqual(db_chat.chat_first_user_message("web", "other"), "")

    def test_first_user_message_without_text_is_empty(self):
        db_chat.chat_save_message("web", "k", {"role": "user"})
        self.assertEqual(db_chat.chat_first_user_message("web", "k"), "")

    def test_count_user_messages_inside_the_window(self):
        now = datetime.now(UTC)
        _say("web", "k", "user", "a", 30, now=now)
        _say("web", "k", "user", "b", 60 * 5, now=now)
        _say("web", "k", "user", "c", 60 * 30, now=now)
        _say("web", "k", "assistant", "d", 5, now=now)
        _say("web", "other", "user", "e", 5, now=now)
        self.assertEqual(db_chat.chat_count_user_messages("web", "k"), 2)
        self.assertEqual(db_chat.chat_count_user_messages("web", "k", hours=1), 1)
        self.assertEqual(db_chat.chat_count_user_messages("web", "k", hours=24 * 7), 3)
        self.assertEqual(db_chat.chat_count_user_messages("web", "k", hours=24 * 99), 3)  # capped at a week
        self.assertEqual(db_chat.chat_count_user_messages("web", "k", hours="x"), 2)  # unparsable: 24
        self.assertEqual(db_chat.chat_count_user_messages("web", "k", hours=0), 2)  # 0 -> default 24
        self.assertEqual(db_chat.chat_count_user_messages("web", "nobody"), 0)


class TestProposalConsumption(DbCase):
    def test_consumes_the_latest_proposal_and_retires_the_older_ones(self):
        _say("web", "k", "booking_proposal", "old")
        _say("web", "k", "booking_proposal", "new")
        _say("web", "k", "user", "sí")
        _say("web", "other", "booking_proposal", "ajena")
        got = db_chat.chat_take_booking_proposal("web", "k")
        self.assertEqual(got["content"], "new")
        self.assertEqual(_roles("web", "k"), ["booking_consumed", "booking_consumed", "user"])
        self.assertEqual(_roles("web", "other"), ["booking_proposal"])  # another conversation untouched
        self.assertIsNone(db_chat.chat_take_booking_proposal("web", "k"))  # exactly once

    def test_nothing_to_consume(self):
        self.assertIsNone(db_chat.chat_take_booking_proposal("web", "k"))

    def test_it_waits_for_the_conversations_advisory_lock(self):
        """The key `booking_proposal:<canal>:<clave>` is what serializes two replies."""
        _say("web", "k", "booking_proposal", "p")
        result = {}
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("booking_proposal:web:k",))
            thread = threading.Thread(
                target=lambda: result.update(row=db_chat.chat_take_booking_proposal("web", "k")))
            thread.start()
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "must wait for the lock of its own key")
            other.commit()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["row"]["content"], "p")

    def test_it_does_not_wait_for_another_conversations_lock(self):
        _say("web", "k", "booking_proposal", "p")
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("booking_proposal:web:elsewhere",))
            result = {}
            thread = threading.Thread(
                target=lambda: result.update(row=db_chat.chat_take_booking_proposal("web", "k")))
            thread.start()
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(result["row"]["content"], "p")


class TestAdoptAndExport(DbCase):
    def test_adopt_copies_the_anonymous_history_once(self):
        _say("web", "ip:1.2.3.4", "user", "hola", 30, extra=1)
        _say("web", "ip:1.2.3.4", "assistant", "¿en qué te ayudo?", 20)
        _say("web", "ip:1.2.3.4", "tool", "t", 10)
        self.assertIsNone(db_chat.chat_adopt_history("web", "ip:1.2.3.4", "dni:30111222"))
        copied = _sql("SELECT role, content FROM chat_messages WHERE key=%s ORDER BY id", ("dni:30111222",))
        self.assertEqual([(r["role"], r["content"]["content"]) for r in copied],
                         [("user", "hola"), ("assistant", "¿en qué te ayudo?"), ("tool", "t")])
        self.assertEqual(copied[0]["content"]["extra"], 1)
        self.assertEqual(len(_roles("web", "ip:1.2.3.4")), 3)  # the origin stays
        db_chat.chat_adopt_history("web", "ip:1.2.3.4", "dni:30111222")  # a second time: nothing
        self.assertEqual(len(_roles("web", "dni:30111222")), 3)

    def test_adopt_never_overwrites_an_existing_conversation(self):
        _say("web", "ip:1", "user", "viejo")
        _say("web", "dni:1", "user", "propio")
        db_chat.chat_adopt_history("web", "ip:1", "dni:1")
        self.assertEqual([m["content"] for m in db_chat.chat_history("web", "dni:1")], ["propio"])

    def test_adopt_ignores_degenerate_arguments(self):
        _say("web", "ip:1", "user", "x")
        for args in (("web", None, "dni:1"), ("web", "ip:1", None), ("web", "ip:1", "ip:1"), ("web", "", "dni:1")):
            db_chat.chat_adopt_history(*args)
        self.assertEqual(_sql("SELECT count(*) AS n FROM chat_messages")[0]["n"], 1)
        db_chat.chat_adopt_history("web", "ip:empty", "dni:1")  # an origin without messages
        self.assertEqual(_roles("web", "dni:1"), [])

    def test_transcripts_group_by_conversation_in_order(self):
        _say("web", "b", "user", "b1", 30)
        _say("web", "a", "user", "a1", 50)
        _say("web", "a", "assistant", "a2", 40)
        _say("whatsapp", "a", "user", "w1", 20)
        _say("web", "old", "user", "viejo", 60 * 30)
        got = db_chat.chat_transcripts()
        self.assertEqual([(c["canal"], c["clave"]) for c in got],
                         [("web", "a"), ("web", "b"), ("whatsapp", "a")])
        self.assertEqual(set(got[0]), {"canal", "clave", "mensajes"})
        self.assertEqual([(m["rol"], m["contenido"]["content"]) for m in got[0]["mensajes"]],
                         [("user", "a1"), ("assistant", "a2")])
        self.assertEqual(set(got[0]["mensajes"][0]), {"rol", "contenido", "created"})
        stamp = got[0]["mensajes"][0]["created"]
        self.assertIsInstance(stamp, str)  # ISO text, not a datetime
        self.assertEqual(datetime.fromisoformat(stamp), NOW - timedelta(minutes=50))

    def test_transcripts_window_and_whole_conversations(self):
        now = datetime.now(UTC)
        _say("web", "k", "user", "viejo", 60 * 5, now=now)
        _say("web", "k", "assistant", "nuevo", 10, now=now)
        _say("web", "z", "user", "z", 60 * 30, now=now)
        got = db_chat.chat_transcripts(hours=1)
        self.assertEqual([(c["clave"], len(c["mensajes"])) for c in got], [("k", 2)])
        self.assertEqual({c["clave"] for c in db_chat.chat_transcripts(hours=24 * 30)}, {"k", "z"})
        self.assertEqual({c["clave"] for c in db_chat.chat_transcripts(hours="x")}, {"k"})  # 24h
        self.assertEqual(db_chat.chat_transcripts(hours=1000000)[-1]["clave"], "z")  # capped at 30 days -> z (30h) is in


class TestAdminList(DbCase):
    def test_lists_conversations_with_their_counts_and_first_message(self):
        _say("web", "dni:1", "assistant", "hola", 100)
        _say("web", "dni:1", "user", " primera ", 90)
        _say("web", "dni:1", "user", "segunda", 80)
        _say("whatsapp", "tel:2", "user", "otra", 10)
        _say("web", "dni:3", "assistant", "solo asistente", 5)
        got = db_chat.chat_conversations()
        self.assertEqual([(r["channel"], r["key"]) for r in got],
                         [("web", "dni:3"), ("whatsapp", "tel:2"), ("web", "dni:1")])
        first = got[2]
        self.assertEqual((first["user_messages"], first["messages"], first["first_message"]), (2, 3, "primera"))
        self.assertEqual(set(first), {"channel", "key", "started", "last_at", "user_messages",
                                      "messages", "first_message"})
        self.assertLess(first["started"], first["last_at"])
        self.assertEqual((got[0]["user_messages"], got[0]["messages"], got[0]["first_message"]), (0, 1, ""))

    def test_synthetic_smoke_sessions_never_reach_the_list(self):
        _say("web", "ip:1.2.3.4:chat:smoke_demo_real_chat_ab12", "user", "picazon", 20)
        _say("web", "dni:9:chat:smoke_demo_real_chat_cd34", "user", "picazon", 15)
        _say("web", "ip:1.2.3.4:chat:abc", "user", "real", 10)
        _say("web", "ip:1.2.3.4:chat:smokeXdemoY_1", "user", "otro", 5)
        with _env.patched_env(TURNOS_DEMO_MODE="1"):
            self.assertEqual([r["key"] for r in db_chat.chat_conversations()],
                             ["ip:1.2.3.4:chat:smokeXdemoY_1", "ip:1.2.3.4:chat:abc"])

    def test_outside_the_demo_a_client_chosen_smoke_prefix_hides_nothing(self):
        _say("web", "ip:1.2.3.4:chat:smoke_demo_x", "user", "visible", 5)
        with _env.patched_env(TURNOS_DEMO_MODE="0"):
            self.assertEqual([r["key"] for r in db_chat.chat_conversations()], ["ip:1.2.3.4:chat:smoke_demo_x"])

    def test_the_demo_smoke_names_its_chat_with_the_marked_prefix(self):
        with open(os.path.join(ROOT, "scripts", "smoke_demo.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('"' + db_chat.SYNTHETIC_SESSION_PREFIX + 'real_chat_"', source)

    def test_limit_and_days_bound_the_list(self):
        for n in range(5):
            _say("web", f"k{n}", "user", f"m{n}", 60 * 24 * n + 1)
        self.assertEqual([r["key"] for r in db_chat.chat_conversations(limit=2)], ["k0", "k1"])
        self.assertEqual([r["key"] for r in db_chat.chat_conversations(days=2)], ["k0", "k1"])
        self.assertEqual(len(db_chat.chat_conversations(limit=0)), 5)  # 0 -> default 200
        self.assertEqual(len(db_chat.chat_conversations(limit=-5)), 1)  # clamped to 1
        self.assertEqual(len(db_chat.chat_conversations(limit="x")), 5)  # unparsable -> defaults
        self.assertEqual(len(db_chat.chat_conversations(limit=10 ** 9)), 5)
        self.assertEqual(db_chat.chat_conversations(days=0)[0]["key"], "k0")  # 0 -> 90 days

    def test_excluded_channels_leave_before_the_limit(self):
        # M-4: a reader who may not see the interview still gets `limit` rows of the rest.
        _say("interview", "interview:1", "user", "dolor", 1)
        _say("caso", "caso:t", "user", "fiebre", 2)
        _say("web", "w", "user", "turno", 3)
        self.assertEqual([r["key"] for r in db_chat.chat_conversations(
            limit=1, exclude_channels=("interview", "caso"))], ["w"])
        self.assertEqual([r["key"] for r in db_chat.chat_conversations(limit=1)], ["interview:1"])

    def test_a_non_object_first_message_is_shown_as_text(self):
        _sql("INSERT INTO chat_messages (channel, key, role, content) VALUES ('web','k','user','\"texto plano\"'::jsonb)")
        self.assertEqual(db_chat.chat_conversations()[0]["first_message"], "texto plano")


# ---- memory between sessions ------------------------------------------------------------

class TestSummaries(DbCase):
    def test_latest_cover_and_recent_summaries(self):
        self.assertIsNone(db_chat.chat_summaries_latest_covers("p1"))
        self.assertEqual(db_chat.chat_summaries_recent("p1"), [])
        t1, t2, t3, t4 = (NOW - timedelta(days=d) for d in (4, 3, 2, 1))
        db_chat.chat_summary_save("p1", "uno", t2, 3)
        db_chat.chat_summary_save("p1", "dos", t4, "7", channel="web")
        db_chat.chat_summary_save("p1", "tres", t1, None)
        db_chat.chat_summary_save("p1", "cuatro", t3)
        db_chat.chat_summary_save("p2", "ajeno", NOW)
        self.assertEqual(db_chat.chat_summaries_latest_covers("p1"), t4)
        got = db_chat.chat_summaries_recent("p1")
        self.assertEqual([r["summary"] for r in got], ["dos", "cuatro", "uno"])
        self.assertEqual(set(got[0]), {"summary", "created", "covers_until"})
        self.assertEqual(got[0]["covers_until"], t4)
        self.assertEqual([r["summary"] for r in db_chat.chat_summaries_recent("p1", limit=1)], ["dos"])
        self.assertEqual([r["summary"] for r in db_chat.chat_summaries_recent("p1", limit=10)],
                         ["dos", "cuatro", "uno", "tres"])

    def test_save_defaults_and_truncation(self):
        db_chat.chat_summary_save("p", "x" * 3000, NOW, 4)
        db_chat.chat_summary_save("p", None, NOW - timedelta(days=1))
        rows = {r["covers_until"]: r for r in _sql("SELECT * FROM chat_summaries")}
        long = [r for r in rows.values() if r["summary"] != ""][0]
        self.assertEqual((len(long["summary"]), long["message_count"], long["channel"]), (2000, 4, "merged"))
        empty = [r for r in rows.values() if r["summary"] == ""][0]
        self.assertEqual(empty["message_count"], 0)


class TestCompaction(DbCase):
    def test_get_save_overwrite_clear(self):
        self.assertIsNone(db_chat.chat_compaction_get("p"))
        t1, t2 = NOW - timedelta(hours=2), NOW - timedelta(hours=1)
        self.assertIsNone(db_chat.chat_compaction_save("p", "uno", t1, 5))
        row = db_chat.chat_compaction_get("p")
        self.assertEqual((row["patient_key"], row["summary"], row["message_count"], row["covers_until"]),
                         ("p", "uno", 5, t1))
        self.assertEqual(set(row), {"patient_key", "summary", "message_count", "covers_until",
                                    "created", "updated"})
        db_chat.chat_compaction_save("p", "dos" * 2000, t2, None)
        row = db_chat.chat_compaction_get("p")
        self.assertEqual((len(row["summary"]), row["message_count"], row["covers_until"]), (4000, 0, t2))
        self.assertEqual(len(_sql("SELECT 1 FROM chat_compactions")), 1)
        db_chat.chat_compaction_save("q", None, t1)
        self.assertEqual(db_chat.chat_compaction_get("q")["summary"], "")
        self.assertIsNone(db_chat.chat_compaction_clear("p"))
        self.assertIsNone(db_chat.chat_compaction_get("p"))
        self.assertIsNotNone(db_chat.chat_compaction_get("q"))
        self.assertIsNone(db_chat.chat_compaction_clear("never-there"))


# ---- hand-off codes -----------------------------------------------------------------------

class TestHandoff(DbCase):
    def test_create_issues_a_code_and_reuses_the_live_one(self):
        code = db_chat.chat_handoff_create("dni:30111222")
        self.assertRegex(code, r"^ST-[A-HJ-NP-Z2-9]{12}$")
        self.assertEqual(db_chat.chat_handoff_create("dni:30111222"), code)
        row = _sql("SELECT * FROM chat_handoffs")[0]
        self.assertEqual((row["code"], row["web_key"], row["used"]), (code, "dni:30111222", False))
        self.assertLess(abs((row["expires"] - datetime.now(UTC) - timedelta(hours=48)).total_seconds()), 120)
        other = db_chat.chat_handoff_create("ip:1.1.1.1", ttl_horas=2)
        self.assertNotEqual(other, code)
        row = _sql("SELECT * FROM chat_handoffs WHERE code=%s", (other,))[0]
        self.assertLess(abs((row["expires"] - datetime.now(UTC) - timedelta(hours=2)).total_seconds()), 120)

    def test_create_issues_a_new_code_when_the_old_one_is_used_or_expired(self):
        used = db_chat.chat_handoff_create("k1")
        _sql("UPDATE chat_handoffs SET used=true WHERE code=%s", (used,))
        self.assertNotEqual(db_chat.chat_handoff_create("k1"), used)
        expired = db_chat.chat_handoff_create("k2")
        _sql("UPDATE chat_handoffs SET expires=now() - interval '1 minute' WHERE code=%s", (expired,))
        self.assertNotEqual(db_chat.chat_handoff_create("k2"), expired)

    def test_create_retries_a_code_collision_and_gives_up_after_five(self):
        _sql("INSERT INTO chat_handoffs (code, web_key, expires) VALUES ('ST-TAKEN', 'x', now() + interval '1 day')")
        with patch.object(db_chat, "_generate_handoff_code", side_effect=["ST-TAKEN", "ST-TAKEN", "ST-FRESH"]):
            self.assertEqual(db_chat.chat_handoff_create("k"), "ST-FRESH")
        self.assertEqual(_sql("SELECT web_key FROM chat_handoffs WHERE code='ST-FRESH'"), [{"web_key": "k"}])
        with patch.object(db_chat, "_generate_handoff_code", return_value="ST-TAKEN") as gen:
            with self.assertRaises(RuntimeError):
                db_chat.chat_handoff_create("k2")
        self.assertEqual(gen.call_count, 5)

    def test_use_consumes_a_code_once(self):
        code = db_chat.chat_handoff_create("dni:30111222")
        got = db_chat.chat_handoff_use(f"  {code.lower()} ", "+54 9 2255 123456")
        self.assertEqual(got, {"code": code, "clave_web": "dni:30111222"})
        row = _sql("SELECT used, used_phone, used_at FROM chat_handoffs")[0]
        self.assertEqual((row["used"], row["used_phone"]), (True, "+54 9 2255 123456"))
        # The real clock at the check, not NOW: the module is imported long before a full run gets here.
        self.assertLess(abs((row["used_at"] - datetime.now(UTC)).total_seconds()), 120)
        self.assertIsNone(db_chat.chat_handoff_use(code, "other phone"))
        self.assertEqual(_sql("SELECT used_phone FROM chat_handoffs"), [{"used_phone": "+54 9 2255 123456"}])

    def test_use_refuses_an_expired_unknown_or_empty_code(self):
        code = db_chat.chat_handoff_create("k")
        _sql("UPDATE chat_handoffs SET expires=now() - interval '1 second'")
        self.assertIsNone(db_chat.chat_handoff_use(code, "t"))
        for bad in ("ST-UNKNOWN", "", "   ", None):
            self.assertIsNone(db_chat.chat_handoff_use(bad, "t"))
        self.assertEqual(_sql("SELECT used FROM chat_handoffs"), [{"used": False}])


class TestPhoneResolution(DbCase):
    def _redeemed(self, web_key, phone, days_ago=0, code=None):
        code = code or db_chat.chat_handoff_create(web_key)
        _sql("UPDATE chat_handoffs SET used=true, used_phone=%s, used_at=%s WHERE code=%s",
             (phone, NOW - timedelta(days=days_ago), code))
        return code

    def test_user_by_phone_matches_the_last_ten_digits_of_one_account(self):
        _user("30111222", "+54 9 2255-12-3456")
        _user("30111223", "011 4444 5555")
        got = db_chat.user_by_phone("5492255123456")
        self.assertEqual(got["dni"], "30111222")
        self.assertNotIn("pass_hash", got)
        self.assertNotIn("portal_user_id", got)
        self.assertEqual(got["nombre"], "Test")  # wired
        self.assertEqual(db_chat.user_by_phone("(2255) 123456")["dni"], "30111222")
        self.assertEqual(db_chat.user_by_phone(" 2255123456 ")["dni"], "30111222")
        self.assertIsNone(db_chat.user_by_phone("2255999999"))
        self.assertIsNone(db_chat.user_by_phone("12345"))  # fewer than 10 digits
        self.assertIsNone(db_chat.user_by_phone(None))
        self.assertIsNone(db_chat.user_by_phone(""))

    def test_user_by_phone_never_guesses_between_two_accounts(self):
        _user("30111222", "2255123456")
        _user("30111223", "+54 9 2255 12 34 56")
        self.assertIsNone(db_chat.user_by_phone("2255123456"))

    def test_user_by_handoff_phone_follows_a_logged_in_redemption(self):
        _user("30111222", "x")
        self._redeemed("dni:30111222", "+54 9 2255 123456")
        got = db_chat.user_by_handoff_phone("2255123456")
        self.assertEqual(got["dni"], "30111222")
        self.assertNotIn("pass_hash", got)
        self.assertIsNone(db_chat.user_by_handoff_phone("2255999999"))
        self.assertIsNone(db_chat.user_by_handoff_phone("123"))
        self.assertIsNone(db_chat.user_by_handoff_phone(None))

    def test_user_by_handoff_phone_ignores_anonymous_unused_and_stale_codes(self):
        _user("30111222", "x")
        self._redeemed("ip:1.2.3.4", "2255123456")
        self.assertIsNone(db_chat.user_by_handoff_phone("2255123456"))
        db_chat.chat_handoff_create("dni:30111222")  # issued, never used
        self.assertIsNone(db_chat.user_by_handoff_phone("2255123456"))
        self._redeemed("dni:30111222", "2255123456", days_ago=45, code=db_chat.chat_handoff_create("dni:30111222"))
        self.assertIsNone(db_chat.user_by_handoff_phone("2255123456"))
        self.assertEqual(db_chat.user_by_handoff_phone("2255123456", grant_days=60)["dni"], "30111222")

    def test_user_by_handoff_phone_prefers_the_most_recent_redemption(self):
        _user("30111222", "x")
        _user("30111223", "x")
        self._redeemed("dni:30111222", "2255123456", days_ago=5)
        self._redeemed("dni:30111223:chat:abc", "2255123456", days_ago=1)
        self.assertEqual(db_chat.user_by_handoff_phone("2255123456")["dni"], "30111223")

    def test_linked_claves_from_the_registered_contact_and_from_handoffs(self):
        _say("whatsapp", "tel:5492255123456", "user", "a")
        _say("whatsapp", "tel:5492255123456", "user", "a2")
        _say("whatsapp", "tel:5491100000000", "user", "b")
        _say("web", "tel:5492255123456", "user", "no es whatsapp")
        self._redeemed("dni:30111222", "+54 9 3333 444 555")
        self._redeemed("dni:30111222:chat:tab1", "2266111222", days_ago=2, code=db_chat.chat_handoff_create("dni:30111222:chat:tab1"))
        self._redeemed("dni:30111223", "2244000000")  # another account's
        self._redeemed("ip:9.9.9.9", "2244999999")  # anonymous
        self._redeemed("dni:30111222", "2299000000", days_ago=40, code=db_chat.chat_handoff_create("dni:30111222"))  # stale
        got = db_chat.linked_whatsapp_claves("30.111.222", contact="+54 9 2255 123456")
        self.assertEqual(got, ["tel:2266111222", "tel:5492255123456", "tel:5493333444555"])
        self.assertEqual(db_chat.linked_whatsapp_claves("30111222", contact="2255123456", grant_days=60),
                         ["tel:2266111222", "tel:2299000000", "tel:5492255123456", "tel:5493333444555"])

    def test_linked_claves_need_a_real_contact_or_dni(self):
        _say("whatsapp", "tel:5492255123456", "user", "a")
        self.assertEqual(db_chat.linked_whatsapp_claves(None, contact="12345"), [])
        self.assertEqual(db_chat.linked_whatsapp_claves("", None), [])
        self.assertEqual(db_chat.linked_whatsapp_claves(None, contact="2255123456"), ["tel:5492255123456"])
        self.assertEqual(db_chat.linked_whatsapp_claves("30111222"), [])

    def test_web_claves_are_the_most_recent_web_conversations_of_that_dni(self):
        _say("web", "dni:30111222", "user", "a", 50)
        _say("web", "dni:30111222:chat:t1", "user", "b", 40)
        _say("web", "dni:30111222:chat:t2", "user", "c", 30)
        _say("web", "dni:30111222:chat:t3", "user", "d", 20)
        _say("web", "dni:30111222:chat:t1", "user", "e", 10)
        _say("web", "dni:301112229", "user", "otro dni", 5)
        _say("whatsapp", "dni:30111222", "user", "otro canal", 5)
        self.assertEqual(db_chat.web_claves_for_dni("30.111.222"),
                         ["dni:30111222:chat:t1", "dni:30111222:chat:t3", "dni:30111222:chat:t2"])
        self.assertEqual(db_chat.web_claves_for_dni("30111222", limit=1), ["dni:30111222:chat:t1"])
        self.assertEqual(db_chat.web_claves_for_dni("30111222", limit=10)[-1], "dni:30111222")
        self.assertEqual(db_chat.web_claves_for_dni(None), [])
        self.assertEqual(db_chat.web_claves_for_dni("abc"), [])
        self.assertEqual(db_chat.web_claves_for_dni("99999999"), [])


if __name__ == "__main__":
    unittest.main()
