---
title: Tier 5 catalog editors as direct screens
scope: repo
area: dev
tags: [tier5, admin, catalog, routing, manifest]
created: 2026-08-21
refs: [tier5-admin-knowledge-direct-screen.md, tier5-admin-prompts-direct-screen.md]
status: active
---

## Problem

The Services, Centers and Doctors editors only existed behind the legacy
`#services` / `#centers` / `#doctors` hashes (with Spanish aliases), so they had
no reload-safe direct URL. The turnos manifest also listed the three English
hash aliases as sections of their own, which duplicated every content entry
once the server-rendered sidebar started reading `admin_sections`.

## Solution

One shared screen module and template (`admin_panel/admin_content_screen.py`,
`templates/admin/content.html`) serve `/admin/services`, `/admin/centers` and
`/admin/doctors`, but the manifest names THREE distinct one-route `APIRouter`
objects (`services_screen_router` / `centers_screen_router` /
`doctors_screen_router`). `app_modules.app_router_entries()` mounts whatever
object a `screen.router_attr` names and never deduplicates it — a single shared
router listed three times would be included three times. Screen metadata lives
on the Spanish manifest ids (`servicios` / `centros` / `medicos`, the ids
admin.html's `#sec-*` containers and `data-nav` links already use) with English
URL paths; the English alias entries are gone from the manifest.

`admin-content.js` remains the shared renderer. In direct mode its item state is
the canonical query `?item=<id>` (`?item=new` for the blank form): replaceState
to canonical on load (including removing any stray legacy hash), pushState on
every in-screen link (ficha, Nuevo, crumb), popstate for Back/Forward; the
legacy hash state (including `/nuevo` and the Spanish aliases) is
byte-identical. Cross-editor links (a service's centre, a centre's doctors) go
through the one `itemHref()` helper, so they target the sibling direct screen
only when running direct. Services and Centers load only the shared catalog
cohort; the doctor-only payload (import map + calendar island, typeable combo,
schedule editor, doctor user panel, create-appointment helper with
search/dates) ships on `/admin/doctors` alone, in admin.html's own dependency
order. At <=900px the shared catalog table becomes labelled stacked rows; it
does not hide the primary links and actions behind horizontal scrolling.

## How to apply

When several legacy sections share one controller and one template, still
give each manifest `screen` its own router object — the shared module is fine,
the shared router is not. Keep the English hash aliases as a client-side
translation (`URL_TIPO`), never as manifest sections. A direct shell that used
to lean on `admin-app.js` only for `toast`/`showGate` gets a local `#toast`
(same control) and a `#content-status` boundary instead of the whole
controller.
