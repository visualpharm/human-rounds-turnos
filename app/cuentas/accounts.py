"""accounts.py — patient registration, login and account.

A patient can see all the information without registering; to BOOK an
appointment they need an account. The account is keyed by DNI (same key HSI
uses).

Native router surface owned by this module:
  GET  /api/account/config     Turnstile sitekey for the login (or null)
  POST /api/account/register   creates the account + opens a session (Set-Cookie)
  POST /api/account/login      validates credentials + opens a session (Turnstile
                               if TURNOS_TURNSTILE_SECRET is configured — same
                               pair of envs as admin_auth.py)
  GET  /auth/test              local selector of test identities
  GET  /api/account/test-identities  lists synthetic identities (stub only)
  POST /api/account/test-identities  creates/edits a synthetic identity
  POST /api/account/test-identities/random generates a synthetic identity
  POST /api/account/test-login opens a session with a chosen identity
  POST /api/account/logout     closes the session + expires the cookie
  GET  /api/account/me         the logged-in user (or 401)
  GET  /api/account/dependents the user's dependents
  POST /api/account/dependents adds/updates a person by DNI + affidavit
  POST /api/account/dependents/delete removes the link from the account
  POST /api/account/update    edits the logged-in account's data (requires
                               leaving contact or email, never both empty)
  GET  /account                the account's HTML page (auth or dashboard)
  POST /api/account/recover   requests the recovery link by email (always
                               responds the same, match or not, so it never
                               leaks which DNI exists) + if the account has a
                               contact, also requests an OTP send over
                               WhatsApp (best-effort; a logged no-op in
                               production today — no OTP transport exists,
                               see otp_send.py)
  POST /api/account/reset      consumes the link's token and sets the new password
  POST /api/account/reset_otp  sets the new password with the OTP code (instead
                               of the email link) — {dni, code, password}
  GET  /account/reset          the recovery link's page (?token=...)
  POST /api/account/otp/send      {contacto, proposito} requests an OTP send
                               over WhatsApp (register or recover;
                               rate-limited; the send itself is a logged
                               no-op in production today — see otp_send.py)
  POST /api/account/otp/verify    {contacto, proposito, code} validates the code
                               and issues a 15-minute "verified" ticket that the
                               next POST (register/reset_otp) consumes

OTP (see otp_send.py + tracking card sms-provider): the endpoints above are
ALWAYS LIVE — only registration ENFORCEMENT is feature-gated, by
OTP_GATING_ENABLED=1 (off by default). With no production OTP transport
(the WAHA sender reaches loopback dev only; Meta is refused without an
approved Authentication template), requiring a verified phone would break
real registration, so the flag stays 0 until a transport and the patient
code-entry UI both ship. GET /api/account/config exposes
`otp_registro_requerido` so the front end can adapt the flow without
needing a new deploy when it gets turned on.

Cookie contract (FIXED — other modules code against this):
  name `st_ses`, HttpOnly, SameSite=Lax, Path=/, Max-Age 10 years (the product owner
  2026-07-11: "keep people logged in at all times" — the session in the
  database no longer expires server-side, so stretching the cookie is the
  only thing left to do).
Exposes `current_user(request) -> dict | None` for authenticated callers.

Import-safe: storage access is routed through accounts_store; if the database
isn't there, DbUnavailable -> 503 JSON (same as booking_requests.py). Importing
this module at server startup must NEVER fail.
"""

import ipaddress
import threading
import re
import json
import secrets
import urllib.parse
from datetime import date, datetime

import logging

from fastapi import Request

from app.cuentas import accounts_store as store
from app.cuentas import claim_gate
from app.cuentas import portal_identity
from app.cuentas.native_result import Result
from app.core import format
from app.core import proxy_trust
from app.core import rate_window
from app.auth import turnstile
from app.core import site_identity
from app.core import theme
from app.core import config
from app.core import log_pii
from app.ops import demo_installation

from app.core import i18n
from app.core.ids import dni as _norm_dni
from app.core import ids as _ids

# ---- cookie contract --------------------------------------------------------
COOKIE_NAME = "st_ses"
COOKIE_MAX_AGE = 10 * 365 * 24 * 3600  # 10 years — "never log them out"

def _env(key, default=""):
    return config.get(key, default)


# ---- Cloudflare Turnstile (patient login) -----------------------------------
# turnstile.py decides the gate — same pair of envs for the whole site, and
# off on dev hosts (Cloudflare's sitekey isn't valid on `devbox:7700`).
# GET /api/account/config serves the sitekey (or null) to the widget;
# upload-order.js reuses that same endpoint ("no new config endpoint per
# feature").

def _secure_flag():
    """`; Secure` when the installation's public origin is https (never from a request header:
    a plain-http dev checkout would drop the cookie). Security review 2026-11, M-5."""
    return "; Secure" if site_identity.base_url().lower().startswith("https://") else ""


def _set_cookie(token):
    return ("%s=%s; Max-Age=%d; Path=/; HttpOnly; SameSite=Lax%s"
            % (COOKIE_NAME, token, COOKIE_MAX_AGE, _secure_flag()))


def _clear_cookie():
    return "%s=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax%s" % (COOKIE_NAME, _secure_flag())


def _cookie_token(request):
    raw = request.headers.get("Cookie") or ""
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == COOKIE_NAME:
                return v.strip()
    return None


def _user_json(u):
    """Serializes the user dict (dates -> ISO) and strips any pass_hash."""
    if not u:
        return None
    out = {}
    for k, v in u.items():
        if k == "pass_hash":
            continue
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    # A synthetic key must never reach a screen that paints it back as if it
    # were the person's document. Users carry the authoritative stored flag
    # (users.identity_kind); dependent rows have no such column, but a key
    # minted by _mint_synthetic_key is recognizable by shape ("9" + 8 digits)
    # — stamp it here so EVERY projection (me, dependents, reset, register)
    # exposes identity_kind exactly once.
    if not out.get("identity_kind") and _is_synthetic_key(out.get("dni")):
        out["identity_kind"] = "synthetic"
    if "cobertura" in u:
        from app.cuentas.health_coverage import revision
        out["coverage_revision"] = revision(u.get("cobertura"))
    return out


def _guarded(fn, *args, **kwargs):
    """Run a domain operation and normalize database outages to a result."""
    try:
        return fn(*args, **kwargs)
    except store.DbUnavailable:
        from app.core import deps
        return deps.answer_db_unavailable(
            lambda status: Result.json({"error": "base de datos no disponible"}, status=status))
    except portal_identity.ConfigurationError:
        return Result.json({"error": "autenticacion_no_disponible"}, status=503)


# ---- input normalization -----------------------------------------------------

def _es_lang_active():
    """True when the request's resolved language is the canonical one (es).
    The visitor's language — never the installation country — decides whether
    a DNI is asked for (the product owner, 2026-09-11): the live demo is an AR install
    shown to international visitors, and a non-Spanish visitor is never asked
    for a DNI / DNI photo / obra social / PUCO lookup."""
    return str(i18n.active() or "es").strip().lower().split("-")[0] == "es"


# The keys _mint_synthetic_key mints: exactly "9" + 8 digits. Real Argentinian
# DNIs today are 7-8 digits, so the shape test below can only hit what this
# module minted (and erring the other way — hiding a key from a display — is
# the safe direction).
_SYNTHETIC_KEY_RE = re.compile(r"9\d{8}\Z")


def _is_synthetic_key(v):
    return bool(_SYNTHETIC_KEY_RE.match(str(v or "")))


def _mint_synthetic_key(taken):
    """A plausible identity key for an account that has no real document
    behind it: "9" + 8 random digits, redrawn on collision. It threads the
    SAME key space every DNI-keyed table already uses (login, sessions,
    booking_requests, dependents), so nothing downstream changes shape."""
    while True:
        cand = "9" + "".join(str(secrets.randbelow(10)) for _ in range(8))
        if not taken(cand):
            return cand


def _valid_email(v):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(v or "").strip()))


def _phone_error(raw, lang):
    """None when the phone is empty or can be called; otherwise the sentence
    that says what to write (TASK-51, 26.10: the sign-up stored
    22542254000000 — the «2254» the form carried as a value, plus the full
    number typed after it). format.tel_valido is the one rule, mirrored by
    format.mjs telValido."""
    s = str(raw or "").strip()
    if not s or format.tel_valido(s):
        return None
    return i18n.t_or(
        "account.form.error.phone_invalid",
        "Revisá el teléfono: escribí el código de área y el número una sola vez, "
        "por ejemplo 2254 550000.", lang)


def _norm_phone(v):
    """Phone number, stored in the site's CANONICAL form (the product owner, 2026-07-28:
    "canonical display/storage format") — `+5492254123456` / `+542254401234`,
    the same one format.mjs shows with separators.

    Storing it canonically is what makes two people who type the same number
    in different ways (with 15, with 0, with +54) end up with the SAME value
    in the database. Anything that isn't a domestic number is stored as
    before (digits, with a leading + if it had one): inventing a format for
    a foreign number would leave it unable to receive anything."""
    s = str(v or "").strip()
    if not s:
        return None
    canonico = format.tel_e164(s)
    if canonico:
        return canonico
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    return ("+" + digits) if plus else digits


