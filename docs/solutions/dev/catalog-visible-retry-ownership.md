---
title: Catalog visible retry ownership
scope: repo
area: dev
tags: [catalog, recovery, browser, i18n]
created: 2026-09-10
refs: [catalog-served-journeys]
status: active
---

## Problem
Catalog and doctor-calendar read failures asked users to retry but provided no
action. Browser reload could discard an unsaved doctor information edit.

## Root cause
Failure paths painted text only. The calendar result host and its grid are
siblings, so replacing host content does not require remounting the grid.
Spanish mode-off localization has an empty catalog and uses call-site fallbacks.

## Solution
Render a localized recovery button through existing owned delegation. Disable
it synchronously before awaiting route dependencies. Retry the calendar read in
its existing owner and retain the draft and sibling grid. Refuse stale or disposed
controls. Use the shared reload key with the established Spanish fallback and
verify both empty-catalog Spanish and populated English behavior.

## How to apply
The six actual-module Chromium controls cover repeated failure, rapid clicks,
navigation and disposal for both retry surfaces. C08 and D07 additionally prove
real served failure and recovery at five widths, a 44px target, and retained
unsaved doctor information. Capture the adverse state before retrying; a final
green screenshot alone cannot prove that recovery was usable.
