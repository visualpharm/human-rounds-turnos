---
title: Chat tools use the chat persistence boundary
scope: repo
area: dev
tags: [chat, persistence, dependency-direction, refactor]
created: 2026-08-21
refs: [f048d1c6]
status: active
---

## Problem

`chat_agent.py` used the module store seam, but `chat_tools.py` still imported
`core.db` inside booking recovery, dependent checks, request listing, and
identity projection. The chat feature therefore retained two persistence
directions and could not swap or test storage at one boundary.

## Root cause

The original chat-store extraction covered conversation history only. Tool
execution was left as an implicit exception even though its calls are simple
table API reads with the same fallback ownership in `chat_tools.py`.

## Solution

Add signature-identical, one-return-call delegates to `chat_store.py` and route
all four tool read families through them. Keep recovery decisions, exception
handling, response shaping, and booking orchestration in `chat_tools.py`.
Structural tests require both callers to contain no direct `core.db` import and
require every adapter function to match the underlying signature exactly.

## How to apply

When a chat caller needs another persistence primitive, add only a transparent
delegate to `chat_store.py`; never move policy or catch/transform exceptions in
the adapter. Extend the exact delegate-set test in the same slice.
