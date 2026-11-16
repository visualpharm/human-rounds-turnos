# "My admin password stopped working" — it's two separate gates, not one broken one

## Symptom

The product owner reports the `/admin` password no longer works in production. Locally the
identical password is accepted fine.

## Cause

Production has **two independent auth layers** stacked in front of the admin
console, added on different days, and nothing documented that they're separate:

1. **`site_auth.py`** — a whole-SITE HTTP Basic Auth wall (added 2026-07-17,
   commit `2c7a981`, pre-launch privacy while working with the hospital's counterparts). Env
   `TURNOS_BASIC_AUTH_USER`/`_PASS`. This is a browser-native Basic Auth popup
   that appears on **every** page, including `/`, before anything renders.
   Credentials: Coolify runtime env, never in the repo (password-manager item
   "Human Rounds — Basic Auth (pre-launch gate)").
2. **`admin_auth.py`** — the real `/admin` login: usuario + contraseña + TOTP
   (owner TOTP secret was set in Coolify on 2026-07-16). Env
   `TURNOS_ADMIN_USER`/`_PASS`/`_TOTP_SECRET`.

Typing the real admin password into the **first** (browser Basic Auth) prompt
always fails — that gate wants the shared site credential, not the admin credential. This
reads exactly like "my password stopped working" even though neither
credential actually changed. Verified 2026-07-20: Coolify's non-preview
runtime `TURNOS_ADMIN_PASS`/`_SECRET`/`_TOTP_SECRET` are byte-identical to the
repo's local `.env` — there was no credential drift this time (contrast with
`admin-credential-drift.md`, a real drift case). The PR-preview environment
does carry a stale, never-updated `TURNOS_ADMIN_PASS` (set
2026-07-08 and since replaced) — harmless, only affects PR preview deploys,
not production.

## Resolution / how to log in

1. Browser Basic Auth popup (whole site): on pinamar.humanrounds.org the
   credential in password-manager item "Human Rounds — pinamar.humanrounds.org
   Basic Auth" (gate back 2026-10-26; it had been lost 2026-08-05, when the
   app env was recreated and only the PR-preview copies of the keys
   survived). PR-preview deploys keep their own item, "Human Rounds — Basic
   Auth (pre-launch gate)" (rotated 2026-09-27).
2. `/admin` login form: `admin` / (canonical password in credential vault "Sana
   Turnos — Admin", AI vault) + 6-digit TOTP code from the secret in that same
   item — must be added once to an authenticator app (Google Authenticator /
   Authy) since it doesn't have a UI enrollment flow yet.

## Prevention

Any time a new gate goes in front of an existing login (`site_auth.py`-style
whole-site walls, a new TOTP requirement, etc.), tell the product owner explicitly and
record the credential in the same credential vault item as the login it's stacked
in front of — a silent new gate is indistinguishable from a broken password.
