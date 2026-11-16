"""Structural guard for the retired chat-to-booking request facades."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.modules.chat import chat_api, chat_tools  # noqa: E402
from app.modules.turnos import booking_create  # noqa: E402
from app.modules.turnos import booking_patient  # noqa: E402


def _facade_classes(source):
    """Find classes by handler protocol shape, independent of their name."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {
            item.name for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assigned = {
            target.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Assign)
            for target in item.targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        request_clone = "_read_json" in methods and bool(
            {"headers", "client_address", "path", "_body"} & assigned)
        response_clone = "_send_json" in methods or bool(
            {"send_response", "send_header", "end_headers"} & methods)
        if request_clone or response_clone:
            found.append(node.name)
    return found


class TestChatBookingShimRetirement(unittest.TestCase):
    def test_renamed_request_and_response_clones_are_detected(self):
        renamed = """
class TotallyNewName:
    def __init__(self, body, headers):
        self._body = body
        self.headers = headers
        self.client_address = ('127.0.0.1', 1)
    def _read_json(self):
        return {}
    def _send_json(self, payload, status=200):
        pass
"""
        self.assertEqual(_facade_classes(renamed), ["TotallyNewName"])

    def test_chat_sources_have_no_handler_protocol_facades(self):
        offenders = {}
        for relative in (
            "app/modules/chat/chat_api.py",
            "app/modules/chat/chat_api_router.py",
            "app/modules/chat/chat_agent.py",
            "app/modules/chat/chat_tools.py",
        ):
            names = _facade_classes((ROOT / relative).read_text(encoding="utf-8"))
            if names:
                offenders[relative] = names
        self.assertEqual(offenders, {})

    def test_booking_operations_are_typed_and_transport_free(self):
        self.assertEqual(
            tuple(booking_create.create_request.__code__.co_varnames[:2]),
            ("usuario", "body"),
        )
        self.assertEqual(
            tuple(booking_patient.cancel_request.__code__.co_varnames[:2]),
            ("dni", "sid"),
        )
        self.assertTrue(hasattr(chat_api, "ChatTurnInput"))
        self.assertFalse(hasattr(chat_tools, "_Shim"))


if __name__ == "__main__":
    unittest.main()
