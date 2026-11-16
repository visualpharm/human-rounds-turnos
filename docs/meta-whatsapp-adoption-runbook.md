# Meta WhatsApp adoption runbook — TEST tier today, what production takes

Tier 6 documentation slice (tracking card
`2026-08-10-capture-human-rounds-tier-6-close-meta-whatsapp-adoptionru`).
This runbook separates three things that keep getting conflated:

1. **What is implemented and live on `main`** (verified in code, 2026-08-14).
2. **What a clinic must do to reach production** (external gates, owned by
   The product owner / the clinic — not by any coding session).
3. **What can go wrong** at each layer, and how to tell which layer broke.

Nothing in this document authorizes sending messages to real patients or
activating Meta production access. Those are explicit human decisions (§4).

## 1. Current state — implemented, TEST tier

The Meta WhatsApp Cloud API connector is **implemented and live in TEST
mode** on `main`. (Older text claiming "no production code was changed"
described the July research pass and is superseded — see the status note at
the top of [whatsapp-migration.md](whatsapp-migration.md).)

What exists in code:

- **One normalized inbound webhook** — `GET/POST /api/whatsapp/webhook`
  (`app/modules/chat/whatsapp_chat.py`). WAHA and Meta push to the *same*
  endpoint; `_es_meta(body)` distinguishes by payload shape and both are
  normalized to one internal message shape before any processing. Meta's
  `hub.challenge` verification handshake is implemented on GET.
- **An env-selected outbound seam** — `otp_send.wa_provider()` +
  `otp_send.enviar_texto()` (`app/notifications/otp_send.py`). Meta runs with
  `TURNOS_WA_PROVIDER=meta` or, absent an explicit pin, when `META_WA_TOKEN`
  is set. WAHA runs ONLY as an explicit dev opt-in: `TURNOS_WA_PROVIDER=waha`
  AND an `OTP_WAHA_URL` pointing at loopback (127.0.0.0/8 / localhost / ::1) —
  a remote WAHA URL sends nothing, ever (zero network, logged no-op), and
  auto-detect never selects WAHA. No provider → logged no-op. Every transport
  is best-effort: never raises, returns `False`. Meta logs mask the phone to
  its last 4 digits; the WAHA sender's logs still print the destination
  UNMASKED on its unconfigured / send-success / send-failure lines (only the
  remote-refusal line masks) — pre-existing dev-path behavior deliberately
  left alone, so treat loopback WAHA logs as containing real numbers
  (`chat-y-whatsapp.md` records the same asymmetry).
- **What actually goes out over WhatsApp** (all via `enviar_texto`, all
  free-text — but two different delivery shapes):
  - Chat replies from the turnos assistant (same engine as the web widget) —
    free text inside the 24h service window the PATIENT opens by writing
    first. That makes them policy-ELIGIBLE (no template needed), not
    guaranteed delivered — an in-window send can still fail (token, quality
    hold), and on today's TEST number only allowlisted recipients are
    reachable at all.
  - Day-before appointment reminders (`app/notifications/send.py`
    `_whatsapp_reminder`) asking for a SÍ/NO confirmation —
    BUSINESS-initiated: their free-text send delivers only when a window
    happens to be open from the last 24h; reliable scheduled delivery needs
    an approved **Utility** template (§5).
  - Rejection remedies (`_whatsapp_rejection`) when a request is rejected
    but a concrete alternative exists — same business-initiated shape and
    same Utility-template gap as the reminder.
- **What does NOT go over Meta**: the OTP. The state is three-part: (1) the
  provider-independent OTP **backend ships on `main` and its endpoints are
  LIVE** — hashed codes with attempt caps and cooldowns,
  `/api/account/otp/{send,verify}` + `/api/account/reset_otp` (the Spanish
  spellings `/api/cuenta/otp/enviar`, `/api/cuenta/otp/verificar`,
  `/api/cuenta/reset_otp` and `/api/cuenta/config` are compatibility
  aliases via `app/core/legacy_urls.py`); only REGISTRATION
  ENFORCEMENT is feature-gated, by `OTP_GATING_ENABLED` (default `0`) — the
  flag decides whether registering requires a verified phone, not whether
  the endpoints answer; (2) the Meta Authentication-template **transport
  exists since 2026-11-16 but no site enables it** —
  `OTP_PROVIDER=meta` sends the code as the approved template named by
  `META_WA_OTP_TEMPLATE` (Human Rounds WABA: `turnos_codigo`, `es_AR`,
  copy-code button, approved 2026-11-16) and is a logged no-op without it;
  `sms` is a logging stub, and the default `whatsapp` WAHA sender only
  reaches a loopback dev URL; (3) the patient-facing
  **code-entry UI is implemented but dormant** — when
  `otp_registro_requerido` is true and a phone is present,
  `account-form.js` sends, accepts a 6-digit code, verifies it, and only
  then posts registration. It preserves the email-only flow and does not
  alter the server's one-use verification ticket. **`OTP_GATING_ENABLED`
  must stay `0`** until an approved Authentication template and the
  transport that sends it ship.
  (A stale branch `codex/whatsapp-cloud-otp` and the solutions doc
  [whatsapp-cloud-api-otp.md](solutions/deploy/whatsapp-cloud-api-otp.md)
  describe that future slice; do not treat them as current behavior.)
