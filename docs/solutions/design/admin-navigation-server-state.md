---
title: Server-backed admin navigation state
scope: repo
area: design
tags: [admin, navigation, preferences, roles]
created: 2026-07-28
refs: []
status: active
---

## Problem

The admin always expanded every navigation group and reopened Solicitudes,
discarding each staff member's menu state and last screen between devices.

## Root cause

Navigation state lived only in static HTML and role-specific boot branches
forced a hash after login. Existing server preferences covered tables and
filters but not shared navigation chrome.

## Solution

Store `nav.groups` and `nav.last_screen` through `/api/admin/preferences`.
New accounts default to Atención open and the other groups closed. After role
visibility is applied, restore the last permitted screen only when the URL has
no valid explicit deep link. Flush debounced writes with `fetch(...,
keepalive:true)` on `pagehide`.

## How to apply

- Add stable `data-nav-group` keys to shared navigation groups.
- Apply role visibility before validating a saved screen.
- Let a valid URL hash win over server state.
- Route only the restored screen's owning module to avoid an intermediate
  Solicitudes frame.
- Verify restoration by opening `/admin` without a hash, not by reloading a URL
  that already contains the saved hash.
