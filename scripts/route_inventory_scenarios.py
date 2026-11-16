"""route_inventory_scenarios.py — the closed request-scenario inventory the
offline route probe (`tests/_probe_rows.py`, `parity_rows()`; asserted by
`tests/test_route_probe.py`) drives against the live composition.

These tables were born in `scripts/route_parity_check.py` as the
legacy-vs-native parity driver's scenario families. The parity driver was
retired with the legacy dispatch topology itself (native-migration
campaign, docs/roadmap/native-migration-campaign-20260826.md): once no
legacy transport exists there is nothing left to diff against — but the row
inventory remains the probe's authoritative per-route request list, so the
DATA lives on here, verbatim, one assignment per historical family. Rows
whose first element used to be the owning legacy module object now carry
that module's import alias as a plain string.

Pure literals only. `_probe_rows.parity_rows()` reads this file as AST
(never imports it), and fails closed on any dynamic row — keep every
entry a literal tuple whose (method, path) pair stays constant.
"""

ESCENARIOS = [
    ("GET", "/api/admin/config", None, {"Host": "testserver"}),
    ("GET", "/api/admin/whoami", None, {"Host": "testserver"}),  # sin cookie -> 401
    ("POST", "/api/admin/logout", None, {"Host": "testserver"}),
    ("POST", "/api/admin/login",
     {"usuario": "admin", "password": "credencial-que-no-es-la-real"}, {"Host": "testserver"}),
    ("GET", "/api/admin/passkeys", None, {"Host": "testserver"}),  # sin cookie -> 401
    ("POST", "/api/admin/passkeys/login-options", {}, {"Host": "testserver"}),  # sin sesión, siempre 200
]

ESCENARIOS_PAGINAS = [
    (path, lang)
    for path in ("/", "/about.html", "/privacy.html", "/terms.html")
    for lang in ("es", "en")
]

ESCENARIOS_INTAKE = [
    ("intake_interview_patient", "GET", "/entrevista/1", None),
    ("intake_interview_patient", "GET", "/api/intake/t/faketoken", None),
    ("intake_interview_patient", "GET", "/e/somecode", None),
    ("intake_interview_patient", "POST", "/api/intake/t/faketoken/message", {"texto": "hola"}),
    ("intake_interview_patient", "POST", "/api/intake/t/faketoken/reopen", {}),
    ("intake_interview_patient", "POST", "/api/intake/qr/somecode/start", {}),
    ("intake_consult_admin", "GET", "/api/admin/intake/consult/1", None),
    ("intake_consult_admin", "POST", "/api/admin/intake/consult/1/confirm", {}),
    ("intake_intake_admin", "GET", "/api/admin/intake/settings", None),
    ("intake_intake_admin", "GET", "/api/admin/intake/my-settings", None),
    ("intake_intake_admin", "GET", "/api/admin/intake/alerts/count", None),
    ("intake_intake_admin", "GET", "/api/admin/intake/alerts", None),
    ("intake_intake_admin", "GET", "/api/admin/intake/door/somecode", None),
    ("intake_intake_admin", "POST", "/api/admin/intake/settings", {}),
    ("intake_intake_admin", "POST", "/api/admin/intake/my-settings", {}),
    ("intake_intake_admin", "POST", "/api/admin/intake/alerts/1/ack", {}),
    ("intake_intake_admin", "POST", "/api/admin/intake/door/somecode/regenerate", {}),
    # Native Jinja twins render the same printable not-found document for a
    # token that reaches the path route but fails printable._TOKEN_RE.
    ("intake_printable", "GET", "/r/abc$def", None),
    ("intake_printable", "GET", "/o/a$b", None),
    ("intake_printable", "GET", "/r/faketoken", None),
    ("intake_printable", "GET", "/o/faketoken", None),
]

