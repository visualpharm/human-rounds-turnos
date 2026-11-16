---
title: Registro canónico de columnas de listado.js (entidad-primero + Turno combinada)
scope: repo
area: design
tags: [listado, tablas, links, table-design, sort, color]
created: 2026-07-11
refs: [commit 9e8471d]
status: active
---

## Problem
Cada superficie de listado (home, /medicos, /centro/<slug>, /d/<cluster>, /estudio/…,
mis-solicitudes) armaba su propia lista de columnas a mano: labels y orden distintos
para el mismo dato, el centro linkeando directo a sacar turno, walk-ins "abiertos
ahora" desplazando a los turnos reservables en el sort y brillando con el mismo verde.

## Root cause
No había una única fuente de verdad para tipo/dato/label/orden de columna, ni una
calibración única de sort/color del "cuándo".

## Solution
`listado.js` define `PRESETS`/`COL_ORDER` con el orden canónico ENTIDAD-PRIMERO
(the product owner, 2026-07-11 13:40): entidad (servicio/estudio/medico) · contexto
(especialidad/area) · centro/lugar · estado · turno · proximo · orden. El payload
de `cols` acepta claves string, `{preset:"x", ...override}` u objetos crudos (van
al final) y SIEMPRE se reordena al canon. Espejo Python:
`hospital.py:_COL_PRESETS`/`_COL_ORDER`/`_cols(*claves, **overrides)`.
`turno` = columna COMBINADA estado+acción ("el control ES el estado"): reservable →
botón "Sacar turno" (href de la fila); walk-in → gris no clickeable "En orden de
llegada". No existe columna `go`. Centro/especialidad SIEMPRE linkean a su página
(`centro_href`/`esp_href`). Sort de próxima (responsable del producto, 12:27): tier "0" exclusivo de
reservables; walk-in en tier "1" (abierto → apertura por fecha → genéricos)
(`listado.js:ORDEN`). Color por cercanía sólo en reservables (≤7 días verde, 8-30
ámbar, >30 gris); walk-in siempre atenuado (`.lst-time--walkin`, kind walkin→muted).

## How to apply
Nueva tabla → `hospital._cols("servicio", "centro", "turno", "proximo", ...)`
(nunca dicts de columna a mano); ajuste puntual como kwarg
(`_cols(..., centro={"hideSm": True})`). Nuevo dato "dónde" con link → agregar
`<algo>_href` a la fila del row-builder, nunca reusar `href` (ese es la acción de
la fila). Contrato completo: `test/test_listado_consistencia.py` +
`test/listado-orden.test.mjs`.

## v2 (2026-07-12): tarjeta mobile entidad-primero + colapso de columnas uniformes
- Tarjeta mobile (<640px): línea 1 = entidad + contexto (`.lst-td-sub`) a la
  DERECHA; línea 2 = el "cuándo"; datos cortos después. Sin columna de entidad
  (p.ej. "Dónde te lo hacés", donde la fila ES el centro) el sub `:first-child`
  se comporta de entidad (izquierda, crece).
- `colapsoUniforme` (listado.js): columna donde TODAS las filas visibles dicen
  el mismo texto genérico conocido ("Te confirma el centro" / "Por orden de
  llegada" / "Vení en el día") se esconde y sale UNA nota-oración al pie
  ("Elegí dónde atenderte: el centro te confirma la fecha y la hora del
  turno." si hay columna de centro y varias filas). La columna Turno colapsa
  igual cuando toda la lista es walk-in. Un botón/acción y las celdas con dato
  real (pastilla con fecha/apertura) NUNCA colapsan. Se recalcula por render
  (búsqueda/filtros pueden volver uniforme al subconjunto). En la búsqueda de
  la home, notas idénticas de servicios+médicos se dejan una vez (index.html).
- La columna sede en la página del propio centro no repite el nombre del
  centro: sólo la sede distinta (hospital.py `_page_centro`).
