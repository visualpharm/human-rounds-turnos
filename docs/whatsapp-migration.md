# WhatsApp migration — drop the SMS plan, go WhatsApp for OTP + notifications

> **Status update (2026-08-14):** the "no production code was changed" framing below
> described the July research pass and is now stale. `main` already contains the Meta
> Cloud API connector (`otp_send._enviar_meta` + `wa_provider()` seam), the shared
> normalized webhook for WAHA and Meta (`app/modules/chat/whatsapp_chat.py`), cassette and
> provider tests — live in **TEST tier** (test number, allowlisted recipients only).
> Current operating state, adoption steps, and failure modes:
> [meta-whatsapp-adoption-runbook.md](meta-whatsapp-adoption-runbook.md). Since the
> 2026-08-14 transport contract (commit `6c34573e`) the split is enforced in code:
> **Meta is the only production transport; WAHA is development-only, requires BOTH an
> explicit `TURNOS_WA_PROVIDER=waha` opt-in and a loopback `OTP_WAHA_URL` — a remote
> WAHA URL sends nothing, ever, and auto-detect never selects WAHA.** Still true:
> the OTP backend ships provider-independent with its endpoints LIVE — only
> registration enforcement is gated (`OTP_GATING_ENABLED`, default 0; see
> solutions/deploy/whatsapp-otp-feature-flagged-rollout.md); its
> missing pieces are the Meta Authentication-template transport (§3's OTP swap
> is unshipped, OTP over Meta is a refused no-op) and the patient code-entry UI,
> and the §4 external gates
> (Business verification, production number, template approval) remain with the product owner.

Research + migration plan (2026-07-16). §1 "Current state" describes July 2026; for
today's state see the runbook linked above.

## 1. Current state (verified in code, 2026-07-16)

**SMS was never actually implemented.** `_enviar_sms()` in `otp_send.py` is a stub
that logs `"proveedor SMS pago aún no elegido/configurado"` and returns `False` — no SMS
vendor was ever chosen or configured, and no SMS has ever been sent from this app. The
original ask (tracking card `2026-07-11-contact-sms-provider-...`) was to shop for an SMS
provider; that's the plan the product owner is now cancelling. There is no live SMS cost to compare
against — the real comparison is **WhatsApp cost vs. the SMS cost sana-turnos would have
paid had that plan gone ahead** (Twilio/Vonage ≈ US$0.05+/msg to AR, per
`docs/solutions/deploy/whatsapp-otp-feature-flagged-rollout.md`, plus mandatory A2P/campaign
registration overhead SMS carriers require in AR).

**OTP's provider default already pointed at WhatsApp**, not SMS: `OTP_PROVIDER`
defaults to `"whatsapp"`, which routes to the generic WAHA HTTP sender
(`_enviar_whatsapp` → `OTP_WAHA_URL/api/sendText`). This was dormant in
production then (sana-turnos has **no WAHA
instance of its own** — the three existing WAHA containers (Icons8 :3000, Il Buco :3002,
owner-personal :3010) belong to other identities, and reusing one to send a patient's OTP
was explicitly rejected as "a bad look" in the rollout doc). Since the 2026-08-14
transport contract it is dormant **by design**: provisioning a WAHA instance changes
nothing — the sender only reaches loopback, so OTP has no production transport at all.
`OTP_GATING_ENABLED` is unset in
Coolify (default `"0"`, enforced by `app/cuentas/accounts.py`
`_otp_registration_required()` — it gates only registration enforcement; the OTP
endpoints themselves stay live), so
registration currently requires no phone verification at all — zero live user impact today.

**Appointment reminders were email-only when this was written (2026-07-16).**
`reminders.py` (`recordatorios.py` pre-rename) sends a Mailgun email one day
before an approved turno (09:00–21:00 ART window). **Superseded 2026-07-24: a
day-before WhatsApp reminder channel now exists** (`app/notifications/send.py`
`_whatsapp_reminder`, free-text via `otp_send.enviar_texto`, plus
`_whatsapp_rejection` remedies — runbook §1). In production it can only ride
Meta's free-text path, which delivers just inside the 24h service window the
patient opens — the reliable business-initiated reminder still needs an
approved Utility template (runbook §5). No SMS channel exists.

