---
title: Doctor symptom capture belongs in the doctor editor
scope: repo
area: design
tags: [admin, intake, doctors, preferences, qr]
created: 2026-08-01
refs: []
status: active
---

## Problem

The intake module already supported post-booking interviews, door QR entry,
triage, and queue cells, but administrators could not discover or configure it
while editing a doctor. Its global screen made the feature look absent.

## Root cause

Capability and configuration were separated by navigation. Doctor enablement
lived in the catalog overlay and printable QR routes lived in
`intake_admin.py`. Queue columns are a different concern: a personal view
preference stored per account in `admin_preferences`.

## Solution

Expose a `Síntomas` tab for every existing doctor. Reuse the effective intake
toggle and printable QR endpoint. Keep column editing exclusively in the
`Pacientes de hoy` gear, including on empty days, so each signed-in doctor
changes only their own database-backed view. When capture is enabled and the
account has no saved column choice, show the symptom column before the first
interview arrives. Fleet operations use the doctor-list bulk action menu.

## How to apply

When a module changes a doctor's daily workflow, put its operational control in
that doctor's editor and provide bulk enablement from the doctor list. Keep
workflow state separate from presentation preferences: enablement belongs to
the doctor record, while queue columns are edited on the queue and persist for
the account viewing it. Do not mirror personal columns into doctor settings.