ESCENARIOS_PATIENT_SITE = [
    ("patient_site", "GET", "/api/services", None),
    ("patient_site", "GET", "/api/doctors", None),
    ("patient_site", "GET", "/api/centers", None),
    ("patient_site", "GET", "/api/directory", None),
    # Slice 8 (segundo lote, booking-flow) — service.py's info+wizard page
    # (una sola ruta FastAPI, `{rest:path}`, cubre las dos formas legacy) +
    # la API de horarios que el calendario del wizard consulta + los dos
    # links de "Mis turnos" (ambos son SIEMPRE un 302, nunca dependen de
    # Postgres — `_run_fastapi` corre con `follow_redirects=False` desde
    # este lote justamente por estos tres).
    ("patient_site", "GET", "/service/algun-servicio", None),
    ("patient_site", "GET", "/service/algun-servicio/book", None),
    ("patient_site", "GET", "/api/service-slots/algun-servicio", None),
    ("patient_site", "GET", "/my-requests", None),
    ("patient_site", "GET", "/my-requests/", None),
    ("patient_site", "GET", "/request/123", None),
    # Slice 8 closing batch — de-linking redirects + catalog/directory pages
    # (the family this router's own docstring names as "left for a later
    # batch"). Every one of these is either an unconditional 302 to "/" (the
    # de-linking redirects) or a 200/302 depending on whether the catalog
    # has a matching entry — never dependent on a session, so "same response
    # whatever it is" is enough without seeding data.
    ("patient_site", "GET", "/app", None),
    ("patient_site", "GET", "/app/", None),
    ("patient_site", "GET", "/app.html", None),
    ("patient_site", "GET", "/clinicas", None),
    ("patient_site", "GET", "/clinicas/", None),
    ("patient_site", "GET", "/directorio", None),
    ("patient_site", "GET", "/directorio/", None),
    ("patient_site", "GET", "/directorio.html", None),
    ("patient_site", "GET", "/obras-sociales", None),
    ("patient_site", "GET", "/obras-sociales/", None),
    ("patient_site", "GET", "/panel", None),
    ("patient_site", "GET", "/panel/", None),
    ("patient_site", "GET", "/panel.html", None),
    ("patient_site", "GET", "/clinica/algo", None),
    ("patient_site", "GET", "/cobertura/algo", None),
    ("patient_site", "GET", "/doctors", None),
    ("patient_site", "GET", "/doctors/", None),
    ("patient_site", "GET", "/doctor/algun-medico", None),
    ("patient_site", "GET", "/d/diagnostico", None),
    ("patient_site", "GET", "/study/algun-pais/algun-estudio", None),
    ("patient_site", "GET", "/centers", None),
    ("patient_site", "GET", "/centers/", None),
    ("patient_site", "GET", "/center/algun-centro", None),
    ("patient_site", "GET", "/news", None),
    ("patient_site", "GET", "/news/", None),
    ("patient_site", "GET", "/news/pedidos", None),
    ("patient_site", "GET", "/news/pedidos/", None),
]

ESCENARIOS_BOOKING_REQUESTS = [
    ("booking_requests", "GET", "/api/requests", None),
    ("booking_requests", "GET", "/api/requests/1", None),
    ("booking_requests", "POST", "/api/requests", {}),
    ("booking_requests", "POST", "/api/requests/1/cancel", {}),
    ("booking_requests", "POST", "/api/requests/1/reprogramar", {}),
    ("booking_requests", "POST", "/api/requests/1/waitlist", {}),
    ("booking_requests", "POST", "/api/analyze-order", {}),
    ("booking_requests", "GET", "/api/orden-preview/token-inexistente", None),
    # Slice 8 (third batch, admin surfaces) — the STAFF half of this same
    # file: `GET /admin` (the shell page, no auth of its own — always 200
    # HTML in both transports) plus every `/api/admin/*` path gated by
    # `admin_auth.require_admin(...)` from INSIDE the handler function
    # itself (`_check_admin`/`_request_staff`/the Settings triad's own
    # inline check) — without a session cookie every one of these 401s
    # before touching the DB, in both legacy and FastAPI, same "same
    # response whatever it is" criterion as the rest of this file.
    # `/api/admin/config`/`/whoami`/`/login`/`/logout` are NOT repeated
    # here — already covered by `ESCENARIOS` above via `admin_auth_router.py`
    # (slice 1), which claims those four paths first in both transports.
    ("booking_requests", "GET", "/admin", None),
    ("booking_requests", "GET", "/api/admin/theme", None),
    ("booking_requests", "POST", "/api/admin/theme", {}),
    ("booking_requests", "GET", "/api/admin/auth-mode", None),
    ("booking_requests", "POST", "/api/admin/auth-mode", {}),
    ("booking_requests", "GET", "/api/admin/turnstile", None),
    ("booking_requests", "POST", "/api/admin/turnstile", {}),
    ("booking_requests", "GET", "/api/admin/requests", None),
    ("booking_requests", "GET", "/api/admin/hsi-sync", None),
    ("booking_requests", "POST", "/api/admin/hsi-sync/resolver", {}),
    ("booking_requests", "POST", "/api/admin/hsi-sync/conflicts", {}),
    ("booking_requests", "GET", "/api/admin/order/1", None),
    ("booking_requests", "GET", "/api/admin/refeps", None),
    ("booking_requests", "POST", "/api/admin/requests/1/approve", {}),
    ("booking_requests", "POST", "/api/admin/requests/1/assign", {}),
    ("booking_requests", "POST", "/api/admin/requests/1/retry-hsi", {}),
    ("booking_requests", "POST", "/api/admin/requests/1/reject", {}),
    ("booking_requests", "POST", "/api/admin/requests/1/campos", {}),
    ("booking_requests", "POST", "/api/admin/requests/1/attendance", {}),
]

