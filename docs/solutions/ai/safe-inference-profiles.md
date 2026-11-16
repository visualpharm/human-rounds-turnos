---
title: Safe switchable AI inference profiles
scope: repo
area: ai
tags: [inference, credentials, redirect, settings, tier-6]
created: 2026-08-10
status: active
---

## Problem

The original single-provider client used OpenRouter-named credentials and
followed urllib redirects. Repointing its endpoint would risk sending that
credential to another provider or a redirect target.

## Solution

Keep one OpenAI-compatible request path. `cloud_shared` retains the exact
OpenRouter endpoint and legacy primary/backup sticky failover. The two
non-cloud profiles require dedicated `AI_INFERENCE_*` keys and cannot read
`OPENROUTER_*` credentials.

Validate a complete endpoint before Settings persists it: hosted inference is
HTTPS-only; self-hosted plain HTTP is only localhost, literal loopback, or
literal RFC1918 IPv4. Reject URL userinfo, queries, fragments, root-only
paths, and redirects. Snapshot destination and keys once per call so a
concurrent Settings write cannot move a failover retry to a different host.
The System-status OpenRouter spend probe takes that same kind of snapshot, so
a concurrent profile switch cannot send a non-cloud key to OpenRouter.

## How to apply

- Use `ai_client.available()` for every caller's configured-state guard.
- Keep provider errors value-free when they can reach UI, health, or saved
  state.
- Treat a non-cloud profile as unavailable until both its safe destination and
  dedicated key are present.
- Keep OpenRouter balance/spend probing limited to `cloud_shared`; non-cloud
  System status must not send its dedicated key to OpenRouter.
