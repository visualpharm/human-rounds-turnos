---
title: Appearance block-local invalid data
scope: repo
area: dev
tags: [appearance, lifecycle, validation]
created: 2026-09-07
refs: [2026-09-07-human-rounds-native-appearance-lifecycle]
status: active
---

## Problem
A successful HTTP response containing a malformed theme collection prevented
all Appearance blocks from rendering, including independently healthy ones.

## Root cause
The native staging controller accepted any object as a valid payload, then
called forEach on themes/modes/options without checking the collections.
JSON parsing success did not prove the renderer's input contract.

## Solution
Validate each option collection and its entries before publishing its block.
Reject arrays as top-level records. Invalid data becomes a block-local failure
with visible load recovery; the healthy branding and voice controls survive.

## How to apply
Import the actual controller in runtime tests. Exercise HTTP200 with an object
instead of a collection, null entries, missing labels, and array payloads.
Retain valid-response positive controls. The original implementation failed
the theme collection test; the corrected lifecycle suite passes37 tests under
Node20 and the related Node26 group passes61. This proves the bounded data
failure fix, not completed template cutover, browser acceptance or deployment.

## Follow-up lifecycle review
GLM independently identified reset drafts restoring the removed color and a
logo confirmation silently discarded during upload. Root reproduced both RED,
then fixed them: consume fresh branding state after reset (initial effective
color can itself be custom), replace only unchanged submitted drafts, and guard
logo ownership before confirmation. Late reset reads cannot publish. Voice
malformed acknowledgements and newer-edit loss also reproduced RED; validate
the server model and retain text entered after submission. Read cancellation
and input events now have whole-module tests from a separate GLM slice.
The other five mutation families also accepted truthy non-boolean `ok` values.
GLM wrote actual-module tests; root reproduced11PASS/10RED, then required
`ok === true` for theme, auth mode, anti-bot, logo upload and deletion.
The related101 runtime/consumer/divider tests pass. The first21 native browser
cases cover real local persistence, recovery, session and late-owner behavior;
visual corrections, full frozen verification and deployment remain open.

## Visual handoff verification
The later GLM UI attempt reached its turn cap with partial edits, six focused
failures and no catalog entries. Root corrected both language catalogs and
tested the actual checkbox change/rollback path instead of the retired cards.
Fresh full frontend1686PASS and native browser24PASS include real keyboard
setting persistence and actual logo upload, cancelled removal and deletion.
Ten complete ES/EN cold-load screenshots have no overflow, at least16px inline
gutters and observed CLS0 in this run. A computed-font assertion caught the
color input still inheriting13px; the control now explicitly uses16px.
The text-density heuristic still flags necessary option-label containers and
security/provider warnings. Those warnings were not deleted to make a gate
pass. This evidence does not certify full-product UX or deployment.

## Full-suite consumer correction
The first frozen full ladder ran5388 backend tests with one error: the theme
token mirror guard still opened the physically retired `admin-theme.js`.
Frontend1686, routes13, replay31, four cassette audits and browser391 passed
(99 backend external-pack skips and8 browser mode skips remain explicit).
The guard now checks the matching native swatch rule for each theme rather
than merely finding its color anywhere in the module. All25 theme tests pass.
No old controller or forwarding shim was restored. After a scoped copy pass
that retains availability/security facts and upload constraints,101 Node,
20 exact Account and24 native browser cases pass. The full ladder must run
again on the corrected frozen source; the earlier red run is not reclassified.
