# Login con identidad nacional — RENAPER / AUTENTICAR / Mi Argentina

Estado: **scaffolding listo, esperando credenciales reales.** El módulo
`auth_renaper.py` corre el flujo entero HOY con `TURNOS_FAKE_RENAPER=1`; cuando
lleguen `client_id`/`client_secret` del IDP se prende sin tocar código.

Contexto (reunión 2026-07-17 con Johnny + administración, transcripción Lira
"Integración del Portal de Salud Municipal en Pinamar"): la municipalidad quiere
que el portal se loguee **"con RENAPER o Mi Argentina"** — "para que a la gente
no le pidas que se registre setenta veces en distintos sistemas". La autorización
del trámite ante RENAPER **les llegó el 2026-07-16**; están por recibir el
entorno de pruebas. Hoy el sitio usa contraseña propia (y venían de Google auth
en el prototipo previo).

## Las tres plataformas (y cuál es cuál)

Son cosas distintas que la gente mezcla en la reunión:

| Plataforma | Qué es | Para nosotros |
|---|---|---|
| **RENAPER / SID** | Registro Nacional de las Personas. **SID** = validación de identidad biométrica (foto DNI + reconocimiento facial). API de *verificación*, no un "login con botón". | La fuente de verdad del padrón. Requiere contrato propio con RENAPER (el trámite que la muni ya autorizó). |
| **AUTENTICAR** | Plataforma de autenticación de Nación (Jefatura de Gabinete). **OpenID Connect** (OAuth 2.0). El IdP federado para organismos. | El camino técnico correcto para "login con el Estado": flujo authorization-code, el organismo se registra como Relying Party. |
| **Mi Argentina** | El perfil ciudadano (`id.argentina.gob.ar`). También OIDC; endpoints públicos documentados. | La cara conocida por el ciudadano ("entrar con Mi Argentina"). Su login está montado sobre la misma identidad nacional. |

Los tres convergen en **OpenID Connect / authorization-code flow**, que es
exactamente lo que implementa `auth_renaper.py`. La diferencia entre apuntar a
AUTENTICAR o a Mi Argentina es cambiar tres URLs (authorize/token/userinfo) por
env — el código es el mismo.

### Endpoints documentados de Mi Argentina (defaults del módulo)

- authorize: `https://id.argentina.gob.ar/authorize/`
  (`client_id`, `redirect_uri`, `response_type=code`, `scope=openid …`, `state`, `nonce`)
- token: `https://id.argentina.gob.ar/token/` (POST, `grant_type=authorization_code`,
  `client_id`, `client_secret`, `code`, `redirect_uri` → `access_token`, `id_token`, `refresh_token`)
- userinfo: `https://id.argentina.gob.ar/userinfo/` (POST, `Authorization: Bearer <access_token>`)

AUTENTICAR usa el mismo protocolo (standard flow + implicit; nosotros usamos
**sólo authorization-code**, que es el server-side seguro). Sus URLs concretas
salen del onboarding de RP — se cargan por env cuando lleguen.

## Cómo está armado (auth_renaper.py)

- **Setting `TURNOS_AUTH_METHODS`** = `password` | `renaper` | `password,renaper`
  (default `password`). El sitio puede ofrecer la contraseña propia, el login
  nacional, o **los dos a la vez** — que es lo que pidió el responsable del producto.
  - **Anti-lockout (regla dura):** cualquier config que dejaría al sitio sin
    ningún método utilizable (p.ej. `renaper` sin credenciales cargadas) degrada
    sola a password-only. Un typo en Coolify jamás deja a los pacientes afuera.
- **`GET /auth/renaper`** — arranca: firma una cookie de estado `st_oidc`
  (HMAC, 10 min, con `state`/`nonce`/`next`) y redirige al IDP.
