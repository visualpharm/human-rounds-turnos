# Durable bidirectional HSI synchronization

HSI is the clinical source of record. Sana keeps a durable transport journal and
read model; it does not use last-writer-wins or treat a local approval as proof
that HSI accepted the appointment.

## Reliable transport

- Every Sana intent enters `hsi_outbox` before network I/O.
- Initial approval first persists the request as HSI-pending, enqueues the
  create intent, claims that exact row, and only then calls HSI. A successful
  synchronous approval therefore has the same journal evidence as a replay;
  startup backfill adopts the pending decision if the process dies between the
  local decision and enqueue.
- Idempotency keys hash a canonical payload. Appointment datetimes are converted
  to `America/Argentina/Buenos_Aires` before hashing, so equivalent offsets do
  not create a second intent.
- States are explicit: `pending`, `sending`, `confirmed`, `error`, `conflict`.
- A `sending` row has a lease. An expired lease returns to the claimable set after
  a process crash.
- Network errors use bounded backoff. Impossible diary, professional, or exact
  slot mappings become durable conflicts instead of retrying forever.
- HSI ids are converted to strings at the journal boundary because Sana stores
  them in text columns even when HSI returns numbers.

## Exactly-once effect

HSI does not expose ETags or client idempotency keys. Sana therefore combines a
unique local intent key with a remote read before or after every mutation:

1. Claim one intent with a lease.
2. Read the appointment checkpoint and reject drift as a conflict.
3. Apply the supported HSI operation.
4. Read HSI's confirmed result.
5. Persist the HSI time, ids, checkpoint, and mirror.
6. Mark the outbox row confirmed.

If Sana crashes after step 3, the retry detects the already-created patient and
appointment or the already-applied state and confirms the same journal row.

## Inbound reconciliation

`hsi_inbox` deduplicates remote observations. Mirrors use `IS DISTINCT FROM` on
their current payload, not inbox novelty alone: a diary changed from A to B and
later restored to A must still update the B mirror.

When one HSI appointment maps to one Sana request and there is no local intent in
flight, HSI's confirmed time updates Sana. Concurrent changes, remote
cancellation, and duplicate local links become visible conflicts. Reconciliation
never picks an ambiguous row silently.

HSI can remove a cancelled appointment from the diary response; disappearance
after the verified cancellation endpoint is a confirmed cancel, not a false
`hsi_state_change_not_confirmed` conflict. Conversely, a concurrent reprogram
can move an appointment away from both the saved checkpoint date and Sana's
requested date. Before calling it missing, the client scans the bounded HSI
diary window by appointment id and records `hsi_checkpoint_drift` with the
actual remote appointment payload.

The current HSI role cannot enumerate every patient globally. Sana polls known
patient ids and learns patients through appointments. A patient who has never
had an appointment and was never mirrored requires a supported HSI patient feed
or audit permission.

## Verification

The 2026-07-18/19 controlled runs stopped HSI, queued create/cancel/reassign
intents, restarted Sana while still offline, restored HSI, and waited for
automatic replay. Every operation confirmed, no duplicate live appointment
link remained, direct HSI patient/appointment/schedule changes reconciled, and
a concurrent third-date edit produced a linked `hsi_checkpoint_drift` before
HSI's result was explicitly accepted as source of truth.

Reference-deployment run evidence is retained in the private operations archive.
and [admin + HSI hardening](../../usability-study/run-2026-07-18-admin-hsi-2h/run-log.md).
