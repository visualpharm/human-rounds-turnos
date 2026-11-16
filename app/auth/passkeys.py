"""Admin passkeys verified by the maintained ``webauthn`` library.

Routes:
  POST /api/admin/passkeys/register-options
  POST /api/admin/passkeys/register
  GET  /api/admin/passkeys[?usuario=X]
  POST /api/admin/passkeys/delete
  POST /api/admin/passkeys/reset
  POST /api/admin/passkeys/login-options
  POST /api/admin/passkeys/login

Registration and authentication use py_webauthn's complete ceremonies. The
historical ES256/RS256 JSON row shape remains supported through
``auth.webauthn_adapter``; there is no custom CBOR, COSE, curve, DER, or
signature verifier. A missing or malformed library path therefore fails
closed. Password plus TOTP remains the recovery route.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Mapping

from app.auth import admin_auth
from app.core import b64url

_BASE = "/api/admin/passkeys"
_RETO_TTL = 300
_DATABASE_PURPOSE = {"registro": "registration", "login": "login"}


def _webauthn_adapter():
    """Load the optional passkey dependency only when a passkey is used.

    A missing package must disable this sign-in factor, not make password and
    TOTP recovery unavailable by preventing the admin application from booting.
    """
    from app.auth import webauthn_adapter
    return webauthn_adapter


def _base_no_disponible():
    """The refusal of a database that does not answer, through `core.deps.answer_db_unavailable`."""
    from app.core import deps
    return deps.answer_db_unavailable(lambda status: admin_auth.AuthResult(status, {"error": "base_no_disponible"}))


b64u = b64url.encode
b64u_dec = b64url.decode


def _clave():
    return admin_auth._signing_key()


def nuevo_reto(proposito: str, usuario: str = "") -> str:
    """Create a signed, short-lived ceremony challenge token."""
    payload = json.dumps({
        "r": b64u(secrets.token_bytes(16)), "p": proposito,
        "u": usuario or "", "exp": int(time.time()) + _RETO_TTL,
    }, separators=(",", ":")).encode("utf-8")
    encoded = b64u(payload)
    return encoded + "." + hmac.new(
        _clave(), encoded.encode("ascii"), hashlib.sha256).hexdigest()


def reto_wire(token: str) -> str:
    return b64u(token.encode("utf-8"))


def _challenge_digest(reto: str) -> bytes:
    """A database may retain only this irreversible challenge identifier."""
    return hashlib.sha256(reto.encode("utf-8")).digest()


def _reto_from_wire(wire: str) -> str | None:
    try:
        return b64u_dec(wire).decode("utf-8")
    except Exception:
        return None


def leer_reto(reto: str, proposito: str):
    """Validate a signed challenge; durable storage owns one-time use.

    This intentionally does *not* keep replay state in process memory.  A
    signed token is first validated here and then atomically consumed by
    ``core.db_auth.passkey_challenge_consume`` after WebAuthn verification.
    """
    try:
        encoded, supplied = str(reto or "").split(".", 1)
        expected = hmac.new(_clave(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        payload = json.loads(b64u_dec(encoded).decode("utf-8"))
    except Exception:
        return None
    now = int(time.time())
    if payload.get("p") != proposito or int(payload.get("exp") or 0) < now:
        return None
    return payload


def _validated_reto_wire(wire: str, proposito: str, usuario: str | None = None):
    """Return a signed challenge token only when its ceremony owner matches.

    Registration is bound to the currently authenticated account before any
    attestation is evaluated.  Login challenges deliberately have no owner:
    credential discovery happens after the browser response is received.
    """
    reto = _reto_from_wire(wire)
    payload = leer_reto(reto, proposito) if reto else None
    if not payload:
        return None
    owner = payload.get("u") or ""
    if usuario is not None:
        if not hmac.compare_digest(owner, usuario):
            return None
    elif owner:
        return None
    return reto


def _issue_reto(proposito: str, usuario: str = "") -> str:
    """Persist a digest-only, expiring challenge before returning it.

    The raw signed token is sent only to the browser.  It is never stored in
    PostgreSQL, so a database read cannot recover a usable challenge.
    """
    from app.core import db_auth
    database_purpose = _DATABASE_PURPOSE.get(proposito)
    if not database_purpose:
        raise ValueError("invalid passkey challenge purpose")
    reto = nuevo_reto(proposito, usuario)
    db_auth.passkey_challenge_issue(
        _challenge_digest(reto), database_purpose, usuario or None, _RETO_TTL,
    )
    return reto_wire(reto)


def _consume_reto(reto: str, proposito: str, usuario: str | None = None) -> bool:
    """Atomically claim a valid ceremony challenge across all ASGI workers."""
    from app.core import db_auth
    database_purpose = _DATABASE_PURPOSE.get(proposito)
    if not database_purpose:
        return False
    return db_auth.passkey_challenge_consume(
        _challenge_digest(reto), database_purpose, usuario,
    )


def rp_id_from_host(host_header: str | None) -> str:
    host = (host_header or "").split(",")[0].strip()
    return host.split(":")[0].lower()


def origen_from_headers(host_header: str | None, forwarded_proto_header: str | None) -> str:
    host = (host_header or "").split(",")[0].strip()
    proto = (forwarded_proto_header or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = "https" if ":" not in host or host.endswith(":443") else "http"
    return f"{proto}://{host}"


_IDENTIDAD_FLAGS = dict(permitir_medico=True, permitir_admisionista=True,
                        permitir_reportes=True, permitir_marca=True)


def identidad_gate(cookie_token, method, path):
    return admin_auth.evaluate_admin_gate(cookie_token, method, path, **_IDENTIDAD_FLAGS)


def _registration_credential(body: dict) -> dict:
    response = body.get("response") or {}
    return {
        "id": str(body.get("id") or ""),
        "rawId": str(body.get("rawId") or body.get("id") or ""),
        "type": str(body.get("type") or "public-key"),
        "response": {
            "clientDataJSON": str(response.get("clientDataJSON") or ""),
            "attestationObject": str(response.get("attestationObject") or ""),
        },
    }


def _authentication_credential(body: dict) -> dict:
    response = body.get("response") or {}
    return {
        "id": str(body.get("id") or ""),
        "rawId": str(body.get("rawId") or body.get("id") or ""),
        "type": str(body.get("type") or "public-key"),
        "response": {
            "clientDataJSON": str(response.get("clientDataJSON") or ""),
            "authenticatorData": str(response.get("authenticatorData") or ""),
            "signature": str(response.get("signature") or ""),
            "userHandle": response.get("userHandle"),
        },
    }


def _webauthn_error(exc: Exception, ceremony: str) -> str:
    """Keep the public error contract while verification stays library-owned."""
    detail = str(exc).lower()
    if "rp id" in detail:
        return "dominio_inesperado"
    if "user was not present" in detail or "user presence" in detail:
        return "la_llave_no_confirmo_presencia"
    if "sign count" in detail:
        return "llave_duplicada"
    if "signature" in detail:
        return "firma_invalida"
    return "respuesta_webauthn_invalida" if ceremony == "login" else "attestation_ilegible"


def register_options_result(cookie_token, method, path, host_header):
    gate = identidad_gate(cookie_token, method, path)
    if not gate.ok:
        return admin_auth.AuthResult(gate.status, gate.body)
    usuario = gate.identity["usuario"]
    from app.core import db, theme
    from app.core import db_auth
    try:
        existing = [key["credential_id"] for key in db_auth.passkeys_of(usuario)]
        challenge = _issue_reto("registro", usuario)
    except db.DbUnavailable:
        return _base_no_disponible()
    return admin_auth.AuthResult(200, {
        "challenge": challenge,
        "rp": {"id": rp_id_from_host(host_header), "name": theme.brand_name()},
        "user": {"id": b64u(usuario.encode("utf-8")), "name": usuario, "displayName": usuario},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}],
        "excludeCredentials": [{"type": "public-key", "id": item} for item in existing],
        "authenticatorSelection": {"residentKey": "preferred", "userVerification": "preferred"},
        "timeout": _RETO_TTL * 1000,
        "attestation": "none",
    })


def register_result(cookie_token, method, path, host_header, forwarded_proto_header, raw_body):
    gate = identidad_gate(cookie_token, method, path)
    if not gate.ok:
        return admin_auth.AuthResult(gate.status, gate.body)
    try:
        body = admin_auth.parse_json_body(raw_body)
    except Exception as exc:
        return admin_auth.AuthResult(400, {"error": f"JSON inválido: {exc}"})
    challenge_wire = str(body.get("challenge") or "")
    reto = _validated_reto_wire(challenge_wire, "registro", gate.identity["usuario"])
    if not reto:
        return admin_auth.AuthResult(400, {"error": "desafio_invalido"})
    try:
        adapter = _webauthn_adapter()
        verified = adapter.verify_registration(
            credential=_registration_credential(body), expected_challenge=b64u_dec(challenge_wire),
            expected_rp_id=rp_id_from_host(host_header),
            expected_origin=origen_from_headers(host_header, forwarded_proto_header),
        )
        public_key = adapter.public_key_to_storage(verified.credential_public_key)
    except Exception as exc:
        return admin_auth.AuthResult(400, {"error": _webauthn_error(exc, "registration")})
    cid = b64u(verified.credential_id)
    nombre = str(body.get("nombre") or "").strip() or None
    from app.core import db
    from app.core import db_auth
    try:
        if not _consume_reto(reto, "registro", gate.identity["usuario"]):
            return admin_auth.AuthResult(400, {"error": "desafio_invalido"})
        row = db_auth.passkey_create(gate.identity["usuario"], cid, public_key, verified.sign_count, nombre)
    except db.DbUnavailable:
        return _base_no_disponible()
    except Exception:
        return admin_auth.AuthResult(409, {"error": "esa_llave_ya_estaba"})
    return admin_auth.AuthResult(200, {"ok": True, "passkey": row})


def list_result(cookie_token, method, path, usuario_pedido):
    gate = identidad_gate(cookie_token, method, path)
    if not gate.ok:
        return admin_auth.AuthResult(gate.status, gate.body)
    identidad = gate.identity
    pedido = (usuario_pedido or "").strip()
    if pedido and pedido != identidad["usuario"] and identidad.get("rol") != "admin":
        return admin_auth.AuthResult(403, {"error": "solo_admin"})
    from app.core import db
    from app.core import db_auth
    try:
        return admin_auth.AuthResult(200, {"usuario": pedido or identidad["usuario"],
                                           "passkeys": db_auth.passkeys_of(pedido or identidad["usuario"])})
    except db.DbUnavailable:
        return _base_no_disponible()


def delete_result(cookie_token, method, path, raw_body):
    gate = identidad_gate(cookie_token, method, path)
    if not gate.ok:
        return admin_auth.AuthResult(gate.status, gate.body)
    try:
        cid = str(admin_auth.parse_json_body(raw_body).get("credential_id") or "")
    except Exception as exc:
        return admin_auth.AuthResult(400, {"error": f"JSON inválido: {exc}"})
    if not cid:
        return admin_auth.AuthResult(400, {"error": "falta_credential_id"})
    from app.core import db
    from app.core import db_auth
    try:
        deleted = db_auth.passkey_delete(cid, None if gate.identity.get("rol") == "admin" else gate.identity["usuario"])
    except db.DbUnavailable:
        return _base_no_disponible()
    return admin_auth.AuthResult(200, {"ok": True}) if deleted else admin_auth.AuthResult(404, {"error": "no_encontrada"})


def reset_result(cookie_token, method, path, raw_body):
    gate = identidad_gate(cookie_token, method, path)
    if not gate.ok:
        return admin_auth.AuthResult(gate.status, gate.body)
    try:
        usuario = str(admin_auth.parse_json_body(raw_body).get("usuario") or "").strip() or gate.identity["usuario"]
    except Exception as exc:
        return admin_auth.AuthResult(400, {"error": f"JSON inválido: {exc}"})
    if usuario != gate.identity["usuario"] and gate.identity.get("rol") != "admin":
        return admin_auth.AuthResult(403, {"error": "solo_admin"})
    from app.core import db
    from app.core import db_auth
    try:
        return admin_auth.AuthResult(200, {"ok": True, "borradas": db_auth.passkeys_delete_of(usuario)})
    except db.DbUnavailable:
        return _base_no_disponible()


def login_options_result(host_header):
    from app.core import db
    if admin_auth.staff_portal.enabled():
        return admin_auth.AuthResult(403, {"error": "acceso_por_portal"})
    try:
        challenge = _issue_reto("login")
    except db.DbUnavailable:
        return _base_no_disponible()
    return admin_auth.AuthResult(200, {
        "challenge": challenge, "rpId": rp_id_from_host(host_header),
        "allowCredentials": [], "userVerification": "preferred", "timeout": _RETO_TTL * 1000,
    })


def _rol_de(usuario):
    if usuario == admin_auth._admin_user():
        return "admin"
    from app.core import db_operators
    row = db_operators.operator_get(usuario)
    return (row.get("rol") or None) if row and row.get("activo") else None


def login_result(headers: Mapping[str, str] | None, client_host: str, raw_body: bytes):
    headers = headers or {}
    if admin_auth.staff_portal.enabled():
        # A passkey would mint a native session, which a portal installation
        # does not accept anyway; refuse before touching any challenge.
        return admin_auth.AuthResult(403, {"error": "acceso_por_portal"})
    ip = admin_auth.turnstile.client_ip_from(headers, client_host)
    if admin_auth._throttled(ip):
        return admin_auth.AuthResult(429, {"error": "demasiados_intentos"})
    try:
        body = admin_auth.parse_json_body(raw_body)
    except Exception as exc:
        return admin_auth.AuthResult(400, {"error": f"JSON inválido: {exc}"})
    challenge_wire = str(body.get("challenge") or "")
    reto = _validated_reto_wire(challenge_wire, "login")
    if not reto:
        admin_auth._record_attempt(ip)
        return admin_auth.AuthResult(400, {"error": "desafio_invalido"})
    cid = str(body.get("id") or "")
    from app.core import db
    from app.core import db_auth
    try:
        row = db_auth.passkey_by_credential(cid)
    except db.DbUnavailable:
        return _base_no_disponible()
    if not row:
        admin_auth._record_attempt(ip)
        return admin_auth.AuthResult(401, {"error": "llave_desconocida"})
    try:
        verified = _webauthn_adapter().verify_authentication(
            credential=_authentication_credential(body), expected_challenge=b64u_dec(challenge_wire),
            expected_rp_id=rp_id_from_host(headers.get("Host")),
            expected_origin=origen_from_headers(headers.get("Host"), headers.get("X-Forwarded-Proto")),
            public_key=row["public_key"], current_sign_count=row.get("sign_count") or 0,
        )
    except Exception as exc:
        admin_auth._record_attempt(ip)
        return admin_auth.AuthResult(401, {"error": _webauthn_error(exc, "login")})
    try:
        if not _consume_reto(reto, "login"):
            admin_auth._record_attempt(ip)
            return admin_auth.AuthResult(400, {"error": "desafio_invalido"})
    except db.DbUnavailable:
        return _base_no_disponible()
    try:
        rol = _rol_de(row["usuario"])
    except Exception:
        rol = None
    if not rol:
        return admin_auth.AuthResult(403, {"error": "cuenta_inactiva"})
    from app.core import deps
    try:
        new_count = int(verified.new_sign_count or 0)
        persisted = db_auth.passkey_mark_use(cid, new_count if new_count > 0 else None)
    # A nonzero counter is clone detection state.  Issuing a session when
    # it cannot be advanced would make a later replay indistinguishable.
    except deps.DB_UNAVAILABLE_ERRORS:
        return _base_no_disponible()
    except Exception as exc:  # noqa: BLE001 — not the database: its own error, no db_unavailable
        from app.core import log_pii
        logging.getLogger(__name__).error("the passkey counter was not advanced: %s", log_pii.exc(exc))
        return admin_auth.AuthResult(500, {"error": "contador_no_guardado"})
    if new_count > 0 and not persisted:
        admin_auth._record_attempt(ip)
        return admin_auth.AuthResult(401, {"error": "llave_duplicada"})
    admin_auth.marcar_acceso(row["usuario"])
    admin_auth._clear_attempts(ip)
    token = admin_auth._mint_token(row["usuario"], rol)
    return admin_auth.AuthResult(200, {"ok": True, "usuario": row["usuario"], "rol": rol},
                                 set_cookie=admin_auth._set_cookie_from(headers, token))
