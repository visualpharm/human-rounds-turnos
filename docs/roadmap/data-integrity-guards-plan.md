# Tier 1.4 — data-integrity guards (design)

Audit item 3.0, hoisted into Tier 1 (races exist in live prod). Recon 2026-08-05
confirmed every cited gap; facts below are verified against the code, not the audit.
Statuses are feminine: `pendiente / aprobada / rechazada / cancelada`. Doctor column:
`assigned_doctor_id`; datetime: `appointment_at`. Migrations = `migrations/NNN_name.py`
with `up(cur)`, tracked in `schema_migrations`, applied by `apply_pending()`.

## Constraint the audit missed
**Sobreturno (deliberate overbooking) is a feature** — the HSI conflict resolver and
admin actions place a second appointment at an occupied time on purpose. A hard unique
index on (doctor, slot) would break it. Design: new column
`booking_requests.sobreturno boolean NOT NULL DEFAULT false`; every deliberate
overbook path sets it true; the unique index exempts those rows.

## Migration (one new `migrations/NNN_booking_integrity.py`)
1. `ALTER TABLE booking_requests ADD COLUMN sobreturno boolean NOT NULL DEFAULT false`.
2. Backfill: for existing duplicate groups on `(assigned_doctor_id, appointment_at)`
   with `status='aprobada'`, keep the earliest row as-is and mark the rest
   `sobreturno=true` (prod data already contains legit overbooks; index must not fail).
3. `CREATE UNIQUE INDEX booking_requests_doctor_slot_uniq ON booking_requests
   (assigned_doctor_id, appointment_at) WHERE status='aprobada' AND NOT sobreturno
   AND assigned_doctor_id IS NOT NULL AND appointment_at IS NOT NULL`.
4. `appointment_offers`: partial unique index on the live offer —
   `(service_id, center_slug, slot_at) WHERE estado='ofrecida'` (verify exact column
   names in `db.py:477` before writing).
5. `booking_requests.idem_key text` + partial unique index
   `WHERE idem_key IS NOT NULL`.

## Code changes (each guard reuses a pattern already in the repo)
1. **Approve/reject status-CAS** — `db.approve` (`db.py:1697`) and the reject path:
   `UPDATE … WHERE id=%s AND status='pendiente' RETURNING id`; zero rows → caller
   returns "ya procesada" conflict, no side effects (no HSI enqueue, no email).
   Pattern: assign-doctor CAS at `db.py:1197`.
2. **Doctor/slot collision** — keep the existing `_doctor_appointment_busy` SELECT as
   the UX pre-check; the real guard is the new unique index: `db.approve` catches
   UniqueViolation → conflict result → surfaced via the existing soluciones path
   (front desk sees "ese horario acaba de ocuparse"). Front desk keeps its flow
   (create → approve) — the index closes its race too. **Implemented, not the
   deviation this section originally proposed**: the front desk (`POST
   /api/admin/appointments`, `admin_appointments.py:342-379`) DOES route through
   `slot_holds.try_take` — the same atomic hold primitive the patient wizard uses —
   to close the window against a patient holding that exact slot live in the wizard;
   `slot_holds.taken()`'s 3s cache isn't enough for that case. Fail-closed: any
   `slot_holds`/DB hiccup is treated as "couldn't take it", never as silent success
   (mirrors `booking_requests._create`'s item 7 fix). Deliberate sobreturno call sites
   (HSI resolver "Sobreturno" action, any admin overbook) must set `sobreturno=true`
   on the row.
3. **Booking limit under lock** — new `db.py` function that, in ONE transaction:
   `SELECT pg_advisory_xact_lock(hashtext('booklimit:'||dni||':'||service_id))` →
   count active (`db.py:1126` SQL) → insert. `booking_requests._create` (~:1213) and
   the other create site (~:1122) switch to it. Enforced only on the patient's own
   POST, never the front desk (product rule).
4. **Confirmá idempotency** — client already holds `hold_token`; send it with
   `POST /api/requests`; insert with `idem_key = hold_token` via
   `ON CONFLICT (idem_key) DO NOTHING` + fetch-existing → repeat submit returns the
   SAME request (mirror the chat dedup at `chat_tools.py:773`). Missing/legacy
   clients (no token) → idem_key NULL, behavior unchanged.
5. **Reminder claim-CAS** — `reminders.tick()` (`reminders.py:44`): claim BEFORE send:
   `UPDATE booking_requests SET reminder_at=now() WHERE id=%s AND reminder_at IS NULL
   RETURNING id`; send only if claimed. Crash after claim = no duplicate (one lost
   reminder is acceptable; today's shape can double-send).
6. **Waitlist single live offer** — `_create_offer` inserts with
   `ON CONFLICT DO NOTHING` against the new partial index; zero rows → another worker
   already offered this slot → skip. Keep `release_slot`'s existing pre-check as fast
   path.
7. **Holds fail CLOSED** — `db.hold_acquire` (`db.py:2410`): on `DbUnavailable` return
   False (not True); `booking_requests.py:~1183`: exception during `try_take` → treat
   as NOT acquired → patient gets the retry solución. Rationale: if the DB is down the
   booking can't persist anyway; failing open just silently disables double-booking
   protection.
8. **Migration runner cross-process lock** — in `apply_pending()`: take
   `pg_advisory_lock(hashtext('turnos:migrations'))` on the connection for the whole
   run (the existing `threading.Lock` at `db.py:34` only covers one process).

## Tests (house pattern: `threading.Barrier` + N threads, as in
`test/test_slot_holds.py:172` `test_carrera_un_solo_ganador`; DB fixture:
`test/_dbtest.py` `PerModuleDb`)
- approve×2 same request concurrently → exactly one success + one conflict; email/HSI
  side effects fire once (assert via fake notifications).
- two pendientes, same doctor+slot, approved concurrently → one `aprobada`, one
  conflict.
- sobreturno path still inserts at an occupied time (index exemption works).
- limit=1: two concurrent creates same dni+service → one created.
- double-submit with same hold_token → one row, both responses reference it.
- two concurrent reminder ticks → notification sent once.
- two concurrent waitlist releases of the same slot → one live offer.
- `hold_acquire` with DB unreachable → False.

## Sequencing
Implement AFTER the Tier-1 hygiene commit lands (test suites must not run
concurrently — `PerModuleDb` names throwaway DBs per module, two simultaneous full
runs collide). Split across executors by file ownership: (a) migration + db.py,
(b) booking_requests.py + admin surface + service.js token pass-through,
(c) reminders.py + waitlist.py, (d) concurrent tests. (b)-(d) depend on (a).
