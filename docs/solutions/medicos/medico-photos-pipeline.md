---
title: Fotos de médicos — de dónde salen y cómo agregar una
scope: repo
area: data
tags: [medicos, fotos, colmed9, gpt-image, sprite, icons8]
created: 2026-07-09
refs: [defb054, 83cc782, f7ff2db]
status: active
---

## Problem
El directorio necesita retratos reales de los profesionales municipales; no hay lista oficial con fotos.

## Root cause
No existe una fuente única: colmed9 (Colegio de Médicos Distrito IX) tiene foto por matrícula pero SOLO médicos matriculados en el distrito; licenciadas (obstétricas, nutricionistas, psicólogas) y matriculados de otros distritos no aparecen.

## Solution
`scripts/build_medico_photos.py` en 4 etapas: `--collect` (copia tiles ya retocados de `<workspace>/pinamar-turnos` + baja crudos de `servicios.colmed9.com.ar/fotos_matriculados/<matricula>.jpg`) → `--sheet` (hoja de contacto 4x3) → `--edit` (OpenAI `gpt-image-2` images/edits, un edit por hoja, preserva identidades) → `--pack` (sprite `webdata/medico-photo-sprite.webp` 128px + manifest posicionado en % — sirve a cualquier tamaño). Para el roster recién importado, `discover_new_medico_photos.py --discover` consulta Colmed9 sin LLMs y deja una revisión auditable; `--apply` incorpora sólo coincidencias high con foto oficial. `batch_medico_photo_edits.py --submit` manda las contact sheets a `/v1/images/edits` en OpenAI Batch (50% de descuento); `--status` y `--ingest` permiten reanudar sin repetir ediciones. Mapa de procedencia: `webdata/medico-fotos.json`. Sin foto → avatar de iniciales (7/30). Los clusters sin profesional llevan glifos Icons8 (`static/icons/`, estilo ios-glyphs de `img.icons8.com/ios-glyphs/96/<name>.png` — probar el nombre con curl, muchos 404).

## How to apply
Médico nuevo con matrícula: buscarla con `<workspace>/salud-pinamar/scripts/colmed9.py "Apellido, Nombre"` (confianza high + especialidad alineada), agregar `{"colmed9": "<matricula>"}` a `medico-fotos.json`, correr las 4 etapas (OPENAI_API_KEY desde un .env de proyecto; op CLI está bloqueado). Sin matrícula (licenciadas, otros distritos): foto pública VERIFICADA POR NOMBRE EN LA FUENTE (zócalo de video, placa municipal con nombre impreso, página profesional propia, bio de IG que nombre el hospital) → `{"web": "<url>", "_prov": "..."}` + recorte cuadrado curado en `data/medico-photos-src/<id>.jpg`; con barbijo se DEJA el barbijo (nunca inventar la cara tapada). `lookaside.fbsbx.com` sólo responde con User-Agent `facebookexternalhit/1.1`. El servidor arma el estilo del avatar en `hospital.py:_foto_style` — reiniciarlo tras el pack (y subir el `?v=` del sprite ahí). Cambiaste JS/CSS → subí `?v=`.

**Trampa (2026-07-10):** `--edit` saltea el llamado a la API si `edited-sheet-01.png` ya existe — y `--sheet` SIEMPRE numera desde `sheet-01`, así que un batch nuevo reusa la hoja editada VIEJA y corta caras equivocadas con nombres nuevos. Antes de un batch nuevo: borrar `data/medico-photos-src/sheet-*`, `edited-sheet-*` y `*-response.json`. Verificar SIEMPRE la hoja editada con Read antes del pack.

**Identity and framing check (2026-07-13):** Contact-sheet edits can bleed an adjacent identity into another tile even when the source captions are correct. If two clinicians appear to share a face, remove both portraits from the published set unless the face can be independently corroborated. Before packing, compare the final tiles visually and normalize visibly small portraits to the set's usual framing (face roughly 40–45% of tile height). Preserve the uncropped originals under `data/medico-photos-rejected/` so the correction stays reversible.

**Búsqueda web de retratos (2026-07-14):** el playbook completo (serper images fan-out → parallel.ai → lookaside de IG/FB → Apify sweep de cuentas locales) ahora vive en el skill de usuario **doctor-photo-search** (`~/.claude/skills/doctor-photo-search/SKILL.md`) — cargarlo antes de cazar fotos. Claves de esa sesión: los que faltan son casi todos NO-médicos (colmed9 no los tiene por diseño, `NON_MD_SPECIALTIES`); el media_id de un post de IG se deriva del shortcode (base64 `A-Za-z0-9-_`) y `lookaside.instagram.com/seo/google_widget/crawler/?media_id=` con UA Googlebot devuelve og:image (CDN real) + og:title (caption = evidencia de identidad); el og:image de un perfil de FB sale con UA `facebookexternalhit/1.1`; revisar `data/medico-photos-src/` ANTES de buscar — una sesión anterior pudo dejar el recorte curado sin mapear en `medico-fotos.json` (pasó con dos médicos del catálogo). Homónimos = el riesgo #1 (un apellido compuesto ≠ un funcionario con el mismo apellido; dos profesionales del mismo apellido en centros distintos). Sin fuente verificable (matrícula activa pero sin foto en colmed9/Doctoralia, o sin rastro web propio) → iniciales, no se inventa.