def _valid_date(y, m, d):
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    today = date.today()
    if not (1900 <= y <= today.year):
        return None
    if dt > today:
        return None
    return dt.isoformat()


def _expand_year(yy, length, m, d):
    """2 digits -> century that does NOT leave the date in the future; 4 digits as-is."""
    if length >= 4 or yy > 99:
        return yy
    c2000 = 2000 + yy
    try:
        if date(c2000, m, d) <= date.today():
            return c2000
    except ValueError:
        pass
    return 1900 + yy


def _parse_date(v):
    """dd/mm/yyyy, yyyy-mm-dd, ./- separators, ddmmyyyy, and 2-digit years.
    Day-first (AR). Returns ISO yyyy-mm-dd or None. (Server-side version, lean;
    the rich parser lives in account-form.js — we normalize here too just in case.)"""
    s = str(v or "").strip()
    if not s:
        return None
    parts = [p for p in re.split(r"[^\d]+", s) if p]
    if len(parts) == 3:
        a, b, c = parts
        if len(a) == 4:                      # yyyy-mm-dd
            return _valid_date(int(a), int(b), int(c))
        y = _expand_year(int(c), len(c), int(b), int(a))
        return _valid_date(y, int(b), int(a))  # dd/mm/yyyy (day-first)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:                      # ddmmyyyy
        return _valid_date(int(digits[4:]), int(digits[2:4]), int(digits[:2]))
    if len(digits) == 6:                      # ddmmyy
        y = _expand_year(int(digits[4:]), 2, int(digits[2:4]), int(digits[:2]))
        return _valid_date(y, int(digits[2:4]), int(digits[:2]))
    return None


def _clean(v, n=120):
    return str(v or "").strip()[:n]


def _insurers_list(cob):
    """The union/labor insurers (`obras_sociales`) of a stored coverage, as a
    list (tolerates the old single-insurer shape: {obra_social: {...}})."""
    cob = cob if isinstance(cob, dict) else {}
    xs = cob.get("obras_sociales")
    if isinstance(xs, list):
        return [x for x in xs if isinstance(x, dict) and x.get("nombre")]
    x = cob.get("obra_social")
    if isinstance(x, dict) and x.get("nombre"):
        return [x]
    if x:
        return [{"nombre": str(x)}]
    return []


def _norm_coverage(v, dni=None, existente=None):
    """Sanitizes the coverage {obras_sociales, prepaga} the client sends.

    obras_sociales = [{nombre, rnos?, origen, afiliado?}, ...] — a person can
    show up in more than one registry (IOMA + a deregulated prepaid plan) and
    can also declare others by hand. prepaga = {nombre, afiliado?, plan?}
    (tolerates the old JS's bare string; also tolerates the old body with a
    singular obra_social). Insurers "per the registry" NEVER come from the
    body: if the active insurance_lookup connector (`connectors.adapter_for`,
    puco by default) has a fresh lookup cached for that DNI (the form
    triggered it while typing), ALL of those get attached with origen
    'puco', first.
    Hand-typed entries come in as 'declarada' — unless they match by name
    something already saved (`existente`), in which case the original record
    is kept (never downgrade a saved 'puco' to 'declarada' because of an
    autosave that changed nothing). The member number always comes from the
    body though: the registry doesn't carry it, only the patient knows it.
    Returns a dict or None."""
    if not isinstance(v, dict):
        v = {}
    out = {}

    body_os = v.get("obras_sociales")
    if not isinstance(body_os, list):
        legacy = v.get("obra_social")
        body_os = [legacy] if legacy else []
    tipeadas = []
    for o in body_os[:6]:
        if not isinstance(o, dict):
            o = {"nombre": o} if o else {}
        nombre = _clean(o.get("nombre"))
        if nombre:
            tipeadas.append({"nombre": nombre, "afiliado": _clean(o.get("afiliado"), 40)})

    padron = []
    try:
        from app import connectors
        adapter = connectors.adapter_for(connectors.INSURANCE_LOOKUP) if dni else None
        # `cached()` never calls out: it is what the connector already learned
        # while the form was typing. No connector routed (off / none) → [] →
        # every insurer the body sends is 'declarada' — the documented degrade.
        padron = (adapter.cached(dni) if adapter else None) or []
    except Exception:  # noqa: BLE001
        padron = []
    padron_map = {}
    for obra in padron:
        nm = _clean(obra.get("nombre"))
        if nm and nm not in padron_map:
            padron_map[nm] = obra

    previas = {p.get("nombre"): p for p in _insurers_list(existente)}
    menciona = "obras_sociales" in v or "obra_social" in v

    # The CLIENT is the source of truth for WHICH insurers exist: removing one
    # = not sending it (2026-07-16, product owner: they can edit and remove them, even
    # the registry ones). The origen 'puco' NEVER comes from the body: it's
    # only set if the name matches puco.py's cache for THAT dni (fresh
    # registry) or an insurer already saved as 'puco' (expired cache) — so the
    # client can't invent "per the registry", but editing a name downgrades it
    # to 'declarada' and removing it really does delete it.
    lista, vistos = [], set()
    for t in tipeadas:
        nombre = t["nombre"]
        if nombre in vistos:
            continue
        vistos.add(nombre)
        previa = previas.get(nombre)
        if nombre in padron_map:
            os_out = {"nombre": nombre, "origen": "puco"}
            if padron_map[nombre].get("rnos"):
                os_out["rnos"] = padron_map[nombre]["rnos"]
        elif previa and previa.get("origen") == "puco":
            os_out = {"nombre": nombre, "origen": "puco"}
            if previa.get("rnos"):
                os_out["rnos"] = previa["rnos"]
        else:
            os_out = {"nombre": nombre, "origen": "declarada"}
        if t["afiliado"]:
            os_out["afiliado"] = t["afiliado"]
        lista.append(os_out)
    # body with insurers → that list wins (empty and mentioned = deliberate
    # delete); body that doesn't even mention the key (autosave of another
    # field with old JS) → keeps what's saved.
    if lista:
        out["obras_sociales"] = lista
    elif not menciona:
        guardadas = _insurers_list(existente)
        if guardadas:
            out["obras_sociales"] = guardadas

    pre = v.get("prepaga")
    if not isinstance(pre, dict):
        pre = {"nombre": pre} if pre else {}
    pnombre = _clean(pre.get("nombre"))
    if pnombre:
        p_out = {"nombre": pnombre}
        paf = _clean(pre.get("afiliado"), 40)
        pplan = _clean(pre.get("plan"), 40)
        if paf:
            p_out["afiliado"] = paf
        if pplan:
            p_out["plan"] = pplan
        out["prepaga"] = p_out
    if not out:
        if (v.get("declarada_sin_cobertura") is True
                or isinstance(existente, dict) and existente.get("declarada_sin_cobertura") is True):
            return {"declarada_sin_cobertura": True}
        return None
    # Which one wins. The marked one wins; if nothing is marked (or it points
    # to a coverage that was just removed) the best one gets picked — see
    # health_coverage.py.
    marcada = _clean(v.get("principal"), 80)
    if marcada:
        out["principal"] = marcada
    elif isinstance(existente, dict) and existente.get("principal"):
        out["principal"] = existente["principal"]
    from app.cuentas import health_coverage
    return health_coverage.normalize(out)


_DOM_CAMPOS = (("calle", 120), ("numero", 12), ("piso", 20),
               ("localidad", 80), ("provincia", 80), ("cp", 12), ("texto", 200))


def _norm_address(v):
    """Sanitizes the address {calle, numero, piso, localidad, provincia, cp,
    texto} the client sends (it can come pre-filled from OCR on the back of
    the DNI, or typed by hand). Returns the dict with whatever it has, or
    None if it's all empty. Informational only — never validated against a
    registry."""
    v = v if isinstance(v, dict) else {}
    out = {}
    for k, n in _DOM_CAMPOS:
        val = _clean(v.get(k), n)
        if val:
            out[k] = val
    return out or None


def _norm_notifications(v, existente=None):
    """Notification-channel preference {whatsapp, email} (bools). Without the
    key in the body, keeps what's saved. null → defaults on the client."""
    if not isinstance(v, dict):
        return existente
    out = {}
    for k in ("whatsapp", "email"):
        if k in v:
            out[k] = bool(v[k])
        elif isinstance(existente, dict) and k in existente:
            out[k] = bool(existente[k])
    return out or None


def _norm_lang(v, existente=None):
    """The language notifications.py sends this account's emails/WhatsApp in
    (`users.lang`, db.py's _SCHEMA_SQL). Only a currently ENABLED language is
    accepted — same filter `i18n.resolve_lang()` applies to a cookie, so a
    stale value (a language an admin later turned back off) can't get
    written back. Anything else keeps what's already saved."""
    code = str(v or "").strip().lower()
    if code and code in i18n.enabled_languages():
        return code
    return existente


# ---- public API for other modules -------------------------------------------