**Password recovery** already has two paths: the live one is an email reset link via
Mailgun (`app/cuentas/accounts.py`'s recover handler → `_send_email` → `email_mailgun.send`),
and an additive
WhatsApp-code path is already coded (`/api/cuenta/reset_otp` — canonical today as
`/api/account/reset_otp`, the Spanish path a compatibility alias —
`_send_otp_whatsapp` in
`app/cuentas/accounts.py`) but dormant for the same reason as OTP gating — and, since the
2026-08-14 contract, no WAHA provisioning could ever wake it in production.

**Conclusion:** there is no SMS channel to rip out. The real work is (a) never build the SMS
stub out, and (b) replace the *WAHA* WhatsApp transport (personal/unofficial, currently
unconfigured anyway) with the *official* Meta WhatsApp Business Cloud API before flipping
`OTP_GATING_ENABLED=1` for real patients.

## 2. WhatsApp Business pricing — Argentina, 2026

Meta retired conversation-based billing on **July 1, 2025**. Current model: **per delivered
template message**, billed by category + recipient country code. There is no longer a
"1,000 free conversations/month" tier for business-initiated messages — that phrase from
older guides described the pre-2025 model. The only free case today is a **service**
reply inside a 24h window the *user* opened by messaging first (and even that becomes
billable at the utility rate from **October 1, 2026**, per Meta's own pricing page).

Argentina indicative rates (per delivered message; cross-referenced across two independent
2026 sources against Meta's own docs — confirm exact figure on Meta's rate card before
committing spend, since Meta updates rates quarterly on the 1st of Jan/Apr/Jul/Oct):

| Category | Rate (USD) | Use |
|---|---|---|
| Marketing | ~$0.0618 | promos, re-engagement (not needed here) |
| Utility | ~$0.0120 | transactional — appointment reminders, confirmations |
| Authentication | ~$0.0220 | OTP / registration verification / password-recovery codes |
| Service | Free until Oct 1, 2026, then billed at utility rate | user-initiated replies only |

Sources: [Meta official pricing page](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing) (updated 2026-07-01, confirms per-message-by-category-and-country model + the Oct 1, 2026 change), [Ominiflow Argentina rate table](https://ominiflow.com/whatsapp-api-pricing/argentina) (2026-06-19), [Botias.io Argentina breakdown](https://botias.io/blog/cuanto-cuesta-whatsapp-business-api-argentina-2026) (2026-04-20, independently lists the same ~$0.05/$0.01/$0.015 order-of-magnitude for marketing/utility/auth).

**Provider comparison:**

| Route | Cost structure | Notes |
|---|---|---|
| **Meta Cloud API (direct)** | Meta's rate above, zero markup | You own webhook infra, token refresh, template submission. Most dev work, cheapest. |
| **Twilio** | Meta's rate + ~$0.005–0.010/msg markup, plus a rented phone number | [Twilio WhatsApp pricing](https://www.twilio.com/en-us/whatsapp/pricing) — mature tooling, but the markup buys nothing sana-turnos needs at this volume. |
| **360dialog** | Flat monthly fee (listed from **$59/mo** or **€49/mo** depending on the page — get a direct quote) claiming **no markup on Meta's per-message fee** | [360dialog pricing](https://360dialog.com/pricing) — fastest onboarding ("~30 min" self-reported), reasonable if the direct-API dev lift is unwanted. |
| **WAHA (unofficial, dev-only since the 2026-08-14 transport contract — never a default, remote URLs refused in code)** | Free (self-host VPS cost only) | Explicitly the wrong production path: no owned instance exists, ToS-violating (linked-device automation), real permanent-ban risk with **no recourse** — WhatsApp's own compliance docs and multiple 2026 write-ups (e.g. [WAHA vs Cloud API](https://whatsapp.checkleaked.cc/blog/whatsapp-cloud-api-vs-unofficial)) flag this exact failure mode for OTP/utility use. Fine for **local dev only**, keep the seam, never point it at production. |

**Verdict vs. SMS:** Authentication-category WhatsApp ($0.022/msg via direct Cloud API) is
roughly **60% cheaper per message than the SMS plan being cancelled** (~$0.05+/msg), before
counting the AR A2P/campaign-registration overhead SMS requires and WhatsApp doesn't. At
sana-turnos's current volume (unknown — no SMS or OTP has ever actually been sent in
production; `OTP_GATING_ENABLED` has been off since launch) the absolute monthly dollar
figure either way is small — tens of dollars at a few hundred messages/month. The dollar
delta isn't the deciding factor yet; **compliance and delivery reliability are**: an
official, Meta-verified channel vs. an unofficial one with ban risk on a channel patients
depend on to register/reset a password.

**Recommendation: direct Meta Cloud API**, no BSP markup, for authentication (OTP,
password-recovery) messages. If the webhook/template-management dev lift is more than
The product owner wants to own, **360dialog** is the fallback (flat fee, no per-message markup, faster
setup) — get a live quote before deciding between the two. **Twilio is not recommended**:
its markup buys nothing sana-turnos needs at this scale. **WAHA stays dev-only**, never
production, for the reasons above.

## 3. Exact code changes (NOT applied in this pass — for the follow-up PR)

- `otp_send.py`:
  - Delete `_enviar_sms()` and the `"sms"` entry in `_PROVIDERES` — the SMS stub
    is dead weight once the plan is formally cancelled.
  - Repoint `_enviar_whatsapp()` from the generic WAHA `sendText` call to the Meta
    Cloud API `POST https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages`
    call, sending the approved `authentication` template instead of a free-text message
    (Cloud API requires an approved template for business-initiated messages outside a
    user-opened window — the current free-text `_mensaje(code)` body won't work as-is).
  - Env vars: replace `OTP_WAHA_URL` / `OTP_WAHA_API_KEY` / `OTP_WAHA_SESSION` with
    `META_WA_TOKEN`, `META_WA_PHONE_NUMBER_ID`, `META_WA_TEMPLATE_NAME` (or keep the WAHA
    vars alongside, gated by a `WA_PROVIDER=meta|waha` sub-flag — which did ship, as
    `TURNOS_WA_PROVIDER`, 2026-07-23 — if local dev should keep
    using WAHA against a sandbox number).
  - ~~Update the module docstring~~ — DONE separately: the docstring was rewritten
    (transport contract, 2026-08-14/18) to state the current truth — WAHA loopback
    dev-only, OTP over Meta refused until an approved Authentication template exists —
    and no longer frames paid SMS as the future path (it survives only as an explicit
    stub note).
- `app/core/db.py` — **no change needed**. `otp_codigos`/`otp_verificados` are already
  provider-agnostic (hashed codes, attempt cap, cooldown).
- `app/cuentas/accounts.py` — **no change needed** for the transport swap. `_send_otp_whatsapp`
  and `_otp_registration_required` (then `_enviar_otp_whatsapp`/`_otp_registro_requerido`
  in the pre-split `cuentas.py`) already call through the
  `otp_send.enviar_otp` seam; that's the isolation this seam was built for. Comments
  referencing "tracking card sms-provider" / future SMS should be updated
  to point at this doc instead.
- `account-form.js` (`cuenta-form.js` pre-rename) — still has **no OTP-entry step** in the
  registration UI (flagged as
  not-built in `docs/solutions/deploy/whatsapp-otp-feature-flagged-rollout.md`). This is a
  prerequisite for flipping `OTP_GATING_ENABLED=1` regardless of transport — separate
  front-end task, not part of the SMS→WhatsApp swap itself.
- **Out of scope, flag only:** `reminders.py` (`recordatorios.py` pre-rename; email was the only channel when this
  was written; a WhatsApp reminder channel has since shipped — §1 above) was
  not asked to move to WhatsApp and has no SMS involvement — don't touch it unless the product owner
  asks for that separately (utility-category template, ~$0.012/msg, would apply if he does).

## 4. What still needs the product owner (cannot be started autonomously)

- **Meta Business Manager account + Business Verification** for sana-turnos as a legal
  entity — requires the product owner's account/signature and business documents. This is the actual
  blocker; nothing below can happen without it.
- **A dedicated WhatsApp Business phone number** for sana-turnos (not a personal or
  Icons8/Il Buco number) — needs to be chosen and registered.
- **Authentication-category message template submission** to Meta for approval (the OTP
  text) — requires the verified Business Manager account to exist first.
- **BSP decision + signup** if going the 360dialog route instead of direct Cloud API —
  needs the product owner's payment method and business identity on the account.
- **Re-confirm pricing** before building: Meta has two more scheduled rate-card changes
  already announced (Aug 1, 2026 — Meta Business Agent token billing, unrelated; Oct 1,
  2026 — service/utility-in-window messages stop being free). Neither changes the
  authentication rate used above, but re-check the live rate card at build time regardless.

## 5. tracking card

Card: `2026-07-16-sana-turnos-whatsapp-pricing-research-drop-sms-move-notifica`. Research
done; code swap (§3) and Meta account setup (§4) are follow-on work pending the product owner's go-ahead
on the account/verification piece.
