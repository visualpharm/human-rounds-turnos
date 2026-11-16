"""Contract tests for the chat persistence seam.

This is intentionally a structural test: the adapter stays a transparent
pass-through while ``chat_agent`` retains its existing best-effort behavior.
"""

import ast
import inspect
import os
import sys
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _dbowner  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.modules.chat import chat_agent  # noqa: E402
from app.modules.chat import chat_store  # noqa: E402
from app.core import db_chat  # noqa: E402


_DELEGATES = (
    "chat_save_message",
    "chat_metadata_get",
    "chat_take_booking_proposal",
    "get_user",
    "linked_whatsapp_claves",
    "web_claves_for_dni",
    "chat_history_multi",
    "chat_summaries_latest_covers",
    "chat_summary_save",
    "chat_summaries_recent",
    "chat_compaction_get",
    "chat_compaction_save",
    "chat_compaction_clear",
    "chat_first_user_message",
    "chat_new_session_marker_at",
    "chat_count_user_messages",
    "get_dependent",
    "active_booking_request_by_slot",
    "list_by_dni",
    "list_dependents",
)


class TestChatStoreContract(unittest.TestCase):
    def test_delegates_keep_core_signatures_and_one_call_bodies(self):
        tree = ast.parse(inspect.getsource(chat_store))
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(set(functions), set(_DELEGATES))
        for name in _DELEGATES:
            self.assertEqual(inspect.signature(getattr(chat_store, name)),
                             inspect.signature(getattr(_dbowner.owner_of(name), name)))
            body = functions[name].body
            self.assertEqual(len(body), 1, name)
            self.assertIsInstance(body[0], ast.Return, name)
            call = body[0].value
            self.assertIsInstance(call, ast.Call, name)
            self.assertIsInstance(call.func, ast.Attribute, name)
            self.assertEqual(call.func.attr, name)

    def test_one_save_delegation_preserves_the_exact_call(self):
        message = {"role": "user", "content": "Necesito un turno"}
        with patch.object(db_chat, "chat_save_message", return_value=None) as save:
            self.assertIsNone(chat_store.chat_save_message("web", "dni:30999888", message))
        save.assert_called_once_with("web", "dni:30999888", message)

    def test_chat_agent_has_no_direct_db_dependency_and_keeps_fallback(self):
        source = inspect.getsource(chat_agent)
        self.assertNotIn("from app.core import db", source)
        self.assertNotIn("app.core.db", source)
        with patch.object(chat_agent.chat_store, "chat_history_multi",
                          side_effect=RuntimeError("store unavailable")):
            self.assertEqual(chat_agent._load_history(None, "web", "ip:store-contract"), [])

    def test_chat_tools_has_no_direct_db_dependency(self):
        from app.modules.chat import chat_tools

        source = inspect.getsource(chat_tools)
        self.assertNotIn("from app.core import db", source)
        self.assertNotIn("app.core.db", source)

    def test_booking_recovery_delegation_preserves_the_exact_call(self):
        with patch.object(
                db_slots, "active_booking_request_by_slot",
                return_value={"id": 17}) as recover:
            self.assertEqual(
                chat_store.active_booking_request_by_slot(
                    "30999888", "cardiology", "2026-08-25", "09:30"),
                {"id": 17})
        recover.assert_called_once_with(
            "30999888", "cardiology", "2026-08-25", "09:30")


if __name__ == "__main__":
    unittest.main()
