---
title: Preparations browser evidence and column fitting
scope: repo
area: dev
tags: [browser, esm, cls, tables]
created: 2026-09-08
refs: [82fe90d5]
status: active
---

## Problem
Native Preparations passed runtime tests but overflowed on phones. A new
browser suite also erased already-delivered layout-shift evidence and raced
the request-interception callback when asserting a held refresh started.

## Root cause
The shared table's default considers widths <=1024 stacked, so it skips
column fitting. Preparations never supplied a stacked layout. Its declared
dropped-column classes also lacked cell-hiding CSS. The test used only
PerformanceObserver.takeRecords(), discarding earlier callback entries.

## Solution
Use the existing shared fit engine with apilado returning false and scoped
cell selectors for its dropped columns. Preserve observer callback entries,
combine pending records, calculate the largest CLS session window, and assert
the actual bound. Await the route handler's explicit interception promise,
not just the browser request event, before asserting its counter.

## How to apply
Keep failed browser artifacts. The first run had6PASS3FAIL; corrected native
entry and evidence produced10PASS and10 unique ES/EN captures. This is scoped
proof, not full acceptance: classic retirement, UI semantics, remaining
session/disposal scenarios and the frozen full ladder are still required.
Do not infer zero layout shift from an emptied observer queue. Pure negative
controls in layout-shift-evidence.test.mjs guard the scoring helper.
Preserve the current editor when a post-save GET fails; show the read error
inline so a successful mutation cannot strand the user. Assert a second save.
CLS boundaries are strict: exactly1000ms gap or5000ms duration starts a new
window. Do not accept an audit's threshold change without its source/control.
With display:none table cells, visible cells remap onto earlier col slots.
The first reveal-negativecontrol resized col4 but left the revealed cell0px;
size all cols in the mutant, require painted bounds and display:none for
the real hidden state. Corrected27browser cases pass, including restoration.
