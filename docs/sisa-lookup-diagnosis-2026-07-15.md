# SISA/CISA lookups: diagnóstico de la falla del 2026-07-15 + comparación vs REFEPS/colmed9

Origen: dictado del responsable del producto (Aqua Voice, 2026-07-15 15:47 ART) — *"Then do the CISA lookups and
compare with the lookups we have. Maybe it's more complete or better. Also analyze why the
lookup didn't work today."* "CISA" es un mishearing de **SISA** (Sistema Integrado de
Información Sanitaria Argentino, `sisa.msal.gov.ar`) — en español rioplatense "ese" y "ci"
suenan igual (seseo), y así lo dijeron los mismos interlocutores del hospital en la reunión
grabada por Lira ese mismo día: *"Vos estás usando CISA para cruzar datos... en CISA vos
tenés, entrá a CISA.ar"* (transcript `d69c9e1f…jsonl` línea 206).

## Hallazgo central: SISA y REFEPS son EL MISMO sistema, no dos fuentes distintas

Se investigó `sisa.msal.gov.ar` directamente (research vía parallel.ai + fetch de la ayuda
en línea oficial). La página de ayuda de SISA lo dice explícito:

> *"La Red Federal de Registros de Profesionales de la Salud (REFEPS) abarca a la totalidad
> de los efectores de salud del país... El diseño y la estructuración de los datos incluidos
> en el formulario han sido desarrollados por el equipo del SISA."*
> — `sisa.msal.gov.ar/sisadoc/docs/050102/refeps_intro.jsp`

Y la consulta pública de REFEPS referenciada por el propio catálogo FHIR oficial
(simplifier.net/guide/practitionerrefeps) apunta a `https://sisa.msal.gov.ar/sisa/` — el
mismo dominio SISA. **REFEPS ES el módulo de registro federal de profesionales de SISA**;
`argentina.gob.ar/salud/buscador-nacional-de-profesionales-de-la-salud` (lo que ya consulta
nuestro `refeps.py`) es el buscador público de esa misma base. `sisa.msal.gov.ar/sisa/` en
sí es el portal GWT de LOGIN para gestión (cargar/editar fichas, uso de los Ministerios
provinciales) — no ofrece una consulta pública alternativa ni más completa; sin credenciales
sólo se llega a la home. No hay ninguna API/búsqueda SISA "extra" que no sea REFEPS.

**Conclusión: no existe una segunda fuente "CISA/SISA" para correr y comparar — el lookup
que el responsable del producto pidió correr YA es lo que `refeps.py` corre.** Lo único que faltaba era: (1)
diagnosticar por qué falló hoy, y (2) terminar de aplicar lo que REFEPS/colmed9 ya sabían
sobre nuestros médicos (`webdata/medicos.json` tenía **0 matrículas** guardadas pese a que
el dato está disponible hace días).

`find-cred SISA` / `find-cred MSAL` y una búsqueda en credential vault (AI/Argentina) no
encontraron credenciales SISA — irrelevante para el diagnóstico ya que el REFEPS público no
las necesita, pero se deja registrado por si en el futuro se evalúa un acceso SISA con
usuario (gestión, no sólo consulta).

## Por qué falló el lookup el 2026-07-15

Dos fallas reales y distintas, ambas confirmadas con evidencia:

### 1. Falla de producto — dos 502 reales en `/api/admin/refeps` (09:50–09:51 ART)

`grep "admin/refeps" logs/access.log` muestra, el mismo día:

```
09:50:45  GET /api/admin/refeps?matricula=MN+100482&medico=Ana+Amanda+Robledo   → 502
09:50:48  GET /api/admin/refeps?matricula=MN+100482&medico=Ana+Amanda+Robledo   → 200  (reintento manual, 3s después)
09:51:27  GET /api/admin/refeps?matricula=MP+100537&medico=Eduardo+Larrea   → 502
12:46:48  GET /api/admin/refeps?matricula=MP+100537&medico=Eduardo+Larrea   → 200  (recién ~3h después)
```