def current_user(request: Request, *, raise_db_unavailable=False):
    """The logged-in user (dict without pass_hash) or None. booking_requests.py
    imports it. By default DB down / invalid token -> None. Account bootstrap
    opts into propagating a storage/configuration outage so _guarded can
    return 503. Opt-in portal mode resolves only a verified, bound identity;
    native cookies do not override or bypass the gateway. With the patient
    portal login the account comes from the portal: found by the portal
    account id, else the account with the CUIT's DNI is bound to it or
    created (store.user_claim_portal refuses a disabled account or one bound
    to another portal account)."""
    try:
        if portal_identity.enabled():
            identity = portal_identity.authenticate(request)
            if identity is None:
                return None
            if portal_identity.patient_login_enabled():
                usuario = (store.user_by_portal_id(identity.subject)
                           or store.user_claim_portal(identity.subject, identity.dni,
                                                      identity.email, identity.nombre,
                                                      identity.apellido)
                           # A concurrent first request may have won the claim.
                           or store.user_by_portal_id(identity.subject))
                # The account a portal account opens is the one of ITS CUIT:
                # a mismatch (a DNI corrected by hand, a changed binding) is
                # refused rather than guessed.
                if usuario and usuario.get("dni") != identity.dni:
                    # ...and the assistant connected under the old binding is
                    # cut off (2026-11-13): the portal never tells us about an
                    # unbinding, this is the moment we see one.
                    try:
                        # only_if_set: every later request of the mismatched
                        # binding finds nothing to revoke and writes nothing.
                        store.mcp_token_revoke(usuario["dni"], only_if_set=True)
                    except Exception as exc:  # noqa: BLE001 — the refusal stands either way
                        logging.getLogger(__name__).error("MCP token not revoked after a portal rebinding: %s", log_pii.exc(exc))
                    return None
            else:
                usuario = store.get_user(identity.dni)
            if not usuario or usuario.get("activo") is False:
                return None
            # The portal asserted a name for an account that lacks one (it
            # was created before the portal sent names): fill the empty
            # field only, never overwrite what the person saved.
            if ((identity.nombre and not usuario.get("nombre"))
                    or (identity.apellido and not usuario.get("apellido"))):
                usuario = store.user_fill_name(
                    usuario["dni"], identity.nombre, identity.apellido) or usuario
            return usuario
        token = _cookie_token(request)
        if not token:
            return None
        sesion = store.get_session(token)
    except (store.DbUnavailable, portal_identity.ConfigurationError):
        if raise_db_unavailable:
            raise
        return None
    except Exception:  # noqa: BLE001
        return None
    if not sesion:
        return None
    return sesion.get("usuario") or None


def _otp_registration_required():
    """OTP_GATING_ENABLED=1 only once OTP has a production transport (an
    approved Meta Authentication template — see otp_send.py; the WAHA sender
    is loopback dev only) AND the patient code-entry UI ships. Off by
    default: with no transport, requiring a verified phone would break real
    registration (tracking card sms-provider, 2026-07-11)."""
    return _env("OTP_GATING_ENABLED", "0") == "1"


def _dni_claims_enabled():
    """The desk checks the document behind a number (security review 2026-11, TASK-68).

    On by default: a sign-up by password and DNI photo proves nothing about the number, so the first
    account on it sees no clinical data until the desk confirms, and a second sign-up on it is not
    refused — it becomes a claim the desk decides. `TURNOS_DNI_CLAIMS=0` turns it off (the demo, where
    anyone may try the flow); salud and pinamar log in through a portal that vouches for the number."""
    return claim_gate.enabled()


MAX_PENDING_CLAIMS = 5  # open claims one number takes; login tries them all, so the bound is also its cost


def _claim_key(dni):
    """The key a second sign-up on a taken number lives under (minted, never the number itself)."""
    return _mint_synthetic_key(lambda cand: store.get_user(cand))


def _with_verification(out):
    """`dni_verificacion` on the user JSON of an account the desk has not confirmed yet:
    'unverified' (it holds the number, nobody checked) or 'pending' (it asks for a number that is
    taken). Absent for every confirmed account, so nothing changes for them."""
    if out and out.get("dni") and _dni_claims_enabled():
        claim = store.open_claim_of(out["dni"])
        if claim:
            out["dni_verificacion"] = claim["status"]
    return out


def _auth_mode():
    """Patient authentication mode. Password remains the safe default.

    Since 2026-07-19 the value lives in the admin config (auth_mode.py,
    DB-first with fallback to the env TURNOS_AUTH_MODE) — Settings > Patient
    login."""
    from app.auth import mode as auth_mode
    return auth_mode.current()


def _renaper_provider():
    """Provider choice is independent from the authentication mode.

    ``real`` deliberately has no guessed HTTP integration; use ``stub`` only
    for local/CI scenarios until RENAPER authorises a concrete contract.
    """
    value = _env("TURNOS_RENAPER_PROVIDER", "real").strip().lower()
    return value if value in ("real", "stub") else "real"


def _stub_desplegado():
    """Opt-in to serve the identity simulator on a DEPLOYED installation.

    Pinamar's own installation shows the RENAPER login as its sign-in screen
    while the municipality's real credentials arrive (the product owner, 2026-08-02): the
    hospital has to see the flow it actually asked for, not the DNI-photo
    sign-up. The simulator hands a session to ANY DNI, so this is never a
    default and never inferred — it is a third env that an installation sets
    on purpose, alongside the two below. (In 2026-08 pinamar.humanrounds.org
    sat behind HTTP basic auth; by 2026-10-23 it no longer did.)

    Since 2026-10-23 it also needs a DEMO installation (`TURNOS_DEMO_MODE=1`,
    The product owner on MR !46): an installation with real patients never serves the
    simulator, whatever its env says — its test accounts would land in the
    reports as real ones (problems.md №5).
    """
    if _env("TURNOS_RENAPER_STUB_DEPLOYED", "0").strip() != "1":
        return False
    return demo_installation.enabled()


def _test_auth_enabled(headers, client_host):
    """Allow synthetic identities only from the local network, on a dev host.

    The socket peer is authoritative here. A public client must not be able to
    enable arbitrary-DNI sessions by forging Host or forwarded-IP headers after
    a stub setting is copied into a deployed environment. Loopback always
    qualifies; a LAN/Tailscale peer (`devbox:7700` resolves to a non-loopback IP)
    qualifies only when the Host is also a dev host (turnstile.host_de_dev) —
    in Coolify the peer is the docker network but the Host is the public
    domain, so the stub stays off there even if the envs get copied. The one
    way past the peer check is `_stub_desplegado()`, which an installation has
    to declare for itself.
    """
    if _auth_mode() != "renaper" or _renaper_provider() != "stub":
        return False
    if _stub_desplegado():
        return True
    try:
        # Tier 4 proxy-trust (proxy_trust.py): el peer REAL, no el peer crudo
        # del socket -- detrás del proxy interno de asgi_main.py ese sería
        # siempre el loopback del proxy, nunca el del cliente de verdad.
        peer = ipaddress.ip_address(proxy_trust.real_peer_from(headers, client_host))
    except (AttributeError, IndexError, TypeError, ValueError):
        return False
    if peer.is_loopback:
        return True
    return (not peer.is_global) and turnstile.host_de_dev_from(headers)


def _effective_auth_mode(headers, client_host):
    """Advertise federated login only when its provider can complete it.
    'portal' = TURNOS_PATIENT_PORTAL_LOGIN: the municipal portal is the only
    way in, so no screen offers a password, a sign-up or a recovery."""
    if portal_identity.patient_login_enabled():
        return "portal"
    if _auth_mode() == "renaper" and _test_auth_enabled(headers, client_host):
        return "renaper"
    return "password"


def auth_mode(headers, client_host):
    """Public read of the effective patient authentication mode.

    patient_web/site_*.py inlines it in the wizard's data island so Confirmá can say
    "la cuenta que verificaste con RENAPER" only when that is TRUE — reading
    it from /api/account/config would repaint the screen after first paint.
    """
    return _effective_auth_mode(headers, client_host)


def _config_core(headers, client_host, request=None):
    """GET /api/account/config — Turnstile sitekey for the login widget
    (null = no gate, local dev) + whether registration requires a verified
    phone. preparation.js reuses it instead of its own endpoint. The native
    request supplies headers, peer, and host to the security
    helpers without an intermediate compatibility object.

    In 'portal' mode it adds what the one portal screen needs:
    `portal_login_url` (the portal's sign-in, '' = none configured),
    `portal_identidad` (the request carries a trusted portal identity — so a
    visitor still without an account was refused, not signed out) and
    `ayuda_whatsapp` (the installation's help line, digits for wa.me) and
    `centro_principal` (the main center whose admission desk the refusal
    names — site_identity.main_center_place()).

    The help line and the main center travel in every mode (TASK-50,
    26.10): password recovery is by email only, and its screen tells an
    account without an email where to get one added."""
    out = {
        "turnstile_sitekey": turnstile.sitekey_from(headers),
        "otp_registro_requerido": _otp_registration_required(),
        "auth_mode": _effective_auth_mode(headers, client_host),
        "renaper_provider": _renaper_provider(),
        "test_identity_provider": _test_auth_enabled(headers, client_host),
    }
    out.update(_help_contact())
    if out["auth_mode"] == "portal":
        out.update(_portal_screen(request))
    return Result.json(out)


def _help_contact():
    """Where a patient asks for help: the installation's help line (digits
    for wa.me, plus its written form) and the main center whose admission
    desk the screens name (the product owner 23.10), None = unset."""
    ayuda = str(site_identity.get("help_whatsapp") or "").strip()
    return {
        "ayuda_whatsapp": format.tel_wa(ayuda) if ayuda else "",
        "ayuda_whatsapp_texto": format.tel_text(ayuda) if ayuda else "",
        "centro_principal": site_identity.main_center_place(),
    }


