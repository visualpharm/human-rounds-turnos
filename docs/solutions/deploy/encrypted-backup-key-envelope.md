---
title: Rewrappable backup headers and independent recovery evidence
scope: repo
area: deploy
tags: [backup, encryption, rotation, recovery]
created: 2026-10-14
status: active
---

## Problem

Binding mutable wrapped-key bytes into chunk AAD makes key rewrapping require
reencrypting every chunk. Signing a new archive's manifest only with an old
retained key also breaks recovery using only the newly saved operator key.

## Solution

HRBACK01 has an immutable content header whose hash binds every chunk,
and a separate mutable key envelope authenticated by a data-key header MAC.
New manifests have one ownership MAC per retained KEK. Offline recovery
authenticates the stream and file hashes; extra manifest ownership checks
admit retention, where old ownership keys remain available.

Failed re-verification clears old integrity claims in the journal. Upload,
integrity and scratch-database restore proof are separate evidence fields.

## Verification

test_backup_crypto.py exercises header/chunk corruption and unchanged
ciphertext rewrapping. test_backup_full.py proves recovery using only the
new saved key, rewrapped archive recovery and stale-integrity invalidation.
These synthetic checks do not establish R2 or clean-host acceptance.
