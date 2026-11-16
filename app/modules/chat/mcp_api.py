"""mcp_api.py — servidor MCP: el asistente de IA del paciente puede buscar
servicios y pedir turnos en su nombre.

Protocolo: MCP streamable HTTP SIN estado (JSON-RPC 2.0 sobre POST /mcp; cada
request se responde con application/json — sin SSE, sin Mcp-Session-Id; GET y
DELETE /mcp responden 405, permitido por la spec para servidores stateless).
Sólo stdlib: el server entero es SimpleHTTPRequestHandler, acá no entra un SDK.

Auth: Bearer token por usuario (mismo patrón "el token ES la autenticación" que
el feed ICS de calendar_feed.py — pero éste puede ESCRIBIR, así que es un token
aparte y regenerarlo revoca el anterior). La base guarda sólo su SHA-256
(`users.mcp_token`) y sus últimos 4 caracteres (`users.mcp_token_hint`): una
copia de la base no entrega tokens que sirvan (2026-11-06). `initialize`,
`tools/list` y `ping` no exigen token (descubrimiento: no filtran nada);
`tools/call` sí — 401 + WWW-Authenticate sin token válido.

El paciente saca su token de Mi cuenta (el token en claro se ve UNA vez):
  GET  /api/account/mcp             {url, token, hint}  (sesión; sin token lo
                                    crea y lo devuelve; con token, token=null)
  POST /api/account/mcp/regenerate  {url, token, hint}  (rota y revoca el anterior)

Tools (nombres en inglés; todas actúan como el dueño del token — la identidad
NUNCA viene del body, mismo contrato que POST /api/requests). El REGISTRO de
tools (specs + implementación) vive en chat_tools.py — lo comparte con el
motor de chat multi-turno (chat_agent.py, widget del sitio + WhatsApp), así
que un cambio acá vale para las tres puertas:
  search_services         qué se atiende y cómo (turno / orden médica / sin turno)
  available_appointments  los horarios elegibles de un servicio (los del wizard)
  request_appointment     crea la solicitud — REUSA booking_requests._create vía un
                          shim (edad server-side, soft-lock 409, notificaciones:
                          exactamente las mismas reglas que el wizard web);
                          acepta orden_token (de analyze_order) para pedir un
                          servicio 'solicitud_con_orden' sin pasar por la web
  my_requests             las solicitudes del titular (y las que pidió por sus hijos)
  cancel_request          cancela una propia (reusa booking_requests._cancel)
  whoami                  la identidad del dueño del token (nombre, DNI, si tiene
                          contacto/email, personas a cargo) — nunca pass_hash/tokens
  analyze_order           lee una foto/PDF de orden médica con IA (mismo pipeline
                          que POST /api/analyze-order) y devuelve un orden_token;
                          sujeto a un enfriamiento escalonado por identidad
                          (ai_cooldown.py)

Un servicio `solicitud_con_orden` sólo se puede pedir por acá CON un
orden_token ya analizado (si no, la tool explica el porqué y el link para
subirla en la web); walk-in no reserva nunca (invariante del sitio).

Import-safe: db/patient_site/solicitudes/chat_tools se importan perezosamente o
son livianos. Base caída → la tool responde "no disponible", nunca tumba el
server.
"""

import json
import logging
import re
from typing import Mapping

from app.modules.chat import chat_tools, mcp_oauth
from app.auth import mcp_guard
from app.core import theme
from app.core import ids as _ids
from app.core import log_pii

PROTO_SOPORTADOS = ("2025-06-18", "2025-03-26", "2024-11-05")
PROTO_DEFAULT = "2025-06-18"


def _server_info():
    return {"name": "human-rounds", "title": theme.brand_name(),
            "version": "1.2.0"}


def _instructions():
    from app.core import site_identity
    network = str(site_identity.get("network_long") or
                  "la red pública de salud configurada")
    return (
        f"Turnos de {network}. Flujo: search_services para encontrar el servicio "
        "→ available_appointments para ver horarios → request_appointment para "
        "pedirlo. Todo se pide a nombre del dueño del token; la red confirma cada "
        "solicitud (estado 'pendiente' hasta que un operador la apruebe). Los "
        "estudios que requieren orden médica se piden adjuntando la imagen o el "
        "PDF a analyze_order y pasando el orden_token resultante a "
        "request_appointment."
    )

MAX_MCP_BODY_BYTES = 256 * 1024
_MAX_BODY = MAX_MCP_BODY_BYTES  # historical tests/import compatibility


from dataclasses import dataclass


