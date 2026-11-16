# SISA PUCO — API pública de "Consulta de Cobertura de Salud", reverse-engineered

**Fecha:** 2026-07-16. **Contexto:** puco.py hoy apunta al **WS131 REST** (`POST /sisa/services/rest/puco/<dni>` + `{usuario,clave}` → XML), que necesita una cuenta de servicio SISA pendiente de la contraparte técnica. La **consulta pública** de la web SISA (`sisa.msal.gov.ar/sisa/#sisa` → "Consulta de Cobertura de Salud (PUCO)") devuelve exactamente la misma cobertura por DNI **sin credenciales**. Esto documenta cómo funciona, por si sirve de stopgap.

## Qué es
No es REST: es la **GWT-RPC** del panel PUCO del SPA de SISA. Dos POST a `text/x-gwt-rpc`:
1. **Bootstrap** (mint de `JSESSIONID`, sin auth): `LoadEnvironmentAction` → `/sisa/sisa/dispatch`.
2. **Búsqueda**: `ListService.getPage` con el DNI → `/sisa/sisa/service/list`.

Sin cookie de sesión válida, el paso 2 responde `//EX[...SessionTimeOutException...]`. El `LoadEnvironmentAction` es el único que mint-ea el `JSESSIONID` (el GET a `/sisa/` sólo da una cookie de load-balancer; `LoadPucoVersionAction` ya exige sesión). **No se necesita token de reCAPTCHA** en el RPC (el widget está en la página pero no gatea la llamada, al menos a bajo volumen).

## Receta headless (curl puro, verificada)
```bash
BASE=https://sisa.msal.gov.ar/sisa/sisa
H=(-H 'Content-Type: text/x-gwt-rpc; charset=UTF-8' -H 'X-GWT-Module-Base: https://sisa.msal.gov.ar/sisa/sisa/' -H 'Referer: https://sisa.msal.gov.ar/sisa/')

# 1) bootstrap → JSESSIONID en el cookie jar
ENV='7|0|6|https://sisa.msal.gov.ar/sisa/sisa/|2CB1B348CFC741E7C76AF8DA0A2C6911|net.customware.gwt.dispatch.client.standard.StandardDispatchService|execute|net.customware.gwt.dispatch.shared.Action|ar.gob.msal.sisa.shared.rpc.action.LoadEnvironmentAction/1047162293|1|2|3|4|1|5|6|'
curl -s -c jar.txt "${H[@]}" -H 'X-GWT-Permutation: 2CB1B348CFC741E7C76AF8DA0A2C6911' --data-raw "$ENV" "$BASE/dispatch" >/dev/null

# 2) búsqueda por DNI (reemplazar 20456781)
BODY='7|0|14|https://sisa.msal.gov.ar/sisa/sisa/|5CFBDB55F3DE4A47FE42E765E5AA02D3|ar.gob.msal.sisa.client.commons.components.lista.service.ListService|getPage|java.lang.Integer/3438268394|java.util.List|Z|ar.gob.msal.sisa.shared.model.list.ComplexFilter/30068811|java.util.ArrayList/4159755760|ar.gob.msal.sisa.client.commons.components.lista.simple.SearchFilter/1978531670|97390|ar.gob.msal.sisa.client.entitys.list.Filter$OPERATION/3408968308|ar.gob.msal.sisa.client.entitys.list.Filter$OPERATOR/860546718|20456781|1|2|3|4|10|5|5|5|6|6|5|7|5|6|8|5|790|5|1|-2|9|0|9|1|10|11|12|0|13|0|0|14|0|0|1|5|25|0|0|'
curl -s -b jar.txt "${H[@]}" -H 'X-GWT-Permutation: 5CFBDB55F3DE4A47FE42E765E5AA02D3' --data-raw "$BODY" "$BASE/service/list"
```
`97390` = id de la lista PUCO; `25` = page size; el DNI viaja como string plano en el body.

## Respuesta
Formato GWT-RPC string-pool: `//OK[<ints de referencia>,<pool de strings>,0,7]`. El pool trae las filas de cobertura. Cada fila = `[TipoDoc, NroDoc, Sexo, "Cobertura Social", "Denominación"]`. Varias filas = varias coberturas.
- un DNI con tres coberturas → 3 filas, una por obra social; Denominación = `APELLIDO, NOMBRE` del titular.
- un DNI con PAMI → 1 fila: INSSJP (PAMI), sexo, Denominación = el nombre completo del titular.

⚠️ **`Denominación` = el NOMBRE del ciudadano, no la cobertura** — mismo gotcha que anota CLAUDE.md/puco.py. NUNCA reexponer al cliente.

## Por qué el WS131 REST sigue siendo el camino correcto (esto es sólo stopgap)
- **Brittle:** los strong-name `2CB1B348…`/`5CFBDB55…` son hashes de la policy de serialización GWT — cambian en cada redeploy del SPA de SISA. Habría que re-scrapearlos (viven en el `.nocache.js`/permutación compilada).
- **reCAPTCHA:** hoy no gatea el RPC, pero puede endurecerse bajo volumen/bot-detection; server-to-server sin browser es más sospechoso.
- **Scraping de un sistema de gobierno:** ToS, rate y riesgo de ban. El WS131 es el contrato oficial, estable, con XML limpio y sin captcha.
- **Parsing:** hay que deserializar el string-pool GWT-RPC (fiddly) vs. el XML directo del WS131.

## Estado: WIRED como fallback (2026-07-16)
`puco.py:consultar()` ya encadena **fake → WS131 (si hay creds) → este GWT-RPC público → manual**. `_consultar_publico`/`_parse_publico` implementan la receta de arriba; env `TURNOS_PUCO_PUBLIC` (ON por default, `0` apaga) y `TURNOS_PUCO_GWT_PERM_DISP`/`_LIST` (pisar los strong-names sin deploy). **Verificado en prod** (`sana-turnos.brieflysites.com`, IP de datacenter de Coolify) el 2026-07-16: un DNI con tres obras sociales y otro con PAMI, todos con `rnos:null` (firma del path público) y SIN reCAPTCHA wall. Tests: `TestParsePublico`/`TestConsultarPublico` en `test/test_puco.py` (incluye el invariante nombre-no-sale).

### Gotcha: "sólo trae UNA obra social en local" = modo fake, no un bug
Síntoma real (2026-07-16): el form de registro en `devbox:7700/cuenta` mostraba una sola obra (IOMA) para un DNI que en el padrón tiene 3. Causa: el `.env` local tenía `TURNOS_FAKE_PUCO=1`, y el fake mapea por el ÚLTIMO dígito del DNI (0-3→IOMA, 4-5→IOMA+OSECAC, 6-7→OSECAC, 8-9→no figura) — nunca los datos reales. La API multi-obra y el form (register: itera `state.puco.obras`; edit: `cobertura.obras_sociales`) ya renderizan la lista completa; era el dato lo que venía sintético.
**Decisión: local corre el padrón REAL** — `TURNOS_FAKE_PUCO` quedó comentado en `.env` (responsable del producto, 2026-07-16: "leave the actual lookup even on local"). Para diagnosticar multi-obra sin red igual sirve un DNI terminado en 4/5 con fake ON. Antes de gritar "bug", `curl -s -XPOST localhost:7700/api/puco -d '{"dni":"..."}'` y mirar `obras_sociales`.

**Recomendación:** WS131 sigue siendo el objetivo (estable, con `rnos`, sin scraping) — perseguir las creds con la contraparte técnica; cuando lleguen, WS131 gana por precedencia y el público queda de red de seguridad. Mientras tanto el registro muestra cobertura REAL con el público, sin esperar.
