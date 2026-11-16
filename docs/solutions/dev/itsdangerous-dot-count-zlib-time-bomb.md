# itsdangerous token asserts on dot count are time bombs (zlib flips by payload bytes)

**Date:** 2026-08-08 · **Branch:** `tier4/worker-registry-hardening` gate run → main `b516e43d`

## Symptom
`test_admin_auth_itsdangerous_dual_verify.py` failed ONCE in a full ASGI CI
lane (`token.count(".") == 2` got 3) yet passed solo, on main, and on every
re-run — looked like ASGI- or order-dependent state. It is neither.

## Root cause
`URLSafeTimedSerializer.dumps()` zlib-compresses the payload WHENEVER the
compressed form is shorter than the raw bytes, and marks that by prepending
`"."` — turning `payload.ts.sig` (2 dots) into `.payload.ts.sig` (3 dots).
For a small payload like `{"v","u","r","exp"}` the verdict sits on a
knife-edge decided by the EXACT bytes of `exp` (epoch + configured session
length). Reproduced deterministically: default/30-day session → 2 dots;
`TURNOS_ADMIN_SESSION_DAYS=365` → 3 dots. So the assert fails on certain
wall-clock seconds/dates and passes on others — a genuine time bomb, not
flaky infrastructure.

## Fix
Assert the CONTRACT, never the exact dot count: the new token is not the
legacy 1-dot format (`assertGreaterEqual(token.count("."), 2)`) AND
`_verify_token` accepts it. Production was never at risk: the dual-verify
dispatcher tries itsdangerous first regardless of shape, and `loads()`
handles its own compression transparently.

## Rule
Never assert equality on the serialized SHAPE of an itsdangerous token
(dot count, length, prefix). Assert what it means: verifies, and is
distinguishable from the format you migrated away from.
