---
title: Upload readiness must retain current step and file intent
scope: repo
area: dev
tags: [upload, esm, turnstile, browser-tests]
created: 2026-09-05
refs: [test/turnos-upload-native.test.mjs]
status: active
---

## Problem
A preloaded order could leave bootstrap paste capture installed indefinitely.
Leaving the upload step while verification loaded could paint an error on the
next step. Two files selected during loading could submit the same token twice.

## Root cause
Readiness alone does not establish ownership. The early-input bridge had no
handoff when routing skipped upload. Each file independently awaited the same
mount promise without retaining the originating step or coalescing file intent.

## Solution
Release bootstrap capture when the resolved flow has no upload control, while
preserving it through legitimate dependency loading. Capture the rendered upload
step, cancel its continuation after navigation, and keep only the newest file
while mounting. A configured provider failure still blocks the POST and permits
a later retry; no-sitekey and server authentication policies remain unchanged.

## How to apply
Exercise actual server-rendered pages and native controllers. Hold verification,
select two files, and require exactly one token-bearing analysis. Leave the step
before release and require no POST or recovery on its replacement. An accepted
order must not prevent unrelated later image paste. Keep the existing delayed
dependency, keyboard, rejection and retry cases; checking imports is insufficient.
