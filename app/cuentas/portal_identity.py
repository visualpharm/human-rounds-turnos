"""Opt-in patient identity from one configured private portal gateway.

Transport trust is the original socket peer, captured by asgi_main with
Uvicorn proxy-header rewriting disabled. HTTP headers cannot supply that peer.
See docs/portal-gateway-identity.md for deployment assumptions and limitations.
"""

import ipaddress
import json
import os
import re
import socket
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from app.core import config, format

_MODE = "private-gateway-v1"
# The test portal (cuentas/test_portal.py) — same names, kept in step by test.
_TEST_MODE = "test-portal-v1"
_TEST_PEER = "fd00::7e57"
_PEER = "humanrounds.portal_peer"
_PRIVATE = tuple(ipaddress.ip_network(value) for value in
                 ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


class ConfigurationError(Exception):
    """Invalid or unavailable trust configuration; contains no identity data."""


class InvalidAssertion(Exception):
    """Untrusted request; contains no header values."""


@dataclass(frozen=True)
class Identity:
    """`dni` is the reviewed binding's when one matches, else the validated
    CUIT's; `email` is the gateway's (may be ''); `nombre`/`apellido` are the
    name the portal asserted, in normal capitalization (both '' when it sent
    none — the account then waits for the person to say it)."""
    subject: str
    dni: str
    email: str = ""
    nombre: str = ""
    apellido: str = ""


def _env(name, default=""):
    return os.environ.get(name, config.dotenv_values().get(name, default))


def enabled():
    # Unknown modes fail closed rather than silently restoring cookie authority.
    return _env("TURNOS_PORTAL_IDENTITY_MODE", "off") != "off"


def municipal_patient_login():
    """Patients enter through the municipal portal itself (the private gateway), not the
    in-process test portal: the gateway there answers 401 on /mcp, so the assistant
    connection cannot work. pinamar.humanrounds.org uses the test portal and keeps it."""
    return patient_login_enabled() and _env("TURNOS_PORTAL_IDENTITY_MODE", "off") != _TEST_MODE


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _address(value):
    if not isinstance(value, str) or "%" in value:
        raise ValueError("invalid peer")
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not any(address.version == network.version and address in network
               for network in _PRIVATE):
        raise ValueError("peer must be private")
    return address


def _staff_login_enabled():
    # Same flag as auth.staff_portal.enabled(); read here so this platform
    # adapter does not import the staff package.
    return str(_env("TURNOS_STAFF_PORTAL_LOGIN", "")).strip() == "1"


def patient_login_enabled():
    """`TURNOS_PATIENT_PORTAL_LOGIN=1`: the portal is the patients' only way
    in and their account comes from it — found or created by the DNI of the
    validated CUIT, then recognized by the portal account id. Installation
    environment only (process env, then the env file), never a DB setting.
    The process refuses to start with the flag and the portal identity mode
    off (`validate_patient_login`)."""
    return enabled() and _patient_flag()


def _patient_flag():
    return str(_env("TURNOS_PATIENT_PORTAL_LOGIN", "")).strip() == "1"


def validate_patient_login():
    """Boot check: the patient portal login without the gateway boundary would
    leave the native password login open on an installation meant to have
    none."""
    if _patient_flag() and not enabled():
        raise ConfigurationError()


def login_url():
    """Where a person without a portal session goes to sign in (optional)."""
    url = str(_env("TURNOS_PORTAL_LOGIN_URL", "") or "").strip()
    return url if url.startswith("https://") else ""


def logout_url():
    """The portal's own sign-out address (https only; '' = none known). Our
    logout cannot end a portal session: the gateway re-identifies the next
    request, so «Cerrar sesión» is offered only where this address exists."""
    url = str(_env("TURNOS_PORTAL_LOGOUT_URL", "") or "").strip()
    return url if url.startswith("https://") else ""


def login_url_back_to(path):
    """The portal sign-in that comes back to `path` (a same-site path) on this
    installation: `return=<TURNOS_PUBLIC_ORIGIN><path>`, the parameter the
    municipal portal reads on /sso/login (and the gateway itself sends) — an
    absolute https URL under .pinamar.gob.ar. Without it the portal ends on
    its own home page (the product owner, 26.10). '' when there is no sign-in URL or no
    valid public origin."""
    url = login_url()
    if not url:
        return ""
    try:
        back = _public_origin() + path
    except ValueError:
        return ""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "return"]
    query.append(("return", back))
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote, safe="")))