El caso de Eduardo es exactamente el que la contraparte técnica mencionó en la reunión grabada.
`_admin_refeps` (`solicitudes.py:776`) llama a `refeps.buscar_matricula()` (`refeps.py`, el
motor propio del producto — NO el de `salud-pinamar`, ver más abajo) sin ningún reintento:
si el GET/POST a `argentina.gob.ar` tira una excepción de red (timeout, conexión resetada,
Cloudflare devolviendo un error transitorio), `RefepsError` sube directo y `_admin_refeps`
devuelve 502 al operador — salvo que exista una entrada vieja en `refeps_cache` (Postgres) de
esa matrícula, que en este caso no existía porque era la primera consulta.

**Reproducción en vivo (2026-07-16):** `refeps.buscar_matricula('92933')`,
`refeps.buscar_matricula('100482')` y `refeps.buscar_matricula('100537')` corridos ahora
mismo devuelven 200/OK en 1.6–2.8s cada uno, sin ningún cambio de código — confirma que **no
es un bug de parseo ni un cambio de markup del sitio**, sino un blip transitorio de red/
Cloudflare contra `argentina.gob.ar` que se autoresuelve en segundos u horas. Un solo intento
sin reintento convierte cualquier blip de terceros en un 502 visible para el operador del
hospital, justo cuando está aprobando una solicitud.

**Fix aplicado:** `refeps.py` ahora reintenta la consulta completa (GET del formulario +
POST de búsqueda) hasta 2 veces con 1.5s de espera antes de propagar `RefepsError`
(`refeps.buscar_matricula` → `_buscar_matricula_once` + loop de reintento). Test agregado:
`test/test_refeps_retry.py` (3 casos: éxito sin reintento, recuperación al segundo intento,
falla persistente tras agotar reintentos — mockeado, sin red). Suite completa: 325/325 verde.

### 2. Falla de la herramienta de enriquecimiento — el batch REFEPS (Playwright) es lento/frágil

Aparte del endpoint del producto, la sesión de ayer (`d69c9e1f…jsonl`, 12:46–13:08 ART)
corrió el **motor REFEPS de `<workspace>/salud-pinamar/scripts/refeps.py`** (un script
DISTINTO — usa Playwright/Chromium real porque, según su propio docstring, la réplica HTTP
sin navegador es "unreliable (CDN-cached form_build_id)") para completar las 26 matrículas
que colmed9 no pudo resolver (no-médicos: psicólogos, odontólogos, nutricionistas,
enfermería). Ese batch quedó **colgado en el ítem 11/26** cuando terminó la sesión (el
`.harness/friction-log.md` registra un `Bash Exit code 1` a las 13:08:19 con la salida
parcial `[6/26]…[8/26]`), dejando procesos Chromium huérfanos.

**Reproducido hoy (2026-07-16):** se re-corrió el mismo batch (33 misses actuales — creció
de 26 a 33 porque `medicos.json` sumó médicos el 14-07). Los primeros 9 ítems tardaron
~10s/uno; el ítem 10 ("Bernasconi, Pedro Actis") tardó **~120s**, y el ítem 13
("East Iturbe, Brenda") superó los **160s sin resolver** — en ese punto se mató el proceso
a propósito para no quemar la sesión entera esperando un script de terceros. La causa está en
el propio motor: cada intento hace `page.goto(..., timeout=45000)` +
`wait_for_function(..., timeout=25000)`, y `search()` reintenta hasta 4 veces
(`retries=4, delay=1.5`) → peor caso ~280s por ítem si la página tarda en cargar o el
resultado tarda en poblarse. No es un colgado infinito real: es una cola de reintentos que,
sumada en un batch largo corrido con polling manual (`sleep 15/20` + `tail`), se lee como
"se colgó" cuando en verdad sigue avanzando muy lento — exactamente lo que le pasó a la
sesión de ayer, que también terminó (contexto agotado o kill) sin completar, dejando el
Chromium huérfano que aparece en el friction log.

