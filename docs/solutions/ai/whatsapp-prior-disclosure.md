---
title: WhatsApp disclosure must precede inference
scope: repo
area: ai
tags: [whatsapp, transparency, governance]
created: 2026-10-14
refs: [codex-slice8-continue]
status: active
---

## Problem
The first WhatsApp reply contained the AI notice, but the chat or document
reader had already processed the person's message. Failed sends also stored
a disclosure receipt.

## Root cause
Disclosure lived in the final-reply wrapper rather than the inference boundary.
Receipt persistence ignored the transport's boolean success result.

## Solution
Deliver a prior chat notice before first inference and a whole-document notice
before every attachment read. Stop AI processing when notice delivery fails.
Store receipts only after successful sends as ai_disclosure conversation
metadata. Look up the receipt across the conversation, not the last 200 turns;
exclude metadata and earlier system receipts from all LLM history/compaction. Keep deterministic reminder and
unknown-account replies combined, since they perform no AI processing.

## How to apply
Run `test_ai_register_notices.py` with mocked transport/readers and assert
notice-before-reader ordering plus failed-delivery negative controls.
The transport result establishes delivery acceptance, not human readership.
Real DB-backed channel tests also assert the separate notice and final reply.
