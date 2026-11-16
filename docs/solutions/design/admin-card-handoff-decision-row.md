---
title: Admin request cards from a simplified design handoff
scope: repo
area: design
tags: [admin, requests, design-handoff, responsive, attachments]
created: 2026-07-21
refs: []
status: active
---

## Problem

The handoff only modeled compact requests without referrals. Real cards also
contain extracted clinical fields, editable values, long rejection reasons,
and a 300 px attachment preview. Copying the action row into the narrower text
column caused approve and reject controls to overlap or truncate.

## Root cause

The handoff's action row assumed the whole card width. Attachment cards gave
the same controls only the width left beside the preview, while the controls
still had realistic minimum widths.

## Solution

Keep clinical fields and the attachment in the two-column body, then render one
shared decision row across the full card below them. Use two explicit branches:
approve on the left and rejection reason plus reject on the right. Highlight the
suggested branch with a soft state background and dim, but do not disable, the
alternative. Use concise operator labels for rejection options while submitting
the full patient-facing reason.

For the covered no-order state, measure against the handoff at the same viewport
instead of relying on visual memory. The 1280 px reference established the
shared 196 px sidebar, 18 px by 12 px content inset, compact filter/context/batch
rhythm, 12 px card radius, and 128.25 px no-order card height. Those values are
the baseline for the richer cards too; extra height must come only from order
fields, the attachment, or their decision controls.

## How to apply

- Treat a simplified design as the visual contract for shared regions, not as a
  reason to remove production-only information or validation.
- Compare source and implementation at identical viewport dimensions after each
  visual batch; a side-by-side render exposes cumulative spacing drift quickly.
- Keep each fact in one visible place; edit-in-place controls replace their
  label while open instead of duplicating it.
- Use one breakpoint for the entire decision group so it switches from two
  columns to a deliberate stack without partial wrapping.
- Verify no-order, extracted-order, and attachment states at desktop and mobile.