def _portal_screen(request):
    identidad = False
    if request is not None:
        try:
            identidad = portal_identity.gateway_assertion(request) is not None
        except portal_identity.ConfigurationError:
            identidad = False
    return {
        "portal_login_url": portal_identity.login_url(),
        "portal_identidad": identidad,
        **_help_contact(),
    }


_PORTAL_ENTRY = "/account/portal-login"


def _same_site_path(value):
    """`value` when it is a path on this site, else the account page."""
    value = str(value or "")
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or value.startswith(_PORTAL_ENTRY)):
        return "/account"
    return value


def portal_entry_location(request, nxt):
    """GET /account/portal-login?next=<path> — «Ingresar» on an installation
    whose patients come in through the portal (the product owner 26.10: straight to the
    portal, and back to the page). A request the gateway already vouched for
    goes to the page itself (signed in, or the remedy when refused — never a
    second trip); anyone else to the portal's sign-in with that page as its
    `return`. Without the portal login or a sign-in URL: the page."""
    nxt = _same_site_path(nxt)
    if not portal_identity.patient_login_enabled():
        return "/account"
    try:
        vouched = portal_identity.gateway_assertion(request) is not None
    except portal_identity.ConfigurationError:
        vouched = False
    if vouched:
        return nxt
    return portal_identity.login_url_back_to(nxt) or "/account"


def portal_entry_closed():
    """The native patient entries (password login, sign-up, recovery) on an
    installation whose patients come in only through the portal: the remedy
    — where to sign in — instead of a form that cannot work. None elsewhere."""
    if not portal_identity.patient_login_enabled():
        return None
    return Result.json({
        "error": i18n.t_or("account.form.portal.use_portal",
                           "Entrá con tu cuenta del portal.",
                           i18n.active()),
        "codigo": "acceso_por_portal",
        "portal_login_url": portal_identity.login_url(),
    }, status=403)


# ---- per-IP registration throttle (in-memory, best-effort) ------------------
# Sign-up had NO brake at all: the login has one, and the referral upload has
# abuse_guard, but anyone could POST /api/account/register in a loop and fill
# the patients list with invented DNIs (security review of the public demo,
# 2026-08-01). Counts accounts actually CREATED, never failed attempts, and
# over a whole hour — a family signing up from one phone line, or a whole
# neighbourhood behind one carrier NAT, still gets through; a script does not.
# In memory and best-effort, like `_login_throttled`: a restart clears it.

_REGISTERED = {}          # ip -> [epochs of accounts created]
_REGISTER_MAX = 10        # per window
_REGISTER_WINDOW = 3600   # 1 h


def _register_throttled(ip):
    return rate_window.over_limit(_REGISTERED, ip, _REGISTER_MAX, _REGISTER_WINDOW)


def _register_core(body, ip):
    """Pure domain function: `body` already parsed, `ip` already resolved —
    no handler-shaped object touched here at all.

    A visitor whose active language is not Spanish may sign up WITHOUT a DNI
    (the product owner, 2026-09-11): the email becomes required (valid + unique among
    users) and the server mints a synthetic identity key (users.identity_kind
    'synthetic') so every DNI-keyed table keeps working. A Spanish sign-up
    without DNI fails exactly as before."""
    if _register_throttled(ip):
        return Result.json({"error": "demasiadas_cuentas"}, status=429)

    lang_activo = i18n.active()
    dni = _norm_dni(body.get("dni"))
    email = (body.get("email") or "").strip() or None
    identity_kind = None

    if not dni and not _es_lang_active():
        # No document behind this account: the email carries the identity
        # (login + recovery key on it) and the key itself is minted.
        if not email or not _valid_email(email):
            return Result.json(
                {"error": i18n.t_or("account.form.error.email_required",
                                    "Dejá tu email para crear la cuenta.", lang_activo)},
                status=400)
        if store.get_user_by_email(email):
            return Result.json(
                {"error": i18n.t_or("account.form.error.email_taken",
                                    "Ya hay una cuenta con ese email.", lang_activo)},
                status=409)
        dni = _mint_synthetic_key(lambda cand: store.get_user(cand))
        identity_kind = "synthetic"
    else:
        if not format.dni_valid_length(dni):
            return Result.json({"error": i18n.t_or(
                "account.form.error.dni_length_server",
                "DNI inválido (%d a %d dígitos)" %
                (format.DNI["length_min"], format.DNI["length_max"]),
                lang_activo,
                min=format.DNI["length_min"], max=format.DNI["length_max"])}, status=400)

    password = str(body.get("password") or "")
    if len(password) < 8:
        return Result.json(
            {"error": i18n.t_or("account.form.password.min_length_error",
                                "La contraseña tiene que tener al menos 8 caracteres.",
                                lang_activo)}, status=400)

    nombre = (body.get("nombre") or "").strip() or None
    apellido = (body.get("apellido") or "").strip() or None

    sexo = (body.get("sexo") or "").strip().upper() or None
    if sexo is not None and sexo not in ("M", "F", "X"):
        return Result.json({"error": "sexo inválido (M, F o X)"}, status=400)

    nac_raw = (body.get("nacimiento") or "").strip()
    nacimiento = _parse_date(nac_raw)
    if nac_raw and nacimiento is None:
        return Result.json({"error": "fecha de nacimiento inválida"}, status=400)

    error_tel = _phone_error(body.get("contacto"), lang_activo)
    if error_tel:
        return Result.json({"error": error_tel}, status=400)
    contacto = _norm_phone(body.get("contacto"))

    if email and not _valid_email(email):
        return Result.json({"error": i18n.t_or("account.form.error.email_invalid",
                                               "email inválido", lang_activo)}, status=400)

    if not contacto and not email:
        return Result.json({"error": "contacto_o_email_requerido"}, status=400)

    # WhatsApp OTP registration gating — only if it's turned on (see
    # _otp_registration_required) and only when the patient left a phone
    # number; email-only registration keeps working as it does today.
    if contacto and _otp_registration_required():
        if not store.consume_verified_otp(contacto, "registro"):
            return Result.json({"error": "telefono_no_verificado"}, status=400)

    # DNI already registered -> 409 (the client offers "Already have an account? Log in") — unless the
    # desk checks documents (_dni_claims_enabled): then the second sign-up becomes a claim the desk
    # sees, under a minted key, and the real owner is never locked out by whoever typed the number first.
    claim = None
    if identity_kind != "synthetic" and _dni_claims_enabled():
        claim = ("unverified", dni)
    if store.get_user(dni):
        if claim is None or len(store.pending_claimants(dni)) >= MAX_PENDING_CLAIMS:
            # Check off, or the number already has as many open claims as the desk reads at once:
            # the old answer, never an unbounded pile of accounts (and of password hashes at login).
            return Result.json({"error": "dni_registrado"}, status=409)
        dni, identity_kind, claim = _claim_key(dni), "synthetic", ("pending", dni)

    cobertura = _norm_coverage(body.get("cobertura"), dni=dni)
    domicilio = _norm_address(body.get("domicilio"))

    # Idioma de la cuenta (2026-08-04, users.lang): el que el
    # visitante YA estaba viendo al registrarse (routing.dispatch() ya lo
    # resolvió para este request) — notifications.py lo usa para saber en qué
    # idioma mandar cada email/WhatsApp, porque un recordatorio del día
    # siguiente no tiene request del que leer i18n.active(). En una
    # instalación de un solo idioma esto siempre da el canónico, sin cambio de
    # comportamiento.
    # Account and first session must commit together. Only a DNI conflict
    # becomes dni_registrado; unrelated storage failures still surface.
    for attempt in range(3):
        try:
            usuario, token = store.create_user_and_session(
                dni, nombre, apellido, nacimiento, sexo, contacto, password,
                email=email, cobertura=cobertura, domicilio=domicilio,
                lang=lang_activo, identity_kind=identity_kind, claim=claim)
            break
        except store.UserEmailTaken:
            return Result.json(
                {"error": i18n.t_or("account.form.error.email_taken",
                                    "Ya hay una cuenta con ese email.", lang_activo)},
                status=409)
        except store.UserKeyTaken:
            if attempt == 2:
                return Result.json({"error": "dni_registrado"}, status=409)
            if claim is not None and claim[0] == "unverified":
                # Lost the race for the number: the other sign-up won, this one becomes the claim.
                dni, identity_kind, claim = _claim_key(dni), "synthetic", ("pending", dni)
            elif identity_kind == "synthetic":
                dni = _mint_synthetic_key(lambda candidate: store.get_user(candidate))
            else:
                return Result.json({"error": "dni_registrado"}, status=409)
    import time as _t
    _REGISTERED.setdefault(ip, []).append(_t.time())
    return Result.json({"ok": True, "usuario": _with_verification(_user_json(usuario))},
                        status=200, set_cookie=_set_cookie(token))


# ---- per-IP login throttle (in-memory, best-effort) --------------------------
# Same pattern as admin_auth: the patient login had NO brute-force brake at
# all (2026-07-17 security review, item F). 5-minute window.

_LOGIN_ATTEMPTS = {}      # ip -> [epochs of failed attempts]
_LOGIN_MAX = 10           # per window
_LOGIN_WINDOW = 300       # 5 min


