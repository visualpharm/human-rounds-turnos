"""Staff login through the municipal portal gateway (opt-in per installation).

With ``TURNOS_STAFF_PORTAL_LOGIN=1`` the panel has no login of its own: every
request carries the portal gateway's identity headers and is mapped to an
operator HERE, on each request, with no session of ours — disabling an
account or changing its role takes effect on the next click.

Trust: the headers count only when ``cuentas.portal_identity`` proves the
request came from the configured gateway's exact private socket peer (the
same boundary the patient adapter uses; ``TURNOS_PORTAL_IDENTITY_MODE`` must
be ``private-gateway-v1``). Mapping:

  1. An operator bound to this portal account (``X-User-Id``, the portal's
     immutable account id) → that operator, and only it.
  2. Otherwise the gateway's email (any case) must belong to exactly ONE
     active operator not bound to a portal account yet; the account id and
     the validated CUIT are recorded on it, and every later login goes by the
     account id (a changed portal email keeps working).
  3. Anything else — no or several matches, a missing CUIT (portal identity
     not validated; the gateway never lets one through, but we fail closed),
     a disabled account — is refused with one remedy: ask an administrator to
     register this email.

Permissions come only from the operator's role; ``X-User-Roles`` is ignored.
The native password, password reset, invitation link and passkey logins are
closed on such an installation. Our TOTP stays available behind
``TURNOS_STAFF_PORTAL_TOTP=1`` (off by default): an operator with a second
factor proves it once per session window with a signed cookie bound to the
account, its portal account and its current secret.
"""

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core import config as _config

_LOG = logging.getLogger(__name__)

TOTP_COOKIE = "st_staff_2fa"
_TOTP_SALT = "staff-portal-2fa-v1"
# A portal request refreshes «último acceso» at most this often: the column
# feeds the account state («ingresó»), not an audit trail of every click.
_ACCESS_EVERY = timedelta(hours=1)


def _env(name):
    # Installation environment only (process env, then the env file) — never
    # a DB settings override: an admin screen must not be able to reopen the
    # password login or switch the second factor.
    return str(os.environ.get(name, _config.dotenv_values().get(name, "")) or "").strip()


def enabled():
    return _env("TURNOS_STAFF_PORTAL_LOGIN") == "1"


def totp_enabled():
    return enabled() and _env("TURNOS_STAFF_PORTAL_TOTP") == "1"


def portal_login_url():
    """Where a person without a portal session goes to sign in (optional)."""
    from app.cuentas import portal_identity
    return portal_identity.login_url()


def validate_configuration():
    """Boot check: the staff portal login needs the gateway trust boundary."""
    from app.cuentas import portal_identity
    if not portal_identity.enabled():
        raise portal_identity.ConfigurationError()
    portal_identity._transport()


@dataclass
class Resolution:
    """`reason`: ok · no_portal (no trusted gateway identity) · not_registered
    (identity present, no usable account) · totp (second factor pending) ·
    unavailable (trust configuration or database unusable)."""
    reason: str
    email: str = ""
    identity: dict | None = None
    operator: dict | None = field(default=None, repr=False)


class Credential:
    """Stands in for the admin cookie token on a portal installation. It holds
    the request and resolves lazily (in the threadpool, where the gate runs),
    once per request."""

    __slots__ = ("request", "_resolution")

    def __init__(self, request):
        self.request = request
        self._resolution = None

    def resolve(self, *, check_totp=True):
        if not check_totp:
            return resolve(self.request, check_totp=False)
        if self._resolution is None:
            self._resolution = resolve(self.request)
        return self._resolution


def _cookie(request, name):
    from http.cookies import SimpleCookie
    try:
        jar = SimpleCookie()
        jar.load(request.headers.get("cookie") or "")
        return jar[name].value if name in jar else None
    except Exception:  # noqa: BLE001 — a malformed cookie is simply absent
        return None


def _stamp(operator):
    return hashlib.sha256(("%s|%s|%s" % (operator.get("usuario"), operator.get("portal_user_id"),
                                          operator.get("totp_secret"))).encode()).hexdigest()


def _totp_serializer():
    from itsdangerous import URLSafeTimedSerializer
    from app.auth import admin_auth
    return URLSafeTimedSerializer(admin_auth._signing_key(), salt=_TOTP_SALT)


def totp_cookie(operator, headers):
    """Set-Cookie proving this operator's second factor for one session window."""
    from app.auth import admin_auth
    token = _totp_serializer().dumps({"s": _stamp(operator)})
    parts = ["%s=%s" % (TOTP_COOKIE, token), "Max-Age=%d" % admin_auth._max_age(),
             "Path=/", "HttpOnly", "SameSite=Lax"]
    if admin_auth._secure_cookie_from(headers):
        parts.append("Secure")
    return "; ".join(parts)


def _totp_proven(request, operator):
    from app.auth import admin_auth
    token = _cookie(request, TOTP_COOKIE)
    if not token:
        return False
    try:
        data = _totp_serializer().loads(token, max_age=admin_auth._max_age())
    except Exception:  # noqa: BLE001 — bad signature, expired or no signing key
        return False
    import hmac
    return isinstance(data, dict) and hmac.compare_digest(str(data.get("s") or ""), _stamp(operator))


def _mark_access(operator):
    last = operator.get("ultimo_acceso")
    if isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < _ACCESS_EVERY:
            return
    from app.auth import admin_auth
    admin_auth.marcar_acceso(operator["usuario"])


def resolve(request, *, check_totp=True):
    """Map this request's gateway identity to an operator (see module doc)."""
    from app.cuentas import portal_identity
    from app.core import db
    from app.core import db_operators
    try:
        assertion = portal_identity.gateway_assertion(request)
    except portal_identity.ConfigurationError:
        _LOG.warning("staff portal login: gateway trust configuration unusable")
        return Resolution("unavailable")
    if assertion is None:
        return Resolution("no_portal")
    email = assertion.email
    if not assertion.cuit:
        return Resolution("not_registered", email=email)
    try:
        operator = db_operators.operator_by_portal_id(assertion.subject)
        if operator is None and email:
            operator = db_operators.operator_claim_portal(email, assertion.subject, assertion.cuit)
    except db.DbUnavailable:
        return Resolution("unavailable", email=email)
    if not operator or not operator.get("activo"):
        return Resolution("not_registered", email=email)
    identity = {"usuario": operator["usuario"], "rol": operator.get("rol") or "medico"}
    if check_totp and totp_enabled() and operator.get("totp_secret") \
            and not _totp_proven(request, operator):
        return Resolution("totp", email=email, operator=operator)
    try:
        _mark_access(operator)
    except Exception:  # noqa: BLE001 — auditing never blocks the login
        pass
    return Resolution("ok", email=email, identity=identity, operator=operator)
