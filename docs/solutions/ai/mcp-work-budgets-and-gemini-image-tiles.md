---
title: MCP work budgets and Gemini image tiles
scope: repo
area: ai
tags: [mcp, abuse, gemini, images, cost]
created: 2026-07-28
refs: [MCP v1.2.0]
status: active
---

## Problem

Bearer auth and a flat hourly call limit did not stop one buggy agent from
parallelizing expensive tools, nor did an in-memory counter survive deploys.
Large order scans also paid for many visual tiles that carried blank paper.

## Root cause

MCP calls have unequal cost. Gemini bills images at 258 tokens up to 384×384,
then in 768×768 tiles. A 769px edge can cross a billing boundary; bytes alone
do not predict visual tokens.

## Solution

`mcp_guard.py` validates arguments before charging them, applies locked
per-token and global weighted budgets, caps AI concurrency, and atomically
reserves the daily MCP image allowance in Postgres. `ai_orden._image_for_model`
rotates, conservatively crops white margins, and fits scans into at most two
768px tiles. Paid model output is capped; original medical files stay intact.

## How to apply

- Assign every new MCP tool a weight, argument allowlist, and length limits.
- Reserve paid work immediately before the model, after cooldown/validation.
- Keep burst limits in memory, but financial ceilings in shared durable state.
- Snap image dimensions to provider tile boundaries; verify a real handwritten
  sample still parses before shipping.
