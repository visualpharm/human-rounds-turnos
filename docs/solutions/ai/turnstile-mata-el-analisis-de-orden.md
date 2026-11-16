# El gate de Turnstile mataba el análisis de órdenes en el wizard (en silencio)

**Síntoma (responsable del producto, 2026-07-12):** subís una orden equivocada en el wizard y el paciente no ve ningún error: ni "esto no parece una orden", ni re-subir, ni enviar igual. La pantalla `stepDocDudoso` de `servicio.js` existía y estaba bien escrita — nunca se ejecutaba.

**Causa raíz:** `abuso.py` exige Cloudflare Turnstile en `/api/analizar-orden` y `/api/preparacion` cuando `TURNOS_TURNSTILE_SECRET` está seteado (lo está: `.env` local y Coolify). `preparacion.js` renderiza el widget y manda `turnstile_token`; **`servicio.js` (el wizard) nunca lo hizo**. Entonces:

1. Toda subida del wizard → `403 {"error":"turnstile"}`.
2. `servicio.js analyze()` trata cualquier respuesta no-ok como "IA no disponible" — degradación deliberada — y **avanza en silencio**.

Resultado: la IA de órdenes estaba **muerta en producción** en el único flujo que la necesita. Toda solicitud llegaba al admin sin `ai_json`, y una factura de peluquería canina pasaba como orden médica.

**Verificación (así se reproduce):**
```
curl -s -X POST http://127.0.0.1:7700/api/analizar-orden -H 'Content-Type: application/json' \
  -d '{"servicio_id":"electrocardiograma-hospital","filename":"f.png","mime":"image/png","data_b64":"..."}'
→ 403 {"error": "turnstile"}
```
La IA en sí anda: `ai_orden.analizar_orden_3pass(<factura-peluqueria-canina.png>, "image/png", {...})` →
`verdict=documento_equivocado`, `documento.es_orden=false`, `tipo="una factura de peluquería canina"`, 3/3 pasadas.

**Fix (2026-07-12; physically modernized 2026-09-04):** shared canonical `static/platform/turnstile.mjs` named exports (`mount`/`token`/`reset`, sitekey from `GET /api/cuenta/config`) are imported by `service.js` and `upload-order.js`; the former classic file and browser global are deleted. `token()` waits for the widget to resolve (~1s) instead of sending `""` and eating a 403. `analyze()` distinguishes gate failures: Turnstile 403 and rate-limit 429 show a message and retry in the same control, while a real AI failure (timeout, network, quota) still degrades silently to `revision_manual`.

**Y el gate no corre en dev.** Server-side el dueño único es `turnstile.py` (`activo`/`sitekey`/`verificar`; `admin_auth.py`/`cuentas.py`/`abuso.py` delegan): en hosts de dev (`devbox:7700`, `localhost`, IP privada, `*.local`, host de una sola etiqueta) o con `TURNOS_TURNSTILE_OFF=1` sirve `turnstile_sitekey: null` **y** no exige token. El sitekey de Cloudflare está atado al dominio de producción, así que el widget nunca resolvía en `devbox:7700` y el login quedaba trabado en "Esperá a que cargue la verificación anti-bot" (responsable del producto, 2026-07-12). La misma función decide las dos cosas — cliente y servidor no pueden divergir.

**Invariant (do not relitigate):** if an endpoint is behind `abuso.py`, its client must import and mount `static/platform/turnstile.mjs`. A gated endpoint plus a client that sends no token is a dead feature that ordinary tests miss because the gate is inactive without `TURNOS_TURNSTILE_SECRET`. Every new client of `/api/analyze-order` must use the canonical module; every gate failure is shown to the person, never swallowed.

---

## Secuela (2026-07-20): el widget montado que NUNCA resuelve

**Síntoma (responsable del producto):** en producción, subir una orden **válida** devolvía error una y otra vez — y el mensaje culpaba a la foto ("probá con otra, más clara y con buena luz").

**Root cause:** the invariant above ("the client must mount the widget") was satisfied but insufficient. `upload-order.js` mounted the shared Turnstile client, but in production the widget **never called its callback**: `turnstile.render()` resolved neither success nor `error-callback` (Playwright observed 20 s without a token or error code; the console showed `[Cloudflare Turnstile] Cannot find Widget cf-chl-widget-…`). Then `token()` waited 8 s, resolved `""`, and the server correctly returned `403 {"error":"turnstile"}`. Retrying repeated the same closed loop.

Reproducción (headless, contra prod):
```
turnstileIframes: 0        # el widget no llegó a existir
REQ analyze-order  turnstile_token len = 0
RESP 403 {"error": "turnstile"}
```

**Fix:**
1. **El gate necesita las DOS mitades del par.** `activo()` ahora exige `SECRET` **y** `SITEKEY`. Con secret pero sin sitekey el cliente no puede producir un token ni queriendo — exigirlo era un lazo cerrado garantizado.
2. **Modo `blando` para las rutas de IA del paciente** (`turnstile.verificar(handler, token, blando=True)`, que usa `abuso.turnstile_ok`): token ausente, o Cloudflare inalcanzable, **no bloquea**. Lo que corta el abuso ahí es el rate limit por IP (10/hora, 30/día) + el fair-use de `costos_ia`, que siguen intactos. Turnstile en esas rutas es una señal de más, no la puerta.
3. Los gates de verdad (**login de admin y de paciente**, `admin_auth.py`/`cuentas.py`) siguen **fail-closed**: llaman a `verificar()` sin `blando`.

**Invariante (no relitigar):** *ningún gate anti-bot puede ser la única puerta de un flujo de paciente en un hospital público.* Un captcha que no renderiza es un evento normal (navegador viejo, extensión, red del hospital, Cloudflare caído), no un caso raro — y el costo de dejar pasar a un humano de más es infinitamente menor que el de dejar a alguien sin turno. Si un flujo de paciente se apoya en Turnstile, el gate va **blando** y el rate limit por IP hace el trabajo pesado.

**Segundo invariante, del mismo incidente:** *el motivo que se le muestra a la persona tiene que ser el REAL.* `upload-order.js` tenía un único `stepFallo()` que decía "probá con otra foto, más clara y con buena luz" para **cualquier** fallo — incluidos los nuestros (503, timeout, 403 del gate). Mandaba a repetir una foto que estaba perfecta. Ahora el motivo se clasifica: servicio caído / archivo pesado / archivo ilegible, y pedir otra foto pasa **sólo** cuando el modelo respondió que no la pudo leer. Una orden que entró sin análisis (`pendiente_ia`) tampoco dice "no reconocimos qué pide": eso culpa a la orden por algo nuestro.