def _login_throttled(ip):
    return rate_window.over_limit(_LOGIN_ATTEMPTS, ip, _LOGIN_MAX, _LOGIN_WINDOW)


# Lockout by account (security review 2026-11, M-5): the per-IP brake alone lets a script rotating
# addresses guess one DNI's password (or recovery code) without end.
_DNI_FAILS = {}           # dni (or "email:<address>") -> [epochs of login attempts]
_DNI_MAX = 10
_DNI_WINDOW = 900         # 15 min
_RESET_FAILS = {}         # dni -> [epochs of recovery-code attempts]: its own count, so ten wrong codes
_RESET_IP = {}            # ip -> [epochs of recovery-code attempts]   never lock the victim's login
_RESET_IP_MAX = 20
_RATE_LOCK = threading.Lock()


def _reserve(hits, key, limit, window):
    """Count one attempt BEFORE the secret is checked, in one step with the limit test (security review
    2026-11, item 13: «ask, then check, then count» let parallel requests all pass the question first).
    False = the key is locked and the attempt was not counted. A blank key is never limited."""
    if not key:
        return True
    with _RATE_LOCK:
        return rate_window.allow(hits, key, limit, window)


def _login_keys(dni, body):
    """What a login attempt is counted on: the DNI, and for a login by email the address too — an unknown
    address is limited exactly like a known one (item 15: 429 for one and 401 for the other told them apart)."""
    keys = [dni] if dni else []
    if not dni or not body.get("dni"):
        email = str(body.get("email") or "").strip().lower()
        if email:
            keys.append("email:" + email[:254])
    return keys


def _login_core(headers, client_host, body, ip):
    """Authenticate from explicit request values and an already-resolved IP."""
    from app.auth import admin_auth
    if _login_throttled(ip) or admin_auth._throttled(ip):
        return Result.json({"error": "demasiados_intentos"}, status=429)

    if _effective_auth_mode(headers, client_host) == "renaper":
        return _login_renaper_core()

    if not turnstile.verificar_from(
        headers, client_host, body.get("turnstile_token") or ""
    ):
        return Result.json({"error": "turnstile"}, status=403)

    dni = _norm_dni(body.get("dni"))
    password = str(body.get("password") or "")
    # {email, password} is an alternative to {dni, password} (the product owner, 2026-09-11):
    # a visitor that signed up without a DNI keys their login on the email.
    # Resolution only — everything downstream (verify, throttle, session)
    # still runs on the resolved user's own key.
    if not dni:
        por_email = store.get_user_by_email(str(body.get("email") or ""))
        if por_email:
            dni = _norm_dni(por_email.get("dni") or "")
    keys = _login_keys(dni, body)
    if not all([_reserve(_DNI_FAILS, k, _DNI_MAX, _DNI_WINDOW) for k in keys]):
        return Result.json({"error": "demasiados_intentos"}, status=429)
    usuario = store.verify_password(dni, password)
    por_reclamo = False
    if not usuario and dni:
        # Not the password of the account holding this number: maybe of a sign-up that claimed it
        # (it lives under a minted key, so it can only be reached through the number it asked for).
        for clave in store.pending_claimants(dni):
            usuario = store.verify_password(clave, password)
            if usuario:
                por_reclamo = True
                break
    if not usuario:
        staff = admin_auth.patient_staff_login(
            headers, client_host, str(body.get("email") or body.get("dni") or "").strip(), password)
        if staff.status == 200:
            _LOGIN_ATTEMPTS.pop(ip, None)
            for k in keys:
                _DNI_FAILS.pop(k, None)
            return Result.json(staff.body, status=200, set_cookie=staff.set_cookie)
        import time as _t
        _LOGIN_ATTEMPTS.setdefault(ip, []).append(_t.time())
        return Result.json({"error": "credenciales"}, status=401)
    _LOGIN_ATTEMPTS.pop(ip, None)
    if not por_reclamo:  # a claimant's own login must not reset the holder's lockout counter
        for k in keys:
            _DNI_FAILS.pop(k, None)
    token = store.create_session(usuario["dni"])
    return Result.json({"ok": True, "usuario": _with_verification(_user_json(usuario))},
                        status=200, set_cookie=_set_cookie(token))


def _login_renaper_core():
    """Federated mode never accepts a naked DNI through the password route."""
    return Result.json({"error": "proveedor_de_identidad_requerido"}, status=400)


def _persist_provider_identity_core(persona, provider, ip=""):
    """Persist one trusted identity assertion and open the existing session.
    Pure: no handler-shaped object touched at all."""
    dni = _norm_dni(persona.get("dni"))
    domicilio = None
    if persona.get("domicilio") or persona.get("localidad"):
        domicilio = {
            "texto": (persona.get("domicilio") or "").strip(),
            "localidad": (persona.get("localidad") or "").strip(),
        }
        domicilio = {k: v for k, v in domicilio.items() if v}

    existente = store.get_user(dni)
    if existente:
        # The provider owns civil identity fields.  A test profile can also
        # seed contact/address data; missing values never erase account data.
        # `lang` isn't something RENAPER/Mi Argentina carries — keep whatever
        # was already saved (None the first time, same as any other account).
        store.update_user(
            dni, nombre=persona["nombre"], apellido=persona["apellido"],
            nacimiento=persona["nacimiento"], sexo=persona["sexo"],
            contacto=persona.get("contacto") or existente.get("contacto"),
            email=persona.get("email") or existente.get("email"),
            cobertura=existente.get("cobertura"),
            domicilio=domicilio or existente.get("domicilio"),
            avisos=existente.get("avisos"),
            lang=existente.get("lang"), coverage_write=False,
        )
    else:
        # This random hash only satisfies the existing password column.  It is
        # never presented or accepted while federated mode is active. Same
        # criterion as the password-mode register: the language the visitor
        # is viewing the login page in, right now.
        store.create_user(
            dni, persona["nombre"], persona["apellido"], persona["nacimiento"],
            persona["sexo"], persona.get("contacto"), secrets.token_urlsafe(32),
            email=persona.get("email"), domicilio=domicilio, lang=i18n.active(),
        )

    _LOGIN_ATTEMPTS.pop(ip, None)
    token = store.create_session(dni)
    usuario = store.get_user(dni)
    return Result.json({"ok": True, "usuario": _user_json(usuario), "auth_provider": provider},
                        status=200, set_cookie=_set_cookie(token))


def _test_identities_core(headers, client_host):
    if not _test_auth_enabled(headers, client_host):
        return Result.json({"error": "test_auth_no_disponible"}, status=404)
    from app.connectors.renaper import renaper
    # A leftover copy of a real account carries that patient's phone, email
    # and address until the purge script removes it: never listed.
    return Result.json({"identidades": [
        item for item in renaper.list_test_identities() if not renaper.is_account_copy(item)]})


def _save_test_identity_core(headers, client_host, body):
    if not _test_auth_enabled(headers, client_host):
        return Result.json({"error": "test_auth_no_disponible"}, status=404)
    from app.connectors.renaper import renaper
    # A test person may hold the DNI of an existing account only if it already
    # was a test person (2026-10-23): a NEW one would open that account, and
    # saving a leftover copy of a real account would rewrite its origin to
    # 'manual' (the form sends none) and open the account again. Editing a
    # test person keeps working.
    nuevo = _norm_dni((body.get("identidad") or body).get("dni")
                      if isinstance(body.get("identidad") or body, dict) else "")
    if nuevo and store.get_user(nuevo):
        actual = renaper.get_test_identity(nuevo)
        if actual is None or renaper.is_account_copy(actual):
            return Result.json({"error": "cuenta_real_no_simulable"}, status=409)
    try:
        identity = renaper.save_test_identity(
            body.get("identidad") or body,
            original_dni=body.get("original_dni"),
        )
    except renaper.InvalidIdentity as exc:
        return Result.json({"error": str(exc)}, status=409 if str(exc) == "dni_ya_existe" else 400)
    return Result.json({"ok": True, "identidad": identity})


def _random_test_identity_core(headers, client_host):
    if not _test_auth_enabled(headers, client_host):
        return Result.json({"error": "test_auth_no_disponible"}, status=404)
    from app.connectors.renaper import renaper
    try:
        identity = renaper.create_random_identity()
    except renaper.InvalidIdentity as exc:
        return Result.json({"error": str(exc)}, status=500)
    return Result.json({"ok": True, "identidad": identity}, status=201)


