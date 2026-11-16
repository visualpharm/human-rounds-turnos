---
title: WhatsApp Cloud API for registration and password-recovery OTP
scope: repo
area: deploy
tags: [otp, auth, whatsapp, meta, cuentas, feature-flag]
created: 2026-07-17
refs: [password-reset-email-via-mailgun, whatsapp-otp-feature-flagged-rollout]
status: unshipped (future design — the flow it describes is not on main; only the shared dormant OTP backend it builds on is)
---

## Decision

> **Status: NOT shipped — future-slice design only.** The provider-independent
> OTP backend already ships on `main` — hashed codes, attempt caps,
> `/api/account/otp/*` endpoints (the Spanish spellings
> `/api/cuenta/otp/enviar`, `/api/cuenta/otp/verificar` and
> `/api/cuenta/reset_otp` are compatibility aliases) that stay LIVE; only
> registration enforcement is
> gated by `OTP_GATING_ENABLED` (default `0`). This
> record covers only what is MISSING: the Meta Authentication-template
> transport (OTP over Meta is a refused no-op — transport contract,
> 2026-08-14 / commit `6c34573e`; the WAHA sender reaches loopback dev only,
> so there is no production OTP transport at all) and the patient-facing
> code-entry UI (`account-form.js` has no OTP step). `OTP_GATING_ENABLED`
> stays `0` until both ship. See
> [meta-whatsapp-adoption-runbook.md](../../meta-whatsapp-adoption-runbook.md) §5.

Use the official Meta WhatsApp Cloud API directly. In this design, WAHA and
the dormant SMS stub are removed from the OTP transport, and registration and
password recovery share the existing hashed-code storage and expose complete
patient-facing code-entry flows — all of it to be built, none of it shipped.

The gate would stay dormant until the Meta development setup has passed one
real send and receive test; `OTP_GATING_ENABLED=1` only after that proof.

## Meta assets

| Item | Value |
|---|---|
| Meta app | `SANA by Icons8` |
| Owning business portfolio | Icons8 LLC, business ID `1277465612348504` |
| App ID | Pending: The product owner currently has partial portfolio access |
| WhatsApp Business Account ID | Pending Meta app creation |
| Test phone-number ID | Pending Meta app creation |
| Template | `sana_codigo_acceso` |
| Category | Authentication |
| Language | Spanish (Argentina), `es_AR` |
| Expiration warning | 10 minutes |
| Button | Copy code |

Meta Authentication templates do not allow a custom body. Meta renders a
fixed localized verification-code sentence, with an optional security line and
expiration warning. Therefore the requested custom sentence cannot be used
verbatim in the Authentication category; the compliant equivalent is the
Meta-provided Spanish body plus the 10-minute expiration warning.

## Environment

Store secrets in the untracked `.env` for local development and in Coolify's
runtime environment for deployment. Never commit a token.

```text
META_WA_TOKEN=<temporary development token or production system-user token>
META_WA_PHONE_NUMBER_ID=<sender phone-number ID>
META_WA_BUSINESS_ACCOUNT_ID=<WABA ID, needed for template management>
META_WA_TEMPLATE_NAME=sana_codigo_acceso
META_WA_TEMPLATE_LANGUAGE=es_AR
META_WA_GRAPH_VERSION=v25.0
OTP_GATING_ENABLED=1   # design end-state only — stays 0 until this ships
```

In this design `otp_send.py` sends the approved template and requires Meta to
return a `wamid` message ID. The code is passed twice, once to the body and
once to the copy-code button, as required by Meta. Logs mask phone numbers
and never print the access token.

## Test recipients requested for the development number

- The product owner AR, ending `7302`
- AR test contact, ending `7867`
- Icons8/Mint US, ending `4305`

Each number must complete Meta's recipient verification before the temporary
test number can message it.

## Activation order

1. Create the app under Icons8 LLC and add the WhatsApp use case.
2. Generate the temporary test number and token.
3. Verify the three test recipients and receive one `hello_world` message.
4. Create the Spanish Authentication template with 10-minute expiry and a
   copy-code button; wait for approval.
5. Store IDs in this document and the token in `.env` plus credential vault.
6. Run registration and password recovery locally with the real template.
7. Replace the temporary token with a system-user token before production.
8. Turn on `OTP_GATING_ENABLED=1` only after the live delivery proof.

## Current external blocker

The product owner's Meta profile has only partial access to Icons8 LLC. Meta omits Icons8
LLC from the app-creation portfolio selector, disables **Add** on the
portfolio's Apps page, and says sensitive actions need approval from someone
with full control. A full-control Icons8 administrator must grant the product owner full
portfolio/app access or create/approve the app ownership.
