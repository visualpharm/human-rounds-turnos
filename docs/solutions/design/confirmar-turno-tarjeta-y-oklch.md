# Confirmá: el turno arriba de todo — y el `color-mix` que pintó rosa el verde

**Fecha:** 2026-07-28
**Archivos:** `service.js`, `hospital.py`, `cuentas.py`, `test/test_usability_fixes.py`
**Origen:** handoff de Claude Design «Pinamar Turnos Confirmar Turno.dc.html»
(proyecto *Pinamar design system*, `dbe97c70-…`).

## Qué cambió

El último paso del wizard mostraba una frase corrida —«Cardiología en Hospital de
Pinamar para Sofía Herrera, DNI 99.000.001»— y recién abajo, entre los campos de
contacto y el botón, aparecía el turno elegido. Lo que la persona viene a
confirmar era lo último que leía.

Ahora la pantalla arranca por el turno, en su propia tarjeta verde:

- **Fecha y hora grandes**, la hora en monoespaciada (se lee de un vistazo), con
  «Cambiar fecha y horario» al lado del dato que cambia.
- **Reloj del hold** en el borde derecho: «Te reservamos el horario 12:04»,
  contando de verdad.
- **Qué y con quién** («Medicina general con Victoria Mailén Zubiri») y **dónde**
  (centro + dirección) con su propio «Cambiar centro».
- Debajo, **para quién** es; después **«¿Cómo te avisamos?»** con los datos de la
  cuenta como TEXTO (no formulario).

**2026-10-27 (responsable del producto: «muchos marcos; una página blanca, una sola capa»):** el paso
es una página blanca y la tarjeta del turno es el ÚNICO recuadro — sin tarjeta
alrededor del paso, sin borde en «¿Cómo te avisamos?», sin el sello «Cuenta
verificada». WhatsApp y email van en una línea, cada uno con su ícono de Icons8 en
vez de la etiqueta y su propio «Cambiar».

## Decisiones que no hay que relitigar

**El reloj sale del `ttl_min` que manda el servidor**, no de un 15 escrito en el
cliente: `reservas.py` es el dueño del TTL y `holdTomar` anota `wiz.holdHasta` en
cada toma/renovación. Al llegar a 0:00 el cartel lo dice («Se venció la reserva
del horario») en vez de desaparecer; el envío se intenta igual, porque la palabra
final es el 409 de `POST /api/requests`, que ya sabe volver a la grilla sin
perder nada de lo cargado.

**Una franja no es una fecha.** Sin slot concreto no hay reloj (no hay nada
reservado), el titular va más chico y la nota dice que la fecha la confirma el
centro. Es la misma regla de siempre: no prometer un horario que nadie confirmó.

**El modo de ingreso de la cuenta viaja en el HTML** (el sello «Cuenta
verificada» que lo usaba se quitó el 2026-10-27). `hospital.py` escribe `auth_mode` dentro de `#svc-data`
(server-side, vía `cuentas.modo_auth`) en vez de que el cliente consulte
`/api/account/config`: leerlo por fetch repintaría la pantalla después del primer
pintado, que es justo lo que se arregló el 2026-07-27.

**Los datos de contacto se leen, pero se pueden arreglar sin salir del trámite.**
El mockup no tiene ningún control ahí; sacar la edición del todo dejaba sin
salida a quien tiene un teléfono mal cargado. Regla: el dato que FALTA se pide en
el acto (si no, no hay forma de avisarle a nadie) y el que está se cambia con un
«Cambiar» que convierte la caja en formulario. El teléfono se rotula **WhatsApp**
— es por ahí que el hospital escribe (recordatorio del día previo).

## El bug que vale para cualquier proyecto

La tarjeta salió **rosa salmón** con este CSS, que parecía obviamente verde:

```css
background: color-mix(in oklch, var(--accent-soft) 55%, var(--panel));
/* --accent-soft: #eaf4ec (verde claro) · --panel: #fff */
```

`#fff` es **acromático**: en oklch no tiene tono. Al interpolar, el tono
resultante es `none`, y el navegador lo resuelve como 0 → rojo. El valor
computado lo canta:

```
oklch(0.976581 0.00823573 none)
```

**Regla: mezclar con blanco o negro va `in srgb`.** `in oklch` sirve para mezclar
dos colores cromáticos. Y verificar el color con `getComputedStyle` en el
navegador, no leyendo el CSS — el código se leía correcto.

## Verificado

Las cuatro variantes en `:7700`, sesión real (identidad sintética Sofía Herrera,
`/auth/sso` stub): slot con médico, slot sin médico, franja sin agenda, y con la
foto de la orden al costado (orden de ergometría real, análisis real). Envío
completo hasta «Solicitud enviada». Cero desborde horizontal en 360 / 390 / 768 /
1024 / 1280.

