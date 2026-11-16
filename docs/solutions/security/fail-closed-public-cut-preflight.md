---
title: Fail-closed public cut preflight design
scope: Public-cut preflight checker for Tier 7 publication
area: security
tags: [publication, security, preflight, fail-closed, tier7]
created: 2026-08-11
refs:
  - scripts/public_cut_preflight.py
  - scripts/public_cut_policy.json
  - test/test_public_cut_preflight.py
  - docs/publication/preflight.md
status: active
---

# Fail-closed public cut preflight design

## Problem

A public-cut preflight checker must verify that a candidate repository tree is safe for publication WITHOUT exposing private data, credentials, or control-plane state during the check itself. The checker faces two opposing risks: false-negative risk (skipping checks could leak private data) and false-positive risk (over-strict checks could block valid publication candidates).

## Root cause

Naive preflight designs that inspect file contents before validating directory structure can expose forbidden data. Additionally, reporting matched credential values, following symlinks, or continuing after inspection errors all create attack surfaces.

## Solution

Name-first rejection with fail-closed phase ordering: forbidden paths fail FIRST (name-only, content never read), value-free reporting (shapes/locations only, never values), symlink rejection without following, inspection errors fail-closed, descriptor-relative `O_NOFOLLOW` traversal for binary sets, and Git subprocess hygiene.

## How to apply

The preflight checker enforces this 7-phase ordering (via `PublicCutPreflight.run()`): forbidden paths (name-only matching, stops immediately), external evidence (informational only, `passed=None`), ambient GIT_*, required artifacts (policy-required files present, non-symlink, above floor, UTF-8, and structurally complete: required H2 sections, word-bounded placeholder-marker absence, approval-record form — never approval substance), closed binary asset sets (exact reviewed manifest, media signature, size, and digest), secret markers (credential shapes only, values never reported), and git history (single-commit, no-remote, no-extra-refs). Proofs in `test/test_public_cut_preflight.py`: credential inside forbidden pack never detected, symlinks rejected without following, current private tree fails first on `packs/ar-b-pinamar/` with NO secret-marker or git-history findings produced, historical placeholder stubs rejected by marker/floor, binary sets fail closed without leaking bytes or identifiers, and the private-tree CLI line stays byte-stable.
