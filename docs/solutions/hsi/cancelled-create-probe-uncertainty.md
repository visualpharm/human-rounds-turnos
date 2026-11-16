---
title: Cancelled create needs a definitive remote probe
scope: repo
area: hsi
tags: [hsi, outbox, cancellation, crash-recovery]
created: 2026-09-25
refs: [tests/test_emr_bridge_conformance.py, tests/test_hsi_sync.py]
status: active
---

## Problem
A remote create can succeed while its response or local link is lost. If the
patient then cancels, replay sees an unlinked cancelled request. Confirming a
skip can leave the remote appointment live.

## Root cause
The cancelled-create branch used a missing adoptable probe result as proof of
no remote appointment. An unsupported probe, outage, malformed response, and a
definitive `found: false` all looked the same.

## Solution
First-lease cancelled creates still skip because no remote call preceded that
claim. Later leases require a definitive `found: false` to skip. A valid found
result is adopted and its cancel queued; every uncertain result remains in the
retryable outbox without resending the cancelled create.

## How to apply
Use the real disposable-Postgres and loopback-bridge proof in
`tests/test_emr_bridge_conformance.py`: remote accepted before local link,
cancellation, probe outage, then recovered probe and cancel. The unsupported
probe path is pinned in `tests/test_hsi_sync.py`. This original change did not
make adoption and cancel enqueue atomic; the subsequent
[acknowledgement fix](atomic-appointment-acknowledgement.md) now commits them
together. Remote cancellation still happens in a separate replay.
The subsequent [configuration uncertainty fix](unconfigured-intent-uncertainty.md)
also retains attempted creates when the connector becomes unavailable. This
original proof covers probe uncertainty with a selected, configured connector.

Independent validation: 13 bridge tests and 30 journal tests pass, including
seven malformed/unsupported probe cases. Restoring only the prior replay
function makes the outage/cancellation regression fail (`confirmed` vs `error`).
