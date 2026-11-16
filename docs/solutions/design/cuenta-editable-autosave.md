---
title: /cuenta datos editables (autosave) + contacto-o-email obligatorio
scope: repo
area: design
tags: [cuenta-form, cuentas, autosave, ui-rules]
created: 2026-07-11
refs: []
status: active
---

## Problem
`/cuenta` sólo mostraba los datos de la cuenta como texto fijo (DNI,
nacimiento, contacto) — no se podían editar. Además nada impedía dejar la
cuenta sin ningún canal de contacto (ni teléfono ni email), lo que rompe la
recuperación de contraseña y cualquier aviso futuro de turno.

## Solution
`cuenta-form.js:renderEditar(host, {usuario, onSave})` — nuevo tercer modo del
componente (junto a `renderAuth`/`renderReset`): DNI fijo (no editable, es la
clave de login/HSI), el resto de los campos editable con **auto-save al
perder foco** (`ui-rules`: "no Cancelar, auto-save preferido") — sin botón
Guardar. Pega a `POST /api/cuenta/actualizar` (`cuentas.py:_actualizar` +
`db.update_user`), que resuelve el DNI desde la sesión (nunca del
body, mismo contrato que `/api/solicitudes`) y devuelve 400
`contacto_o_email_requerido` si ambos quedarían vacíos — el mismo check corre
en `_registro` para que no se pueda ni crear una cuenta sin ninguno de los
dos. `/cuenta`'s `renderCard` monta `renderEditar` en vez de la tarjeta
read-only de antes.

## How to apply
- Cualquier pantalla nueva que edite datos de cuenta reusa
  `renderEditar` — no reimplementar otro form de edición.
- El dedupe de guardado usa un `JSON.stringify` del payload contra el último
  guardado (`lastSaved`) para no pegarle al server en cada blur si nada
  cambió.
- Ver también [[auth-component-heading-suppression]]: el mismo `/cuenta` le
  sacamos el subtítulo "Tu cuenta de Sana Turnos." una vez logueado — un
  subtítulo que sólo repite el título con otras palabras se borra (regla
  nueva en el skill personal `ui-rules`, no es específica de este repo).
