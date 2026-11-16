"""api_models.py — Pydantic models of the staff member's own JSON API: login
and passkeys (`admin_auth_router.py`, `passkeys_router.py`), the password
reset and the login panel's events, and the family answers to a body those
routes cannot read. They document the API and let `core.api_body` tell a
body the panel never sends from one it does; the domain still gets the body
as it came — see `core/api_body.py`.
"""

from pydantic import Field

from app.core import i18n
from app.core.api_body import BodyContract, BodyModel, ResponseModel
from app.core.http_json import gzip_json_response, json_response


def _auth_response(_request, payload, status):
    """The `AuthResult` answer of login and passkeys."""
    from app.auth import admin_auth, auth_response
    return auth_response.response(admin_auth.AuthResult(status, payload))


AUTH = BodyContract(message=lambda reason: f"JSON inválido: {reason}", respond=_auth_response)
"""Login and passkeys: «JSON inválido: <reason>», as `admin_auth`/`passkeys` answer it."""

RESET = BodyContract(
    message=lambda _reason: i18n.t_or("admin.reset.invalid_json", "JSON inválido.", i18n.active()),
    respond=lambda _request, payload, status: json_response(payload, status=status))
"""POST /api/admin/reset-password: the translated «JSON inválido.», no reason."""

PANEL_EVENT = BodyContract(message=lambda _reason: "JSON inválido", respond=gzip_json_response)
"""POST /api/login-panel/event: «JSON inválido», no reason."""


class StaffLoginIn(BodyModel):
    """`codigo`: the TOTP code when the account has 2FA."""
    usuario: str | None = None
    password: str | None = None
    codigo: str | None = None
    turnstile_token: str | None = None
    staff_pending: bool | None = None


class WebAuthnResponseIn(BodyModel):
    clientDataJSON: str | None = None
    attestationObject: str | None = None
    authenticatorData: str | None = None
    signature: str | None = None
    userHandle: str | None = None


class PasskeyRegisterIn(BodyModel):
    """The browser's `PublicKeyCredential` (base64url) plus the challenge the
    register-options call gave, and an optional name for the key."""
    challenge: str | None = None
    nombre: str | None = None
    id: str | None = None
    rawId: str | None = None
    type: str | None = None
    response: WebAuthnResponseIn | None = None


class PasskeyLoginIn(BodyModel):
    challenge: str | None = None
    id: str | None = None
    rawId: str | None = None
    type: str | None = None
    response: WebAuthnResponseIn | None = None


class PasskeyDeleteIn(BodyModel):
    credential_id: str | None = None


class PasskeyResetIn(BodyModel):
    """Another user's keys: admin only; without `usuario`, the caller's own."""
    usuario: str | None = None


class ResetPasswordIn(BodyModel):
    """`action` "request" asks for a link for `usuario`; otherwise `token` from
    the link and the new `password`."""
    action: str | None = None
    usuario: str | None = None
    token: str | None = None
    password: str | None = None


class PanelEventIn(BodyModel):
    sid: str | None = None
    variante: str | None = None
    evento: str | None = None
    tab: str | None = None
    item: str | None = None
    ms: int | None = None


class StaffLoginOut(ResponseModel):
    ok: bool
    usuario: str
    rol: str


class PasskeyOut(ResponseModel):
    ok: bool
    passkey: dict


class PasskeyResetOut(ResponseModel):
    ok: bool
    borradas: int


class StaffOkOut(ResponseModel):
    ok: bool


class StaffConfigOut(ResponseModel):
    """What the login form needs: the Turnstile site key (null or "" without
    one), whether the owner has 2FA, the owner's user name; `acceso_portal` (and
    the rest empty) on a portal-login installation."""
    turnstile_sitekey: str | None
    totp: bool
    usuario: str
    acceso_portal: bool | None = None


class WhoamiOut(ResponseModel):
    """The session's identity, its permissions and its 2FA state (as
    `admin_auth.account_security` gives it); `2fa_no_aplica` when the portal
    owns the second factor, `acceso_portal` on a portal-login installation."""
    usuario: str
    rol: str
    permisos: list[str]
    tiene_2fa: bool
    deadline_2fa: str | None = Field(default=None, alias="2fa_deadline")
    vencido_2fa: bool = Field(alias="2fa_vencido")
    no_aplica_2fa: bool | None = Field(default=None, alias="2fa_no_aplica")
    acceso_portal: bool | None = None


class PasskeyListOut(ResponseModel):
    """The passkeys of `usuario` (the caller, unless an admin asked for another)."""
    usuario: str
    passkeys: list[dict]


class PasskeyRegisterOptionsOut(ResponseModel):
    """WebAuthn `PublicKeyCredentialCreationOptions` (base64url) for a new key of the caller."""
    challenge: str
    rp: dict
    user: dict
    pubKeyCredParams: list[dict]
    excludeCredentials: list[dict]
    authenticatorSelection: dict
    timeout: int
    attestation: str


class PasskeyLoginOptionsOut(ResponseModel):
    """WebAuthn `PublicKeyCredentialRequestOptions` for a sign-in with a discoverable key."""
    challenge: str
    rpId: str
    allowCredentials: list[dict]
    userVerification: str
    timeout: int


class ResetPasswordOut(ResponseModel):
    """A link request: the neutral `message`. A new password: where to go
    next (`redirect`) and, when it opened a session, who (`usuario`, `rol`)."""
    ok: bool
    message: str | None = None
    redirect: str | None = None
    usuario: str | None = None
    rol: str | None = None


class LoginPanelOut(ResponseModel):
    """The login screen's side panel: the memes and the tips."""
    humor: list[dict]
    consejos: list[dict]