ESCENARIOS_SLOT_HOLDS = [
    ("slot_holds", "POST", "/api/slot-hold", {}),
    ("slot_holds", "DELETE", "/api/slot-hold/token-inexistente", None),
]

ESCENARIOS_APPOINTMENT_OFFER = [
    ("appointment_offer", "GET", "/appointment/offer/token-inexistente", None),
    ("appointment_offer", "POST", "/api/offer/token-inexistente/accept", {}),
    ("appointment_offer", "POST", "/api/offer/token-inexistente/reject", {}),
]

ESCENARIOS_APPOINTMENT_CONFIRM = [
    ("appointment_confirm", "GET", "/appointment/confirm/token-inexistente", None),
    ("appointment_confirm", "POST", "/api/confirm/token-inexistente/si", {}),
]

ESCENARIOS_ACCOUNTS = [
    ("accounts", "GET", "/account", None),
    ("accounts", "GET", "/api/account/config", None),
    ("accounts", "GET", "/api/account/me", None),  # sin cookie -> 401
    ("accounts", "GET", "/api/account/dependents", None),  # sin cookie -> 401
    ("accounts", "POST", "/api/account/register",
     {"dni": "00000000", "nombre": "Ruta", "apellido": "Paridad",
      "password": "no-es-una-cuenta-real"}),
    ("accounts", "POST", "/api/account/login",
     {"dni": "00000000", "password": "credencial-que-no-es-la-real"}),
    ("accounts", "POST", "/api/account/logout", None),
    ("accounts", "POST", "/api/account/update", {}),  # sin cookie -> 401
    ("accounts", "POST", "/api/account/dependents/delete", {}),  # sin cookie -> 401
    # Slice 8 closing batch — reset/OTP/test-identity family.
    ("accounts", "GET", "/account/reset", None),
    ("accounts", "GET", "/account/portal-login", None),  # sin portal -> 303 /account
    ("accounts", "GET", "/auth/test", None),
    ("accounts", "GET", "/auth/sso", None),
    ("accounts", "GET", "/api/account/test-identities", None),
    ("accounts", "POST", "/api/account/test-identities", {}),
    ("accounts", "POST", "/api/account/test-identities/random", {}),
    ("accounts", "POST", "/api/account/test-login", {}),
    ("accounts", "POST", "/api/account/recover", {}),
    ("accounts", "POST", "/api/account/reset", {}),
    ("accounts", "POST", "/api/account/reset_otp", {}),
    ("accounts", "POST", "/api/account/otp/send", {}),
    ("accounts", "POST", "/api/account/otp/verify", {}),
]

ESCENARIOS_ADMIN_CONTENT = [
    ("admin_content", "GET", "/api/admin/content/services", None),
    ("admin_content", "POST", "/api/admin/content/services", {}),
    ("admin_content", "PUT", "/api/admin/content/services/1", {}),
    ("admin_content", "DELETE", "/api/admin/content/services/1", None),
    ("admin_content", "POST", "/api/admin/content/services/1/restaurar", {}),
    ("admin_content", "POST", "/api/admin/content/knowledge/properties", {}),
]

ESCENARIOS_ADMIN_PATIENTS = [
    ("admin_patients", "GET", "/api/admin/patients", None),
    ("admin_patients", "GET", "/api/admin/patients/12345678", None),
    ("admin_patients", "POST", "/api/admin/patients", {}),
    ("admin_patients", "PUT", "/api/admin/patients/12345678", {}),
    ("admin_patients", "DELETE", "/api/admin/patients/12345678", None),
    ("admin_patients", "POST", "/api/admin/patients/12345678/temp-password", None),
    ("admin_patients", "GET", "/api/admin/dni-claims", None),
    ("admin_patients", "POST", "/api/admin/dni-claims/1/resolve", {"aprobar": False}),
    ("admin_patients", "POST", "/api/admin/patients/12345678/dependents", {}),
    ("admin_patients", "DELETE", "/api/admin/patients/12345678/dependents/87654321", None),
    ("admin_patients", "POST", "/api/admin/patients/12345678/clinical-history", {}),
]

ESCENARIOS_ADMIN_APPOINTMENTS = [
    ("admin_appointments", "POST", "/api/admin/appointments", {}),
    ("admin_appointments", "GET", "/api/admin/appointment-slots", None),
]

