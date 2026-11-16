---
title: Expired connector appointment snapshots
scope: repo
area: connectors
tags: [appointments, snapshots, availability, regression]
created: 2026-09-05
refs: [b96c6dc6, a286dce8]
status: active
---

## Problem
Public search offered past appointments from a successfully loaded connector
snapshot. A green HTTP response and correct date formatting did not make its
availability current.

## Root cause
The slot-source consumer trusted adapter dates. Separate doctor-next helpers
selected the earliest entry before checking whether it remained usable.

## Solution
Filter malformed, past-date and elapsed same-day timestamps at the generic
consumer boundary using the installation's ART clock. Copy adapter objects,
keep valid future entries even from an old snapshot, and recompute `proximo`
from accepted entries. An empty result follows the existing local-agenda path.
Doctor helpers apply the acceptor while scanning all entries, preserving
identity/center preference instead of hiding later valid appointments.

## How to apply
Use fixed-clock regressions for date/time boundaries, mixed past/future rows,
single-next legacy payloads, unchanged input objects and doctor matching.
Verify both rendered availability and no-store public API dates after the
exact tested revision is served. Tests: `test_connector_rewire.py`,
`test_msd_data.py`, `test_patient_site_router.py`; release a286dce8 passed the
full managed ladder and both deployment smoke checks. Snapshot age alone is
not a rejection policy, and no refresh or fabricated appointment is implied.
