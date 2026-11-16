---
title: Calendar feed completion belongs to the current doctor render
scope: repo
area: dev
tags: [doctor, asynchronous, lifecycle, regression]
created: 2026-09-07
refs: [2026-09-07-human-rounds-doctor-feed-render-ownership]
status: active
---

## Problem
Changing visible appointment columns while the calendar feed request was pending
could append two feed sections to the same doctor screen.

## Root cause
Every render requested the feed and retained the same section reference. Both
asynchronous completions appended after the newest render replaced its content.
A section identity check alone cannot distinguish two renders of the same host.

## Solution
Capture a monotonically increasing render generation and the section identity.
Append only if both still match. Replacing the section also cancels an old append.
This bounded correction does not implement full controller mount/dispose ownership.

## How to apply
Execute the complete controller with delayed network/DOM boundary doubles. Test
both old-first and new-first completion orders, an actual replacement host, and
a positive control that requires a usable feed link. The unchanged controller
passes the positive control and fails all three ownership cases; the correction
passes all four. Browser, manifest-delta and release proof remain separate gates.
