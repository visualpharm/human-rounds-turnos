---
title: Catalog served evidence ownership
scope: repo
area: dev
tags: [browser, acceptance, catalog]
created: 2026-09-10
refs: [catalog-served-journeys]
status: active
---

## Problem
The first 24-story catalog suite had real browser machinery but weak assertions,
duplicate per-width mutations, and missing screenshots when actions failed.

## Root cause
Browser contexts do not reset server-backed preferences or database content.
Registering a route does not prove interception. A visible form is not proof of
validation or persistence. A generic console-error gate rejects expected HTTP400
responses, while ignoring all such messages would hide unrelated failures.

## Solution
Use width-specific mutation names and independent API readback, clear persisted
search intent before a filtering scenario, and close the login landing document
before fault interception. Observe the intercepted request and adverse state,
capture it, then release/reload and prove recovery. Classify an expected resource
error only after observing its exact response URL/status and visible feedback.
Capture failures before teardown; never turn diagnostic evidence into a pass.
Keep separate readable viewport images alongside full-page originals. Require
distinct assets for the no-match, filtered, and individual tab phases rather
than counting a final recovered state as evidence of those intermediate actions.

## How to apply
The managed `e2e-catalog-native-asgi` profile runs only the catalog suite against
disposable synthetic data. Its report checks exact IDs, all declared phases and
widths, nonempty screenshot/HTML/JSON assets, initialized observers and retained
CLS entries, geometry and exact served-module source bytes. Pure adversarial
report tests reject missing phases, stale source, uninitialized zero-CLS evidence
and failed actions. Root screenshot review and the full frozen gate remain
separate from this focused suite's green result.
