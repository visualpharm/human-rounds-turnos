"""Native Jinja twins for the token-gated prescription and study-order pages.

``printable.py`` owns the token lookup and pure document-building oracles.
This router owns the only HTTP transport and renders the print chrome through
Jinja.

``printable._consulta_para`` is a pure domain function; it runs through
Starlette's ``run_in_threadpool``.
"""

import html

from fastapi import APIRouter, Depends, Request, Response
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from app.core import deps, i18n, jinja_env
from app.core.site_identity_router import _maybe_lang_switch
from app.modules.intake import printable

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("intake"))])

_COPY_DEFAULTS = {
    "paciente": "Paciente", "farmacia": "Farmacia", "obra_social": "Obra social",
    "archivo": "Archivo", "original": "Original", "duplicado": "Duplicado",
}
_RECETA_COPIES = ("paciente", "farmacia", "obra_social", "archivo")
_ORDEN_COPIES = ("original", "duplicado")


def _esc(value):
    # Markup: el valor queda escapado EXACTAMENTE una vez (html.escape) y el
    # entorno Jinja con autoescape (2026-09-27) no le aplica una segunda
    # pasada — paridad byte a byte con los oráculos manuales de printable.py
    # que test_intake_printable_jinja.test_16 sigue exigiendo.
    return Markup(html.escape("" if value is None else str(value), quote=True))


def _context(payload, kind):
    payload = payload if isinstance(payload, dict) else {}
    medico = payload.get("medico") if isinstance(payload.get("medico"), dict) else {}
    paciente = payload.get("paciente") if isinstance(payload.get("paciente"), dict) else {}
    matricula = medico.get("matricula")
    return {
        "lang": i18n.active(),
        "borrador": payload.get("borrador") is True,
        "anulada": payload.get("anulada") is True,
        "copies": _RECETA_COPIES if kind == "receta" else _ORDEN_COPIES,
        "label_defaults": _COPY_DEFAULTS,
        "espacio": printable._espacio(payload),
        "titulo": _esc(payload.get("titulo")),
        "fecha": _esc(payload.get("fecha")),
        "medico_nombre": _esc(medico.get("nombre")),
        "medico_especialidad": _esc(medico.get("especialidad")),
        "medico_matricula": _esc(matricula),
        "paciente_nombre": _esc(paciente.get("nombre")),
        "paciente_dni": _esc(paciente.get("dni")),
        "lineas": [_esc(value) for value in payload.get("lineas", [])]
        if isinstance(payload.get("lineas"), list) else [],
        "estudios": [_esc(value) for value in payload.get("estudios", [])]
        if isinstance(payload.get("estudios"), list) else [],
        "diagnosticos": [_esc(value) for value in payload.get("diagnosticos", [])]
        if isinstance(payload.get("diagnosticos"), list) else [],
        "notas": _esc(payload.get("notas")) if payload.get("notas") else "",
        "pie": _esc(payload.get("pie")),
    }


def _render_printable(kind, payload, reemplazo=None):
    template = "modules/intake/print_receta.html" if kind == "receta" else "modules/intake/print_orden.html"
    context = _context(payload, kind)
    context["reemplazo_url"] = (reemplazo or {}).get("url") or ""
    context["reemplazo_fecha"] = _esc((reemplazo or {}).get("fecha"))
    return jinja_env.render(template, **context).rstrip("\n")


def _render_not_found():
    return jinja_env.render("modules/intake/print_not_found.html", lang=i18n.active()).rstrip("\n")


def _html_response(body, status=200):
    return Response(
        content=body.encode("utf-8"), status_code=status,
        media_type="text/html; charset=utf-8",
    )


def _make_endpoint(kind):
    async def _endpoint(request: Request, token: str):
        switch = await _maybe_lang_switch(request)
        if switch is not None:
            return switch
        row = await run_in_threadpool(printable._consulta_para, token, kind)
        if not row:
            return _html_response(_render_not_found(), status=404)
        payload = printable.payload_de(row, kind)
        # Reemplazada por un «Reemplazar receta y orden» (problems.md №1): el
        # link viejo NO se borra ni cambia de código (responsable del producto, 2026-10-23) — sigue
        # mostrando el texto emitido completo, con ANULADA en cada copia, un
        # aviso arriba con el link al documento nuevo y sin botón de imprimir.
        reemplazo = None
        if payload.get("anulada"):
            reemplazo = await run_in_threadpool(printable.reemplazo_de, row, kind)
        return _html_response(_render_printable(kind, payload, reemplazo))
    return _endpoint


router.add_api_route("/r/{token}", _make_endpoint("receta"), methods=["GET"])
router.add_api_route("/o/{token}", _make_endpoint("orden"), methods=["GET"])
