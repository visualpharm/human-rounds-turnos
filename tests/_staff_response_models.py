"""_staff_response_models.py — which staff operations of booking requests,
patients and the doctor's appointments must document their success answer
(TASK-76, OpenAPI stage D, area b). The checks of an answer are the
shared ones of `tests/_response_models.py`. Shared by `tests/test_staff_response_docs.py` (the
schema and the golden answers, DB-less) and
`tests/routes/test_staff_response_models.py` (every operation answered by the
live app).

The area is read from the built schema and the routes that answer it
(`routers/api_docs.build_schema`, `api_docs._route_index`), never from the
source text: every `/api/admin/...` operation whose endpoint is defined in
the `turnos` module or in one of the panel's routers of patients, the desk's
appointments and the doctor's own workspace — except the settings the
`turnos` module also serves (theme, sign-in mode, Turnstile), which belong
with the panel's other settings.

Not named test_*.py: unittest discovery must not collect it.
"""

AREA_MODULES = (
    "app.modules.turnos",
    "app.admin_panel.admin_patients_router",
    "app.admin_panel.admin_appointments_router",
    "app.admin_panel.doctor_patients_router",
    "app.admin_panel.doctor_workspace_screen",
)

# Settings the `turnos` module serves: documented with the panel's settings.
NOT_THIS_AREA = {
    ("get", "/api/admin/theme"), ("post", "/api/admin/theme"),
    ("get", "/api/admin/auth-mode"), ("post", "/api/admin/auth-mode"),
    ("get", "/api/admin/turnstile"), ("post", "/api/admin/turnstile"),
}

# Operations of the area whose success answer is not a JSON document a model
# could describe, and why.
EXCLUDED = {
    ("get", "/api/admin/order/{req_id}"): "the referral the patient uploaded, as it came (image or PDF bytes)",
    ("get", "/api/admin/my-appointments/{req_id}/order"):
        "the referral of the doctor's own appointment, as it came (image or PDF bytes)",
    ("get", "/api/admin/day-queue/events"): "a server-sent event stream (text/event-stream) of queue updates",
    ("get", "/api/admin/patients/{dni}/data-export"):
        "the patient's data as a zip download (application/zip), not JSON",
    ("get", "/api/admin/hsi-extension/{name}"): "the HSI extension's guide (PDF) or package (zip), a download",
}


def _in_area(module):
    return any(module == m or module.startswith(m + ".") for m in AREA_MODULES)


def area_operations(schema, app):
    """[(method, path, operation)] of the area, in schema order."""
    from app.routers import api_docs
    index = api_docs._route_index(app)
    out = []
    for path, item in schema["paths"].items():
        if not path.startswith("/api/admin/"):
            continue
        for method, operation in item.items():
            if (method, path) in NOT_THIS_AREA:
                continue
            if _in_area(index[(path, method)].endpoint.__module__):
                out.append((method, path, operation))
    return out
