# Catalog served journeys

This is the acceptance matrix for the native, served `/admin/services`,
`/admin/centers`, and `/admin/doctors` catalog screens. Every journey uses the
disposable `example-town` ASGI fixture. The browser suite records each named
phase at 360, 390, 768, 1024, and 1280 pixels; widths are evidence dimensions,
not additional journeys.

| ID | Given | Action | Expected |
| --- | --- | --- | --- |
| S01 | An authenticated admin and the synthetic service catalog | Open the services list | A native table and its fingerprinted module are served |
| S02 | The services list | Search for the agenda-backed general-medicine service | Its row remains visible |
| S03 | A services search has no match | Clear the no-match control | The unfiltered table returns |
| S04 | An existing walk-in service | Open its editor and round-trip appointment/same-day choices | Unavailable properties including labels are absent; returning modes preserve choices and immediately restore the applicable agenda |
| S05 | An existing scheduled service with an agenda | Open its editor and reload | Capacity controls remain native and ready after reload |
| S06 | A new service editor | Submit its empty required name | It stays editable and reports a safe failed save |
| S07 | A new service editor | Create a synthetic walk-in service and reread the list | The persisted name is present after the real mutation |
| S08 | A real services API read is held | Release that same request | The list recovers through the real response without a replacement controller |
| C01 | An authenticated admin and the synthetic center catalog | Open the centers list | A native center table is served |
| C02 | The centers list | Open an existing center editor | Existing address and booking controls render |
| C03 | A center editor | Change and save its address, then reread | The changed address persists through the real API |
| C04 | A new center editor | Submit an empty required name | It remains an editable safe validation failure |
| C05 | A new center editor | Create a synthetic bookable center and reread | The new center is listed after the real mutation |
| C06 | A created synthetic center | Disable it and return to the list | The list owns navigation after the real delete mutation |
| C07 | A held real centers read | Release the held request | The native list paints only after its dependency settles |
| C08 | A rejected real centers read | Click the visible retry action without interception | A real read recovers the native list without browser reload |
| D01 | An authenticated admin and synthetic doctors | Open the doctors list | The native table and doctor-only resources load |
| D02 | The doctors list | Use a specialty filter then clear it | Filtered and recovered rows are owned by the same list |
| D03 | An existing doctor | Open schedule, calendar, and basic-information tabs | Each owned tab paints and the calendar uses its real API |
| D04 | An existing doctor editor | Reload after selecting the calendar | Reload returns to a ready owned editor, not stale tab DOM |
| D05 | A new doctor editor | Submit missing identity fields | It remains editable and reports a safe failed save |
| D06 | A new doctor editor | Create a synthetic doctor and reread | The persisted doctor is in the real list |
| D07 | The doctors appointment dependency is rejected | Keep an unsaved information edit and click calendar retry | A real read recovers the calendar without discarding the draft or remounting its sibling grid |
| D08 | A held real doctors read | Release it | The native list paints from the released real response |

Completion is deliberately fail-closed: every ID needs a distinct durable
record, all five viewport captures, HTML and geometry snapshots, initialized
layout-shift observation, completed assertions, nonempty assets, and no
unexpected runtime errors. Each phase retains a readable viewport image alongside
the original full-page image. No-match, filtered and individual doctor-tab states
are required before clearing or leaving them. Screenshot review remains a human
gate; generated contact sheets do not themselves constitute visual inspection.
