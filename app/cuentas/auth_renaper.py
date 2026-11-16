"""auth_renaper.py — login con la identidad digital nacional (RENAPER vía
AUTENTICAR / Mi Argentina, protocolo OpenID Connect).

Contexto (reunión con Johnny + administración, 2026-07-17): la municipalidad
quiere que el portal de salud se loguee "con Renaper o Mi Argentina" — la
autorización del trámite ante RENAPER les llegó el 2026-07-16 y en breve van a
tener credenciales de prueba. Este módulo es el scaffolding completo del flujo:
funciona HOY con `TURNOS_FAKE_RENAPER=1` (sin red) y queda listo para enchufar
las credenciales reales cuando lleguen. Detalle de plataformas y qué pedirle a
la municipalidad: docs/renaper-integration.md.

Métodos de autenticación (la "setting" que pidió el responsable del producto 2026-07-17): el sitio
puede ofrecer la contraseña propia, el login nacional, o LOS DOS a la vez.
  TURNOS_AUTH_METHODS = "password" | "renaper" | "password,renaper"
(default "password"). Regla dura anti-lockout: si la config deja al sitio sin
ningún método utilizable (p.ej. "renaper" sin credenciales cargadas), password
se prende solo — un typo en Coolify jamás puede dejar a los pacientes sin login.

Rutas (montadas por ``auth_renaper_router.py``):
  GET /auth/renaper           arranca el flujo: setea la cookie de estado
                              `st_oidc` (firmada, 10 min) y redirige al IDP.
                              ?next=/ruta/local = adónde volver al terminar.
  GET /auth/renaper/callback  vuelta del IDP (?code&state): valida el estado,
                              canjea el code por tokens (canal TLS directo al
                              IDP), saca los claims, crea-o-actualiza el
                              usuario por DNI y abre sesión st_ses (el MISMO
                              contrato de cookie de accounts.py).

El usuario creado por esta vía queda con pass_hash NULL — el verificador de
contraseñas rechaza NULL, así que esa cuenta no puede entrar por contraseña salvo que
después haga una recuperación y se fije una. Los claims crudos del IDP se
guardan en usuarios.renaper (jsonb) — ahí vamos a mirar si el token trae
domicilio/fecha de emisión para la regla municipal de "6 meses de residencia
en Pinamar" (pedido del administrador en la reunión; hoy NO se bloquea nada).

Seguridad:
  - state + nonce aleatorios firmados HMAC en cookie propia (stateless, mismo
    espíritu que la cookie st_admin de admin_auth.py); vence a los 10 minutos.
  - `next` sólo acepta rutas locales ("/algo", nunca "//host" ni "https://…").
  - El id_token llega por el canal TLS del token endpoint (flujo código de
    autorización server-side), así que su integridad la da el canal; los datos
    del paciente salen del UserInfo endpoint (también TLS directo). Cuando el
    IDP real publique su JWKS conviene sumar la verificación de firma como
    defensa en profundidad (TODO documentado en docs/renaper-integration.md).
  - Rate limit propio por IP (TURNOS_RENAPER_PER_HOUR, default 60) — bucket
    separado, mismo patrón que puco.py.

Env:
  TURNOS_AUTH_METHODS            métodos ofrecidos (ver arriba)
  TURNOS_RENAPER_CLIENT_ID       credenciales de la app registrada en el IDP
  TURNOS_RENAPER_CLIENT_SECRET
  TURNOS_RENAPER_AUTHORIZE_URL   default https://id.argentina.gob.ar/authorize/
  TURNOS_RENAPER_TOKEN_URL       default https://id.argentina.gob.ar/token/
  TURNOS_RENAPER_USERINFO_URL    default https://id.argentina.gob.ar/userinfo/
  TURNOS_RENAPER_SCOPE           default "openid profile"
  TURNOS_RENAPER_REDIRECT_URL    callback override (otherwise public origin)
  TURNOS_RENAPER_LABEL           texto del botón (default "Mi Argentina")
  TURNOS_RENAPER_PER_HOUR        rate limit por IP (default 60)
  TURNOS_FAKE_RENAPER=1          sin red: el flujo entero corre local y "vuelve"
                                 con una persona de prueba (TURNOS_FAKE_RENAPER_DNI
                                 pisa el DNI, default 99000001)

Import-safe: db se usa dentro de las funciones; si la base no está el callback
responde 503 como el resto del sitio. El import de este módulo NUNCA falla.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.parse
import urllib.request

from app.core import db
from app.core import db_accounts
from app.core import config
from app.core import format
from app.core import data_packs
from app.core import site_identity
from app.core import rate_window

STATE_COOKIE = "st_oidc"
STATE_TTL = 600  # 10 min para ir y volver del IDP

# The 3 RENAPER OIDC URL defaults below now come from packs/ar/endpoints.json
# ("renaper.authorize_url/token_url/userinfo_url") — TURNOS_RENAPER_* env
# vars still win over BOTH (see _env() below), unchanged (Phase 2c,
# 2026-07-30, see docs/architecture/data-packs.md). The literals here are
# only the last-resort fallback if packs/ is missing or mid-deploy.
_DEFAULTS = {
    "TURNOS_RENAPER_AUTHORIZE_URL": "https://id.argentina.gob.ar/authorize/",
    "TURNOS_RENAPER_TOKEN_URL": "https://id.argentina.gob.ar/token/",
    "TURNOS_RENAPER_USERINFO_URL": "https://id.argentina.gob.ar/userinfo/",
    "TURNOS_RENAPER_SCOPE": "openid profile",
    "TURNOS_RENAPER_LABEL": "Mi Argentina",
}


def _load_pack_endpoint_defaults():
    renaper = data_packs.value("endpoints.json", "renaper")
    if renaper is None:
        return
    for key, field in (("TURNOS_RENAPER_AUTHORIZE_URL", "authorize_url"),
                        ("TURNOS_RENAPER_TOKEN_URL", "token_url"),
                        ("TURNOS_RENAPER_USERINFO_URL", "userinfo_url")):
        v = renaper.get(field)
        if isinstance(v, str) and v:
            _DEFAULTS[key] = v


_load_pack_endpoint_defaults()


def _env(key, default=""):
    v = config.get(key)
    if v:
        return v
    return default or _DEFAULTS.get(key, "")


def _fake():
    return _env("TURNOS_FAKE_RENAPER") == "1"


def configurado():
    """¿Se puede arrancar un flujo de verdad (o de mentira)?"""
    if _fake():
        return True
    return bool(_env("TURNOS_RENAPER_CLIENT_ID") and _env("TURNOS_RENAPER_CLIENT_SECRET"))


def metodos():
    """Qué métodos de login ofrece el sitio: {"password": bool, "renaper": bool}.

    Anti-lockout: la combinación que deje al sitio sin NINGÚN método utilizable
    degrada a password-only (y el botón nacional desaparece hasta que haya
    credenciales) — nunca un 'nadie puede entrar' por una env mal cargada.
    """
    raw = _env("TURNOS_AUTH_METHODS") or "password"
    toks = {t.strip().lower() for t in raw.split(",") if t.strip()}
    password = "password" in toks
    renaper = "renaper" in toks and configurado()
    if not password and not renaper:
        password = True
    return {"password": password, "renaper": renaper}


def config_publico(handler=None):
    """Bloque `auth` de GET /api/account/config — lo que el cliente necesita
    para dibujar el login (qué métodos hay y cómo se llama el botón)."""
    m = metodos()
    out = {"password": m["password"], "renaper": m["renaper"]}
    if m["renaper"]:
        out["renaper_label"] = _env("TURNOS_RENAPER_LABEL")
    return out


# ---- rate limit propio (mismo patrón que puco.py, bucket separado) -----------

_HITS = {}
_HOUR = 3600


def _per_hour():
    try:
        v = _env("TURNOS_RENAPER_PER_HOUR")
        return int(v) if v else 60
    except ValueError:
        return 60


def _permitido(ip):
    return rate_window.allow(_HITS, ip, _per_hour(), _HOUR)


def _client_ip_from(forwarded_for, remote_addr):
    """Pure core of `_client_ip()` — a forwarded-for value plus whatever the
    transport resolved as the raw peer address. Called by
    `auth_renaper_router.py` with `request.client.host` as the peer (it
    used to be shared with the retired legacy handler, so the bucket key
    did not change at the cutover).

    KNOWN, BOUNDED residual (2026-08-08, Tier 4 readiness review): the first
    X-Forwarded-For value is client-suppliable (no CDN in front — deploy.md),
    so an abuser can rotate this RATE-LIMIT bucket key. It gates nothing but
    `_permitido`'s requests-per-hour bucket — never identity, which always
    comes from the signed OIDC state + provider round-trip. Keying on the
    verified peer instead would collapse every patient behind the same
    proxy/NAT into ONE bucket (all of Pinamar sharing 60/h), which is worse
    than the abuse it prevents. The real fix is trusted ingress (a CDN or
    proxy that overwrites the header); until then this stays as is — see
    the risk register in docs/roadmap/tier4-replatform-design.md."""
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return remote_addr if remote_addr is not None else "?"


# ---- estado firmado (cookie st_oidc) -----------------------------------------

_PROCESS_SECRET = secrets.token_bytes(32)  # fallback si no hay secreto estable


def _state_secret():
    for key in ("TURNOS_RENAPER_CLIENT_SECRET", "TURNOS_ADMIN_SECRET", "TURNOS_ADMIN_PASS"):
        v = _env(key)
        if v:
            return ("st-oidc|" + v).encode("utf-8")
    return _PROCESS_SECRET


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _firmar_estado(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_state_secret(), raw, hashlib.sha256).digest()
    return _b64e(raw) + "." + _b64e(sig)


def _leer_estado(cookie_val):
    """Devuelve el payload si la firma y la edad están bien, si no None."""
    try:
        raw_b64, sig_b64 = cookie_val.split(".", 1)
        raw = _b64d(raw_b64)
        sig = _b64d(sig_b64)
    except Exception:  # noqa: BLE001
        return None
    want = hmac.new(_state_secret(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, want):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        if time.time() - float(payload.get("t", 0)) > STATE_TTL:
            return None
    except (TypeError, ValueError):
        return None
    return payload


def _set_estado(valor):
    return ("%s=%s; Max-Age=%d; Path=/auth/renaper; HttpOnly; SameSite=Lax"
            % (STATE_COOKIE, valor, STATE_TTL))


def _clear_estado():
    return "%s=; Max-Age=0; Path=/auth/renaper; HttpOnly; SameSite=Lax" % STATE_COOKIE


# ---- armado de URLs -----------------------------------------------------------

def _sanitizar_next(v):
    """Sólo rutas locales: '/algo'. Nada de '//host', 'https://…' ni vacío."""
    v = str(v or "").strip()
    if not v.startswith("/") or v.startswith("//") or "\\" in v:
        return "/account"
    return v


def _redirect_uri(handler=None):
    """`handler` is accepted only for call-site symmetry with the rest of the
    module — `site_identity.base_url()` ignores its argument entirely (it
    reads `TURNOS_PUBLIC_ORIGIN`/the loopback dev origin, never a header), so
    this function is already transport-neutral and the native router calls
    it with no argument at all."""
    override = _env("TURNOS_RENAPER_REDIRECT_URL")
    if override:
        return override
    # The callback registered with the identity provider is an application
    # destination, never a reflection of a client-controlled Host header.
    return site_identity.base_url(handler) + "/auth/renaper/callback"


# ---- flujo --------------------------------------------------------------------

def _pagina_error_body(titulo, detalle):
    """Build the native error page bytes from the user-facing message."""
    from app.core import jinja_env
    return jinja_env.render("auth-renaper-error.html", titulo=titulo, detalle=detalle).encode("utf-8")


def start_flow_decision(qs, client_ip):
    """Pure core of `GET /auth/renaper`: query-string dict (the `parse_qs`
    shape — values are lists) + the caller's resolved client IP -> one of:

      ("login_only",)                    renaper is off -> back to password
      ("rate_limited",)                  per-IP budget exhausted
      ("start", state_cookie_value, url)  proceed: sign+return the state
                                          cookie VALUE (no Set-Cookie
                                          framing) and the IDP/fake-callback
                                          URL to redirect to

    `auth_renaper_router.py` renders this decision through native response
    primitives."""
    if not metodos()["renaper"]:
        return ("login_only",)
    if not _permitido(client_ip):
        return ("rate_limited",)
    payload = {
        "s": secrets.token_urlsafe(16),
        "n": secrets.token_urlsafe(16),
        "next": _sanitizar_next((qs.get("next") or [""])[0]),
        "t": int(time.time()),
    }
    cookie_value = _firmar_estado(payload)
    if _fake():
        # sin red: "volvemos" del IDP al toque, con el mismo state
        url = "/auth/renaper/callback?" + urllib.parse.urlencode(
            {"code": "fake", "state": payload["s"]})
        return ("start", cookie_value, url)
    params = {
        "client_id": _env("TURNOS_RENAPER_CLIENT_ID"),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": _env("TURNOS_RENAPER_SCOPE"),
        "state": payload["s"],
        "nonce": payload["n"],
    }
    url = _env("TURNOS_RENAPER_AUTHORIZE_URL")
    url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    return ("start", cookie_value, url)


def _post_form(url, data, headers=None, timeout=10):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode("utf-8"),
        headers=dict({"Content-Type": "application/x-www-form-urlencoded"},
                     **(headers or {})), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _jwt_payload(token):
    """Decodifica el payload de un JWT SIN verificar firma — ver docstring del
    módulo: la integridad la da el canal TLS del token endpoint; la firma se
    suma como defensa en profundidad cuando el IDP publique su JWKS."""
    try:
        return json.loads(_b64d(token.split(".")[1]))
    except Exception:  # noqa: BLE001
        return {}


def _claims_reales(code):
    tok = _post_form(_env("TURNOS_RENAPER_TOKEN_URL"), {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": _env("TURNOS_RENAPER_CLIENT_ID"),
        "client_secret": _env("TURNOS_RENAPER_CLIENT_SECRET"),
        "redirect_uri": _redirect_uri(),
    })
    claims = _jwt_payload(tok.get("id_token") or "")
    access = tok.get("access_token")
    if access:
        try:
            info = _post_form(_env("TURNOS_RENAPER_USERINFO_URL"), {},
                              headers={"Authorization": "Bearer " + access})
            if isinstance(info, dict):
                claims.update(info)
        except Exception:  # noqa: BLE001
            pass  # con el id_token alcanza si trae DNI
    return claims


def _claims_fake():
    return {
        "sub": "fake-renaper",
        "dni": _env("TURNOS_FAKE_RENAPER_DNI") or "99000001",
        "given_name": "Prueba",
        "family_name": "Renaper",
        "birthdate": "1980-01-01",
        "gender": "F",
        "email": "prueba.renaper@example.com",
        "nivel": 2,
        "fake": True,
    }


def _extraer_datos(claims):
    """Normaliza los claims del IDP a los campos de `usuarios`. El DNI es la
    clave (misma que HSI); si no viene directo se saca del CUIL (XX-DNI-D)."""
    dni = re.sub(r"\D", "", str(claims.get("dni")
                                or claims.get("nro_documento")
                                or claims.get("document_number") or ""))
    if not dni:
        cuil = re.sub(r"\D", "", str(claims.get("cuil") or claims.get("cuit") or ""))
        if len(cuil) == 11:
            dni = cuil[2:-1].lstrip("0")
    if not format.dni_valid_length(dni):
        return None
    sexo_raw = str(claims.get("gender") or claims.get("sexo") or "").strip().upper()
    sexo = {"F": "F", "FEMALE": "F", "FEMENINO": "F",
            "M": "M", "MALE": "M", "MASCULINO": "M"}.get(sexo_raw, "X" if sexo_raw else None)
    nac = str(claims.get("birthdate") or claims.get("fecha_nacimiento") or "") or None
    if nac and not re.match(r"^\d{4}-\d{2}-\d{2}$", nac):
        nac = None
    return {
        "dni": dni,
        "nombre": (claims.get("given_name") or claims.get("nombre")
                   or claims.get("first_name") or None),
        "apellido": (claims.get("family_name") or claims.get("apellido")
                     or claims.get("last_name") or None),
        "nacimiento": nac,
        "sexo": sexo,
        "email": claims.get("email") or None,
    }


def callback_decision(state_cookie_value, qs):
    """Pure-ish core of `GET /auth/renaper/callback`: the raw `st_oidc`
    cookie value (or None) plus the query-string dict -> one of:

      ("error", titulo, detalle)          render the shared error page
      ("success", session_token, next_path)  mint the session, clear the
                                           state cookie, redirect to `next`

    Touches only `db`/network (never a `handler`/`Request`) — the ONE thing
    still not a pure function is the real (non-fake) IDP round trip, which
    is a genuine network call by design. `auth_renaper_router.py` owns the
    native transport."""
    estado = _leer_estado(state_cookie_value) if state_cookie_value else None
    state_param = (qs.get("state") or [""])[0]
    if not estado or not state_param or not hmac.compare_digest(
            str(estado.get("s") or ""), state_param):
        return ("error", "No pudimos completar el ingreso",
                "El enlace venció o no es válido. Empezá de nuevo desde Ingresar.")
    if qs.get("error"):
        return ("error", "Ingreso cancelado",
                "No autorizaste el ingreso. Podés intentarlo de nuevo cuando quieras.")
    code = (qs.get("code") or [""])[0]
    if not code:
        return ("error", "No pudimos completar el ingreso",
                "Faltó el código de autorización. Empezá de nuevo desde Ingresar.")

    if _fake():
        claims = _claims_fake()
    else:
        try:
            claims = _claims_reales(code)
        except Exception:  # noqa: BLE001
            return ("error", "No pudimos hablar con Mi Argentina",
                    "El servicio nacional no respondió. Probá de nuevo en unos minutos "
                    "o ingresá con tu contraseña.")

    datos = _extraer_datos(claims)
    if not datos:
        return ("error", "No pudimos confirmar tu identidad",
                "La respuesta del servicio nacional no trajo tu DNI. "
                "Podés crear una cuenta con contraseña mientras tanto.")

    try:
        db_accounts.login_renaper(datos["dni"], datos, claims)
        token = db_accounts.create_session(datos["dni"])
    except db.DbUnavailable:
        return ("error", "Base de datos no disponible",
                "Probá de nuevo en unos minutos.")

    return ("success", token, _sanitizar_next(estado.get("next")))