- **Safety rails**: demo mode (`ops/demo_installation.enabled()`) blocks
  all WhatsApp output; without `TURNOS_WA_WEBHOOK_SECRET` the webhook
  endpoint is fully disabled (404), never open by accident; outbound
  Markdown is converted to WhatsApp syntax in one choke point
  (`_markdown_a_whatsapp`).
- **Tests**: `tests/test_meta_cassette.py` (recorded Graph API exchanges),
  `tests/test_whatsapp_chat.py` (both inbound shapes), and the WhatsApp
  normalization/provider suites pass on `main` without network.

Live Meta assets (verified via the Meta DevTools API, 2026-08-14):

| Item | Value |
|---|---|
| Meta app (current) | **Human Rounds**, app ID `1761206325139639` |
| Webhook subscription | topic `whatsapp_business_account`, field `messages`, **enabled**, callback on `pinamar.humanrounds.org` |
| Number tier | **TEST number** — deliverable only to the dashboard allowlist (max 5 recipients) |
| Historic app | "SANA by Icons8" (`1067912525897266`) — earlier OTP research targeted it; the live subscription is on Human Rounds |

Test-number IDs are **not stable**: Meta re-provisions the WABA and
`phone_number_id` between dashboard visits. Before blaming a token, run the
diagnostic recipe in
[meta-cloud-api-reprovisioning.md](solutions/deploy/meta-cloud-api-reprovisioning.md)
(`debug_token → granular_scopes.target_ids`, then `/<waba_id>/phone_numbers`).

## 2. Recommended default configuration

| Environment | Setting | Why |
|---|---|---|
| Production | `TURNOS_WA_PROVIDER=meta` (explicit) | Pins the transport visibly. Since the 2026-08-14 code slice the code itself never falls back to WAHA (remote WAHA URLs are dead weight, auto-detect is Meta-or-nothing), so the pin is belt-and-suspenders plus self-documentation, not the only safety. |
| Production | `OTP_WAHA_URL` **unset** | Removes the dead config entirely. WAHA in production is the wrong path: ToS-violating, permanent-ban risk, no recourse — and the code refuses non-loopback WAHA URLs anyway. |
| Production | `OTP_GATING_ENABLED=0` | The OTP endpoints and dormant code-entry UI are present; enable this flag only after `OTP_PROVIDER=meta` + `META_WA_OTP_TEMPLATE=<approved template>` are set and one real code has arrived (§1). |
| Local dev | WAHA against a loopback/own instance, or nothing | Free, no template constraints; the seam keeps it one env var away. |

Fail-closed invariant: **no configured provider → no send, logged no-op** —
never a silent fallback to an unofficial transport. **Enforced in code since
2026-08-14** (`otp_send.wa_provider()` + the loopback guard in
`_enviar_whatsapp`; OTP additionally refuses `OTP_PROVIDER=meta` without
`META_WA_OTP_TEMPLATE`). Contract tests:
`tests/test_transport_contract.py`; the template send, the pause after a
Meta rate limit and the error causes: `tests/test_meta_wa_limits.py`.

## 3. New-clinic integration steps (technical side)

Prerequisites in §4 must be done first; these steps assume the clinic's
verified Meta assets exist.

1. **Create/confirm the Meta app** (type Business) under the clinic's or
   operator's Business portfolio, add the WhatsApp product.
2. **Phone number**: register the clinic's dedicated number under the WABA
   (never a personal number — registration converts it and it cannot run
   the WhatsApp app anymore). Verify via SMS/voice code.
3. **System-user token**: create a system user in Business Manager with
   access to the WABA, generate a permanent token with
   `whatsapp_business_messaging` + `whatsapp_business_management`. Do not
   use the 24-hour temporary dashboard token beyond first smoke tests.
   Store in the deployment's secret store only.