ESCENARIOS_DOCTOR_APPOINTMENTS = [
    ("doctor_appointments", "GET", "/api/admin/doctors/algun-medico/compatible-services", None),
    ("doctor_appointments", "GET", "/api/admin/doctors/algun-medico/appointments", None),
    ("doctor_appointments", "GET", "/api/admin/doctors/stats", None),
    ("doctor_appointments", "GET", "/api/admin/my-appointments", None),
    ("doctor_appointments", "GET", "/api/admin/my-appointments/feed", None),
    ("doctor_appointments", "GET", "/api/admin/my-appointments/1/order", None),
    ("doctor_appointments", "GET", "/api/admin/day-queue", None),
]

ESCENARIOS_DOCTOR_PATIENTS = [
    ("doctor_patients", "GET", "/api/admin/doctor-patients", None),
]

ESCENARIOS_REPORTS = [
    ("reports", "GET", "/api/admin/reports", None),
    ("reports", "GET", "/api/admin/reports/requests.csv", None),
    ("reports", "GET", "/api/admin/reports/patients", None),
    ("reports", "GET", "/api/admin/reports/login-panel", None),
    ("reports", "GET", "/api/admin/reports/searches", None),
    ("reports", "GET", "/api/admin/reports/reviews", None),
    ("reports", "GET", "/api/admin/reports/cost-recovery", None),
    ("reports", "GET", "/api/admin/reports/cost-recovery.csv", None),
    ("reports", "GET", "/api/admin/reports/sumar", None),
    ("reports", "GET", "/api/admin/reports/sumar.csv", None),
    ("reports", "GET", "/api/admin/reports/statistics", None),
    ("reports", "GET", "/api/admin/reports/statistics.csv", None),
    ("reports", "GET", "/api/admin/reports/capacity", None),
    ("reports", "GET", "/api/admin/reports/funnel", None),
    ("reports", "POST", "/api/admin/reports/funnel/steps", {}),
]

ESCENARIOS_CHART_SUMMARY = [
    ("chart_summary", "GET", "/api/admin/patient-summary?dni=00000000", None),
    ("chart_summary", "POST", "/api/admin/patient-summary", {"dni": "00000000"}),
]

ESCENARIOS_LAB_RESULTS = [
    ("lab_results", "GET", "/api/account/lab-results", None),
]

ESCENARIOS_PREPARATION = [
    ("preparation", "GET", "/upload-order", None),
    ("preparation", "GET", "/api/preparation-studies", None),
]

ESCENARIOS_DOCTOR_PHOTOS = [
    ("doctor_photos", "GET", "/api/admin/doctors/algun-medico/photo", None),
    ("doctor_photos", "POST", "/api/admin/doctors/algun-medico/photo", {}),
    ("doctor_photos", "DELETE", "/api/admin/doctors/algun-medico/photo", None),
    ("doctor_photos", "GET", "/doctor-photo/algun-medico", None),
    ("doctor_photos", "GET", "/api/mi/foto", None),
    ("doctor_photos", "POST", "/api/mi/foto", {}),
    ("doctor_photos", "DELETE", "/api/mi/foto", None),
]

ESCENARIOS_AVATAR_SPRITE = [
    ("avatar_sprite", "GET", "/avatar-sprite/0123456789abcdef.webp", None),
    ("avatar_thumb", "GET", "/doctor-thumb/nobody.0123456789abcdef.webp", None),
]

ESCENARIOS_ACCOUNT_PHOTO = [
    ("account_photo", "GET", "/api/account/photo", None),
    ("account_photo", "POST", "/api/account/photo", {}),
    ("account_photo", "DELETE", "/api/account/photo", None),
]

ESCENARIOS_AI_COSTS = [
    ("ai_costs", "GET", "/api/admin/costs", None),
]

ESCENARIOS_AI_HEALTH = [
    ("ai_health", "GET", "/api/ai-status", None),
]

ESCENARIOS_OCR_DNI = [
    ("ocr_dni", "POST", "/api/ocr-dni", {}),
]

ESCENARIOS_PROMPTS = [
    ("prompts", "GET", "/api/admin/prompts", None),
    ("prompts", "GET", "/api/admin/prompts/algun-prompt", None),
    ("prompts", "GET", "/api/admin/prompts/algun-prompt/traduccion", None),
    ("prompts", "GET", "/api/admin/prompts/algun-prompt/historial", None),
    ("prompts", "POST", "/api/admin/prompts/algun-prompt/guardar", {}),
    ("prompts", "DELETE", "/api/admin/prompts/algun-prompt", None),
]

