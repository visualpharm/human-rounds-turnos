"""Test municipal portal: an in-process stand-in for the portal gateway.

`TURNOS_PORTAL_IDENTITY_MODE=test-portal-v1` puts this gateway in front of the
app on an installation that has no municipal portal to talk to
(pinamar.humanrounds.org). It speaks the real gateway's protocol, so the app
behind it runs exactly the code salud.pinamar.gob.ar runs
(`portal_identity.authenticate`, the account found or created by the DNI of
the validated CUIT, the portal screens):

- every request reaches the app from the gateway: the client's `X-User-Id`,
  `X-User-Cuit`, `X-User-Email` and `X-User-Roles` are removed, and the
  captured peer is the gateway's own (`TEST_PEER`), never the socket's;
- a person signed in to this test portal gets the four headers written from
  the portal's own record (`X-User-Cuit` in the `XX-XXXXXXXX-X` form);
- the portal's pages live at `/test-portal/login` and `/test-portal/logout`
  and its session is its own signed cookie, which the app never sees.

The people are fixed by the installation (`TURNOS_TEST_PORTAL_USERS`: subject,
CUIT, email, name, PBKDF2 password hash; the passwords live in the password vault) and
the cookie is signed with `TURNOS_TEST_PORTAL_SECRET`. The mode is read from
the process environment only, and the process refuses to start on a
government origin (`*.gob.ar`): the municipal installation always has the
real portal.

Unlike the real gateway this one keeps the `Cookie` header (our staff on this
installation still sign in with our passwords) and leaves Uvicorn's
forwarded-header handling alone (the peer it sets does not come from the
socket).
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
from urllib.parse import parse_qs, quote, urlsplit

from app.core import config

MODE = "test-portal-v1"
# The gateway's own address as the app sees it. ULA, so portal_identity's
# private-peer rule holds; only this wrapper ever writes it into the scope.
TEST_PEER = "fd00::7e57"
_PEER_KEY = "humanrounds.portal_peer"
_COOKIE = "hr_test_portal"
_SESSION_SECONDS = 12 * 3600
_IDENTITY_HEADERS = {b"x-user-id", b"x-user-cuit", b"x-user-email", b"x-user-roles",
                     b"x-user-name", b"x-user-given-name", b"x-user-family-name"}
_LOGIN = "/test-portal/login"
_LOGOUT = "/test-portal/logout"
_MAX_FORM = 4096


class ConfigurationError(Exception):
    """Unusable test portal configuration; carries no secret."""


def enabled():
    return os.environ.get("TURNOS_PORTAL_IDENTITY_MODE") == MODE


def _env(name):
    return str(os.environ.get(name, config.dotenv_values().get(name, "")) or "")


def hash_password(password, *, salt=None, iterations=240_000):
    """`pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>` for the users list."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def _password_ok(password, encoded):
    try:
        scheme, iterations, salt, digest = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _settings():
    """(users by email, secret). Raises ConfigurationError."""
    try:
        if config.government_installation():
            raise ValueError("a government installation has the real portal")
        secret = _env("TURNOS_TEST_PORTAL_SECRET")
        if len(secret) < 32:
            raise ValueError("secret too short")
        rows = json.loads(_env("TURNOS_TEST_PORTAL_USERS") or "[]")
        if not isinstance(rows, list) or not rows:
            raise ValueError("no test people")
        users, subjects = {}, set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"subject", "cuit", "email", "name", "password"}:
                raise ValueError("invalid person")
            subject, cuit = row["subject"], row["cuit"]
            email = str(row["email"]).strip().lower()
            if (not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", subject)
                    or not isinstance(cuit, str) or not re.fullmatch(r"[0-9]{11}", cuit)
                    or not re.fullmatch(r"[^@\s]+@[^@\s]+", email)
                    or not isinstance(row["name"], str)
                    or not str(row["password"]).startswith("pbkdf2_sha256$")
                    or email in users or subject in subjects):
                raise ValueError("invalid person")
            subjects.add(subject)
            users[email] = dict(row, email=email)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ConfigurationError() from exc
    return users, secret.encode()


def validate_configuration():
    """Boot check: fail before listening."""
    if not enabled() or _env("TURNOS_STAFF_PORTAL_LOGIN").strip() == "1":
        # Staff of this installation keep our passwords: a test person must
        # never be mapped to an operator.
        raise ConfigurationError()
    _settings()


