---
title: Browser connector tests must acknowledge the actual save
scope: repo
area: dev
tags: [browser, settings, async, regression]
created: 2026-09-05
refs: [test/e2e/hsi-disabled.spec.mjs]
status: active
---

## Problem
The HSI toggle browser journey reloaded before the settings controller finished
saving. A first test repair matched any settings GET, including an older page-load
request, and falsely attributed its old value to the just-completed save.

## Root cause
Checkbox visibility is not save readiness. The real controller performs POST,
GET, rerender, then displays its saved status. A matching response URL alone does
not establish which operation produced that response.

## Solution
Observe the actual checkbox POST and verify its body and status. Wait for the
controller's enabled checkbox, requested checked state, and saved status. Only
then perform an independent no-store GET and assert the persisted value before
reloading. Keep both navigation and direct-screen on/off assertions.
Require the native POST schema's explicit `ok: true` plus its group result;
HTTP 200 alone is not acknowledgement. A malformed connector POST must not
start a GET and turn an old settings snapshot into false save confirmation.
Saving Location also preserves the selected `PACK_CHAIN`: its options must
reflect the active chain before any unrelated identity field is edited.

## How to apply
Run the managed e2e-asgi profile against synthetic data, never enable the live
connector for this proof. The corrected full run passed 184 browser tests with
8 declared skips. Do not replace persistence checks with delays or remove the
navigation assertion when a save sequence races.

## 2026-10-15: cold reload, actual acknowledgement and controller ownership

`test/e2e/admin-forms-roundtrip.spec.mjs` drives the existing admin forms
against isolated synthetic PostgreSQL data. Observe each actual mutation before
acting, then wait for the controller's completed state before cold reload.
Native automatic navigation needs its navigation promise registered before the
click. Read response bodies only when assertions need them: an automatic page
change can retire the browser's response-body handle after a successful write.

Validate acknowledgement against the backend's actual shape. Wiki categories
deliberately omit `content`; articles and pages must return it. Identity, type,
title and `ok` checks still apply to all three.

SSR doctor onboarding stays inert until its controller mounts. Keep its Reload
recovery outside that inert subtree, and leave missing-data/read-only screens
usable. Programmatic file uploads bypass inert, so the browser test must wait
for `aria-busy="false"` after every onboarding navigation.

A pending write also owns temporary disabled controls. Dispose must restore
the exact previous pending attribute and disabled states synchronously, before
another mount claims the same DOM. Restore each reservation once, so the old
request's late `finally` cannot unlock a new controller's pending write.
The browser test delays acknowledgement of a real server write across
synthetic persisted page lifecycle events; the runtime regression separately
holds both old and new responses and preserves an initially disabled control.
This proves synthetic remount ownership, not an actual browser BFCache restore.

An independent critic also found the doctor's Queue interview toggle accepted
HTTP errors, empty objects and malformed JSON as success. Require HTTP success
and literal `ok: true` before toast or refresh. The same sweep found Users'
passkey reset trusted the body without checking HTTP status; that path now uses
the same strict acknowledgement, with the other Users mutation acknowledgements
normalized to the explicit boolean contract. Browser failure cases include
HTTP500 with `ok:true` and HTTP200 with `ok:"true"`; real on/off toggles and
administrative passkey reset are independently read after reload.
The Users passkey GET also rejects failed or malformed reads instead of
claiming that no keys exist; its existing query-error state remains visible.

Persistence assertions need a changed baseline. The Catalog portrait case first
removes the Account upload and proves `has_upload=false`, then uploads through
Catalog and proves `true` after reload. The embedded-patient booking case reads
the created request independently by its acknowledged ID and checks patient,
service, assigned doctor (including no assignment) and slot against the actual
POST, then opens the saved row on the cold-loaded Queue.

## Users view ownership

