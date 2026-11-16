# Clic en la hora = hora elegida: ?slot= salta la grilla (y la home arma filas aparte)

**Fecha:** 2026-07-14 · **Commit:** 482464e

## Problema
Clic en la pastilla "Mañana, 08:00" del listado re-mostraba la selección de
horarios en el wizard. The product owner: "Don't show it. Show the thing I already chose.
Continue to the next step" — revoca la decisión 2026-07-12 de mostrar la grilla
igual con el slot preseleccionado.

## Solución
- Filas de listado con turno reservable llevan `slot_href` (`?slot=YYYY-MM-DDTHH:MM`)
  y `info_href` = wizard pelado ("más horarios" = ver horarios). Server:
  `hospital.py:_slot_fields`; la pastilla lo prefiere en `listado.js` (celda proximo).
- Wizard (`servicio.js:stepHorario`): `?slot=` válido → setea wiz.preferencia/
  slotMedico/slotCuando y salta a `stepCuenta()` UNA vez (`slotSaltado`);
  `mountAuth` muestra "Turno elegido" + Cambiar. Slot inválido/vencido → grilla
  normal sin preselección. Cambiar/"Prefiero otro horario" siguen mostrando grilla.

## Gotchas (lo no obvio)
1. **La home NO usa los row builders del server**: `index.html:servRow()` arma
   las filas client-side desde `/api/servicios`. Todo cambio de campos de fila
   (slot_href, info_href…) necesita su espejo ahí o la home queda vieja.
2. **`_medico_servicio` elegía por cluster catch-all**: en "otras" conviven
   especialidades, y el modo-priority mandó a Castagnino (oftalmólogo) a
   Dermatología. Ahora matchea primero por nombre de servicio ≈ especialidad
   (diacritic-fold). Cualquier lógica "servicio del médico" debe matchear por
   especialidad antes que por cluster.
3. El test de consistencia de versiones exige bump parejo: `listado.js?v=` vive
   también en `mis-solicitudes.js`, no sólo en hospital.py/index.html.
