---
title: Cuatro trampas al traducir el sitio entero (no sólo el marco)
scope: repo
area: dev
tags: [i18n, translate, pack_l10n, cache, performance, demo]
created: 2026-08-04
refs: [e9bea88, a62dcf1, a8363f8]
status: active
---

## Problem

Prender el segundo idioma de punta a punta (contenido del pack + literales de
JS + selector en todas las páginas) rompió de cuatro maneras distintas, y
ninguna se ve desde el navegador del que la escribe: todas se manifiestan sólo
en el idioma NO canónico.

## Root cause

1. **Catálogo inline sin fallback.** `SanaI18n.t(key)` devolvía la clave cruda
   mientras el fetch no volvía. Mandar el catálogo inline en TODAS las páginas
   lo arregla pero mete cada string del sitio en el HTML de cada página — y
   además rompe cualquier test que grepee el HTML por un texto ("Sacar turno"
   aparecía dentro del catálogo inline de una página que no lo mostraba).
2. **El hash de la traducción tiene que ser del texto que el render mira.**
   `_doctors()` reescribe "Clínica médica" → "Medicina general" ANTES de
   localizar. El barrido leía el JSON crudo, cacheaba bajo el texto viejo, y
   el render buscaba por el hash del texto nuevo: cache miss silencioso. El
   síntoma fue un filtro de especialidad con "Cardiólogo" Y "Cardiology".
3. **Localizar dentro de un loader lo vuelve caro.** `_services()`/`_centers()`
   se llaman miles de veces por página (una por médico por cálculo de turno) y
   zafaban porque devolvían una lista ya armada. Localizar = lista nueva de ~80
   dicts y ~400 lookups por llamada: `/doctors` pasó de 0,3 s a no terminar nunca.
4. **`TURNOS_FAKE_AI` apagaba también la traducción.** La demo corre con ese
   flag para no mandarle a un proveedor lo que sube un VISITANTE; el catálogo
   no es dato de un visitante. Resultado: "[en] Clínica médica" para todos, y
   como esas filas traen un hash que coincide, un barrido real no las tocaba.

## Solution

1. Catálogo inline **sólo cuando `lang != CANONICAL_LANG`**, y todo call site
   pasa el castellano como tercer argumento (`T(key, vars, "…")` /
   `i18n.t_or(key, "…", lang)`). `test/i18n-fallbacks.test.mjs` lo exige.
2. `site_catalog.canonical_rows(kind)` es la ÚNICA definición de qué texto ve
   el render; los dos barridos (`scripts/translate_pack.py`,
   `translate_warmup.py`) pasan por ahí.
3. `pack_l10n.localize()` memoiza por `(kind, lang)`, validando contra los ids
   de las filas, con TTL de 30 s. `translate.cached_text()` lee TODO el idioma
   en una query (`db.translations_all`) en vez de una por string.
4. `TURNOS_FAKE_TRANSLATE` (escape por función, igual que
   `TURNOS_FAKE_CHAT_AI`), que el modo demo pone en `0`; `translate_warmup`
   borra al arrancar las filas `[<lang>] …` que dejó un barrido falso.

## How to apply

Al traducir una superficie nueva: pasá SIEMPRE el literal canónico como
fallback; agregá el campo a `pack_l10n.FIELDS` sólo si es prosa que nadie
parsea (`horario` NO va: lo lee el parser de días de hours.mjs); y si tocás un
loader, medí una página en el idioma no canónico antes de darla por lista.
Relacionado: [[data-packs]] para de dónde sale el contenido.

## Native module regression (2026-09-05)

The old client-JS exception was incorrect after dynamic imports. Requests
column metadata and simulator titles froze Spanish fallbacks before readiness.
Await the owning module and build translated registries lazily. Every rendered
entry page must publish the shared asset manifest; a synthetic test that supplies
it cannot prove the real template does. The doctor's hours editor also shadowed
its new `dias()` factory with a same-named parameter, breaking real form rendering.

Proofs: `i18n-delayed-load.spec.mjs` samples first paint before navigation;
`i18n-delayed-consumer-metadata.spec.mjs` executes the real scripts with a held
module response; `admin-hours-editor-runtime.test.mjs` fails before the parameter
rename and passes afterward. The full browser gate passed 130 tests with only
8 declared legacy-mode skips. Refs: `dd7b98a4`, `574c7b44`.
