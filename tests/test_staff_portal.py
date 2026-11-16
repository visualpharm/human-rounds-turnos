"""Staff login through the municipal portal gateway (auth/staff_portal.py).

Real app composition behind `SocketPeerApp`, synthetic gateway peer, real
Postgres for the operators. Proves: the trust boundary (only the configured
gateway peer counts; forged headers from anywhere else do nothing), the
per-request mapping (email once → CUIT recorded → CUIT only), the refusals
(unknown, ambiguous, unvalidated, disabled), permissions from the operator
role only, the closed native entry points, the portal invitation, the
optional TOTP step, and the first-admin script.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_staff_portal.py"
"""

import json
import os
import sys
import unittest
import _lifespan
from html import escape
from unittest.mock import patch
from urllib.parse import quote

import _archive  # noqa: E402
import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_staff_portal")
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    _lifespan.no_background_services()
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_PEER = "fd12::10"
_ORIGIN = "https://salud.example.invalid"
_CUIT = "20123456789"
_ENV = {
    "TURNOS_STAFF_PORTAL_LOGIN": "1",
    "TURNOS_STAFF_PORTAL_TOTP": "",
    "TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
    "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps([_PEER]),
    "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": _ORIGIN,
    "TURNOS_ADMIN_SECRET": "staff-portal-test-signing-key",
}


def _portal(email="ana@pinamar.example", cuit="20-12345678-9", subject=None, **extra):
    # One portal account per email unless a test says otherwise.
    subject = subject or "portal-" + email.split("@", 1)[0].lower()
    headers = {"X-User-Id": subject, "X-User-Email": email, "X-User-Cuit": cuit,
               "X-User-Roles": "admin"}
    headers.update(extra)
    return headers


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        self.addCleanup(config._reset_cache_para_tests)   # runs after the environment is back (LIFO)
        _env.set_env(self, _ENV)
        config._reset_cache_para_tests()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM operator_access")
            cur.execute("DELETE FROM admin_account_security")
            cur.execute("DELETE FROM operators")
        db_operators.operator_create("ana", "x" * 20, "admin", None, email="Ana@Pinamar.example")
        db_operators.operator_create("beto", "x" * 20, "admisionista", None, email="beto@pinamar.example")
        from app import main as app_module
        from app.cuentas import portal_identity
        from fastapi.testclient import TestClient
        self.app = portal_identity.SocketPeerApp(app_module.app)
        # https like the installation's own origin: its staff cookies are Secure (finding 19).
        self.client = self.enterContext(TestClient(self.app, client=(_PEER, 50000),
                                                   base_url=_ORIGIN, follow_redirects=False))

    def whoami(self, headers, client=None):
        return (client or self.client).get("/api/admin/whoami", headers=headers)

    def bound_of(self, usuario):
        """(portal account id, CUIT) recorded on the account."""
        row = db_operators.operator_get(usuario) or {}
        return row.get("portal_user_id"), row.get("cuit")


