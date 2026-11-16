"""test_slot_holds_router_native.py — native-transport parity for
slot_holds_router.py's two routes (POST /api/slot-hold, DELETE
/api/slot-hold/<token>). Offline: no managed Postgres — DATABASE_URL="" means
`db_slots.hold_acquire`/`db_slots.hold_release` fail-closed/no-op immediately (see
core/db.py:_connect, `raise DbUnavailable("DATABASE_URL no configurada")`),
so every assertion here exercises real HTTP through TestClient(app) without
touching a real database.

Same env/import bootstrap as tests/test_app_modules_router.py.
"""

import os
import sys
import unittest
from datetime import datetime
from app.core import tz

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.modules.turnos import slot_holds  # noqa: E402

# A real "solicitud" service from the public example-town pack (holdable —
# see slot_holds._service_holdable): packs/example-town/servicios.json.
_SERVICIO_HOLDABLE = "medicina-general-riverside-clinic"


class TestSlotHoldsRouterNative(unittest.TestCase):
    def setUp(self):
        # `setdefault("DATABASE_URL", "")` above is a no-op whenever the run has
        # a database configured, and the "no database" tests passed only after
        # another module had left a dead URL behind — pin it per test instead.
        self.enterContext(_env.patched_env(DATABASE_URL=""))
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)
        slot_holds._HITS.clear()

    def tearDown(self):
        slot_holds._HITS.clear()

    def test_create_hold_sin_base_falla_cerrado_409(self):
        """Con DATABASE_URL="" `db_slots.hold_acquire` degrada a False (fail
        CLOSED, core/db.py's own doc), así que `try_take` nunca puede
        confirmar el hold: la respuesta observable es 409 turno_tomado,
        nunca un 200 silencioso sin persistencia real."""
        response = self.client.post(
            "/api/slot-hold",
            json={"servicio_id": _SERVICIO_HOLDABLE, "dia": datetime.now(tz.tzinfo()).date().isoformat(), "hora": "09:00"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "turno_tomado"})
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        import json
        self.assertEqual(response.content, json.dumps(response.json(), ensure_ascii=False).encode("utf-8"))

    def test_create_hold_dia_hora_invalidos_400(self):
        response = self.client.post(
            "/api/slot-hold",
            json={"servicio_id": _SERVICIO_HOLDABLE, "dia": "no-es-fecha", "hora": "09h00"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "servicio_id/dia/hora inválidos"})

    def test_create_hold_servicio_no_reservable_400(self):
        response = self.client.post(
            "/api/slot-hold",
            json={"servicio_id": "demanda-espontanea-riverside-clinic", "dia": "2027-01-01", "hora": "09:00"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "servicio no reservable"})

    def test_create_hold_json_invalido_400(self):
        response = self.client.post(
            "/api/slot-hold",
            content=b"{esto no es json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON inválido", response.json()["error"])

    def test_create_hold_rate_limit_429(self):
        _env.set_env(self, TURNOS_HOLD_PER_HOUR="0")
        response = self.client.post(
            "/api/slot-hold",
            json={"servicio_id": _SERVICIO_HOLDABLE, "dia": "2027-01-01", "hora": "09:00"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {"error": "demasiadas consultas, esperá un rato"})

    def test_delete_hold_no_op_sigue_devolviendo_ok(self):
        response = self.client.delete("/api/slot-hold/un-token-cualquiera")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_delete_hold_token_en_blanco_400(self):
        """Un token que decodifica a puro espacio (`%20`) es el único caso en
        que `handle()` (y por lo tanto el router nativo) declina el pedido —
        con su propio 400 "falta el token", no con el 404 terminal: el
        template de ruta ya excluye el segmento vacío/multi-segmento antes de
        llegar acá (ver slot_holds_router.py's own docstring)."""
        response = self.client.delete("/api/slot-hold/%20")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "falta el token"})

    def test_create_hold_metodo_get_no_soportado_cae_al_404_terminal(self):
        response = self.client.get("/api/slot-hold")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b'{"error": "not found"}')


if __name__ == "__main__":
    unittest.main()