def _transport():
    """Gateway source and public origin — the trust boundary shared by the
    patient adapter and the staff portal login."""
    # Pin enabled mode in the process so loss of an env file cannot re-enable cookies.
    mode = os.environ.get("TURNOS_PORTAL_IDENTITY_MODE")
    if mode not in (_MODE, _TEST_MODE):
        raise ConfigurationError()
    try:
        if mode == _TEST_MODE:
            # cuentas.test_portal is the gateway, in this process: it alone
            # writes its fixed peer into the scope, whatever the socket was.
            peers, host = frozenset({_address(_TEST_PEER)}), ""
            origin = _public_origin()
            return peers, host, origin
        peers_raw = _env("TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS", "[]")
        peers = json.loads(peers_raw)
        host = _env("TURNOS_PORTAL_IDENTITY_GATEWAY_HOST")
        if not isinstance(peers, list) or bool(peers) == bool(host):
            raise ValueError("select one gateway source")
        peers = frozenset(_address(peer) for peer in peers)
        if host and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.railway\.internal", host):
            raise ValueError("invalid gateway service name")
        origin = _public_origin()
    except (ValueError, TypeError, RecursionError) as exc:
        raise ConfigurationError() from exc
    return peers, host, origin


def _public_origin():
    origin = _env("TURNOS_PUBLIC_ORIGIN")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or parsed.netloc != parsed.netloc.lower()):
        raise ValueError("invalid public origin")
    # Access port to reject malformed authority syntax.
    parsed.port
    return origin


def _settings():
    peers, host, origin = _transport()
    try:
        raw = _env("TURNOS_PORTAL_IDENTITY_BINDINGS")
        # An installation that turns the portal on only for staff may have no
        # approved patient yet: no binding then simply admits no patient. With
        # the patient portal login, no binding admits every validated person
        # and a list keeps a pilot to the people on it.
        open_bindings = _staff_login_enabled() or patient_login_enabled()
        if open_bindings and not str(raw or "").strip():
            raw = "[]"
        rows = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(rows, list) or (not rows and not open_bindings):
            raise ValueError("empty bindings")
        mapping = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"subject", "cuit", "dni"}:
                raise ValueError("invalid binding")
            subject, cuit, dni = row["subject"], row["cuit"], row["dni"]
            if (not isinstance(subject, str) or not 0 < len(subject) <= 256
                    or any(ord(c) < 32 or ord(c) > 126 for c in subject)
                    or not isinstance(cuit, str) or not re.fullmatch(r"[0-9]{11}", cuit)
                    or not isinstance(dni, str) or not re.fullmatch(r"[0-9]+", dni)
                    or not format.dni_valid_length(dni) or subject in mapping):
                raise ValueError("invalid binding")
            mapping[subject] = (cuit, dni)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ConfigurationError() from exc
    return peers, host, origin, mapping


def validate_configuration():
    """Fail before listening if enabled configuration is incomplete."""
    _settings()


