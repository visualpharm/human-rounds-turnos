# Compact admin actions, filters, and schedules

## Problem

At a 965 px admin viewport, the request decision controls wrapped into two uneven rows, editable values left large gaps before their validation, Services lacked its two operational filters, and imported doctor schedules repeated one identical row per weekday.

## Solution

- Keep approve and reject in one row while the card container is wider than 700 px. Use content-sized doctor, appointment, and rejection controls instead of stretching them.
- Estimate editable input width from proportional glyph classes rather than multiplying string length. Render field validation in the same inline-flex value group with an explicit 4 px gap.
- Add Services filters for `centro` and `modo`, using the same compact select language as the doctor list.
- Merge doctor schedule rows only when `desde`, `hasta`, `cada_min`, and every capacity value match. Union and weekday-sort their `dias`; preserve separate rows when any operational value differs.
- Make the selected calendar date independent of availability color with solid accent fill, white text, and a contrast ring.

## Verification

- 965 px: request actions share one line; sidebar is 136 px; field/status gap is 2 px; no horizontal overflow.
- 768 px and 390 px: sidebar becomes the burger header and each action branch stacks as a complete group; no horizontal overflow.
- Services Center filter returned only rows from the selected center.
- Diego Coronel's repeated weekday schedules collapsed into one row per distinct schedule/capacity combination.

## Gotcha

Do not group schedules by time alone. Two rows with identical hours but different web, reserved, or overbook capacity are operationally different and must remain separate.

## 2026-07-24 follow-up

- At `<=1024px`, remove the brand bar and use a fixed left-edge menu handle plus a full-height drawer. A separate scrim owns click-away closing; active links span the drawer with no side rounding.
- For an operator-created appointment, retain the form state, remove the dialog before starting the POST, and show `Turno creado. Guardando…`. On failure, rebuild the dialog from the retained state; a slot collision also refreshes availability.
- An empty schedule field shows a concrete input example. An unparseable value becomes a warning only on blur, not while typing.
- The day queue removes first/last-cell padding, runs full-bleed below 720 px, and keeps the two-line `Sobre / turno` badge beside the time.

Verified without horizontal overflow at 360, 768, 959, 1024, 1280, and 1440 px. The drawer closed from its scrim and preserved a square full-row active state.
