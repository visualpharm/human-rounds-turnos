"""ocr_dni_router.py — native FastAPI twin of `ocr_dni.py` — the DNI-photo
OCR the registration form and the account-photo crop both call.
Native-route migration campaign F6
(docs/roadmap/native-migration-campaign-20260826.md): a genuine `async def`
endpoint that parses the request itself, calls `ocr_dni.py`'s extracted
`_process_core` (sync AI-call/store work through `run_in_threadpool`), and
builds the `Response` from the returned `cuentas.native_result.Result`.
This router is the only HTTP path to `_process_core`.

`ocr_dni` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router, same as before.

  POST /api/ocr-dni

No session required (this runs BEFORE registration) — Turnstile + fair-use
+ `ai_health.available()` all still run from inside `_process_core()`,
unchanged."""

from fastapi import APIRouter, Depends, Request

from app.core import api_body, deps
from app.cuentas import api_models, ocr_dni

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


async def _ocr_dni_endpoint(
        request: Request,
        # A body that does not decode answers the plain "JSON inválido" ocr_dni.py
        # has always used (no interpolated exception message, unlike accounts.py).
        body: api_body.Body = Depends(api_body.body(api_models.BODY_PLAIN, api_models.OcrDniIn))):
    client = request.client
    result = await api_body.run(
        body, ocr_dni._process_core,
        request.headers, client.host if client else "", body.value)
    return result.to_response()


router.add_api_route("/api/ocr-dni", _ocr_dni_endpoint, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.OcrDniIn, api_models.OcrDniOut)))
