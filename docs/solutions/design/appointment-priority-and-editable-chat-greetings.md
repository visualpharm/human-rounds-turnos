---
title: Appointment priority and editable chat greetings
scope: repo
area: design
tags: [account, chat, i18n, admin, responsive]
created: 2026-08-06
refs: []
status: active
---

## Problem

The active appointment card repeated “your next appointment” inside its title,
split the date from its calendar action, and expanded secondary controls at
narrow widths. The chat greeting was an unreadable paragraph and changing it
required a deploy.

## Root cause

The card treated its label, facts, and actions as equivalent rows. The greeting
lived only in the file catalog/fallback, while canonical Spanish intentionally
ships no client catalog.

## Solution

Use the service as the card title, keep date and calendar link in one row, and
group clinical actions plus waitlist in a responsive footer. Store one greeting
override per locale in `config.py` through Settings, then layer only that key
into the normal inline i18n bootstrap (including canonical Spanish).

## How to apply

- Prefer the concrete appointment fact over a redundant “next appointment” label.
- Use container queries because the card renders standalone and inside Account.
- Keep calendar navigation visually a link and on the same row as the date.
- Preserve `{brand}` and the chat mini-markdown contract in editable copy.
- Paint the greeting before any history fetch: first paint must stay synchronous, ordered, and flicker-free.
