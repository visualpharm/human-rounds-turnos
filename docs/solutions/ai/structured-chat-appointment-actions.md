---
title: Structured appointment actions inside chat replies
scope: repo
area: ai
tags: [chat, appointments, tools, ui]
created: 2026-08-02
refs: []
status: active
---

## Problem
The assistant repeated appointment dates in prose, then rendered a detached row of duplicate action chips. Dates and actions could also drift from the actual tool result.

## Root cause
The chat API exposed only model prose and generic options, so the widget had to infer bookable slots from presentation text.

## Solution
Carry validated `turnos` from the availability tool through the agent and API. Each item includes a date label, time, and same-site booking URL. Render those links directly beneath the relevant assistant text, grouped by date, and suppress duplicated model-written slot lists.

## How to apply
When a chat tool produces an actionable entity, preserve it as structured response data through every layer. Let model prose explain the result, but build navigation and mutation controls only from validated tool output.
