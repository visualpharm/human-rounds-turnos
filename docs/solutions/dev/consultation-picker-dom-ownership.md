---
title: Consultation picker DOM ownership
scope: repo
area: dev
tags: [consultation, browser, focus, pickers]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-consultation-lifecycle]
status: active
---

## Problem
Synthetic object-based tests passed although typing Angina into the native
diagnosis search produced only A. Selecting a diagnosis changed its suggested
exam payload without checking the visible exam checkbox. Search also dropped
parent context for a specific diagnosis; exam category groups were missing.

## Root cause
Search replaced the entire host, detaching the focused input and remounting the
summary. Suggestion handlers updated only state. Flat filtering lost the catalog's
hierarchy and categories.

## Solution
Refresh only the adjacent result list; retain the actual focused input and summary
owner. Synchronize visible exam checkboxes after diagnosis suggestions, then let
manual deselection remain authoritative. Filter parent/child groups together and
group exams in an insertion-ordered Map (not prototype-sensitive object keys).
Custom chips and medication rows likewise update only their owned region.
An outside-host status element belongs to the same mount: clear its text and
hide it on route replacement and disposal so a later patient cannot inherit it.
Pending/error confirmation refreshes only its action region; full rerenders erase
unsubmitted custom inputs. Medication catalog defaults update existing input values,
not the tbody: replacing controls during blur breaks Tab into the dose field.

## How to apply
`node --test test/admin-consult-browser.test.mjs` uses the complete native module
in Chromium with synthetic API/dependency responses. Positive POST control passes;
pre-fix search and checkbox cases failed, then the parent-context test failed.
The focused suite now has36 passing cases and0skips, including held503 confirmation
and actual medication Tabfocus negative controls. The separate managed profile
`e2e-consultation-asgi` exercises real login/template/assets plus synthetic data.
Root screenshot review caught shared `.card` grid splitting pickers and a desktop
first-column width leaking into stacked phone rows despite green overflow checks.
Use scoped block layout and reset column width in the stacking media rule; assert
picker order, action position and rendered entered-text fit, not only box bounds.
The first19-case ASGI gate passed; persistence readback, richer content and complete
interactive-target coverage are still being hardened. No release claim follows.
