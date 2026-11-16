# CSS huérfano al mudar una pantalla: los controles quedan desnudos y los tests no lo ven

**Fecha:** 2026-08-06 · **Área:** admin / Solicitudes · **Síntoma:** el responsable del producto mandó una
captura del panel de Resolver: el campo de fecha y hora salía con el borde gris
inset del navegador, en monospace, 9px más bajo que todo lo demás — y «Aprobar»
era un botón **negro** en vez del color de marca.

## Qué pasó

La pantalla se rehizo de la tarjeta vieja (`.acts`, ancho completo, 1240px) al
panel de detalle (`.sol-res-row`, 428px). Se mudó el markup. **No se mudó el
CSS ni dos handlers.** `.acts` dejó de renderizarse en TODO el repo, así que
cada regla escopeada a él quedó viva en el archivo pero sin aplicar a nada:

| Regla huérfana | Consecuencia |
|---|---|
| `.acts input[type=datetime-local],.acts .motivo-otro{…}` | los dos campos libres quedaron con el chrome nativo del navegador |
| `.acts .btn-ok.btn-suggest{background:var(--accent)}` | «Aprobar» cayó al `.btn-ok` genérico (negro) y `.btn-suggest` dejó de significar nada |
| `.acts .cmb-in`, `.acts .adm-sel` | los tres controles de la fila volvieron a tener tres alturas distintas |
| 3 × `@container` colgados de `.card-actions` | tampoco se renderiza: el apilado en angosto era letra muerta |

Y en JS, dos handlers seguían buscando el contenedor por la clase muerta:

```js
sel.closest('.acts').querySelector('.motivo')   // → TypeError: null
f.closest('.acts').querySelector('.turno')      // → TypeError: null
```

Efecto real: elegir «Otro motivo…» **tiraba una excepción y el campo no
aparecía nunca**, y la fecha libre nunca llegaba al input oculto. Dos funciones
del panel estaban rotas, no sólo feas.

## Por qué ningún test lo agarró

Cuatro tests cubrían exactamente esta fila… afirmando sobre el CSS de `.acts`:

```js
assert.match(src, /\.acts\{--decision-control-w:170px;/);
assert.match(src, /\.acts \.btn-no\{[^}]*color:var\(--text-2\)/);
```

Como el CSS muerto seguía **en el archivo**, los `assert.match` pasaban.
Verde permanente sobre una pantalla que ya no existía. Un test que mira el
texto de la hoja de estilos comprueba que la regla está escrita, nunca que le
llegue a un elemento.

## La regla

**Mudar una pantalla de contenedor no termina hasta que el selector viejo no
existe en ningún lado — ni en el CSS, ni en un `closest()`, ni en un test.**

Al renombrar/reemplazar el contenedor de una pantalla:

1. `rg "\.viejo\b"` sobre `*.js`, `*.html`, `*.py` y `test/`. Separá los usos en
   *renderizado* (hay markup que lo emite) y *huérfano*.
2. Si NADA lo renderiza: mudá las reglas al contenedor nuevo y borrá el bloque
   viejo. Dejarlo "por las dudas" es lo que produjo este bug — se lee como si
   estuviera estilando algo.
3. Buscá `closest('.viejo')`, `querySelector('.viejo')`, `matches('.viejo')`.
   Un `closest()` que devuelve `null` no falla despacio: tira TypeError.
4. Reapuntá los tests al contenedor nuevo y dejá una guarda de no-retorno:

```js
assert.ok(!/\.acts\s*\{/.test(src) && !/\.acts\s+\.[\w-]+[^{]*\{/.test(src),
  'la tarjeta vieja `.acts` ya no tiene reglas CSS que puedan quedar huérfanas');
```

5. Verificá **estilos computados en el navegador**, no el texto del CSS. La
   señal barata de un control sin estilo: `border: 2px inset rgb(118,118,118)`,
   `borderRadius: 0px`, `fontFamily: monospace|Arial`, y un alto que no coincide
   con ningún otro control de la pantalla.

## Bonus del mismo barrido

`.sol-nm`/`.sol-mut` no fijaban `font-size`, así que heredaban los **15px del
`body`** dentro de un renglón de 34px, al lado de una fecha de 12px y un
veredicto de 12.5px. Una fila de tabla densa tiene que fijar su propio tamaño:
heredar el cuerpo de la página es siempre un accidente, nunca una decisión.

## Enlaces

- `admin-solicitudes.js` — `.sol-res-row` / `.sol-res` (el módulo es dueño de su CSS)
- [listados-y-busqueda.md](../../architecture/listados-y-busqueda.md)
- [admin-cola-y-agenda.md](../../architecture/admin-cola-y-agenda.md)
