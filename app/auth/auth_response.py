"""auth_response.py — the one JSON response of an `admin_auth.AuthResult`.

`auth/admin_auth_router.py` and `auth/passkeys_router.py` each carried the same `_response`, and
`auth/api_models.py` reached into the first one to answer a body it could not read; the status, the
body (`json.dumps(..., ensure_ascii=False)` in UTF-8), `Cache-Control: no-store` and the optional
`Set-Cookie` are built here once. `tests/test_small_helpers_contract.py` pins what each copy answered.
"""

import json

from starlette.responses import Response


def response(result: "admin_auth.AuthResult") -> Response:  # noqa: F821 — the type is only documentation
    body = json.dumps(result.body, ensure_ascii=False).encode("utf-8")
    headers = {"Cache-Control": "no-store"}
    if result.set_cookie:
        headers["Set-Cookie"] = result.set_cookie
    return Response(content=body, status_code=result.status,
                    media_type="application/json; charset=utf-8", headers=headers)