**Segundo hallazgo, más importante — por qué el trabajo de ayer NUNCA llegó a
`medicos.json` pese a que REFEPS sí estaba resolviendo:** `batch()` en el script de
`salud-pinamar` sólo escribe el `--out` JSON **al final**, después del `for` completo
(`json.dump(...)` fuera del loop). Si el proceso se corta antes de terminar — por un hang
lento, un kill, o que se acabe la sesión/contexto — **se pierden el 100% de los resultados
ya resueltos**, no sólo los pendientes. Eso es lo que pasó dos veces: ayer (11/26 resueltos,
0 guardados) y hoy (12/33 resueltos antes del kill; se rescataron reconstruyéndolos del log
de stderr — ver más abajo — porque el JSON de salida no llegó a existir). Un fix de una
línea (mover el `json.dump` a un checkpoint cada N ítems, o usar `--out` en append/flush) lo
resolvería, pero es tooling puntual de enriquecimiento en el repo hermano, no código de
producto — no se tocó.

**No se tocó el motor Playwright de `salud-pinamar`** (repo hermano, fuera de este repo) —
es tooling de enriquecimiento puntual, no código de producto; el fix de reintento relevante
para el responsable del producto es el de `refeps.py` en `sana-turnos`, que sí es el que corre en vivo cuando un
operador del hospital aprueba una solicitud. Para no perder los 12 ítems ya resueltos hoy
antes del kill, se reconstruyeron desde el log de stderr (matrícula + confianza + profesión,
que sí se imprimen línea a línea) y se aplicaron a `medicos.json` marcados
`fuente:"refeps"` con provincia/estado anotados `(aprox., reconstruido del log —
reconfirmar)` — son reales (la matrícula y la profesión vienen del match real), sólo la
provincia/situación exactas quedan pendientes de una relectura del JSON completo si se
vuelve a correr el batch hasta el final.

## Comparación: colmed9 vs REFEPS/SISA — cobertura sobre los médicos actuales de Pinamar

Corrido en vivo el 2026-07-16 sobre los 96 profesionales con nombre completo en
`webdata/medicos.json` (99 entradas totales, 3 sin apellido/nombre_pila separables):

| Fuente | Cobertura | Fortaleza | Debilidad |
|---|---|---|---|
| **colmed9** (Distrito IX) | 63/96 intentados (66%) — 57 alta confianza, 6 media | Matrícula **provincial** + **foto** (única fuente con foto) | Sólo médicos (MD) matriculados en Distrito IX (Pinamar/Madariaga/Gesell); 0% de psicólogos, odontólogos, nutricionistas, enfermería, etc. |
| **REFEPS/SISA** (nacional) | 12/33 misses de colmed9 resueltos en esta corrida (proceso cortado por el hang del §2, no por falta de cobertura — ver arriba) — ya cubre psicólogos, odontólogos, nutricionistas, auxiliares de enfermería, psicomotricistas, terapistas ocupacionales, psicopedagogos | Nacional, **todas las profesiones**, agrega cada colegio provincial + ministerio — autoritativo para el número de matrícula | **Sin foto** (nunca); requiere Playwright para ser confiable (ver §2 arriba, y su bug de no-checkpoint); un número de matrícula puede repetirse entre profesionales de distinta provincia/profesión, hay que desambiguar por nombre |
| **medicos.json — ANTES de este pase** | **0/99 (0%)** | — | El dato ya estaba disponible en colmed9+REFEPS desde el 2026-07-15 pero nunca se aplicó al archivo — dos corridas seguidas (ayer y hoy) perdieron su trabajo por el bug de no-checkpoint del §2 |
| **medicos.json — DESPUÉS de este pase (2026-07-16)** | **75/99 (76%)** — 63 vía colmed9 (con foto), 12 vía REFEPS (sin foto, 21 misses de colmed9 restantes sin resolver por el corte del batch) | — | 24/99 (24%) siguen sin matrícula: 21 son los REFEPS pendientes (namesakes o no llegaron a correrse), 3 no tienen apellido/nombre_pila separables para buscar |