def _login_test_identity_core(headers, client_host, body, ip=""):
    if not _test_auth_enabled(headers, client_host):
        return Result.json({"error": "test_auth_no_disponible"}, status=404)
    provider = str(body.get("provider") or "").strip().lower()
    if provider not in ("mi_argentina", "renaper"):
        return Result.json({"error": "proveedor_invalido"}, status=400)
    dni = _norm_dni(body.get("dni"))
    from app import connectors
    if not format.dni_valid_length(dni):
        return Result.json({"error": "dni_invalido"}, status=400)
    # The identity_verification connector (renaper by default — registry
    # rewire 2026-08-20). This endpoint is the synthetic-identity login only:
    # production Mi Argentina OIDC (auth_renaper.py) is not on the registry.
    # `conn disable identity_verification` switches THIS endpoint off (404,
    # same answer as the LAN/dev gate above), nothing else.
    adapter = connectors.adapter_for(connectors.IDENTITY_VERIFICATION)
    if adapter is None:
        return Result.json({"error": "identity_verification_no_disponible"}, status=404)
    # Any plausible DNI logs in (the product owner, 2026-08-03) — no more dead end — as long
    # as it is a TEST person. A DNI that already has a real account and is not
    # a registered test person is refused (2026-10-23): the simulator used to
    # open that account and copy its contact data into the test registry.
    # renaper.authenticate owns that rule; this side only hands it the account.
    existente = store.get_user(dni)
    existing_civil = None
    if existente:
        nacimiento = existente.get("nacimiento")
        domicilio = existente.get("domicilio") or {}
        if not isinstance(domicilio, dict):
            domicilio = {}
        existing_civil = {
            "nombre": existente.get("nombre"),
            "apellido": existente.get("apellido"),
            "nacimiento": nacimiento.isoformat() if isinstance(nacimiento, (date, datetime)) else nacimiento,
            "sexo": existente.get("sexo"),
            "contacto": existente.get("contacto"),
            "email": existente.get("email"),
            "domicilio": domicilio.get("texto"),
            "localidad": domicilio.get("localidad"),
        }
    try:
        # `existing_user` MUST reach the implementation (the adapter forwards
        # **kwargs) — it is how the stub knows the DNI belongs to a real account.
        persona = adapter.authenticate(dni, provider="stub", existing_user=existing_civil)
    except connectors.IdentityNotFound as exc:
        if str(exc) == "cuenta_real_no_simulable":
            return Result.json({"error": "cuenta_real_no_simulable"}, status=409)
        return Result.json({"error": "dni_invalido"}, status=400)
    except connectors.InvalidIdentity:
        # The same exception object renaper.py raises (it imports it from
        # the framework), so this is today's 400 unchanged.
        return Result.json({"error": "dni_invalido"}, status=400)
    except connectors.ProviderUnavailable:
        # The stub never raises this; a non-stub connector legitimately may.
        return Result.json({"error": "identity_verification_no_disponible"}, status=503)
    # The real SSO validates DNI + sex as it appears on the document. Our own
    # simulator page stopped asking for the sex (the product owner, 2026-08-02: one field to
    # get wrong, for zero signal in a simulator), so it no longer sends it —
    # the check stays for any client that does.
    sexo = str(body.get("sexo") or "").strip().upper()
    if sexo and (persona.get("sexo") or "").strip().upper() != sexo:
        return Result.json({"error": "sexo_no_coincide"}, status=401)
    return _persist_provider_identity_core(persona, provider, ip=ip)


def _logout_core(request):
    """Read the session cookie from the native request. Never
    guarded by `_guarded`: this "never fails even if the DB is down" — the
    try/except is right here, same as before."""
    token = _cookie_token(request)
    if token:
        try:
            store.delete_session(token)
        except store.DbUnavailable:
            pass
        except Exception:  # noqa: BLE001
            pass
    return Result.json({"ok": True}, status=200, set_cookie=_clear_cookie())


def _me_core(request):
    """Resolve the account associated with the native request cookie."""
    usuario = current_user(request, raise_db_unavailable=True)
    if not usuario:
        return Result.json({"error": "no_autenticado"}, status=401)
    return Result.json({"usuario": _with_verification(_user_json(usuario))})


def _dependents_core(usuario):
    """The dependents of the signed-in account `usuario` (the route's `account_deps.current_account`)."""
    items = [_user_json(x) for x in store.list_dependents(usuario["dni"])]
    return Result.json({"dependientes": items})


def _age_years(nacimiento):
    if not nacimiento:
        return None
    if not isinstance(nacimiento, date):
        try:
            nacimiento = date.fromisoformat(str(nacimiento)[:10])
        except ValueError:
            return None
    hoy = date.today()
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))


def _add_dependent_core(usuario, body):
    """Adds a dependent. The data is the account holder's sworn statement
    (declaración jurada); the facility/HSI can register or verify it later.
    Pure: `usuario` (the already-resolved session user) and `body` in,
    `Result` out.

    For a request whose active language is not Spanish the dependent's DNI is
    OPTIONAL (the product owner, 2026-09-11): when absent, a synthetic key is minted the
    same way sign-up mints the holder's. The dependents table has its own dni
    column with no FK to users (UNIQUE per holder), so a minted "9…"-shaped
    key there stands on its own."""
    dni = _norm_dni(body.get("dni"))
    if not dni and not _es_lang_active():
        dni = None  # minted right before the save, after every field validated
    elif not format.dni_valid_length(dni):
        return Result.json({"error": i18n.t_or(
            "account.form.error.dni_length_server",
            "DNI inválido (%d a %d dígitos)" %
            (format.DNI["length_min"], format.DNI["length_max"]),
            i18n.active(),
            min=format.DNI["length_min"], max=format.DNI["length_max"])}, status=400)
    elif dni == _norm_dni(usuario.get("dni")):
        return Result.json({"error": "Ese DNI ya es el titular de la cuenta"}, status=400)
    nombre = (body.get("nombre") or "").strip()
    apellido = (body.get("apellido") or "").strip()
    if not nombre or not apellido:
        return Result.json({"error": "Completá nombre y apellido"}, status=400)
    nac_raw = (body.get("nacimiento") or "").strip()
    nacimiento = _parse_date(nac_raw)
    edad = _age_years(nacimiento)
    if nacimiento is None or edad is None:
        return Result.json({"error": "fecha de nacimiento inválida"}, status=400)
    if edad < 0 or edad > 130:
        return Result.json({"error": "Revisá la fecha de nacimiento"}, status=400)
    sexo = (body.get("sexo") or "").strip().upper()
    if sexo not in ("M", "F", "X"):
        return Result.json({"error": "sexo inválido (M, F o X)"}, status=400)
    vinculo = (body.get("vinculo") or "").strip().lower()
    if vinculo not in ("hijo", "tutoriado", "otro"):
        return Result.json({"error": "Elegí el vínculo con la persona"}, status=400)
    if body.get("declaracion_jurada") is not True:
        return Result.json({"error": "Confirmá que los datos son correctos"}, status=400)

    if dni is None:
        holder = _norm_dni(usuario.get("dni"))
        dni = _mint_synthetic_key(
            lambda cand: cand == holder or store.get_dependent(holder, cand))

    item = store.save_dependent(usuario["dni"], dni, nombre, apellido,
                                  nacimiento, sexo, vinculo)
    return Result.json({"ok": True, "dependiente": _user_json(item)})


def _delete_dependent_core(usuario, body):
    dni = _norm_dni(body.get("dni"))
    return Result.json({"ok": store.delete_dependent(usuario["dni"], dni)})


