"""native_json.py — shared response plumbing for the admin-core native
routers (admin_users_router.py / admin_patients_router.py /
admin_appointments_router.py).

Why this exists: the native migration split the admin modules into pure domain
functions that take parsed arguments and return data or raise a typed error.
Their FastAPI routers share this response encoder so the JSON, cache, and gzip
contracts stay identical without rebuilding a request-shaped adapter.

`core.gzip_bridge` receives the real Starlette request. No compatibility shim
or fabricated request object exists in this response path.
"""

from app.core.http_json import gzip_json_response as json_response
from app.core.terminal_response import json_not_found_response


class ApiError(Exception):
    """Raised by a domain function with its JSON body and HTTP status."""

    def __init__(self, body, status=400):
        super().__init__(body)
        self.body = body
        self.status = status


def error_response(request, error):
    return json_response(request, error.body, status=error.status)


# The shared terminal 404 (docs/roadmap/native-migration-campaign-20260826.md, "Terminal contract") for a
# path FastAPI matched but whose inner value the legacy regex would have declined (a non-numeric DNI, an
# unrecognized action literal): `core.terminal_response` owns its bytes and headers.
terminal_not_found = json_not_found_response
