---
title: Patient roster intent must retire pending session checks before debounce
scope: repo
area: dev
tags: [patients, lifecycle, session, browser]
created: 2026-09-08
refs: [2026-09-08-human-rounds-native-general-patient-roster-lifecycle]
status: active
---

## Problem
Old search expiry could navigate away during the300ms interval after typing
a new search. Single-flight saves blocked duplicates internally but provided
no disabled in-flight feedback. Tablet filters overflowed by66–94px.

## Root cause
Request identity changed only when the debounced read started. A pending
whoami still owned navigation while the new input already expressed intent.
The toolbar inherited nowrap widths; phone-only CSS missed768/1024px.

## Solution
Input immediately aborts/retires old list and pagination tokens, while only
the new network read stays debounced. Mutations disable their captured buttons
and only that flight restores them. Locally owned styles wrap filters and
reserve the full search placeholder without changing shared Account assets.

## How to apply
Use actual shared session code with a held whoami and release it BEFORE
flushing the new debounce. The original candidate fails49/51 tests;
the corrected owner and style-lifetime suite passes52/52.
Browser tests reset real server preferences between contexts. A held request
must demonstrably start, and newer content must be distinguished from an
already-present matching row. For late-completion proof, explicitly disable
AbortSignal only in the synthetic read transport, keeping real fetch/modules.
Capture each locale/width independently with resource evidence, supported
layout-shift observer/session-window CLS and visible element bounds.
