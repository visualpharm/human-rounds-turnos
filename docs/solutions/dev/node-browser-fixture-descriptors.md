---
title: Browser fixtures across Node runtimes
scope: repo
area: dev
tags: [node, tests, globals]
created: 2026-09-05
refs: [ca7305a4]
status: active
---

## Problem
Chat behavioral tests passed in the shell but failed in managed full CI.

## Root cause
The shell used Node 20.19.2; managed CI used Node 26.3.0. The fixture
assigned to global navigator, which the newer runtime exposes as a
getter-only property. It also leaked requestAnimationFrame after teardown.

## Solution
Snapshot complete own-property descriptors for every overridden global,
install configurable fixture properties, then restore each exact descriptor
or remove a property absent before setup. A behavioral regression supplies
a getter-only navigator and checks complete restoration, including animation.

## How to apply
Run the real module tests with both the shell node and the managed profile's
actual binary. Do not disable built-in navigator or change the production
runtime to hide a fixture failure. This repair changes tests only; the full
combined release gate must still run on the new immutable candidate.

The Interviews story runner also restored `window` after checking only its
synchronous Node return. A still-pending preferences import then registered
`pagehide` on an undefined window. Await the real published dependencies and
preference hydration before fixture teardown; retain the synchronous-return
assertion before that wait. Do not add a production window guard or swallow
the rejected import to make the fixture green.

Preparations exposed a second fixture-only failure: a flat tag scanner left
every textarea value empty, so a real-module Save sent empty instruction
arrays. Hydrate textarea text and decode escaped control values exactly once;
pin escaped tags and entities with a fixture regression. Keep the original
round-trip assertion, and verify translation samples against shipped catalogs.
This does not make the scanner a browser: layout, real dependency readiness
and complete clinical-content round trips still require rendered browser tests.

Staff Account additionally required real fixture `appendChild`, direct-target
listener dispatch, and disposer cleanup before restoring globals. Preserve an
Error's stack in captured diagnostics: JSON-stringifying an Error as `{}` hid
the missing DOM method. A remount barrier must await the first observed request,
not an arbitrary number of event-loop ticks. Browser tests must await the new
mount's `ready`; old visible DOM is not evidence the new owner is ready.
