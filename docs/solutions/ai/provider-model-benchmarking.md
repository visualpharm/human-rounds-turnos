---
title: Provider model benchmarks need the real chat loop and manual review
scope: repo
area: ai
tags: [chat, benchmark, providers, tool-calling]
created: 2026-07-30
refs: [docs/studies/opencode-go-chat-2026-07-30/report.md]
status: active
---

## Problem

A model can appear to pass because it called the right tool and generated the
right link while its visible answer is empty, truncated, exposes an internal
marker, or omits the login marker the widget needs.

## Root cause

Transport-only prompts do not exercise Sana's RAG, catalogue tools, marker
parser, link resolver, response cap, or multi-turn state. Boolean checks on
links and keywords also miss visible-response defects.

## Solution

Run candidates through the real anonymous chat loop and replace only the
provider call. Support each provider's actual protocol (`/chat/completions`
and `/messages` here), start at production output limits, and store no
synthetic transcript in Postgres. Use automatic contracts for breadth, then
manually review every finalist answer before ranking latency and capacity.

Treat provider errors separately from answer quality and retry only errors
explicitly identified as transient. A quality gate comes before speed/value.
If a cheap model truncates, retest a higher cap only when it remains below the
declared cost ceiling.

Run separate models concurrently in isolated processes. The chat harness
temporarily replaces module-level provider and history functions, so threads
would share mutable state and contaminate results. Keep cases within one model
serial when they include stateful follow-ups. Report both per-model
complete-response latency and batch wall time; a non-streaming completion
benchmark is not a time-to-first-token measurement.

Normalize generation limits by money, not shared token counts. Choose a
maximum output spend, calculate `tokens = budget_usd * 1_000_000 /
output_price`, and apply a provider-safe technical ceiling. The most expensive
reasoning model still needs at least 8,000 tokens before a failure can be
treated as evidence. Record actual uncached input, cached input, output,
provider calls, and cents for every case; the cap is exposure, not spend.

## How to apply
- Screen tool use, emergency safety, and prompt injection on every model.
- Advance only models with complete visible answers and intact UI markers.
- Test finalists on grounded high-stakes questions and stateful follow-ups.
- Use `--parallel` to compare models under the same provider conditions.
- Use `--output-budget-cents` for price-normalized relaxed limits.
- Record endpoint compatibility and included request capacity in the ranking.
- Keep keys in credential vault; assert they are absent from artifacts.