Guard the whole direct Users view, including initial reads, passkey queries,
reset, Save, Deactivate, session confirmation and delayed clipboard notices.
An abandoned action must not toast, reveal generated credentials, navigate or
paint errors over a different user. Recheck its route predicate after JSON and
forced refresh, and prevent retired reads from publishing to shared caches.
Writes may invalidate cached data without publishing retired UI effects.
Invalidate the route generation on pagehide; persisted pageshow opens a fresh
owner. Native browser regressions cover late success/failure for all three
mutations across route replacement and synthetic lifecycle restoration, stale
list200/401 and clipboard completion. These synthetic responses prove ownership;
the separate real database browser suite proves durable mutations.

Delegated list sorting belongs on the replaceable table, not its reused section.
Otherwise each list → editor → list adds another handler and one click toggles
sorting twice. Guard redraw, column repaint and detached filter callbacks with
the same route predicate. The browser regression first reproduced the double
toggle, then checks click, Enter, Space and a retired filter after saving.

Stage-specific scheduling tests also hold mutation JSON parsing, confirmed
401/whoami expiry, 403/2fa_requerido parsing and the forced refresh after Save,
Deactivate or invitation. Include current-owner redirect positive controls.
Use the actual session error markers: status401 with an unrelated body never
exercises expiry. These selected boundaries supplement late-response cases;
they are not a proof of every possible asynchronous interleaving.

## Complete browser-gate follow-up

The complete frozen gate exposed one target-size defect and four stale test
setups. Consultation now applies its existing 44px disclosure style to every
summary in that surface, including conditional nested summaries; the reviewed
profile checkbox reuses the existing picker-row label. Both geometry suites
also cover 834px and1440px alongside their earlier widths.

Appearance has three branding inputs and an intentionally locked voice-model
span. Check their exact identities and readable type rather than expecting a
fourth input. The own-agenda overbooking case needs a fresh synthetic center and
doctor: the seed doctor inherits a service-owned agenda and correctly has no
per-doctor overbooking control. Observe the exact write's literal acknowledgement
and completed save status before cold reload. Keep the service-owned path intact.

Date formatter acceptance targets the current date-label element. Delayed-i18n
fixtures must serve and delay both the fingerprinted entry and the relative
module import used by Preferences; a missing dependency is not localisation
behavior. Preserve the English metadata/title assertions.

Before full-page captures, reset scroll and allow two animation frames. A failed
click on the inert SSR form can still scroll to its target; capturing at that
offset strands the sticky header in the full-page image. Separate viewport and
geometry checks confirmed no actual Doctor-field overlap.
The deliberate failed-import check waits for DOMContentLoaded and the explicit
recovery/ready controls; a failed resource can leave the global load event
pending. Expanded profile controls are exercised before Consultation geometry
and captures so their actual interactive targets are included.

Wiki creation also needs initial-read readiness. A held real GET reproduced a
new category draft disappearing when the late list response rendered the editor.
The read now owns `inert` and `aria-busy` on the section, restores their exact
previous values synchronously on destroy, and releases once before rendering.
Lifecycle, read sequence and connected-root checks guard publication and the
shared session boundary; an old finally cannot unlock a remounted read.
The browser regression observes the retired fetch reaching its consumer rather
than waiting for an unread retired response body to finish. Both held reads
still use the real server response, with no substituted acknowledgement.

A Wiki read has an eight-second deadline and a real Reload control for timeout,
failed HTTP, or a successful response without an `items` array. A malformed
HTTP200 must not claim the Wiki is empty. Timeout retires that owner before a
late response or session confirmation can publish. Error and destroy also
release the Wiki dialog's covered elements, key listener and body scroll lock.
Phone browser checks cover focus entry after a held read, Tab containment,
destroy/remount cleanup and Escape returning to the deep-linked row. Other
session/read-sequence permutations remain source-reviewed rather than claimed
as browser coverage.

Releasing a read's temporary UI lock is different from retiring its ownership.
Keep the owner current through rendering so a render exception still reaches
recovery; retire in finally or synchronously on destroy/supersession. Failed
reads clear the cached item list so a remount fetches fresh data rather than
reusing malformed items. A real response with a malformed selected article
reproduced this boundary. Phone focus containment includes native summaries:
Tab from Save must reach More actions, Enter opens its menu, and the actual
menu action is the last tabbable control while open. Check both wrap directions,
not merely that focus stays somewhere inside the editor.
