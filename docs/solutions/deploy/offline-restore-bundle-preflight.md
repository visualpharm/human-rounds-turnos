---
title: Offline restore bundle preflight
scope: repo
area: deploy
tags: [restore, tar, integrity, lifecycle]
created: 2026-09-10
refs: [scripts/restore_bundle_preflight.py, test/test_restore_bundle_preflight.py]
status: active
---

## Problem

A matching checksum does not make a tar structurally complete. An initial
generated preflight also checked size only before hashing, materialized every
header before limiting the count, and followed symlinked parent directories.

## Root cause

Metadata-oriented tar readers may ignore trailing archives and consume
extension records before application bounds run. Final-component O_NOFOLLOW
does not protect parents. Removing optional zero padding is not a genuine
truncated-payload test. Duplicate JSON keys are silently overwritten by default.

## Solution

The offline preflight opens canonical input paths through no-follow parent
descriptors, bounds every hash read, and parses the same held archive descriptor.
It checks inode/size/mtime/ctime and path identity before returning. Raw USTAR
headers are bounded before decoding; extension/sparse/link/device records,
truncated payloads, missing end blocks and nonzero trailing data are refused.
Generated tests exercise real bytes, concurrent mutations and CLI output.

## How to apply

Use `scripts/restore_bundle_preflight.py --help` for exact manifest and artifact
arguments. Accept only uncompressed USTAR ordinary files/directories and explicit
timezone-bearing capture metadata. Freeze all writers and retain immutable
artifacts through the subsequent recovery step; a preflight result is not a
lock against later modification. This is not extraction, SQL validation,
authenticity proof or an installed recovery rehearsal; audit row 8.3 stays open.
