---
title: Un gate de traducción por texto tiene puntos ciegos — enumeralos
scope: repo
area: dev
tags: [i18n, ci, tests, audit]
created: 2026-08-05
refs: []
status: active
---

## Problem
`scripts/i18n_audit.py` se corrigió cuatro veces durante la traducción del
sitio, cada vez por una unidad distinta que chocó con un punto ciego
distinto: sólo reconocía `T("literal.key", …)`, así que una key COMPUTADA
se le escapaba entera; castellano guardado como VALOR de un objeto (no
como string suelto) pasaba de largo; una llamada a función DENTRO de los
`vars` de `T()` rompía el string-stripping; y un bump de `?v=` en un asset
hacía fallar en falso el chequeo de paridad de bytes para cualquier unidad
que tocara ese número. Varias unidades descartaron correctamente esos
fallos de paridad como ruido, pero sin saber POR QUÉ eran ruido — suerte,
no comprensión.

## Root cause
Un gate basado en texto (grep/regex sobre el árbol) no puede ver una key
que se resuelve en tiempo de ejecución, y no distingue "el archivo cambió
de verdad" de "el archivo cambió sólo en un número de versión".

## Solution
Cada punto ciego se cerró puntual: reconocer más formas de `T()`, ignorar
diffs que sólo tocan `?v=`. El gate en sí sigue siendo textual — no ve nada
que se resuelva del lado del cliente en runtime (ver "Why JS literals were
untouched" en [i18n.md](../../architecture/i18n.md)).

## How to apply
Un gate de i18n por texto es un primer filtro, nunca la aceptación final —
la aceptación real es un navegador real en el idioma no canónico. Antes de
confiar en un audit script nuevo (acá o en cualquier otro proyecto con un
gate similar), probarlo contra los cuatro casos de arriba como fixtures
(key computada, castellano en un valor de objeto, función dentro de vars,
bump de versión) en vez de esperar a que una unidad paralela lo descubra
en producción.
