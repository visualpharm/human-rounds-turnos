# Espejo del design system "Pinamar design system" (Claude Design)

**Fuente canónica:** el proyecto de Claude Design
[Pinamar design system](https://claude.ai/design/p/dbe97c70-4426-4630-beb7-bcf5ba3f2f87)
— guidelines y specs de componentes viven ALLÁ; este directorio es un espejo
de solo-lectura para que las sesiones de implementación tengan el DS offline.
Re-sincronizar con `/design-loop sync` (skill de usuario `design-loop`; config en
`.claude/design-loop.json`). Última sync: 2026-07-18.

**Tokens — RETIRADOS de este espejo (Tier 5 slice 1, 2026-08-06).**
`tokens/*.css` y `styles.css` (el wrapper que sólo los `@import`-eaba) ya no
viven acá: eran una copia hecha a mano que el propio archivo admitía
desactualizada frente a la paleta real del producto. La fuente de tokens
canónica es ahora **`tokens.css`** en la raíz del repo (extraída verbatim de
`human-rounds.css` — ver `docs/roadmap/tier5-frontend-design.md`, "tokens.css
as the published design contract"). Un futuro sync de design-loop compara los
tokens exportados del proyecto de Claude Design contra ESE archivo, no contra
una copia local que nadie re-sincronizaba.

**OJO — divergencia de marca deliberada:** este DS estandariza la identidad
**verde municipal "Pinamar Turnos"** (Ubuntu, escudo, #128A40→#0E8B50). El producto
hoy en prod es "Sana Turnos" con dos temas (`data-theme` pinamar/sana). El DS es la
dirección de diseño, no el estado shipped — al implementar un pull, traducir a los
tokens reales de `tokens.css` salvo que el responsable del producto pida adoptar el re-brand.

---

# Pinamar Turnos — Design System

A calm, civic, public-health design system for **Pinamar Turnos**, the appointment
("turnos") platform for the **Hospital de Pinamar and the CAPS** (centros de atención
primaria de la salud) of the Partido de Pinamar — Pinamar, Ostende, Valeria del Mar
and Cariló. It re-skins the original Sana Turnos product into the Municipalidad de
Pinamar's green→emerald identity, on one humanist type system (Ubuntu).

Scope: a visual-style reference. Example screens describe a municipal health product;
the fictional patient data (names, DNIs, coverage) is illustrative only.

## Sources
- Live product + brand: the Pinamar Turnos codebase (`sana-turnos/`, local folder) —
  `human-rounds.css`, `nav.js`, `cuenta-form.js`, `dependientes.js`, page templates.
- Municipal brand reference: the "Pinamar Design System" exploration in the parent
  project (green→emerald municipal system, escudo lockup, `respirá pinamar.`).
- The original product used a terracotta palette; this system standardises on the
  municipal green — that is the canonical identity here.

## Foundations (tokens)
Retired from this local mirror (see the note at the top) — the CD project still holds
its own `styles.css`/`tokens/*.css` structure (fonts, colors, specialty-palettes,
typography, spacing, effects), described here only as a map of what's THERE; the
canonical, synced-against copy is the repo's own `tokens.css`.

## CONTENT FUNDAMENTALS
Warm, plain, and civic. Argentine Spanish in **voseo** — imperatives address the
patient directly: "Sacá turnos", "Encontrá los servicios", "Dejá al menos un teléfono".
Second person ("vos"), never institutional third person. Copy explains the *why* in one
plain sentence ("los necesitamos para avisarte de tus turnos"), never jargon or legalese.
Tone is honest and unbranded — "Hecha y sostenida por vecinos de Pinamar. Gratis y sin
ningún negocio detrás." No marketing gloss, **no emoji**, no exclamation. Numbers that
matter (DNI, phone, counts, times, order codes) are written in mono. Titles are short and
concrete ("Mi cuenta", "Médicos y profesionales"). Sentence case everywhere except the
tiny uppercase section eyebrows (letter-spaced).

## VISUAL FOUNDATIONS
- **Color.** Warm off-white field (`#F6F7F5`) with white cards. Brand is a green→emerald
  family: `#128A40` mark, `#0E8B50` action/links, `#0B6E33` for hover and AA text; a
  `#17A46A→#0E8B50` gradient fills hero CTAs and avatars. Mint (`#EAF4EC`) is the tint for
  active nav, chips and wells; `#F4FAF6` tints featured cards and nested insets. Blue
  (`#2B5CAD`) carries information; amber (`#C08A2E`) caution. A deep gradient
  (`#0E1A12→#0E6B44`) is reserved for the municipal government bar.
- **Type.** Ubuntu throughout — 300 for campaign display ("respirá pinamar."), 700 for
  product headings (H1 clamps 30→44px, −.02em), 500 for nav/labels/buttons, 400 for body.
  Ubuntu Mono for exact values. Body/inputs 16px; labels 13px; eyebrows 13px uppercase
  `.14em`.
- **Shape & depth.** Nav items and chips are full pills (999px); cards are soft 14px
  (18px for identity/summary cards); inputs 10px; icon tiles 12px. Shadows are long, soft
  and very low-opacity, tinted rgba(14,26,18) — most cards rely on a hairline
  (`#E6E8E6`) border plus a faint drop; avatars get an emerald-tinted glow.
- **Borders & inputs.** Inputs use a 1.5px `#D6DAD6` outline (so they read as inputs);
  focus swaps the border to emerald and adds a 3px `rgba(14,139,80,.15)` ring. Nested
  secondary inputs use a softer `#DCE4DE` border.
- **Layout.** Centered 1080px column, 40px gutters. Forms use stacked labels with a
  two-up field grid; subordinate data (a plan's member number) nests in a mint inset with
  a 2px emerald-mint left border. The account page pairs a sticky identity rail with the
  form.
- **Motion & states.** Quiet: .12s transitions. Hover darkens links to `#0B6E33`,
  lightens button fills (brightness 1.06), tints ghost/soft controls to mint. Press nudges
  1px down. No bounces, no large motion.

## ICONOGRAPHY
Inline **stroke SVGs**, ~1.7–2px, rounded caps/joins, drawn in `currentColor` so they
take their container's ink (specialty tiles colour them per family). Medical glyphs:
stethoscope, scan/imaging, brain, baby, bones, heart, syringe, tooth, ear. No icon font,
no emoji, no filled icons. The brand mark is the **municipal escudo** (`assets/pinamar-logo.svg`,
cropped from the real lockup) paired with the "Pinamar Turnos" wordmark — do not redraw it.
(The original Sana heart-pulse mark is intentionally not carried over.)

## Index / manifest (en el proyecto de Claude Design)
- `styles.css` — global entry point (import list only).
- `tokens/` — fonts, colors, specialty-palettes, typography, spacing, effects.
- `guidelines/cards/` — foundation specimen cards (Colors, Type, Spacing, Brand).
- `components/` — forms/ (Button, Input, SegmentedControl, Combobox), data/ (Card,
  Chip, IconTile, DirectoryRow, SpecialtyCard), navigation/ (TopNav).
- `ui_kits/pinamar-turnos/` — Home/Directorio, Médicos, Mi cuenta (static recreations).
- `assets/` — `pinamar-logo.svg` (escudo).
- `SKILL.md` — downloadable Agent-Skill wrapper.

## Intentional additions
- **IconTile** and **SpecialtyCard** are named wrappers over patterns the product uses
  inline (colored specialty tiles, "Más buscadas" cards) — added so consumers reuse the
  exact palette/shape rather than re-rolling them.
