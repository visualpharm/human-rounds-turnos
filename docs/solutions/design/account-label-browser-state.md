---
title: Account labels and real browser interaction state
scope: repo
area: design
tags: [accessibility, account, browser-tests]
created: 2026-09-05
refs: [5ebf3bec]
status: active
---

## Problem
Account fields had visible captions without programmatic label associations.
The initial real-browser regression then failed despite valid associations.

## Root cause
Shared field builders emitted unbound labels. The test also assumed each
field could be clicked independently: focusing Localidad opens its picker,
and profile contact fields stay hidden until their navigation tab is opened.

## Solution
Bind direct controls with label-for/id. Name the composite sex radiogroup
with aria-labelledby rather than pointing a label at a non-labelable div.
In the browser, assert accessible lookup and actual label-click focus,
dismiss the open picker with Escape while checking retained focus, then
open Contact through its real visible navigation before testing its fields.
Do not force-click through popups or unhide panels in test code.

## How to apply
Use test/e2e/account-delayed.spec.mjs for the full user interaction contract.
The repaired slice passed 163 browser tests with 8 declared skips and 1318
frontend tests. Before changing opaque Account page pins, prove all eight
ES/EN native responses reproduce the baseline after reversing only the
reviewed account-form module fingerprint; inspect any other delta first.

The deployed combo review at `fb87f270` produced another false alarm: its
expected option was sampled before ArrowDown changed the highlighted option.
Read the actual highlighted option immediately before Enter, then inspect the
canonical input property in that same combo, not an initial option or another
row. Pointer and keyboard traces at 360/390/430 proved synchronous bubbling
change, unchanged node identity and retained value after 130 ms. No runtime
patch was justified; instrument contradictory evidence before changing code.
