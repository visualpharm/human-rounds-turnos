# Mi Salud Digital PBA — turnos API (reverse-engineered, live data pull)

**Solved 2026-07-09.** The Provincia de Buenos Aires public-health reservation
system that backs Pinamar's real centros is **Mi Salud Digital**
(`https://misaluddigital.ms.gba.gov.ar`). Its `/api/*` is a real JSON API we can
read with a logged-in user's token. Sana Turnos' Pinamar centros (DIABETPIN, CAPS
Ostende/Valeria/Pinamar) are the *same* institutions registered here — so this API
is the authoritative source for "dónde + cuándo" and could back Sana Turnos directly.

## Auth
- Login flow is AFIP/RENAPER → `sso.gba.gob.ar` → sets cookie **`k`** on
  `misaluddigital.ms.gba.gov.ar`. `k` is a JWT (`sub` = DNI+uuid, `exp` = iat+8h).
- API auth = **`Authorization: Bearer <k>`** header. The cookie alone → 401.
- To pull a live session off this Mac: decrypt Chrome's `k` cookie.
  macOS Chrome v10 cookies = AES-128-CBC, key = `PBKDF2-HMAC-SHA1(security
  find-generic-password -w -s "Chrome Safe Storage", salt=b"saltysalt", 1003, 16)`,
  iv = 16 spaces, strip PKCS7 (+ possible 32-byte domain-hash prefix on newer Chrome).

## Response/request encryption (the non-obvious part)
Every `/api/*` body is **AES-192-CBC + base64**, key/iv hardcoded in their SPA
(`main.*.js`, module 8411 / env module 2340):
- key (`keyRequest`) = `T313med1cin4fortestingpl` (24 bytes → AES-192)
- iv = `0123456789abcdef`, mode CBC, PKCS7
- Response: base64 → AES-decrypt → JSON. Request POST bodies: JSON → AES-encrypt →
  base64 → send as `text/plain`.
- GET responses come back as `responseType:"text"` (the base64 blob). Decrypt to read.

## Endpoint map (all GET unless noted; all responses encrypted)
- `/api/jurisdiccion/provincia` → provinces `[{id,nombre,bahraProvincia}]`
- `/api/turno/partidos/{idProvincia}` → **all** partidos (Pinamar = **id 60**, prov 1)
- `/api/reserva-turno/obtener/instituciones/partido/{idPartido}` → centros
  `[{id,nombre,online,direccion,localidad{...}}]`. Pinamar(60): 924 DIABETPIN,
  926 CAPS OSTENDE, 927 CAPS VALERIA DEL MAR, 928 CAPS DE PINAMAR (all online:true).
- `/api/reserva-turno/obtener/especialidad/institucion/{idInst}` → especialidades
  `[{id, especialidad{id,codigo,nombre}, institucion{...,idHsi}}]`. DIABETPIN(924):
  Nutrición(codigo 145), Diabetología(326), Endocrinología(134).
- **Slots:** `/api/reserva-turno/obtener/turno-disponible/{idNodo}/partido/{idPartido}/especialidad/{codigoEspecialidad}?page={n-1}&size={m}`
  (idNodo from JWT = 1). Returns paginated `data.content[]`:
  `{diaryId, day:"YYYY-MM-DD", hour:"HH:MM", openingHoursId, referralOrderRequired,
    profesional, restrictions:[{from,to,unit:"Años"}], institucion{id,idHsi,nombre,localidad,partido,direccion}}`.
  **404 = no slots (SPA treats NOT_FOUND as `[]`).**
- Book (IRREVERSIBLE, do not call casually): `POST /api/reserva-turno/reservar/{a}/{b}/{c}` (multipart w/ order file).

## Key finding for Pinamar
- La Plata(27) Clínica médica(128) → **714 real bookable slots** live → pipeline proven.
- **Pinamar(60) returns 404 for all its especialidades → no online-bookable diaries
  open right now.** Its centros are registered but only take request-and-confirm
  turnos (matches the "solicitud con orden / según agenda" model Sana Turnos already
  uses). If/when Pinamar opens online diaries, this endpoint lights up automatically —
  Sana Turnos' "elegí horario" step should read `turno-disponible` and fall back to
  request-and-confirm on 404.

## Repro / live pull
`tools/msd_pull.py` (zero AI tokens) does the whole thing: **self-refreshes** the
`k` JWT straight from the logged-in Chrome cookie DB each run (Keychain "Chrome
Safe Storage" → AES-128), Bearer-auths, decrypts AES-192 responses (unwrap
`{httpStatusCode,message,data}`), walks Pinamar's 4 centros → especialidades →
`turno-disponible`, and writes `data/msd_turnos.json` (doctors + next turnos).
`--partido 27` = La Plata. 401/403 → exit 3, keeps the last good dump. Token still
expires ~8h after login, so it needs a live Chrome session to refresh.

## Wired into Sana Turnos (2026-07-09)
- **Auto-refresh:** LaunchAgent `com.sana-turnos.msd-refresh` (StartInterval 1800s)
  runs `tools/msd_refresh.sh` → `msd_pull.py`. Logs to `logs/msd-refresh.log`.
- **Server bridge:** `msd_data.py:turnos_for_servicio(servicio)` maps a Sana servicio
  (centro slug → MSD instituto id in `SLUG_TO_MSD`; nombre → especialidad, accent-
  folded) to live slots. Endpoint `GET /api/servicio-turnos/{id}` (hospital.py) →
  `{}` when no online agenda.
- **UI:** servicio.js (`?v=24`) `Wizard(host, {mode})`. **orden mode** (solicitud-
  con-orden services): order → "Cuándo" → account → confirm; the "Cuándo" step shows
  real slot chips (`renderSlots`) or the franja fallback (`stepFranja`). **turno mode**
  (walk-in service that HAS live slots): 3 steps (Cuándo → cuenta → confirm), NO order
  — the mount fetches `/api/servicio-turnos/{id}` and, if slots exist, upgrades the
  walk-in page into a bookable turno (the backend `_create` accepts a no-order
  solicitud). Step headers key by name (`stepHeader('horario'|...)`) so the count
  adapts. The irreversible MSD `reservar/` endpoint is never called — reception
  confirms via Sana's solicitud queue.
- **Name gap:** MSD's online agendas are for *Medicina general / Diabetología /
  Endocrinología / Ortopedia-Traumatología / Pediatría / Neonatología* — which Sana
  models as walk-in or doesn't list; Sana's order-based services (Cardiología…) have
  no MSD agenda. `_ALIAS` in msd_data.py bridges the naming (strips "(demanda
  espontánea)" etc.). Today CAPS Pinamar · Medicina general (Bertolotti + 3) lights up.
- **Correctness:** slot endpoint keys on `especialidad.id` (NOT `codigo` — codigo
  collides: Cardiología.codigo 131 == Pediatría.id 131). Query is partido-wide →
  filter by `institucion.id` per centro. Pinamar: 14 médicos / 269 turnos.