def _update_core(usuario, body):
    """Pure: the already-resolved session `usuario` + parsed `body` in, a
    `Result` out. Identity (DNI) ALWAYS comes from the resolved session
    user, never re-derived from the body."""
    if "cobertura" in body:
        from app.cuentas.health_coverage import revision
        # Old cached clients may submit an unchanged coverage along with a
        # contact edit. Permit that no-op only; never a blind replacement.
        previous = usuario.get("cobertura")
        if body.get("coverage_revision") != revision(previous):
            unchanged_legacy = ("coverage_revision" not in body
                and _norm_coverage(body.get("cobertura"), dni=usuario["dni"], existente=previous) == previous)
            if not unchanged_legacy:
                return Result.json({"error": "La cobertura cambió en otra pantalla. Recargá Mi cuenta para conservarla."}, status=409)
    # Editable DNI (the product owner 2026-07-18): the client sends it only if it comes in
    # a valid format. If it changed from the session's, it gets validated and
    # the key (login/HSI, appointments, dependents) is carried over to the
    # new DNI BEFORE touching anything else. Without the `dni` key, or equal
    # to the current one, nothing gets touched.
    dni_efectivo = usuario["dni"]
    dni_nuevo = _norm_dni(body.get("dni")) if body.get("dni") is not None else ""
    if dni_nuevo and dni_nuevo != usuario["dni"]:
        # DNI migration commits separately. Never combine it with a coverage
        # replacement, whose CAS could fail after the identity has moved.
        if "cobertura" in body and _norm_coverage(body["cobertura"], dni=usuario["dni"], existente=usuario.get("cobertura")) != usuario.get("cobertura"):
            return Result.json({"error": "Guardá el DNI y la cobertura por separado."}, status=409)
        if portal_identity.enabled():
            return Result.json({"error": "dni_administrado_por_portal"}, status=409)
        if not (7 <= len(dni_nuevo) <= 9):
            return Result.json({"error": "dni_invalido"}, status=400)
        if not store.change_dni(usuario["dni"], dni_nuevo):
            return Result.json({"error": "dni_en_uso"}, status=409)
        try:
            from app.cuentas import account_photo
            account_photo.move(usuario["dni"], dni_nuevo)
        except Exception:  # noqa: BLE001 — the DNI change already committed
            pass
        dni_efectivo = dni_nuevo

    nombre = (body.get("nombre") or "").strip() or None
    apellido = (body.get("apellido") or "").strip() or None

    sexo = (body.get("sexo") or "").strip().upper() or None
    if sexo is not None and sexo not in ("M", "F", "X"):
        return Result.json({"error": "sexo inválido (M, F o X)"}, status=400)

    nac_raw = (body.get("nacimiento") or "").strip()
    nacimiento = _parse_date(nac_raw)
    if nac_raw and nacimiento is None:
        return Result.json({"error": "fecha de nacimiento inválida"}, status=400)

    contacto = _norm_phone(body.get("contacto"))
    # A number already on file is not re-judged by an autosave of another
    # field; a NEW one has to be callable (TASK-51).
    if contacto != usuario.get("contacto"):
        error_tel = _phone_error(body.get("contacto"), i18n.active())
        if error_tel:
            return Result.json({"error": error_tel}, status=400)

    email = (body.get("email") or "").strip() or None
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return Result.json({"error": "email inválido"}, status=400)

    if not contacto and not email:
        return Result.json({"error": "contacto_o_email_requerido"}, status=400)

    # Without the `cobertura` key in the body (pages with old cached JS) keeps
    # what's saved — a name autosave can't erase the insurer.
    if "cobertura" in body:
        cobertura = _norm_coverage(body.get("cobertura"), dni=dni_efectivo,
                                    existente=usuario.get("cobertura"))
    else:
        cobertura = usuario.get("cobertura")

    # Same criterion as cobertura: without the key in the body, keeps the
    # saved address (an old autosave doesn't erase it); with the key,
    # whatever it sends wins (empty dict = deliberate delete).
    if "domicilio" in body:
        domicilio = _norm_address(body.get("domicilio"))
    else:
        domicilio = usuario.get("domicilio")

    # Same as cobertura: without the `avisos` key in the body, keeps what's saved.
    if "avisos" in body:
        avisos = _norm_notifications(body.get("avisos"), existente=usuario.get("avisos"))
    else:
        avisos = usuario.get("avisos")

    # Same criterion, for the notification language (2026-08-04): without the
    # `lang` key, keeps what's saved — an old cached client can't silently
    # reset it to null on an unrelated autosave.
    if "lang" in body:
        lang = _norm_lang(body.get("lang"), existente=usuario.get("lang"))
    else:
        lang = usuario.get("lang")

    saved = store.update_user(dni_efectivo, nombre=nombre, apellido=apellido,
                          nacimiento=nacimiento, sexo=sexo, contacto=contacto, email=email,
                          cobertura=cobertura, domicilio=domicilio, avisos=avisos, lang=lang,
                          coverage_write="cobertura" in body and dni_efectivo == usuario["dni"],
                          coverage_guard="cobertura" in body and dni_efectivo == usuario["dni"],
                          coverage_expected=usuario.get("cobertura"))
    if saved is False:
        return Result.json({"error": "La cobertura cambió en otra pantalla. Recargá Mi cuenta para conservarla."}, status=409)
    actualizado = store.get_user(dni_efectivo)
    return Result.json({"ok": True, "usuario": _user_json(actualizado)})


# ---- password recovery (link by email) ---------------------------------------

def _base_url(request=None):
    # Origin pinned by env in prod (site_identity.base_url) — the client's
    # Host doesn't decide which domain the reset link points to (security,
    # item F).
    return site_identity.base_url(request)


def _send_email(destinatario, asunto, cuerpo):
    """Sends the recovery email through the installation's email provider
    (notifications/mail.py — Mailgun by default, Mailjet when selected).
    Best-effort: never blocks the recovery flow (the response to the client
    is the same with or without a real send, so it never leaks which DNI has
    an account) and degrades silently if the provider isn't configured yet."""
    from app.notifications import mail
    return mail.send(destinatario, asunto, cuerpo)


def _send_otp_whatsapp(contacto, code):
    """Requests the OTP send (otp_send.py). The one production transport is
    Meta's approved Authentication template (`OTP_PROVIDER=meta` +
    `META_WA_OTP_TEMPLATE`, 2026-11-16; no site enables it yet) — without
    them it is a logged no-op: the WAHA sender only reaches a loopback dev
    URL (transport contract 2026-08-14) and SMS is a stub. Best-effort just
    like _send_email: never blocks the calling flow."""
    from app.notifications import otp_send
    return otp_send.enviar_otp(contacto, code)


def _return_path(value):
    """A path of THIS site to come back to, or "" — never '//host', '/\\host'
    (browsers read the backslash as '/'), a scheme, whitespace or an
    oversized value. Same rule as Account.safeNext on the client."""
    v = str(value or "")
    if (not v.startswith("/") or v.startswith("//") or "\\" in v
            or len(v) > 1000 or any(c.isspace() or ord(c) < 32 for c in v)):
        return ""
    return v


def _recover_core(body):
    """Create the recovery response from parsed values only.

    `{dni}` and `{email}` are both accepted (the product owner, 2026-09-11): an account
    that signed up without a DNI keys its recovery on the email, and the
    client for a non-Spanish visitor never asks for a DNI in the first
    place. The reset step itself consumes the link's opaque token — it
    never demands any identifier from the visitor."""
    dni = _norm_dni(body.get("dni"))
    email = str(body.get("email") or "").strip()
    usuario = None
    if format.dni_valid_length(dni):
        usuario = store.get_user(dni)
    elif email:
        usuario = store.get_user_by_email(email)
    # The mail and the code go out AFTER the answer, off the request: an account that exists takes the same
    # time to answer as one that does not (security review 2026-11, L-1) — also an address that does not
    # exist goes through the same hand-off, so a full pool of senders is not a timing difference (item 16).
    _detach(_deliver_recovery, usuario, _return_path(body.get("next")))
    # Same response whether or not the account/email/contact exists, always —
    # prevents someone from using this endpoint to find out which DNI is
    # registered.
    return Result.json({"ok": True})


_DETACHED = []
_DETACHED_MAX = 32
_DETACH_WAIT = 2.0


def _detach(fn, *args):
    import threading
    live = [x for x in _DETACHED if x.is_alive()]
    if len(live) >= _DETACHED_MAX:
        # A flood: stay bounded and never do the work on the request (it would take longer for an account
        # that exists). Both kinds wait the same for the oldest sender, then the work is dropped if the
        # pool is still full — a recovery asked again a minute later goes out.
        live[0].join(_DETACH_WAIT)
        live = [x for x in live if x.is_alive()]
        if len(live) >= _DETACHED_MAX:
            _DETACHED[:] = live
            logging.getLogger(__name__).warning("recovery delivery dropped: sender pool full")
            return
    t = threading.Thread(target=fn, args=args, daemon=True)
    live.append(t)
    _DETACHED[:] = live
    t.start()


def drain_detached(timeout=10):
    """Wait for the sends started by `_detach` (tests; the process never needs it)."""
    for t in list(_DETACHED):
        t.join(timeout)


def _deliver_recovery(usuario, volver):
    try:
        _deliver_recovery_now(usuario, volver)
    except Exception as exc:  # noqa: BLE001 — best-effort, as the synchronous send always was
        logging.getLogger(__name__).error("recovery delivery failed: %s", log_pii.exc(exc))


def _deliver_recovery_now(usuario, volver):
    if usuario:
        destino_email = (usuario or {}).get("email")
        contacto = (usuario or {}).get("contacto")
        if destino_email:
            token = store.create_reset_token(usuario["dni"])
            link = f"{_base_url(None)}/account/reset?token={token}"
            # The link returns to where it was asked for (a booking left
            # half-done, its ?slot= included) even when opened in another
            # browser — the return point travels in the link, not in this one.
            if volver and volver != "/account":
                link += "&next=" + urllib.parse.quote(volver, safe="")
            nombre = (usuario.get("nombre") or "").strip()
            saludo = f"Hola {nombre}," if nombre else "Hola,"
            cuerpo = (
                f"{saludo}\n\n"
                f"Pediste recuperar tu contraseña de {theme.brand_name()}.\n\n"
                f"Elegí una contraseña nueva acá (el link vence en 1 hora):\n{link}\n\n"
                "Si no fuiste vos, ignorá este mensaje: tu contraseña sigue siendo la misma."
            )
            _send_email(destino_email, f"Recuperar tu contraseña — {theme.brand_name()}", cuerpo)
        # On top of the email link (if there's an email), an OTP code is
        # requested over WhatsApp when the account has a phone number: covers
        # the very common case, in registration today, of accounts with NO
        # email (only a contact). Purely additive — the send is a logged
        # no-op unless a loopback dev WAHA is configured (production has no
        # OTP transport, see otp_send.py), it doesn't break the email link.
        if contacto and not store.otp_recent(contacto, "recuperar"):
            code = store.create_otp(contacto, "recuperar")
            _send_otp_whatsapp(contacto, code)


