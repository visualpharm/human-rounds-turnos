"""_native_support.py — shared native-router plumbing for modules/chat/
(family W2a of docs/roadmap/native-migration-campaign-20260826.md):
`chat_api_router.py` and `voice_router.py` each build a handful of small
JSON responses off pure domain functions extracted from
`chat_api.py`/`voice.py`. The one `json_response()` here is
`core.http_json.gzip_json_response` (the app's single native JSON response,
built on `core.gzip_bridge` via a `.headers.get(...)`-only duck type a real
FastAPI `Request` already satisfies)."""

from app.core.http_json import gzip_json_response as json_response  # noqa: F401 — the family imports it from here

