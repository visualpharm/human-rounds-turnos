# Google Calendar no permite precargar un feed ICS por deep link — copiar + pegar

**Síntoma (responsable del producto, 2026-07-18):** en el popup "Agregar todos mis turnos al calendario"
(`my-requests.js`), el botón de Google Calendar abría Google pero **NO precargaba la
dirección del feed**. El usuario llegaba a una página vacía y no sabía qué hacer.

## Raíz

Google **desactivó** el prefill de suscripciones ICS externas por URL. No es un bug
nuestro ni un parámetro mal armado — no existe forma de precargar la dirección:

- `calendar.google.com/calendar/render?cid=<url>` — dejó de funcionar (Google Issue
  Tracker **442619500**, reportado 2025-09).
- `calendar.google.com/calendar/r/settings/addbyurl?cid=<url ics>` — no precarga; peor,
  con una URL de ICS como `cid` tira **"No se pudo agregar el calendario. Revisá la
  URL."** (el `cid` es para agregar OTRO calendario de Google por su ID, no un feed
  externo). Ver Stack Overflow 79772521 (2025-09).

El código original (commit 2d6aa69) usaba justamente `addbyurl?cid=<feedUrl>`, así que
el botón nunca podía precargar.

## Fix (commit 9a236be)

Como no hay prefill posible, el botón de Google hace lo próximo mejor:

1. **Copia el feed al portapapeles** en el mismo click (mismo gesto de usuario =
   `navigator.clipboard.writeText` funciona sin permiso extra).
2. Abre la página **`addbyurl` limpia** (sin `cid`): `calendar.google.com/calendar/u/0/r/settings/addbyurl`.
3. Los pasos dicen **pegar (Ctrl+V / ⌘V) y confirmar**.

Marca en el modelo: `MS_CAL_APPS.google.copyFirst = true`. La lógica de copiar se
unificó en un helper `copyToClipboard(text, inputEl)` (best-effort, cae a
`execCommand`, nunca rechaza) que usan el botón de Google y el "Copiar" universal.

## Lo que SÍ precarga (no tocar)

- **Apple Calendar**: `webcal://…` — reemplazar `https?:` por `webcal:` abre la app
  Calendario ya suscribiendo. Funciona.
- **Outlook**: `outlook.live.com/calendar/0/addfromweb?url=<enc>&name=…` — precarga. Funciona.

Sólo Google perdió el deep link. Si algún día Google reactiva un prefill, se puede
volver a un `href` directo y sacar `copyFirst`.

## Gotcha de cache al arreglarlo

El commit anterior de un sibling (08ebb67) había subido la ref a `my-requests.js?v=4`
**sin cambiar el contenido** del archivo. Al cambiar YO el contenido bajo la misma
`?v=4`, Cloudflare (cachea 4h, ignora no-cache de origen) servía el contenido viejo
bajo la clave nueva. Hubo que ir a **v=5**. Regla de siempre: contenido cambiado =
`?v=` nuevo Y no reutilizar una versión que ya se sirvió con otro contenido. Ver
[cloudflare-js-cache](../deploy/cloudflare-js-cache.md).

## Verificación

`:7700` + Playwright, cargando el `my-requests.js` real: default Google, `href` sin
`cid`, click copia el feed y abre `addbyurl`, Apple/Outlook intactos, "Copiar" del
pie funciona.