def _reset_otp_core(body, ip=""):
    """POST /api/account/reset_otp {dni, code, password} — password recovery,
    an alternative to the email link: consumes the OTP code REQUESTED over
    WhatsApp for `contacto` (see _recover — the send itself is a logged
    no-op until a transport exists, otp_send.py) and sets the new
    password. Fully pure."""
    dni = _norm_dni(body.get("dni"))
    password = str(body.get("password") or "")
    if len(password) < 8:
        return Result.json(
            {"error": i18n.t_or("account.form.password.min_length_error", "La contraseña tiene que tener al menos 8 caracteres.", i18n.active())}, status=400)

    # Its own counters (security review 2026-11, item 12): ten wrong codes sent in the victim's name used to
    # fill the login's count and lock them out of their own account; now they lock only this recovery, and
    # one address is limited as well.
    if not _reserve(_RESET_IP, ip, _RESET_IP_MAX, _DNI_WINDOW) \
            or not _reserve(_RESET_FAILS, dni, _DNI_MAX, _DNI_WINDOW):
        return Result.json({"error": "demasiados_intentos"}, status=429)
    usuario = store.get_user(dni) if format.dni_valid_length(dni) else None
    contacto = (usuario or {}).get("contacto")
    if not usuario or not contacto or not store.verify_otp(contacto, "recuperar", body.get("code")):
        return Result.json({"error": "codigo_invalido"}, status=400)

    _RESET_FAILS.pop(dni, None)
    store.set_password(dni, password)
    nuevo_token = store.create_session(dni)
    actualizado = store.get_user(dni)
    return Result.json({"ok": True, "usuario": _user_json(actualizado)},
                        status=200, set_cookie=_set_cookie(nuevo_token))


# ---- generic OTP (register + recover); WhatsApp send seam, no-op without a transport

_OTP_PURPOSES = ("registro", "recuperar")


def _otp_send_core(body):
    """POST /api/account/otp/send {contacto, proposito}. Rate-limited (one
    active code at a time, 45s cooldown to resend — see the storage adapter).
    Always responds {"ok": true} except for invalid data: no need to hide
    whether the real send worked, `contacto` isn't secret. Fully pure."""
    contacto = _norm_phone(body.get("contacto"))
    proposito = str(body.get("proposito") or "").strip().lower()
    if not contacto or _phone_error(body.get("contacto"), i18n.active()):
        return Result.json({"error": "contacto inválido"}, status=400)
    if proposito not in _OTP_PURPOSES:
        return Result.json({"error": "proposito inválido"}, status=400)

    if store.otp_recent(contacto, proposito):
        return Result.json({"error": "espera_unos_segundos"}, status=429)

    code = store.create_otp(contacto, proposito)
    _send_otp_whatsapp(contacto, code)
    return Result.json({"ok": True})


def _otp_verify_core(body):
    """POST /api/account/otp/verify {contacto, proposito, code}. Success ->
    issues the 15-minute 'verificado' ticket that _register/_reset_otp
    consume. Fully pure."""
    contacto = _norm_phone(body.get("contacto"))
    proposito = str(body.get("proposito") or "").strip().lower()
    if not contacto or proposito not in _OTP_PURPOSES:
        return Result.json({"ok": False}, status=400)

    ok = store.verify_otp(contacto, proposito, body.get("code"))
    return Result.json({"ok": ok}, status=200 if ok else 400)


def _reset_core(body):
    """Fully pure."""
    token = str(body.get("token") or "").strip()
    password = str(body.get("password") or "")
    if len(password) < 8:
        return Result.json(
            {"error": i18n.t_or("account.form.password.min_length_error", "La contraseña tiene que tener al menos 8 caracteres.", i18n.active())}, status=400)

    dni = store.consume_reset_token(token)
    if not dni:
        return Result.json({"error": "token_invalido"}, status=400)

    store.set_password(dni, password)
    nuevo_token = store.create_session(dni)
    usuario = store.get_user(dni)
    return Result.json({"ok": True, "usuario": _user_json(usuario)},
                        status=200, set_cookie=_set_cookie(nuevo_token))


# ---- Canonical account/auth Jinja pages --------------------------------------
# Both HTTP transports share these renderers. Account state still resolves
# client-side through /api/account/me; the initial authenticated projection is
# serialized into the account template when available.


def _script_json(obj, *, compact=False):
    if compact:
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        raw = json.dumps(obj, ensure_ascii=False)
    return raw.replace("<", "\\u003c")


def _render_account_template(
        request: Request, template_name, *, context=None, site_pass, theme_pass,
        demo_pass):
    """Render one canonical account template for the native HTTP request."""
    from app.core import jinja_env

    lang = i18n.resolve_lang(request)
    _bound_lang, token = i18n.bind_lang(lang)
    try:
        rendered = jinja_env.render(template_name, **(context or {}))
    finally:
        i18n.unbind(token)
    if site_pass:
        rendered = site_identity.render(rendered)
    rendered = i18n.inject_html_lang(rendered, lang)
    rendered = i18n.inject_bootstrap(rendered, request, lang)
    if theme_pass:
        rendered = theme.inject(rendered)
    if demo_pass:
        rendered = demo_installation.inject_html(
            rendered, "public", lang=lang, switchable=True)
    if isinstance(rendered, str):
        return rendered.encode("utf-8")
    return rendered


def render_account_body(request: Request, bootstrap_user=None, canonical_path=None):
    bootstrap = account_bootstrap(request, bootstrap_user=bootstrap_user)
    # Markup: JSON ya defensivamente serializado por _script_json (todo `<`
    # viaja escapado) que el template inyecta CRUDO en su <script> — con el
    # autoescape del entorno Jinja (2026-09-27) un str plano quedaría
    # entity-escapado y rompería el JSON embebido.
    from markupsafe import Markup
    context = {
        "account_bootstrap": Markup(_script_json(bootstrap, compact=True)),
        "account_canonical": Markup(_script_json(canonical_path)),
    }
    if not bootstrap and portal_identity.patient_login_enabled():
        # The heading is right on first paint: «Ingresar» (the portal entry
        # follows), or the refusal when the portal vouched for the person.
        refused = _portal_screen(request)["portal_identidad"]
        context["portal_head"] = "refused" if refused else "signin"
        context["account_portal"] = Markup(_script_json({"identidad": refused}))
    return _render_account_template(
        request, "account.html", context=context, site_pass=True,
        theme_pass=True, demo_pass=True)


def render_reset_body(request: Request):
    return _render_account_template(
        request, "account-reset.html", site_pass=True, theme_pass=True,
        demo_pass=True)


def render_oauth_consent_body(request: Request, **context):
    """The assistant-connection consent (and its error screens) as a page of the site."""
    return _render_account_template(
        request, "oauth-consent.html", context=context, site_pass=True,
        theme_pass=True, demo_pass=True)


def render_test_auth_body(request: Request):
    return _render_account_template(
        request, "auth-test.html", site_pass=True, theme_pass=True,
        demo_pass=False)


def render_sso_body(request: Request):
    return _render_account_template(
        request, "auth-sso.html", site_pass=False, theme_pass=False,
        demo_pass=False)


def auth_redirect_target(current_user, next_path):
    """None when the caller should render its own page normally — there IS
    a session, or the site isn't actually running federated login right now
    (auth_renaper.metodos()['renaper'], the same anti-lockout-checked signal
    /auth/renaper itself falls back on when it isn't configured). Otherwise
    the absolute URL of a 302 straight into the RENAPER entry, `next_path`
    carried through auth_renaper._sanitizar_next.

    2026-08-03: patient_web/site_*.py's /my-requests and /request/<id>, and this
    module's own /account, used to ALWAYS render the account page first — a
    login/register form the visitor had to open before finding "Ingresar
    con RENAPER", which then still bounced client-side (account-form.js's
    ssoUrl/openIdentityProvider, gated on the older accounts.py-only stub
    check and pointed at /auth/sso, not this module). Calling this before
    serving anything skips straight to the real entry, no intermediate
    paint, no client-side bounce — and the person lands back on
    `next_path`, not the account home, once they're signed in.
    Password-mode installations (metodos()['renaper'] False) are untouched:
    this always returns None for them, same screen as before."""
    if current_user:
        return None
    try:
        from app.cuentas import auth_renaper
        if not auth_renaper.metodos().get("renaper"):
            return None
    except Exception:  # noqa: BLE001 — never break the page over this
        return None
    from urllib.parse import urlencode
    return "/auth/renaper?" + urlencode({"next": next_path})


def account_bootstrap(request: Request, bootstrap_user=None):
    """Return the session-owned account projection for the native request.

    The account domain owns the intentional forward reference to the turnos
    appointment projection. Keeping it here lets the legacy page and its
    Jinja twin preserve the same database-unavailable fallback.
    """
    user = bootstrap_user if bootstrap_user is not None else current_user(request)
    if not user:
        return None
    try:
        from app.modules.turnos import booking_patient
        dni = _ids.dni(user.get("dni"))
        out = {
            "usuario": _user_json(user),
            "solicitudes": booking_patient.patient_rows(dni),
        }
        if portal_identity.patient_login_enabled():
            # The session is the portal's: our «Cerrar sesión» could not end it.
            out["acceso_portal"] = True
        if portal_identity.municipal_patient_login():
            # The gateway closes /mcp there: the account offers no assistant connection.
            out["mcp_cerrado"] = True
        return out
    except store.DbUnavailable:
        return None


# ---- Local identity-provider simulator ------------------------------------


# ---- GET /auth/sso — RENAPER-style SSO login screen (stub) -------------------
# Replicates the look of the provincial SSO (sso-renaper.gba.gob.ar): side
# wedge, card with the Registry's lockup, DNI + sex per the document, and
# "Iniciá Sesión". This is the stub's LOGIN face; /auth/test stays as the
# synthetic-identities admin. Only exists when _test_auth_enabled — once the
# agreement lands, the real flow redirects to the actual SSO and this page
# doesn't get served.


# ---- GET /account/reset (recovery-link page) ---------------------------------
