---
title: Catalog calendar state and request modal ownership
scope: repo
area: dev
tags: [catalog, calendar, modal, lifecycle, regression-tests]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
Navigating between doctors retained the prior patient's calendar query and
could leave an unclickable request modal over the replacement doctor.

## Root cause
Calendar filters/day/sorts lived for the whole document mount. The request
modal was appended to document.body but only its click listener belonged to
the route; route disposal removed the close handler, not the overlay.

## Solution
Clear calendar state at route release. Give each request modal one idempotent
route disposer that removes its DOM and both click/Escape listeners. Check the
captured route before opening a modal, including immediately resolved cached reads.

## How to apply
The actual-module Chromium tests in admin-create-appointment-native.test.mjs
navigate Ana to Bruno and require a fresh query plus visible Bruno data. Use
at least 11 synthetic rows to expose the real filter: fewer rows deliberately
hide that control, so a timeout is fixture failure, not causal evidence.
The two assertions failed on the pre-fix source and pass afterward; separate
manual close, Escape/reopen and disposal checks protect replacement ownership.
These are controller proofs, not full served-screen or deployment acceptance.
Related: [[catalog-response-body-ownership]], [[consultation-draft-mutation-ownership]].
