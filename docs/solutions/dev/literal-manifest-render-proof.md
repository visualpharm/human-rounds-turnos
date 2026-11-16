---
title: Literal source validation before reversing manifest render deltas
scope: repo
area: dev
tags: [esm, fingerprint, raw-render, acceptance]
created: 2026-09-05
refs: [1fbcdcae, 52d547e6]
status: active
---

## Problem
Moving shared modules changes fingerprints embedded in every Account page.
Replacing every new fingerprint with the old one can hide unrelated source
changes while making all raw-render hashes pass.

## Root cause
A permissive proof treats any changed asset as a permitted delta. This proves
neither the relocation's scope nor that unrelated behavior stayed unchanged.

## Solution
For the five-primitives placement, require exactly five manifest key renames.
Validate moved source bytes against the baseline plus only reviewed literal
header/import edits. Restrict changed consumers to an explicit allowlist and
exact import rewrites; unchanged consumers must remain byte-identical.
Reverse only the two reviewed direct Account loader URLs, once each, and the
whole validated manifest. Compare eight literal ES/EN response hashes without
HTML or whitespace normalization. Browser behavior remains a separate gate.

## How to apply
Before changing pins, inject an unrelated edit separately into a moved module,
a manifest consumer and a direct loader. Each must fail the proof. Restore
exact source bytes and require the original positive proof to pass again.
The placement proof rejected all three controls and passed after restoration.
Run the entire frontend suite and affected Python screen contracts too:
consumer expectations may construct a path from a bare module name, so a
literal filename search alone misses them. Full combined verification caught
the Users screen's remaining bare `format` expectation.

The DNI placement applies the same gate to exactly two key renames and four
reviewed source files. Its eight raw responses reverse only the validated
manifest; four source-mutation controls must fail. Browser fixtures derive
fingerprints from real bytes and exercise scanner/photo prefill, not just
requests to conveniently renamed fixture URLs.
