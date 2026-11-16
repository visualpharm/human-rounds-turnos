---
title: Password recovery emails go out via Mailgun (shared account), not the product owner's Gmail
scope: repo
area: deploy
tags: [email, auth, mailgun, cuentas]
created: 2026-07-11
refs: []
status: active
---

## Problem
[[password-reset-email-via-gogx]]'s gogx/Gmail approach was rejected by the product owner —
app-generated email must not go out through his personal inbox. Needed a real
transactional-email service without spinning up a brand-new account.

## Root cause
No email infra existed in this repo before this session; the first pass
defaulted to what was locally available (gogx) instead of checking other
projects for an existing service to reuse.

## Solution
Found and reused the product owner's existing **Mailgun** account (same one backing
`inglesconjenny` and `lira` — key in credential vault "Mailgun", AI vault). Registered
a project-specific sending subdomain, `mg.sana-turnos.brieflysites.com`, via
`POST /v3/domains`. `email_mailgun.py` ports `inglesconjenny/app/email.py`'s
transport verbatim: HTTP API (no SDK), best-effort (never raises, returns
bool), silent no-op when `MAILGUN_API_KEY`/`MAILGUN_DOMAIN` are unset or the
domain isn't verified yet. `cuentas.py:_enviar_email()` now just calls
`email_mailgun.send(...)`. Full pattern (account reuse, per-project subdomain,
code shape) captured in the personal `mailgun-email` skill for reuse beyond
this repo.

## How to apply
- **DNS verified 2026-07-11.** `mg.sana-turnos.brieflysites.com` is `state:
  active` in Mailgun; confirmed with a real send (`accepted` → `delivered`
  in `/v3/<domain>/events`). The 3 sending records (TXT SPF, TXT DKIM,
  CNAME tracking) live in Cloudflare, not Porkbun — see gotcha below.
- **`brieflysites.com`'s registrar (Porkbun) is NOT its DNS host (Cloudflare)
  — don't add records via the Porkbun API, they silently never resolve.**
  `whois brieflysites.com` / `domain/getNs` both show the live NS as
  `ara.ns.cloudflare.com` / `nitin.ns.cloudflare.com`. Porkbun's own DNS
  panel (`dns/create` etc.) still accepts writes and even echoes them back
  via `dns/retrieve` — but they're dead-zone leftovers nothing queries.
  Verified this empirically: wrote a probe TXT via `dns/create`, then it
  resolved on *no* nameserver, not even Porkbun's own `curitiba.porkbun.com`.
  Manage DNS for this domain exclusively through Cloudflare.
- Cloudflare credential: account-scoped API token scoped to the DNS zone
  (credential vault "Cloudflare API Token — brieflysites.com DNS", AI vault) — zone
  `brieflysites.com` = `ae798daee72d506a0b1f760953160d42`. The older
  "Turnstile + aiandtractors DNS" token does NOT cover this zone; don't
  reuse it here.
- Any future transactional email (turno confirmations, etc.) reuses
  `email_mailgun.send()` — don't add a second sender.
- Env vars live in `.env` (`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `MAIL_FROM`) —
  same file db.py already reads, no new loading mechanism needed.
