"""test_admin_users_router.py — native ASGI contract for
`admin_panel/admin_users_router.py` (native-migration campaign, family F3,
docs/roadmap/native-migration-campaign-20260826.md).

Offline only: no Postgres, no network. `auth.admin_auth._verify_token` is
patched to a deterministic fixture identity instead of minting/signing a
real session cookie, and every `core.db` call the domain functions make is
patched too — same fixture strategy as `tests/test_admin_patients_router.py`.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_defaults(TURNOS_ADMIN_TOTP_SECRET="")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import admin_auth  # noqa: E402
from app.admin_panel import admin_users, admin_users_router  # noqa: E402
from app.core import db_operators  # noqa: E402

_FIXTURE_TOKEN = "fixture-admin"
_FIXTURE_IDENTITY = {"usuario": "admin", "rol": "admin"}


def _verify_token_fixture(token):
    if token == _FIXTURE_TOKEN:
        return dict(_FIXTURE_IDENTITY)
    return None


class TestNativeAdminUsersRouter(unittest.TestCase):
    def setUp(self):
        self._verify_patch = patch.object(admin_auth, "_verify_token", side_effect=_verify_token_fixture)
        self._verify_patch.start()
        self.addCleanup(self._verify_patch.stop)
        # `account_security` best-effort DB read — no Postgres here, degrade
        # exactly like production does when the pool is unreachable.
        self._security_patch = patch.object(
            admin_auth, "account_security",
            return_value={"tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False},
        )
        self._security_patch.start()
        self.addCleanup(self._security_patch.stop)

        app = FastAPI()
        app.include_router(admin_users_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app)  # the app's answer of a dependency
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.cookies = {"st_admin": _FIXTURE_TOKEN}

    def test_list_users(self):
        with patch.object(db_operators, "operators_list", return_value=[]):
            native = self.client.get("/api/admin/users", cookies=self.cookies)
        self.assertEqual(native.status_code, 200)
        # `admin_auth.account_security` is patched to a fixed 2FA state for
        # BOTH the outer auth gate and `_with_2fa_status`'s per-row lookup —
        # assert the actual body rather than a hand-typed fixture that could
        # silently drift from the domain function.
        self.assertEqual(native.json()["items"][0]["usuario"], "admin")

    def test_unauthenticated_status(self):
        # KNOWN GAP (not owned by this family): `core.deps.require_admin`
        # raises `HTTPException(detail={"error": "no_autenticado"})`, and
        # FastAPI's default handler wraps that as `{"detail": {...}}` — not
        # a flat `{"error": ...}` body. Same shape exists today on every
        # OTHER `Depends(deps.require_admin(...))` screen router
        # (admin_hsi_screen, admin_reportes_screen, etc.) — it is
        # `core/deps.py`'s auth-failure exception shape, off limits to this
        # family (see docs/roadmap/native-migration-campaign-20260826.md,
        # "do NOT edit core/deps.py"). Pinned here as-is.
        native = self.client.get("/api/admin/users")
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"detail": {"error": "no_autenticado"}})

    def test_unrecognized_action_is_terminal_404(self):
        # FastAPI's path template matches `{action}` for ANY segment; the
        # module's `USER_ACTIONS` literal set is what produces the generic
        # `{"error": "not found"}` terminal 404 — not FastAPI's default 404.
        native = self.client.post(
            "/api/admin/users/actions/not-a-real-action", json={}, cookies=self.cookies,
        )
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.content, b'{"error": "not found"}')
        self.assertEqual(native.headers.get("cache-control"), "no-store")

    def test_disable_user_not_found(self):
        with patch.object(admin_users, "_is_owner", return_value=False), \
             patch.object(db_operators, "operator_deactivate", return_value=None):
            native = self.client.delete("/api/admin/users/ghost", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)


if __name__ == "__main__":
    unittest.main()
