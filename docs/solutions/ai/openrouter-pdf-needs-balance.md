---
title: OpenRouter file-part (PDF) requires $0.50 balance — convert PDF to PNG instead
scope: repo
area: ai
tags: [openrouter, pdf, gemini, ordenes, 402]
created: 2026-07-08
refs: [1c021ba]
status: active
---

## Problem
Analyzing a PDF orden returned `revision_manual` with `costo 0.0` on every pass while the same pipeline read JPEG/PNG fine.

## Root cause
OpenRouter's `file` content part (its PDF parser) rejects requests unless the account holds a minimum balance: HTTP 402 `"This request requires at least $0.50 in balance for files"`. The image path (`image_url` data URI) has no such requirement. Our key runs near-zero balance by design.

## Solution
`ai_orden.py:_pdf_to_png()` renders page 1 of the PDF to PNG before the model call: `pdftoppm -png -r 200 -singlefile` (poppler-utils, installed in the Dockerfile) or `sips -s format png` (macOS). The analysis then rides the image path. No converter available → the old file-part attempt still runs and degrades to `revision_manual`.

## How to apply
Any new model call that must read a PDF in this repo goes through `_pdf_to_png` first — do not add the file content part expecting it to work on this key. If multi-page PDFs ever matter, raise `-l 1` and send several images.
