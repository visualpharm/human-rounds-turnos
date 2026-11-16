"""i18n_catalog_router.py — native transport for the JS catalog API.

`GET /api/i18n/<lang>.json` is `i18n.js`'s lazy fallback (every
server-rendered page inlines its catalog via `bootstrap_script()`; the
fetch covers fragments/harnesses that don't). Its only owner used to be
`core.i18n.handle()` on the retired legacy dispatcher — the native
composition never picked it up, so the fallback 404'd (found by the
final-wave test migration, `test_module_runtime`'s catalog pins). This
router owns the contract directly: the same lax suffix handling (a trailing ``.json`` is
stripped, anything else is looked up as-is), same 404 body for an unknown
language, same ETag/304 + ``Cache-Control: public, max-age=300`` caching,
same gzip behavior (`gzip_bridge`). The other half of the legacy
The ``?lang=`` switch redirect already lives natively
in the page routers' shared preludes (`i18n.lang_switch_redirect`).

Platform-core (i18n is not module-owned): mounted from
`routers/platform.py`'s ORDERED tuple, no module gate.
"""

import hashlib
import json

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import api_body, api_models, gzip_bridge
from app.core import i18n

router = APIRouter()


def _catalog_payload(lang):
    """Runs in the threadpool: validate + build the catalog bytes and ETag,
    or return None for an unknown language."""
    lang = (lang or "").strip().lower()
    if lang not in i18n.available_locales():
        return None
    data = dict(i18n._load_catalog(lang))
    data.update(i18n._runtime_client_overrides(lang))
    body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
    return body, etag


@router.get("/api/i18n/{rest:path}", openapi_extra=api_body.docs(get=(None, api_models.I18nCatalogOut)))
async def catalog(request: Request, rest: str):
    lang = rest[:-5] if rest.endswith(".json") else rest
    payload = await run_in_threadpool(_catalog_payload, lang)
    if payload is None:
        body = json.dumps({"error": "idioma desconocido"}, ensure_ascii=False).encode("utf-8")
        return Response(
            content=body, status_code=404,
            headers={"Content-Type": "application/json; charset=utf-8",
                     "Content-Length": str(len(body)),
                     "Cache-Control": "no-store"})
    body, etag = payload
    inm = (request.headers.get("If-None-Match") or "").strip()
    if inm == etag:
        return Response(
            content=b"", status_code=304,
            headers={"ETag": etag, "Cache-Control": "public, max-age=300"})
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "ETag": etag,
        "Cache-Control": "public, max-age=300",
    }
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=200, headers=headers)
