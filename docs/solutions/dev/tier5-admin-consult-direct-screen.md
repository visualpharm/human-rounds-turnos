---
title: Tier 5 consultation direct screen
scope: repo
area: dev
tags: [tier5, admin, intake, routing]
created: 2026-08-21
refs: []
status: active
---

## Problem

Direct Queue and My patients screens linked a doctor into the legacy
`#consulta/<id>` hash. The direct shell did not load the consultation
controller, so Queue could become blank instead of showing the consultation.

## Root cause

`consulta` was a bare intake manifest section with no server-routed screen,
although its controller and API already formed a self-contained workspace.

## Solution

Give the manifest entry a `/admin/consult` screen router and render the shared
admin shell with the unchanged consultation controller. Keep the legacy hash
route, and use `?interview=<id>` only in direct mode. Copy the consult API's
`permitir_medico=True` gate, while leaving interview reads and doctor scoping
in `consult_admin.py`.

## How to apply

For a direct clinical workspace, keep data authorization in its existing API,
load only its proven script dependencies, preserve the hash twin, and test the
role/module gate plus direct links from every direct caller.

## Verification

Revision `664cf0b6` passed both managed local topologies (legacy and ASGI),
including backend, frontend, route, cassette, and browser E2E gates. Production
and demo then finished deployments on that exact revision; both readiness
endpoints and smoke suites passed, while unauthenticated `/admin/consult`
continued to fail closed with 401. The durable, non-sensitive run record is
[deployment evidence for `664cf0b6`](../../pinamar/deployment-evidence-2026-08-21.md).
