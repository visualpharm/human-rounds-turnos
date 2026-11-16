---
title: WhatsApp-OTP (registration gating + password recovery) ships dormant, not active
scope: repo
area: deploy
tags: [otp, auth, whatsapp, waha, cuentas, feature-flag]
created: 2026-07-12
refs: [password-reset-email-via-mailgun]
status: active
---

## Problem
tracking card `2026-07-11-contact-sms-provider-...` asked to enable outgoing SMS to
gate registration and back up password recovery. Paid SMS (Twilio/Vonage,
~US$0.05+/msg to AR, needs A2P/campaign registration) vs WhatsApp-OTP via
WAHA (free, ~100% AR deliverability, no vendor signup) — free+reversible, so
implemented directly instead of parking on the product owner.

## Root cause / risk found mid-implementation
sana-turnos has **no WAHA instance of its own**. The three existing WAHA
containers (Icons8 3000, Il Buco 3002, owner-personal 3010 — see
`~/.claude/ref/services.md`) all belong to other projects/identities; sending
a hospital-patient OTP "from" one of those would be a bad look and isn't
actually reachable from this box's normal HTTP path anyway (LAN-only, one
loopback-only via SSH). So there is currently NO working transport in
production. Gating registration on phone verification with no working
transport would silently **lock real patients out of registering** the
moment this deploys (this repo auto-deploys on push to `main`).

## Solution
Ship the whole feature, but keep the blocking path OFF by default:
- `otp_send.py` — provider-agnostic seam, `OTP_PROVIDER=whatsapp|sms` (default
  whatsapp). WhatsApp sends via generic WAHA HTTP (`OTP_WAHA_URL`/
  `OTP_WAHA_API_KEY`/`OTP_WAHA_SESSION` — a **future dedicated** sana-turnos
  WAHA instance, not one of the 3 above; since the 2026-08-14 transport
  contract that instance can only ever be a loopback dev box — a remote URL
  is refused by the code). Same best-effort/no-op-when-
  unconfigured pattern as `mailgun.py` (`email_mailgun.py` pre-split).
- `core/db.py` (`db.py` pre-split) — `otp_codigos`/`otp_verificados` tables; hashed
  codes, 5-attempt
  cap, 45s resend cooldown, one-time 15-min "verified" ticket
  (`consume_verified_otp`) so the client doesn't resend the code on the
  next POST.
- `cuentas/accounts.py` (`cuentas.py` pre-split) — `/api/cuenta/otp/{enviar,verificar}` + `/api/cuenta/reset_otp`
  (canonical today: `/api/account/otp/{send,verify}` + `/api/account/reset_otp`;
  the Spanish spellings are compatibility aliases)
  (WhatsApp-code reset, additive alongside the existing Mailgun email-link
  reset) are always live. But registration only *requires* a verified phone
  when `OTP_GATING_ENABLED=1` — **unset in Coolify today**, so this ships
  with zero change to live registration. `GET /api/account/config` (canonical;
  `/api/cuenta/config` the alias) now returns
  `otp_registro_requerido` so the frontend can adapt without a redeploy once
  the flag flips.

## Turning it on for real (not done yet — and the WAHA route below is DEAD)

> **Superseded 2026-08-14** (transport contract, commit `6c34573e`, see
> [meta-whatsapp-adoption-runbook.md](../../meta-whatsapp-adoption-runbook.md)):
> the code refuses non-loopback WAHA URLs, so "provision a WAHA instance and
> point `OTP_WAHA_URL` at it" can no longer enable OTP in production — it is
> zero transport by design. The real enablement path needs BOTH an approved
> Meta **Authentication**-template transport and the missing patient
> code-entry UI (`account-form.js` has no OTP step) — neither exists yet;
> until both ship, OTP is unsupported on every production transport and
> `OTP_GATING_ENABLED` stays `0`. Steps below kept as the historical record.

1. Provision a WAHA instance for sana-turnos (own number/session — do not
   reuse Icons8/Il Buco/personal).
2. Set `OTP_WAHA_URL`/`OTP_WAHA_API_KEY`/`OTP_WAHA_SESSION` in Coolify env.
3. Set `OTP_GATING_ENABLED=1` once the frontend has an OTP-entry step wired
   (not built yet — `account-form.js` still submits registro without one).
4. Only then does phone verification actually block anyone.

## Pattern to reuse
Any time a new gate is added to a *live* user-facing flow and the enabling
infra isn't 100% proven in production yet: ship the code fully wired and
tested, but put the blocking behavior behind an env flag that defaults to
"no change" — don't let "implement the free option" become "silently break
registration" just because the happy path was tested with a stub.
