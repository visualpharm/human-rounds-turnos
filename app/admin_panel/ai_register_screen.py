"""Manifest-owned, admin-only product register and downloadable dossier."""
import json
from collections import Counter

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.admin_panel import api_models
from app.admin_panel import ai_screens_api_models as screen_models
from app.core import ai_register, api_body, db, deps, i18n, screen_page
from app.core import db_ai

router = APIRouter(dependencies=[Depends(deps.require_module_enabled('turnos'))])
api_router = APIRouter(dependencies=[Depends(deps.require_module_enabled('turnos'))])
_SCREEN = Depends(deps.require_admin_screen(solo_admin=True))
_ADMIN = Depends(deps.require_admin(solo_admin=True))


_SHARED = ('model', 'host', 'risk_level', 'runtime_state')


def _cell(row, key):
    return (row.get('provider') or {}).get('host') if key == 'host' else row.get(key)


def shared_values(rows):
    """The value most functions share in each column, said once above the
    table so a row shows only where it differs (the product owner, 02.11: what repeats on
    every row goes outside the brackets). The printable dossier keeps every
    row whole and gets none."""
    out = {}
    for key in _SHARED:
        counts = Counter(_cell(row, key) for row in rows)
        value, n = counts.most_common(1)[0] if counts else (None, 0)
        if value and n >= 2 and n * 2 > len(rows):
            out[key] = value
    if out:
        # A column whose every cell would be empty is not drawn: its header
        # over blank cells is a second statement of the line above the table.
        # A fallback model then reads under the function's name.
        differs = {key: any(_cell(row, key) != out[key] for row in rows) for key in out}
        hide = []
        if not (differs.get('model', True) or differs.get('host', True)):
            hide.append('model')
        hide += [key for key in ('risk_level', 'runtime_state') if key in out and not differs[key]]
        if hide:
            out['hide'] = tuple(hide)
    return out


def _day(value):
    from app.patient_web import site_schedule
    return site_schedule._day_month(value) if value else ''


def change_lines(changes):
    """Each change as a sentence: the function's name, what changed, who and
    when, in the panel's words — never the stored keys or an ISO date."""
    lang = i18n.active()
    function_key, kind_key = 'ai.function.', 'ai.register.kind.'
    out = []
    for change in changes:
        actor = change.get('actor') or ''
        out.append(dict(change,
                        function_name=i18n.t_or(function_key + str(change.get('function_id')),
                                                str(change.get('function_id') or ''), lang),
                        kind_text=i18n.t_or(kind_key + str(change.get('kind')),
                                            _KINDS.get(change.get('kind'), _KINDS['other']), lang),
                        actor_text=(i18n.t_or('ai.register.actor_system', 'detectado por el sistema', lang)
                                    if actor.startswith('system:') else actor),
                        day=_day(change.get('changed_at')),
                        notified_day=_day(change.get('notified_on'))))
    return out


_KINDS = {'model': 'cambio de modelo', 'provider': 'cambio de proveedor',
          'enabled': 'activación o desactivación', 'data_scope': 'cambio en los datos enviados',
          'functionality': 'función nueva o cambiada', 'prompt': 'cambio de instrucciones',
          'other': 'cambio'}


def render_register(identity, payload, printable=False):
    # «Aviso previo» shows the words the person reads before the AI runs —
    # never the catalog key or the developer's note on where it sits.
    rows = [dict(row, notice_text=ai_register.notice(row['notice']['key']))
            for row in ai_register.localized(payload['características'])]
    return screen_page.admin_html(identity, 'admin/ai-register.html', 'ai-register', namespaces=('admin', 'ai'),
                                  rows=rows, changes=change_lines(payload['modificaciones']),
                                  shared={} if printable else shared_values(rows),
                                  state=payload['estado_actual'], printable=printable)


async def _page(identity, printable=False):
    try:
        payload = await run_in_threadpool(ai_register.dossier)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(lambda status: Response(
            ai_register.notice('ai.register.unavailable'), status_code=status, headers={'Cache-Control': 'no-store'}))
    return Response(render_register(identity, payload, printable), media_type='text/html',
                    headers={'Cache-Control': 'no-store'})


@router.get('/admin/ai-register')
async def register_screen(identity: dict = _SCREEN):
    return await _page(identity)


@api_router.get('/admin/ai-register/print')
async def register_print(identity: dict = _SCREEN):
    return await _page(identity, True)


@api_router.get('/api/admin/ai-register', openapi_extra=api_body.docs(get=(None, screen_models.AiRegisterOut)))
@api_router.get('/api/admin/ai-register/export', openapi_extra=api_body.docs(get=(None, screen_models.AiRegisterOut)))
async def register_export(request: Request, identity: dict = _ADMIN):
    try:
        payload = await run_in_threadpool(ai_register.dossier)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(
            lambda status: Response(status_code=status, headers={'Cache-Control': 'no-store'}))
    headers = {'Cache-Control': 'no-store'}
    if request.url.path.endswith('/export'):
        headers['Content-Disposition'] = 'attachment; filename="ai-register.json"'
    return Response(json.dumps(payload, ensure_ascii=False, default=str), media_type='application/json', headers=headers)


@api_router.post('/api/admin/ai-register/notify',
                 openapi_extra=api_body.docs(post=(api_models.AiRegisterNotifyIn, api_body.OkOut)))
async def record_notification(request: Request, identity: dict = _ADMIN):
    try:
        body = await request.json()
        api_body.watch(request, body, api_models.AiRegisterNotifyIn)
        change_id = int(body['id'])
        recorded = await run_in_threadpool(db_ai.ai_register_notify, change_id,
                                           body['reference'], body['date'], identity['usuario'])
    except (ValueError, KeyError, TypeError):
        return Response(ai_register.notice('ai.register.invalid_notification'), status_code=400,
                        headers={'Cache-Control': 'no-store'})
    except db.DbUnavailable:
        return deps.answer_db_unavailable(
            lambda status: Response(status_code=status, headers={'Cache-Control': 'no-store'}))
    return Response(json.dumps({'ok': recorded}), status_code=200 if recorded else 409,
                    media_type='application/json', headers={'Cache-Control': 'no-store'})