@dataclass(frozen=True)
class McpResult:
    """Complete transport-neutral result returned by MCP domain functions."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


def declared_body_too_large(headers):
    """Return true only for a valid declared size above the MCP limit."""
    try:
        return int(headers.get("Content-Length") or 0) > _MAX_BODY
    except (TypeError, ValueError):
        return False


class _LegacyHits:
    """Sólo conserva `_HITS.clear()` para la suite histórica."""

    @staticmethod
    def clear():
        mcp_guard._reset_for_tests()


_HITS = _LegacyHits()


def _env(key, default=""):
    from app.core import config
    return config.get(key, default)


def _permitido(token):
    return mcp_guard.allow_call_only(token)


# ---- helpers ------------------------------------------------------------------

def _usuario_por_token(token):
    """Dueño del Bearer token, o None. Indirección para los tests."""
    from app.core import db_accounts
    return db_accounts.user_by_mcp_token(token)


def _bearer(headers: Mapping[str, str]):
    h = headers.get("Authorization") or ""
    m = re.match(r"^\s*Bearer\s+(\S+)\s*$", h, re.I)
    return m.group(1) if m else None


def _json_result(obj, status=200, headers=None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    response_headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    response_headers.extend((str(k), str(v)) for k, v in (headers or {}).items())
    return McpResult(status=int(status), headers=tuple(response_headers), body=body)


def _rpc_result(rid, result):
    return _json_result({"jsonrpc": "2.0", "id": rid, "result": result})


def _rpc_error(rid, code, message, status=200, headers=None):
    return _json_result(
        {"jsonrpc": "2.0", "id": rid,
         "error": {"code": code, "message": message}},
        status=status,
        headers=headers,
    )


# A failure that is not the database: said as what it is, never «base de datos no disponible».
_INTERNAL_ERROR = "error interno del servidor de turnos"


def _internal_error(what, exc):
    logging.getLogger(__name__).error("%s: %s", what, log_pii.exc(exc))


TOOLS = chat_tools.TOOLS
_TOOL_FNS = chat_tools.TOOL_FNS


# ---- JSON-RPC -----------------------------------------------------------------

def _handle_rpc(headers: Mapping[str, str], msg):
    rid = msg.get("id")
    method = str(msg.get("method") or "")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    if not method:
        return _rpc_error(rid, -32600, "falta method", status=400)

    # notificaciones (sin respuesta): 202 y listo
    if method.startswith("notifications/"):
        return McpResult(status=202, headers=(("Content-Length", "0"),), body=b"")

    if method == "initialize":
        pv = str(params.get("protocolVersion") or "")
        return _rpc_result(rid, {
            "protocolVersion": pv if pv in PROTO_SOPORTADOS else PROTO_DEFAULT,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": _server_info(),
            "instructions": _instructions(),
        })

    if method == "ping":
        return _rpc_result(rid, {})

    if method == "tools/list":
        # Only what a call can actually use: the guard's table is the contract
        # (my_lab_results / search_doctors stay refused there, so not offered).
        # Each tool says it needs the OAuth sign-in: that is what makes ChatGPT offer «Conectar».
        return _rpc_result(rid, {"tools": [dict(t, securitySchemes=[{"type": "oauth2"}])
                                           for t in TOOLS if t.get("name") in mcp_guard.WEIGHTS]})

    if method == "tools/call":
        token = _bearer(headers)
        usuario = None
        if token:
            from app.core import deps
            try:
                usuario = _usuario_por_token(token)
            except deps.DB_UNAVAILABLE_ERRORS:
                return deps.answer_db_unavailable(lambda status: _rpc_error(
                    rid, -32000, "servicio no disponible, probá en un rato", status=status))
            except Exception as exc:  # noqa: BLE001 — not the database: its own error, no db_unavailable
                _internal_error("the MCP token could not be read", exc)
                return _rpc_error(rid, -32603, _INTERNAL_ERROR, status=500)
        if not usuario:
            return _json_result(
                {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32001,
                           "message": "token inválido o ausente: el paciente lo saca de Mi cuenta en la web"}},
                status=401,
                # resource_metadata: where an OAuth client (ChatGPT) starts the sign-in.
                headers={"WWW-Authenticate": mcp_oauth.www_authenticate(chat_tools._base_url())},
            )
        name = str(params.get("name") or "")
        fn = _TOOL_FNS.get(name)
        if not fn:
            return _rpc_error(rid, -32602, f"tool desconocida: {name}")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        ok, reason, retry_after, ai_lease = mcp_guard.admit(token, name, args)
        if not ok:
            if retry_after == 0:
                return _rpc_result(rid, {
                    "content": [{"type": "text", "text": json.dumps(
                        {"error": reason}, ensure_ascii=False)}],
                    "isError": True,
                })
            return _rpc_error(
                rid, -32002, reason, status=429,
                headers={"Retry-After": str(retry_after)},
            )
        invocation = chat_tools.ToolInvocation(mcp_ai_guarded=True)
        try:
            try:
                data = fn(usuario, args, invocation)
            except Exception as e:  # noqa: BLE001 — una tool rota no tumba el conector
                try:
                    from app.core import db
                    if isinstance(e, db.DbUnavailable):
                        data = {"error": "base de datos no disponible, probá en un rato"}
                    else:
                        data = {"error": _INTERNAL_ERROR}
                except Exception:  # noqa: BLE001
                    data = {"error": _INTERNAL_ERROR}
        finally:
            if ai_lease:
                mcp_guard.release_ai()
        data = absolute_hrefs(data)
        return _rpc_result(rid, {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": bool(isinstance(data, dict) and data.get("error")),
        })

    return _rpc_error(rid, -32601, f"método desconocido: {method}")


def absolute_hrefs(value):
    """MCP runs outside the site; every nested destination needs an origin."""
    if isinstance(value, list):
        return [absolute_hrefs(item) for item in value]
    if isinstance(value, dict):
        base = chat_tools._base_url().rstrip('/')
        return {key: (base + item if isinstance(item, str) and item.startswith('/')
                      and not item.startswith('//') and (key == 'href' or key.endswith('_href') or key == 'url' or key.endswith('_url'))
                     else absolute_hrefs(item)) for key, item in value.items()}
    return value


def process_rpc_body(headers: Mapping[str, str], raw: bytes) -> McpResult:
    """Parse one bounded JSON-RPC request into a typed result."""
    if len(raw) > _MAX_BODY:
        return reject_body_too_large()
    try:
        msg = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:  # noqa: BLE001
        return _rpc_error(None, -32700, "JSON inválido", status=400)
    if isinstance(msg, list):
        return _rpc_error(None, -32600, "batch no soportado", status=400)
    if not isinstance(msg, dict):
        return _rpc_error(
            None, -32600, "se esperaba un objeto JSON-RPC", status=400,
        )
    return _handle_rpc(headers, msg)


def reject_body_too_large() -> McpResult:
    """Return the shared 413 response after an incremental native read."""
    return _json_result({"error": "body demasiado grande"}, status=413)


def process_mcp_request(
    method: str, headers: Mapping[str, str], raw: bytes = b"",
) -> McpResult:
    """Apply method, declared-size, and JSON-RPC rules for ``/mcp``."""
    if method != "POST":
        return _json_result(
            {"error": "método no soportado (servidor MCP sin estado: sólo POST)"},
            status=405,
            headers={"Allow": "POST"},
        )
    if declared_body_too_large(headers):
        return reject_body_too_large()
    return process_rpc_body(headers, raw)


def connection_closed() -> bool:
    """True on an installation whose patients enter through the municipal portal (salud):
    the whole /mcp answers as an absent path (mcp_api_router), the account hands out no
    assistant token and OAuth is not offered. The gateway there closes /mcp too, but the
    application does not rely on it."""
    from app.cuentas import portal_identity
    return portal_identity.municipal_patient_login()


def process_account_request(usuario, base_url: str, regenerate: bool = False) -> McpResult:
    """Return the current session's MCP connection data."""
    if connection_closed():
        return _json_result({"error": "no_disponible"}, status=403)
    return _cuenta_mcp(usuario, base_url, regenerar=regenerate)


