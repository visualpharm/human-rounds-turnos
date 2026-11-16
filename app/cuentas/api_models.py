"""api_models.py — Pydantic models of the patient account's JSON API
(`accounts_router.py`, `account_photo_router.py`).

What the account pages (account.js, the registration wizard) send and what
the routes answer. They document the API (`/openapi.json`, admin-only) and
let `core.api_body` tell a body the pages never send from one they do; the
domain functions (`accounts._*_core`) still get the decoded body as it came
and keep every answer they gave — see `core/api_body.py`.
"""

from app.core.api_body import BodyContract, BodyModel, OkOut, ResponseModel  # noqa: F401 — OkOut: the family's answers name it
from app.cuentas.native_result import Result

BODY = BodyContract(
    message=lambda reason: f"JSON inválido: {reason}",
    respond=lambda _request, payload, status: Result.json(payload, status=status).to_response())
"""The family's answer to a body it cannot read (`core.api_body`): the same 400
`{"error": "JSON inválido: <reason>"}` `Result` every account endpoint used to
build in its own `except`."""

BODY_PLAIN = BodyContract(
    message=lambda _reason: "JSON inválido",
    respond=lambda _request, payload, status: Result.json(payload, status=status).to_response())
"""POST /api/ocr-dni: the plain «JSON inválido», no reason."""


# ---- shared pieces -----------------------------------------------------------

class InsurerIn(BodyModel):
    nombre: str | None = None
    afiliado: str | None = None


class PrepaidIn(BodyModel):
    nombre: str | None = None
    afiliado: str | None = None
    plan: str | None = None


class CoverageIn(BodyModel):
    """Health coverage as the form sends it; the registry's own insurers never
    come from here (see `accounts._norm_coverage`)."""
    obras_sociales: list[InsurerIn] | None = None
    prepaga: PrepaidIn | str | None = None


class AddressIn(BodyModel):
    calle: str | None = None
    numero: str | None = None
    piso: str | None = None
    localidad: str | None = None
    provincia: str | None = None
    cp: str | None = None
    texto: str | None = None


class NoticesIn(BodyModel):
    whatsapp: bool | None = None
    email: bool | None = None


class ProfileIn(BodyModel):
    nombre: str | None = None
    apellido: str | None = None
    sexo: str | None = None
    nacimiento: str | None = None
    contacto: str | None = None
    email: str | None = None
    cobertura: CoverageIn | None = None
    domicilio: AddressIn | None = None


# ---- request bodies ----------------------------------------------------------

class RegisterIn(ProfileIn):
    """A Spanish sign-up needs the DNI; any other language may sign up with the
    email alone (a synthetic identity key is minted)."""
    dni: str | None = None
    password: str | None = None


class LoginIn(BodyModel):
    dni: str | None = None
    email: str | None = None
    password: str | None = None
    turnstile_token: str | None = None


class TestLoginIn(BodyModel):
    provider: str | None = None
    dni: str | None = None
    sexo: str | None = None


class RecoverIn(BodyModel):
    dni: str | None = None
    email: str | None = None


class ResetIn(BodyModel):
    token: str | None = None
    password: str | None = None


class ResetOtpIn(BodyModel):
    dni: str | None = None
    code: str | None = None
    password: str | None = None


class UpdateIn(ProfileIn):
    """Only the keys present are changed; `coverage_revision` must be the one
    `/api/account/me` gave when `cobertura` is sent (409 otherwise)."""
    dni: str | None = None
    avisos: NoticesIn | None = None
    lang: str | None = None
    coverage_revision: str | None = None


class DependentIn(BodyModel):
    dni: str | None = None
    nombre: str | None = None
    apellido: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None
    vinculo: str | None = None
    declaracion_jurada: bool | None = None


class DependentRefIn(BodyModel):
    dni: str | None = None


class TestIdentityIn(BodyModel):
    identidad: dict | None = None
    original_dni: str | None = None


class OtpSendIn(BodyModel):
    """`proposito`: "registro" or "recuperar"."""
    contacto: str | None = None
    proposito: str | None = None


class OtpVerifyIn(OtpSendIn):
    code: str | None = None


class PhotoIn(BodyModel):
    data_b64: str | None = None


class OcrDniIn(BodyModel):
    """A photo of the DNI as a data URL; `lado` "frente" (default) or "dorso"."""
    lado: str | None = None
    imageDataUrl: str | None = None
    turnstile_token: str | None = None


# ---- answers -----------------------------------------------------------------

class InsurerOut(ResponseModel):
    nombre: str
    origen: str | None = None
    afiliado: str | None = None
    rnos: str | None = None


class CoverageOut(ResponseModel):
    principal: str | None = None
    obras_sociales: list[InsurerOut] | None = None
    prepaga: dict | None = None


class AccountUser(ResponseModel):
    """The `users` row of the account, without the password hash; dates as ISO."""
    id: int
    dni: str
    nombre: str | None
    apellido: str | None
    nacimiento: str | None
    sexo: str | None
    contacto: str | None
    email: str | None
    created: str | None
    identity_kind: str | None
    activo: bool | None
    cobertura: CoverageOut | None
    domicilio: dict | None
    avisos: dict | None
    updated: str | None
    lang: str | None
    renaper: dict | None
    coverage_revision: str


class AccountOut(ResponseModel):
    ok: bool
    usuario: AccountUser
    auth_provider: str | None = None
    """The identity provider of a federated sign-in (the test sign-in: "renaper")."""


class Dependent(ResponseModel):
    id: int
    dni: str
    nombre: str | None
    apellido: str | None
    nacimiento: str | None
    sexo: str | None
    vinculo: str | None
    declaracion_jurada_at: str | None
    created: str | None
    updated: str | None
    identity_kind: str | None = None
    """"synthetic" when the dependent was added without a DNI (a minted key)."""


class DependentOut(ResponseModel):
    ok: bool
    dependiente: Dependent


class DependentsOut(ResponseModel):
    dependientes: list[Dependent]


class MeOut(ResponseModel):
    usuario: AccountUser


class MainCenterOut(ResponseModel):
    """The main center whose admission desk the screens name, and one way to reach it."""
    nombre: str
    direccion: str
    maps_url: str
    url: str
    telefono: str
    telefono_href: str


class AccountConfigOut(ResponseModel):
    """What the sign-in screens need: the Turnstile sitekey (null = no
    challenge), whether sign-up needs a verified phone, how patients sign in
    (`password`, `renaper` or `portal`) and where to ask for help. Only in
    `portal` mode: the portal's sign-in address and whether the request
    carried a portal identity."""
    turnstile_sitekey: str | None
    otp_registro_requerido: bool
    auth_mode: str
    renaper_provider: str
    test_identity_provider: bool
    ayuda_whatsapp: str
    ayuda_whatsapp_texto: str
    centro_principal: MainCenterOut | None
    portal_login_url: str | None = None
    portal_identidad: bool | None = None


class TestIdentityOut(ResponseModel):
    ok: bool
    identidad: dict


class TestIdentitiesOut(ResponseModel):
    """The synthetic identities of the test sign-in (never a copy of a real account)."""
    identidades: list[dict]


class OcrDniOut(ResponseModel):
    """What the photo of the document read (`patient`, or `domicilio` from the
    back) and where the document is in the photo (`bbox`, fractions)."""
    ok: bool
    motivo: str | None = None
    patient: dict | None = None
    domicilio: dict | None = None
    bbox: dict | None = None
