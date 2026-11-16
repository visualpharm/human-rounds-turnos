"""admin_auth.py — autenticación del panel administrativo (Pinamar Turnos).

El panel `/admin` pasó de una sola contraseña compartida (enviada en cada
request via `X-Admin-Pass`) a login real: **usuario + contraseña + 2FA (TOTP)
+ Cloudflare Turnstile**, con sesión por cookie firmada por `itsdangerous`
(stateless, sin
tabla nueva ni dependencia de la base).

Dos clases de cuentas, un solo login:
  - El **dueño** (env `TURNOS_ADMIN_USER`/`_PASS`/`_TOTP_SECRET`) es el
    super-admin de emergencia: siempre funciona, incluso con la base caída.
    Rol implícito 'admin'.
  - Los **operadores** (tabla `operadores` en Postgres, CRUD en
    admin_users.py) tienen usuario + contraseña propios (Argon2id, mismo
    esquema que las cuentas de paciente) y TOTP opcional por cuenta. Rol
    'admin' (todo), 'operador' (sólo la cola de solicitudes — ver
    require_rol_admin) o 'medico' (sólo sus propios turnos — ver
    permitir_medico más abajo). Si el usuario ingresado NO es el dueño, se
    busca en la tabla; la base caída sólo tumba el login de LOS OPERADORES,
    nunca el del dueño.

Contrato de cookie (FIJO): `st_admin`, HttpOnly, SameSite=Lax, Path=/,
Max-Age 30 días (`TURNOS_ADMIN_SESSION_DAYS` la cambia), y la sesión es
DESLIZANTE: cada `whoami` que encuentra el token pasada la mitad de su vida lo
re-emite, así que usar el panel la mantiene viva sola.
El token usa `itsdangerous.URLSafeTimedSerializer` y contiene
`{"v": 3, "u": usuario, "r": rol, "exp": epoch}`. El formato HMAC manual
anterior dejó de aceptarse al terminar su ventana máxima de 30 días. Cambiar
la contraseña del dueño invalida SUS sesiones (la clave se deriva de ella
salvo que se fije `TURNOS_ADMIN_SECRET`); las sesiones de operadores se cierran
cambiando su contraseña desde el panel (mismo criterio que las cuentas de
paciente) o desactivando la cuenta.

Todo se degrada con gracia para correr local (mismo patrón que HSI/Turnstile):
  - TURNOS_ADMIN_USER          usuario del dueño (default "admin")
  - TURNOS_ADMIN_PASS          contraseña del dueño — si falta, NADIE entra
                               como dueño (fail-closed; los operadores siguen
                               pudiendo entrar si la base está arriba)
  - TURNOS_ADMIN_TOTP_SECRET   base32; si está, el DUEÑO necesita código
  - TURNOS_TURNSTILE_SITEKEY   / TURNOS_TURNSTILE_SECRET — si están, se valida
                               el token del widget del lado del server
  - TURNOS_ADMIN_SECRET        clave de firma; si falta, se deriva de la pass
  - TURNOS_ADMIN_SESSION_DAYS  duración de la sesión en días (default 30)

Native routers delegate here through value-only results: `evaluate_admin_gate`,
`login_result`, `logout_result`, `config_result`, and `whoami_result`.

`permitir_medico`: por default una sesión de rol 'medico' es rechazada con
403 por CUALQUIER endpoint que pase por require_admin — el rol 'medico' es
de un solo uso (doctor_appointments.py: GET /api/admin/my-appointments) y todo lo demás
(cola de solicitudes, contenido, usuarios, reportes, fotos de médicos) le
queda cerrado SIN tener que tocar cada módulo — el gate central es acá.
`whoami_result()` and the doctor appointments router are the callers that pass
permitir_medico=True (whoami: el cliente necesita saber su propio rol para
armar el sidebar).

El acceso a la tabla de operadores importa `db` perezosamente dentro de
`login_result()`, nunca al tope del módulo. Turnstile sigue siendo puro
hashlib/hmac/urllib.

Tier 4 slice 1 (docs/roadmap/tier4-replatform-design.md, "Auth replacement",
2026-08-05): las cookies se mintean y verifican con
`itsdangerous.URLSafeTimedSerializer`. El formato HMAC manual anterior fue
aceptado durante una ventana de migración y retirado el 2026-09-03, después
del `Max-Age` máximo de 30 días. El TOTP usa `pyotp` (mismo RFC 6238 y secreto
base32 guardado, sin migración de datos).

Tier 4/native-migration F1 (docs/roadmap/native-migration-campaign-20260826.md):
Toda la DECISIÓN (verificar cookie,
matriz de roles, Turnstile/TOTP/throttle, qué status/body devolver, mintear
el Set-Cookie) vive en funciones PURAS que reciben valores planos (el string
    de la cookie, method/path, un mapping de headers, IP del peer, el
body ya leído) y devuelven un resultado (`GateResult`/`AuthResult`) — nunca
escriben directo a un request. `core.deps.require_admin`,
`admin_auth_router.py`, and `passkeys_router.py` call those value boundaries
directly; the retired handler transport no longer exists."""

import os
import re
import time
import logging
import hmac
import base64
import hashlib
import secrets
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Mapping

import pyotp
from itsdangerous import BadData, URLSafeTimedSerializer, base64_encode
from itsdangerous.url_safe import URLSafeSerializerMixin

from app.auth import turnstile
from app.auth import ip_exempt
from app.auth import staff_portal
from app.core import api_body
from app.core import rate_window
# alias: el módulo ya tiene una ruta config(handler) más abajo (GET
# /api/admin/config) que pisaría el nombre `config` si se importara tal cual.
from app.core import config as _config
from app.core import launch_signals
from app.core import site_identity
from app.ops import demo_installation
from app.core import permissions as perm_module
# alias: `db` se importa perezosamente más abajo en una función; acá sólo se
# necesita la clase de error para distinguir "almacén ilegible" de un bug.
from app.core import db as _db
from app.core import db_auth

COOKIE_NAME = "st_admin"
# 30 días (antes 12 h). La sesión además se RENUEVA sola en cada whoami que la
# encuentre pasada la mitad de su vida (ver `_debe_renovar`), así que quien usa
# el panel seguido no vuelve a ver el login nunca. Override: TURNOS_ADMIN_SESSION_DAYS.
COOKIE_MAX_AGE = 30 * 24 * 3600

# ---- env (mismo lector liviano que booking_requests._env) -----------------------

def _env(key, default=""):
    return _config.get(key, default)


def _admin_user():
    return _env("TURNOS_ADMIN_USER") or "admin"


def _admin_pass():
    return _env("TURNOS_ADMIN_PASS")


def _totp_secret():
    return re.sub(r"\s", "", _env("TURNOS_ADMIN_TOTP_SECRET") or "")


def _max_age():
    """Duración de la sesión admin en segundos. `TURNOS_ADMIN_SESSION_DAYS`
    la ajusta sin tocar código; un valor basura cae al default."""
    try:
        dias = float(_env("TURNOS_ADMIN_SESSION_DAYS") or 0)
    except ValueError:
        return COOKIE_MAX_AGE
    return int(dias * 24 * 3600) if dias > 0 else COOKIE_MAX_AGE


def _hay_secreto_real():
    """True si TURNOS_ADMIN_SECRET o TURNOS_ADMIN_PASS están configurados.
    Sin esto, _signing_key() colapsa a un literal fijo derivable por cualquiera
    (el mismo patrón que core/proxy_trust.py._hay_secreto_real)."""
    return bool(_env("TURNOS_ADMIN_SECRET") or _admin_pass())


