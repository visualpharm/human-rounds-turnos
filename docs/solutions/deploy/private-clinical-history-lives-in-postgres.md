---
title: Private clinical history lives in Postgres
scope: repo
area: deploy
tags: [clinical-history, postgres, coolify, privacy]
created: 2026-08-02
refs: []
status: active
---

## Problem

A deployment-private medical archive must survive redeploys without entering
git or leaking into the fictional public demo.

## Root cause

The Coolify application container has no persistent `/app` file mount. A JSON
copied into the running container disappears on the next deploy, while a file
committed for durability would expose sensitive data and cross installations.

## Solution

Import the normalized legacy-record JSON into `clinical_history_records` in
the installation's Postgres database. Keep source JSON under `data/private/`
(gitignored), import with `scripts/import_clinical_history.py`, and read through
`connectors/local_history`. Rows retain their source and an idempotency hash.

## How to apply

Use the same `{schema_version, patients: {dni: records}}` shape as the fake
Alepo/Marqui fixtures. Import into only the intended installation database,
verify the DNI and row count, then remove any temporary upload. Never seed the
private archive into demo; demo history comes from synthetic connector data.
