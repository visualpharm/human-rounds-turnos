# Tabla embebida en una card: breakpoint por container query, no por viewport

**Fecha:** 2026-07-18 (ronda 1), actualizado 2026-07-18 (ronda 2) · **Contexto:** rework de "Mis turnos" (my-requests.js)

## El problema

La tabla de Mis turnos se renderiza en DOS contenedores de ancho distinto:
`/my-requests` (página completa, ~1000px útiles) y el panel "Mis turnos" del
dashboard de `/account` (card de ~700–760px al lado del riel). Un breakpoint
por viewport (`@media(max-width:…)`) apilaba bien en un contexto y en el otro
desbordaba EN SILENCIO — el overflow quedaba escondido por el `overflow-x:auto`
del wrapper, así que a simple vista "andaba" pero las celdas se partían en dos
líneas sin necesidad (la queja original del responsable del producto, screenshot 2026-07-18).

## La solución

`container-type: inline-size` en el wrapper (`.mt-wrap`) y el apilado con
`@container mt (max-width: 820px)`: el layout responde al ancho REAL del
contenedor, no al de la ventana. 820px = ancho medido al que las 4 columnas
(Servicio | Turno con médico | Estado fusionado | acciones) dejan de entrar
sin partir líneas — breakpoint content-driven (skill responsive), no de device.

## Reglas que salieron de acá

- Una tabla/componente que vive en más de un contenedor (página + card, panel
  admin + modal) NUNCA decide su layout por viewport: container query.
- El probe de overflow (skill responsive) hay que correrlo EN CADA contexto de
  montaje, no una vez por página: el mismo componente pasa en una superficie y
  falla en otra.
- `overflow-x:auto` esconde regresiones de ancho: si el diseño dice "acá no
  hay scroll horizontal", el probe debe verificar `scrollWidth == clientWidth`
  del wrapper, no sólo el documento.

## Dónde está

`my-requests.js:injectTblStyle` (`.mt-wrap{container-type:inline-size}` +
bloque `@container mt`). Soporte: container queries son baseline 2023 — todos
los navegadores del público objetivo los tienen.

## Ronda 2 (2026-07-18): el 820px de la ronda 1 apilaba TODO — la tarjeta nunca mostraba tabla

El responsable del producto pidió que la tabla completa (4 columnas) entrara en la tarjeta de Mi
cuenta (~706px reales de `.mt-wrap`) — 820px de umbral la apilaba SIEMPRE ahí,
nunca mostraba una tabla real. Bajar sólo el número no alcanzó: medir con el
probe de overflow (forzando `.mt-wrap{container-type:normal!important}` +
ancho por inline-style, en vez de adivinar) encontró que el contenido natural
real era **776px**, no los ~650px que un cálculo a mano (sin medir en
navegador) sugería — y esos 776px NO desbordaban visiblemente la página
(`document.documentElement.scrollWidth`), sólo desbordaban la TARJETA (la
tabla se sale del `.mt-wrap`/`.dcard` sin llegar al borde del viewport) — el
probe de overflow de la skill `responsive`, que compara contra
`document.documentElement.clientWidth`, NO detecta este caso. Hay que además
comparar `wrap.scrollWidth` contra `wrap.clientWidth` del contenedor real
cuando el componente vive dentro de una card angosta, no sólo contra el
viewport.

Dos bugs de ESPECIFICIDAD CSS (no de contenido) inflaban ese número:

1. **Una clase sola pierde contra clase+tipo.** `.mt-svc{white-space:normal}`
   (para permitir que nombres largos como "Otorrinolaringología" — 20
   caracteres sin espacio, uno de varios así en el catálogo real — envuelvan)
   perdía contra `.mt-tbl td{white-space:nowrap}` pese a venir DESPUÉS en la
   hoja: `.mt-tbl td` (clase+tipo) tiene más especificidad que `.mt-svc` (una
   sola clase). El elemento seguía con `white-space:nowrap` computado —
   confirmado con `getComputedStyle`, no asumido. Fix: `.mt-tbl td.mt-svc{...}`
   (clase+tipo también) para igualar/ganar la especificidad.
2. **La misma trampa en el bloque `@container` del apilado.**
   `.mt-tbl td:not(:first-child){width:1%}` (arriba, para el truco de columnas
   auto-ajustadas) seguía ganando sobre el `.mt-tbl td{width:100%}` del bloque
   apilado — `:not(:first-child)` cuenta como una pseudo-clase, así que esa
   regla tiene MÁS especificidad aunque el bloque `@container` esté después en
   la hoja. Efecto real: en apilado, cada `td` (menos el primero) colapsaba a
   ~3px (1% del ancho del bloque contenedor), así que el motivo de rechazo (la
   única celda que envuelve a propósito) se veía UNA PALABRA por línea. Fix:
   repetir el selector completo `.mt-tbl td:not(:first-child){width:100%}`
   DENTRO del bloque `@container` (misma especificidad + orden de aparición sí
   decide ahí).

Con ambos fixes + `overflow-wrap:anywhere` en la columna Servicio (permite
partir a mitad de palabra un nombre sin espacios, en vez de que ese nombre
fije el ancho mínimo de toda la tabla), el contenido natural bajó de 776px a
~630px — la tarjeta (706px) entra con ~75px de margen. Umbral final: 660px
(no 820px).

**Regla que sale de acá:** al mezclar un selector "genérico" (`.mt-tbl td`)
con uno "específico" para una columna (`.mt-svc`), o un override dentro de un
`@container`/`@media` que reusa una clase ya usada afuera, verificar la
especificidad real (contar clases/tipos/pseudo-clases) — el orden en la hoja
NO decide si las especificidades son distintas. Cuando algo "no se aplica"
pese a estar en el CSS, `getComputedStyle(el).propiedad` en el navegador dice
la verdad; no asumir por lectura del código.