def _signing_key():
    """Clave HMAC para firmar la cookie. Preferimos TURNOS_ADMIN_SECRET; si no,
    la derivamos de la contraseña para que un cambio de pass corte las sesiones.
    Requiere que al menos uno de los dos esté configurado (fail-closed)."""
    if not _hay_secreto_real():
        # Fail-closed: sin secreto real, la cookie es forjeable por cualquiera.
        raise RuntimeError("TURNOS_ADMIN_SECRET o TURNOS_ADMIN_PASS deben estar configurados")
    explicit = _env("TURNOS_ADMIN_SECRET")
    base = explicit or ("pass:" + _admin_pass())
    return hashlib.sha256(("st-admin$" + base).encode("utf-8")).digest()


def _secure_cookie_from(headers: Mapping[str, str] | None) -> bool:
    """`Secure` SÓLO cuando el request llegó por HTTPS de verdad. En cloud el
    proxy (Cloudflare/Coolify) manda `X-Forwarded-Proto: https`; el acceso
    directo por HTTP (localhost, la máquina de desarrollo por Tailscale) NO lo manda. Nunca inferir
    Secure del hostname: marcar la cookie Secure sobre una página HTTP hace que
    el navegador la descarte en silencio y la sesión "se cierra" apenas empieza."""
    try:
        proto = ((headers or {}).get("X-Forwarded-Proto") or "")
    except Exception:  # noqa: BLE001
        proto = ""
    proto = proto.split(",")[0].strip().lower()
    if proto == "https":
        return True
    # The installation's own public origin is the other witness (the patient cookie reads only
    # that): a proxy that forgets the header must not hand the staff cookie out without Secure
    # (security review 2026-11, finding 19). A plain-http dev origin still gets no Secure.
    try:
        return site_identity.base_url().lower().startswith("https://")
    except Exception:  # noqa: BLE001
        return False


def _set_cookie_from(headers: Mapping[str, str] | None, token: str) -> str:
    parts = ["%s=%s" % (COOKIE_NAME, token), "Max-Age=%d" % _max_age(),
             "Path=/", "HttpOnly", "SameSite=Lax"]
    if _secure_cookie_from(headers):
        parts.append("Secure")
    return "; ".join(parts)


def _clear_cookie_from(headers: Mapping[str, str] | None) -> str:
    parts = ["%s=" % COOKIE_NAME, "Max-Age=0", "Path=/", "HttpOnly", "SameSite=Lax"]
    if _secure_cookie_from(headers):
        parts.append("Secure")
    return "; ".join(parts)


def _parse_cookie_token(cookie_header):
    """Pure: el `Cookie` header crudo -> el valor de `st_admin`, o None. Toma
    un string plano para que `core.deps` (que lee `request.headers.get
    ("cookie")` directo, sin handler) compartan el mismo parseo."""
    raw = cookie_header or ""
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == COOKIE_NAME:
                return v.strip()
    return None


class GateResult:
    """Resultado de `evaluate_admin_gate`: o una identidad válida, o el
    status+body exacto que un request no autenticado/no autorizado recibe.
    `ok` es la forma corta de `identity is not None` — un GateResult nunca
    tiene AMBOS a la vez."""

    __slots__ = ("identity", "status", "body", "permissions", "security")

    def __init__(self, identity=None, status=None, body=None, permissions=None,
                 security=None):
        self.identity = identity
        self.status = status
        self.body = body
        # Effective permission ids of an admitted identity (frozenset).
        self.permissions = permissions if permissions is not None else frozenset()
        # The account_security() state the gate already loaded for an admitted
        # identity (tiene_2fa/2fa_deadline/2fa_vencido): screens render the
        # enrolment nudge from this instead of re-reading the store.
        self.security = security

    @property
    def ok(self):
        return self.identity is not None


class AuthResult:
    """Resultado de las funciones puras de login/logout/whoami/config/
    passkeys: status + body (el dict que se serializa a JSON) + un
    Set-Cookie opcional — nunca escribe nada, el caller (el handler-shaped
    wrapper o el router nativo) decide cómo emitirlo."""

    __slots__ = ("status", "body", "set_cookie")

    def __init__(self, status, body, set_cookie=None):
        self.status = status
        self.body = body
        self.set_cookie = set_cookie


def parse_json_body(raw_bytes):
    """Un body vacío es `{}`, sólo un JSON
    genuinamente mal formado levanta `json.JSONDecodeError`. Pura — toma los
    bytes crudos del body, nunca un handler — así login_result() puede
    reproducir exactamente `login(handler)`'s "JSON inválido: {e}" sin
    depender de qué objeto se los dio."""
    return api_body.decode(raw_bytes)


# ---- token de sesión firmado (stateless) -----------------------------------

TOKEN_VERSION = 3  # v3 invalida cookies de los roles retirados operador/marca

# ---- gate de sólo-lectura de la cuenta demo (público, ver demo_installation.py) --
#
# La sesión "demo" NUNCA es una fila de `operators`: /admin/demo-access mintea
# rol="admin" a propósito para que vea TODAS las secciones sin tocar
# applyRol/routeCurrentAdminScreen. El único bloqueo real es acá abajo: toda
# mutación queda cerrada salvo logout (así se puede salir del panel sin que
# la sesión demo quede pegada).
# «Descargar» (table-export) writes nothing but the access rows of the file.
_DEMO_MUTATION_ALLOW = {"/api/admin/logout", "/api/admin/table-export"}


def _es_cuenta_demo(identity):
    """True si `identity` es la sesión sintética del demo público — nunca una
    cuenta real (dueño u operador), y sólo si TURNOS_DEMO_MODE está prendido:
    en producción esta función siempre da False, aunque alguien se llame
    "demo" por coincidencia."""
    if not identity:
        return False
    return demo_installation.enabled() and identity.get("usuario") == demo_installation.demo_user()


# Salt fija de la serialización itsdangerous — separa este uso de cualquier
# otro que llegara a usar la misma _signing_key() con otro propósito (no hay
# ninguno hoy, pero itsdangerous soporta salts justo para esto y no cuesta
# nada fijarla).
_ITSD_SALT = "st-admin-v3"


class _PlainPayloadSerializer(URLSafeTimedSerializer):
    """`URLSafeTimedSerializer` that never zlib-compresses the payload it signs: every cookie it
    makes is plain base64 JSON. That is the guarantee; nothing else about the cookie changes.

    The stock serializer compresses whenever zlib comes out shorter, and with this payload that
    depends on what is in it: on samples, a username equal to its role (`admin`/`admin`) and a
    renewed cookie (`sid` beside the `nonce`) compressed every time, and another account's fresh
    cookie from never to most of the time, depending on the username's length and the account's
    password stamp (`cs`) — the measured rates are in the decision log, as samples. A compressed
    cookie's first segment is `.` + compressed bytes instead of base64 JSON, which the e2e journeys
    read (tests/e2e J28 forges a promoted copy from it). Loading is unchanged, and itsdangerous reads
    both forms: a compressed cookie already handed out still verifies, and so does a plain one in a
    release from before this class."""

    def dump_payload(self, obj):
        return base64_encode(super(URLSafeSerializerMixin, self).dump_payload(obj))


def _itsdangerous_serializer():
    return _PlainPayloadSerializer(_signing_key(), salt=_ITSD_SALT)


def _validar_payload(data):
    """Los chequeos de contenido, iguales para el token nuevo (itsdangerous)
    y el viejo (HMAC a mano): versión, vencimiento, usuario y rol conocido.
    Devuelve {"usuario","rol"} o None. El campo "exp" es NUESTRO (absoluto,
    epoch), no el timestamp interno de itsdangerous — así un cambio de
    TURNOS_ADMIN_SESSION_DAYS después de mintear un token no le cambia el
    vencimiento ya fijado, igual que se comportaba antes de este slice.

    Roles are editable (core/permissions.py): any well-formed role
    slug is a session here, and the gate decides against the `roles` table
    (an unknown or disabled role answers 403 there). The retired roles
    'operador'/'marca' stay exactly as before: not a session at all."""
    if data.get("v") != TOKEN_VERSION:
        return None
    if int(data.get("exp") or 0) < int(time.time()):
        return None
    usuario = data.get("u") or None
    if not usuario:
        return None
    rol = data.get("r") or "admin"
    if (not isinstance(rol, str) or rol in perm_module.RETIRED_ROLES
            or not perm_module.SLUG_RE.match(rol)):
        return None
    return {"usuario": usuario, "rol": rol}


