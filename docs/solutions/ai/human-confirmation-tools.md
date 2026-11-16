---
title: Human confirmation at AI appointment boundaries
scope: repo
area: ai
tags: [oversight, chat, booking, confirmation]
created: 2026-10-14T10:22:31-03:00
refs: [codex-slice8]
status: active
---
## Problem
Model-selected cancellation executed directly; booking tools could reach
automatic approval. A model could narrate a confirmation proposal as completed.
## Root cause
Tools delegated to patient write paths without a trusted transport boundary;
the next model turn owned the reply after a non-executing proposal.
## Solution
Cancellation returns the patient's request page; the AI never cancels.
Web/WhatsApp request_appointment stores an exact patient/service/slot proposal.
A subsequent explicit patient Confirmar reply consumes that proposal through
trusted transport metadata before another model turn. It expires after 10
minutes and cannot move to another account or conversation. Model arguments
such as confirmed or allow_auto_confirmation cannot authorize it.
After consent, the canonical create operation applies deterministic service
rules (including auto_confirmar and same-day opening). An existing pending
request is recovered as pending; the tool cannot approve it later.
MCP is stateless and has no client elicitation channel. It returns absolute
selected-slot web links instead of treating a model argument as human consent.
## How to apply
Run test_slice8_review_fixes.py, test_ai_register_oversight.py and MCP suites.
Preserve the negative controls for model-provided confirmation, existing pending
requests, expired/rebound proposals and undeclared AI write tools.
MCP in-conversation elicitation requires separate transport support; the current
web fallback is safe but does not fulfill that part of the review request.
