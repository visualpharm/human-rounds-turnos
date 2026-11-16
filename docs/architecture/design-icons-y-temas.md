# Iconos en mockups de Claude Design + los dos temas del producto

Dos reglas que se juntan porque las pisa el mismo flujo (`design-loop`): qué ícono
usar en un mockup, y en cuál de los dos temas del producto hay que diseñarlo.

## 1. El pack de íconos es Icons8 "sf-regular", pixel-perfect

El único pack real del proyecto es Icons8 estilo **sf-regular** (algunos íconos viejos
quedaron también en `ios7`, pero lo que el código sirve HOY es siempre `sf-regular` —
ver `static/icons/*-sf-regular.{svg,png}` y sus referencias en `chat-widget.js` /
`chat-ui.js`). Un mockup en Claude Design nunca puede referenciar `/static/icons/` del
repo (host distinto), así que la tentación es dibujar un ícono a mano (`<svg><path>`) o
tirar un emoji — **las dos cosas están prohibidas**, igual que en cualquier otra
imagen del proyecto (`imagegen` skill, "never hand-draw SVG/canvas").

Al diseñar o revisar un `.dc.html`:
- El ícono tiene que ser el glyph real de Icons8 (pedilo con la skill `icons8-icons`,
  metáfora + estilo `sf-regular` + tamaño), no una aproximación.
- El tamaño tiene que ser el MISMO que usa el código real, no "parecido". Tabla de
  referencia (medida en `chat-ui.js`/`chat-widget.js`, es la única fuente):

  | Uso | Tamaño real |
  |---|---|
  | Glyph de WhatsApp en el header del chat | 17×17px |
  | attach / mic (composer) | 18×18px |
  | send (composer) | 19×19px |
  | ícono de título (`.stzc-title img`) | 24×24px |

  Cualquier otro ícono nuevo: buscá su tamaño real en el CSS del componente que
  imita antes de dibujarlo en el mockup — no inventes un valor redondo.

**Enforcement:** `.claude/hooks/design-icon-glyph-guard.py` (`PostToolUse` sobre
`mcp__claude-design__write_files`) escanea cada `.dc.html` recién escrito por
emoji-como-ícono, `<svg><path>` a mano, o tamaños de ícono fuera de la tabla de arriba.
Es un aviso (`systemMessage`), no bloquea — así que sigue haciendo falta leerlo.

## 2. El producto tiene DOS temas — diseñá (o migrá) en los dos

`human-rounds.css` implementa el theming con `:root[data-theme="…"]`, no son dos
despliegues separados de CSS:

| Tema | Selector | Acento | Fondo | Tipografía | Dónde vive HOY |
|---|---|---|---|---|---|
| **Human Rounds** (public runtime default) | `[data-theme="humanrounds"]` | links are ink with a faint underline, buttons are black pills, accent is coral `#874f2e` (entity names, the booking button, status/focus) | beige `#f4f1ea` | Manrope + IBM Plex Mono | deployment-configured |
| **Pinamar** (reference deployment and bare-CSS compatibility fallback) | `:root` / `[data-theme="pinamar"]` | green `#0d7d48` | `#f6f7f5` | Ubuntu | `pinamar.humanrounds.org` |

**Human Rounds recolor, 2026-09-14 (the product owner: "more cursor like").** The theme now
reads like cursor.com: beige paper (`--bg #f4f1ea`, `--bg-2 #e9e5da`, card
`--panel #fffdf8`), **links in blue** — the blue measured live on
`devin.ai/blog/windsurf-wave-9-swe-1` (`--text-blue` = `#337df4`), lowered two
luminance steps in the same hue to pass AA on beige: `--accent #1a5ccc`,
`--accent-hover #2166dd` — and the **primary button as a black pill** instead of
an accent-filled one. The button is its own pair of roles now, not a reuse of the
accent: `--btn-bg` / `--btn-bg-hover` / `--btn-fg` / `--btn-radius` (999px) and
the beige outline twin `--btn2-bg` / `--btn2-bg-hover` / `--btn2-fg` / `--btn2-bd`.
Those tokens exist **only** in the humanrounds block; every button rule in the
product consumes them with a fallback (`var(--btn-bg,var(--accent))`,
`var(--btn-radius,11px)`), so "pinamar" renders exactly as before. Two knock-on
recolors were forced by the new paper: `--select` moved to ink (the old slate blue
now collided with the blue accent) and `--info` desaturated to `#3c5a6e`; `--warn`
dropped to `#8f5700` to keep AA on the darker beige
(`test_theme.test_texto_pasa_AA_en_los_dos_temas` pins all of it).