4. **Webhook**: in the app dashboard → WhatsApp → Configuration, set the
   callback URL to `https://<host>/api/whatsapp/webhook` and the verify
   token to the value of `TURNOS_WA_WEBHOOK_SECRET`. The app answers the
   `hub.challenge` handshake automatically once that env is set. Subscribe
   to the `messages` field only. Subscribing the app to the WABA
   (`POST /<waba_id>/subscribed_apps`) can be done by API; the callback URL
   and the test-recipient allowlist are dashboard-only.

   Then copy the **app secret** (App dashboard → Settings → Basic) into
   `META_WA_APP_SECRET`. The verify token covers only the GET handshake;
   inbound POSTs are authenticated by `X-Hub-Signature-256`, an HMAC of the
   raw body under that app secret, which is the only credential Meta sends.
   Without it this callback URL rejects every inbound message with 401 —
   the earlier workaround was appending `?secret=<TURNOS_WA_WEBHOOK_SECRET>`
   to the callback URL, which works but writes the shared secret into the
   Meta dashboard and into every proxy's access log. If an existing
   deployment is configured that way, set the app secret, drop the query
   string, and rotate `TURNOS_WA_WEBHOOK_SECRET` on both sides.
5. **Env vars** (all read through `core/config`, editable in the admin
   Settings screen — Sistema shows a live WhatsApp cluster):

   | Var | Meaning |
   |---|---|
   | `META_WA_TOKEN` | system-user access token |
   | `META_WA_PHONE_NUMBER_ID` | sender number ID (WhatsApp API Setup) |
   | `META_WA_GRAPH_VERSION` | Graph version, default `v25.0` |
   | `TURNOS_WA_PROVIDER` | `meta` (pin explicitly in production) |
   | `TURNOS_WA_WEBHOOK_SECRET` | webhook verify token (GET handshake) / WAHA's shared secret |
   | `META_WA_APP_SECRET` | app secret; verifies `X-Hub-Signature-256` on Meta's inbound POSTs. Unset along with the shared secret = endpoint disabled |
   | `TURNOS_WA_NUMERO` | public display number for wa.me links |

   Coolify gotcha: env rows can silently duplicate; PATCH updates one row
   and leaves the twin stale. Dedupe by uuid with read-back — recipe in
   [meta-cloud-api-reprovisioning.md](solutions/deploy/meta-cloud-api-reprovisioning.md).
6. **Smoke test (TEST tier)**: add the tester's own number to the
   dashboard recipient allowlist, message the test number first (opens the
   24h service window), confirm the round trip: inbound webhook logged →
   assistant reply delivered. `/admin` → Sistema → WhatsApp cluster shows
   provider, token/number presence, and webhook-secret status.
7. **Production cutover** (only after §4 gates): swap
   `META_WA_PHONE_NUMBER_ID` + token to the production number's, re-verify
   the webhook, re-run the smoke test against a staff phone, and only then
   announce the number to patients.

## 4. External gates — owned by the product owner / the clinic, not by code

None of these can be completed autonomously; each is a business/legal act:

- **Meta Business verification** of the operating legal entity (documents,
  domain, sometimes weeks of review). Blocks everything below.
- **WhatsApp Business production access** + display-name approval for the
  WABA.
