"""One-use password reset page for internal panel users."""

import html
import hashlib
import json
import threading
import time

from app.core import db, i18n
from app.core import db_operators
from app.core import db_auth


_REQUESTS = {}
_REQUEST_LOCK = threading.Lock()
_REQUEST_WINDOW = 900
_MAX_REQUEST_KEYS = 4096


def _allow_request(ip, identifier):
    """Bounded, process-local best-effort throttle, independent of login.

    Limit both source IP and identifier; never retain plaintext identifiers.
    The lock makes check-and-record atomic across native request threads.
    """
    keys = (("ip:" + str(ip), 5), ("id:" + hashlib.sha256(
        identifier.strip().lower().encode()).hexdigest(), 3))
    now = time.monotonic()
    with _REQUEST_LOCK:
        for key in list(_REQUESTS):
            _REQUESTS[key] = [t for t in _REQUESTS[key] if now - t < _REQUEST_WINDOW]
            if not _REQUESTS[key]:
                del _REQUESTS[key]
        if any(len(_REQUESTS.get(key, [])) >= limit for key, limit in keys):
            return False
        if len(_REQUESTS) + sum(key not in _REQUESTS for key, _ in keys) > _MAX_REQUEST_KEYS:
            return False
        for key, _ in keys:
            _REQUESTS.setdefault(key, []).append(now)
    return True


def request_response():
    return {"ok": True, "message": i18n.t_or("admin.reset.request_received",
        "Si la cuenta tiene un email registrado, recibirás un enlace para elegir una nueva contraseña.",
        i18n.active())}


def request_reset(identifier, ip):
    """Run after the neutral HTTP response, so mail latency cannot enumerate.

    Only active, unambiguous operator accounts are eligible. Recovery never
    revives disabled access and never changes the env-owner's credentials.
    """
    identifier = str(identifier or "").strip()
    if not identifier or len(identifier) > 254 or not _allow_request(ip, identifier):
        return
    try:
        item = (db_operators.operator_by_email(identifier) if "@" in identifier
                else db_operators.operator_get_public(identifier))
        if not item or not item.get("activo") or not item.get("email"):
            return
        from app.core.staff_access_email import send_access_link as _send_reset
        _send_reset(item, {})
    except Exception:  # DB/config/mail outage: keep the same public response
        return


def token_usuario(token):
    """El usuario del token vigente (sólo lectura); False cuando el token
    venció, ya se usó o no existe; None cuando la base no se puede leer — en
    ese caso la página muestra el formulario sin usuario y el POST aplica la
    validación de siempre (fail-open al comportamiento previo: una base caída
    nunca rompe la página de un enlace ya clickeado)."""
    try:
        return db_auth.admin_reset_token_username(token) or False
    except db.DbUnavailable:
        return None


