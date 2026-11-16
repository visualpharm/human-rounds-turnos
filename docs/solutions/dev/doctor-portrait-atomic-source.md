---
title: Atomic doctor portrait and original publication
scope: repo
area: dev
tags: [photos, onboarding, recovery]
created: 2026-10-14
refs: [codex-slice5]
status: active
---

## Problem
Updating a private original before publishing its processed portrait can leave
an old visible image paired with the new source after a disk failure.

## Root cause
Separate atomic writes do not make the original, portrait and status one commit.

## Solution
Under a per-doctor filesystem lock, prepare a private version bundle containing
the byte-identical original, 512px WebP and metadata. Atomically replace the
public portrait symlink only after every bundle file exists. Source/status
reads resolve that same pointer. A failed publish removes only its unpublished
temporary bundle. Legacy uploads are adopted without changing original bytes.
The shared atlas continues reading the published portrait; originals have no
public route. Removal deletes the doctor's private bundles too.

## How to apply
Use the existing doctor upload endpoint for upload/rotation/restore/reprocess.
Keep current source/status coupled through the pointer, and confirm a photo
mutation only after its acknowledgement includes the doctor's portrait URL,
source availability and a recognized status.
Synthetic unit/ASGI proofs live in test/test_slice5_doctor_workspace.py and
test/doctor-workspace-slice5.test.mjs. Database migrations and rendered browser
geometry still require their separate gates; this record does not prove them.
