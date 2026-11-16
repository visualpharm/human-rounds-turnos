---
title: "?v= bumps cascade through loader JS and regex-escaped test pins"
scope: repo
area: deploy
tags: [cache-busting, cloudflare, tests, vbump]
created: 2026-07-30
refs: [0a42086, 44ca571, 47b8513]
status: active
---

## Problem
After bumping `?v=` on changed JS files, the node suite fails and/or smoke_prod flags stale assets — repeatedly, across three landing rounds of the rename project.

## Root cause
Two reference classes that a plain `grep '?v=' *.html *.py` misses:
1. **Loader JS**: `chat-widget.js` and `my-requests.js` load `account-form.js?v=N` at runtime. Bumping account-form changes THEIR bytes → they need their own bump, recursively.
2. **Test pins**: `test/*.test.mjs` assert versions in regex-escaped form (`/admin-content\.js\?v=38/`) — invisible to a literal-string search/replace, and sed needs the escaped pattern (`admin-content\\\.js\\\?v=38`).

## Solution
When bumping asset X from vN to vN+1:
1. Sweep `--include='*.py' --include='*.html' --include='*.mjs'` AND `--include='*.js'` for `X.js?v=` — the JS hits are loaders.
2. Any loader whose bytes changed gets its own bump; repeat until closure.
3. Grep test/*.mjs for both literal and escaped forms; update pins.
4. Re-run `node --test test/*.test.mjs` and gate on the literal `# fail 0` line (a bare grep of the output masks failures — exit code is 0 even with fails when piped).
Cache-header tests (`test_cache_headers.py`, `test_legacy_urls.py`) use `admin-queue.js?v=31` as an arbitrary sample URL — those are NOT live references, leave them.

## How to apply
Every landing that touches served JS/CSS — even comment-only diffs (Cloudflare caches bytes for 4 h; smoke compares prod against local checkout and goes red on any divergence). Related: [[cloudflare-js-cache]], [[premature-probe-poisons-new-asset-url]].