def process_account_revoke(usuario) -> McpResult:
    """Revoke the signed-in patient's MCP token (only their own)."""
    if not usuario:
        return _json_result({"error": "no_autenticado"}, status=401)
    from app.core import deps
    try:
        from app.core import db_accounts
        done = db_accounts.mcp_token_revoke(_ids.dni(usuario.get("dni")))
    except deps.DB_UNAVAILABLE_ERRORS:
        return deps.answer_db_unavailable(
            lambda status: _json_result({"error": "base de datos no disponible"}, status=status))
    except Exception as exc:  # noqa: BLE001
        _internal_error("the MCP token was not revoked", exc)
        return _json_result({"error": _INTERNAL_ERROR}, status=500)
    if not done:
        return _json_result({"error": "no_autenticado"}, status=401)
    return _json_result({"ok": True, "revoked": True})


def _cuenta_mcp(usuario, base_url: str, regenerar: bool) -> McpResult:
    if not usuario:
        return _json_result({"error": "no_autenticado"}, status=401)
    dni = _ids.dni(usuario.get("dni"))
    from app.core import deps
    try:
        from app.core import db_accounts
        if regenerar:
            token = db_accounts.mcp_token_regenerate(dni)
            issued = {"token": token, "hint": token[-4:], "last_used": None} if token else None
        else:
            issued = db_accounts.mcp_token_issue(dni)
    except deps.DB_UNAVAILABLE_ERRORS:
        return deps.answer_db_unavailable(
            lambda status: _json_result({"error": "base de datos no disponible"}, status=status))
    except Exception as exc:  # noqa: BLE001 — not the database: its own error, no db_unavailable
        _internal_error("the MCP token was not issued", exc)
        return _json_result({"error": _INTERNAL_ERROR}, status=500)
    if not issued:
        return _json_result({"error": "no_autenticado"}, status=401)
    return _json_result({
        "ok": True,
        "url": base_url.rstrip("/") + "/mcp",
        # The token in the clear only when it was just created; afterwards the
        # database holds its hash and the screen shows the last 4 characters.
        "token": issued["token"],
        "hint": issued["hint"],
        # When an assistant last used it (null: never); a token left 30 days
        # without use expires (2026-11-13).
        "last_used": issued.get("last_used"),
    })
