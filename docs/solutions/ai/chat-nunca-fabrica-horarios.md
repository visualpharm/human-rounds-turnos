---
title: El chat inventaba fechas de turno y la app las pintaba como reales
scope: repo
area: ai
tags: [chat, turnos, alucinacion, opciones, seguridad]
created: 2026-08-07
refs: [chat_agent.py, chat_tools.py, prompts.py]
status: active
---

## Problem

El asistente a veces respondía con un turno que no existe. Reproducido en vivo
contra el modelo real (Pinamar pack, sin `TURNOS_FAKE_AI`):

- `"tengo tos y fiebre desde ayer, quiero un turno"` → el modelo ruteó bien a
  guardia y, sin llamar NINGUNA tool, cerró con
  `[opciones: CAPS Ostende: lunes 03/02 08:00 | ...]` — hoy era agosto, no
  febrero. La app pintaba esas opciones como chips tocables.
- Incluso en una respuesta bien fundamentada (con 16 slots reales cosechados),
  la línea de opciones final traía horarios (`Hoy, 16:20`) que NO estaban entre
  los 16 slots reales de ese turno.

El responsable del producto lo había fotografiado antes con otro ejemplo ("CAPS Ostende: lunes 25/11
a las 08:00", también inventado).

## Root cause

Dos agujeros independientes, cada uno suficiente por sí solo:

1. **El modelo no siempre llama una tool antes de ofrecer horarios.**
   `_requires_catalog_routing` sólo fuerza una tool call para preguntas tipo
   "¿a qué médico voy?" — un pedido directo ("...quiero un turno") no la
   dispara, así que el modelo quedaba libre de contestar de memoria.
2. **El filtro de `opciones` sólo corría `if turnos:`.** `chat_full()` sacaba
   de `opciones` los horarios que coincidían con `turnos` (los reales,
   cosechados de las tools) — pero cuando `turnos` quedaba vacío (exactamente
   el caso de arriba: cero tool calls), el filtro entero se saltaba y
   cualquier fecha inventada por el modelo llegaba intacta al chip.
   `_strip_slot_restatements` tenía el mismo problema en la prosa: sólo sacaba
   un renglón si su horario era subconjunto de los horarios YA conocidos como
   reales — con `turnos=[]` no había nada contra qué comparar, así que
   devolvía el texto sin tocar.

## Solution

Tres capas, todas necesarias:

- **Grounding**: `chat_agent._requires_slot_grounding(texto)` detecta un
  pedido explícito de turno ("quiero/necesito/pedime un turno", "book an
  appointment") que no sea ya un `_requires_catalog_routing`, y fuerza
  `tool_choice="required"` en la primera iteración — a diferencia de
  `_requires_catalog_routing`/`_routing_tool`, NO fija cuál tool ni
  corto-circuita con una respuesta fija: el modelo sigue escribiendo su propia
  prosa (guardia primero, incluida), pero recién después de tener datos
  reales. Excluye los red-flags de emergencia (dolor de pecho, ACV, desmayo):
  para esos el prompt ya pide quedarse en la acción segura, sin agregar nada
  de una tool.
- **Enforcement (la red de seguridad real)**: el filtro de `opciones` en
  `chat_full()` ahora corre SIEMPRE, nunca sólo `if turnos:`, y saca CUALQUIER
  opción con un horario (`_clocks(o)` no vacío) — coincida o no con `turnos`:
  si coincide es redundante con la lista estructurada; si no coincide, es
  inverificable, y el modelo nunca es la autoridad sobre si un horario es
  real. `_strip_slot_restatements` aplica la misma idea en la prosa: un ítem
  de lista con un horario se saca siempre (sin comparar contra un set de
  horarios "reales" primero) — sólo una oración normal, no en formato lista,
  conserva sus propios horarios (eso es información, no una oferta de turno).
- **Instrucción**: `prompts.py` (`chat_sistema`, mitad editable) ahora prohíbe
  explícitamente escribir una fecha/hora que no haya salido de una tool en
  ESE MISMO turno, y pide reusar el `etiqueta` de la tool tal cual, incluso
  cuando la respuesta ya mandó a la persona a la guardia.

`turnos` (la lista estructurada que la web renderiza como links reales) nunca
necesitó este arreglo: se arma en código, siempre y sólo desde los resultados
reales de las tools (`_slots_de_las_tools`), nunca desde el texto del modelo
— por diseño no puede alucinar.

## How to apply

- Un dato que el modelo puede "ofrecer" (una fecha, un monto, una
  disponibilidad) nunca se muestra sólo porque el modelo lo escribió: se
  muestra sólo si la app puede señalar el resultado de tool concreto que lo
  respalda, ESTE turno. La comparación va por dato (hora + centro/servicio),
  nunca por igualdad de string — el modelo parafrasea.
- Un filtro de seguridad que corre `if <hay_datos_reales>:` dejaba pasar
  exactamente el caso "no hay datos reales" sin filtrar — que es la mitad de
  los casos de alucinación. El filtro tiene que correr siempre, con "no hay
  nada que verificar" resolviendo a "no se muestra nada", nunca a "se muestra
  lo que sea que haya".
- Forzar `tool_choice="required"` (sin fijar CUÁL tool) es más seguro que un
  regex tratando de predecir cuándo el modelo se va a olvidar de llamar una
  tool — pero igual hay que excluir los casos donde forzar una tool call
  activamente contradice una instrucción de seguridad existente (acá, no
  agregar nada a la respuesta de emergencia).
