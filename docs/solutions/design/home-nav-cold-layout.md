---
title: Reserve the home navigation before its deferred controller arrives
scope: repo
area: design
tags: [navigation, layout-shift, browser, negative-control]
created: 2026-09-06
refs: [87fbda267b63689719f94582c2b05608327c473a]
status: active
---

## Problem
The home hero moved when the deferred shared navigation mounted. The sticky
search offset also changed because its fallback differed from the actual bar.

## Root cause
The direct home template emitted an empty header with no flow reservation.
Holding the actual fingerprinted nav response on the exact baseline produced
a 0-to-73px header change and a 40-to-113px main-position change at 360px.
The sticky offset changed from 79px to 83px; this was movement, not overlap.

## Solution
Reserve the existing navigation footprint only while its host is empty:
73px at widths up to 420px, 74px otherwise. Seed the matching height variable;
the shared controller's measured inline value takes ownership after mounting.
No replacement navigation, hidden content or decorative separator is added.

## How to apply
The Search delayed browser suite intercepts the real immutable nav request,
measures both sides of release and records native layout-shift entries after
paint boundaries. A unique durable report retains failures and screenshots.
The unchanged test fails on baseline source and passes on the candidate at
360/390/768/1024/1280px; main, header and sticky offset remain unchanged.
Small font-related shifts still occur: this is not proof of zero total CLS,
nor closure of the separate compact-actions or historical input-loss issues.

The commit gate also exposed a retirement-scan race: `statSync` followed every
directory entry, including a Python bytecode temp file renamed by another test.
The five source walkers now use Dirent types, following symlinks as before.
They still scan source extensions inside cache directories: an actual ignored
`__pycache__/*.js` negative fixture was rejected, then removed and reverified.
