# Fail-closed outbound kill switch + proving business queues without activating them

**Date:** 2026-08-08 · **Branch:** `tier4/business-queue-activation-proof` → main `d5d086ce`

## What shipped
`core/outbound_guard.py` — `TURNOS_OUTBOUND_KILL=1` arms a socket-level
default-deny (`connect`/`connect_ex`/`sendto`/`sendmsg`) allowing only
AF_UNIX, the `DATABASE_URL` endpoint, and loopback strictly on the DB's
port(s). `worker.py` arms it before anything else and REFUSES to run
(exit 2) if arming fails. `test/test_business_queue_proof.py` proves the
real waitlist/reminders sweeps on the real worker process, with both
transports configured with dummy creds so sends are attempted and cut.

## Lessons (each cost a review finding or a real flake)

1. **A kill switch that "skips" is not a kill switch.** The soft
   per-transport switches (`MAIL_ENABLED=0`, unconfigured WA provider)
   fail OPEN — a new code path or a present credential walks past them.
   One boundary at the layer every HTTP client shares (`socket.socket`),
   plus refuse-to-run-if-unarmed, is what "fail closed" actually means.
2. **A blocked-transport test must configure the transport.** With no WA
   provider set, `_whatsapp_reminder` no-ops and the test "passes" while
   proving nothing (adversarial finding 2). Configure DUMMY credentials
   and assert the attempt log (`Meta WhatsApp FAILED`, `send FAILED`) AND
   the guard's own `BLOQUEADO` — the only line that proves the guard cut
   it, not a coincidental network error.
3. **Loopback is not safe by definition.** This repo's WAHA instances
   listen on loopback ports in real deployments — a loopback-wide
   allowance would wave a real WhatsApp send through (finding 3). Allow
   loopback only on the DB's own port(s).
4. **`sendmsg` is a connect-less egress path** — patching
   connect/connect_ex/sendto still let a UDP `sendmsg([data],[],0,addr)`
   out (finding 1, reproduced live). Patch it too.
5. **State the boundary honestly:** the guard neither observes nor
   controls native libpq or libc DNS (`getaddrinfo`) — allowed DB
   traffic flows through libpq as it must, and a refused send may still
   have resolved the destination's name. The no-message claim is scoped
   to the reviewed Python transport paths (urllib/http.client and the
   patched socket senders); it holds here because the business handlers
   have no subprocess and no native external-send path besides
   libpq-to-Postgres.
6. **Full-suite discovery leaks module-level env** — sibling suites
   export `MAIL_ENABLED=0` at import; a test that passes standalone then
   passes VACUOUSLY (send skipped before connect). Pin every env your
   assertion depends on with `patch.dict`, both directions.
7. **The reminders 09:00–21:00 window kills determinism at night** — no
   production seam needed: give the worker SUBPROCESS a computed
   `TURNOS_TIMEZONE=Etc/GMT±N` so "now" is always ~12:00 local, and seed
   "tomorrow" in that same zone (`tz.py` resolves at import, per process).
8. **Live evidence without touching the live thing:** run the EXACT
   deployed image as a one-shot container against a disposable Postgres
   on the same host, hermetic explicit env (`TURNOS_ENV_FILE=/dev/null`,
   `TURNOS_CONFIG_LOCK=1`, no `TURNOS_DEMO_MODE`). The real sweeps scan
   whole tables — pointing them at the standing demo DB would have
   mutated unrelated fictional rows. Destroying the disposable DB IS the
   synthetic-row cleanup.

## Where the proof lives
`test/test_business_queue_proof.py` (12 cases; harness shared via
`test/_workerproc.py`). Demo-host live run 2026-08-08: image
`d5d086ce…`, all 8 evidence items PASS, standing demo maintenance-only
throughout, prod runs no worker. Business queues remain OFF everywhere —
this closed the execution PROOF, not activation.
