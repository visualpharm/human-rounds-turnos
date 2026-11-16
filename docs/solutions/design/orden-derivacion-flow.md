---
title: Orden-derivación flow design (v4 wizard) + wait/prep product direction
scope: repo
area: design
tags: [ux, elderly, orden, espera, preparacion, wizard]
created: 2026-07-09
updated: 2026-07-23
refs: [hospital.py, servicio.js, fechas.js, preparacion.py, abuso.py, ai_orden.py, wz-upload.js, upload-order.js, "~/.claude/skills/wizard-design/SKILL.md", "~/.claude/skills/patient-flow-design/SKILL.md"]
status: active
---

## Problem
`solicitud_con_orden` services (Audiometría etc.) had a generic info page; elderly patients got lost, saw no appointment time (order requirement hid the "when"), and dead-ended without an order.

## Root cause
The flow was designed system-first (modes, requirements) instead of task-first for the real audience (60+, low-tech, phone).

## Solution (v4 wizard, 2026-07-10 — do NOT relitigate; supersedes v3 where they clash)
- Per the product owner + user skill `wizard-design`: each step = process header (h1 "Orden de derivación" + sub "servicio · centro", server-side) + ONLY that step's task. `chrome="focus"` now renders NO logo bar and a **minimal footer**: brand-home + legal + un contacto de escape del centro (`c.whatsapp` → `c.telefono`, nunca inventado). The v3 when-line (`_proximo_turno_label`) and the "Cómo llegar y contacto" `<details>` were REMOVED from the wizard — show-the-wait applies to lists/landing surfaces, not steps.
- `servicio.js`: steps REPLACE (`host.innerHTML`, nothing appends below the control). Valid orden or analysis-failure → **straight to "Cuándo"** (no read-back, no empty field rows). Not-an-order → `stepDocDudoso`: warn + preview (side column ≥640px, `.wz-err` grid) + mismo `uploadZoneHtml()` + "Estoy seguro" + "Todavía no tengo la orden".
- **Paste**: document-level `paste` listener active while `#wz-file` is mounted; zone caption advertises Ctrl/⌘V (hint hidden on touch via `@media(hover:hover)`).
- **Fechas amigables** (`fechas.js` + `hospital._fecha_amigable`, twin contracts + twin tests): display "Hoy/Mañana, 14:40" / "Martes 14 de julio, 14:40"; texto GUARDADO (preferencia) usa `larga()` — siempre absoluta, lo relativo se pudre en el admin.
- Still true from v3: ONE filled CTA per screen; "No tengo la orden" numbered future-steps explainer ending in "Sacar turno con clínico" (flow-CTA carve-out — keep as button).

## R4 done (2026-07-11) — "Cómo prepararte para tu estudio"
Real data file: `webdata/estudios-preparacion.json` (30 estudios, researched via parallel.ai — see `_meta` in the JSON for sourcing/ambiguity notes), NOT `webdata/preparaciones.json` (the placeholder name below is stale).

- **Standalone tool, `preparacion.py` + `abuso.py`:** `GET /preparacion[?servicio=<id>]` (1-step wizard, `chrome="focus"`) + `POST /api/preparacion` — upload ANY document (order or otherwise), `ai_orden.leer_estudios` (single pass, cheap) reads which studies it asks for, `preparacion.match_estudios` matches in code against the dataset by longest keyword (specific beats generic — "ecografía abdominal" beats "ecografía"). No account, nothing persisted (temp file + `_pdf_to_png` sidecar deleted every request, success or failure). Shared anti-abuse (`abuso.py`, rate limit + Turnstile, same envs as admin_auth/cuentas) now also gates `/api/analizar-orden`.
- **Entry points:** service info pages for `categoria=="estudios"` get a text-link teaser to `/preparacion?servicio=<id>`; the diagnóstico cluster page gets a general "¿Ya tenés la orden?" link to `/preparacion`. Deliberately NOT added inside the `solicitud_con_orden` wizard page itself — wizard-design rule 1 (a step shows only its own task) wins there; that flow already shows prep on ITS OWN success screen (see below).
- **Tie-in into the existing order flow:** `solicitudes._analizar_orden` now attaches `ai_json.preparacion` (matched via the specialty the 3-pass read + the servicio's own name) — surfaces on `servicio.js`'s success screen (prefers the server match, falls back to the old single-keyword client match) and in `/mis-solicitudes` for `aprobada` solicitudes (patient needs it the day BEFORE, not just at upload — `db.list_by_dni` now selects `ai_json`, `_public_row` re-exposes only `.preparacion`).
- **Never promise missing guidance:** the result shows `Cómo prepararte` only for concrete entries from `webdata/estudios-preparacion.json`. An unmatched practice stays visible under `Tu orden pide`, but does not generate “el centro te indica…” or a generic disclaimer: the site is the centre's communication channel, so unsourced future promises create calls and false expectations.
- **Shared upload control extracted:** `wz-upload.js` (`window.WzUpload = {css, zoneHtml, wire, fileToOrden}`) — was duplicated inline in `servicio.js`, now both wizards consume the same control (wizard-design rule 6).
- Tests: `test/test_preparacion.py` (match_estudios specificity/dedup/fallback, abuso rate-limit/turnstile, full endpoint via TURNOS_FAKE_AI).

## R3 (still open) — espera en listas
- Every service/specialty/center list row gets an approximate wait. Data: `msd_data.turnos_for_servicio` (next slot → days out) for online agendas; median solicitud→confirmación from the `solicitudes` table for order services; walkin = "por orden de llegada". Buckets in words ("esta semana", "~2 semanas", "más de un mes"); unknown = "sin datos", never invented. Same service at several centros: default-sort by soonest, wait + centro visible together (people travel within the partido to wait less). Hook: `_servicio_row` already takes `proximo=` — extend to all lists.
- Reusable principles → user skill `patient-flow-design` (load it before touching these flows).
