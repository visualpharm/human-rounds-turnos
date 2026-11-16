---
title: Code-derived clinical indicators fill the visual summary safely
scope: repo
area: design
tags: [clinical-summary, charts, reference-ranges, mobile]
created: 2026-08-02
refs: []
status: active
---

## Problem

The model selects at most 3 clinical trends. Paired systolic and diastolic pressure consume 2 selections but render as 1 chart, leaving the doctor's quick scan with only 2 visual cards while 5 actionable measurements remain buried in prose.

## Root cause

The same model-curated list was doing 2 different jobs: choosing the few detailed trends and supplying the dashboard's current indicators. Increasing the model limit would add cost and still let a generated selection determine visual completeness.

## Solution

Keep the 3 model-selected detailed trends, then build a separate 4-card `indicadores` list in code. Start with the chosen series and fill remaining cards from the newest out-of-range code-extracted series. Treat blood pressure as 1 visual card while preserving separate systolic and diastolic ranges. The browser derives latest value, delta from range, sparkline, and status only from these structured points.

## How to apply

- Never parse prose or ask the model for a chart number.
- Count rendered indicator cards, not raw series ids.
- Complete a real blood-pressure pair automatically; keep both limits distinct.
- Invalidate cached summaries missing the derived field after a schema-like UI upgrade.
- Verify 2-column cards at 360 and 390 px, then expanded charts at desktop width.
