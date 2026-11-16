# Meta WhatsApp Cloud API — el test number se RE-PROVISIONA (y cómo descubrir el real)

**Síntoma (2026-07-23):** token fresco pegado por el responsable del producto, `me` responde bien, permisos `whatsapp_business_*` granted — pero cada GET/POST al `phone_number_id` guardado (`1232574123268638`) devuelve `code 100 / subcode 33` ("Object does not exist... missing permissions"). El dashboard incluso mostraba el número viejo.

**Causa:** Meta re-provisionó los assets de prueba de la app "SANA by Icons8": WABA nueva `3862973760677859`, test number nuevo **+1 555-144-5058** (`phone_number_id 1061836037016518`). El WABA/número viejos ya no existen para el token nuevo. Los IDs de test NO son estables entre visitas al dashboard.

**Receta de diagnóstico (siempre, antes de culpar al token):**
1. `GET /debug_token?input_token=<tok>` con el mismo token como auth → `granular_scopes[].target_ids` = la(s) WABA que el token VE de verdad.
2. `GET /<waba_id>/phone_numbers?fields=id,display_phone_number` → el `phone_number_id` real.
3. Recién ahí actualizar envs (`META_WA_PHONE_NUMBER_ID`, `TURNOS_WA_NUMERO`) y `<workspace>/sana-turnos-whatsapp-cloud/.env` (la copia canónica de assets Meta).

**Extras que se pueden hacer por API con ese token (sin dashboard):** `POST /<waba_id>/subscribed_apps` (suscribir la app al WABA). Lo que NO se puede sin app secret: fijar la callback URL del webhook y el allowlist de destinatarios de prueba — eso queda en el dashboard (responsable del producto).

**Gotcha Coolify de la misma sesión:** la lista de envs de la app tenía CADA fila duplicada (PATCH `/api/v1/applications/<uuid>/envs` actualiza UNA fila y deja la gemela con el valor viejo — cuál gana es indefinido). Dedupe: GET envs, borrar por `uuid` (`DELETE .../envs/<env_uuid>`) las segundas ocurrencias, y verificar con read-back `total/dups/empty`.