DEMO_SESSION_SECONDS = 12 * 3600


def _session_seconds(usuario):
    """Length of a new session. The public demo's one-click sessions (admin and doctor) are open
    to anyone and are never revoked by a password change, so they live hours, not the staff
    session length (security review 2026-11, L-1); an active demo visitor is renewed as usual."""
    if demo_installation.enabled() and usuario in (
            demo_installation.demo_user(), demo_installation.demo_doctor_user()):
        return min(DEMO_SESSION_SECONDS, _max_age())
    return _max_age()


def _is_demo_session_user(usuario):
    return bool(demo_installation.enabled() and usuario in (
        demo_installation.demo_user(), demo_installation.demo_doctor_user()))


def _mint_token(usuario, rol="admin", sid=None):
    # rol por defecto "admin": los tests viejos (test_booking_requests.py,
    # test_admin_content.py) siguen llamando _mint_token(usuario) con un
    # solo argumento — y ese usuario siempre es el dueño, que ES admin.
    #
    # Tier 4 slice 1: se mintea SIEMPRE con itsdangerous de acá en adelante
    # `itsdangerous` owns serialization and signing. The payload remains an
    # explicit project contract so role and absolute expiry checks stay
    # visible and testable.
    #
    # `sid` identifies the SESSION, not the token: a renewal or the re-issue after a
    # password change passes the sid of the cookie it replaces, so «Cerrar sesión»
    # (which lists the sid, see `revoke_session`) also closes every earlier copy.
    #
    # A first token has no `sid` of its own: its `nonce` is the session id, so the payload stays
    # byte-for-byte what it was (a duplicate random string would make itsdangerous compress it).
    payload = {"v": TOKEN_VERSION, "u": usuario, "r": rol, "exp": int(time.time()) + _session_seconds(usuario),
               "nonce": secrets.token_urlsafe(24)}
    if sid:
        payload["sid"] = sid
    if _is_demo_session_user(usuario):
        # The cookie says it is a demo session: with TURNOS_DEMO_MODE off it is no session at all
        # (security review 2026-11, finding 8), whatever the signing key still is.
        payload["d"] = 1
    stamp = _password_stamp(usuario)
    if stamp:
        payload["cs"] = stamp
    tstamp = _totp_stamp(usuario)
    if tstamp:
        payload["ts"] = tstamp
    return _itsdangerous_serializer().dumps(payload)


def _password_stamp(usuario):
    """Short fingerprint of the account's current stored password hash, or None when it cannot be
    known (no password of ours: the env owner, a missing row, an unreadable store).

    The cookie carries it and `_verify_token` compares it with the live one, so a new password —
    chosen by the person or reset by an admin — closes every session opened with the old one
    (security review 2026-11-04; a lost cookie or a former holder of the old password otherwise kept
    the panel for the whole session length). Short on purpose: a long hex string would make
    itsdangerous compress the payload, which the e2e journeys read as plain base64 JSON."""
    try:
        if usuario == _admin_user():
            secret = (db_auth.admin_security_get(usuario) or {}).get("owner_pass_hash")
        else:
            from app.core import db_operators
            secret = db_operators.operator_pass_hash(usuario)
    except Exception:  # noqa: BLE001 — degrade like the revocation store: no binding, never a lockout
        return None
    if not secret:
        return None
    digest = hashlib.sha256(("pw|" + str(secret)).encode()).digest()[:12]
    return base64.urlsafe_b64encode(digest).decode()


def _totp_stamp(usuario):
    """Short fingerprint of the account's current 2FA secret (an account without one has its own
    fingerprint), or None when it cannot be known. Same job as `_password_stamp`: enrolling,
    replacing or removing the authenticator closes every other session of the account (security
    review 2026-11, L-1). A cookie minted before this field existed carries none and stays valid."""
    try:
        if usuario == _admin_user():
            row = db_auth.admin_security_get(usuario) or {}
            secret = row.get("owner_totp_secret") if row.get("owner_totp_set") else _totp_secret()
        else:
            from app.core import db_operators
            row = db_operators.operator_get(usuario)
            if row is None:
                return None
            secret = row.get("totp_secret")
    except Exception:  # noqa: BLE001 — degrade like the password stamp: no binding, never a lockout
        return None
    digest = hashlib.sha256(("2fa|" + str(secret or "")).encode()).digest()[:9]
    return base64.urlsafe_b64encode(digest).decode()


# ---- sesiones cerradas con «Cerrar sesión» -----------------------------------
#
# The cookie is stateless, so a copy made before signing out used to keep working
# until its expiry (TASK-55). Signing out now lists the session id in
# `admin_session_revocations` and `_verify_token` refuses a listed id. A token
# issued before the ids existed has none: its own `nonce` is its session id.

_LOG = logging.getLogger(__name__)
_REVOKED_KEEP = 4096          # ids remembered by this process (bounded)
_REVOKED_LOCAL = {}           # sid -> epoch until which the id may matter
_STORE_WARNED = [0.0]


def _session_id(data):
    """The session id of a verified payload, or None for a payload that has neither."""
    sid = data.get("sid") or data.get("nonce")
    return sid if isinstance(sid, str) and sid else None


def _signed_payload(token):
    """The payload of a token WE signed (expiry NOT checked), or None."""
    if not isinstance(token, str) or not token:
        return None
    try:
        data = _itsdangerous_serializer().loads(token)
    except Exception:  # noqa: BLE001 — BadData, or no signing secret configured
        return None
    return data if isinstance(data, dict) else None


def session_id_of(token):
    """The session id of a cookie value we signed (None for anything else, e.g. a portal credential)."""
    data = _signed_payload(token)
    return _session_id(data) if data else None


def _remember_revoked(sid, until):
    now = time.time()
    if len(_REVOKED_LOCAL) >= _REVOKED_KEEP:
        for key in [k for k, v in _REVOKED_LOCAL.items() if v < now]:
            del _REVOKED_LOCAL[key]
        if len(_REVOKED_LOCAL) >= _REVOKED_KEEP:
            _REVOKED_LOCAL.clear()
    _REVOKED_LOCAL[sid] = until


def _is_revoked(sid, exp):
    """True when `sid` was signed out. An unreadable store degrades (the gate does the same for
    permissions, owner decision 2026-09-12) — except for the ids this process already knows;
    any other failure refuses the token, it never lets it in silently."""
    if sid is None:
        return False
    if _REVOKED_LOCAL.get(sid, 0) >= time.time():
        return True
    try:
        listed = db_auth.admin_session_is_revoked(sid)
    except _db.DbUnavailable:
        if time.time() - _STORE_WARNED[0] > 60:
            _STORE_WARNED[0] = time.time()
            _LOG.warning("session revocation store unreadable; signed-out sessions are not checked")
        return False
    except Exception as exc:  # noqa: BLE001
        # No traceback or message in the log (test_logs_pii): the type says what broke.
        _LOG.error("session revocation check failed (%s); refusing the token", type(exc).__name__)
        return True
    if listed:
        _remember_revoked(sid, exp)
    return listed


def revoke_session(token):
    """Sign out the session of `token`: list its id so no copy of it works again.

    A token we did not sign (garbage, another secret, a portal credential) has nothing to
    revoke and is ignored. The listing outlives every copy of the session: the longest a
    token of the session could have been renewed to (now + the session length), or the
    token's own expiry if later (the length setting may have been lowered since).
    Raises when the store cannot be written; the caller reports it."""
    data = _signed_payload(token)
    sid = _session_id(data) if data else None
    if sid is None:
        return False
    now = time.time()
    until = max(int(data.get("exp") or 0), now + max(_max_age(), COOKIE_MAX_AGE)) + 24 * 3600
    db_auth.admin_session_revoke(sid, str(data.get("u") or ""), until)
    _remember_revoked(sid, until)
    return True


