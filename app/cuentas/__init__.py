"""cuentas/ — patient accounts, DNI sign-up, and health coverage: register/
login/logout/profile (`accounts.py`, DNI + password, sessions, dependents,
password recovery, OTP), the RENAPER/Mi Argentina federated-login HTTP route
(`auth_renaper.py`), health-coverage/prepaid-plan resolution
(`health_coverage.py`), the profile-photo upload+serve path
(`account_photo.py`), and DNI-photo OCR/barcode sign-up assist (`ocr_dni.py`).

Tier 2 batch G (docs/roadmap/tier2-physical-modularization-plan.md): a
domain package, not a module — platform-core, shared infra other code calls
into, never disabled at runtime (`accounts.py` is deliberately NOT one of
`modules/turnos/module.json`'s `routes` entries; `app.py` mounts
`accounts_router` directly, same footing as `auth/admin_auth_router.py`).
Deliberately minimal: no re-export facade. Importers use explicit
submodules:

    from cuentas import accounts                # register/login/profile/dependents, `current_user(handler)`
    from cuentas import accounts_router          # FastAPI twin: /account, /api/account/*, /auth/*
    from cuentas import account_photo            # profile-photo upload/serve/Gravatar fallback
    from cuentas import account_photo_router     # FastAPI twin: /api/account/photo*
    from cuentas import auth_renaper             # RENAPER/Mi Argentina federated login (/auth/sso, /auth/renaper)
    from cuentas import health_coverage          # obra social / prepaga principal-coverage ranking
    from cuentas import ocr_dni                  # DNI photo OCR assist for registration
    from cuentas import ocr_dni_router           # FastAPI twin: /api/ocr-dni
"""
