"""intake_admin_router.py — native FastAPI routes for intake administration
(family W2b of docs/roadmap/native-migration-campaign-20260826.md).

All seven rows call the pure domain functions (`settings_payload`/
`settings_post_payload`/`my_settings_payload`/`my_settings_post_payload`/
`alerts_count_payload`/`alerts_list_payload`/
`alert_ack_scope_check`+`alert_ack_payload`/`guard_medico_or_404`/
`regenerate_payload`/`get_or_create_door_token`).

Staff auth via `modules.intake._native_support.gate()` — a MANUAL (not
`Depends`-default) call to `core.deps.require_admin(**flags)`, the same
role flags `admin_auth.require_admin` takes. Called manually, not as a `Depends`
default, because `door/{code}`'s `?lang=` switch must answer even for an
unauthenticated visitor — FastAPI resolves `Depends()` defaults before the
endpoint body runs at all, so a `Depends`-based gate would silently reorder
the auth check ahead of `_maybe_lang_switch`, a real behavior change.

  GET/POST /api/admin/intake/settings
  GET/POST /api/admin/intake/my-settings
  GET      /api/admin/intake/alerts/count
  GET      /api/admin/intake/alerts
  POST     /api/admin/intake/alerts/{aid}/ack
  GET      /api/admin/intake/door/{code}             (poster; Jinja-rendered)
  POST     /api/admin/intake/door/{code}/regenerate

`{aid}` is FastAPI's loosest path converter (any non-slash string), but the
legacy inner regex only ever matched digits (`_ALERT_ACK_RE`) — a non-numeric
`aid` declines with the campaign's shared terminal-404 contract (migration
brief step 3), same convention `modules/turnos/appointment_confirm_router.py`
already established for its own "FastAPI matched, inner regex declines" case."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import ai_register, api_body, db, deps, i18n, jinja_env, native_gate, site_identity
from app.core.site_identity_router import _maybe_lang_switch
from app.modules.intake import api_models, intake_admin
from app.patient_web import catalog
from app.modules.intake._native_support import (
    ApiError, gate, json_response, terminal_not_found_response,
)

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("intake"))])

_STAFF_FLAGS = dict(permitir_medico=True)


def _esc(value):
    # Markup: una sola pasada de escape — con el autoescape del entorno Jinja
    # (2026-09-27) un str plano se entity-escaparía dos veces.
    import html
    from markupsafe import Markup
    return Markup(html.escape("" if value is None else str(value), quote=True))


def _render_poster(doc, url, qr_svg, matricula=None):
    especialidad = _esc(doc.get("especialidad"))
    matricula = _esc(matricula)
    from markupsafe import Markup
    # qr_svg: SVG generado por el servidor (segno) — markup de confianza que
    # el template inyecta tal cual; Markup para que autoescape no lo rompa.
    return jinja_env.render(
        "modules/intake/door_poster.html", lang=i18n.active(),
        nombre=_esc(doc.get("nombre")), especialidad=especialidad, matricula=matricula,
        url=_esc(url), qr_svg=Markup(qr_svg) if qr_svg else "",
    ).rstrip("\n")


async def _settings(
        request: Request, identity: dict = Depends(native_gate.staff(solo_admin=True)),
        body: api_body.Body = Depends(api_body.body(
            api_models.SETTINGS, api_models.IntakeSettingsIn, methods={"POST"}))):
    try:
        if request.method == "GET":
            payload = await run_in_threadpool(intake_admin.settings_payload)
        else:
            with ai_register.actor(identity):
                payload = await api_body.run(
                    body, intake_admin.settings_post_payload, body.value,
                    expected=(intake_admin.IntakeAdminError,))
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _my_settings(
        request: Request, identity: dict = Depends(native_gate.staff(**_STAFF_FLAGS)),
        body: api_body.Body = Depends(api_body.body(
            api_models.SETTINGS, api_models.IntakeMySettingsIn, methods={"POST"}))):
    try:
        if request.method == "GET":
            payload = await run_in_threadpool(intake_admin.my_settings_payload, identity)
        else:
            with ai_register.actor(identity):
                payload = await api_body.run(
                    body, intake_admin.my_settings_post_payload, identity, body.value,
                    expected=(intake_admin.IntakeAdminError,))
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _alerts_count(request: Request):
    try:
        identity = await gate(request, **_STAFF_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        payload = await run_in_threadpool(intake_admin.alerts_count_payload, identity)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _alerts_list(request: Request):
    try:
        identity = await gate(request, **_STAFF_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        payload = await run_in_threadpool(intake_admin.alerts_list_payload, identity)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _alert_ack(request: Request, aid: str):
    if not aid.isdigit():
        # `_ALERT_ACK_RE` (`intake_admin.py`) only ever matched digits —
        # FastAPI's `{aid}` accepts any non-slash string, so a non-numeric
        # value is the "matched, inner regex declines" case (migration brief
        # step 3): the shared terminal 404, not a domain-level response.
        # Checked BEFORE auth: `_ALERT_ACK_RE` never reaches
        # `admin_auth.require_admin` for a shape it doesn't recognize, so an
        # unauthenticated request with a malformed `aid` still 404s, never
        # 401s.
        return terminal_not_found_response()
    iid = int(aid)
    try:
        identity = await gate(request, **_STAFF_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        await run_in_threadpool(intake_admin.alert_ack_scope_check, identity, iid)
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    try:
        payload = await run_in_threadpool(intake_admin.alert_ack_payload, identity, iid)
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _door_endpoint(request: Request, code: str, fmt: str | None = Query(
        None, alias="format", description="`pdf`: the poster as a PDF to print (only while the interview is "
        "on for the doctor) instead of the HTML page", json_schema_extra={"enum": ["pdf"]})):
    switch = await _maybe_lang_switch(request)
    if switch is not None:
        return switch

    try:
        identity = await gate(request, **_STAFF_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        await run_in_threadpool(intake_admin.guard_medico_or_404, identity, code)
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)

    try:
        token, doc = await run_in_threadpool(
            intake_admin.get_or_create_door_token, code,
            require_active=fmt == "pdf")
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    if doc is None:
        return json_response({"error": "no encontrado"}, status=404)

    url = f"{site_identity.base_url()}/e/{token}"
    if fmt == "pdf":
        if not await run_in_threadpool(intake_admin.enabled_for, doc):
            return json_response({"error": "no encontrado"}, status=404)
        try:
            pdf = await run_in_threadpool(intake_admin.door_poster_pdf, doc, url)
        except intake_admin.IntakeAdminError as exc:
            return json_response(exc.body, exc.status)
        filename = "cartel-entrevista-previa.pdf"
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="%s"' % filename})

    qr_svg = await run_in_threadpool(intake_admin._qr_svg, url)
    matricula = await run_in_threadpool(catalog.license_number, doc)
    body = _render_poster(doc, url, qr_svg, matricula)
    return Response(content=body.encode("utf-8"), media_type="text/html; charset=utf-8")


async def _door_regenerate(request: Request, code: str):
    try:
        identity = await gate(request, **_STAFF_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        await run_in_threadpool(intake_admin.guard_medico_or_404, identity, code)
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    try:
        payload = await run_in_threadpool(intake_admin.regenerate_payload, code)
    except intake_admin.IntakeAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


router.add_api_route("/api/admin/intake/settings", _settings, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.IntakeSettingsOut),
                                                 post=(api_models.IntakeSettingsIn, api_models.IntakeSettingsSavedOut))
                     | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/intake/my-settings", _my_settings, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.IntakeMySettingsOut),
                                                 post=(api_models.IntakeMySettingsIn, api_body.OkOut))
                     | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/intake/alerts/count", _alerts_count, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.RedAlertCountOut)) | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/intake/alerts", _alerts_list, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.RedAlertsOut)) | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/intake/alerts/{aid}/ack", _alert_ack, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_body.OkOut)) | deps.STAFF_GATE_DOCS)
router.add_api_route(
    "/api/admin/intake/door/{code}", _door_endpoint, methods=["GET"],
    openapi_extra=api_body.query_docs(get={
        "lang": api_body.LANG_SWITCH,
    }) | deps.STAFF_GATE_DOCS)
router.add_api_route(
    "/api/admin/intake/door/{code}/regenerate", _door_regenerate, methods=["POST"],
    openapi_extra=api_body.docs(post=(None, api_models.DoorLinkOut)) | deps.STAFF_GATE_DOCS)
