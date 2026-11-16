# Búsqueda: stemming de profesiones + tiers de relevancia (no relitigar)

**Fecha:** 2026-07-16 · **Origen:** estudio de búsqueda del responsable del producto ("si escribo *gine* me da resultados equivocados")

## El problema (dos fallas distintas)

1. **Profesional vs. especialidad.** La gente busca "ginecólogo", "pediatra",
   "obstetra"; el catálogo nombra "Ginecología", "Pediatría", "Obstetricia".
   Con substring pelado, esas búsquedas daban **CERO** resultados.
2. **La popularidad sola entierra al más relevante.** "gine" mostraba 6×
   "Medicina general" (tiene "ginecología" en `palabras` y muchas horas)
   ANTES que el servicio Ginecología. Con un solo criterio de orden (horas),
   un match débil con muchas horas gana a un match exacto con pocas.

## La solución — `buscar.js:SanaBuscar.buscar(rows, q, getCampos)`

- **Variantes morfológicas por palabra del query, en las DOS direcciones**
  (el dato puede nombrar cualquiera de las dos formas — el listado de médicos
  muestra "Cardiólogo" desde el cambio de microcopy del 2026-07-16):
  `-ólogo/-óloga/-ología → -olog` (con prefijo mínimo de 2: **ur**ólogo),
  `-iatra/-iatría → -iatr`, `-grafía/-grafista → -graf`, plural→singular,
  irregulares en un mapa (`obstetra/obstetricia→obstetr`, `diabetes→diabet`,
  `médico→medic`, `enfermería→enfermer`). Stopwords incluyen `dr`/`dra`.
- **Tiers de relevancia por palabra** (menor = mejor, puntaje = suma):
  0 el nombre EMPIEZA con la palabra · 1 arranque de otra palabra del nombre
  ("obstetra" → "Ecografía **obstétrica**") · 2 substring del nombre ("cardio"
  en "Electro**cardio**grama") · 3 arranque de palabra del resto (sinónimos/
  categoría/centro/sede/horario) · 4 substring del resto. AND entre palabras.
- **Las horas ordenan sólo DENTRO de cada puntaje** (`ordenarPorPopularidad`
  por bucket). La relevancia manda; la popularidad desempata.
- La home pasa `defaultSort:false` a listado.js para que respete ese orden
  (ya NO `rank:"popularidad"` en resultados de búsqueda; los catálogos sin
  query lo siguen usando).

## Gotchas que costaron encontrar

- **`python3 -m unittest test.x` no corre** (stdlib `test` tapa el dir) — discover.
- **El server huérfano sobrevive a `launchctl kickstart -k`**: si el PID en
  :7700 no cambia tras el kickstart, hay un proceso viejo fuera del
  LaunchAgent — matar por puerto (`kill $(lsof -tnP -iTCP:7700 -sTCP:LISTEN)`)
  y dejar que KeepAlive lo relance. Si no, el API sirve código viejo y el fix
  "no anda" sin estar roto.
- **Duplicados stale del import**: la planilla renombró prestaciones
  ("Grupo de ansiedad"→"Ansiedad") y el import por nombre-normalizado no las
  matcheó → convivían las dos. Al ver dos filas casi iguales en búsqueda,
  sospechar de esto (se borraron 8 el 2026-07-16).
- **Filas-rol de la planilla** ("Enfermería", "Técnico/a", "Administrativo.")
  llevan `rol:true` en medicos.json: `hospital._profesionales()` las filtra de
  directorio/búsqueda/centro/cluster, pero sus franjas SIGUEN alimentando
  `_horas_semana` y `_turnos_servicio` — no borrarlas.

## Batería de experimentos (reusar al tocar la búsqueda)

Harness de la sesión: replicar el pipeline de la home contra /api/servicios +
/api/medicos y correr ~50 queries reales (gine, ginecólogo, cardio, trauma,
pediatra, eco, rayos, muela, ojos, embarazo, vacuna, guardia, certificado,
apellidos, con y sin acentos, plural/singular). Aceptación mínima en
`test/buscar-match.test.mjs` (19 casos contra webdata real).

## buscarFrase — frases de una orden médica (2026-07-16, "Subí tu orden")

- **Problema:** matchear la práctica leída de una orden ("Ergometría de 12
  derivaciones") con `buscar()` da CERO (AND entre palabras) y la degradación
  ingenua "sacar palabras de la cola hasta el primer nivel NO vacío" elige la
  entidad equivocada: "Ergometría de 12" matchea Cardiología (keyword
  "ergometría" en `palabras` + "12" en el horario, tiers 4+4) y ahí se frena —
  el servicio Ergometría (match por NOMBRE, tier 0) quedaba último.
- **Solución:** `SanaBuscar.buscarFrase(rows, q, getCampos)` corre TODOS los
  niveles de degradación por la cola y gana el de **mejor tier promedio**
  (score del mejor match ÷ palabras del nivel), no el primero que devuelve
  algo. Tests: los 3 casos `buscarFrase` en `test/buscar-match.test.mjs`.
- **Cómo aplicar:** cualquier texto LARGO de un documento (práctica, motivo,
  diagnóstico) contra el catálogo → `buscarFrase`, nunca `buscar` directo ni
  una degradación primer-no-vacío. Queries cortas tipeadas por el usuario
  siguen en `buscar`.
- **Gotcha aparte descubierto en la misma verificación:** `SanaListado.render`
  APPENDEA su `.lst-slot` al host — al re-renderizar sobre un host con spinner
  hay que `host.innerHTML=""` antes, o el spinner queda arriba de la tabla.