ESCENARIOS_SEARCHES = [
    ("searches", "POST", "/api/search-log", {}),
    ("searches", "POST", "/api/ev", {}),
]

ESCENARIOS_SITEMAP = [
    ("sitemap", "GET", "/sitemap.xml", None),
]

ESCENARIOS_PUCO = [
    ("connectors_puco_puco", "POST", "/api/puco", {"dni": "00000000"}),
]

ESCENARIOS_CHAT_API = [
    # /api/chat/stream is NOT here — it stays legacy-proxy-only (SSE would
    # buffer wrong through this same LegacyHandlerShim adapter; see
    # chat_api_router.py's own docstring), so it has no router path to run
    # this same-response comparison against.
    ("chat_api", "POST", "/api/chat", {"mensaje": "hola"}),
    ("chat_api", "GET", "/api/chat/whatsapp", None),
    ("chat_api", "GET", "/api/chat/history", None),
    ("chat_api", "GET", "/api/chat-transcripts", None),
]

ESCENARIOS_VOICE = [
    ("voice", "GET", "/api/chat/voice", None),
    ("voice", "POST", "/api/chat/transcribe", {}),
    ("voice", "GET", "/api/admin/chat-voice", None),
    ("voice", "POST", "/api/admin/chat-voice", {}),
]

ESCENARIOS_CALENDAR_FEED = [
    ("calendar_feed", "GET", "/api/appointment/123.ics", None),
    ("calendar_feed", "GET", "/api/calendar/feed", None),
    ("calendar_feed", "POST", "/api/calendar/feed/regenerate", {}),
    ("calendar_feed", "GET", "/feed/appointments/token-inexistente.ics", None),
    ("calendar_feed", "GET", "/feed/doctor/token-inexistente.ics", None),
    ("calendar_feed", "GET", "/appointment/token-inexistente.ics", None),
]

ESCENARIOS_ADMIN_USERS = [
    ("admin_users", "GET", "/api/admin/users", None),
    ("admin_users", "POST", "/api/admin/users", {}),
    ("admin_users", "POST", "/api/admin/users/actions/disable", {}),
    ("admin_users", "PUT", "/api/admin/users/algun-usuario", {}),
    ("admin_users", "DELETE", "/api/admin/users/algun-usuario", None),
]

ESCENARIOS_ADMIN_PREFERENCES = [
    ("admin_preferences", "GET", "/api/admin/preferences", None),
    ("admin_preferences", "PUT", "/api/admin/preferences", {}),
]

ESCENARIOS_ADMIN_PASSWORD_RESET = [
    ("admin_password_reset", "GET", "/admin/reset-password", None),
    ("admin_password_reset", "POST", "/api/admin/reset-password", {}),
]

ESCENARIOS_ADMIN_ACCOUNT = [
    ("admin_account", "GET", "/api/admin/account", None),
    ("admin_account", "POST", "/api/admin/account/profile", {}),
    ("admin_account", "POST", "/api/admin/account/password", {}),
    ("admin_account", "POST", "/api/admin/account/2fa/start", {}),
    ("admin_account", "POST", "/api/admin/account/2fa/confirm", {}),
    ("admin_account", "POST", "/api/admin/account/2fa/remove", {}),
]

ESCENARIOS_SETTINGS_ADMIN = [
    ("settings_admin", "GET", "/api/admin/settings", None),
    ("settings_admin", "POST", "/api/admin/settings", {}),
    ("settings_admin", "POST", "/api/admin/settings/probar", {}),
]

ESCENARIOS_PANEL_LOGIN = [
    ("panel_login", "GET", "/api/login-panel", None),
    ("panel_login", "POST", "/api/login-panel/event", {}),
]

ESCENARIOS_SYSTEM_STATUS = [
    ("system_status", "GET", "/api/admin/system", None),
]

ESCENARIOS_BRANDING = [
    # /logo.png and /logo stay out on purpose (static-fallback chain, see
    # branding_router.py's own docstring) — no router path to compare.
    ("branding", "GET", "/api/admin/branding", None),
    ("branding", "POST", "/api/admin/branding", {}),
    ("branding", "POST", "/api/admin/branding/logo", {}),
    ("branding", "DELETE", "/api/admin/branding/logo", None),
]

ESCENARIOS_LEGAL = [
    ("legal", "GET", "/api/admin/legal", None),
    ("legal", "GET", "/api/admin/legal/terms", None),
    ("legal", "PUT", "/api/admin/legal/terms", {}),
    ("legal", "DELETE", "/api/admin/legal/terms", None),
]