class TestMapping(_Base):
    def test_first_login_by_email_records_the_portal_account_then_it_alone_decides(self):
        response = self.whoami(_portal())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["usuario"], "ana")
        self.assertTrue(response.json()["acceso_portal"])
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.bound_of("ana"), ("portal-ana", _CUIT))
        # The portal email changes: the recorded account id still finds Ana.
        moved = _portal(email="ana.nueva@otro.example", subject="portal-ana")
        self.assertEqual(self.whoami(moved).json()["usuario"], "ana")
        # Another portal account with Ana's email: Ana is already bound.
        other = _portal(subject="portal-otra", cuit="27999999990")
        self.assertEqual(self.whoami(other).status_code, 401)
        self.assertEqual(self.bound_of("ana"), ("portal-ana", _CUIT))
        # Fail closed: even a bound account needs the validated CUIT header.
        self.assertEqual(self.whoami(_portal(cuit="")).status_code, 401)

    def test_a_cuit_already_bound_cannot_bind_a_second_account(self):
        self.assertEqual(self.whoami(_portal()).status_code, 200)
        beto = _portal(email="beto@pinamar.example")  # Ana's CUIT, another portal account
        self.assertEqual(self.whoami(beto).status_code, 401)
        self.assertEqual(self.bound_of("beto"), (None, None))

    def test_unknown_unvalidated_ambiguous_and_disabled_are_refused(self):
        self.assertEqual(self.whoami(_portal(email="nadie@pinamar.example")).status_code, 401)
        self.assertEqual(self.whoami(_portal(cuit="")).status_code, 401)
        self.assertEqual(self.whoami(_portal(cuit="123")).status_code, 401)
        self.assertEqual(self.bound_of("ana"), (None, None))
        db_operators.operator_create("ana2", "x" * 20, "admin", None, email="ana@pinamar.example")
        self.assertEqual(self.whoami(_portal()).status_code, 401)
        self.assertEqual(self.bound_of("ana"), (None, None))
        # An inactive duplicate does not count: exactly one ACTIVE account.
        db_operators.operator_update("ana2", activo=False)
        self.assertEqual(self.whoami(_portal()).status_code, 200)
        # Disabling takes effect on the very next request — no session of ours.
        db_operators.operator_update("ana", activo=False)
        self.assertEqual(self.whoami(_portal()).status_code, 401)

    def test_role_comes_from_the_operator_only_and_changes_apply_at_once(self):
        response = self.whoami(_portal(email="beto@pinamar.example", cuit="20111111112"))
        self.assertEqual(response.json()["rol"], "admisionista")
        users = self.client.get("/api/admin/users",
                                headers=_portal(email="beto@pinamar.example", cuit="20111111112"))
        self.assertEqual(users.status_code, 403)
        db_operators.operator_update("beto", rol="admin")
        later = _portal(email="x@y.example", cuit="20111111112", subject="portal-beto")
        self.assertEqual(self.whoami(later).json()["rol"], "admin")

    def test_login_screen_lets_the_known_in_and_tells_the_rest_what_to_do(self):
        ok = self.client.get("/admin/login?next=/admin/users", headers=_portal())
        self.assertEqual(ok.status_code, 303)
        self.assertEqual(ok.headers["location"], "/admin/users")
        stranger = self.client.get("/admin/login", headers=_portal(email="nadie@pinamar.example",
                                                                   cuit="20444444445"))
        self.assertEqual(stranger.status_code, 200)
        self.assertIn("Pedile al administrador que te dé de alta con este correo.", stranger.text)
        self.assertIn("nadie@pinamar.example", stranger.text)
        self.assertNotIn('id="gate-pass"', stranger.text)
        anonymous = self.client.get("/admin/login")
        self.assertIn("Entrá con tu cuenta del portal.", anonymous.text)
        self.assertNotIn('id="gate-pass"', anonymous.text)
        screen = self.client.get("/admin/users", headers=_portal(email="nadie@pinamar.example",
                                                                 cuit="20444444445"))
        self.assertEqual(screen.status_code, 303)
        self.assertTrue(screen.headers["location"].startswith("/admin/login"))

    def test_without_a_portal_session_the_login_goes_to_the_portal_by_itself_once(self):
        # The product owner m1008550: signed in on the portal = signed in here, no button.
        # The portal's `sso` cookie lasts 8 h and is renewed only by the portal
        # page itself, so a person still signed in there arrives with no
        # identity: /admin/login sends them through the portal's /sso/login,
        # which renews it and comes back — straight to the screen asked for.
        with _env.patched_env(TURNOS_PORTAL_LOGIN_URL="https://portal.example.invalid/sso/login"):
            trip = self.client.get("/admin/login?next=/admin/users")
            self.assertEqual(trip.status_code, 303)
            self.assertEqual(trip.headers["location"],
                             "https://portal.example.invalid/sso/login?return="
                             + quote(_ORIGIN + "/admin/login?next=/admin/users&via=portal", safe=""))
            self.assertEqual(trip.headers["cache-control"], "no-store")
            # Back from the portal signed in: the screen asked for.
            back = self.client.get("/admin/login?next=/admin/users&via=portal", headers=_portal())
            self.assertEqual(back.status_code, 303)
            self.assertEqual(back.headers["location"], "/admin/users")
            # Back without an identity: the card, never a second trip — and its
            # button comes back to the same screen.
            card = self.client.get("/admin/login?next=/admin/users&via=portal")
            self.assertEqual(card.status_code, 200)
            self.assertIn("Entrá con tu cuenta del portal.", card.text)
            self.assertIn(escape(trip.headers["location"]), card.text)
            # A forged next stays on the panel.
            outside = self.client.get("/admin/login?next=https://evil.example/")
            self.assertIn(quote(_ORIGIN + "/admin/login?next=/admin&via=portal", safe=""),
                          outside.headers["location"])
        # No sign-in URL configured: the card, as before.
        self.assertEqual(self.client.get("/admin/login").status_code, 200)

    def test_expired_enrolment_deadline_does_not_lock_a_portal_account_out(self):
        from datetime import datetime, timedelta, timezone
        db_auth.admin_security_set_deadline("ana", datetime.now(timezone.utc) - timedelta(days=1))
        response = self.whoami(_portal())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["2fa_vencido"])
        self.assertTrue(response.json()["2fa_no_aplica"])

    def test_screens_are_told_to_hide_password_passkey_and_2fa_controls(self):
        account = self.client.get("/api/admin/account", headers=_portal()).json()
        self.assertTrue(account["acceso_portal"])
        self.assertTrue(account["2fa_no_aplica"])
        users = self.client.get("/api/admin/users", headers=_portal()).json()
        self.assertTrue(users["acceso_portal"])
        self.assertFalse(users["acceso_portal_totp"])
        # The portal binding never reaches the panel.
        for item in users["items"]:
            self.assertNotIn("portal_user_id", item)
            self.assertNotIn("cuit", item)
        with _env.patched_env({"TURNOS_STAFF_PORTAL_LOGIN": ""}):
            from app.auth import admin_auth
            self.client.cookies.set("st_admin", admin_auth._mint_token("ana", "admin"))
            self.assertNotIn("acceso_portal", self.client.get("/api/admin/users").json())


