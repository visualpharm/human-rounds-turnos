---
title: Venue links and local RAG terms are installation data
scope: repo
area: webdata
tags: [data-packs, portability, patient-site, rag]
created: 2026-08-21
refs: [f048d1c6]
status: active
---

## Problem

The engine embedded one deployment's off-site venue destination and named its
localities when deciding whether a knowledge query requested town-level facts.
A different installation could show the wrong map link or rank local knowledge
using another town's words.

## Root cause

Venue routing and locality vocabulary had been treated as renderer/search
constants even though both vary with the active installation pack.

## Solution

Read exact venue-to-HTTPS mappings from `sitio.json.venues`, rejecting unsafe
schemes and falling back to the owning center. Build local retrieval tokens on
each call from `site_identity.get("city")` plus the active
`example-localities.json`; missing/malformed locality data contributes no
foreign fallback. Keep legacy `jurisdiction="pinamar"` only as an `l4` data
alias, not as an active-town trigger.

## How to apply

Put deployment-specific destinations and locality names in the town pack.
When adding a new pack, test its city and one nearby locality against an `l4`
article, and test a foreign locality does not become a local-preference signal.
