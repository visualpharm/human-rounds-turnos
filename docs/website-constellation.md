# Public-website constellation diagram — concept + content

## Revisión 2026-07-31 bis — esfera 3D compacta (vigente, se suma a la revisión de abajo)

Feedback del responsable del producto sobre la constelación plana: demasiado alta. Rediseño shippeado
(sana `b78e12b`):

- **Mitad de altura o menos**: `.const-figure` pasa de `aspect-ratio:3/4` (~907px)
  a `height: clamp(400px, 46vh, 460px)` — la altura ya no depende del ancho.
- **Esfera 3D**: nodos en latitudes por banda (instituciones en el ecuador — el
  switcher de país sigue morfando un anillo reconocible; capacidades en dos
  bandas medias; outcome dots hacia los polos). Proyección débil billboarded
  (texto siempre plano), 4 niveles discretos de niebla: el lado de atrás se ve
  chico, translúcido y borroso. Drag con inercia rota la esfera; parallax leve
  al mover el mouse. Constantes finales: R=230px, bandas 50°/130° y 32°/148°.
- **UN solo dot viajero** (se eliminaron los pulsos simultáneos): vuela con
  easing cinético (cubic-bezier 0.65,0,0.15,1 — acelera y frena contra el
  impacto), el nodo receptor retrocede con coseno amortiguado y amplitud
  inversa a su tamaño; el próximo vuelo sale de un nodo CERCANO al aterrizaje
  (≤35° de arco) para que el ojo lo siga.
- **Hub = ícono de red + título solo** («Your health network» / «Tu red de
  salud»). El pill de Pinamar y «114 servicios» pasan a una línea de prueba
  (`.const-proof`) entre la figura y el figcaption.
- **Labels a una línea** EN/ES (≤14 chars los chips; tabla completa en el plan
  `~/.claude/plans/tender-foraging-cocoa.md`). Único cambio de sentido real:
  «State policy, not one term's» → «Policy, not politics» / «Política de Estado».
- **Íconos: Icons8 iOS 27 Glyph (`ios11`)** para TODO el proyecto Human Rounds
  — regla persistida en `sana/icons8.json` + memoria del skill `icons8-icons` +
  CLAUDE.md de sana. Hub: glifo hub-and-spoke (id 69519), nunca un hospital.
- Fallbacks: mobile ≤760px mantiene el layout apilado sin motor; reduced-motion
  congela la esfera sin dot.

## Revisión 2026-07-31 (feedback del responsable del producto — vigente, corrige lo de abajo)

- **La constelación REEMPLAZA la ilustración estática del hero** («Human Rounds
  modules» .webp) en humanrounds.org — no es una sección aparte más abajo. Una
  sola constelación por página, en el slot de ilustración del hero.
- **El hub NO es un hospital**: la instalación es municipal. Hub = «Tu red de
  salud» / "Your health network" (hospital + centros de salud del municipio).
  El pill «Hospital de Pinamar — live in production» se mantiene (es un hecho).
- **Cero estadísticas inventadas.** Nada de «ausentismo 30% → 18-20%»,
  «4-12 bajas/jornada», «$0.001/orden», «24/25 campos», ni el dot «Menos
  ausentismo, medido». El sistema no lleva tiempo corriendo: cualquier mejora
  «medida» es mentira. Permitido: «114 servicios en el directorio» y el pill de
  Pinamar en producción. Cada dot restante promete un MECANISMO que el software
  tiene hoy, nunca un resultado estadístico.
- **Selector de país: SOLO dropdown** (un `<select>`), sin tabs de banderas y
  sin opción «Auto» — se preselecciona el país del visitante por geolocalización
  (data-cf-country → navigator.language → AR); país sin entrada → genérico.
  El control se mete debajo de la figura con margen negativo, así que necesita
  `position: relative; z-index: 50`: sin eso la figura (elemento posicionado
  sobre un hermano estático) se come todos los clics y el desplegable no abre.
- **Marca pública: Human Rounds, nunca «Sana»** en nada visible al usuario
  («AI triage (sana)» → «AI triage», etc.). Sana/Sana Turnos son nombres
  internos de repo/código.
- Se mantiene la gramática de estados que gustó: vivo = color pleno; parcial =
  letra chica «in progress»; planificado = escala de grises + «connector planned».

Status: concept approved in session 2026-07-30, built 2026-07-30/31. The visualization
engine is ported from Finda Capital's landing (`<workspace>/finda/app/public.py` —
hand-rolled CSS/JS constellation: absolutely-positioned `.cnode`s, drift keyframes,
hover "hot" states, `prefers-reduced-motion` fallback, no d3). This doc is the
content spec; grounding comes from three research passes (web research on
bottlenecks/desires, the Pinamar meeting-notes mining, and the prototype
capability inventory of 2026-07-30).

## Core idea

The hub is NOT the Sana brand — it is the visitor's own institution:

> 🏥 **Tu hospital** — *en línea, con turnos, IA y datos*

(Variant for the live-proof section: "Hospital de Pinamar · en producción".)
Sana's mark appears small, as the fabric holding the edges, not the star.
The buyer buys prestige for their organization; the diagram shows their
institution at the center of a modern galaxy.

Three node classes (same visual grammar as Finda):

1. **Capability chips** (icon nodes) — replaces the boring module list.
2. **Institution logo chips** — the credibility ring (government logos above all).
   A logotype is shown bare, never inside a card/tile/border. An institution we
   have no real logo file for shows its NAME alone — never an initials monogram
   or a placeholder glyph, which read as a real mark and say nothing.
   The asset is the **isolated mark**, never a lockup: SUMAR+ shipped as
   «Sumar+ | escudo | Ministerio de Salud República Argentina» and rendered as
   an illegible 10px smudge, so it was cut back to the wordmark alone. A logo
   whose artwork is wider than 2:1 gets a wide box instead of the 34px square —
   decided from the image's own natural aspect, so a new country's assets need
   no list to maintain.
