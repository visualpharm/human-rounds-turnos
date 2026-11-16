"""Shared byte-exact responses for native ASGI terminal boundaries."""

from fastapi import HTTPException, Response


def json_not_found_response():
    """Return the canonical terminal no-store JSON 404 for route declines."""
    body = b'{"error": "not found"}'
    return Response(
        content=body,
        status_code=404,
        media_type=None,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
    )


def redirect_response(location):
    """The empty-body 302 a native router answers with: `Location` and a zero `Content-Length`."""
    return Response(
        content=b"",
        status_code=302,
        headers={"Location": location, "Content-Length": "0"},
    )


class TerminalNotFound(HTTPException):
    """Raise where a route (or one of its dependencies) must decline with
    the exact shared terminal 404 — the bytes the legacy dispatcher's
    fallthrough always produced for a path its inner regex rejected. A
    plain `return json_not_found_response()` works in an endpoint body;
    this exception exists for the places that can only RAISE (FastAPI
    dependencies, shared gates). `app.py` registers
    `render_terminal_not_found` for the byte-exact rendering; an
    HTTPException subclass so a composition that forgot the handler still
    degrades to a 404 (FastAPI's default detail shape), never a 500."""

    def __init__(self):
        super().__init__(status_code=404, detail={"error": "not found"})


def render_terminal_not_found(_request, _exc):
    return json_not_found_response()