- **`GET /auth/renaper/callback`** — vuelta: valida el estado, canjea el `code`
  por tokens (canal TLS directo al IDP), saca los claims, **crea-o-actualiza el
  usuario por DNI** (sin pisar lo que la persona curó a mano) y abre la MISMA
  sesión `st_ses` que el login con contraseña.
- El usuario creado por esta vía queda con `pass_hash` NULL (no puede loguearse
  con contraseña salvo que después haga una recuperación). Los claims crudos se
  guardan en `usuarios.renaper` (jsonb).
- El botón "Ingresar con Mi Argentina" aparece en login **y** registro,
  server-driven por `GET /api/account/config` (`cfg.auth`). Con `password` apagado
  el form de contraseña desaparece y queda sólo el botón.
- Rate limit propio (`TURNOS_RENAPER_PER_HOUR`, default 60), bucket separado.
- **`TURNOS_FAKE_RENAPER=1`** corre todo sin red (dev/tests). Ships OFF por
  default (mismo patrón que el rollout de OTP por WhatsApp).

## Qué falta para prenderlo en prod

1. **Credenciales del IDP** (las que la muni está por recibir):
   `TURNOS_RENAPER_CLIENT_ID`, `TURNOS_RENAPER_CLIENT_SECRET`.
2. **Registrar el `redirect_uri`** ante el IDP: `https://<dominio>/auth/renaper/callback`.
   El módulo lo arma solo del `Host` (o se fija con `TURNOS_RENAPER_REDIRECT_URL`).
3. **URLs del entorno**: si es AUTENTICAR (no Mi Argentina), setear
   `TURNOS_RENAPER_AUTHORIZE_URL` / `_TOKEN_URL` / `_USERINFO_URL` del entorno de
   pruebas, y `TURNOS_RENAPER_SCOPE` según los claims que autoricen.
4. **Prender el método**: `TURNOS_AUTH_METHODS=password,renaper` (o `renaper`
   solo) en el env runtime de Coolify.
5. **Guardar las credenciales en credential vault** (item "Sana Turnos — Admin" o uno
   nuevo "Sana Turnos — RENAPER").

## Pendientes / decisiones abiertas

- **Verificación de firma del `id_token` (JWKS):** hoy el `id_token` se decodifica
  SIN verificar firma — la integridad la da el canal TLS del token endpoint
  (authorization-code server-side) y los datos salen del UserInfo (también TLS).
  Cuando el IDP real publique su JWKS, sumar la verificación de firma como
  defensa en profundidad (`jwks_uri` del `.well-known/openid-configuration`).
- **Regla municipal de residencia** (pedido del administrador en la reunión):
  "para sacar turno tenés que tener emisión en Pinamar de más de 6 meses". Eso
  depende de que el token traiga **domicilio** y/o **fecha de emisión del DNI**.
  Por eso guardamos los claims crudos en `usuarios.renaper`: cuando tengamos el
  token real, mirar qué campos vienen y recién ahí decidir si/cómo se aplica el
  gate (hoy NO se bloquea nada). Ojo: es una regla sensible — mejor advertir que
  bloquear en silencio.
- **Mapeo de claims:** `_extraer_datos` cubre los nombres OIDC estándar
  (`given_name`, `family_name`, `birthdate`, `gender`, `dni`/`cuil`). El IDP real
  puede usar otros nombres (`nombre`, `nro_documento`…) — ya se toleran varios,
  pero verificar contra la respuesta real y ampliar si hace falta.

## Referencias

- AUTENTICAR (protocolo/flujos): argentina.gob.ar/jefatura/innovacion/autenticar/desarrolladores-autenticar
- Mi Argentina (login): argob.github.io/mi-argentina-docs/doc/iniciar-sesion.html
- RENAPER SID: argentina.gob.ar/interior/renaper/sid-sistema-de-identidad-digital
- Precedente en este repo de interfaz con SSO AFIP/RENAPER (read-only, decrypt):
  `docs/solutions/misalud-digital-pba-turnos-api.md`
- Tests: `tests/test_auth_renaper.py`.
