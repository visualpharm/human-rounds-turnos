"""_native_support.py — shared plumbing for the native FastAPI routers of
family F5 (settings_admin, admin_preferences, admin_content, admin_account,
doctor_patients — docs/roadmap/native-migration-campaign-20260826.md).

`ApiError`, `gate()` and `read_json_body()` live in `core/native_gate.py` (the
same three serve family W2b of `modules/intake`); the routers import them from
here. `not_found_response()` is the shared terminal 404, for the "FastAPI
matched a path template but the inner regex declined it" case, e.g.
`/api/admin/content/<tipo>` with a `<tipo>` outside `TYPE_MAP`.
"""

from app.core.http_json import json_response  # noqa: F401 — the family's routers import it from here
from app.core.native_gate import ApiError, gate, read_json_body  # noqa: F401 — same
from app.core.terminal_response import json_not_found_response as not_found_response  # noqa: F401 — same