**Coral accent + consistency pass, 2026-09-15.** the product owner: "check the contrast with
the new coral scheme so we don't have blue on beige of the same tone" — the blue
accent (by then demoted to denim `#2c5673`) left the demo with blue buttons on
beige while humanrounds.org had moved to a clay/coral accent
(`oklch(.52 .085 52)` = `#8f5a39` in its landing.css). The humanrounds accent is
now that family: `--accent #874f2e` (one step darker for AA: 5,7:1 on `--bg`,
5,1:1 on `--bg-2`, 6,5:1 under white button text), `--accent-hover #8f5a39` (the
public value, lighter as eval 8c demands), `--accent-soft #f8e8de`, `--accent-bd
#e6c2ad`. The three hand copies (`branding.py`, `notifications/send.py`, the
Appearance swatch) moved with it. Where the accent may still appear: the entity
name in a listing (exam, doctor), the booking button, status/focus — every other
text link is ink with the theme underline (the same day, every patient-site rule
that still wrote `text-decoration:none` on a text link was removed: listing
names/"Más horarios"/examples, wizard prose links, account + footer links).
Same pass: listing rows have ONE fixed height regardless of a button or avatar
(`listing.mjs`, `tbody td{height}` at ≥901px, everything vertical-align middle),
the footer follows the listing breakout width, the logotype grew to 52/24px, and
`stories/Consistency.stories.js` snapshots the two stacked tables.

**Links iteration, 2026-09-14:** the product owner reversed the "links never underlined" rule
(2026-08-07 was the ban). In humanrounds, a link is now rendered as **ink text with
a faint underline** (the Linear pattern: `--link-underline` muted denim, `--link-underline-hover`
stronger on hover, both theme-driven). Pinamar keeps colored bare links (no change).
Pinamar uses color alone for affordance; in humanrounds the affordance is the
underline itself (theme-optional), and it lives
ONLY in `tokens.css` + the base `a{}` rule in `human-rounds.css`. A link never declares
`font-weight` — it inherits the surrounding text's weight. No other file may hardcode
underline declarations; `human-rounds.css` is the sole affordance source.

Cada tema tiene su propio proyecto de Claude Design — **no son la misma carpeta**:

| Tema | Proyecto Claude Design | project_id |
|---|---|---|
| Pinamar | "Pinamar design system" (alias histórico: "Pinamar Turnos") | `dbe97c70-4426-4630-beb7-bcf5ba3f2f87` |
| Human Rounds | "Human Rounds" | `8c286ddb-3fb5-49dd-aac6-220d3a1d791b` |

`.claude/design-loop.json` sólo apunta al proyecto Pinamar por default (`project_id`
de nivel superior) — es deuda histórica de antes del rebrand a Human Rounds
(2026-07-31), no una decisión de que Pinamar sea "el" tema. Para trabajar el tema
Human Rounds, pasá el `project_id` de la tabla de arriba explícitamente en vez de
confiar en el default del config.

### Regla: un handoff estructural se aplica a los DOS temas

Un cambio de **estructura** (pantalla nueva, componente nuevo, layout distinto) que se
diseña o se pulla para un tema tiene que revisarse contra el otro tema también — si el
componente es compartido (`chat-widget.js`, `nav.js`, `admin-table.js`, etc.), la
implementación real ya sirve ambos vía `data-theme`, así que un Phase PULL que sólo
mira un mockup puede dejar el otro tema con un componente visualmente roto o
desactualizado sin que ningún test lo note (los temas son sólo CSS, no ramas de
código). Un cambio puramente de **color/token** (no estructural) no necesita el
mockup espejado en el otro proyecto — el CSS ya resuelve el color por tema solo.

## EVAL — correr antes de dar un mockup por bueno

1. **¿Todos los íconos son Icons8 sf-regular reales?** (no emoji, no `<svg><path>` a
   mano) — el hook lo avisa solo, pero confirmá que no se ignoró el aviso.
2. **¿El tamaño de cada ícono coincide con la tabla de arriba?** Si es un ícono nuevo,
   ¿se buscó su tamaño real en el componente que imita antes de fijarlo en el mockup?
3. **¿En qué tema se diseñó?** ¿Es un cambio estructural? Si sí: ¿existe (o se generó)
   el mockup espejo en el otro proyecto de Claude Design?
4. **Al hacer Phase PULL:** si el componente tocado es compartido entre temas
   (verificar tokens `--accent`/`--bg`/`--font` en vez de colores hardcodeados), ¿se
   probó visualmente con `data-theme="pinamar"` Y `data-theme="humanrounds"` antes de
   dar el pull por terminado?