def _sign(secret, subject, expires):
    payload = base64.urlsafe_b64encode(subject.encode()).decode().rstrip("=")
    body = "%s.%d" % (payload, expires)
    mac = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return "%s.%s" % (body, mac)


def _session_subject(secret, value, now=None):
    try:
        payload, expires, mac = value.split(".")
        body = "%s.%s" % (payload, expires)
        good = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, mac) or int(expires) < (now or time.time()):
            return ""
        return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    except (ValueError, TypeError, UnicodeDecodeError):
        return ""


def _cookies(headers):
    jar = {}
    for name, value in headers:
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, val = part.strip().partition("=")
                if key:
                    jar.setdefault(key, val)
    return jar


def _without_portal_cookie(value):
    parts = [p for p in value.decode("latin-1").split(";")
             if p.strip().partition("=")[0] != _COOKIE]
    return ";".join(parts).strip().encode("latin-1")


def _dashed(cuit):
    return "%s-%s-%s" % (cuit[:2], cuit[2:10], cuit[10])


def _safe_next(value):
    value = str(value or "")
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(c) < 32 for c in value) or value.startswith(_LOGIN)
            or value.startswith(_LOGOUT)):
        return "/"
    return value


def _referer_path(headers, host):
    for name, value in headers:
        if name == b"referer":
            parts = urlsplit(value.decode("latin-1"))
            if parts.netloc == host:
                return parts.path + ("?" + parts.query if parts.query else "")
    return "/"


def _return_path(value, host):
    """The path of `return` when it is an https URL of this site, else '/'."""
    parts = urlsplit(value)
    if parts.scheme != "https" or not host or parts.netloc != host:
        return "/"
    return (parts.path or "/") + ("?" + parts.query if parts.query else "")


