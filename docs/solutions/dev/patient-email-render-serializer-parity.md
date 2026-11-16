---
title: Test rendered patient controls against their serialized payload
scope: repo
area: dev
tags: [refactor, forms, serialization, acceptance]
created: 2026-09-08
refs: [4879d845]
status: active
---

## Problem
Saving a patient silently sent a null email despite a populated email input.
The entire existing suite passed because its normalized-body test omitted email.

## Root cause
The native renderer used pat-email while its serializer still queried pac-email.
The missing element silently became an empty string and then null.

## Solution
Read the actual rendered input ID. Runtime and browser cases now exercise
unchanged, edited, explicitly cleared, and newly created email values.
Select the rendered input by type in regression tests rather than constructing
a fixture element with the same stale ID as the broken serializer.

## How to apply
For form refactors, assert every outgoing field, including optional data that
must be retained when another field changes. Include intentional clearing as
a distinct case. Browser request interception proves client serialization;
do not describe it as backend persistence or production verification.
