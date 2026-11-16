---
title: Native photo viewer lifecycle and image fallback
scope: repo
area: dev
tags: [esm, photo, lifecycle, browser, fallback]
created: 2026-09-06
refs: ["the maintainer rows 1.1 and 4.1", "base 6af2eda6"]
status: active
---

## Problem
Moving the shared photo viewer exposed lost localization, scroll/focus lifetime
bugs, and upload previews that became dead controls when enhancement failed.

## Root cause
A classic document-wide handler concealed ownership. Uploaded previews are data
URLs, which Chromium cannot reliably open as top-level new-tab navigation.
Fixture-only geometry also missed overflowing actions on the real center page.

## Solution
The canonical module owns an idempotent mount/destroy lifecycle. Repeated open
does not overwrite prior page state; destroy closes first and makes old actions
inert. Optional translation cannot block mounting or mutate a destroyed owner.
Ordinary photos retain image anchors; uploaded data URLs retain native download.
The shared listing action pair wraps without splitting either individual link.

## How to apply
Run real uploaded-image failure cases and compare download bytes with the actual
converter output, not the original input PNG. Exercise real center markup at
360/390/768/1024/1280 before, during and after zoom; capture geometry on failure.
Use short synthetic token labels; the public-cut guard correctly rejects long
token-shaped assignments even inside tests. Never allowlist this fixture.
Keep source retirement checks supplementary to lifecycle/failure browser tests.
Validate exact shared-source edits before reversing a fingerprint in raw Account
proofs; never normalize arbitrary HTML or silently regenerate snapshots.
The separate availability inline viewer is not retired by this shared migration.
Live WebP responses also need explicit `image/webp`: minimal containers can lack
the host MIME database and return `application/octet-stream`, causing native
links to download. Test actual GET/HEAD bytes and headers with MIME guessing
unavailable, then verify the deployed fallback opens a decoded image. Do not
relax the ordinary-photo fallback into a download just to pass the browser gate.
Image links also need accessible names before any script runs. Viewer-only data
attributes do not name links: the center hero and gallery now use the center name
as image alt. Assert actual role/name lookup with the module loaded and rejected;
axe on the deployed page caught this even after ordinary click tests passed.
