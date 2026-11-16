---
title: El chat listaba los mismos turnos tres veces (texto + chips + enlaces)
scope: repo
area: ai
tags: [chat, turnos, enlaces, i18n, whatsapp]
created: 2026-08-06
refs: [chat_agent.py, chat_tools.py, chat-widget.js, chat-ui.js, prompts.py]
status: active
---

## Problem

Una respuesta del chat con turnos disponibles mostraba la MISMA información
hasta tres veces: la lista que escribía el modelo dentro del texto, los chips
del marcador `[opciones: ...]` y la lista de horarios reales que pinta la app.
Encima, arriba iba una lista con viñetas de los centros y abajo otra lista con
las horas de esos mismos centros. El responsable del producto lo marcó como "no debería haber listas
separadas de centros; todo tiene que estar listado una sola vez".

## Root cause

Cada capa resolvía la duplicación por su cuenta y ninguna era dueña del
problema:

- El prompt le pedía al modelo copiar la `etiqueta` de cada turno **y** cerrar
  con `[opciones: ...]` — o sea, escribirlos dos veces por diseño.
- `chat_full()` leía sólo el ÚLTIMO resultado de `available_appointments`
  (`break` incondicional). Una especialidad ofrecida en dos centros son dos
  llamadas: si la última venía vacía, la app no pintaba nada y quedaban los
  chips duplicando el texto.
- El widget intentaba recortar el texto (`textoAntesDeTurnos`) buscando la
  etiqueta literal del turno: si el modelo escribía "**CAPS Ostende**: Martes
  26 a las 08:30" en vez de la etiqueta exacta, no cortaba nada — y cuando sí
  cortaba, se llevaba puesto todo lo que venía después (el "no necesitás
  orden").

## Solution

El servidor es el único dueño de la deduplicación, para los dos canales:

- `chat_agent._slots_de_las_tools()` junta los turnos de TODAS las llamadas a
  `available_appointments` del turno, deduplicados por destino, y sólo deja
  `mas_horarios_href` cuando todos son del mismo servicio.
- `chat_agent._strip_slot_restatements()` saca del texto lo que la app va a
  pintar: un ítem de lista cuyos relojes son todos horarios nuestros, un
  renglón que repite la etiqueta de un turno, y un ítem que es SÓLO el nombre
  de un centro que ya encabeza una fila de horarios. Un renglón con
  información propia ("sin turno, martes y jueves de 8 a 12") se queda.
- Cada canal la muestra una vez: la web con enlaces reales (agrupados por día
  y, si hay más de un centro, por centro), WhatsApp con la lista numerada —
  `chat()` la arma desde `parsed["turnos"]` cuando el modelo no dejó opciones.
- El prompt (`chat_sistema`) ahora pide ofrecer los horarios UNA vez, en la
  línea de opciones, y no repetir la lista de centros. La regla de "no
  repetir lo que ya se toca" también quedó en el prompt de la entrevista.

De paso, todo lo nombrado quedó enlazado: `chat_tools.enlaces_para()` agrega
la especialidad (`/d/<cluster>`, tanto "Pediatría" como "Pediatra") y el
servicio nombrado cuando es inequívoco — con el nombre normalizado, para que
"Medicina general" en tres CAPS caiga en su página de especialidad en vez de
enlazar al primer centro que apareció. Los enlaces se deduplican por ETIQUETA
y no por destino: el servicio y el centro son dos palabras distintas de la
misma oración y las dos merecen su enlace.

## How to apply

- Una lista de datos que la app pinta NO se le pide también al modelo. Si el
  modelo la escribe igual, se saca en el servidor, no en el cliente: el
  recorte en JS sólo cubría la web y WhatsApp quedaba con el texto crudo.
- Si el modelo puede llamar N veces a la misma tool en un turno, juntá los N
  resultados; leer sólo el último es un bug que aparece recién cuando hay dos
  centros.
- Un enlace inline se busca por texto en el mensaje: la comparación en
  `chat-ui.js` es sin mayúsculas ni tildes (`plegar()`, plegado 1 a 1 para no
  perder los índices) y el ancla se pinta con las palabras que escribió el
  asistente, nunca con la etiqueta del catálogo.
