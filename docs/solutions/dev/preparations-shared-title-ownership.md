---
title: Reserve shared title ownership before asynchronous rendering
scope: repo
area: dev
tags: [lifecycle, ownership, admin, browser]
created: 2026-09-08
refs: [82fe90d5]
status: active
---

## Problem
Preparations hid the shared heading and painted a second heading below an
empty title band. Borrowing the shared heading introduced a late-owner race.

## Root cause
Claiming ownership at render time lets a delayed older host steal the title
from a newer mount. Replacing a captured className also erases classes added
by another component. Same-host remount tests do not exercise this race.

## Solution
Pass the existing heading explicitly from the screen template. Reserve its
ownership token synchronously at mount, before imports and reads. Only that
token may write or restore its title. Preserve other components' classes and
remove only the class the controller added. Leave the shared header and
alerts attachment point intact; retain the standalone heading fallback.

## How to apply
Test separate hosts with an older held response, disposal in both orders,
and external class additions between renders. Root's two negative tests
reproduced both defects in GLM's first implementation. Final 52 pure tests
and 26 real-browser cases passed; ten ES/EN list and ten editor measurements
prove one visible shared heading. This is not full migration acceptance.
