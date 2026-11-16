---
title: Delayed-module browser tests must own their navigation promise
scope: repo
area: dev
tags: [playwright, esm, verification]
created: 2026-09-05
refs: [23f30f65]
status: active
---

## Problem
The full candidate browser gate failed in the delayed Search journey with
`page.goto: Target page, context or browser has been closed`. The next
managed gate exposed the same failure pattern in the delayed Listing journey.

## Root cause
Each fixture started a `load` navigation promise but only awaited it after
its delayed-state assertions. Earlier teardown could close the page while
navigation remained outstanding, hiding the original assertion behind a
navigation rejection. That error alone does not prove a product regression.

## Solution
For these dynamically imported modules, await `domcontentloaded` immediately,
then wait for the held module request and assert the real early-input state.
Release the import and retain all existing visible-result, obsolete-script,
logging and responsive assertions. No production code or skip changed.
The corrected managed browser gate passed 198 tests with eight declared skips.

## How to apply
Do not mechanically apply this to a parser-blocking module or top-level await:
its DOMContentLoaded event may intentionally depend on the held response.
Such a fixture must observe navigation rejection immediately, await the
original promise on its success path, and settle it during cleanup without
swallowing a failed test. Keep the failed reports before rerunning a correction.

Login can restore a previously visited Reports screen. Before observing a new
cold-load journey, detach that authenticated document (for example, navigate
to `about:blank` while retaining cookies). Otherwise its pending boot requests
can be counted as requests from the deliberately held new document. Keep the
strict zero-before-release and positive-after-release assertions unchanged.

Staff Account's native cutover also exposed a dangling `waitForResponse` while
the test called a retired router global. Exercise the visible Save action and
observe its real unauthenticated POST inside `Promise.all`; keep exact 401,
direct-login URL and return-destination assertions. The six real auth journeys
pass without restoring a global facade or intercepting the authentication API.
