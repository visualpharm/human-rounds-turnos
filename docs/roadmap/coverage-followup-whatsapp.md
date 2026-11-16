# Optional coverage follow-up — local design, inactive

Prepared against `78afa83a0abc5ba0be1c81172d88ceb80b913e79` on 2026-10-21. This document and its synthetic scenarios do not register a job, change a webhook, send a message, upload an image, or change provider configuration.

## Scope and existing behavior

After a successful booking, an optional coverage card can collect an obra social or prepaga and member number. Missing coverage must never block booking or care. A separate WhatsApp follow-up may eventually ask for those details or a credential photo, but only through the existing institutional transport and an authorized, verified contact. The local text-card slice is independent of this design.

Source inventory:

- `notifications/send.py:_destination_phone` extracts digits from the booking contact and, since 2026-10-27, honours an explicit `avisos.whatsapp = false`. It does not prove that contact ownership was verified.
- `notifications/send.py:_whatsapp_reminder` calls `otp_send.enviar_texto` for the appointment reminder. Do not reuse the appointment reminder's claim or completion flag for coverage.
- `notifications/reminders.py:tick` and `core/db.py:claim_reminder` demonstrate the current claim-before-send pattern. A crash after claim can lose one message, deliberately preferred over duplication.
- `modules/chat/whatsapp_chat.py:_normalizar_meta/_normalizar_waha` currently omit the incoming provider message ID. Deduplication must be added before any new coverage reply processing is connected.
- `notifications/otp_send.py:enviar_texto` returns a boolean; the Meta path discards the accepted message ID and converts timeout/error outcomes to `False`. A coverage sender needs a distinct accepted receipt, proven pre-send failure, and unknown outcome. Never treat a legacy `False` as proof that retry is safe.
- `modules/chat/whatsapp_chat.py:_procesar_mensaje` currently sends every attachment through referral analysis. Credential photos require an explicit separate path; they must never be passed to that parser by default.
- `cuentas/accounts.py:_norm_notifications` stores channel preferences. Missing preferences are not proof of coverage-follow-up consent.
- `core/db.py:user_by_phone` rejects ambiguous matches but a unique stored phone alone is not durable verification evidence. `user_by_handoff_phone` supports an existing authenticated web-to-WhatsApp grant; its validity and patient scope must be rechecked.
- The current WAHA downloader's diagnostic logging includes request headers and URLs. Do not inherit those log lines for credential media; exception paths require the same structured, content-free logging as normal paths.
- `docs/meta-whatsapp-adoption-runbook.md` and `docs/architecture/notificaciones-y-lista-espera.md` describe TEST/free-text transport and a pending Utility-template production path. This audit did not verify current production approval or send a probe.

## Proposed eligibility contract

Keep this feature off until the following facts are available, without interpreting absent data as approval:

1. The booking is still actionable, and the patient has neither supplied coverage nor explicitly selected no coverage. “Prefer later” is not permission to send a WhatsApp message.
2. A saved, explicit WhatsApp opt-in covers this purpose, has not been withdrawn, and belongs to the authenticated account responsible for this booking.
3. A still-valid verified contact binding belongs to that same account. Do not send to a phone supplied in the follow-up payload, a newly changed unverified phone, or a digit-only booking contact without proof. Existing authenticated handoff evidence may be reused within its existing scope and expiration; do not expand it.
4. The clinical patient is bound to the booking. Initial implementation is self-only. Dependent coverage remains ineligible until its storage and authorization contract is implemented; never copy a child's reply into the holder's coverage.
5. The institutional provider and the exact approved template/window policy are confirmed operational. If unknown, stop locally; never try free text to discover whether delivery works.
6. Recheck all facts immediately before an outbound claim. Booking cancellation, completed/no-coverage answer, withdrawal, reply, expiry or changed binding suppresses the send.

## State and delivery truth

Use a coverage-specific durable row, keyed by booking plus purpose/version, with a unique database constraint. Store opaque IDs and timestamps; do not put names, DNI, phone, member numbers, message text, media bytes or tokens in ordinary logs.

| State | Meaning | Permitted next action |
|---|---|---|
| `waiting` | Eligible candidate; nothing sent | Revalidate and atomically claim once |
| `claimed` | One worker owns the outbound attempt | Send only after final consent/binding check |
| `sent` | Provider returned an accepted message ID | Wait for reply; do not resend |
| `delivery_unknown` | Transport may have accepted the request | Reconcile using provider evidence; never blindly resend |
| `replied` | An authenticated, matching incoming message arrived | Stop reminders immediately; stage answer for confirmation |
| `completed` | Patient confirmed coverage or no coverage | No further reminders |
| `stopped` | Opt-out, cancellation, expiry, lost binding or unsupported patient | No send; restart only through a new explicit valid action |

Do not promise exactly-once delivery when the transport cannot provide it. Worker contention is protected by a DB claim. An uncertain network outcome is not a confirmed failure. Any retry after a proven pre-send failure must reuse the logical attempt identity and recheck consent. Do not reset an appointment reminder's state.

A late accepted receipt may attach delivery metadata but must not move `replied`, `completed` or `stopped` back to `sent`. A reply received during `claimed` or `delivery_unknown` is still authoritative and ends further follow-ups. A crash after provider acceptance but before local persistence remains uncertain; it must never re-arm the send automatically. Claim, reply, opt-out and acknowledgement updates need guarded transitions so concurrent events cannot undo a terminal decision.

Any authenticated reply to this follow-up stops its automated reminders, even if it is incomplete, says “later”, or includes an unreadable image. A reply to an unrelated appointment confirmation must not be consumed as coverage. Match provider reply context or an explicit active coverage conversation, account and booking; ambiguous context falls back to the existing authenticated web flow without exposing another patient's identity. Group messages, echoes, unsigned events and duplicate provider IDs are ignored. Opt-out applies even when the answer is incomplete.

Identity checks compare concrete account, booking and clinical-patient IDs, the grant owner and expiry, and the consent's purpose/version. A valid grant for a different account is insufficient, including the most recent phone handoff belonging to another account. Deduplicate authenticated provider events before downloading media, invoking OCR or producing a reply; a duplicate attachment may have no second side effect.

## Credential-photo draft path

This remains unimplemented. It requires a dedicated upload intent tied to an already authorized patient/booking, secure actual-byte decoding with existing size/resource limits, private local storage, and a documented deletion trigger. No public URL or referral-parser routing. Do not add a new AI vendor or send a real credential to any vendor while evaluating this feature.

OCR, if later enabled through an already authorized institutional path, returns suggestions only. Show insurer, member number and plan for patient confirmation; preserve leading zeros and canonical insurer identity. Never silently bind a different payer or update account identity from the image. Delete the original after the approved short review window; the duration and exceptional retention owner must be decided before activation. Tests use generated cards with fictional values.

## Activation checks still required

- Exact institutional provider account/template status and approved delivery route; do not infer these from configured secrets.
- Persisted purpose-specific consent and usable verified contact evidence, including withdrawal and changed-phone behavior.
- Durable coverage-only attempt state, unique claim, preserved provider IDs, inbound deduplication, and stop-on-reply semantics.
- Patient-scoped dependent coverage storage/authorization before enabling dependents.
- Separate private credential-photo handling, retention decision and authorized OCR route before accepting photos.

The accompanying JSON is an acceptance fixture proposal, not proof that this runtime exists or passes tests.
