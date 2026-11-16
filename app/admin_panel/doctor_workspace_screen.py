"""Manifest-owned onboarding and own-schedule screens; no global redirect gate."""
from fastapi import APIRouter, Depends, Response
from starlette.concurrency import run_in_threadpool
from app.auth import admin_auth
from app.core import api_body, db, deps, screen_page
from app.core import db_operators
from app.admin_panel import api_models, doctor_workspace, admin_navigation

onboarding_router = APIRouter(dependencies=[Depends(deps.require_module_enabled('turnos'))])
schedules_router = APIRouter(dependencies=[Depends(deps.require_module_enabled('turnos'))])
api_router = APIRouter(dependencies=[Depends(deps.require_module_enabled('turnos'))])


async def _render(identity, onboarding):
    if identity.get('rol') not in ('admin', 'medico'):
        return Response(status_code=403)
    if onboarding and identity.get('rol') != 'medico':
        return Response(status_code=403)
    try:
        data = await run_in_threadpool(doctor_workspace.workspace_data, identity)
    except db.DbUnavailable:
        data = None
    return screen_page.admin_page(identity, 'admin/doctor-workspace.html', 'onboarding' if onboarding else 'my-schedules',
                                  rol=identity.get('rol'), onboarding=onboarding, data=data,
                                  normal_landing=admin_navigation.default_path_for_role(identity.get('rol')) or '/admin/account')


@onboarding_router.get('/admin/onboarding')
async def onboarding_screen(identity: dict = Depends(deps.require_admin_screen(permitir_medico=True))):
    return await _render(identity, True)


@schedules_router.get('/admin/my-schedules')
async def schedules_screen(identity: dict = Depends(deps.require_admin_screen(permitir_medico=True))):
    return await _render(identity, False)


@api_router.get('/api/admin/doctor-profile', openapi_extra=api_body.docs(get=(None, api_models.DoctorProfileOut)))
async def own_profile(identity: dict = Depends(deps.require_admin(permitir_medico=True))):
    """The session doctor's own profile for Cuenta; a medico only."""
    from app.admin_panel._native_support import json_response
    if identity.get('rol') != 'medico':
        return json_response({'error': 'solo_medico'}, 403)
    try:
        return json_response(await run_in_threadpool(doctor_workspace.own_profile, identity))
    except db.DbUnavailable:
        return deps.answer_db_unavailable()


async def _doctor_only(identity: dict = Depends(deps.require_admin(permitir_medico=True))):
    """The session's identity when it is a doctor's; anyone else gets 403 `solo_medico` before the body."""
    from app.admin_panel._native_support import json_response
    if identity.get('rol') != 'medico':
        raise deps.EarlyResponse(json_response({'error': 'solo_medico'}, 403))
    return identity


@api_router.post('/api/admin/doctor-profile', openapi_extra=api_body.docs(post=(api_models.DoctorProfileIn, api_models.DoctorProfileOut)))
async def save_own_profile(
        identity: dict = Depends(_doctor_only),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.DoctorProfileIn))):
    """Save name, specialty and phone of the session's OWN doctor — the body
    carries no id, so no other doctor's record is reachable from here."""
    from app.admin_panel._native_support import json_response
    from app.admin_panel import admin_content
    try:
        result = await api_body.run(body, doctor_workspace.save_own_profile, identity, body.value,
                                    expected=(admin_content.ContentError,))
    except admin_content.ContentError as exc:
        return json_response(exc.payload, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(result)


@api_router.post('/api/admin/onboarding/complete', openapi_extra=api_body.docs(post=(None, api_models.OnboardingOut)))
async def complete(identity: dict = Depends(deps.require_admin(permitir_medico=True))):
    from app.admin_panel._native_support import json_response
    if identity.get('rol') != 'medico':
        return json_response({'error': 'solo_medico'}, 403)
    try:
        missing = await run_in_threadpool(doctor_workspace.onboarding_missing, identity)
        if missing:
            return json_response({'error': doctor_workspace.missing_message(missing), 'faltan': missing}, 400)
        saved = await run_in_threadpool(db_operators.operator_complete_onboarding, identity['usuario'])
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    if not saved:
        return json_response({'error': 'cuenta_sin_medico'}, 400)
    # Lectura del estado 2FA tras completar el onboarding: sin segundo factor
    # exigido vigente (la política actual cuenta sólo el TOTP) el paso
    # siguiente es el propio paso de seguridad de la cuenta, no la cola. En
    # una instalación de portal sin nuestro TOTP el server ya marca
    # «2fa_no_aplica»: el portal es el segundo factor y el alta conserva el
    # destino normal del rol. La lectura nunca bloquea el alta ya persistida.
    security = await run_in_threadpool(admin_auth.account_security, identity)
    if not security.get('tiene_2fa') and not security.get('2fa_no_aplica'):
        return json_response({'ok': True, 'next': '/admin/account'})
    return json_response({'ok': True, 'next': admin_navigation.default_path_for_role('medico') or '/admin/account'})
