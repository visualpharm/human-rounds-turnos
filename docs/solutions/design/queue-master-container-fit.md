---
title: Measure queue content inside its actual pane
scope: repo
area: design
tags: [queue, responsive, container-query, browser-proof]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
The doctor master table needed 685px inside a 240–300px desktop pane.
Page overflow was zero while specialty, center and count columns were hidden.

## Root cause
The shared table's natural width was contained by its scroll wrapper. A
viewport-only mobile breakpoint did not describe the narrow desktop pane.

## Solution
Use the master's actual inline-size container to select its existing compact
table presentation. Keep every datum and the shared table runtime; wrap long
context, keep counts adjacent, and do not shrink fonts or hide overflow.
Remove redundant field labels and row dividers from the compact presentation.

## How to apply
The real-ASGI regression measures every visible cell against its host and
master text fragments against their cells, even when overflow is visible.
Include the plain SSR table during enhancement failure. Require nonempty
measured content; a next-day scenario must open its real unbooked-agenda action
before claiming a table fit check. The unchanged source failed normal/recovered
1024px and 1280px cases. The corrected five-width suite passes all 15 cases.
Retain failed reports and inspect rendered phone/desktop results. This bounded
fit proof is not full UI polish, actual BFCache admission, or release evidence.