class SocketPeerApp:
    """Outermost application wrapper; requires server proxy_headers=False."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            scope = dict(scope)
            scope[_PEER] = scope.get("client")
        await self.app(scope, receive, send)


def _trusted_peers(peers, host):
    if not host:
        return peers
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        resolved = frozenset(_address(answer[4][0]) for answer in answers)
        if not resolved:
            raise ValueError("no gateway addresses")
        return resolved
    except (OSError, ValueError, TypeError) as exc:
        # No stale address cache/fallback when gateway DNS cannot be validated.
        raise ConfigurationError() from exc


def _one_header(request, name, maximum, *, optional=False):
    values = request.headers.getlist(name)
    if optional and not values:
        return ""
    if len(values) != 1:
        raise InvalidAssertion()
    value = values[0]
    if len(value) > maximum or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise InvalidAssertion()
    return value


def normalize_cuit(value):
    """Eleven digits from `20123456788` or `20-12345678-8`, else ''."""
    value = str(value or "").strip()
    if re.fullmatch(r"[0-9]{2}-[0-9]{8}-[0-9]", value):
        value = value.replace("-", "")
    return value if re.fullmatch(r"[0-9]{11}", value) else ""


# ARCA prefixes of a natural person's CUIT/CUIL. 30, 33 and 34 are companies
# and organizations — never a patient — and any other prefix is refused too.
_PERSON_PREFIXES = frozenset({"20", "23", "24", "25", "26", "27"})


def person_dni(cuit):
    """The DNI inside a natural person's CUIT: the eight middle digits without
    leading zeros (20-12345678-9 → 12345678). '' for an organization's CUIT
    or anything that does not hold a valid DNI."""
    cuit = normalize_cuit(cuit)
    if not cuit or cuit[:2] not in _PERSON_PREFIXES:
        return ""
    dni = cuit[2:10].lstrip("0")
    return dni if dni and format.dni_valid_length(dni) else ""


def _gateway_headers(request, peers, host, origin):
    """(subject, raw cuit, email) once the peer, headers and Origin hold."""
    raw_peer = request.scope.get(_PEER)
    if not isinstance(raw_peer, (tuple, list)) or len(raw_peer) != 2:
        raise InvalidAssertion()
    peer = _address(raw_peer[0])
    if peer not in _trusted_peers(peers, host):
        raise InvalidAssertion()
    subject = _one_header(request, "x-user-id", 256)
    cuit = _one_header(request, "x-user-cuit", 13, optional=True)
    email = _one_header(request, "x-user-email", 512, optional=True)
    # Roles are read only to reject malformed duplicates; they never grant anything.
    _one_header(request, "x-user-roles", 512, optional=True)
    if not subject:
        raise InvalidAssertion()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if _one_header(request, "origin", 512) != origin:
            raise InvalidAssertion()
    return subject, cuit, email


_NAME_MAX = 128


def _name_header(request, header):
    """One optional name header, decoded; '' when absent, repeated, oversized
    or unreadable. A name only fills a profile: it never decides who gets in.

    The wire is ASCII, so the gateway sends percent-encoded UTF-8
    (`P%C3%A9rez`); plain ASCII passes unchanged, and raw UTF-8 bytes (which
    Starlette hands over as Latin-1) are read back as UTF-8."""
    values = request.headers.getlist(header)
    if len(values) != 1 or len(values[0]) > 3 * _NAME_MAX:
        return ""
    raw = values[0]
    try:
        if any(ord(c) > 126 for c in raw):
            raw = raw.encode("latin-1").decode("utf-8")
        text = unquote(raw, errors="strict") if "%" in raw else raw
    except (UnicodeError, ValueError):
        return ""
    text = " ".join(text.split())
    if len(text) > _NAME_MAX or any(unicodedata.category(c).startswith("C") for c in text):
        return ""
    return text


def assertion_name(request):
    """(nombre, apellido) the gateway asserts for this person, '' for what it
    does not say. `X-User-Given-Name` / `X-User-Family-Name` say where the
    surname is; the single `X-User-Name` does only as «APELLIDO, Nombre» —
    without the comma nothing says where the surname starts, so the whole
    name goes in `nombre` rather than a guess (the booking step asks for the
    rest). Shouted registry names come out in normal capitalization."""
    given = _name_header(request, "x-user-given-name")
    family = _name_header(request, "x-user-family-name")
    if not (given or family):
        full = _name_header(request, "x-user-name")
        if "," in full:
            family, _, given = (part.strip() for part in full.partition(","))
        else:
            given = full
    return format.person_name(given), format.person_name(family)


def authenticate(request):
    """The patient identity of this request, or None; never grants staff roles.

    Without the patient portal login: a reviewed binding only, and the caller
    looks up an existing account (never provisions). With it: any natural
    person with a validated CUIT — only the bound subjects when a list is
    configured — and the caller finds or creates the account."""
    peers, host, origin, bindings = _settings()
    try:
        subject, cuit, email = _gateway_headers(request, peers, host, origin)
        cuit = normalize_cuit(cuit)
        if not cuit:
            raise InvalidAssertion()
        # Only the portal login provisions, so only it needs a person's CUIT;
        # the reviewed-binding pilot keeps exactly its old rule.
        if patient_login_enabled() and not person_dni(cuit):
            raise InvalidAssertion()
        binding = bindings.get(subject)
        if binding:
            if binding[0] != cuit:
                raise InvalidAssertion()
            dni = binding[1]
        elif patient_login_enabled() and not bindings:
            dni = person_dni(cuit)
        else:
            raise InvalidAssertion()
        nombre, apellido = assertion_name(request)
        return Identity(subject=subject, dni=dni, email=email.strip(),
                        nombre=nombre, apellido=apellido)
    except (InvalidAssertion, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class GatewayAssertion:
    """What the trusted gateway says about the person behind this request.

    `subject` is `X-User-Id`, the portal account's immutable id. `cuit` is ''
    when the header is missing (the gateway sends it only once ARCA/Mi
    Argentina validated the person) or malformed; `email` may be ''. Nothing
    here grants a permission by itself: auth.staff_portal maps it to an
    operator."""
    subject: str
    cuit: str
    email: str


def gateway_assertion(request):
    """The gateway's assertion for the staff login, or None when the request
    did not come from the configured gateway (or its headers are malformed).
    Raises ConfigurationError when the trust configuration is unusable."""
    peers, host, origin = _transport()
    try:
        subject, cuit, email = _gateway_headers(request, peers, host, origin)
    except (InvalidAssertion, ValueError, TypeError):
        return None
    return GatewayAssertion(subject=subject, cuit=normalize_cuit(cuit),
                            email=email.strip())
