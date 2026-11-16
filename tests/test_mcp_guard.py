"""Budgets and validation for hostile or malfunctioning MCP clients."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
from app.auth import mcp_guard  # noqa: E402


class TestMcpGuard(unittest.TestCase):
    def setUp(self):
        mcp_guard._reset_for_tests()

    def tearDown(self):
        mcp_guard._reset_for_tests()

    def test_argumento_invalido_no_consume_presupuesto(self):
        ok, reason, retry, lease = mcp_guard.admit(
            "tok", "search_services", {"query": "cardio", "inyeccion": "x"})
        self.assertFalse(ok)
        self.assertIn("no permitidos", reason)
        self.assertEqual(retry, 0)
        self.assertFalse(lease)
        self.assertNotIn("tok", mcp_guard._CALLS)

    def test_limite_por_minuto(self):
        _env.set_env(self, TURNOS_MCP_PER_MINUTE="2")
        for now in (100, 101):
            self.assertTrue(mcp_guard.admit(
                "tok", "whoami", {}, now=now)[0])
        ok, reason, retry, _ = mcp_guard.admit("tok", "whoami", {}, now=102)
        self.assertFalse(ok)
        self.assertIn("minuto", reason)
        self.assertEqual(retry, 60)

    def test_presupuesto_ponderado_frena_trabajo_pesado(self):
        _env.set_env(self, TURNOS_MCP_WORK_PER_HOUR="60")
        self.assertTrue(mcp_guard.admit(
            "tok", "analyze_order", {"data_b64": "AA=="}, now=100)[0])
        mcp_guard.release_ai()
        ok, reason, retry, _ = mcp_guard.admit("tok", "whoami", {}, now=101)
        self.assertFalse(ok)
        self.assertIn("presupuesto", reason)
        self.assertEqual(retry, 3600)

    def test_limite_global_suma_tokens_distintos(self):
        _env.set_env(self, TURNOS_MCP_GLOBAL_WORK_PER_MINUTE="60")
        self.assertTrue(mcp_guard.admit(
            "a", "analyze_order", {"data_b64": "AA=="}, now=100)[0])
        mcp_guard.release_ai()
        ok, reason, retry, _ = mcp_guard.admit(
            "b", "analyze_order", {"data_b64": "AA=="}, now=101)
        self.assertFalse(ok)
        self.assertIn("servidor", reason)
        self.assertEqual(retry, 60)

    def test_concurrencia_de_ia_tiene_lease_y_release(self):
        _env.set_env(self, TURNOS_MCP_AI_CONCURRENCY="1")
        first = mcp_guard.admit(
            "a", "analyze_order", {"data_b64": "AA=="}, now=100)
        self.assertTrue(first[0])
        self.assertTrue(first[3])
        second = mcp_guard.admit(
            "b", "analyze_order", {"data_b64": "AA=="}, now=100)
        self.assertFalse(second[0])
        self.assertIn("curso", second[1])
        mcp_guard.release_ai()
        self.assertTrue(mcp_guard.admit(
            "b", "analyze_order", {"data_b64": "AA=="}, now=101)[0])
        mcp_guard.release_ai()

    def test_rechaza_base64_gigante_antes_de_decodificar(self):
        ok, reason, retry, _ = mcp_guard.admit(
            "tok", "analyze_order", {"data_b64": "A" * 245_001})
        self.assertFalse(ok)
        self.assertIn("demasiado largo", reason)
        self.assertEqual(retry, 0)


if __name__ == "__main__":
    unittest.main()