3. **Italic outcome dots** (clay serif) — the desire layer, one register per
   participant group.

Aliveness upgrades over Finda's version:

- **Traveling pulses along edges**: a dot runs a real path every few seconds
  (WhatsApp → Turnos → HSI; Orden → IA → Cola de aprobación). CSS `offset-path`,
  no dependencies.
- **Live ticker at the hub**: real Pinamar numbers when available.
- **Hover a capability** → highlight only its edges + connected institutions +
  outcome dots.
- **Load animation in narrative order**: institutions first (the world that
  exists), then capabilities (what Sana adds), then outcome dots (the payoff).
- **Country switcher morphs the institution ring** (see below) — the diagram
  itself demonstrates the connector architecture.

## Capability chips

Bright (implemented, per the 2026-07-30 inventory):

- Turnos online · Lectura de órdenes con IA · Directorio de servicios ·
  Cola de aprobación · Recordatorios · Lista de espera · Reportes de gestión ·
  Ranking de ausentismo · Cuenta con foto del DNI · Agenda del médico ·
  Motor multi-localidad · Triaje con IA (sana) · Escaneo de DNI (sana)

Lit-but-partial (show normal, don't over-promise in copy): Turnos por WhatsApp ·
Multi-idioma · Cobertura PUCO.

Dimmed «próximamente» (zero code, roadmap docs exist): Teleconsulta ·
Resumen de historia clínica.

## Outcome dots — revised, three registers

Grounded in verbatim meeting pains/desires and citable press. ≤5 words each,
italic clay serif, mixed around the outer field like Finda's.

**Pacientes** (pain: colas de madrugada — Página/12 2021; slots liberados que
nadie puede tomar — «pasó en los 3-4 sistemas que tuvieron»):

- «Sin cola de madrugada»
- «El turno en el bolsillo»
- «La orden no se pierde»
- «Si se libera, te avisa»

**Personal y dirección** (pain: ausentismo ~30% histórico → 18-20% con horizonte
de 31 días; matrículas sin verificar; recupero SUMAR en planillas):

- ~~«Menos ausentismo, medido»~~ (ELIMINADO 2026-07-31: mejora no medida = mentira)
- «Ningún turno liberado se pierde»
- «Matrícula verificada al instante»
- «Recupero SUMAR sin planillas»
- «Se entiende con HSI»

**Intendentes y secretarios de salud** (desire: modernización visible, costo
cero, narrativa nacional/internacional — FITBA, WSA, AI for Good, SaludOnDigital
LATAM; durabilidad: «el año que viene cambia el color del municipio»):

- «Modernización con titular de diario»
- «Gratis y de código abierto»
- «Caso para el congreso»
- «Datos para el ministerio»
- «Política de Estado, no de gestión»

## Institution rings

**Argentina (default ring — real integrations, government logos lead):**

| Chip | Capability | Status in prototype |
|---|---|---|
| RENAPER / Mi Argentina | Identity (OIDC) | real client, off by default |
| PUCO · SISA | Coverage lookup | live fallback chain; credentialed tier pending |
| REFEPS | Professional licenses | implemented (public lookup) |
| HSI | EMR push/resync | implemented vs dev HSI |
| Mi Salud Digital | Provincial slot source | offline dump, 30-min refresh |
| SUMAR+ | Billing/recupero reports | implemented (reports) |
| WhatsApp | Booking chat + reminders | Meta Cloud API only (TEST tier — no production rollout claimed); WAHA loopback dev-only |

Tech vendors (Mailgun, Cloudflare, OpenRouter/Gemini) stay OFF the ring —
government logos are the credibility signal; SaaS logos dilute it.

**International (secondary ring — appears via country switcher):**

| Capability | Options |
|---|---|
| Identity | Aadhaar (India) · gov.br (Brazil) · e-Estonia |
| Coverage | SUS / Meu SUS Digital (Brazil) · ADRES (Colombia) · IMSS-Bienestar (Mexico) · NHS (UK) |
| Licenses | GMC (UK) · CFM/CRM (Brazil) |
| EMR / interop | ABDM (India) · RNDS (Brazil) · NHS Spine · HL7 FHIR |
| Messaging | WhatsApp (global) |

**The switcher IS the pitch**: chips 🇦🇷 🇧🇷 🇮🇳 🇬🇧 morph the logo ring in place
(RENAPER→Aadhaar/gov.br, PUCO→SUS/ADRES, REFEPS→GMC/CFM, HSI→RNDS/ABDM) while
capabilities and outcome dots stay put — a literal animation of the
connector architecture in docs/connectors.md ("the engine is generic, the
country is a connector set").

## Numbers available for the hub ticker

⚠️ Revisión 2026-07-31: de esta lista SOLO «114 servicios» y la referencia viva
van al sitio público. El resto es contexto interno de reuniones, no copy.

From the meeting mining + inventory (verify freshness at build time):

- 114 servicios en el directorio
- Ausentismo ~30% → 18-20% con horizonte de 31 días
- 4-12 bajas por jornada que hoy se pierden (sin lista de espera)
- Lectura de orden: ~$0.001 por orden
- DNI OCR: 24/25 campos correctos en benchmark
- 65 de 135 municipios PBA + 11 provincias ya usan HSI (contexto provincial)
- Referencia viva: pinamar.humanrounds.org