def page_html(token, valido=True, usuario=None):
    """Bytes puros del HTML de GET /admin/reset-password para `token` — sin
    tocar ningún handler: el transporte nativo (password_reset_router.py)
    lo llama directo. `valido=False` (vencido, ya usado o desconocido)
    renderiza el estado vencido SIN formulario: no dejar escribir una
    contraseña que el POST iba a rechazar igual. `usuario` sale SIEMPRE de la
    fila del token (nunca de la query): un médico invitado con un enlace
    copiado no tiene email con el que entrar y necesita saber su usuario."""
    from app.core import theme
    from app.core import static_fingerprint
    def t(key, fallback):
        return html.escape(i18n.t_or("admin.reset." + key, fallback, i18n.active()))
    messages = json.dumps({
        "mismatch": i18n.t_or("admin.reset.mismatch", "Las contraseñas no coinciden.", i18n.active()),
        "failed": i18n.t_or("admin.reset.failed", "No se pudo guardar. Pedí un enlace nuevo.", i18n.active()),
        "saved": i18n.t_or("admin.reset.saved", "Contraseña guardada. Ir al panel", i18n.active()),
        "network": i18n.t_or("admin.reset.network", "Error de red. Reintentá.", i18n.active()),
    }, ensure_ascii=True).replace("<", "\\u003c")
    safe = html.escape(token, quote=True)
    tid = theme.current()
    brand = html.escape(theme.brand_name(tid))
    if valido is False:
        inner = f"""<h1>{t("expired_title", "El enlace venció")}</h1>
<p>{t("expired_body", "Este enlace venció o ya fue usado. Solicitá uno nuevo desde el acceso al panel.")}</p>
<a class="btn btn-ok btn-lg" href="/admin/login">{t("login", "Acceso al panel")}</a>"""
        title = f"{t('expired_title', 'El enlace venció')} · {brand}"
    else:
        # The username, when the token row names one: the line the doctor
        # needs to log in afterwards, plus a username field so a password
        # manager saves the pair.
        user_line = user_field = ""
        if usuario:
            u = html.escape(str(usuario), quote=True)
            user_line = f'\n<p class="reset-user">{t("username", "Tu usuario")}: <strong>{u}</strong></p>'
            user_field = f'\n<input type="text" name="username" autocomplete="username" value="{u}" readonly hidden>'
        inner = f"""<h1>{t("title", "Elegí una nueva contraseña")}</h1>{user_line}
<p>{t("requirements", "Usá al menos 8 caracteres. Este enlace se puede usar una sola vez.")}</p>
<form id="reset-form" method="post">
<input type="hidden" id="reset-token" value="{safe}">{user_field}
<label for="reset-pass">{t("password", "Nueva contraseña")}</label>
<input id="reset-pass" name="password" type="password" minlength="8" autocomplete="new-password" required>
<label for="reset-confirm">{t("confirm", "Repetir contraseña")}</label>
<input id="reset-confirm" name="confirmation" type="password" minlength="8" autocomplete="new-password" required>
<button class="btn btn-ok btn-lg" type="submit">{t("save", "Guardar contraseña")}</button>
</form><p class="reset-msg" id="reset-msg" role="status"></p>"""
        title = f"{t('password', 'Nueva contraseña')} · {brand}"
    # The admin login's own primary button (admin-gate.css .btn-ok.btn-lg) in
    # the active theme's colors and typeface, so the page reads as its sibling.
    body = f"""<!doctype html>
<html lang="{html.escape(i18n.active(), quote=True)}" data-theme="{html.escape(tid, quote=True)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
{theme.fonts(tid)}
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<link rel="stylesheet" href="{static_fingerprint.url('admin-gate.css')}">
<style>
body{{background:#f6f7f5;margin:0;color:#20231f;font-size:16px;text-wrap:pretty}}
.reset{{width:min(100% - 32px,460px);margin:10vh auto;background:#fff;border:1px solid #dfe3dd;border-radius:16px;padding:28px;box-sizing:border-box}}
.reset h1{{font-size:28px;margin:0 0 10px}} .reset p{{line-height:1.5;color:#5b625b}}
.reset .reset-user{{color:#20231f;font-size:18px}}
.reset label{{display:block;font-weight:650;margin:22px 0 7px}}
.reset input{{width:100%;box-sizing:border-box;font:inherit;padding:12px;border:1px solid #b9c0b8;border-radius:8px}}
.reset .btn{{margin-top:20px;width:100%;justify-content:center;box-sizing:border-box}} .reset-msg{{min-height:24px;margin-top:14px}}
</style></head><body>
<main class="reset">{inner}</main>
<script>
document.getElementById('reset-form')&&document.getElementById('reset-form').addEventListener('submit',async function(e){{
 e.preventDefault(); const msg=document.getElementById('reset-msg');
 const messages={messages}; const btn=this.querySelector('button');
 if(btn.disabled)return;
 const password=document.getElementById('reset-pass').value;
 if(password!==document.getElementById('reset-confirm').value){{msg.textContent=messages.mismatch;return;}}
 btn.disabled=true;
 try{{
 const res=await fetch('/api/admin/reset-password',{{method:'POST',headers:{{'Content-Type':'application/json'}},
 body:JSON.stringify({{token:document.getElementById('reset-token').value,password:password}})}});
 const data=await res.json().catch(()=>({{}}));
 if(!res.ok){{msg.textContent=data.error||messages.failed;return;}}
 document.getElementById('reset-form').hidden=true;
 const destination=['/admin','/admin/login?staff=1','/admin/login'].includes(data.redirect)?data.redirect:'/admin/login';
 const seguir=document.createElement('a');seguir.href=destination;seguir.className='btn btn-ok btn-lg';
 seguir.textContent=messages.saved;
 msg.replaceChildren(seguir);
 if(destination!=='/admin/login')window.location.replace(destination);
 }}catch(e){{msg.textContent=messages.network;}}finally{{btn.disabled=false;}}
}});
</script></body></html>""".encode()
    return body


class ResetError(Exception):
    """`status` + `payload` que el router nativo traduce a una respuesta, para
    cada rama de error."""

    def __init__(self, status, payload):
        super().__init__(payload)
        self.status = status
        self.payload = payload


def reset_password(token, password, headers=None, client_host=""):
    """Consume the link atomically, then hand its password proof to login.

    Invitation and recovery share one token purpose; neither bypasses TOTP.
    A lost/repeated response never reuses a consumed link to create a session.
    """
    from app.auth import admin_auth, staff_portal
    if staff_portal.enabled():
        raise ResetError(404, {"error": "no_encontrado"})
    if len(password) < 8:
        raise ResetError(
            400, {"error": i18n.t_or("admin.srv.users.password_corta", "La contraseña tiene que tener al menos 8 caracteres.", i18n.active())})
    try:
        usuario = db_auth.admin_reset_token_consume(token, password)
    except db.DbUnavailable:
        from app.core import deps
        raise deps.answer_db_unavailable(lambda status: ResetError(status, {"error": i18n.t_or(
            "admin.reset.db_unavailable", "Base de datos no disponible.", i18n.active())}))
    if not usuario:
        raise ResetError(
            400, {"error": i18n.t_or("admin.reset.expired", "El enlace venció o ya fue usado. Pedí uno nuevo.", i18n.active())})
    return admin_auth.password_link_login(headers, client_host, usuario, password)