- **Dedicated phone number** procurement and ownership decision (clinic's
  number vs operator's).
- **Message-template submission and approval** (§5) — requires the
  verified WABA.
- **Patient consent / opt-in policy** for business-initiated reminders
  (Meta requires opt-in for template sends; the clinic owns how consent is
  captured at booking).
- **Go-live decision**: pointing real patients at the number, and any
  spend commitment (per-delivered-template billing — rates and the
  cost analysis live in [whatsapp-migration.md](whatsapp-migration.md) §2).

## 5. Message templates — current gap and what production needs

Today every Meta send is a **free-text** message, valid only inside the
24-hour service window the patient opens by writing first. That is correct
for chat replies, and structurally insufficient for the rest:

| Flow | Today | Production needs |
|---|---|---|
| Assistant chat reply | free text, in-window | nothing — stays free text |
| Appointment confirmation (2026-10-21) | Utility template `turnos_confirmacion` (es_AR), behind `TURNOS_WA_NOTIFY` | the same template approved in the clinic's own WABA |
| Day-before reminder | Utility template `turnos_recordatorio` (es_AR, quick replies Sí/No), behind `TURNOS_WA_NOTIFY` | the same template approved in the clinic's own WABA |
| Rejection remedy | free text behind `TURNOS_WA_NOTIFY`; **delivers only if the patient wrote in the last 24h** | approved **Utility** template |

The template bodies (their `{{1}}…{{8}}` order is `send._wa_fields`) are in
[notificaciones-y-lista-espera.md](architecture/notificaciones-y-lista-espera.md);
a new WABA submits both under the same names before turning
`TURNOS_WA_NOTIFY` on.
| OTP (registration / password recovery) | not sent via Meta at all | approved **Authentication** template (fixed Meta-rendered body + copy-code button; custom wording is not allowed in that category — see [whatsapp-cloud-api-otp.md](solutions/deploy/whatsapp-cloud-api-otp.md)) |

Template facts that shape the work: templates are submitted per language
(`es_AR`), reviewed by Meta (minutes to days), can be rejected for
formatting/policy, and can be *paused* later by low quality ratings.
Authentication templates have a fixed body; Utility templates take
variables but must stay transactional or Meta reclassifies them as
Marketing at the higher rate.

## 6. Failure modes

Ordered by layer; the first question is always "which layer": webhook in,
transport out, Meta policy, or account status.

**Webhook / inbound**
- `TURNOS_WA_WEBHOOK_SECRET` and `META_WA_APP_SECRET` both unset → endpoint
  returns 404, Meta's URL verification fails, no inbound at all. This is by
  design (fail closed).
- Verify-token mismatch → 403 on the GET handshake; the dashboard shows
  "callback verification failed".
- `META_WA_APP_SECRET` missing or wrong (rotated in the dashboard, or copied
  from a different app) → the GET handshake still passes, so the dashboard
  looks healthy, but every inbound POST 401s and Meta retries then disables
  the subscription. A one-sided failure that reads like "patients write and
  nothing answers" — check the App secret row in Sistema → WhatsApp first.
- Secret rotated in one place only (app env vs Meta dashboard) → inbound
  POSTs rejected; Meta retries with backoff and eventually disables the
  subscription. Rotate both sides together.
- Webhook must answer 2xx fast; any non-2xx makes Meta retry (duplicate
  processing risk). The handler answers 200 first and processes
  best-effort — keep that contract.

**Transport / outbound**
- No provider configured → logged no-op, `False`. Expected in dev;
  in production it means the env rows are wrong (check Sistema cluster,
  then the Coolify duplicate-row gotcha).
- TEST tier: send to a number not on the allowlist → Graph error
  `131030` (recipient not in allowed list). Not a bug; add the number in
  the dashboard or move to production.
- Free text outside the 24h window → error `131047`
  (re-engagement required). This is the reminder gap in §5, not a
  transport failure.
- `code 100 / subcode 33` on the stored `phone_number_id` → the test
  assets were re-provisioned; run the
  [re-provisioning recipe](solutions/deploy/meta-cloud-api-reprovisioning.md).
- Token expired (temporary 24h token, or a system-user token revoked by a
  password/2FA event) → `190` auth errors on every send; inbound keeps
  working (webhook doesn't need the token) — a one-sided failure where
  patients write and nothing answers. Alert on send-failure rate, not just
  webhook liveness.
- Rate/messaging limits: production numbers start at 250 unique recipients
  per 24h for business-initiated conversations and scale with quality
  (1k → 10k → 100k). Chat replies in-window are not capped by this.

**Policy / account**
- Template paused or rejected after approval (quality rating drop) →
  business-initiated sends fail while chat replies still work.
- Number quality rating degraded (user blocks/reports) → messaging-limit
  downgrade; the fix is consent hygiene, not code.
- Business verification revoked/expired documents → WABA restricted;
  everything stops. Dashboard-only recovery.

**App-level**
- Demo mode on → all WhatsApp output blocked by design (`[otp_send] modo
  demo` in logs).
- Double-star Markdown or raw headings in outbound text → formatting bug
  in a caller; fix must go through `_markdown_a_whatsapp`, the single
  choke point.
- WAHA silently active in production → **impossible since the 2026-08-14
  contract**: auto-detect never selects WAHA, and a non-loopback
  `OTP_WAHA_URL` sends nothing even with `TURNOS_WA_PROVIDER=waha` pinned.
  If Sistema shows WAHA active, the URL is loopback — someone pointed dev
  config at prod; check `OTP_WAHA_URL` and the pin per §2.

## 7. Boundaries of this document

- It changes no behavior; it documents `main` as of 2026-08-14.
- It does not authorize contacting real patients, activating production
  access, or exposing credentials (tokens live only in the secret store;
  logs never print tokens, and Meta-path logs mask phones — the loopback
  WAHA dev path still logs destinations unmasked, as §1 notes).
- The Meta-only-production / fail-closed *code* slice **shipped 2026-08-14**
  (commit `6c34573e`: WAHA restricted to explicit loopback dev,
  `tests/test_transport_contract.py`, OTP over Meta refused). The OTP
  Authentication-template flow it deferred is still unbuilt and must not
  harvest the stale `codex/whatsapp-cloud-otp` branch.
