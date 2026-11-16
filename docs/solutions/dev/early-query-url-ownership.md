---
title: Early search input owns URL state before dependency readiness
scope: repo
area: dev
tags: [search, async, url-state, browser-tests]
created: 2026-09-06
refs: [fb87f270]
status: active
---

## Problem
A person replaces or clears a seeded home query before translations or
catalogs finish loading. Deferred fallback restores the old query, or a
catalog failure leaves the old URL so reload restores it later.

## Root cause
The original i18n continuation unconditionally applied the URL seed. A second
catalog continuation restored it whenever the input was empty. Tracking an
early edit fixed visible replacement, but URL synchronization still depended
on successful catalog loading.

## Solution
Capture the initial URL once, immediately track input intent, and synchronize
the URL in that immediate listener. Both later seed fallbacks respect edits,
including explicit clear. Rendering still waits for its real dependencies;
URL ownership does not. Retain one URL writer and preserve other parameters
and the fragment. An untouched empty URL needs no cleanup mutation.

## How to apply
Hold the actual i18n and Search modules plus catalog response. Type and clear
before release, verify the visible value and URL, then reload. Separately
reject each real catalog dependency and repeat. The original source fails
the replacement assertion; the partial fix fails URL persistence after a
catalog rejection. Keep those observed red controls with the green candidate.
This separate seeded-URL defect does not explain an earlier intermittent
blank on bare `/`; retain its causal trace instead of claiming that fix.