def request_credential(request):
    """What identifies the staff member behind `request`: the `st_admin`
    cookie token, or — on an installation with the portal login
    (auth/staff_portal.py) — the gateway identity, resolved lazily by
    `_verify_token`. Every gate caller reads it through here."""
    if staff_portal.enabled():
        return staff_portal.Credential(request)
    return _parse_cookie_token(request.headers.get("cookie"))


def _verify_token(token):
    """Devuelve {"usuario","rol"} si la firma, versión y vencimiento del token
    son válidos, si no None. Nunca levanta.

    Sólo acepta el formato mantenido por `itsdangerous`. La compatibilidad de
    lectura con la cookie HMAC manual terminó después de su ventana máxima de
    30 días; un formato desconocido vuelve al login.

    On a portal-login installation the only credential is the per-request
    gateway identity: a cookie token — even a validly signed one — is not a
    session there."""
    if isinstance(token, staff_portal.Credential):
        resolution = token.resolve()
        return dict(resolution.identity) if resolution.identity else None
    if token is None or staff_portal.enabled():
        return None
    try:
        data = _itsdangerous_serializer().loads(str(token))
    except BadData:
        return None
    except Exception:  # noqa: BLE001
        return None
    identidad = _validar_payload(data)
    if identidad is not None:
        if data.get("d") and not demo_installation.enabled():
            return None   # a demo session, and this installation is no demo any more
        if _is_revoked(_session_id(data), int(data.get("exp") or 0)):
            return None
        bound = data.get("cs")
        if bound:
            live = _password_stamp(identidad["usuario"])
            if live is not None and not hmac.compare_digest(str(bound), live):
                return None   # the password changed after this session was opened
        live_2fa = _totp_stamp(identidad["usuario"])
        if live_2fa is not None:
            bound_2fa = data.get("ts")
            # A cookie with no stamp is older than the revocation (or forged from one): it cannot
            # prove the authenticator did not change, so it is not a session (finding 9).
            if not bound_2fa or not hmac.compare_digest(str(bound_2fa), live_2fa):
                return None   # the authenticator changed after this session was opened
        return identidad
    # Verificó la firma nueva pero el contenido no pasa (vencido, rol
    # desconocido, etc.) -- no tiene sentido probar el formato viejo con el
    # mismo payload, ya se sabe que no es válido.
    return None


