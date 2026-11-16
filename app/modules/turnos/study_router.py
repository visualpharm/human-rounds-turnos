"""Native public study routes; never use the staff authentication dependency."""
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core import api_body, deps, i18n, jinja_env, theme, db, security_headers_middleware
from app.modules.turnos import api_models, study, study_store


async def require_study():
    if not study.enabled():
        raise HTTPException(404, detail={"error": "not_found"})


router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos")), Depends(require_study)])
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}


async def body(request):
    # Use the same TLS-termination signal as the repository's cookie/CSP
    # boundary; the ASGI socket itself can be HTTP behind the demo proxy.
    scheme = "https" if security_headers_middleware._arrived_over_https(request.scope) else request.url.scheme
    expected_origin = scheme + "://" + request.url.netloc
    if request.headers.get("origin") and request.headers["origin"] != expected_origin:
        raise HTTPException(403, detail={"error": "origin"})
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, detail={"error": "body_too_large"})
    try:
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("object")
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(422, detail={"error": "invalid_body"}) from None


def study_body(model):
    """`body()` as a dependency of the route, the drift against `model` logged (`api_body.watched`)."""
    return api_body.watched(model, body)


async def call(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except LookupError:
        raise HTTPException(404, detail={"error": "not_found"}) from None
    except (ValueError, TypeError):
        raise HTTPException(422, detail={"error": "invalid_study_data"}) from None
    except db.DbUnavailable:
        raise deps.answer_db_unavailable(
            lambda status: HTTPException(status, detail={"error": "study_unavailable"})) from None


def page(template, **context):
    html = jinja_env.render(template, **context)
    html = i18n.inject_html_lang(html, i18n.active())
    html = i18n.inject_bootstrap(html, None, i18n.active(), namespaces=list(i18n.client_namespaces()) + ["study"])
    return HTMLResponse(theme.inject(html), headers=HEADERS)


@router.get("/study")
async def landing(role: str = Query("", description="Preselect this role on the form (one the study has)")):
    return page("study/landing.html", definition=study.definition(), retention_days=study.retention_days(),
                selected_role=role if role in study.ROLES else "")


@router.post("/api/study/session", openapi_extra=api_body.docs(
        post=(api_models.StudySessionIn, api_models.StudySessionOut, 201)))
async def create_session(request: Request,
        payload: dict = Depends(study_body(api_models.StudySessionIn))):
    if payload.get("consent") is not True or payload.get("role") not in study.ROLES:
        raise HTTPException(422, detail={"error": "consent_and_role_required"})
    token = await call(study_store.create_session, payload["role"], i18n.active(), True,
                       study.user_agent_class(request.headers.get("user-agent")))
    return JSONResponse({"url": "/study/s/" + token}, status_code=201, headers=HEADERS)


@router.get("/study/s/{token}")
async def runner(token: str):
    session = await call(study_store.session, token)
    if session is None:
        raise HTTPException(404, detail={"error": "not_found"})
    if session["finished_at"]:
        return page("study/thanks.html")
    data = study.definition(session["locale"])
    payload = {"token": token, "role": session["role"], "group": data["roles"][session["role"]],
               "final_questions": data["final_questions"], "completed": await call(study_store.completed, token)}
    _, binding = i18n.bind_lang(session["locale"])
    try:
        return page("study/runner.html", payload=payload)
    finally:
        i18n.unbind(binding)


@router.post("/api/study/s/{token}/event", openapi_extra=api_body.docs(
        post=(api_models.StudyEventsIn, api_models.StudyEventsOut)))
async def events(token: str, request: Request,
        payload: dict = Depends(study_body(api_models.StudyEventsIn))):
    count = await call(study_store.add_events, token, payload.get("events"))
    return JSONResponse({"accepted": count}, headers=HEADERS)


@router.post("/api/study/s/{token}/result", openapi_extra=api_body.docs(
        post=(api_models.StudyResultIn, api_models.StudyResultOut)))
async def result(token: str, request: Request,
        payload: dict = Depends(study_body(api_models.StudyResultIn))):
    saved = await call(study_store.save_result, token, payload)
    return JSONResponse(saved, headers=HEADERS)


@router.post("/api/study/s/{token}/finish", openapi_extra=api_body.docs(
        post=(api_models.StudyFinishIn, api_models.StudyFinishOut)))
async def finish(token: str, request: Request,
        payload: dict = Depends(study_body(api_models.StudyFinishIn))):
    await call(study_store.finish, token, payload.get("answers", {}))
    return JSONResponse({"finished": True}, headers=HEADERS)
