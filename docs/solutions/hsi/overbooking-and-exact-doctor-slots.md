# HSI overbooking capacity and exact assigned-doctor slots

## Problem

Two independent HSI defaults can invalidate Sana's local scheduling decision:

1. `overturn:true` is rejected when the matching `diary_opening_hours` row has
   `overturn_count=0`.
2. HSI can snap a normal request to the next free agenda boundary. If Sana
   already collision-checked a named doctor at the requested time, persisting
   the snapped time can create a duplicate local doctor slot.

## Solution

- `HsiClient.create_diary` sends an explicit `overturnCount`; the demo
  Cardiología diary uses 2 and the idempotent seed repairs existing rows.
- Explicit Sana overbooks remain exact and carry `overturn:true`.
- A named professional makes `push_turno` call HSI in exact-slot mode. HSI must
  accept the operator-approved boundary or the handoff fails closed and remains
  recoverable from the admin queue. Unassigned flows may retain their existing
  snap-forward behavior because they do not bypass a named-doctor collision
  decision.
- Sana validates a named doctor's own recurring agenda, full-day closures,
  partial blocks, minute boundary, collision, and configured overbook maximum
  before approval. A free-text doctor without a catalog agenda fails closed.
- Patient reprogramming keeps the assigned doctor. The slot API filters to that
  doctor's schedule and the POST repeats the same server-side validation; the
  generic service agenda cannot offer or smuggle a different boundary.
- Both manual retry and the background HSI-resync worker preserve the stored
  `sobreturno` flag; otherwise a later recovery would retry an authorized
  overbook as an ordinary occupied slot.

## Verification

One regular and 2 authorized overbooks were created at Friday 24 July 2026,
08:00 in the Cardiología HSI diary. A third Sana request remained pending with
the configured-capacity message and created no HSI appointment. Browser checks
also rejected an off-boundary time, a conference day, and a partial block, then
showed only the assigned doctor's valid reprogramming slots. Contract tests
cover all of those rules plus diary payload capacity and named-professional
routing.