def _debe_renovar(token):
    """True si el token ya pasó la mitad de su vida. La cookie es stateless, así
    que la única forma de que una sesión activa no venza es RE-EMITIRLA mientras
    la persona usa el panel (sliding session). Se renueva a mitad de camino y no
    en cada request para no mandar Set-Cookie de más.

    Sólo las cookies válidas de `itsdangerous` participan en la renovación."""
    try:
        data = _itsdangerous_serializer().loads(str(token))
    except Exception:  # noqa: BLE001
        return False
    try:
        exp = int(data.get("exp") or 0)
    except Exception:  # noqa: BLE001
        return False
    return (exp - int(time.time())) < (_max_age() // 2)


# ---- TOTP (RFC 6238, SHA1, 6 dígitos, paso 30 s) ---------------------------
# `pyotp` is the sole implementation. The former hand-written HOTP/TOTP
# reference implementation was removed after the migration parity window;
# keeping it as a test oracle would preserve the custom auth code this
# refactor is required to retire.


def _totp_valido(codigo, secret, window=1):
    """True si `codigo` matchea el TOTP en ±window pasos (tolerancia de reloj)."""
    codigo = re.sub(r"\s", "", str(codigo or ""))
    if not re.fullmatch(r"\d{6}", codigo):
        return False
    secreto_limpio = re.sub(r"\s", "", str(secret or "")).upper()
    if not secreto_limpio:
        return False
    try:
        return bool(pyotp.TOTP(secreto_limpio).verify(codigo, valid_window=window))
    except Exception:  # noqa: BLE001 — secreto base32 corrupto: "no verifica", no 500
        return False


# ---- Cloudflare Turnstile --------------------------------------------------
# El cálculo de IP del cliente vive en un solo lugar (turnstile.client_ip) —
# este módulo tenía su propia copia idéntica, movida acá el 2026-08-03 al
# armar la excepción por IP (ver ip_exempt.py).


def _turnstile_ok_from(headers: Mapping[str, str] | None,
                       client_host: str, token: str) -> bool:
    """Value-only admin Turnstile decision for native login routes."""
    if ip_exempt.exento_from(headers, client_host):
        return True
    return turnstile.verificar_from(headers, client_host, token)


# ---- throttle de intentos por IP (en memoria, best-effort) -----------------

_ATTEMPTS = {}          # ip -> [epochs]
_MAX_ATTEMPTS = 8       # por ventana
_WINDOW = 300           # 5 min
# Techo del mapa (endurecimiento 2026-09-15): `_throttled` poda la lista de
# UNA ip cuando esa ip vuelve, pero una ip que nunca vuelve dejaba su entrada
# para siempre — un atacante que rote IPs reales (o cualquier glitch que
# registre pares distintos) crecía el mapa sin límite en un proceso de vida
# larga. Pasado el tope, `_record_attempt` descarta las entradas
# completas-mente vencidas antes de insertar; los contadores vivos (dentro
# de la ventana) nunca se podan. Best-effort, igual que el resto del
# throttle: en memoria, sin persistencia.
_MAX_TRACKED_IPS = 4096


def _account_attempt_key(usuario):
    """The throttle key of a typed account name (same map as the addresses: `|` cannot be in an IP)."""
    name = str(usuario or "").strip().casefold()[:120]
    return "u|" + name if name else None


def _throttled(ip):
    return rate_window.over_limit(_ATTEMPTS, ip, _MAX_ATTEMPTS, _WINDOW)


def _record_attempt(ip):
    launch_signals.count("staff_login_failed")  # a wrong password / code / bot check: the launch digest's number
    if len(_ATTEMPTS) >= _MAX_TRACKED_IPS:
        now = time.time()
        for k in [k for k, xs in _ATTEMPTS.items()
                  if all(now - t >= _WINDOW for t in xs)]:
            del _ATTEMPTS[k]
    _ATTEMPTS.setdefault(ip, []).append(time.time())


def _clear_attempts(ip):
    _ATTEMPTS.pop(ip, None)


# ---- rol de la request para el render de pantallas directas ------------------
#
# El mismo patrón request-scoped que i18n.bind()/active() (ContextVar, ver
# core/i18n_middleware.py): deps.require_admin — la ÚNICA compuerta que toda
# pantalla directa /admin/* ya atraviesa — bindea el rol de la sesión que
# acaba de verificar, y jinja_env lo expone a templates/admin/base.html como
# {{ screen_rol() }}. Una sola fuente real (la sesión verificada del lado
# servidor), cero edición por ruta, y nunca una variable Jinja indefinida que
# renderice vacía y apague el widget en silencio (la clase de bug que este
# slice vino a cerrar: la campanita de triage se inicializaba con un rol que
# nadie pasaba). Sólo lectura por pantalla: el gate de cada API sigue siendo
# require_admin, este valor no autoriza nada por sí mismo.

_ACTIVE_ROLE: ContextVar[str] = ContextVar("admin_active_role", default="")


def bind_role(rol):
    """Request-scoped: the verified session's role, for the render only."""
    _ACTIVE_ROLE.set(str(rol or ""))


def active_role():
    """The role require_admin verified for THIS request ('' if none did)."""
    return _ACTIVE_ROLE.get()


_ACTIVE_PERMISSIONS: ContextVar[tuple] = ContextVar("admin_active_permissions", default=())


def bind_permissions(permissions):
    """Request-scoped: the verified effective permissions, for the render and
    the sidebar only — every API still runs the gate itself."""
    _ACTIVE_PERMISSIONS.set(tuple(sorted(permissions or ())))


def active_permissions():
    """Sorted permission ids require_admin verified for THIS request."""
    return list(_ACTIVE_PERMISSIONS.get())


# Mismo patrón request-scoped que screen_rol()/screen_permisos(): el gate
# admite la sesión y DEJA su estado de 2FA al alcance del render. La pantalla
# Account ya avisaba "Activá 2FA antes del {fecha}"; pero el médico vive en
# /admin/cola y nadie le avisaba antes del corte del día 8. Con esto, el
# base.html de TODA pantalla directa puede pintar el recordatorio con la
# fecha exacta. Sólo lectura: autoriza nada, sólo informa.
_ACTIVE_2FA: ContextVar[dict] = ContextVar("admin_active_2fa", default={})

_MESES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def bind_2fa(security):
    """Request-scoped: the admitted session's 2FA state, for the render only."""
    info = dict(security or {})
    deadline = info.get("2fa_deadline")
    if info.get("tiene_2fa") or not deadline:
        _ACTIVE_2FA.set({})
        return
    try:
        from datetime import datetime, timezone
        dt = deadline
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        info["dia_mes"] = (local.day, local.month)
        info["fecha"] = f"{local.day} de {_MESES_ES[max(0, local.month - 1)]}"
    except Exception:  # noqa: BLE001 — la fecha es un adorno, nunca un bloqueo
        info["fecha"] = ""
    _ACTIVE_2FA.set(info)


def _fecha_en_idioma(dia_mes):
    """«3 de noviembre» / «November 3»: the whole phrase per language, never a
    Spanish month inside an English sentence."""
    from app.core import i18n
    lang = i18n.active()
    dia, mes = dia_mes
    mes_txt = i18n.t_or(f"date.month_full.{mes}", _MESES_ES[max(0, mes - 1)], lang)
    return i18n.t_or("date.day_month", "{n} de {month}", lang, n=dia, month=mes_txt)


def active_2fa():
    """The 2FA-enrolment nudge state for THIS request ({} = no nudge).

    Sólo devuelve algo cuando falta el segundo factor Y hay fecha límite:
    `2fa_vencido` nunca llega acá renderizable (el gate 403-redirecteó a la
    pantalla Account antes de pintar nada)."""
    info = _ACTIVE_2FA.get()
    if not info or info.get("tiene_2fa") or info.get("2fa_vencido"):
        return {}
    if info.get("dia_mes"):
        info = dict(info, fecha=_fecha_en_idioma(info["dia_mes"]))
    return info


# ---- API pública que consume booking_requests.py --------------------------------

def _holds_everything(identity):
    """The owner (env login) and the public demo session hold every
    permission without a database read — the emergency login keeps working
    with Postgres down, exactly as before roles became editable."""
    if identity.get("rol") != "admin":
        return False
    return identity.get("usuario") == _admin_user() or _es_cuenta_demo(identity)


def evaluate_admin_gate(cookie_token, method, path, solo_admin=False,
                        permitir_medico=False, permitir_admisionista=False,
                        permitir_reportes=False, permitir_marca=False, route=None):
    """Evaluate the staff gate from explicit request values.

    Returns a ``GateResult`` and never writes an HTTP response. The shared
    FastAPI dependency is the only transport adapter, so access policy has one
    source of truth and no handler-shaped compatibility surface.

    Access is permission-based (core/permissions.py, R5/R6): the route
    template (``route``; the concrete ``path`` when a caller has no template)
    maps to one permission, and the account's CURRENT role preset plus its
    per-user overrides must hold it. The ``solo_admin``/``permitir_*`` flags
    are the pre-roles contract every call site still declares: they decide
    only a route the permission map does not know (the four legacy roles
    only — any other role is denied) and keep the pre-roles error text when
    both systems deny. ``permitir_marca`` stays accepted for call-site
    compatibility; that role is retired and its cookies are not sessions.

    The owner and the demo session hold every permission without touching the
    database. For every other account a failed lookup DEGRADES to the
    pre-roles, role-based decision (the legacy flags alone decide, per-user
    overrides do not apply until the store is readable again), so each
    endpoint keeps answering exactly what its own contract promises; an
    unknown role 403 ``rol_no_autorizado``, a disabled one 403
    ``rol_deshabilitado``."""
    identity = _verify_token(cookie_token)
    if not identity:
        return GateResult(status=401, body={"error": "no_autenticado"})
    security = account_security(identity, ensure_deadline=True)
    path = str(path or "").split("?", 1)[0]
    security_paths = ("/api/admin/account", "/api/admin/whoami", "/api/admin/logout")
    # The direct self-service screen (/admin/account) is the only UI that can
    # repair an expired 2FA deadline, so it is admitted by EXACT path equality
    # — never a prefix: /admin/accounts, /admin/account/x and any other nearby
    # lookalike keep failing closed, and no unrelated /admin/* path widens.
    repair_screens = ("/admin/account",)
    if (security.get("2fa_vencido")
            and path not in repair_screens
            and not any(path.startswith(p) for p in security_paths)):
        return GateResult(status=403, body={"error": "2fa_requerido",
                                            "deadline": security.get("2fa_deadline")})

    degraded = False
    if _holds_everything(identity):
        access = {"slug": "admin", "state": "ok", "permissions": perm_module.ALL}
    else:
        try:
            access = perm_module.access_for(identity.get("usuario"), identity.get("rol"))
        except _db.DbUnavailable:
            # SÓLO el almacén ilegible degrada: cualquier otro error sigue
            # siendo un error. Un `except Exception` acá convertiría un bug
            # de `access_for` en un permiso concedido en silencio.
            # Decisión del dueño (2026-09-12): si el almacén de permisos no se
            # puede leer, el portón vuelve al comportamiento POR ROL de siempre
            # y cada endpoint responde lo que su contrato ya promete (200, 403
            # o su propio cuerpo de base caída). Los permisos por usuario
            # simplemente no aplican hasta que el almacén vuelva.
            logging.getLogger(__name__).warning(
                "permission store unreadable; falling back to role-based access")
            degraded = True
            rol_token = identity.get("rol")
            preset = perm_module.BUILTIN_ROLES.get(rol_token) or {}
            access = {"slug": rol_token, "state": "ok",
                      "permissions": (perm_module.ALL if rol_token == "admin"
                                      else frozenset(preset.get("permissions", ())))}
    if access["state"] == "inactive":
        return GateResult(status=401, body={"error": "no_autenticado"})
    if access["state"] == "unknown":
        return GateResult(status=403, body={"error": "rol_no_autorizado"})
    if access["state"] == "disabled":
        return GateResult(status=403, body={"error": "rol_deshabilitado"})
    # The account's current role wins over the one signed at login: a role
    # change applies on the next request.
    rol = access["slug"]
    identity = dict(identity, rol=rol)
    held = access["permissions"]
    flags = dict(solo_admin=solo_admin, permitir_medico=permitir_medico,
                 permitir_admisionista=permitir_admisionista,
                 permitir_reportes=permitir_reportes)
    required = (perm_module.UNMAPPED if degraded
                else perm_module.required_permission(method, route or path, path, role=rol))
    if required is perm_module.UNMAPPED:
        denial = perm_module.legacy_denial(rol, **flags)
    elif perm_module.holds(required, held):
        denial = None
    else:
        denial = (perm_module.legacy_denial(rol, **flags)
                  if rol in perm_module.LEGACY_ROLES else None) or "rol_no_autorizado"
    if denial:
        return GateResult(status=403, body={"error": denial})

    # Sólo-lectura de la cuenta demo pública: cualquier mutación (POST/PUT/
    # DELETE) que no sea el logout responde 403 acá, el único choke point que
    # atraviesan los 71 call sites de /api/admin/*. Los botones del panel
    # siguen visibles (el cliente no sabe de esto); el servidor rechaza.
    if ((_es_cuenta_demo(identity) or (demo_installation.enabled()
             and identity.get("rol") == "medico"
             and identity.get("usuario") == demo_installation.demo_doctor_user()))
            and str(method or "GET").upper() in ("POST", "PUT", "PATCH", "DELETE")
            and path not in _DEMO_MUTATION_ALLOW):
        return GateResult(status=403, body={"error": "demo_solo_lectura"})
    return GateResult(identity=identity, permissions=held, security=security)


def centros_permitidos(identity):
    """Los centros de salud que esa cuenta puede ver, o None = todos.

    Nace del pedido de Gabriel en la reunión del 2026-07-24: la mesa de
    admisión de un CAPS no tiene por qué ver la jornada de los otros nueve.
    Es un ACOTAMIENTO, no un permiso nuevo: una cuenta sin centros cargados
    sigue viendo todo, que es como venía funcionando. El dueño (env) no vive
    en la tabla de operadores, así que nunca queda acotado.

    None (todos) también es la respuesta si la base no está: el alcance es
    una comodidad organizativa, no un secreto — cortar el acceso entero
    porque Postgres se cayó sería peor que mostrar de más a alguien que ya
    está autenticado."""
    usuario = (identity or {}).get("usuario")
    if not usuario:
        return None
    try:
        from app.core import db_operators
        row = db_operators.operator_get(usuario)
    except Exception:  # noqa: BLE001
        return None
    slugs = [s for s in ((row or {}).get("centros") or []) if s]
    return set(slugs) or None


def owner_usuario():
    """Usuario del dueño (env) — admin_users.py lo usa para blindar esa
    cuenta contra edición/desactivación desde el CRUD de operadores."""
    return _admin_user()


def owner_tiene_totp():
    return bool(account_totp_secret(_admin_user()))


def _owner_row():
    """Overrides del dueño guardados en Postgres, o {} si no hay base."""
    try:
        from app.core import db_auth
        return db_auth.admin_security_get(_admin_user()) or {}
    except Exception:  # noqa: BLE001 — sin base el dueño entra igual
        return {}


def owner_email():
    return (_owner_row().get("owner_email") or "") or None


def owner_ultimo_acceso():
    valor = _owner_row().get("owner_ultimo_acceso")
    return valor.isoformat() if hasattr(valor, "isoformat") else valor


def account_email(usuario):
    """Email de CUALQUIER cuenta del panel. El dueño no vive en `operadores`,
    así que su email sale de los overrides; el resto, de su fila."""
    if usuario == _admin_user():
        return owner_email()
    try:
        from app.core import db_operators
        return (db_operators.operator_get(usuario) or {}).get("email") or None
    except Exception:  # noqa: BLE001
        return None


def marcar_acceso(usuario):
    """Registra una visita completada, sea del dueño o de un operador. Nunca
    bloquea el ingreso: la auditoría no es parte de la autenticación."""
    launch_signals.count("staff_login_ok")
    try:
        from app.core import db_operators
        from app.core import db_auth
        if usuario == _admin_user():
            db_auth.admin_security_owner_access(usuario)
        else:
            db_operators.operator_mark_access(usuario)
    except Exception:  # noqa: BLE001
        pass


def config_result(headers: Mapping[str, str] | None) -> AuthResult:
    """Puerto PURO de `config(handler)`: GET /api/admin/config's body entero,
    a partir de un mapping de headers (para el chequeo de host de desarrollo
    de Turnstile), nunca un request. `admin_auth_router.py` llama acá directo."""
    if staff_portal.enabled():
        # No password form on a portal installation: no anti-bot widget and
        # no owner account to describe.
        return AuthResult(200, {"turnstile_sitekey": "", "totp": False, "usuario": "",
                                "acceso_portal": True})
    return AuthResult(200, {
        "turnstile_sitekey": turnstile.sitekey_from(headers),
        "totp": owner_tiene_totp(),
        "usuario": _admin_user(),
    })


def whoami_result(headers: Mapping[str, str] | None,
                  method: str, path: str, credential=None) -> AuthResult:
    """Puerto PURO de `whoami(handler)`: la identidad de la sesión ya
    autenticada (o el 401/403 del gate) más la renovación de cookie a mitad
    de vida. `admin_auth_router.py` llama ACÁ directo con
    `request_credential(request)`."""
    cookie_token = (credential if credential is not None
                    else _parse_cookie_token(headers.get("Cookie")))
    gate = evaluate_admin_gate(cookie_token, method, path, permitir_medico=True,
                               permitir_admisionista=True, permitir_reportes=True,
                               permitir_marca=True)
    if not gate.ok:
        return AuthResult(gate.status, gate.body)
    identity = gate.identity
    # El panel llama a whoami al arrancar: es el punto natural para renovar la
    # cookie y que la sesión no venza mientras se la usa.
    cookie = None
    if _debe_renovar(cookie_token):
        cookie = _set_cookie_from(
            headers, _mint_token(identity["usuario"], identity["rol"],
                                 sid=session_id_of(cookie_token)),
        )
    status = account_security(identity, ensure_deadline=True)
    body = {"usuario": identity["usuario"], "rol": identity["rol"],
            "permisos": sorted(gate.permissions), **status}
    if staff_portal.enabled():
        body["acceso_portal"] = True
    return AuthResult(200, body, set_cookie=cookie)


def account_totp_secret(usuario, strict=False):
    """Secreto 2FA efectivo de una cuenta. El dueño puede tener un override
    en DB; si no existe o la DB cae, conserva el secreto del env.

    `strict=True` is for a decision that must not read an unreadable store as «no authenticator»
    (the 2FA enrolment start, security review 2026-11, finding 11): a read error of an operator
    row raises instead of answering an empty secret."""
    if usuario == _admin_user():
        try:
            from app.core import db_operators
            from app.core import db_auth
            row = db_auth.admin_security_get(usuario)
            if row and row.get("owner_totp_set"):
                return row.get("owner_totp_secret") or ""
        except Exception:  # noqa: BLE001
            pass
        return _totp_secret()
    try:
        from app.core import db_operators
        from app.core import db_auth
        row = db_operators.operator_get(usuario)
        return (row or {}).get("totp_secret") or ""
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return ""


def account_security(identity, ensure_deadline=False):
    """Estado público del 2FA actual. La ausencia de DB nunca impide el acceso
    de emergencia del dueño; simplemente no puede iniciar el plazo."""
    usuario = identity.get("usuario") if identity else ""
    # The public sandbox uses a short-lived signed session created by
    # /admin/demo-access. It has no real operator account or second factor;
    # production is unaffected because the exception is doubly opt-in.
    if (demo_installation.enabled()
            and (usuario == demo_installation.demo_user()
                 or (identity.get("rol") == "medico"
                     and usuario == demo_installation.demo_doctor_user()))):
        # The demo session is the sandbox gate; presenting the real enrolment
        # policy would ask every visitor to configure a private second factor
        # for a deliberately shared environment.
        return {"tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False}
    tiene_2fa = bool(account_totp_secret(usuario))
    if staff_portal.enabled() and not staff_portal.totp_enabled():
        # Portal login without our TOTP: the portal owns the second factor,
        # so no enrolment deadline is started or enforced here.
        return {"tiene_2fa": tiene_2fa, "2fa_deadline": None, "2fa_vencido": False,
                "2fa_no_aplica": True}
    deadline = None
    try:
        from app.core import db_auth
        row = (db_auth.admin_security_ensure_deadline(usuario, tiene_2fa)
               if ensure_deadline else db_auth.admin_security_get(usuario))
        deadline = (row or {}).get("totp_deadline")
    except Exception:  # noqa: BLE001
        deadline = None
    vencido = bool(deadline and deadline <= datetime.now(timezone.utc) and not tiene_2fa)
    return {
        "tiene_2fa": tiene_2fa,
        "2fa_deadline": deadline.isoformat() if hasattr(deadline, "isoformat") else deadline,
        "2fa_vencido": vencido,
    }


def _authenticate(usuario, password):
    """Credenciales -> {"usuario","rol","totp_secret"} si usuario+password son
    válidos, si no None. Primero el dueño (env, tiempo constante); si el
    usuario ingresado no es el dueño, se busca un operador ACTIVO en la base
    (Argon2id, con actualización transparente de hashes históricos) — la base
    caída sólo tumba ESTE camino, nunca el del dueño."""
    return _authenticate_ex(usuario, password)[0]


def _authenticate_ex(usuario, password, *, with_stamp=False):
    """`(identity, base_caída)`, la lógica COMPLETA de `_authenticate`.

    `base_caída=True` sólo cuando el camino OPERADOR no pudo leer la base
    (DbUnavailable): distinguirlo de "credenciales inválidas" permite que
    login_result responda 503 `base_datos` en vez del 401 `credenciales` —
    durante un corte, decirle "usuario o contraseña incorrectos" a alguien
    con credenciales perfectas es una mentira activa (responsable del producto, 2026-09-15).
    El dueño (env) no toca la base para autenticar: si sus credenciales
    valen, la identidad vuelve aunque `base_caída` sea True y su login de
    emergencia no se interrumpe."""
    expected_user = _admin_user()
    if hmac.compare_digest(usuario.encode(), expected_user.encode()):
        override = None
        try:
            from app.core import db
            from app.core import db_operators
            from app.core import db_auth
            override = db_auth.admin_security_get(usuario)
        except Exception:  # noqa: BLE001
            override = None
        pass_hash = (override or {}).get("owner_pass_hash")
        if pass_hash:
            try:
                from app.core import db
                from app.core import db_operators
                from app.core import db_auth
                password_ok = db_auth.admin_security_verify_owner_password(
                    usuario, password
                )
            except Exception:  # noqa: BLE001
                password_ok = False
        else:
            expected_pass = _admin_pass()
            password_ok = bool(expected_pass) and hmac.compare_digest(password, expected_pass)
        if password_ok:
            if override and override.get("owner_totp_set"):
                totp = override.get("owner_totp_secret") or None
            else:
                totp = _totp_secret() or None
            return {"usuario": expected_user, "rol": "admin", "totp_secret": totp}, False
        return None, False
    from app.core import db
    from app.core import db_operators
    from app.core import db_auth
    try:
        if "@" in usuario:
            account = db_operators.operator_get_public(usuario) or db_operators.operator_by_email(usuario)
            if not account or not account.get("activo"):
                from app.auth import password_hashing
                password_hashing.dummy_verify(password)
                return None, False
            usuario = account["usuario"]
        row = db_operators.operator_verify_password(usuario, password)
    except db.DbUnavailable:  # base caída: NO son credenciales inválidas
        return None, True
    except Exception:  # noqa: BLE001  (otra falla: nunca romper el login del dueño)
        return None, False
    if not row:
        return None, False
    identity = {"usuario": row["usuario"], "rol": row.get("rol") or "operador",
                "totp_secret": row.get("totp_secret") or None}
    if with_stamp:
        identity["credential_stamp"] = _credential_stamp(row)
    return identity, False


def verify_current_password(identity, password):
    """Verifica la contraseña actual contra la misma fuente que usa login."""
    if not identity:
        return False
    # A copied session cookie must not be able to guess the password here without limit: the guesses
    # count on the account's own login counter (review of the staff-auth fix, 2026-11).
    account_key = _account_attempt_key(identity.get("usuario"))
    if _throttled(account_key):
        return False
    got = _authenticate(identity.get("usuario") or "", str(password or ""))
    ok = bool(got and got.get("usuario") == identity.get("usuario"))
    if ok:
        _clear_attempts(account_key)
    else:
        _record_attempt(account_key)
    return ok


# The staff probe resolves the catalog doctor's DNI to a medico_id, but auth
# may not import patient_web (dependency direction,
# test_domain_dependency_direction) — the composition root hands the catalog's
# doctors() over, the same registration shape notifications.send.register_turnos
# established. None = this boot never wired a catalog; the DNI path degrades
# to "not a staff identifier" while email login keeps working.
_staff_doctors_provider = None


def register_staff_doctors_provider(fn):
    global _staff_doctors_provider
    _staff_doctors_provider = fn


def _staff_operator(identifier):
    """Resolve one operator by email or the linked catalog doctor's DNI."""
    from app.core import db_operators
    identifier = str(identifier or "").strip()
    if "@" in identifier:
        return db_operators.operator_by_email(identifier)
    dni = re.sub(r"[.\s-]", "", identifier)
    if not re.fullmatch(r"[0-9]{7,9}", dni) or _staff_doctors_provider is None:
        return None
    doctors = {str(d.get("id")) for d in (_staff_doctors_provider() or [])
               if not d.get("_borrado") and d.get("activo", True)
               and re.sub(r"[.\s-]", "", str(d.get("dni") or "")) == dni}
    matches = [op for op in db_operators.operators_list() if str(op.get("medico_id")) in doctors]
    return matches[0] if len(matches) == 1 else None


def _pending_serializer():
    return URLSafeTimedSerializer(_signing_key(), salt="staff-login-pending-v1")


def _credential_stamp(row):
    return hashlib.sha256((str(row.get("pass_hash")) + "|" + str(row.get("totp_secret"))).encode()).hexdigest()


def pending_staff(headers):
    """A short-lived password proof, never an admin session; recheck live state."""
    from http.cookies import SimpleCookie
    from app.core import db
    from app.core import db_operators
    try:
        cookies = SimpleCookie()
        cookies.load((headers or {}).get("cookie") or (headers or {}).get("Cookie") or "")
        data = _pending_serializer().loads(cookies["st_staff_pending"].value, max_age=300)
        row = db_operators.operator_get(data["u"])
        if (not row or not row.get("activo") or not row.get("totp_secret")
                or not hmac.compare_digest(data["s"], _credential_stamp(row))):
            return None
        return {"usuario": row["usuario"], "rol": row["rol"],
                "totp_secret": row["totp_secret"], "identifier": data["i"]}
    except (BadData, KeyError, TypeError, ValueError, db.DbUnavailable):
        return None


def _finish_login(headers, ip, identity, codigo=""):
    # Both entry points finish through the same MFA/session/access policy.
    totp = identity.get("totp_secret")
    if totp:
        if not codigo:
            return AuthResult(401, {"need_totp": True})
        if not _totp_valido(codigo, totp):
            _record_attempt(ip)
            return AuthResult(401, {"error": "codigo", "need_totp": True})
    _clear_attempts(ip)
    marcar_acceso(identity["usuario"])
    token = _mint_token(identity["usuario"], identity["rol"])
    return AuthResult(200, {"ok": True, "usuario": identity["usuario"], "rol": identity["rol"]},
                      set_cookie=_set_cookie_from(headers, token))


def _pending_staff_result(headers, identity, identifier):
    """Carry password proof to the existing TOTP screen, never a session."""
    proof = _pending_serializer().dumps({"u": identity["usuario"], "i": identifier,
                                        "s": identity["credential_stamp"],
                                        "nonce": secrets.token_urlsafe(24)})
    secure = "; Secure" if _secure_cookie_from(headers) else ""
    return AuthResult(200, {"ok": True, "redirect": "/admin/login?staff=1"},
                      set_cookie=f"st_staff_pending={proof}; Path=/; HttpOnly; SameSite=Strict; Max-Age=300{secure}")


def password_link_login(headers, client_host, usuario, password):
    """Finish an already-consumed invitation/recovery through normal MFA.

    Both links use the same one-use token type. Recheck the newly saved
    password and active operator, so a later reset cannot transfer its proof.
    No owner fallback or guessed role is allowed. Failure after consumption
    keeps the saved password but leaves authentication to the normal login.
    """
    from app.core import db
    from app.core import db_operators
    fallback = AuthResult(200, {"ok": True, "redirect": "/admin/login"})
    if staff_portal.enabled() or usuario == owner_usuario():
        return fallback
    try:
        row = db_operators.operator_verify_password(usuario, password)
        if (not row or not row.get("activo") or row.get("usuario") != usuario
                or not isinstance(row.get("rol"), str)
                or row["rol"] in perm_module.RETIRED_ROLES
                or not perm_module.SLUG_RE.fullmatch(row["rol"])):
            return fallback
        access = perm_module.access_for(usuario, row["rol"])
        role = access.get("slug")
        if (access.get("state") != "ok" or not isinstance(role, str)
                or role in perm_module.RETIRED_ROLES
                or not perm_module.SLUG_RE.fullmatch(role)):
            return fallback
        identity = {"usuario": usuario, "rol": role,
                    "totp_secret": row.get("totp_secret"),
                    "credential_stamp": _credential_stamp(row)}
        if identity["totp_secret"]:
            return _pending_staff_result(headers, identity, usuario)
        result = _finish_login(headers, turnstile.client_ip_from(headers, client_host), identity)
        result.body["redirect"] = "/admin"
        return result
    except db.DbUnavailable:
        return fallback
    except RuntimeError:
        # A missing signing key cannot undo a committed password change.
        return fallback


def patient_staff_login(headers, client_host, identifier, password):
    """Called only after patient auth fails and its Turnstile gate passes."""
    from app.core import db
    from app.auth import password_hashing
    if staff_portal.enabled():
        # Staff enter through the portal there: a password never opens the panel.
        password_hashing.dummy_verify(password)
        return AuthResult(401, {"error": "credenciales"})
    ip = turnstile.client_ip_from(headers, client_host)
    if _throttled(ip):
        return AuthResult(401, {"error": "credenciales"})
    account = _staff_operator(identifier)
    if not account or not account.get("activo") or account["usuario"] == owner_usuario():
        password_hashing.dummy_verify(password)
        identity = None
    else:
        identity, _ = _authenticate_ex(account["usuario"], password, with_stamp=True)
    if not identity:
        _record_attempt(ip)
        return AuthResult(401, {"error": "credenciales"})
    if identity.get("totp_secret"):
        return _pending_staff_result(headers, identity, identifier)
    result = _finish_login(headers, ip, identity)
    result.body["redirect"] = "/admin"
    return result


def _portal_totp_result(headers, client_host, raw_body, credential):
    """The one thing /api/admin/login still does on a portal installation:
    accept the TOTP code of the portal-identified operator (when our second
    factor is switched on) and remember it in a signed cookie."""
    if not staff_portal.totp_enabled() or not isinstance(credential, staff_portal.Credential):
        return AuthResult(403, {"error": "acceso_por_portal"})
    ip = turnstile.client_ip_from(headers, client_host)
    if _throttled(ip):
        return AuthResult(429, {"error": "demasiados_intentos"})
    try:
        body = parse_json_body(raw_body)
    except Exception as e:  # noqa: BLE001
        return AuthResult(400, {"error": f"JSON inválido: {e}"})
    resolution = credential.resolve(check_totp=False)
    if resolution.reason != "ok":
        return AuthResult(401, {"error": "no_autenticado"})
    operator = resolution.operator
    identity = resolution.identity
    secret = operator.get("totp_secret")
    if not secret:
        return AuthResult(200, {"ok": True, **identity})
    codigo = str(body.get("codigo") or "")
    if not codigo:
        return AuthResult(401, {"need_totp": True})
    if not _totp_valido(codigo, secret):
        _record_attempt(ip)
        return AuthResult(401, {"error": "codigo", "need_totp": True})
    _clear_attempts(ip)
    return AuthResult(200, {"ok": True, **identity},
                      set_cookie=staff_portal.totp_cookie(operator, headers))


def login_result(headers: Mapping[str, str] | None, client_host: str,
                 raw_body: bytes, credential=None) -> AuthResult:
    """Puerto PURO de `login(handler)`: la secuencia ENTERA (throttle antes
    de parsear, parseo JSON,
    Turnstile, autenticar, TOTP, mintear+cookie) a partir de headers + IP peer +
    los bytes crudos del body, nunca un handler. `admin_auth_router.py` (F1)
    llama ACÁ directo con `await request.body()`; Turnstile/TOTP/lockout no
    se duplican. On a portal installation no password is accepted at all."""
    if staff_portal.enabled():
        return _portal_totp_result(headers, client_host, raw_body, credential)
    ip = turnstile.client_ip_from(headers, client_host)
    if _throttled(ip):
        return AuthResult(429, {"error": "demasiados_intentos"})

    try:
        body = parse_json_body(raw_body)
    except Exception as e:  # noqa: BLE001
        return AuthResult(400, {"error": f"JSON inválido: {e}"})

    usuario = str(body.get("usuario") or "").strip()
    password = str(body.get("password") or "")
    codigo = str(body.get("codigo") or "")
    ts_token = body.get("turnstile_token") or ""

    # The account has its own counter beside the address (finding 14): one password guessed from
    # many addresses is still one account. The answer is the same for an account that exists and
    # one that does not, and the key is the typed name, never a lookup.
    account_key = None if body.get("staff_pending") else _account_attempt_key(usuario)
    if account_key and _throttled(account_key):
        return AuthResult(429, {"error": "demasiados_intentos"})

    # 1) Turnstile primero (barato, corta bots antes de tocar credenciales).
    if not _turnstile_ok_from(headers, client_host, ts_token):
        _record_attempt(ip)
        return AuthResult(403, {"error": "turnstile"})

    # 2) Usuario + contraseña — dueño (env) u operador (base).
    if body.get("staff_pending"):
        identity = pending_staff(headers)
        base_caida = False
    else:
        identity, base_caida = _authenticate_ex(usuario, password)
    if identity is None and base_caida:
        # Corte de base: credenciales inválidas sería una mentira; tampoco
        # cuenta para el throttle (no fue un intento del usuario).
        from app.core import deps
        return deps.answer_db_unavailable(lambda status: AuthResult(status, {"error": "base_datos"}))
    if identity is None:
        _record_attempt(ip)
        if account_key:
            _record_attempt(account_key)
        return AuthResult(401, {"error": "credenciales"})

    result = _finish_login(headers, ip, identity, codigo)
    if account_key:
        if result.status == 200:
            _clear_attempts(account_key)
        elif (result.body or {}).get("error") == "codigo":
            _record_attempt(account_key)
    return result


def logout_result(headers: Mapping[str, str] | None) -> AuthResult:
    """Resultado de logout: la cookie siempre se borra, y la sesión de la cookie
    presentada se cierra para TODA copia (`revoke_session`), no sólo en este navegador.
    Sin cookie, o con una que no firmamos, no hay nada que cerrar y responde 200 (idempotente).
    Si la base no deja anotar el cierre responde 503 `base_datos` (con la cookie ya borrada):
    una copia anterior seguiría valiendo, y quien llama —el log, un cliente de API— tiene que saberlo.
    On a portal-login installation the cookie is not a session at all: nothing to close."""
    clear = _clear_cookie_from(headers)
    token = _parse_cookie_token((headers or {}).get("Cookie"))
    if token is None or staff_portal.enabled():
        return AuthResult(200, {"ok": True}, set_cookie=clear)
    from app.core import deps, log_pii
    try:
        revoke_session(token)
    except Exception as exc:  # noqa: BLE001
        _LOG.error("could not record the signed-out session (%s); a copy of the cookie stays valid", log_pii.exc(exc))
        if isinstance(exc, deps.DB_UNAVAILABLE_ERRORS):
            return deps.answer_db_unavailable(lambda status: AuthResult(
                status, {"ok": False, "error": "base_datos", "revoked": False}, set_cookie=clear))
        return AuthResult(500, {"ok": False, "error": "cierre_no_registrado", "revoked": False}, set_cookie=clear)
    return AuthResult(200, {"ok": True}, set_cookie=clear)
