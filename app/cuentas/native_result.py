"""native_result.py — transport-neutral endpoint outcome for the `cuentas`
family (accounts.py / account_photo.py / ocr_dni.py) and their FastAPI
routers.

The native-route migration (docs/roadmap/native-migration-campaign-20260826.md)
uses this value object to keep domain operations transport-neutral. Routers
convert a `Result` to a Starlette response; no legacy response writer remains.
"""

import json


class Result:
    """One HTTP outcome: a JSON-serializable body OR raw bytes, plus status
    and extra headers — the same three things `_json()`/`_send_json()`
    always took."""

    __slots__ = ("status", "json_body", "raw_body", "content_type", "extra_headers")

    def __init__(self, *, status=200, json_body=None, raw_body=None,
                 content_type=None, extra_headers=None):
        self.status = status
        self.json_body = json_body
        self.raw_body = raw_body
        self.content_type = content_type
        self.extra_headers = extra_headers or {}

    @classmethod
    def json(cls, obj, status=200, set_cookie=None, extra_headers=None):
        headers = {"Cache-Control": "no-store"}
        if extra_headers:
            headers.update(extra_headers)
        if set_cookie:
            headers["Set-Cookie"] = set_cookie
        return cls(status=status, json_body=obj,
                    content_type="application/json; charset=utf-8",
                    extra_headers=headers)

    @classmethod
    def raw(cls, data, status=200, content_type="application/octet-stream",
            extra_headers=None):
        return cls(status=status, raw_body=data, content_type=content_type,
                    extra_headers=extra_headers or {})

    def body_bytes(self):
        if self.raw_body is not None:
            return self.raw_body
        return json.dumps(self.json_body, ensure_ascii=False).encode("utf-8")

    def to_response(self):
        from starlette.responses import Response
        body = self.body_bytes()
        headers = dict(self.extra_headers)
        headers["Content-Length"] = str(len(body))
        return Response(content=body, status_code=self.status,
                         media_type=self.content_type, headers=headers)
