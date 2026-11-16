# La marca por tema se rompe en las copias escritas a mano

**2026-08-01.** La demo pública (`demo.humanrounds.org`) ya corría con
`data-theme="humanrounds"` y decía "Human Rounds" en todos los títulos, y aun
así mostraba **la marca vieja**: el logo del nav era el corazón con pulso de
"Sana" (`/logo.png`, byte por byte igual a `sana-mark.png`), el ícono de la
pestaña era la "P" verde de Pinamar, y la paleta/tipografía eran una
aproximación (`#c96342` + Inter) en vez de las del sitio apex.

El tema estaba bien elegido. Lo que estaba mal era **todo lo que no salía del
tema**.

## El patrón

`human-rounds.css` re-skinea el sitio entero con cambiar `data-theme`, así que
es fácil creer que la identidad "sale de tokens". No toda: hay pedazos de marca
que un token CSS no puede resolver, y cada uno se había escrito a mano en su
página o en su módulo. Cuando el tema cambió de color, esas copias se quedaron
con el anterior — en silencio, sin romper nada, sin fallar un test.

Los cuatro lugares encontrados, y por qué cada uno se le escapa al CSS:

| Qué | Dónde estaba a mano | Por qué no lo puede resolver un token |
|---|---|---|
| `<link rel="icon">` | 12 páginas, `href="/static/favicon.svg"` fijo | El CSS no llega al `<head>`; hay que resolverlo server-side |
| Familias tipográficas | `admin.html` pedía las **cuatro** familias de los dos temas | El `<link>` a Google Fonts es markup, no token |
| `--accent` para el mail | `notifications.py` | Un mail HTML no tiene CSS con variables |
| `--accent` del selector | `admin-theme.js` (swatch de cada tema) | El swatch pinta el tema que **no** está activo |

Y el logo del nav sí salía de un token (`background-image` por `[data-theme]`),
pero apuntaba a un archivo (`/logo.png`) que nadie había actualizado.

## Lo que hicimos

- El favicon pasa por el mismo mecanismo que ya resolvía el tema:
  `theme.favicon()` + placeholder `{{favicon}}` en `theme.inject()`. Las dos
  instalaciones salen del MISMO build, así que esto **no** se puede resolver
  eligiendo qué archivo se copia en el deploy.
- `admin.html` usa `{{fuentes}}` como el resto del sitio (de paso dejó de bajar
  dos familias que no usa).
- Las tres copias de `--accent` quedaron **atadas al token por un test** que
  parsea `human-rounds.css` y las compara. Ese test encontró de arranque una
  cuarta deriva que nadie buscaba: `branding.py` tenía el verde de Pinamar
  **anterior** al repaso de contraste del 2026-07-28 (`#0e8b50` vs `#0d7d48`).

## La trampa de contraste

El repaso WCAG AA del 2026-07-28 se le había hecho sólo al bloque `pinamar`.
El de `humanrounds` nunca pasó por ahí y tenía `--text-3` en 2,9:1 y `--text-4`
en 2,2:1. Mientras ese tema fue "el otro", no importó; el día que se volvió el
de la demo pública, sí.

Dos cosas que no son obvias:

- **El color de marca del apex no sirve tal cual.** `humanrounds.org` pinta
  `#e54b27` en bloques grandes y usa `#bd2f16` para el *texto* de los links.
  En la app `--accent` es una sola variable y casi siempre es texto o relleno
  de botón: hay que tomar el profundo (3,2-3,9:1 vs 4,8-5,9:1).
- **El piso hay que medirlo contra la PEOR superficie, no contra `--bg`.**
  El papel hondo de esta marca (`--bg-2 #eee8dd`) es más oscuro que el gris de
  `pinamar`, así que un tono que pasa allá puede no pasar acá: el mismo bronce
  `#946318` da 4,68:1 sobre `--bg` y 4,24:1 sobre `--bg-2`.

`test_theme.py:test_texto_pasa_AA_en_los_dos_temas` ahora corre el cálculo
sobre **los dos** bloques y las cuatro superficies, así que un tema nuevo no
puede repetirlo.

## Qué mirar la próxima vez que se agregue o recolore un tema

1. `grep` el hex viejo en `.py`/`.js`/`.css`/`.mjs` — no alcanza con cambiar el CSS.
2. ¿Hay algún `<link rel="icon">`, `family=`, o hex de marca escrito en una página?
3. ¿El tema nuevo pasó el cálculo de contraste contra sus **propias** superficies?
4. `/logo.png` (y `static/logo-admin.png`, que es el mark del MOTOR, no el del
   municipio — ver `panel_login.py`) ¿son de la marca actual?
