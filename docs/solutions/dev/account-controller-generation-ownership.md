---
title: Account controller generation ownership
scope: repo
area: dev
tags: [native-modules, lifecycle, async, account]
created: 2026-09-07
refs: [2026-09-07-human-rounds-account-controller-lifecycle]
status: active
---

## Problem
Account logout/login could leave Requests blank, initial lab detail links lost
their report selection, and a pending lab-analysis retry duplicated history
listeners. Moving the same controller behind an ESM export did not fix these.

## Root cause
Script-exists loading confused asset lifetime with screen lifetime. Late imports
looked up current DOM IDs rather than retaining their original host. A single
mount-wide alive flag could not distinguish dashboard generations. Labs rendered
before its Listing dependency and installed history listeners on every refresh.

## Solution
The shell owns each dashboard generation and child disposer. Async completions
check the captured generation and host. Labs owns one history listener, invalidates
older pending renders, and resolves translated columns after dependency readiness.
Failed HTTP reads show an error rather than a false empty clinical history.

Treat unknown request data separately from a confirmed empty list. A failed
count lookup must not hide Requests navigation or show a zero badge. Embedded
Requests delegates authentication expiry to its Account owner; authenticating
inside the child alone can mix one person's requests with another's profile.
Session-check outages are recovery states, not evidence that a user logged out.
The same authentication boundary applies to Labs: a 401/403 delegates to the
Account owner, while 503/offline keeps the valid shell and shows a load error.
The stale response guard runs before invoking either callback. Real controller
red controls reproduced both unauthorized cases; real browser checks cover
both plus outage controls and Spanish login rendering. A DNI input is also part
of profile editing, so test the actual authentication form, not every `#sc-dni`.

A recovery anchor targeting the current URL with a fragment does not reload
the document. Keep its real URL but explicitly reload on activation, preserving
the deep link. Chromium confirmed the original same-fragment link made zero
new document requests. Test both the recovery text and its actual action.

## How to apply
Run the whole-module `test/account-native-lifecycle.test.mjs` and
`test/lab-results.test.mjs` controls. Require successful fresh content as well as
absence of stale writes: an empty detached host alone can produce a vacuous pass.
Test the actual emitted client manifest, not just the broader asset registry.
These Node DOM doubles do not prove browser usability, rendered-byte parity,
release authorization, or production behavior; those gates remain separate.
The `test/e2e/account-lifecycle.spec.mjs` suite exercises the actual emitted
bootstrap and injected network failures against a synthetic local server. Its
page-transition events test lifecycle handlers, not browser BFCache eligibility.

## Browser evidence and diagnostic limits

The real Back journey returned an authenticated Account with navigation type
`back_forward`, but Chromium reported no persisted page transition. It proves
Back navigation works in that run, not BFCache admission. Keep the synthetic
persisted-event tests as a separate lifecycle contract.

Capture full-page evidence only after scrolling to the top and two animation
frames. At a restored scroll offset Chromium can paint the sticky header in the
middle of a full-page capture. Check header/footer text, geometry and computed
visibility as well as the image before diagnosing missing product chrome.

The fixed `e2e-account-asgi` and `e2e-home-listing-asgi` managed profiles are
diagnostic subsets, not replacements for `local-ci-asgi`. They accept no caller
file selector, use DB-only credentials and fix ASGI/fake-AI mode themselves.
They do not expand the Claude hook's separate approved-profile whitelist.

An earlier full-suite Home delayed-Listing failure recorded an empty query
without an input event. The diagnostic run recorded focus, beforeinput and input
events and passed the unchanged query assertion. This does not establish the
earlier failure's cause or justify waiving it. Retain both runs and the browser
trace; a subsequent complete gate must still enforce the same assertion.

## System controller application

The same failure appeared in the classic System screen: deferred status fetched
the current host by ID and a confirmed session redirect fell through to a generic
error. The native module captures its host and badge, owns one AbortController,
and checks ownership before session classification and every subsequent write.
Its template owns an eight-second readiness deadline, disposal on pagehide,
remount on persisted pageshow, and real reload recovery even if imports fail.
Whole-module red/control tests reproduced both original defects. Actual browser
tests cover five widths, ordinary outages, shared 401/2FA navigation, held imports
and late responses. Explicitly await old delivery before asserting fresh content.
Retire the root asset and Spanish alias physically; a server-template-only native
module belongs in MIGRATED, not the broader emitted CLIENT_LOADED manifest.
