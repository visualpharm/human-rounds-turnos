---
title: Golden conversations pin the patient wiki's RAG matcher
scope: repo
area: content
tags: [rag, knowledge-base, admin, testing]
created: 2026-09-05
refs:
  - patient_web/knowledge_golden.py
  - packs/global/conversaciones-doradas.json
  - packs/ar/conversaciones-doradas.json
  - packs/ar-b/conversaciones-doradas.json
  - core/data_packs.py
  - test/test_knowledge_golden.py
status: active
---

## Problem

The patient-site wiki's RAG lookup (`knowledge.rag_context()`) is text-matched
(title + body scoring), so adding or editing an article can silently break a
retrieval a real patient depends on, with no test noticing and no way for
staff to check it from the admin.

## Root cause

There was no fixture format capturing "this real patient question must
retrieve these article ids and the RAG text must contain these facts" — so
regressions only surfaced when a patient actually asked and got the wrong
answer.

## Solution

`packs/{global,ar,ar-b}/conversaciones-doradas.json` holds golden cases
(patient question + expected article ids + plain-substring facts). Registered
in `core/data_packs._COMPOSABLE` as `("conversations", "id")` so packs layer
like every other catalog. `patient_web/knowledge_golden.py` runs each case
against the real `knowledge.rag_context()` — deterministic, no AI call — so it
runs in CI (`test/test_knowledge_golden.py`) and on demand from
`POST /api/admin/content/knowledge/golden`, wired to the wiki admin's "Probar
conversaciones" button (`admin-knowledge.js`, `runGolden`).

Day-one catch: "Tengo 16 años, ¿puedo sacar un turno e ir al médico sin mis
padres?" retrieved `guardia-o-turno` (title match on "turno" outweighed body
matches) instead of `adolescentes-salud`. Fixed by adding patient-language
tags ("sin padres", "solo", "16 años") to the `adolescentes-salud` article —
the fix was content, not the matcher.

## How to apply

When adding a wiki article, add its golden question (and expected ids/facts)
to the same pack's `conversaciones-doradas.json` in the same change. If a
golden case fails after an edit, fix the ARTICLE's tags/wording first — only
change the matcher if the fact genuinely isn't in any article yet.
