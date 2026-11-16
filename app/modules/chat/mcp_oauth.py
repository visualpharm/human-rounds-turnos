"""mcp_oauth.py — OAuth 2.1 for the MCP server (2026-11-13).

ChatGPT (and other hosted assistants) cannot present a pasted Bearer token: they only speak
OAuth 2.1 (authorization code + PKCE, dynamic client registration) or no auth at all. This is
the smallest correct OAuth on top of the patient account:

  GET  /.well-known/oauth-protected-resource       RFC 9728: where the MCP server's tokens come from
  GET  /.well-known/oauth-authorization-server     RFC 8414: the endpoints below
  POST /oauth/register                             RFC 7591: the assistant registers its redirect URI
  GET  /oauth/authorize                            the patient (signed in) sees the consent screen
  POST /oauth/authorize                            «Permitir» / «Cancelar»
  POST /oauth/token                                code + PKCE verifier -> access token

The client id, the consent and the authorization code are HMAC-signed, short-lived blobs (the key is
derived from the installation's signing key, with its own domain). The one thing stored is that a consent
or a code was USED (a hash row in `alert_claims`), so single use holds across workers and restarts.
The access token IS the patient's MCP token (`users.mcp_token`, SHA-256 only): the same lookup,
the same 30-days-without-use expiry, and «Desconectar el asistente» / regenerate revoke it. One
connection per patient, as before: connecting an assistant replaces the previous token.
A consent blob is bound to the patient's DNI, so a forged cross-site POST cannot approve anything.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

CLIENT_TTL = 3600 * 24 * 365
CONSENT_TTL = 600
CODE_TTL = 60

# Hosts an assistant may redirect back to. A redirect URI outside this list is refused at
# registration, so the code can only ever travel to a known assistant.
_REDIRECT_HOSTS = ("chatgpt.com", "chat.openai.com", "openai.com", "claude.ai", "claude.com")
# Native clients return to their own machine over plain http (RFC 8252 §7.3); the code never leaves it.
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
SCOPE = "mcp"
# RFC 7636 §4.1/4.2: a verifier and an S256 challenge are base64url text, 43 to 128 characters.
_PKCE_TEXT = re.compile(r"[A-Za-z0-9_-]{43,128}")


def _key(purpose: str) -> bytes:
    from app.auth import admin_auth
    return hashlib.sha256(b"mcp-oauth:" + purpose.encode() + b":" + admin_auth._signing_key()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(purpose: str, payload: dict, ttl: int) -> str:
    body = dict(payload, exp=int(time.time()) + ttl, n=secrets.token_urlsafe(6))
    encoded = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    mac = hmac.new(_key(purpose), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return encoded + "." + mac


def verify(purpose: str, token: str) -> dict | None:
    try:
        encoded, supplied = str(token or "").split(".", 1)
        expected = hmac.new(_key(purpose), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        payload = json.loads(_unb64(encoded).decode("utf-8"))
    except Exception:  # noqa: BLE001 — a malformed blob is just «not valid»
        return None
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


# ---- discovery ----------------------------------------------------------------------

def protected_resource_metadata(base_url: str) -> dict:
    base = base_url.rstrip("/")
    return {"resource": base + "/mcp", "authorization_servers": [base],
            "bearer_methods_supported": ["header"], "scopes_supported": [SCOPE]}


def authorization_server_metadata(base_url: str) -> dict:
    base = base_url.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SCOPE],
    }


def www_authenticate(base_url: str) -> str:
    return ('Bearer realm="sana-turnos", resource_metadata="%s/.well-known/oauth-protected-resource"'
            % base_url.rstrip("/"))


# ---- registration -------------------------------------------------------------------

def _redirect_ok(uri) -> bool:
    text = str(uri)
    # A backslash, space or control character makes browsers and parsers disagree on the host.
    if not text.isascii() or any(c == "\\" or c <= " " or c == "\x7f" for c in text):
        return False
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" or host in _LOOPBACK_HOSTS:
        # RFC 8252 loopback redirect of a native client (Codex CLI): http only, any port and path.
        try:
            port = parts.port
        except ValueError:
            return False
        netloc = ("[%s]" % host if ":" in host else host) + (":%d" % port if port is not None else "")
        return (parts.scheme == "http" and host in _LOOPBACK_HOSTS and not parts.fragment
                and not parts.username and parts.netloc.lower() == netloc)
    return (parts.scheme == "https" and not parts.fragment and not parts.username
            and parts.netloc.lower() == host and parts.path not in ("", "/")
            and any(host == h or host.endswith("." + h) for h in _REDIRECT_HOSTS))


def redirect_host(uri: str) -> str:
    return (urlsplit(str(uri)).hostname or "").lower()


def register(body) -> tuple[int, dict]:
    """RFC 7591: the client id is a signed blob carrying its redirect URIs."""
    uris = body.get("redirect_uris") if isinstance(body, dict) else None
    if not isinstance(uris, list) or not uris or len(uris) > 5 or not all(_redirect_ok(u) for u in uris):
        return 400, {"error": "invalid_redirect_uri",
                     "error_description": "redirect_uris must be https URLs of a known assistant"}
    name = str((body.get("client_name") or "Asistente"))[:80]
    return 201, {
        "client_id": sign("client", {"uris": uris, "name": name}, CLIENT_TTL),
        "client_name": name, "redirect_uris": uris,
        "grant_types": ["authorization_code"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": SCOPE,
    }


# ---- authorize ----------------------------------------------------------------------

def check_authorize(params) -> tuple[dict | None, str]:
    """Validate an authorization request. (request, "") or (None, reason). Errors found before
    the redirect URI is trusted are shown to the patient, never redirected."""
    client = verify("client", params.get("client_id"))
    if not client:
        return None, "client_id"
    redirect = params.get("redirect_uri") or ""
    if redirect not in client.get("uris", []):
        return None, "redirect_uri"
    if params.get("response_type") != "code":
        return None, "response_type"
    challenge = params.get("code_challenge") or ""
    if params.get("code_challenge_method") != "S256" or not _PKCE_TEXT.fullmatch(challenge):
        return None, "code_challenge"
    resource = params.get("resource") or ""
    try:
        wrong_resource = bool(resource) and urlsplit(resource).path.rstrip("/") != "/mcp"
    except ValueError:  # «http://[x»: not a URL at all
        wrong_resource = True
    if wrong_resource:
        return None, "resource"
    return {"client_id": params.get("client_id"), "redirect_uri": redirect,
            "state": params.get("state") or "", "code_challenge": challenge,
            "resource": params.get("resource") or "", "name": client.get("name") or "Asistente"}, ""


def consent_token(request: dict, dni: str) -> str:
    return sign("consent", dict(request, dni=dni), CONSENT_TTL)


def decide(consent: str, dni: str, allow: bool) -> str | None:
    """The redirect back to the assistant, or None when the consent blob is not this patient's."""
    req = verify("consent", consent)
    if not req or req.get("dni") != dni or not _once("consent", consent, req):
        return None
    query = {"state": req["state"]} if req.get("state") else {}
    if not allow:
        query["error"] = "access_denied"
    else:
        query["code"] = sign("code", {"dni": dni, "cid": req["client_id"], "ru": req["redirect_uri"],
                                      "cc": req["code_challenge"], "res": req.get("resource", "")}, CODE_TTL)
    sep = "&" if "?" in req["redirect_uri"] else "?"
    return req["redirect_uri"] + sep + urlencode(query)


