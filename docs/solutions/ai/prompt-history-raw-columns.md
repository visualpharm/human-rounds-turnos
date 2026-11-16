---
title: Archive raw prompt columns before wire mapping
scope: repo
area: ai
tags: [prompts, history, audit]
created: 2026-10-14T10:22:31-03:00
refs: [codex-slice8]
status: active
---
## Problem
A second prompt edit raised KeyError instead of archiving and saving.
## Root cause
prompt_save read actualizado/actualizado_por from a raw Postgres row. Those
names exist only after _wire; the database columns are updated/updated_by.
## Solution
Archive raw updated/updated_by inside the same transaction as the prompt write
and ai_register_changes evidence. Use wire names only on returned API objects.
## How to apply
Run test_ai_register_changes.py (offline raw-row regression) and the real-DB
prompt/audit tests. Keep before/after hashes and lengths, never prompt content,
in the regulatory change ledger. Fingerprint the submitted source instructions
(Spanish when supplied, otherwise the direct English source). An unedited
translation-cache row is not a human saved override; do not label it as one. Audit failure must roll back the write.
