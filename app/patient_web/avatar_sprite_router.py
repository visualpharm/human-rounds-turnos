"""avatar_sprite_router.py — Tier 4 slice 8 closing batch (docs/roadmap/
tier4-replatform-design.md's own honest inventory): the FastAPI `router`
twin of `avatar_sprite.py` — the content-addressed WebP atlas the catalog
cards' small round portraits share.

Family W2c of docs/roadmap/native-migration-campaign-20260826.md: a real
`async def` endpoint. It calls `avatar_sprite.atlas_bytes()`, a pure
function.

`avatar_sprite` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET /avatar-sprite/{hash}.webp

One path, content-addressed by the hash. FastAPI's `{h}` converter matches
ANY non-slash segment, wider than `avatar_sprite._HASH_RE`'s own 16-hex-char
group — this router re-validates `h` against the SAME regex before calling
`atlas_bytes()`, and a value that doesn't match gets the SHARED terminal
404 (`core.terminal_response.TerminalNotFound` -> `{"error": "not
found"}`)."""

from fastapi import APIRouter, Depends, Request, Response

from app.patient_web import avatar_sprite
from app.core import deps
from app.core.terminal_response import TerminalNotFound
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/avatar-sprite/{h}.webp")
async def get_avatar_sprite(h: str, request: Request):
    m = avatar_sprite._HASH_RE.match(f"/avatar-sprite/{h}.webp")
    if not m:
        # Shared terminal 404.
        raise TerminalNotFound()
    try:
        data = avatar_sprite.atlas_bytes(m.group(1))
    except avatar_sprite.AtlasNotFound:
        return _json_response(request, {"error": "sprite no encontrado"}, 404)
    return Response(
        content=data, status_code=200, media_type=None,
        headers={"Content-Type": "image/webp",
                 "Content-Length": str(len(data)),
                 "Cache-Control": "public, max-age=31536000, immutable"})
