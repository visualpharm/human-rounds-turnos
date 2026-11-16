---
title: El fallback estático de http.server servía código fuente y PHI sin auth
scope: repo
area: security
tags: [static-files, phi, idor, xss, http.server, uploads]
created: 2026-07-17
refs: [server.py, solicitudes.py]
status: active
---

## Problem

`routers/legacy_http_transport.py` compone un `SimpleHTTPRequestHandler` con `directory=APP_ROOT` (el
directorio de la app, `/app` en Docker). `do_GET` sólo intercepta rutas
conocidas; lo no ruteado caía a `super().do_GET()`, que servía **cualquier**
archivo bajo ROOT sin ningún control. Verificado en vivo:

- `GET /server.py` → 200 (169 KB de código fuente); `/db.py`, `/admin_auth.py`,
  `/.env`, `/requirements.txt` igual.
- `GET /data/ordenes/` → 200 **listado de directorio**; `GET /data/ordenes/1.png`
  → 200 = la orden médica real de un paciente (nombre, DNI, diagnóstico). Los
  archivos se nombran con un id secuencial (`1.png`, `2.jpg`…), enumerables.
- `GET /data/cuenta-fotos/<dni>.jpg` → la foto del paciente (salteando el diseño
  "sólo el dueño" de `cuenta_foto.py`).

Dos IDOR/exposición más en la misma familia:

- `GET /api/requests?dni=<cualquiera>` **sin sesión** devolvía las solicitudes
  (PHI) de ese DNI: el código honraba el `?dni=` del query cuando no había
  cookie ("legacy/testing").
- Amplificador XSS almacenado: `solicitudes._ext_for` guardaba la orden con la
  extensión que **declaraba el cliente**. Un archivo con `<script>` nombrado
  `x.html` y subido como "orden médica" quedaba en `data/ordenes/<sid>.html` y
  el fallback estático lo servía como `text/html`, ejecutándose desde nuestro
  propio origen.

En prod hoy TODO el sitio está detrás de HTTP basic-auth (`site_auth.py`),
así que la exposición NO llega desde internet abierto — pero es exposición para
cualquiera con las credenciales de demo, y se vuelve exposición real el día que
se saque el basic-auth para abrir a los pacientes.

## Root cause

`http.server` es un servidor de archivos: sin un guard explícito sirve el
webroot entero, incluido el código, `.env` y el volumen `data/` con PHI. El
volumen es especialmente traicionero porque en dev vive dentro del repo
(`ROOT/data/`) y en prod es un mount persistente — nunca fue pensado como
público, pero compartía webroot con los assets que sí lo son.

## Solution

En `services/legacy_static.py`, antes del `super().do_GET()` terminal:

1. `_static_publico(path)` — allowlist: sólo extensiones de asset conocidas
   (`js/css/png/jpg/svg/ico/woff…/html`) más los prefijos `/webdata/` y
   `/vendor/`. Niega `.py`, dotfiles (`/.env`, `/.git`), `/data/`, y cualquier
   path que termine en `/`. Todo lo que no esté en la allowlist → 403.
2. `Handler.list_directory` overrideado a 403 (mata los listados de directorio
   en todo el árbol).
3. El branch de compat `/data/<path>` que mapea a `/webdata/` ahora, si el
   archivo NO resuelve bajo `WEBDATA_DIR`, hace `_deny_static()` en vez de caer
   al fallback (ahí vive el volumen con PHI).

Para el IDOR de `/api/requests`: la identidad SIEMPRE sale de la sesión; sin
sesión → 401 (mismo contrato que `POST /api/requests`). El `?dni=` del cliente
ya no se honra nunca.

Para el XSS: `_ext_for(orden_bytes, mime, filename)` valida por **magic bytes**
(`_sniff_orden`) y constriñe a `{png,jpg,jpeg,webp,heic,gif,pdf}`; lo desconocido
cae a `.bin` (servido como octet-stream, no ejecutable). El mime/filename del
cliente no decide más la extensión en disco.

## How to apply

- Cualquier ruta/archivo nuevo servido estáticamente tiene que caer dentro de la
  allowlist de `_static_publico` (una extensión de asset, o bajo `/webdata//vendor`).
  Datos de usuario (uploads, fotos, dumps) NUNCA se sirven por el fallback: van
  por un handler Python autenticado, como `cuenta_foto.py`/`medico_fotos.py`.
- Todo endpoint que devuelve datos de un paciente resuelve el DNI de la sesión,
  jamás de un parámetro del cliente (regla IDOR ya vigente en `/api/requests/<id>`,
  `.ics`, cancel, waitlist — este fix la extiende al listado).
- Cualquier archivo que el cliente sube y después se sirve: validar por magic
  bytes y allowlist de extensiones, nunca confiar en el mime/filename declarado.
- Tests: `test/test_static_hardening.py` (unit de `_static_publico`),
  `test/test_solicitudes.py::...::test_get_sin_sesion_no_filtra_por_query_dni`.
  Verificación en vivo: levantar el server y `curl` `/server.py` (403),
  `/data/ordenes/` (403), `/nav.js` (200), `/api/requests?dni=X` sin cookie (401).