**Veredicto:** REFEPS/SISA es estrictamente **más completo** que colmed9 solo (cubre todas
las profesiones y todas las provincias), confirmando la intuición del responsable del producto — pero no porque
sea "otra fuente que probar", sino porque es la fuente NACIONAL que ya complementa a colmed9
(regional, MD-only, con foto) exactamente como documenta el skill `doctor-lookup`. La
estrategia correcta ya estaba bien diseñada (colmed9 primero por la foto, REFEPS de
respaldo); lo que faltaba era (a) hacer el lookup resiliente a blips de red en el endpoint
de producto, y (b) terminar de aplicar el resultado a los datos reales en vez de dejarlo en
un scratchpad de sesión.

## Qué se hizo

1. **`refeps.py`** (sana-turnos): agregado reintento (2 intentos extra, 1.5s de espera) en
   `buscar_matricula()` antes de propagar `RefepsError` — soluciona directamente los dos 502
   reales del 2026-07-15.
2. **`test/test_refeps_retry.py`**: 3 tests nuevos (mockeados, sin red) que cubren éxito
   directo, recuperación al reintentar, y falla persistente.
3. **`webdata/medicos.json`**: se corrió colmed9 (batch completo, 96 profesionales) + REFEPS
   sobre los misses, y se aplicó el resultado — cada médico encontrado ahora lleva un objeto
   `matricula: {numero, provincia, estado, confianza, fuente, verify_url, foto_url?}`.
   **Antes: 0/99 con matrícula. Después: 75/99 (76%)** — 63 colmed9 (con foto), 12 REFEPS
   (sin foto, provincia/estado marcados "aprox." porque se reconstruyeron del log de stderr
   tras el corte del batch — ver §2).
4. Suite completa verificada: `TURNOS_FAKE_AI=1 python3 -m unittest discover -s test -p
   "test_*.py"` → 325/325 verde (incluye los 3 tests nuevos) + `node --test
   test/horarios.test.mjs test/edad.test.mjs test/listado-orden.test.mjs` → 50/50 verde.

## Qué queda abierto (no se tocó en este pase, fuera del pedido puntual)

- El **verify-link clickeable** de la matrícula en la página del médico/admin (mandato del
  skill `doctor-lookup`: "cualquier matrícula mostrada debe ser un link vivo a su
  verificación, con el estado habilitado/inactivo al lado") — es trabajo de UI, ya estaba
  anotado como el próximo paso #2 en el parking note del 2026-07-15 y no formaba parte del
  pedido dictado ("run the lookups, compare, diagnose the failure"). No se tocó para no
  colisionar con la sesión hermana que estaba trabajando en `hospital.py`/`servicio.js`/
  `listado.js` al mismo tiempo.
- Quedan **21 misses de colmed9 sin intentar en REFEPS** (el batch se cortó en el ítem 13/33
  por el hang del §2) + los que ni colmed9 ni REFEPS resuelven con confianza (namesakes,
  nombres ambiguos). Para terminarlos: correr de nuevo
  `<workspace>/salud-pinamar/scripts/refeps.py --batch scratchpad/colmed9-misses-2026-07-16.json
  --out scratchpad/refeps-results-2026-07-16.json` (excluyendo los 12 ya resueltos) en
  background con `run_in_background`/`Monitor`, sin esperar en foreground — y considerar
  primero el fix de checkpoint del §2 (guardar cada N ítems) para no volver a perder el
  progreso si vuelve a colgarse.
- Los 12 registros marcados `fuente:"refeps"` en `medicos.json` tienen matrícula y profesión
  reales pero provincia/estado **aproximados** (reconstruidos del log, no del JSON completo
  de REFEPS) — re-correr esos 12 con el batch completo cuando se retome el punto anterior
  para reemplazar el aproximado por el dato exacto (especialidad REFEPS, sanción, etc.).
