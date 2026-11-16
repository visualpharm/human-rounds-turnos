"""http_json.py — the one native JSON response of the app (2026-10-25).

Every native router used to carry its own `_json_response` (26 copies in 22 files, plus the
`_native_support` / `native_json` families): the same body — `json.dumps(obj, ensure_ascii=False)` in
UTF-8 —, `Content-Type: application/json; charset=utf-8`, `Cache-Control: no-store` and, for the ones
that take the request, the opportunistic gzip of `core.gzip_bridge` (`Content-Encoding` + `Vary`).
`tests/test_json_response_contract.py` pins status, headers and body of each former copy.

Deliberately NOT here: `modules/chat/chat_api_router._json_response`, whose `Content-Type` is plain
`application/json` (no charset) — a different wire contract, kept as it is.
"""

import json

from starlette.responses import Response

from app.core import gzip_bridge


def json_response(obj, status=200, background=None):
    """The body as it is, never compressed — for routes whose callers get no request to look at."""
    return _build(json.dumps(obj, ensure_ascii=False).encode("utf-8"), status, None, background)


def gzip_json_response(request, obj, status=200):
    """Same, compressed with `gzip_bridge` when the client accepts it and the body is big enough."""
    body, encoding = gzip_bridge.body_for(request, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    return _build(body, status, encoding, None)


def _build(body, status, encoding, background):
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Length": str(len(body)),
    }
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=status, media_type=None, headers=headers,
                    background=background)