_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Portal de prueba</title>
<style>
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  color:#1f1f1f;background:#f4f5f7;text-wrap:pretty}
main{max-width:400px;margin:48px auto;padding:0 16px}
.card{background:#fff;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,.12)}
h1{font-size:24px;margin:0 0 8px}
p{font-size:16px;line-height:1.5;margin:0 0 16px}
label{display:block;font-size:16px;font-weight:600;margin:16px 0 8px}
input{box-sizing:border-box;width:100%%;font-size:18px;padding:12px;border:1px solid #8a8f98;border-radius:8px}
button,.btn{display:block;box-sizing:border-box;width:100%%;margin-top:24px;padding:14px;font-size:18px;
  font-weight:600;border:0;border-radius:8px;background:#1d4ed8;color:#fff;text-align:center;
  text-decoration:none;cursor:pointer}
.err{color:#b91c1c;font-weight:600}
.out{margin-top:16px;display:block;text-align:center;font-size:16px;color:#1d4ed8}
</style></head><body><main><div class="card">%s</div></main></body></html>"""


def _login_html(nxt, error=""):
    err = '<p class="err">%s</p>' % html.escape(error) if error else ""
    return _PAGE % (
        "<h1>Portal de prueba</h1>"
        "<p>Ingresá con una persona de prueba. Su CUIT ya figura como validado.</p>%s"
        '<form method="post" action="%s">'
        '<input type="hidden" name="next" value="%s">'
        '<label for="email">Correo</label>'
        '<input id="email" name="email" type="email" autocomplete="username" required>'
        '<label for="password">Contraseña</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" required>'
        "<button type=\"submit\">Ingresar</button></form>"
        % (err, _LOGIN, html.escape(nxt, quote=True)))


def _signed_in_html(user, nxt):
    return _PAGE % (
        "<h1>Portal de prueba</h1><p>Entraste como %s (CUIT %s).</p>"
        '<a class="btn" href="%s">Ir a turnos</a>'
        '<a class="out" href="%s">Salir del portal</a>'
        % (html.escape(user["name"] or user["email"]), _dashed(user["cuit"]),
           html.escape(nxt, quote=True), _LOGOUT))


_PAGE_HEADERS = [
    (b"content-type", b"text/html; charset=utf-8"),
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy",
     b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-robots-tag", b"noindex, nofollow"),
]


class TestPortalGateway:
    """Outermost wrapper: the gateway every request crosses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        users, secret = _settings()
        by_subject = {u["subject"]: u for u in users.values()}
        headers = list(scope.get("headers") or [])
        jar = _cookies(headers)
        user = by_subject.get(_session_subject(secret, jar.get(_COOKIE, "")))
        path = scope.get("path", "")
        if scope["type"] == "http" and path in (_LOGIN, _LOGOUT):
            # The portal's own pages sit outside the app, so the site's Basic
            # Auth (app.py's SiteAuthMiddleware) never sees them: ask for the
            # same credential here, or the login form is the one page of the
            # host open to anyone (2026-10-26).
            from app.auth import site_auth, site_auth_middleware
            from starlette.datastructures import Headers
            if not site_auth.authorize(site_auth_middleware._raw_request_path(scope),
                                       Headers(scope=scope).get("Authorization", "")):
                await site_auth_middleware._unauthorized_response()(scope, receive, send)
                return
            await self._portal_page(scope, receive, send, users, secret, user)
            return
        forwarded = []
        for name, value in headers:
            if name in _IDENTITY_HEADERS:
                continue
            if name == b"cookie":
                value = _without_portal_cookie(value)
                if not value:
                    continue
            forwarded.append((name, value))
        if user:
            forwarded += [
                (b"x-user-id", user["subject"].encode()),
                (b"x-user-cuit", _dashed(user["cuit"]).encode()),
                (b"x-user-email", user["email"].encode()),
            ]
            if user["name"]:
                # Percent-encoded: header values are ASCII on the wire.
                forwarded.append((b"x-user-name", quote(user["name"]).encode()))
        scope = dict(scope, headers=forwarded)
        scope[_PEER_KEY] = (TEST_PEER, 0)
        await self.app(scope, receive, send)

    async def _portal_page(self, scope, receive, send, users, secret, user):
        host = ""
        for name, value in scope.get("headers") or []:
            if name == b"host":
                host = value.decode("latin-1")
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if scope["path"] == _LOGOUT:
            await self._respond(send, 303, b"", [
                (b"location", b"/"),
                (b"set-cookie", self._cookie(scope, "", 0))])
            return
        method = scope.get("method", "GET")
        if method == "POST":
            body = await self._body(receive)
            form = parse_qs(body.decode("utf-8", "replace"))
            nxt = _safe_next((form.get("next") or ["/"])[0])
            email = (form.get("email") or [""])[0].strip().lower()
            password = (form.get("password") or [""])[0]
            person = users.get(email)
            if person and _password_ok(password, person["password"]):
                expires = int(time.time()) + _SESSION_SECONDS
                await self._respond(send, 303, b"", [
                    (b"location", nxt.encode("latin-1", "replace")),
                    (b"set-cookie", self._cookie(scope, _sign(secret, person["subject"], expires),
                                                 _SESSION_SECONDS))])
                return
            await self._respond(send, 401, _login_html(
                nxt, "El correo o la contraseña no coinciden.").encode(), _PAGE_HEADERS)
            return
        if method not in {"GET", "HEAD"}:
            await self._respond(send, 405, b"", [(b"allow", b"GET, POST")])
            return
        back = (query.get("return") or [""])[0]
        if back:
            # The municipal portal's parameter: an absolute URL of this site.
            nxt = _safe_next(_return_path(back, host))
            if user:
                # Signed in already: straight back, as the portal's /sso/login does.
                await self._respond(send, 303, b"", [
                    (b"location", nxt.encode("latin-1", "replace")),
                    (b"cache-control", b"no-store")])
                return
        else:
            nxt = _safe_next((query.get("next") or [_referer_path(scope.get("headers") or [], host)])[0])
        page = _signed_in_html(user, nxt) if user else _login_html(nxt)
        await self._respond(send, 200, b"" if method == "HEAD" else page.encode(), _PAGE_HEADERS)

    @staticmethod
    def _cookie(scope, value, max_age):
        secure = "; Secure" if scope.get("scheme") == "https" or _env(
            "TURNOS_PUBLIC_ORIGIN").startswith("https://") else ""
        return ("%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s"
                % (_COOKIE, value, max_age, secure)).encode()

    @staticmethod
    async def _body(receive):
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > _MAX_FORM:
                return b""
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        return b"".join(chunks)

    @staticmethod
    async def _respond(send, status, body, headers):
        await send({"type": "http.response.start", "status": status,
                    "headers": list(headers) + [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
