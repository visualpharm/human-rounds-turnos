# `packs/example-town/*.json` es GENERADO: editarlo a mano se revierte solo

2026-08-05.

## El síntoma

Agregué el campo `icon` a los 13 clusters de `packs/example-town/clusters.json`,
`git status` lo mostró modificado, corrí la batería de tests, y el archivo volvió
solo a su estado original — sin conflicto, sin error, sin mensaje. Lo hice dos veces
antes de mirar por qué.

## La causa

Ese archivo lo escribe `scripts/generate_example_town_catalog.py` (junto con
`servicios.json`, `medicos.json`, `centros-salud.json`, `especialidades.json`), y hay
un test que corre el generador para verificar que lo commiteado esté al día. O sea:
el propio suite de tests pisa la edición a mano. El `note` del JSON lo dice —
"Generado por scripts/… — NO editar a mano" — pero el archivo se abre igual que
cualquier otro y nada frena la edición.

**Antes de editar un `.json` bajo `packs/`, buscá si algo lo genera:**

```
rg -n "_write\(\"<archivo>" scripts/
```

## El bug que estaba tapando

`packs/example-town/pack.json` declara `"compose": {"clusters.json": "replace"}`, así
que su copia tapa entera la de `packs/global/` — y el generador nunca emitía `icon`.
Resultado: en la demo NINGÚN cluster tenía ícono, mientras que en Pinamar (que hereda
de `global`) los 13 lo tenían. Un bug que sólo existe en una instalación es
exactamente lo que produce un `replace`: **lo que un pack reemplaza, lo reemplaza
completo, incluidos los campos que nadie se acordó de copiar.**

El arreglo no fue copiar la columna: el `icon` se deriva del `id` en el generador,
porque los ids de este pack ya reusan a propósito los de `global` justamente porque
comparten los `/static/icons/<id>.png`. Repetir la ruta habría sido una segunda copia
de esa relación esperando desincronizarse.

## La regla

**Un pack con `compose: replace` necesita un chequeo de campos contra el pack que
reemplaza**, no sólo de ids. Hoy no existe: el que agregue un campo nuevo a
`packs/global/clusters.json` se lo va a olvidar de nuevo en `example-town`, y sólo se
va a ver mirando la demo.

Relacionado: [data-packs-resolved-path-cache-key.md](data-packs-resolved-path-cache-key.md),
[docs/architecture/data-packs.md](../../architecture/data-packs.md).