class TestSignOut(_Base):
    """Cuenta → «Cerrar sesión» on a portal installation (salud, 2026-10-29).

    The panel has no session of ours: the gateway's headers identify every
    request. A button that clears a cookie that does not exist and lands on
    /admin/login, where the same headers sign the person straight back in,
    "does nothing" — so the button is shown only when the portal's own
    logout address is configured."""

    def screen(self, url):
        with _env.patched_env(TURNOS_PORTAL_LOGOUT_URL=url):
            return self.client.get("/admin/account", headers=_portal())

    def test_without_a_portal_logout_address_the_account_screen_has_no_sign_out_button(self):
        response = self.screen("")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('id="adm-logout"', response.text)

    def test_with_the_portal_logout_address_the_button_leaves_through_it(self):
        url = "https://portal.example.invalid/logout"
        response = self.screen(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('id="adm-logout"', response.text)
        self.assertIn('data-portal-logout="%s"' % url, response.text)

    def test_a_portal_logout_address_that_is_not_https_is_ignored(self):
        response = self.screen("http://portal.example.invalid/logout")
        self.assertNotIn('id="adm-logout"', response.text)


class TestSignOutOwnLogin(unittest.TestCase):
    def test_the_template_keeps_the_button_when_the_installation_has_its_own_login(self):
        from app.core import jinja_env
        html = jinja_env.render("admin/account.html", nav_items=[], active_section="cuenta",
                                current_user="ana", portal_logout=None)
        self.assertIn('id="adm-logout"', html)
        self.assertNotIn("data-portal-logout", html)


class TestTrustBoundary(_Base):
    def test_forged_headers_from_any_other_peer_do_nothing(self):
        from fastapi.testclient import TestClient
        headers = _portal(**{"X-Forwarded-For": _PEER, "Forwarded": 'for="[%s]"' % _PEER})
        for peer in ("fd12::11", "10.0.0.2", "8.8.8.8", "127.0.0.1"):
            with self.subTest(peer=peer), TestClient(self.app, client=(peer, 1),
                                                    follow_redirects=False) as other:
                self.assertEqual(self.whoami(headers, client=other).status_code, 401)
        self.assertEqual(self.bound_of("ana"), (None, None))

    def test_signed_native_cookie_is_not_a_session_here(self):
        from app.auth import admin_auth
        token = admin_auth._mint_token("ana", "admin")
        self.client.cookies.set("st_admin", token)
        self.assertEqual(self.whoami({}).status_code, 401)

    def test_unsafe_request_needs_the_public_origin(self):
        body = {"usuario": "nueva", "rol": "admisionista", "email": "nueva@pinamar.example"}
        denied = self.client.post("/api/admin/users", json=body, headers=_portal())
        self.assertEqual(denied.status_code, 401)
        self.assertIsNone(db_operators.operator_get("nueva"))

    def test_boot_refuses_the_flag_without_the_gateway_boundary(self):
        from app.auth import staff_portal
        from app.cuentas import portal_identity
        with _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": "off"}):
            with self.assertRaises(portal_identity.ConfigurationError):
                staff_portal.validate_configuration()
        staff_portal.validate_configuration()

    def test_without_the_flag_portal_headers_grant_nothing(self):
        with _env.patched_env({"TURNOS_STAFF_PORTAL_LOGIN": ""}):
            self.assertEqual(self.whoami(_portal()).status_code, 401)
        self.assertEqual(self.bound_of("ana"), (None, None))


class TestClosedNativeEntries(_Base):
    def test_password_reset_passkey_and_staff_fallback_are_closed(self):
        login = self.client.post("/api/admin/login", json={"usuario": "ana", "password": "x" * 20})
        self.assertEqual(login.status_code, 403)
        self.assertNotIn("set-cookie", login.headers)
        self.assertEqual(self.client.get("/admin/reset-password?token=x").status_code, 404)
        self.assertEqual(self.client.post("/api/admin/reset-password",
                                          json={"action": "request", "usuario": "ana"}).status_code, 404)
        self.assertEqual(self.client.post("/api/admin/passkeys/login-options").status_code, 403)
        from app.auth import admin_auth
        staff = admin_auth.patient_staff_login({}, "127.0.0.1", "ana@pinamar.example", "x" * 20)
        self.assertEqual(staff.status, 401)
        self.assertIsNone(staff.set_cookie)

    def test_admin_adds_by_email_and_the_invitation_points_to_the_portal(self):
        headers = _portal(Origin=_ORIGIN)
        sent = []
        with patch("app.notifications.mailgun.send", side_effect=lambda *a: sent.append(a) or True):
            missing = self.client.post("/api/admin/users", headers=headers,
                                       json={"usuario": "sin.mail", "rol": "admisionista"})
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(missing.json()["field"], "email")
            created = self.client.post("/api/admin/users", headers=headers,
                                       json={"usuario": "carla", "rol": "admisionista",
                                             "email": "carla@pinamar.example"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertNotIn("enlace", created.json())
        self.assertTrue(created.json()["email_enviado"])
        (to, _subject, text), = sent
        self.assertEqual(to, "carla@pinamar.example")
        self.assertIn("/admin", text)
        self.assertNotIn("reset-password", text)
        self.assertNotIn("token", text)
        # Carla's first portal login finds her by that email.
        carla = self.whoami(_portal(email="CARLA@pinamar.example", cuit="27222222223"))
        self.assertEqual(carla.json()["usuario"], "carla")

    def test_two_active_accounts_never_share_the_portal_email(self):
        headers = _portal(Origin=_ORIGIN)
        with patch("app.notifications.mailgun.send", return_value=True):
            twin = self.client.post("/api/admin/users", headers=headers,
                                    json={"usuario": "ana.bis", "rol": "admisionista",
                                          "email": " ANA@pinamar.example"})
            self.assertEqual((twin.status_code, twin.json()["field"]), (409, "email"))
            self.assertIsNone(db_operators.operator_get("ana.bis"))
            edit = self.client.put("/api/admin/users/beto", headers=headers,
                                   json={"email": "ana@pinamar.example"})
            self.assertEqual((edit.status_code, edit.json()["field"]), (409, "email"))
            # A disabled holder of the email does not block, and re-enabling
            # it while another active account has the email is refused.
            db_operators.operator_update("beto", activo=False)
            moved = self.client.put("/api/admin/users/beto", headers=headers,
                                    json={"email": "ana@pinamar.example"})
            self.assertEqual(moved.status_code, 200, moved.text)
            back = self.client.put("/api/admin/users/beto", headers=headers, json={"activo": True})
            self.assertEqual(back.status_code, 409)


class TestPortalTotp(_Base):
    def test_operator_with_a_second_factor_proves_it_once_per_window(self):
        import pyotp
        secret = pyotp.random_base32()
        db_operators.operator_update("ana", totp_secret=secret)
        with _env.patched_env({"TURNOS_STAFF_PORTAL_TOTP": "1"}):
            self.assertEqual(self.whoami(_portal()).status_code, 401)
            screen = self.client.get("/admin/login", headers=_portal())
            self.assertEqual(screen.status_code, 200)
            self.assertIn('data-staff-pending="1"', screen.text)
            headers = _portal(Origin=_ORIGIN)
            wrong = self.client.post("/api/admin/login", headers=headers,
                                     json={"staff_pending": True, "codigo": "000000"})
            self.assertEqual(wrong.status_code, 401)
            right = self.client.post("/api/admin/login", headers=headers,
                                     json={"staff_pending": True, "codigo": pyotp.TOTP(secret).now()})
            self.assertEqual(right.status_code, 200, right.text)
            self.assertIn("st_staff_2fa=", right.headers["set-cookie"])
            self.assertEqual(self.whoami(_portal()).json()["usuario"], "ana")
            # The proof belongs to Ana's portal account and secret: a new secret voids it.
            db_operators.operator_update("ana", totp_secret=pyotp.random_base32())
            self.assertEqual(self.whoami(_portal()).status_code, 401)
        # With the flag off (default) the portal is the only factor.
        self.client.cookies.clear()
        self.assertEqual(self.whoami(_portal()).status_code, 200)


class TestFirstAdminScript(_Base):
    def test_dry_run_then_create_then_enable_then_ambiguous(self):
        _archive.skip_in_archive_copy(self)
        sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "scripts", "fixes"))
        import staff_portal_admin as script
        lines = []
        self.assertEqual(script.run("jefa@pinamar.example", emit=lines.append), 0)
        self.assertIn("simulacion", lines[-1])
        self.assertIsNone(db_operators.operator_by_email("jefa@pinamar.example"))
        self.assertEqual(script.run("jefa@pinamar.example", apply=True, emit=lines.append), 0)
        jefa = db_operators.operator_by_email("jefa@pinamar.example")
        self.assertEqual((jefa["usuario"], jefa["rol"], jefa["activo"]), ("jefa", "admin", True))
        self.assertEqual(self.whoami(_portal(email="jefa@pinamar.example", cuit="27333333334"))
                         .json()["usuario"], "jefa")
        # Locked out (disabled, demoted, new portal account): one run restores her.
        db_operators.operator_update("jefa", activo=False, rol="admisionista")
        self.assertEqual(script.run("jefa@pinamar.example", apply=True, unbind=True,
                                    emit=lines.append), 0)
        jefa = db_operators.operator_get("jefa")
        self.assertEqual((jefa["rol"], jefa["activo"]), ("admin", True))
        self.assertEqual(self.bound_of("jefa"), (None, None))
        self.assertFalse(any("27333333334" in line or "portal-jefa" in line for line in lines))
        # Idempotent: the same run again changes nothing.
        self.assertEqual(script.run("jefa@pinamar.example", apply=True, unbind=True,
                                    emit=lines.append), 0)
        self.assertIn("ninguno", lines[-2])
        self.assertEqual(self.whoami(_portal(email="jefa@pinamar.example", cuit="27333333334",
                                             subject="portal-jefa-nueva")).json()["usuario"], "jefa")
        db_operators.operator_create("jefa2", "x" * 20, "admin", None, email="jefa@pinamar.example")
        self.assertEqual(script.run("jefa@pinamar.example", apply=True, emit=lines.append), 1)


if __name__ == "__main__":
    unittest.main()
