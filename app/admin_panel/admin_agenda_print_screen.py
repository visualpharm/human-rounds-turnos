"""admin_agenda_print_screen.py — `GET /admin/cola/print`: one doctor's day on an A4 sheet.

The sheet HSI prints as «Turnos programados» (header, one block per agenda
window, rows by time), printed from the panel: `?dia=YYYY-MM-DD` (today when
absent) and `?medico=<id>` (a doctor's own session ignores it and prints its
own day). The data and who may print what live in
`modules/turnos/agenda_print.py`; this file only answers.

The page stands alone — no admin shell, no menu — so what reaches the paper
is the sheet. `?auto=1` opens the browser's print dialog once the page has
loaded (the panel's «Imprimir» links use it).

Gate: the queue's own (`require_admin_screen(permitir_medico=True,
permitir_admisionista=True)`); the path inherits the `cola` permission from
the `/admin/cola` prefix in `core/permissions._PREFIXES`.
"""

from fastapi import APIRouter, Depends, Query, Response
from starlette.concurrency import run_in_threadpool

from app.core import access_log, db, deps, i18n, jinja_env
from app.modules.turnos import agenda_print

api_router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
_SCREEN = Depends(deps.require_admin_screen(permitir_medico=True, permitir_admisionista=True))
_TEMPLATE = "admin/agenda-print.html"

_NOTICES = {
    "dia_invalido": ("admin.queue.print.error.bad_day", "La fecha no es válida."),
    "falta_medico": ("admin.queue.print.error.pick_doctor", "Elegí un médico para imprimir su agenda."),
    "medico_desconocido": ("admin.queue.print.error.unknown_doctor", "No encontramos a ese médico."),
    "cuenta_sin_medico": ("admin.queue.print.error.unlinked", "Tu cuenta todavía no está vinculada a un médico."),
    "sin_servicio": ("admin.queue.print.error.unavailable", "No pudimos armar la agenda. Probá de nuevo en un rato."),
}


def _html(body):
    return Response(body.encode("utf-8"), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


def _page(**context):
    lang = i18n.active()
    return i18n.inject_html_lang(jinja_env.render(_TEMPLATE, lang=lang, **context), lang)


def _notice(status, code):
    key, fallback = _NOTICES[code]
    response = _html(_page(sheet=None, notice=i18n.t_or(key, fallback, i18n.active())))
    response.status_code = status
    return response


@api_router.get("/admin/cola/print")
async def agenda_print_page(
        identity: dict = _SCREEN,
        dia: str | None = Query(None, description="The day to print (`YYYY-MM-DD`, today when absent); one "
                                                  "that does not parse is a 400 page"),
        medico: str | None = Query(None, description="Only this doctor's patients; a doctor's session is its own"),
        auto: str | None = Query(None, description="`1`: open the print dialog on load",
                                 json_schema_extra={"enum": ["1"]})):
    dia = agenda_print.parse_day(dia)
    if dia is None:
        return _notice(400, "dia_invalido")
    try:
        sheet = await run_in_threadpool(agenda_print.sheet_data, identity, dia, medico)
    except agenda_print.PrintError as exc:
        return _notice(exc.status, exc.code)
    except (db.DbUnavailable, access_log.AccessLogUnavailable):
        return deps.answer_db_unavailable(lambda status: _notice(status, "sin_servicio"))
    return _html(_page(sheet=sheet, notice=None, auto=auto == "1"))
