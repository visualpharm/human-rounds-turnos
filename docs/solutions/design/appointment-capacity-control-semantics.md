---
title: Appointment capacity controls must match runtime semantics
scope: repo
area: design
tags: [scheduling, forms, capacity]
created: 2026-09-04
status: active
---

## Problem
The doctor and service editors displayed three unexplained numeric quotas.
Renaming them by audience still left their counting unit ambiguous.

## Root cause
`cupos.web` is a boolean: one ordinary appointment at each generated time.
Legacy `cupos.sobreturnos` permits extra patients at the exact same minute through
staff booking paths. `cupos.reservado` is stored but has no runtime consumer
that enforces the displayed count.

## Solution
Render web booking as a checkbox with an explicit per-time-slot label.
The superseding product request uses an Overbooking checkbox beneath each
doctor schedule row. Enabling it reveals a percentage (20% initially), saved
as `cupos.overbooking_percent`; zero disables it. The percentage yields a
shared extra-appointment allowance for that schedule block on its ART date,
rounded down. It never creates additional public slots. A user-requested
accessible help disclosure explains the temporary-absence use case and unit.

Hide unused service overbooking and reserved controls, preserving stored
values. They have no enforcement consumer. The collector reads checkbox
checked state and includes percentage in the schedule-compaction identity.
An existing per-time allowance N becomes N times 100% on an explicit editor
save, preserving its total allowance while adopting block-wide allocation.

The limit must be enforced at shared persistence, not just an API precheck:
queue approval and direct staff creation converge there. Count and approve
under one transaction lock, covering concurrent requests at different times.
Include legacy appointment identities and center boundaries in the count.
Verify the remaining allowance returned to the calendar, not just creation.

## How to apply
Trace the runtime consumer before naming a setting. Name both the object
being counted and its scope. Keep closure descriptions beside their own
center's closures, and omit empty headings. Verify serialization as well
as populated and empty rendered states.
