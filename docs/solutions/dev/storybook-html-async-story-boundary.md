---
title: Storybook HTML stories must return a Node synchronously
scope: repo
area: dev
tags: [storybook, html, stories, fixtures, i18n, assets]
created: 2026-09-05
refs: [codex/directory-real-component-story]
status: active
---

## Problem

An async story export passed tests that `await`ed it, but Storybook HTML showed
an error instead of the Directory because its renderer only accepts a string or
DOM Node from `storyFn()`.

## Root cause

The fake test hid the Promise boundary. The fixture also relied on absent
first-party preview routes for its catalog and public icon URLs.

## Solution

Return a host Node synchronously, start the real renderer in place, and scope
its fetch fixture to the renderer's synchronous `fetch()` initiation. Bootstrap
the committed local catalog before native modules load and publish only the
specific public asset directories referenced by stories.

## How to apply

Test the actual installed renderer/build, not only direct awaited exports.
Render representative stories at phone and desktop widths; require no page
errors, failed/non-2xx first-party requests, missing fixture assets, raw i18n
keys, or horizontal overflow. A Storybook preview fix is not production-runtime
proof.
