---
title: SanaCuenta.renderAuth embeds must pass heading:false or the page shows two titles
scope: repo
area: design
tags: [cuenta-form, ui-rules, double-heading]
created: 2026-07-11
refs: []
status: active
---

## Problem
`/cuenta` rendered `<h1>Mi cuenta</h1><p class="lede">Registrate…</p>` and then,
right below it, `cuenta-form.js` rendered its OWN `<h2>Crear cuenta</h2><p
class="sc-sub">Necesitás una cuenta…</p>` — two title+subtitle pairs stacked,
restating the same fact. Same bug independently in the servicio.js wizard's
"Tu cuenta" step and mis-solicitudes.js.

## Root cause
`cuenta-form.js` is a reusable component (`window.SanaCuenta.renderAuth`)
mounted under THREE different pages, each of which already renders its own
page/step-level title before mounting it. The component always rendered its
own heading unconditionally — every call site duplicated whichever page-level
title was already there.

## Solution
`renderAuth(host, opts)` takes `opts.heading` (default `true`); when `false`,
the internal `heading()` helper returns `""` and only the toggle
("¿Ya tenés cuenta?"/"¿No tenés cuenta?"/forgot-password) plus the form fields
render. All three call sites that already have a page-level title pass
`heading:false`: `cuentas.py`'s inline `/cuenta` script, `servicio.js`
`stepCuenta()`, `mis-solicitudes.js` `renderLogin()`.

## How to apply
Any NEW page/flow that mounts `SanaCuenta.renderAuth` must decide: if it
already renders its own title+subtitle for the auth step, pass
`heading:false`. If the component is the ONLY thing on the screen (no outer
title), leave `heading` unset (defaults to `true`). Same convention now
applies to any future reusable form component in this repo — see the personal
`ui-rules` skill's "one title, one subtitle per screen" rule for the general
principle this codifies.