# ---- token --------------------------------------------------------------------------

def _once(purpose: str, blob: str, payload: dict) -> bool:
    """True for the first caller that takes this blob, False for every later one, across processes and
    restarts: the claim is a row of `alert_claims` (unique per key and day; the key is the blob's digest and
    the day is the day of the blob's own expiry, so one blob always lands on one row). The blob itself is
    never stored. A database that cannot answer refuses (fail closed): a code must never be exchanged twice."""
    from app.core import db_analytics
    day = datetime.datetime.fromtimestamp(int(payload.get("exp") or 0), datetime.timezone.utc).date()
    key = "mcp-oauth:%s:%s" % (purpose, hashlib.sha256(str(blob).encode("utf-8")).hexdigest())
    try:
        return bool(db_analytics.claim_daily_alert(key, day))
    except Exception:  # noqa: BLE001
        return False


def _err(error: str, description: str = "", status: int = 400) -> tuple[int, dict]:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return status, body


def exchange(form, issue_token) -> tuple[int, dict]:
    """grant_type=authorization_code. `issue_token(dni)` returns a fresh MCP token (or None)."""
    if form.get("grant_type") != "authorization_code":
        return _err("unsupported_grant_type")
    code = verify("code", form.get("code"))
    if not code or code.get("cid") != form.get("client_id") or code.get("ru") != form.get("redirect_uri"):
        return _err("invalid_grant")
    verifier = str(form.get("code_verifier") or "")
    if not _PKCE_TEXT.fullmatch(verifier):
        return _err("invalid_grant", "PKCE verifier does not match")
    digest = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
    if not hmac.compare_digest(digest.encode("ascii"), str(code.get("cc") or "").encode("utf-8")):
        return _err("invalid_grant", "PKCE verifier does not match")
    if not _once("code", form.get("code"), code):
        return _err("invalid_grant", "code already used")
    token = issue_token(code["dni"])
    if not token:
        return _err("invalid_grant", "account unavailable")
    return 200, {"access_token": token, "token_type": "Bearer", "scope": SCOPE}
