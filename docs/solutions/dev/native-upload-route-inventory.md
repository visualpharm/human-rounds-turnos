---
title: Native upload aliases and the ordered route inventory
scope: repo
area: dev
tags: [asgi, modules, routing, tests]
created: 2026-09-05
refs: [f64b09fe, 0c4b11a7]
status: active
---

## Problem
Native upload HTTP tests passed, but the full backend composition guard failed.

## Root cause
The upload cutover deliberately added six exact raw-module aliases. The literal
router registers a slash twin for each alias, adding twelve ordered GET routes.
The composition fixture still described the route table before that cutover.

## Solution
Compare actual and pinned inventories before editing the fixture. This change
added exactly twelve routes, removed none, and preserved the order of every
existing route and all middleware. Add only those twelve literal entries.
Verify exact aliases redirect to current fingerprints and slash twins stay
no-store 404 responses, never an alternate source-serving route.

## How to apply
Run composition, native legacy-URL, and legacy-fallback-census suites together.
The census reads the fixture too: these twelve unique native rows increase
its composition, native, and unique-native counts by twelve; all other counts
stay unchanged. Do not regenerate an
entire route fixture to conceal unexplained changes. A green source subset is
not a substitute for the frozen candidate's full backend and browser ladder.

## Editorial retirement follow-up (2026-09-06)
Retiring the Knowledge root also removed `/admin-conocimiento.js` and its
slash twin. Exact comparison against `0d3c599e` proved only those two GET rows
disappeared: all remaining ordered routes and middleware were identical.
Delete only those two fixture objects; composition/native/unique-native census
counts each decrease by two. Keep real HTTP assertions for retired Spanish and
English URLs, query variants, no-store behavior and absence of redirects.
The first full candidate correctly rejected stale expectations; never restore
a dead alias or relax the census to make that gate green.
