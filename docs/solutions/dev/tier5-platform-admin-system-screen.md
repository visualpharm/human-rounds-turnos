---
title: Platform-core admin screens without a module manifest
scope: repo
area: dev
tags: [admin, routing, jinja, platform-core]
created: 2026-08-19
refs: []
status: active
---

## Problem

Module-owned direct admin screens get their path and router from
`module.json`. Platform sections such as System have no manifest, so copying
that mechanism would either invent a fake module or leave sidebar links on the
legacy hash route.

## Root cause

The platform section registry contained IDs and role policy, but no canonical
direct-screen path. `screen_nav.nav_items()` could therefore resolve direct
paths only for manifest dictionaries.

## Solution

Keep platform routing explicit in `routers/platform.py`. Record only the
ID-to-path relationship in `core.app_modules._PLATFORM_ADMIN_SCREEN_PATHS`,
and let the shared `screen_nav` helper consult it for bare platform entries.
The legacy payload remains bare strings, so `admin.html` behavior does not
change. The screen router renders only the authenticated shell; its controller
fetches the existing API after paint, so expensive status probes never run
during HTML rendering.

## How to apply

Shared navigation now lives physically in `core/screen_nav.py`. Keeping its
core-only implementation under `admin_panel` still made module-owned Chats
import a foreign screen package, failing the dependency-direction gate after
main integration. Move the implementation, update every consumer (including
`admin_navigation`), and leave no old file, alias, or relaxed import exception.
`test_admin_screen_nav.py` proves canonical ownership and actual served-sidebar
sentinel propagation; `test_domain_dependency_direction.py` remains unchanged.

For another platform-core direct screen, add its English path to the platform
path registry, mount its router in `routers/platform.py`, reuse
`templates/admin/base.html` and `screen_nav.nav_items()`, and preserve the
legacy hash controller. Pin admin role gating, no-store HTML, fingerprinted
assets, zero server-render probes, the composition inventory, and both direct
and legacy browser paths.
