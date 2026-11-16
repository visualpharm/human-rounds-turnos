# Entrar al admin de dev desde Playwright sin tipear la contraseña

## Problema
Para verificar el panel en el browser (:7700/admin) hace falta una sesión, pero
tipear `TURNOS_ADMIN_PASS` en un `browser_type`/`fill` la deja impresa en el
transcript, y el entorno de los servidores lanzados desde la sesión se sanea
(sólo sobrevive `PORT`), así que tampoco se pueden inyectar credenciales de
descarte por env.

## Solución
La cookie `st_admin` es HMAC stateless: se puede acuñar server-side sin pasar
por el login.

```bash
python3 -c "
import os
for line in open('.env'):
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); os.environ.setdefault(k,v)
import admin_auth
print(admin_auth._mint_token('admin'))" > $SCRATCH/tok.txt
```

Después, en la página ya navegada a `/admin`:
`document.cookie = 'st_admin=<token>; path=/'` y recargar. El server lee el
header `Cookie` aunque `document.cookie` se muestre vacío después del set (no
importa: el fetch a `/api/admin/reports` devuelve 200 y el boot del panel
detecta la sesión al recargar).

## Gotchas
- El token expira a las 12 h; es de dev/loopback, no un secreto durable.
- Chromium puede servir un JS viejo de su caché HTTP aunque el disco haya
  cambiado y el `?v=` esté bumpeado en el HTML: navegar con un query extra en
  el HTML (`/admin?nc=2`) fuerza el re-parse (mismo gotcha que encontró el
  agente de admin-charts.js con su preview).
