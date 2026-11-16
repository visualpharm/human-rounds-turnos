"""avatar_thumb_router.py — FastAPI endpoint for `avatar_thumb.py`.

  GET /doctor-thumb/{name}.webp   name = <doctor id>.<16-hex content hash>

A malformed name gets the shared terminal 404. A stale hash is answered with
the current thumbnail and a short cache, never a 404: pages already open in
a browser still reference the URL they were served."""

from fastapi import APIRouter, Depends, Response

from app.core import deps
from app.core.terminal_response import TerminalNotFound
from app.patient_web import avatar_thumb

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/doctor-thumb/{name}.webp")
async def get_doctor_thumb(name: str):
    r = avatar_thumb.thumb_bytes(name)
    if not r:
        raise TerminalNotFound()
    data, current = r
    cache = ("public, max-age=31536000, immutable" if current
             else "public, max-age=300")
    return Response(
        content=data, status_code=200, media_type=None,
        headers={"Content-Type": "image/webp",
                 "Content-Length": str(len(data)),
                 "Cache-Control": cache})
