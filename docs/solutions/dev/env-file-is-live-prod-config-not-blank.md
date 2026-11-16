---
title: El .env de un checkout es config real de producción — un harness lo aísla, no lo vacía
scope: repo
area: dev
tags: [testing, env, hermetic, mailgun, auth, harness]
created: 2026-08-05
refs: [9eec027, b93ac60, ea142ed]
status: active
---

## Problem
Tres bocados distintos del mismo problema en un solo día, escribiendo el
tier de rutas de Tier 3:
1. `admin_auth._env()` cae a `.env` cuando una var de entorno está VACÍA
   (no ausente) — un `.env` de worktree con el `TURNOS_ADMIN_TOTP_SECRET`/
   Turnstile reales hacía que el login de e2e admin (seeded, sin esos
   secretos) devolviera 401 `{"need_totp": true}` **aunque el harness ya
   vaciaba esas vars en el subprocess env** (`docs/roadmap/
   module-contract-design.md`, "e2e harness gotcha, applies to every
   worktree").
2. `accounts.py`'s `_auth_mode()` es DB-first con fallback a
   `TURNOS_AUTH_MODE` del `.env` — el `.env` de este checkout (que refleja
   la config real de producción de Pinamar) trae `TURNOS_AUTH_MODE=renaper`,
   así que la primera corrida del tier de rutas mandó CADA caso de
   `test_account_login.py` por `_login_renaper` en vez del camino de
   password: 400 `proveedor_de_identidad_requerido` en vez de los 401/200
   esperados.
3. `email_mailgun.py`'s `_cfg()` lee `MAILGUN_API_KEY`/`DOMAIN` directo de
   `.env` sin override de test — un test de cobertura de slice 4
   (`test_cancel_waitlist_trigger.py`) que ejercita la notificación mandó un
   email REAL a una dirección fixture descartable (`beto@example.com`) vía
   la cuenta Mailgun real de Human Rounds, en la primera corrida.

## Root cause
Un `.env` copiado a un checkout/worktree de desarrollo NO es un archivo de
prueba vacío — es, byte a byte, la config de producción real (o su espejo
más cercano): secretos de TOTP/Turnstile reales, `TURNOS_AUTH_MODE` real,
credenciales Mailgun reales. Cualquier test que lo lea sin aislarlo
explícitamente hereda ese comportamiento de producción, en silencio, hasta
que un caso lo expone (login que nunca debería pedir TOTP, login que nunca
debería exigir federación, un email que nunca debería salir).

## Solution
Doctrina fijada hoy en tres capas:
- **`TURNOS_ENV_FILE`** (`config.py`) — un override de proceso que apunta a
  un archivo mínimo (o vacío) en vez del `.env` real; el e2e de Tier 2 ya lo
  usa para que un `.env` de desarrollador con secretos reales nunca sea
  consultado (`docs/roadmap/tier3-harness-design.md`, "hermetic env
  (`TURNOS_ENV_FILE` points at an empty temp file...)").
- **Defaults del fixture, no del `.env`** (`test/_routes_fixture.py`) —
  `os.environ.setdefault(...)` para cada var sensible a producción:
  `TURNOS_AUTH_MODE=password` (nunca `renaper`), `MAIL_ENABLED=0`,
  `TURNOS_SKIP_HSI=1`, `TURNOS_FAKE_AI=1`, y se hace `pop()` explícito de
  `TURNOS_BASIC_AUTH_USER`/`PASS` para que el gate de `site_auth.py` no
  devuelva 401 antes de llegar a la ruta bajo test. `setdefault`, no `set`
  incondicional — un test que SÍ quiere ejercitar la rama renaper o afirmar
  un envío real puede sobreescribir la var él mismo.
- **Nunca "vaciar las keys del `.env` del checkout"** como solución — eso
  rompe el desarrollo normal (el dev SÍ necesita esos secretos para probar
  a mano) y es frágil (alguien los vuelve a poner). El aislamiento vive en
  el harness, no en el archivo compartido.

## How to apply
1. Todo test que boot-ea la app real (routes tier, e2e, cualquier
   `LiveServerMixin`) fija sus propios defaults de env ANTES de importar
   `db`/`server` — nunca asume que el `.env` del checkout es inerte.
2. Nueva var sensible a producción (auth mode, feature flag de canal
   externo, credencial de proveedor) → agregarla a la lista de
   `setdefault()` del fixture correspondiente, con un comentario que
   explique QUÉ rompe si no se fija (mismo patrón que las líneas de
   `_routes_fixture.py` referenciadas arriba).
3. Antes de dar por buena una corrida nueva de un test que toca login,
   notificaciones o cualquier conector externo: preguntarse "¿esto pudo
   haber mandado algo real?" — no asumir que "pasó verde" significa "no
   tuvo efectos secundarios".

## Related
- [ai/turnstile-mata-el-analisis-de-orden.md](../ai/turnstile-mata-el-analisis-de-orden.md)
  — otro caso de un gate de producción real silenciando/rompiendo un flujo
  bajo test.
- [deploy/password-reset-email-via-mailgun.md](../deploy/password-reset-email-via-mailgun.md)
  — la cuenta Mailgun compartida cuyo envío real hay que evitar en tests.