---

## Ronda 2 (mismo día, el responsable del producto sobre la pantalla ya implementada)

**El reloj se fue.** «Probably we don't need the time ticking because it's really
fast to confirm it… don't show this ticker because it will only distract.» Lo que
quedó: la reserva dura **3 minutos con sesión abierta y 10 sin ella**
(`reservas._ttl_para`, decidido por request), y el aviso aparece **sólo en los
últimos 60 segundos, en el botón de enviar**, escrito en LETRAS — «Te guardamos
el horario cuarenta y cuatro segundos más». Es una frase que se lee, no un
cronómetro que corre; y aparece en el único lugar que la persona está mirando
cuando todavía le queda algo por hacer.

Regla general que deja: **un contador visible es una promesa de urgencia**. Si el
99% de la gente termina mucho antes del límite, mostrarlo todo el tiempo sólo
apura a quien no lo necesita. Mostralo cuando falte poco, y decí qué significa.

**Dos implementaciones del mismo formato tienen que probarse juntas.** El
teléfono ahora se escribe igual en el navegador (`formato.js`, ex
`admin-formato.js` — perdió el prefijo porque ya no es del panel) y en el
servidor (`hospital.py:_tel_texto`, que renderiza las páginas de centro y el
contacto de escape). Son dos códigos distintos para una sola regla, así que hay
un test que exige que las dos tengan los mismos cortes y las mismas excepciones
(`test/formato.test.mjs`). Dos casos que estaban mal en las DOS: el `15`
histórico pegado después del característico, y los `0800`, que no son
geográficos y salían cortados como si lo fueran («8002 22-1002»).

**La forma canónica, y UN lugar donde vive** (responsable del producto, mismo día, después de ver
las dos formas conviviendo): se muestra y se guarda `+54 9 2254 12-3456`
(celular) / `+54 2254 40-1234` (fijo). El abonado se corta dejando los últimos
4 dígitos después del guion — un criterio para 8, 7 y 6 dígitos, no tres casos.

Dos cosas que hay que respetar acá:

- **El 9 no se inventa.** `+54 11 4321-5678` y `+54 9 11 4321-5678` son dos
  líneas distintas, así que el 9 sale sólo si el valor guardado lo trae
  marcado (el 9 después del país, o el 15 después del característico). Un
  número pelado se muestra como fijo. El link de WhatsApp sí usa `549` siempre:
  es lo que wa.me pide, y un fijo simplemente no va a tener cuenta.
- **Todo lo que es "argentino" vive en un bloque `PAIS`** al principio de
  `formato.js` y de `formato.py`. Para otro país se cambia ese bloque y nada
  más; el resto del archivo sólo aplica las reglas.

**Dos runtimes, dos archivos, un test que los corre a los dos.** El test de
paridad no mira el texto del código: ejecuta `formato.js` en un contexto `vm`,
ejecuta `formato.py` con `spawnSync("python3")` y compara las tres salidas
(texto, E.164, wa) para 18 formas de escribir un número. Encontró drift real el
día que se escribió: los campos con varios números (`"(02254) 49-1670 /
49-1671"`) se partían sólo del lado de Python.

**Trampa de caché que mordió acá:** bumpear el `?v=` y DESPUÉS seguir editando el
archivo deja al navegador con la versión vieja bajo el número nuevo. El síntoma
fue un teléfono con el formato anterior en una página cuyo resto ya era el
nuevo. El bump va al final, o se repite.

**Guardar canónico rompe el envío si el que manda no usa la misma regla.**
Al pasar `usuarios.contacto` a la forma canónica, `otp_send` quedaba mandando
a `542254123456` — SIN el 9 — porque `_meta_digits` devolvía tal cual todo lo
que empezara con `+`, y `_chat_id` mandaba los dígitos pelados, sin país. El
número existe, así que no falla ruidosamente: simplemente no llega. Los dos
pasan ahora por `formato.tel_wa`. Regla: **cambiar cómo se GUARDA un dato
obliga a revisar a todos los que lo LEEN para mandarlo a un tercero**, no sólo
a los que lo muestran.

La migración (`scripts/normalizar_telefonos.py`) es dry-run por defecto, guarda
cada valor anterior en `telefono_backup` y **se saltea cualquier fila cuyos
últimos 10 dígitos cambiarían** — eso sería cambiarle el teléfono a alguien, no
su formato. Los valores que no son números (`'F'`, `'wa:11'`) se listan y se
dejan intactos.
