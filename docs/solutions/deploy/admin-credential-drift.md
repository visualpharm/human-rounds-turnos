# Admin production credential drift

## Symptom

`POST /api/admin/login` returns 401 in production although the canonical credential vault item works locally and the deployed pages are current.

## Cause

Coolify keeps runtime environment values independently of the repository and credential vault. A redeploy does not correct an old `TURNOS_ADMIN_PASS` by itself.

## Resolution

1. Read the canonical value from credential vault item **Sana Turnos — Admin** without printing it.
2. Update the Coolify application runtime environment key `TURNOS_ADMIN_PASS`.
3. Redeploy the application.
4. Smoke-test login, an authenticated admin endpoint, and logout.

Do not rotate the canonical password merely to match an unknown runtime value.
