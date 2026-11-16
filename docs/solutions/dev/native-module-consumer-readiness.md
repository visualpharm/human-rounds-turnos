---
title: Native module migration must preserve consumer readiness
scope: repo
area: dev
tags: [esm, login, search, browser-tests]
created: 2026-09-05
refs: [6dfc7f10, 6cca4882, a63130f0]
status: active
---

## Problem
Physical Search/Charts/Login migration passed focused tests while missing
browser-global leakage and delayed-load behavior at real consumers.

## Root cause
A top-level variable in a classic inline script published a replacement
`window.Search`. Login's translation promise existed but was not consumed;
waiting for configuration only during queued-submit replay missed ordinary
submissions arriving after event binding but before the config response.

## Solution
The home controller owns its imported namespace in a local closure. The
standalone Login template prevents default form submission immediately,
before importing its controller, and shows a localized failed-import recovery.
The controller awaits translation and configuration readiness before building
the login payload or displaying feedback. Rejected translation loading
settles to existing fallback copy; server authentication remains unchanged.

## How to apply
The Plan controller showed another bypass: awaiting i18n before initial paint
did not protect a popstate or retained-tab click registered before readiness.
Guard every event entry point with live-owner AND ready state, including after
a rejected import; a later history event must not turn recovery into success.
The doctor screens also retained inline retry callbacks after disposal. A
response-time epoch check prevented repaint but still allowed an obsolete
button to start a network request. Guard the request entry point before any
loading DOM mutation or fetch. Both whole-module regression tests retain the
real registered retry callback, prove a mounted retry makes a second request,
then dispose and prove that callback cannot make a third request or repaint.
Column-picker callbacks need the same guard at the renderer entry point:
the shared picker can retain its registered callback after its old host is
replaced. A whole-module test must first prove the live callback repaints,
then retain that exact callback across disposal and prove replacement markup
is unchanged. Request cancellation alone cannot protect this synchronous path.
The whole-module Plan tests first reproduced three failures before correction.
Browser tests exercise held imports, history, page exit and actual recovery.

Hold each native dependency independently in a real browser. Type before
arrival, verify retained state after release, and exercise rejection/reload.
For Login, test both early queued submits and ordinary post-bind submits;
require English wrong-password/TOTP copy and a configured anti-bot token.
Assert absence of replacement globals, not merely the old `Sana*` name.
Preserve strict pageerror checks and serve actual native modules in fixtures.
Whole-page snapshot changes need exact asset-manifest delta proof before
updating pins; blanket normalization does not establish behavior parity.

Any change to `core/static_fingerprint.py` or a fingerprinted shared asset
must include `test_accounts_jinja.py` in the scoped gate, even if no account
template was edited. Hours and Week initially omitted it: their browser
journeys passed while six whole-page assertions failed because every account
page carries the shared manifest. Prove the exact eight Spanish/English
render deltas against the lane's base before updating pins, then repeat the
proof when merging concurrent manifest changes. A green component suite is
not evidence that the shared page contract is unchanged.
The actual pre-commit ladder now runs that fast contract. A synthetic hook
regression proves a red Account render blocks a commit even when frontend
and routes are green, and keeps the complete failing report for diagnosis.

The public chat widget has two independent delay layers. Prove them
separately: (1) `templates/_chat_bootstrap.html` importing
`platform/chat-widget.mjs` (generation-checked open/close on the painted
launcher — there is no `window.SanaChat` stub); (2) that module importing
`platform/chat-ui.mjs` (`pendingOpen` after evaluation). A rejected widget
import cannot paint an in-panel recovery; the bootstrap owns that line. A
rejected Chat UI still boots a degraded panel with the same open/close
contract and no composer. Stale callbacks must not reopen a closed instance.

Main integration can introduce new consumers after a physical move: Knowledge
and Chats still requested bare dates/format keys while the merged manifest
exposed only platform keys. No exception was thrown: the fallback silently
painted raw dates and omitted phone formatting. Scan newly added consumers,
then test their actual rendered values. `admin-chats-formatters.spec.mjs` also
serves deliberately regressed keys and proves the same visible-value gate fails.

Confirmation migration exposed a split-test blind spot: the real loader retained
an early decline as `initialAction=no`, but the controller accepted only `si`.
Both separate tests passed while the user's click disappeared. Keep explicit
queued clicks separate from affirmative-only URL auto intent, and exercise the
Python-rendered loader plus canonical controller through the observed POST.
The first captured action owns the translation wait; later clicks must neither
replace it nor follow an anchor. Bound optional i18n readiness and invalidate
pending loads/responses on page exit. A direct `destroy()` unit test alone is
not proof that the real page ever invokes that lifecycle boundary.
