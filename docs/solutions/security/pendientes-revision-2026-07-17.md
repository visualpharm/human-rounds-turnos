---
title: Revisión de seguridad 2026-07-17 — hallazgos NO corregidos (pendientes)
scope: repo
area: security
tags: [security, backlog, rate-limit, host-header, brute-force]
created: 2026-07-17
refs: [cuentas.py, notificaciones.py, admin_auth.py, turnstile.py]
status: active
---

## Contexto

Revisión de seguridad completa del 2026-07-17. Lo **CRÍTICO** ya está arreglado
y verificado (ver `static-fallback-servia-fuente-y-phi.md`): exposición de
código/PHI por el fallback estático, IDOR de `/api/requests?dni=`, y el
amplificador de XSS almacenado en las órdenes.

Estos hallazgos quedaron SIN corregir a propósito — o tocan archivos que otra
sesión estaba editando en paralelo (`cuentas.py`), o necesitan una decisión de
config (dominio canónico, IPs de proxy). Todos están **mitigados hoy** por el
basic-auth de prod (`site_auth.py`), pero hay que resolverlos antes de abrir a
pacientes.

## HIGH

1. **Login de paciente sin throttle + contraseña mínima de 4 caracteres**
   (`cuentas.py:_login`, `_registro`). El admin login sí tiene throttle
   (`admin_auth._throttled`, 8/5min); el de paciente sólo tiene Turnstile (que
   está apagado en dev y no limita intentos por cuenta). DNI es público y
   enumerable → credential stuffing sin freno server-side.
   **Fix:** portar el sliding-window IP+DNI de `admin_auth.py` a `_login`; subir
   el mínimo de contraseña a 8. (Archivo lo tenía la sesión hermana — coordinar.)

2. **`Host` header sin allowlist en links que se mandan por email**
   (`cuentas._base_url`, `notificaciones.py`, `mcp_api.py`, `medico_turnos.py`).
   El reset de contraseña (`/cuenta/reset?token=`) y la oferta de lista de espera
   (`/appointment/offer/<token>`) arman la URL con `handler.headers['Host']` sin
   validar. Si el origen acepta un `Host` arbitrario (p.ej. alcanzable saltando
   Cloudflare), un atacante dispara el reset de la víctima y el email real le
   llega con un link válido apuntando al dominio del atacante → robo del token.
   **Fix:** una env `TURNOS_BASE_URL` canónica para TODOS los links salientes,
   nunca confiar en `Host`. (Toca `cuentas.py` — coordinar con la sesión hermana.)

## MEDIUM

3. **`X-Forwarded-For` confiado sin cadena de proxy** (`admin_auth._client_ip`,
   `turnstile.client_ip`/`abuso.client_ip`, `reservas`). Todos los rate limits
   (throttle de admin login, abuso de IA 10/h·30/día, holds 120/h) se keyean por
   XFF. Si el origen es alcanzable directo, el atacante manda un XFF distinto por
   request y resetea todos los contadores.
   **Fix:** honrar XFF sólo cuando el peer inmediato es una IP de Cloudflare/Coolify
   conocida; si no, usar `client_address`.

4. **Órdenes médicas sin sniff de contenido** — YA MITIGADO parcialmente por el
   fix de `_ext_for` (magic bytes + allowlist de extensión), pero el `mime` que
   se guarda en la DB y se sirve en `/api/admin/order/<id>` todavía sale del
   cliente. Confirmar que el handler del admin fija el Content-Type por la
   extensión real, no por el mime declarado.

## LOW / INFO

5. **Enumeración de cuentas**: `POST /api/account/register` responde
   `409 dni_registrado` → se puede saber qué DNIs tienen cuenta. DNI no es muy
   secreto; bajo impacto.
6. **Race no atómica en holds** (`reservas.intentar_tomar`): chequeo previo no
   atómico antes del INSERT..ON CONFLICT. Ventana mínima; aprobar es una acción
   manual lenta. Bajo riesgo.
7. **`str(e)` reflejado** en algunos endpoints admin-gated (`solicitudes`,
   `calendar_busy`). Todos detrás de `require_admin`. Baja sensibilidad.

## Lo que se revisó y está LIMPIO

SQL 100% parametrizado; XSS server/cliente pasa por `esc()`/`html.escape`;
admin auth (HMAC + `compare_digest` + TOTP RFC6238 + throttle); scrypt con salt
por usuario; tokens `secrets.token_urlsafe` (128-256 bits); OTP hasheado con
tope de intentos; IDOR en `/api/requests/<id>`/cancel/waitlist correcto
(dueño por sesión, 404 en ajeno); CSRF (todo POST/DELETE, cookies SameSite=Lax);
sin secretos hardcodeados en el repo; CSV de reportes sin PII (iniciales).
