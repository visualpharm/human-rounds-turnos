
CREATE TABLE IF NOT EXISTS ai_register_changes (
    id bigserial PRIMARY KEY,
    function_id text NOT NULL,
    kind text NOT NULL,
    actor text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    substantial boolean NOT NULL DEFAULT false,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL CHECK (status IN ('recorded', 'pending', 'notified')),
    notification_reference text,
    notified_on date,
    notified_by text,
    notified_at timestamptz
);


-- Anonymous opt-in demo research (0020_real_study; follows 0019_ai_register_changes).
CREATE TABLE IF NOT EXISTS study_sessions (
    id bigserial PRIMARY KEY, token_hash text NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('patient','doctor','admin')),
    locale text NOT NULL CHECK (locale IN ('es','en')),
    consent_at timestamptz NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    user_agent_class text NOT NULL CHECK (user_agent_class IN ('mobile','tablet','desktop'))
);
CREATE INDEX IF NOT EXISTS study_sessions_retention ON study_sessions(started_at);
CREATE TABLE IF NOT EXISTS study_task_results (
    id bigserial PRIMARY KEY, session_id bigint NOT NULL,
    task_id text NOT NULL, outcome text NOT NULL CHECK (outcome IN ('ok','doubt','stuck','skipped')),
    ease integer CHECK (ease BETWEEN 1 AND 5), comment text NOT NULL DEFAULT '',
    duration_ms integer NOT NULL CHECK (duration_ms BETWEEN 0 AND 86400000),
    started_at timestamptz NOT NULL, ended_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id,task_id)
);
CREATE TABLE IF NOT EXISTS study_events (
    id bigserial PRIMARY KEY, session_id bigint NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    task_id text NOT NULL, t integer NOT NULL CHECK (t BETWEEN 0 AND 86400000),
    kind text NOT NULL CHECK (kind IN ('click','nav','dead_click','scroll_stop','visibility')),
    target jsonb NOT NULL DEFAULT '{}'::jsonb, path text NOT NULL
);
CREATE INDEX IF NOT EXISTS study_events_session ON study_events(session_id,task_id);
CREATE TABLE IF NOT EXISTS study_findings (
    id bigserial PRIMARY KEY, title text NOT NULL, detail text NOT NULL DEFAULT '',
    role text NOT NULL CHECK (role IN ('patient','doctor','admin')),
    task_id text NOT NULL, kind text,
    severity text NOT NULL CHECK (severity IN ('low','medium','high')),
    source text NOT NULL CHECK (source IN ('auto','manual')),
    evidence_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'found' CHECK (status IN ('found','in_progress','fixed','not_done','dismissed')),
    fix_note text NOT NULL DEFAULT '', commit text NOT NULL DEFAULT '', live_url text NOT NULL DEFAULT '',
    decided_by text, decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS study_findings_auto ON study_findings(task_id,kind) WHERE source='auto';
CREATE TABLE IF NOT EXISTS booking_requests (
    id                  serial PRIMARY KEY,
    dni                 text,
    first_name          text,
    last_name           text,
    birth_date          date,
    sex                 text,
    contact             text,
    service_id          text,
    center_slug         text,
    status              text DEFAULT 'pendiente',
    referral_path       text,
    referral_mime       text,
    ai_json             jsonb,
    rejection_reason    text,
    appointment_at      timestamptz,
    hsi_patient_id      text,
    hsi_appointment_id  text,
    hsi_status          text,
    attendance          text CHECK (attendance IN ('presente','ausente')),
    attendance_updated_at timestamptz,
    attendance_updated_by text,
    overbooking         boolean NOT NULL DEFAULT false,
    created             timestamptz DEFAULT now()
);
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS sex text;
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS time_preference text;
-- Quién hizo la gestión. `dni` sigue siendo SIEMPRE el paciente (adulto o
-- menor); `requester_dni` permite que el adulto vea y audite los turnos de
-- sus hijos sin mezclar identidades clínicas.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS requester_dni text;
UPDATE booking_requests SET requester_dni=dni WHERE requester_dni IS NULL;
-- El operador puede reasignar el servicio y anotar el médico al approve.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS assigned_doctor text;
-- El médico de una solicitud es UN par (2026-10-23, problems.md №3): la
-- fuente de verdad es `assigned_doctor_id`; `assigned_doctor` es el nombre
-- del catálogo para ese id (doctor_appointments.resolve_pair en TODA
-- escritura; renombrar al médico en el catálogo lo actualiza acá). Sólo un
-- nombre que no resuelve a UN médico (fuera del catálogo, ambiguo, de HSI)
-- queda con id NULL — nunca con el id de otro. Las lecturas que identifican
-- al médico van por id; el nombre es compat sólo para filas sin id
-- (históricas: scripts/medico_por_id.py completa las que resuelven).
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS assigned_doctor_id text;
-- Cuándo se mandó (o se intentó mandar) el recordatorio del día antes
-- (reminders.py). NULL = todavía no se barrió; se marca aunque el email
-- no salga (sin cuenta con email, Mailgun caído) para no re-escanear la fila
-- en cada tick.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS reminder_at timestamptz;
-- Mismo patrón que reminder_at de arriba, pero para el SEGUNDO mail del día
-- antes: la invitación a la entrevista previa (deliverable 3, 2026-08-01,
-- notifications.notify_interview_reminder / reminders.tick_interview).
-- Independiente de reminder_at a propósito: una instalación puede tener
-- prendido un recordatorio y apagado el otro. NULL = todavía no se barrió;
-- se marca SIEMPRE que se procesa la fila (módulo intake apagado, entrevista
-- ya completada, o sin email) para no re-escanearla en cada tick.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS interview_reminder_at timestamptz;
-- Auditoría de "quién resolvió qué": el usuario admin/operador que aprobó o
-- rechazó (login real, ver admin_auth.py/admin_users.py). Vacío en
-- solicitudes resueltas antes de que existiera este campo.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS resolved_by text;
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
-- El sobreturno es una decisión explícita del personal. No se infiere de dos
-- filas que coincidan: queda persistido para que la cola y la auditoría HSI
-- distingan una excepción autorizada de una doble reserva accidental.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS overbooking boolean NOT NULL DEFAULT false;
-- La marca vigente es cómoda para la operación diaria; el historial completo
-- queda en attendance_events para que corregir una ausencia no borre quién la
-- registró ni cuándo.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS attendance text
    CHECK (attendance IN ('presente','ausente'));
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS attendance_updated_at timestamptz;
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS attendance_updated_by text;
-- Respuesta del paciente al recordatorio del día previo (reunión hospital
-- 2026-07-24). Es lo que el paciente DIJO que iba a hacer; `attendance` es lo
-- que pasó. Son dos cosas distintas y se guardan aparte: quien confirmó y no
-- vino es justamente el caso que interesa medir.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS confirmation text
    CHECK (confirmation IN ('si','no'));
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS confirmation_at timestamptz;
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS confirmation_channel text;
-- El token del link de un clic del recordatorio por email (2026-08-02). Hasta
-- hoy el "¿vas a poder venir?" sólo salía por WhatsApp, y el número
-- institucional de Meta sigue en trámite: sin esto nadie confirma nada. El
-- token ES la autenticación del link (mismo modelo que appointment_offers.token
-- y interviews.patient_token) porque el paciente que abre el mail en el
-- teléfono no tiene sesión, y pedirle que se loguee para decir "sí" es
-- exactamente la fricción que hace que no conteste. Se crea recién cuando se
-- manda el recordatorio, no en el alta.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS confirm_token text;
CREATE UNIQUE INDEX IF NOT EXISTS booking_requests_confirm_token_idx
    ON booking_requests (confirm_token);
-- Cuándo canceló el paciente. `status='cancelada'` sólo dice QUE canceló, y
-- para el ranking de asistencia (patient_ranking.py) la diferencia entre
-- avisar con tres días y avisar con veinte minutos es toda la diferencia: en
-- un caso la lista de espera recupera el lugar, en el otro se pierde igual
-- que una ausencia. Las filas anteriores quedan NULL y el ranking las trata
-- como "canceló, sin saber cuándo" — a favor del paciente, nunca inventando
-- un aviso tardío que no consta.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS cancelled_at timestamptz;
-- El médico del BLOQUE de agenda que el paciente eligió en el wizard
-- (id del catálogo, 2026-08-01). NO es una asignación (esa sigue siendo
-- assigned_doctor/_id al aprobar): sólo decide si se ofrece la entrevista
-- previa antes de la aprobación — el override por médico puede estar ON con
-- el default del sistema OFF, y sin esto la solicitud pendiente no tiene
-- ningún médico que consultar.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS slot_doctor_id text;
-- Clave de idempotencia del POST propio del paciente (2026-08-05, data-integrity
-- guards): el wizard manda el `hold_token` que ya tenía como `idem_key`, y
-- `db.create_booking_request_with_limit` hace el insert con
-- `ON CONFLICT (idem_key) DO NOTHING` — un doble tap o un retry tras timeout
-- en Confirmá nunca crea una segunda solicitud. NULL para todo lo demás
-- (front desk, clientes viejos sin token): el índice de abajo es parcial y
-- los deja afuera, sin dedup ahí.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS idem_key text;
-- «Contanos tu caso» (Jorge, 2026-10-17): cuando un servicio no tiene turnos
-- libres, el paciente describe por qué no puede esperar y el hospital lo
-- contacta para evaluarlo. Texto libre del paciente, acotado en el POST; lo
-- lee la mesa en la cola. Nunca viaja a una IA.
ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS patient_note text;
CREATE UNIQUE INDEX IF NOT EXISTS booking_requests_idem_key_uniq
    ON booking_requests (idem_key) WHERE idem_key IS NOT NULL;
-- El guard real contra doble reserva (2026-08-05): un (médico, horario)
-- aprobado sólo puede tener UNA fila que no sea sobreturno (`overbooking`) a
-- la vez. `db.approve` confía en el pre-check `_doctor_appointment_busy` de
-- booking_requests.py para la UX, pero el que de verdad cierra la carrera de
-- dos approve simultáneos es este índice — `db.approve` atrapa su
-- unique-violation y la traduce a `SlotConflict`. La migración
-- 005_booking_integrity backfillea los duplicados que YA existían en prod
-- (marcándolos `overbooking=true`) ANTES de que este CREATE INDEX corra —
-- that is why it lives in `_SCHEMA_SQL` (which, in the only database shape
-- that still needs that backfill — the partial legacy bridge — runs AFTER
-- the numbered migrations) and not in the migration itself: on a freshly
-- created database this index is created directly on the empty table, with
-- nothing to backfill.
CREATE UNIQUE INDEX IF NOT EXISTS booking_requests_doctor_slot_uniq
    ON booking_requests (assigned_doctor_id, appointment_at)
    WHERE status = 'aprobada' AND NOT overbooking
      AND assigned_doctor_id IS NOT NULL AND appointment_at IS NOT NULL;

-- Índices de booking_requests (2026-07-22, auditoría de queries). `booking_requests`
-- es la única tabla que crece sin techo y la más filtrada de la app; sin estos
-- índices cada endpoint era un seq scan del histórico completo.
--   status+created  → cola admin (list_admin) y todos los reportes_* (rango created)
--   status+appointment_at → aprobadas futuras, HSI pendientes, recordatorios, colisión de médico
--   dni / requester_dni → "Mis turnos" (WHERE dni=%s OR requester_dni=%s → BitmapOr)
--   hsi_appointment_id (parcial) → NOT EXISTS correlado en hsi_appointments_by_dni
CREATE INDEX IF NOT EXISTS booking_requests_status_created_idx ON booking_requests (status, created DESC);
CREATE INDEX IF NOT EXISTS booking_requests_status_appointment_idx ON booking_requests (status, appointment_at);
CREATE INDEX IF NOT EXISTS booking_requests_dni_idx ON booking_requests (dni);
CREATE INDEX IF NOT EXISTS booking_requests_requester_idx ON booking_requests (requester_dni);
CREATE INDEX IF NOT EXISTS booking_requests_hsi_appt_idx ON booking_requests (hsi_appointment_id)
    WHERE hsi_appointment_id IS NOT NULL;
-- Soporta `db.list_approved_future_for_doctor` (2026-08-06 auditoría): el
-- feed ICS/"Mis turnos" de UN médico ya no hace SELECT * hospital-wide, así
-- que el WHERE assigned_doctor_id=%s necesita su propio índice — el parcial
-- `booking_requests_doctor_slot_uniq` de arriba no sirve para esto (excluye
-- sobreturnos, y sólo cubre `status='aprobada'`, que acá también aplicamos
-- pero como filtro adicional, no como parte del índice).
CREATE INDEX IF NOT EXISTS booking_requests_doctor_appointment_idx
    ON booking_requests (assigned_doctor_id, appointment_at)
    WHERE assigned_doctor_id IS NOT NULL AND appointment_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS attendance_events (
    id             bigserial PRIMARY KEY,
    request_id     integer NOT NULL,
    dni            text NOT NULL,
    attendance_from text CHECK (attendance_from IN ('presente','ausente')),
    attendance_to  text NOT NULL CHECK (attendance_to IN ('presente','ausente')),
    operator       text NOT NULL,
    source         text NOT NULL DEFAULT 'sana' CHECK (source IN ('sana','hsi')),
    created        timestamptz NOT NULL DEFAULT now()
);
-- Los tests y el mantenimiento truncan ``booking_requests`` de forma aislada. La
-- bitácora se conserva por id, pero no agrega una FK que convierta ese reset
-- de bajo riesgo en una operación con CASCADE.
ALTER TABLE attendance_events DROP CONSTRAINT IF EXISTS asistencia_eventos_solicitud_id_fkey;
CREATE INDEX IF NOT EXISTS attendance_events_dni_idx ON attendance_events (dni, created DESC);
CREATE INDEX IF NOT EXISTS attendance_events_request_idx ON attendance_events (request_id, created DESC);

-- Historia de cada turno (verificación formal 2026-09-24, AppointmentHsi.tla
-- AuditReconstructable): UNA fila por cada cambio de estado, de horario o de
-- asistencia de una solicitud, escrita en la MISMA transacción que el cambio
-- (`_log_appointment_event`). Con esto se reconstruye quién hizo qué y cuándo
-- — antes `reassign_appointment` y `cancel` no dejaban rastro y
-- `resolved_by` se pisaba. `actor` usa la forma de `resolved_by`
-- ("mesa:<usuario>", "auto:web", "paciente", "hsi:sync"); nunca un DNI, así
-- que un cambio de DNI no tiene nada que mover acá. Sin FK por la misma razón
-- que attendance_events.
CREATE TABLE IF NOT EXISTS appointment_events (
    id              bigserial PRIMARY KEY,
    request_id      integer NOT NULL,
    kind            text NOT NULL,
    actor           text,
    status_from     text,
    status_to       text,
    appointment_from timestamptz,
    appointment_to  timestamptz,
    attendance_from text,
    attendance_to   text,
    created         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS appointment_events_request_idx ON appointment_events (request_id, id);

-- A non-clinical, durable invalidation log for the authenticated day queue.
-- This intentionally contains no patient identifier, attendance value, or
-- operator. It lets every ASGI worker replay an update after reconnecting;
-- pg_notify below is only the low-latency wake-up, never the source of truth.
CREATE TABLE IF NOT EXISTS day_queue_events (
    id                  bigserial PRIMARY KEY,
    appointment_day     date NOT NULL,
    center_slug         text,
    assigned_doctor_id  text,
    assigned_doctor     text,
    created             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS day_queue_events_replay_idx
    ON day_queue_events (id);

-- Roles y permisos del panel (R5/R6, 2026-09-10 — core/permissions.py).
-- `slug` es el valor que viaja en la cookie de sesión; `permissions` es la
-- lista de permisos del preset (el rol `admin` siempre tiene todos, lo impone
-- el código). Los roles de fábrica se desactivan, nunca se borran. Revisión
-- Alembic 0012 crea lo mismo en instalaciones existentes. El seed de los
-- roles de fábrica NO vive acá: este texto ES el cuerpo de la baseline de
-- Alembic y su probador de forma sólo entiende DDL, así que un INSERT lo
-- vuelve improbable. Lo siembra `_seed_builtin_roles()` después del
-- esquema, idempotente (ON CONFLICT DO NOTHING) y derivado de
-- permissions.BUILTIN_ROLES.
CREATE TABLE IF NOT EXISTS roles (
    slug         text PRIMARY KEY,
    name         text NOT NULL,
    description  text NOT NULL DEFAULT '',
    builtin      boolean NOT NULL DEFAULT false,
    enabled      boolean NOT NULL DEFAULT true,
    permissions  text[] NOT NULL DEFAULT '{}',
    created      timestamptz NOT NULL DEFAULT now(),
    updated      timestamptz NOT NULL DEFAULT now()
);

-- Operadores del panel admin (además del dueño, que loguea por env — ver
-- admin_auth.py). `role`: 'admin' = todo (contenido+usuarios+solicitudes);
-- 'operador' = sólo la cola de solicitudes. `active=false` = desactivado —
-- NUNCA se borra en duro: `booking_requests.resolved_by` sigue apuntando al
-- usuario aunque se le quite el acceso. Password = mismo esquema Argon2id que
-- `users` (pacientes); `totp_secret` es 2FA opcional POR CUENTA (a
-- diferencia del dueño, que usa TURNOS_ADMIN_TOTP_SECRET del env).
CREATE TABLE IF NOT EXISTS operators (
    id           serial PRIMARY KEY,
    username     text UNIQUE NOT NULL,
    pass_hash    text NOT NULL,
    totp_secret  text,
    role         text NOT NULL DEFAULT 'medico' CHECK (role IN ('admin','operador')),
    active       boolean NOT NULL DEFAULT true,
    created      timestamptz DEFAULT now()
);
-- Cuentas de médico (2026-07-16, doctor_appointments.py): un rol nuevo que sólo ve
-- sus propios turnos aprobados. `doctor_id` liga la cuenta al catálogo de
-- médicos (webdata/medicos.json + overlay catalog.py); admin_users.py
-- exige que exista ahí antes de crear/editar una cuenta con rol 'medico'.
-- `feed_token` es el mismo patrón lazy-get-or-create que users.feed_token
-- (calendar_feed.py), para el feed ICS propio del médico.
-- El CHECK de `role` hay que dropearlo/recrearlo (Postgres no tiene
-- "ALTER CHECK IF NOT MATCHES"): DROP+ADD es idempotente porque el nombre
-- del constraint es fijo (el que Postgres autogeneró al crear la tabla).
ALTER TABLE operators ADD COLUMN IF NOT EXISTS doctor_id text;
ALTER TABLE operators ADD COLUMN IF NOT EXISTS onboarding_completed_at timestamptz;
ALTER TABLE operators ADD COLUMN IF NOT EXISTS feed_token text UNIQUE;
ALTER TABLE operators ADD COLUMN IF NOT EXISTS last_access timestamptz;
ALTER TABLE operators ADD COLUMN IF NOT EXISTS email text;
-- Médico = usuario (Ivan, 2026-10-19): every doctor has exactly one account.
-- `invited_at` separates «sin invitar» from «invitado»; the partial unique
-- index makes «one doctor, one account» a database rule (Alembic 0022).
ALTER TABLE operators ADD COLUMN IF NOT EXISTS invited_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS operators_doctor_id_uniq
    ON operators (doctor_id) WHERE doctor_id IS NOT NULL;
-- Staff login through the municipal portal (auth/staff_portal.py): at the
-- first portal login (matched by email) the account records the portal's
-- immutable account id (`X-User-Id`) and its validated CUIT (eleven digits);
-- from then on it is found only by the account id. One portal account, one
-- operator; one person, one operator (Alembic 0023, 0024).
ALTER TABLE operators ADD COLUMN IF NOT EXISTS cuit text;
CREATE UNIQUE INDEX IF NOT EXISTS operators_cuit_uniq
    ON operators (cuit) WHERE cuit IS NOT NULL;
ALTER TABLE operators ADD COLUMN IF NOT EXISTS portal_user_id text;
CREATE UNIQUE INDEX IF NOT EXISTS operators_portal_user_id_uniq
    ON operators (portal_user_id) WHERE portal_user_id IS NOT NULL;
-- OJO: este CHECK tiene que listar TODOS los roles de admin_users._ROLES.
-- 'admisionista' (2026-07-24) se agregó a _ROLES pero no acá, y crear esa
-- cuenta reventaba la conexión antes de llegar a validar nada. Al sumar un rol
-- nuevo, se suma en los dos lados.
-- Ambos DROP cubren tanto el nombre legado (tablas migradas desde `operadores`,
-- donde ALTER TABLE ... RENAME TO no renombra el constraint) como el nombre
-- nuevo (reruns de este script sobre una base ya en inglés).
ALTER TABLE operators DROP CONSTRAINT IF EXISTS operadores_rol_check;
ALTER TABLE operators DROP CONSTRAINT IF EXISTS operators_role_check;
UPDATE operators SET role='admisionista' WHERE role='operador';
UPDATE operators SET role='admin' WHERE role='marca';
ALTER TABLE operators ADD CONSTRAINT operators_role_check
    CHECK (role IN ('admin','medico','admisionista','reportes'));
-- Alcance por centro de salud (reunión hospital 2026-07-24, Gabriel: la mesa
-- de admisión de un CAPS no tiene por qué ver la jornada de los otros). NULL o
-- vacío = todos los centros, que es el comportamiento de siempre y el de una
-- cuenta admin; una lista acota la cola del día y el alta de turnos a esos
-- slugs. Se guarda como array nativo: son 10 centros, no una entidad.
ALTER TABLE operators ADD COLUMN IF NOT EXISTS centers text[];

-- Acceso por cuenta (R6, 2026-09-10 — core/permissions.py): el rol
-- de la cuenta cuando no es uno de los cuatro que admite operators.role (su
-- CHECK queda congelado: lo usa la prueba de forma pre-Alembic), más los
-- permisos agregados/quitados a esa persona sobre su rol. Efectivo = preset
-- del rol + granted - denied. Revisión Alembic 0012.
CREATE TABLE IF NOT EXISTS operator_username_history (
    old_username text PRIMARY KEY,
    new_username text NOT NULL,
    operator_id integer NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operator_access (
    username     text PRIMARY KEY,
    role_slug    text,
    granted      text[] NOT NULL DEFAULT '{}',
    denied       text[] NOT NULL DEFAULT '{}',
    updated      timestamptz NOT NULL DEFAULT now()
);

-- Preferencias del panel por usuario. Los filtros, columnas visibles y anchos
-- siguen a la persona entre equipos; no quedan atados a un navegador.
CREATE TABLE IF NOT EXISTS admin_preferences (
    username    text NOT NULL,
    key         text NOT NULL,
    value       jsonb NOT NULL,
    updated     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (username, key)
);

-- Los restablecimientos de contraseña de usuarios internos son de un solo uso.
-- Sólo se guarda el hash del token enviado por email.
CREATE TABLE IF NOT EXISTS admin_reset_tokens (
    token_hash  text PRIMARY KEY,
    username    text NOT NULL,
    expires     timestamptz NOT NULL,
    used        boolean NOT NULL DEFAULT false,
    created     timestamptz NOT NULL DEFAULT now()
);

-- Seguridad de la cuenta administrativa actual. Los operadores conservan
-- contraseña/2FA en `operators`; esta tabla guarda el plazo común para
-- activar 2FA y, sólo para el dueño de emergencia, overrides editables desde
-- el panel. Si Postgres cae, admin_auth.py sigue aceptando las credenciales
-- del env como acceso break-glass.
CREATE TABLE IF NOT EXISTS admin_account_security (
    username             text PRIMARY KEY,
    owner_pass_hash      text,
    owner_totp_secret    text,
    owner_totp_set       boolean NOT NULL DEFAULT false,
    totp_deadline        timestamptz,
    updated              timestamptz NOT NULL DEFAULT now()
);

-- El dueño no tiene fila en `operators`, así que su email y su última visita
-- viven acá (2026-07-27): sin email no hay recuperación de contraseña ni aviso
-- que le llegue, y la lista de Usuarios lo mostraba eternamente en «—».
ALTER TABLE admin_account_security ADD COLUMN IF NOT EXISTS owner_email text;
ALTER TABLE admin_account_security ADD COLUMN IF NOT EXISTS owner_last_access timestamptz;

-- Passkeys del panel (WebAuthn, 2026-07-27). Una fila por llave registrada:
-- una misma persona suele tener varias (la del teléfono, la del navegador de
-- la mesa de entradas). `username` es el mismo string que la cookie de sesión
-- —el dueño del env NO vive en `operators`, así que la clave es el nombre,
-- no un id de esa tabla—. `public_key` guarda la clave pública ya parseada de
-- COSE a JSON; el secreto nunca sale del dispositivo, así que acá no hay nada
-- que proteger más allá de la integridad. `sign_count` sirve para detectar un
-- autenticador clonado (muchas passkeys reportan siempre 0: sólo se compara
-- cuando de verdad cuenta).
CREATE TABLE IF NOT EXISTS passkeys (
    id            serial PRIMARY KEY,
    username      text NOT NULL,
    credential_id text UNIQUE NOT NULL,
    public_key    jsonb NOT NULL,
    sign_count    bigint NOT NULL DEFAULT 0,
    name          text,
    created       timestamptz NOT NULL DEFAULT now(),
    last_used     timestamptz
);
CREATE INDEX IF NOT EXISTS passkeys_username_idx ON passkeys (username);

-- WebAuthn challenges are stored only as SHA-256 digests.  The browser gets
-- the signed raw token; a database read must never recover a challenge that
-- can complete a ceremony.  `consumed_at` makes replay rejection durable
-- across ASGI processes and replicas.
CREATE TABLE IF NOT EXISTS passkey_challenges (
    challenge_digest bytea PRIMARY KEY,
    purpose          text NOT NULL CHECK (purpose IN ('registration', 'login')),
    username         text,
    expires_at       timestamptz NOT NULL,
    consumed_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS passkey_challenges_expiry_idx
    ON passkey_challenges (expires_at);

-- Cuentas de paciente (registro/login). Ligadas por DNI (misma clave que HSI).
CREATE TABLE IF NOT EXISTS users (
    id            serial PRIMARY KEY,
    dni           text UNIQUE NOT NULL,
    first_name    text,
    last_name     text,
    birth_date    date,
    sex           text,
    contact       text,
    email         text,
    pass_hash     text,
    created       timestamptz DEFAULT now(),
    identity_kind text NOT NULL DEFAULT 'dni'
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS email text;
-- Archivo de pacientes (2026-07-24, "entities could be created, edited or
-- archived"): active=false = cuenta archivada — no puede loguearse y sus
-- sesiones dejan de resolver; los datos y solicitudes históricas quedan.
ALTER TABLE users ADD COLUMN IF NOT EXISTS active boolean DEFAULT true;
ALTER TABLE users ADD COLUMN IF NOT EXISTS feed_token text UNIQUE;
-- Token del conector MCP del paciente (mcp_api.py): su asistente de IA lo
-- manda como Bearer y puede buscar servicios y pedir turnos EN SU NOMBRE.
-- Mismo patrón lazy-get-or-create que feed_token; regenerarlo lo revoca.
ALTER TABLE users ADD COLUMN IF NOT EXISTS mcp_token text UNIQUE;
-- Cobertura de salud del paciente: {"obra_social": {"nombre", "rnos", "origen"},
-- "prepaga": "..."}. origen 'puco' = confirmada contra el padrón SISA (puco.py)
-- server-side; 'declarada' = la escribió el paciente. Informativa — los turnos
-- del hospital público no dependen de esto.
ALTER TABLE users ADD COLUMN IF NOT EXISTS coverage jsonb;
-- Domicilio del paciente: {"calle","numero","piso","localidad","provincia",
-- "cp","texto"}. Se puede pre-llenar leyendo el DORSO del DNI (ocr_dni.py) o
-- escribir a mano. Informativo (el hospital lo usa para ubicar al paciente);
-- los turnos no dependen de esto.
ALTER TABLE users ADD COLUMN IF NOT EXISTS address jsonb;
-- Cómo quiere que le avisemos: {"whatsapp": bool, "email": bool, "sms": bool}.
-- null = defaults (whatsapp+email on, sms off). Email es el único canal que hoy
-- se envía de verdad (notifications.py); whatsapp/sms guardan la preferencia
-- para cuando existan esos canales.
ALTER TABLE users ADD COLUMN IF NOT EXISTS notifications jsonb;
-- Última vez que la ficha se tocó DESDE LA MESA (alta aparte: `created` ya
-- cubre eso; la edición propia del paciente, update_user, no la toca a
-- propósito — no es "contacto" en el sentido que le interesa a admisión).
-- Alimenta el orden de «Pacientes» cuando no hay búsqueda: reunión hospital
-- 2026-08-27, Jorge probando el panel como admisionista, "que le aparezcan
-- a pedido, no que aparezcan todos" — el default deja de ser el padrón
-- entero alfabético y pasa a ser CONTACTO RECIENTE (turno/solicitud reciente
-- o ficha editada hace poco). Ver users_admin_list(recientes=True) y
-- user_admin_update, que es quien la actualiza.
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated timestamptz;
UPDATE users SET updated = created WHERE updated IS NULL;
-- Idioma en el que le mandamos los emails/WhatsApp (notifications.py,
-- 2026-08-04): lo fija accounts.py al registrarse (el que el visitante ya
-- estaba viendo, `i18n.active()`) y se puede editar en Mi cuenta. NULL en
-- toda cuenta que ya existía antes de esta columna, o creada por la mesa de
-- admin sin request de por medio — notifications._destination_lang() cae al
-- idioma canónico en ese caso, el mismo comportamiento que ya tenían.
-- This ALTER lives HERE (not in migrations/) on purpose: it is the same
-- table `_SCHEMA_SQL` creates below, and the numbered migrations (today a
-- frozen bridge that only runs on a partial legacy schema) used to run
-- BEFORE this script — an ALTER TABLE users in migrations/ against a
-- freshly created database would fail (the table does not exist yet) or,
-- worse, would be marked "applied" without having touched anything and
-- would never retry. An ALTER ... IF NOT EXISTS here, like the rest of
-- the user columns, is always idempotent. (Under Alembic this script
-- runs via the baseline — add a new column on EXISTING databases with a
-- forward revision, not by editing only this line: the baseline does not
-- re-run.)
ALTER TABLE users ADD COLUMN IF NOT EXISTS lang text;
-- Qué ES la clave de la cuenta (Ivan, 2026-09-11): 'dni' = un documento
-- nacional real (default de TODA cuenta, incluidas las anteriores a esta
-- columna); 'synthetic' = clave "9" + 8 dígitos que el server acuñó al
-- registrarse sin DNI (visitante cuyo idioma activo no es el canónico —
-- el gate es el IDIOMA del visitante, no el país de la instalación). La
-- clave de login/HSI sigue siendo users.dni: esto sólo registra QUE NO es
-- un DNI real, para que ninguna pantalla se la muestre a la persona como
-- si lo fuera.
-- Identidad nacional (auth_renaper.py): claims crudos del último login con
-- RENAPER/Mi Argentina + timestamp. Acá vamos a mirar si el token trae
-- domicilio/fecha de emisión del DNI para la regla municipal de residencia.
-- Una cuenta creada por esta vía queda con pass_hash NULL (no valida password).
ALTER TABLE users ADD COLUMN IF NOT EXISTS renaper jsonb;
-- Patient login through the municipal portal (cuentas/portal_identity.py,
-- TURNOS_PATIENT_PORTAL_LOGIN): at the first portal request the account with
-- the DNI of the validated CUIT — found or created — records the portal's
-- immutable account id (`X-User-Id`); from then on it is found only by it.
-- One portal account, one patient (Alembic 0025).
ALTER TABLE users ADD COLUMN IF NOT EXISTS portal_user_id text;
CREATE UNIQUE INDEX IF NOT EXISTS users_portal_user_id_uniq
    ON users (portal_user_id) WHERE portal_user_id IS NOT NULL;
-- Menores/personas a cargo declarados por el titular de una cuenta. El DNI es
-- la identidad clínica estable que usa HSI; nombre+edad solos no alcanzan.
CREATE TABLE IF NOT EXISTS dependents (
    id                    serial PRIMARY KEY,
    adult_dni             text NOT NULL,
    dni                   text NOT NULL,
    first_name            text NOT NULL,
    last_name             text NOT NULL,
    birth_date            date NOT NULL,
    sex                   text NOT NULL,
    relationship          text NOT NULL,
    affidavit_at          timestamptz NOT NULL DEFAULT now(),
    created               timestamptz DEFAULT now(),
    updated               timestamptz DEFAULT now(),
    UNIQUE (adult_dni, dni)
);

-- Índices de la vuelta de performance (2026-07-28). Van ACÁ, después de
-- `users` y `dependents`: _SCHEMA_SQL se ejecuta como UN script de
-- arriba a abajo, así que un índice puesto antes de su tabla rompe el
-- esquema entero ("relation users does not exist"). Cubren consultas que
-- hoy son seq scan y que crecen con el padrón — no con el prototipo, donde
-- `users` tiene 25 filas y cualquier plan es instantáneo.

-- `user_by_phone` compara los ÚLTIMOS 10 DÍGITOS de `contact`
-- normalizado, así que ningún índice sobre la columna cruda sirve: hay que
-- indexar LA MISMA expresión. Corre en CADA mensaje entrante de WhatsApp
-- (whatsapp_chat.py), que es donde un scan se nota primero.
CREATE INDEX IF NOT EXISTS users_contact_tel_idx
    ON users (right(regexp_replace(contact, '\D', '', 'g'), 10))
    WHERE contact IS NOT NULL;

-- La lista de pacientes filtra por archivado/activo en cada carga.
CREATE INDEX IF NOT EXISTS users_active_idx ON users (active);

-- `dependents` ya tiene UNIQUE (adult_dni, dni), que resuelve "los chicos
-- de este adulto". La vuelta — `adults_of_dependent(dni)`, "de quién está
-- a cargo este chico", que la ficha del paciente pide siempre — filtra por la
-- SEGUNDA columna del índice compuesto y no lo puede usar.
CREATE INDEX IF NOT EXISTS dependents_dni_idx ON dependents (dni);

-- Sesiones de login (cookie opaca -> dni). Prototipo: sin expiración server-side.
CREATE TABLE IF NOT EXISTS sessions (
    token       text PRIMARY KEY,
    dni         text NOT NULL,
    created     timestamptz DEFAULT now(),
    last_seen   timestamptz DEFAULT now()
);
-- change_dni / invalidación de sesiones al cambiar password barren por dni.
CREATE INDEX IF NOT EXISTS sessions_dni_idx ON sessions (dni);
-- Tokens de recuperación de contraseña (link por email, un solo uso, 1h).
CREATE TABLE IF NOT EXISTS reset_tokens (
    token       text PRIMARY KEY,
    dni         text NOT NULL,
    expires     timestamptz NOT NULL,
    used        boolean DEFAULT false,
    created     timestamptz DEFAULT now()
);

-- Catálogo editable (overlay del admin sobre webdata/*.json): una fila por
-- (type, id) con el doc completo en jsonb. `type` in ('centros','servicios',
-- 'medicos'). `deleted` = soft-delete (para ids que existen en el JSON base:
-- ocultarlos sin perder el original, que vive en el archivo). catalog.py es
-- quien mergea esto con el JSON base; acá sólo se persiste el overlay.
-- También reutilizada, desde 2026-08-03, como key-value store llano por
-- connectors/renaper/renaper.py (`type='renaper_test_identity'`, sin JSON
-- base que mergear) — el registro de identidades de prueba del simulador
-- RENAPER, que antes vivía en un archivo que un redeploy de Coolify borra
-- (no hay volumen persistente montado en /app/data en prod).
CREATE TABLE IF NOT EXISTS catalog (
    type     text NOT NULL,
    id       text NOT NULL,
    doc      jsonb NOT NULL,
    deleted  boolean DEFAULT false,
    created  timestamptz DEFAULT now(),
    updated  timestamptz DEFAULT now(),
    PRIMARY KEY (type, id)
);
ALTER TABLE catalog ADD COLUMN IF NOT EXISTS created timestamptz DEFAULT now();
-- PostgreSQL rellena una columna nueva con su DEFAULT para las filas
-- existentes. Esas ediciones históricas terminaban con "creado" posterior a
-- "actualizado"; en ese caso la primera fecha conocida es justamente updated.
UPDATE catalog SET created = updated WHERE created > updated;

-- Códigos OTP (WhatsApp hoy vía WAHA, SMS pago mañana — ver otp_send.py) para
-- gating de registro y recuperación de contraseña alternativa a la del link
-- por email. Se guarda el HASH del código, nunca el código en texto plano.
-- Clave = contact (no dni): el registro pide verificar el teléfono ANTES de
-- que exista la fila en `users`.
CREATE TABLE IF NOT EXISTS otp_codes (
    id          serial PRIMARY KEY,
    contact     text NOT NULL,
    purpose     text NOT NULL,       -- 'registro' | 'recuperar'
    code_hash   text NOT NULL,
    attempts    int NOT NULL DEFAULT 0,
    used        boolean DEFAULT false,
    expires     timestamptz NOT NULL,
    created     timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS otp_codes_contact_idx ON otp_codes (contact, purpose, created DESC);
-- Ticket de "este contacto ya verificó un código" con vencimiento corto, para
-- que el cliente no tenga que reenviar el código en el POST de registro/reset
-- que sigue. Se consume (borra) al usarse — un solo uso.
CREATE TABLE IF NOT EXISTS otp_verified (
    contact           text NOT NULL,
    purpose           text NOT NULL,
    verified_until    timestamptz NOT NULL,
    PRIMARY KEY (contact, purpose)
);

-- Cache de verificación de matrícula vs. REFEPS (Buscador Nacional de
-- Profesionales de la Salud, argentina.gob.ar). Clave = sólo dígitos del
-- número de matrícula; `items` = la lista cruda de personas devuelta por
-- REFEPS para ese número (puede haber varias, se desambigua en refeps.py al
-- leer). Se consulta poco: un puñado de médicos que no cambia seguido, así
-- que una vez verificado casi no vuelve a pegarle al sitio de terceros.
CREATE TABLE IF NOT EXISTS refeps_cache (
    license_number text PRIMARY KEY,
    items          jsonb,
    ok             boolean DEFAULT true,
    checked        timestamptz DEFAULT now()
);

-- Lista de espera "avisame si se libera antes" (waitlist.py). El paciente
-- con un turno programado pide un turno más cercano; cuando uno se libera se
-- ofrece por email a la cola en orden FIFO, con ventana garantizada por
-- persona, hasta la hora misma del turno.
CREATE TABLE IF NOT EXISTS waitlist (
    id            serial PRIMARY KEY,
    request_id    integer,
    dni           text,
    email         text,
    service_id    text,
    center_slug   text,
    appointment_at timestamptz,   -- el turno ACTUAL del paciente (quiere algo antes)
    active        boolean DEFAULT true,
    created       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS waitlist_created_idx
    ON waitlist (service_id, center_slug, created) WHERE active;
-- waitlist_active(request_id) barre por la solicitud.
CREATE INDEX IF NOT EXISTS waitlist_request_idx ON waitlist (request_id);

-- Ofertas de un turno liberado, una fila por (slot, paciente). El barrido
-- (tick) avanza la cola creando la siguiente fila cuando la vigente expira.
CREATE TABLE IF NOT EXISTS appointment_offers (
    id             serial PRIMARY KEY,
    slot_at        timestamptz,   -- el turno liberado que se ofrece
    service_id     text,
    center_slug    text,
    waitlist_id    integer,      -- a quién se le ofrece
    token          text UNIQUE,   -- el token ES la autenticación del link del email
    sent_at        timestamptz,
    expires_at     timestamptz,   -- sent_at + VENTANA_MIN
    status         text DEFAULT 'ofrecida',  -- ofrecida | aceptada | expirada | vacante
    created        timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS appointment_offers_slot_idx
    ON appointment_offers (slot_at, service_id, center_slug);
CREATE INDEX IF NOT EXISTS appointment_offers_current_idx
    ON appointment_offers (status, expires_at) WHERE status='ofrecida';
-- Guard 2026-08-05: como máximo UNA oferta 'ofrecida' viva por slot.
-- `db.create_appointment_offer` inserta con `ON CONFLICT ... DO NOTHING`
-- contra este índice — dos workers/ticks que decidan ofrecer el MISMO slot a
-- la vez (release_slot vs. tick() solapados) sólo dejan una fila viva y un
-- mail. La migración 005_booking_integrity dedupea las ofertas 'ofrecida'
-- que ya existieran duplicadas en prod ANTES de que este CREATE INDEX corra
-- (mismo motivo que booking_requests_doctor_slot_uniq arriba: en una base
-- recién creada no hay nada que dedupear, el índice se crea directo).
CREATE UNIQUE INDEX IF NOT EXISTS appointment_offers_live_slot_uniq
    ON appointment_offers (service_id, center_slug, slot_at) WHERE status='ofrecida';

-- Búsquedas de pacientes (searches.py), para el futuro reporte admin
-- "Búsquedas". Sin PII: ni IP ni dni ni sesión, sólo la query + cuántos
-- resultados dio + de qué página vino.
-- At-most-once claims for daily alerts (housekeeping.check_disk, Codex
-- gate 2026-08-08): one row = the right to emit THAT alert THAT day,
-- taken BEFORE any send. Durable and cross-process: two workers the same
-- day, or a SIGKILL after the provider accepted the mail, cannot
-- duplicate the alert — INSERT ... ON CONFLICT decides exactly once.
CREATE TABLE IF NOT EXISTS alert_claims (
    alert_key   text NOT NULL,
    day         date NOT NULL,
    claimed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (alert_key, day)
);

-- One durable, non-sensitive completion record for the public demo's daily
-- refresh.  This deliberately records a *completed* generation, not that a
-- process or daemon thread happened to start: `outcome='success'` is written
-- only after assets, referral repair, and current-day seeded data all passed.
-- A partial/failed attempt overwrites `outcome` but preserves the last
-- complete generation, so an admin surface can fail closed instead of
-- mistaking an older green run for the current one.  The singleton key makes
-- this bounded, restart-safe operational state, never an event log or data
-- channel for demo records.
CREATE TABLE IF NOT EXISTS demo_refresh_completion (
    job             text PRIMARY KEY CHECK (job = 'demo_refresh'),
    attempted_for   date NOT NULL,
    outcome         text NOT NULL CHECK (outcome IN ('success', 'partial', 'failed')),
    last_attempt_at timestamptz NOT NULL DEFAULT now(),
    success_for     date,
    last_success_at timestamptz
);

-- Durable journal of the data/ volume's off-box backup (ops/data_backups,
-- Tier 4 daemon batch 5): one row per backed-up file. Identity is the
-- sha256 of the bytes ACTUALLY uploaded — size/mtime_ns are only the cheap
-- has-it-maybe-changed prefilter; a stat change with identical content
-- refreshes the row without re-uploading. Committed AFTER the remote
-- accepts, so a crash between accept and commit re-uploads that one file
-- (same bytes, a harmless extra B2 version) instead of ever recording a
-- backup that didn't happen. Replaces data/.backup-manifest.json (local,
-- lost with the very disk the backup exists to survive).
CREATE TABLE IF NOT EXISTS backup_runs (
    id text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL,
    archive_key text,
    size bigint NOT NULL DEFAULT 0,
    sha256 text,
    uploaded boolean NOT NULL DEFAULT false,
    integrity_verified boolean NOT NULL DEFAULT false,
    restore_tested boolean NOT NULL DEFAULT false,
    error text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS backup_journal (
    path        text PRIMARY KEY,
    sha256      text NOT NULL,
    size        bigint NOT NULL,
    mtime_ns    bigint NOT NULL,
    remote_name text NOT NULL,
    origin      text NOT NULL DEFAULT 'upload',
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    -- The B2 version currently named by this path.  NULL is intentional for
    -- journal rows written before Tier 4's immutable-receipt ledger: those
    -- legacy rows are useful evidence, but cannot prove an exact remote
    -- object and are therefore never retention candidates.  It stays last so
    -- a fresh baseline matches PostgreSQL's additive 0004 ALTER layout.
    remote_file_id text
);

-- Immutable remote-acceptance receipts.  One B2 file id identifies one
-- version, even when multiple versions share a file name.  Acceptance fields
-- are never updated: a repeated id with different bytes/metadata is a
-- protocol violation, not a conflict to silently overwrite.  Deletion state
-- lives in the separate tombstone table below so the receipt itself remains
-- an audit record after a remote retention action succeeds.
CREATE TABLE IF NOT EXISTS backup_versions (
    file_id       text PRIMARY KEY,
    remote_name   text NOT NULL,
    sha256        text NOT NULL,
    sha1          text NOT NULL,
    size          bigint NOT NULL,
    journal_path  text NOT NULL,
    accepted_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS backup_versions_path_idx
    ON backup_versions (journal_path, accepted_at DESC);

-- A tombstone is written only after an exact (name, file_id) B2 deletion
-- succeeds, or when a retried exact deletion reports that the same version is
-- already absent.  Keeping it separate makes the receipt immutable and lets
-- a delete-success/DB-crash retry converge without treating an unexplained
-- missing version as safely deleted.
CREATE TABLE IF NOT EXISTS backup_version_tombstones (
    file_id       text PRIMARY KEY,
    remote_name   text NOT NULL,
    deleted_at    timestamptz NOT NULL DEFAULT now()
);

-- Archivos que la RETENCIÓN borró a propósito (revisión de esquema №11): la
-- fila se escribe ANTES de borrar el archivo y es la memoria de que ese
-- camino no debe volver — ni subirse a B2, ni restaurarse, ni quedar en el
-- volumen. `path` es relativo a data/ (el mismo de backup_journal.path).
-- `remote_done` queda NULL hasta que el barrido de B2 borró todas las
-- versiones remotas de ese camino. Sin datos clínicos: sólo el camino.
CREATE TABLE IF NOT EXISTS purged_files (
    path         text PRIMARY KEY,
    reason       text NOT NULL,
    purged_at    timestamptz NOT NULL DEFAULT now(),
    remote_done  timestamptz
);

CREATE TABLE IF NOT EXISTS searches (
    id          bigserial PRIMARY KEY,
    ts          timestamptz DEFAULT now(),
    q           text NOT NULL,
    results     int NOT NULL,
    source      text
);
CREATE INDEX IF NOT EXISTS searches_q_idx ON searches (q, ts DESC);
CREATE INDEX IF NOT EXISTS searches_ts_idx ON searches (ts DESC);

-- Soft-lock de turnos (slot_holds.py, decisión 2026-07-16 — reunión con Pavel):
-- hoy nada bloquea un slot, dos personas pueden elegir el mismo horario. Esta
-- tabla retiene un slot por unos minutos desde que alguien lo ELIGE en el
-- wizard (nunca por sólo mirar la grilla) hasta que termina el formulario;
-- `request_id` liga el hold a la solicitud creada (lo deja bloqueado hasta
-- que el operador aprueba/rechaza); una solicitud APROBADA queda bloqueada
-- por su propio `appointment_at` en `booking_requests`, no por esta tabla.
CREATE TABLE IF NOT EXISTS slot_holds (
    id           bigserial PRIMARY KEY,
    service_id   text NOT NULL,
    day          date NOT NULL,   -- día local ART del slot (los lectores reciben YYYY-MM-DD)
    time         time NOT NULL,   -- hora local ART del slot (los lectores reciben HH:MM)
    token        text NOT NULL,
    request_id   bigint,          -- seteado al convertirse en solicitud
    expires      timestamptz NOT NULL,
    created      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (service_id, day, time)
);
CREATE INDEX IF NOT EXISTS slot_holds_token_idx ON slot_holds (token);
-- holds_release_booking_request / hold_of_booking_request barren por la solicitud ligada.
CREATE INDEX IF NOT EXISTS slot_holds_request_idx ON slot_holds (request_id)
    WHERE request_id IS NOT NULL;

-- Lo que la revisión 0030 (días/horas de texto -> date/time) no pudo conservar
-- en su columna: la fila entera de un hold imposible o repetido, o el texto de
-- una fecha de informe que no era un día real, con el motivo. Casi siempre
-- vacía; nadie la lee salvo quien quiera recuperar un dato (problems.md №13).
CREATE TABLE IF NOT EXISTS date_time_conversion_leftovers (
    id           bigserial PRIMARY KEY,
    revision     text NOT NULL,
    source_table text NOT NULL,
    row_id       bigint NOT NULL,
    reason       text NOT NULL,
    original     jsonb NOT NULL,
    created      timestamptz NOT NULL DEFAULT now()
);

-- Costos de IA (ai_costs.py): una fila por cada llamada a un modelo multimodal
-- (OCR del DNI frente/dorso, análisis 3-pass de la orden). `cost_usd` es el
-- usage.cost que devuelve OpenRouter. Alimenta el reporte admin "Costos de IA"
-- y la detección de abuso (una IP que quema tokens con puras imágenes
-- irreconocibles). Guarda la IP a propósito: sólo el admin la ve (detrás de
-- auth) y es el dato accionable para cortar a un scraper; la política de
-- privacidad ya declara que guardamos logs con IP. Analytics puro: escribir
-- acá NUNCA tumba el request (db.log_ai_cost absorbe cualquier falla).
CREATE TABLE IF NOT EXISTS ai_costs (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    operation   text NOT NULL,        -- 'analizar_orden' | 'ocr_frente' | 'ocr_dorso'
    cost_usd    numeric(10,6) NOT NULL DEFAULT 0,
    ip          text,
    user_dni    text,
    model       text,
    recognized  boolean               -- ¿el modelo sacó algo útil? (NULL = no aplica)
);
CREATE INDEX IF NOT EXISTS ai_costs_ts_idx ON ai_costs (ts DESC);
CREATE INDEX IF NOT EXISTS ai_costs_ip_idx ON ai_costs (ip, ts DESC);

-- Tope duro de lecturas de órdenes originadas en MCP. A diferencia de los
-- rate limits en memoria, no se reinicia con un deploy ni se saltea al escalar
-- el proceso: la reserva es atómica en Postgres antes de llamar al modelo.
CREATE TABLE IF NOT EXISTS mcp_ai_budget (
    day         date PRIMARY KEY,
    uses        integer NOT NULL DEFAULT 0 CHECK (uses >= 0),
    updated     timestamptz NOT NULL DEFAULT now()
);

-- Panel de la pantalla de login del admin (panel_login.py): experimento
-- "Humor de guardia" vs "Consejos de uso". `sid` = id efímero de UNA vista del
-- gate (random client-side, muere al navegar); `variant` = pestaña que esa
-- vista abrió por defecto (asignación 50/50 persistida en localStorage);
-- `event` ∈ exposicion | tab | item | login_ok | login_error; `tab` = pestaña
-- donde pasó el evento; `item` = id del meme/consejo mostrado; `ms` = tiempo
-- desde que se mostró el gate (sólo login_ok/login_error). Sin PII, sin IP —
-- mismo contrato que `searches`. Alimenta el sub-reporte "Panel de login".
CREATE TABLE IF NOT EXISTS login_panel_events (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    sid       text NOT NULL,
    variant   text NOT NULL,
    event     text NOT NULL,
    tab       text,
    item      text,
    ms        int
);
CREATE INDEX IF NOT EXISTS login_panel_events_ts_idx ON login_panel_events (ts DESC);
CREATE INDEX IF NOT EXISTS login_panel_events_sid_idx ON login_panel_events (sid);

-- Registro de accesos a datos clínicos (Ley 26.529 art. 18, Ley 25.326,
-- Res. AAIP 47/2018 — core/access_log.py, revisión Alembic 0026): una fila
-- por cada vez que alguien del panel abre la ficha, la entrevista, la
-- consulta, el resumen IA, la historia clínica, los análisis, la orden o el
-- perfil de salud de un paciente. `actor` = usuario del panel; `action` =
-- qué pantalla o acción; `dni_hash` = HMAC del DNI clínico (se busca por DNI
-- sin guardarlo); `allowed` = false cuando el acceso se negó. Sólo se agrega:
-- `_ACCESS_LOG_GUARD_SQL` rechaza todo UPDATE y todo DELETE fuera de la
-- purga por antigüedad.
CREATE TABLE IF NOT EXISTS clinical_access_log (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL,
    actor_role  text NOT NULL DEFAULT '',
    action      text NOT NULL,
    dni_hash    text NOT NULL,
    target_id   text,
    allowed     boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS clinical_access_log_dni_idx
    ON clinical_access_log (dni_hash, ts DESC);
CREATE INDEX IF NOT EXISTS clinical_access_log_actor_idx
    ON clinical_access_log (actor, ts DESC);
CREATE INDEX IF NOT EXISTS clinical_access_log_ts_idx
    ON clinical_access_log (ts);

-- Registro de pedidos de los titulares de datos (Ley 25.326 arts. 14-16 —
-- core/subject_requests.py, revisión Alembic 0028): acceso (10 días corridos),
-- rectificación y supresión (5 días hábiles). La persona se guarda como
-- `subject_hash` (el HMAC del DNI del registro de accesos), nunca el DNI. La
-- fila lleva el estado actual; `data_subject_request_events` es el rastro y
-- sólo se agrega (`_SUBJECT_EVENTS_GUARD_SQL`).
CREATE TABLE IF NOT EXISTS data_subject_requests (
    id              bigserial PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('access','rectification','erasure')),
    subject_hash    text NOT NULL,
    received_on     date NOT NULL,
    due_on          date NOT NULL,
    channel         text NOT NULL DEFAULT '',
    identity_check  text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'received'
                    CHECK (status IN ('received','under_review','answered','refused')),
    outcome         text NOT NULL DEFAULT '',
    registered_by   text NOT NULL,
    decided_by      text,
    answered_on     date,
    created         timestamptz NOT NULL DEFAULT now(),
    updated         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS data_subject_requests_subject_idx
    ON data_subject_requests (subject_hash, received_on DESC);
CREATE INDEX IF NOT EXISTS data_subject_requests_open_idx
    ON data_subject_requests (status, due_on);
CREATE TABLE IF NOT EXISTS data_subject_request_events (
    id          bigserial PRIMARY KEY,
    request_id  bigint NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL,
    event       text NOT NULL,
    detail      text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS data_subject_request_events_request_idx
    ON data_subject_request_events (request_id, ts);

-- HSI sync journal.  HSI is the clinical source of truth; these rows are the
-- durable transport/audit layer, not a second clinical system.  One intent
-- receives one deterministic idempotency key, so a worker restart cannot turn
-- an already confirmed remote booking into a duplicate create.
CREATE TABLE IF NOT EXISTS hsi_outbox (
    id                bigserial PRIMARY KEY,
    entity_type       text NOT NULL,
    entity_id         text NOT NULL,
    operation         text NOT NULL,
    payload           jsonb NOT NULL,
    idempotency_key   text NOT NULL UNIQUE,
    state             text NOT NULL DEFAULT 'pending'
                      CHECK (state IN ('pending','sending','confirmed','error','conflict')),
    attempts          integer NOT NULL DEFAULT 0,
    next_attempt_at   timestamptz NOT NULL DEFAULT now(),
    lease_expires_at  timestamptz,
    last_error        text,
    hsi_result        jsonb,
    created           timestamptz NOT NULL DEFAULT now(),
    updated           timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE hsi_outbox ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
CREATE INDEX IF NOT EXISTS hsi_outbox_ready_idx ON hsi_outbox (state, next_attempt_at);

CREATE TABLE IF NOT EXISTS hsi_inbox (
    id                bigserial PRIMARY KEY,
    source_key        text NOT NULL UNIQUE,
    entity_type       text NOT NULL,
    entity_id         text NOT NULL,
    payload           jsonb NOT NULL,
    observed_at       timestamptz NOT NULL DEFAULT now(),
    reconciled_at     timestamptz,
    state             text NOT NULL DEFAULT 'pending'
                      CHECK (state IN ('pending','confirmed','conflict','error')),
    conflict          jsonb
);
CREATE INDEX IF NOT EXISTS hsi_inbox_pending_idx ON hsi_inbox (state, observed_at);

CREATE TABLE IF NOT EXISTS hsi_sync_conflicts (
    id                bigserial PRIMARY KEY,
    entity_type       text NOT NULL,
    entity_id         text NOT NULL,
    direction         text NOT NULL CHECK (direction IN ('inbound','outbound')),
    reason            text NOT NULL,
    local_payload     jsonb,
    remote_payload    jsonb,
    resolved_at       timestamptz,
    created           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hsi_sync_conflicts_open_idx ON hsi_sync_conflicts (resolved_at, created DESC);
-- Qué hizo el resolutor automático (hsi_resolver.py) al cerrar el conflicto:
-- {"accion","texto"} — la sección «HSI» del admin lo muestra tal cual.
ALTER TABLE hsi_sync_conflicts ADD COLUMN IF NOT EXISTS resolution jsonb;
ALTER TABLE hsi_sync_conflicts ADD COLUMN IF NOT EXISTS resolved_by text;

-- Minimal imported mirrors.  We deliberately do not create patient web
-- accounts from HSI data; imported clinical identity remains separate from
-- Sana authentication until the patient registers.
CREATE TABLE IF NOT EXISTS hsi_patients (
    hsi_patient_id    text PRIMARY KEY,
    dni               text,
    payload           jsonb NOT NULL,
    observed_at       timestamptz NOT NULL DEFAULT now()
);
-- Join por dni desde hsi_appointments_by_dni / attendance_summary_by_dnis.
CREATE INDEX IF NOT EXISTS hsi_patients_dni_idx ON hsi_patients (dni);
CREATE TABLE IF NOT EXISTS hsi_doctor_schedules (
    hsi_diary_id      text PRIMARY KEY,
    hsi_professional_id text,
    payload           jsonb NOT NULL,
    observed_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS hsi_appointments (
    hsi_appointment_id text PRIMARY KEY,
    hsi_diary_id       text,
    hsi_patient_id     text,
    payload            jsonb NOT NULL,
    observed_at        timestamptz NOT NULL DEFAULT now()
);
-- Lados de join que acumulan historial (los mirrors chicos ya tienen PK).
CREATE INDEX IF NOT EXISTS hsi_appointments_patient_idx ON hsi_appointments (hsi_patient_id);
CREATE INDEX IF NOT EXISTS hsi_appointments_diary_idx ON hsi_appointments (hsi_diary_id);
CREATE TABLE IF NOT EXISTS hsi_checkpoints (
    entity_type       text NOT NULL,
    entity_id         text NOT NULL,
    remote            jsonb NOT NULL,
    observed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_id)
);

-- Chat de turnos (2026-07-23, chat_agent.py): historial multi-turno,
-- compartido por los dos canales (widget del sitio y WhatsApp). `key`
-- identifica la CONVERSACIÓN — "dni:<dni>" o "ip:<ip>" en el canal web,
-- "tel:<digitos>" en WhatsApp; `content` es el mensaje ENTERO en formato
-- OpenAI-style ({"role":..., "content":..., "tool_calls":[...]?,
-- "tool_call_id":...?}) — guardarlo así, en vez de columnas por campo, deja
-- reconstruir messages[] para el próximo llamado al modelo sin reensamblar
-- nada. `role` es un duplicado barato de content->>'role' para poder
-- filtrar/auditar sin parsear el jsonb.
CREATE TABLE IF NOT EXISTS chat_messages (
    id        bigserial PRIMARY KEY,
    channel   text NOT NULL,
    key       text NOT NULL,
    role      text NOT NULL,
    content   jsonb NOT NULL,
    created   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_conv_idx ON chat_messages (channel, key, created);

-- `linked_whatsapp_claves` matchea WhatsApp por los ÚLTIMOS 10 DÍGITOS de
-- `key` (mismo criterio que `users_contact_tel_idx` arriba) en CADA fusión
-- de conversación de un usuario logueado -- sin índice sobre la expresión
-- misma, un scan completo de chat_messages.
CREATE INDEX IF NOT EXISTS chat_messages_whatsapp_tel_idx
    ON chat_messages (channel, right(regexp_replace(key, '\D', '', 'g'), 10))
    WHERE channel='whatsapp';

-- Memoria entre sesiones de chat (RAG-lite, 2026-08-03, ver chat_agent.py
-- _maybe_summarize_closed_session): cuando una conversación (web o
-- WhatsApp, o las dos fusionadas — ver chat_agent._conversation_pairs)
-- queda en silencio más de TURNOS_CHAT_SESSION_GAP_HOURS, la sesión
-- cerrada se resume con el mismo cliente de IA del resto del chat
-- (ai_client.py) y se guarda acá, POR PACIENTE (`patient_key` = "dni:<dni>"
-- — nunca se resumen conversaciones anónimas, no hay identidad estable a la
-- que atarlas). `covers_until` es el timestamp del último mensaje que ese
-- resumen cubre: sirve de tope de idempotencia (una sesión cerrada se
-- resume UNA sola vez, ver chat_summaries_latest_covers) y de orden de
-- recencia para inyectar los últimos N en el prompt de una sesión nueva
-- (chat_agent._memory_block). Sin embeddings ni pgvector — a esta escala,
-- texto plano + recencia alcanza.
CREATE TABLE IF NOT EXISTS chat_summaries (
    id             bigserial PRIMARY KEY,
    patient_key    text NOT NULL,
    channel        text NOT NULL DEFAULT 'merged',
    summary        text NOT NULL,
    message_count  integer NOT NULL DEFAULT 0,
    covers_until   timestamptz NOT NULL,
    created        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_summaries_patient_idx
    ON chat_summaries (patient_key, covers_until DESC);

-- Código de traspaso web -> WhatsApp ("ST-" + 12 chars, ver whatsapp_chat.py):
-- transfiere el historial de la conversación (`web_key`) a quien lo escriba
-- por WhatsApp. Single-use (used=true al consumirlo) y vence solo
-- (`expires`, default 48h). Si `web_key` viene de una sesión logueada
-- ("dni:<dni>..."), redimirlo TAMBIÉN autoriza reservas desde ese teléfono
-- por un tiempo (Ivan, 2026-08-02, ver db.user_by_handoff_phone) — un código
-- de una conversación anónima ("ip:...") nunca autoriza nada. El match
-- directo por teléfono (`users.contact`) sigue siendo la vía primaria.
CREATE TABLE IF NOT EXISTS chat_handoffs (
    code       text PRIMARY KEY,
    web_key    text NOT NULL,
    created    timestamptz NOT NULL DEFAULT now(),
    expires    timestamptz NOT NULL,
    used       boolean NOT NULL DEFAULT false,
    used_phone text,
    used_at    timestamptz
);
CREATE INDEX IF NOT EXISTS chat_handoffs_web_key_idx ON chat_handoffs (web_key);

-- Prompts de la IA editables desde el panel (prompts.py). Mismo patrón de
-- overlay que `catalog`: el original vive en el código y esta tabla lo pisa.
-- `text_en` es el canónico (lo que se le manda al modelo); `text_es` es la
-- traducción que se edita en pantalla y que sirve de BASE para el merge a tres
-- bandas del próximo guardado. `edited=false` = la fila existe sólo para
-- cachear la traducción del original (no pisa nada).
CREATE TABLE IF NOT EXISTS ai_prompts (
    key             text PRIMARY KEY,
    text_en         text NOT NULL,
    text_es         text,
    edited          boolean NOT NULL DEFAULT false,
    updated         timestamptz NOT NULL DEFAULT now(),
    updated_by      text
);

-- Cada versión anterior, para poder deshacer. Un prompt gobierna si una orden
-- se aprueba: una edición desafortunada tiene que revertirse en un click.
CREATE TABLE IF NOT EXISTS ai_prompts_history (
    id              serial PRIMARY KEY,
    key             text NOT NULL,
    text_en         text NOT NULL,
    text_es         text,
    updated         timestamptz NOT NULL DEFAULT now(),
    updated_by      text
);
CREATE INDEX IF NOT EXISTS ai_prompts_history_idx ON ai_prompts_history (key, id DESC);

-- Entrevista previa (modules/intake/). Una fila por entrevista; el
-- transcript NO vive acá — reusa chat_messages (channel='interview',
-- key='interview:<id>'), que por eso pasó a ser tabla compartida de
-- plataforma. `request_id` referencia booking_requests.id POR VALOR (sin
-- FK, como todo el esquema): una entrevista por QR puede no matchear ningún
-- turno y el médico la ve igual ("sin turno asignado"). El resultado del
-- análisis (triage/summary/brief/...) se guarda desnormalizado acá porque
-- la cola del día lo lee en un solo query batcheado (interviews_for_day).
CREATE TABLE IF NOT EXISTS interviews (
    id              bigserial PRIMARY KEY,
    doctor_id       text,
    request_id      integer,
    entry_mode      text NOT NULL DEFAULT 'qr',
    entered_name    text,
    entered_surname text,
    matched         boolean NOT NULL DEFAULT false,
    dni             text,
    session_key     text NOT NULL UNIQUE,
    patient_token   text UNIQUE,
    status          text NOT NULL DEFAULT 'active',
    triage          text,
    summary         text,
    brief           jsonb,
    differential    jsonb,
    suggested_exams jsonb,
    red_flags       jsonb,
    ai_model        text,
    analyzed_at     timestamptz,
    created         timestamptz NOT NULL DEFAULT now(),
    updated         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS interviews_doctor_day_idx ON interviews (doctor_id, created);
CREATE INDEX IF NOT EXISTS interviews_request_idx ON interviews (request_id);
-- UNA entrevista por solicitud (verificación formal 2026-09-24,
-- IntakeTriage.tla OneInterviewPerRequest): `interview_bind` decide quién
-- queda atada bajo un advisory lock y este índice es el respaldo. Las
-- duplicadas que ya existieran NO se borran: la que gana es la misma que
-- `interview_for_request` ya mostraba (la no-QR, y entre iguales la más
-- nueva); las demás se desatan (`request_id` NULL) y guardan de qué
-- solicitud venían en `detached_request_id`.
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS detached_request_id integer;
UPDATE interviews SET detached_request_id = request_id, request_id = NULL
    WHERE request_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM interviews w
                   WHERE w.request_id = interviews.request_id AND w.id <> interviews.id
                     AND ((w.entry_mode = 'qr') < (interviews.entry_mode = 'qr')
                          OR ((w.entry_mode = 'qr') = (interviews.entry_mode = 'qr')
                              AND w.id > interviews.id)));
CREATE UNIQUE INDEX IF NOT EXISTS interviews_request_uniq ON interviews (request_id)
    WHERE request_id IS NOT NULL;
-- Fase 2 (entrevista post-reserva, 2026-07-31): una solicitud recién creada
-- puede no tener médico asignado todavía (medico_asignado_id sólo se
-- completa cuando el hospital aprueba) — doctor_id pasó a nullable acá
-- arriba; los ALTER de abajo lo migran en una base que ya tenía la fase 1
-- (columna NOT NULL, sin patient_token). El token opaco ES la autorización
-- de /entrevista/<token-implícito-en-la-URL-por-request_id> y de
-- /api/intake/t/<patient_token>/... (mismo modelo de confianza que
-- appointment_offers.token) — por eso UNIQUE, nunca reusado entre filas.
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS patient_token text;
CREATE UNIQUE INDEX IF NOT EXISTS interviews_patient_token_idx ON interviews (patient_token);
ALTER TABLE interviews ALTER COLUMN doctor_id DROP NOT NULL;
-- Red-triage alert bell (2026-08-01, admin header): quién vio la alerta y
-- cuándo — sin fila propia, el ack vive en la misma entrevista que la generó
-- (una entrevista sólo puede disparar UNA alerta). Nombres en inglés a
-- propósito, sin entrada en _WIRE_BACK: `interviews` es una tabla nacida ya
-- en el esquema nuevo (todas sus columnas son inglesas), así que una única
-- columna en español acá sería la rareza, no la consistencia.
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS alert_seen_at timestamptz;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS alert_seen_by text;
CREATE INDEX IF NOT EXISTS interviews_red_alert_idx ON interviews (doctor_id)
    WHERE triage = 'ROJO' AND alert_seen_at IS NULL;
-- "Actualizar entrevista" (2026-08-01): un paciente con una entrevista DONE
-- para un turno todavía futuro puede reabrirla para agregar información
-- nueva (interview_patient._reopen). `reopened_at` se pisa en cada reopen y
-- NUNCA se limpia — una vez reabierta, el resumen que lee el médico siempre
-- lleva la marca "Actualizada" (ver interview_agent.py, consult_admin.py).
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS reopened_at timestamptz;

-- Lo que ya sabemos de esta persona (2026-08-01, Ivan: "next time I have an
-- appointment, the chat should already know what medication I take and only
-- ask me for updates"). UNA fila por DNI clínico — no por turno ni por
-- entrevista: la medicación que toma una persona no cambia porque pidió otro
-- turno. La entrevista la lee al arrancar y la reescribe al cerrar.
--
-- `datos` es jsonb y no columnas sueltas a propósito: son listas de texto
-- libre en el idioma del paciente ("metilfenidato 20 mg a la mañana"), no un
-- vademécum codificado — esto NO es una historia clínica, es lo que la
-- persona contó, y el HSI sigue siendo el sistema de registro. Forma:
--   {"medicacion": [...], "condiciones": [...], "alergias": [...],
--    "antecedentes": [...]}
-- `source_interview_id` = la última entrevista que lo actualizó, para poder
-- rastrear de dónde salió cada dato.
CREATE TABLE IF NOT EXISTS patient_health_profile (
    dni                 text PRIMARY KEY,
    datos               jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_interview_id bigint,
    updated             timestamptz NOT NULL DEFAULT now()
);

-- Lo que el médico confirmó sobre una entrevista (modules/intake/
-- consult_admin.py): diagnósticos/estudios/medicación editados a mano sobre
-- la sugerencia de la IA. `receta_json`/`orden_json` es el contenido YA
-- compuesto de los imprimibles (el pase de IA corre al confirmar, nunca al
-- imprimir); los tokens opacos SON la autorización de /r/<t> y /o/<t>
-- (mismo modelo que appointment_offers.token).
CREATE TABLE IF NOT EXISTS consultations (
    id              bigserial PRIMARY KEY,
    interview_id    bigint NOT NULL,
    doctor_id       text NOT NULL,
    diagnoses       jsonb NOT NULL DEFAULT '[]',
    exams           jsonb NOT NULL DEFAULT '[]',
    meds            jsonb NOT NULL DEFAULT '[]',
    notes           text,
    receta_json     jsonb,
    orden_json      jsonb,
    receta_token    text UNIQUE,
    orden_token     text UNIQUE,
    created         timestamptz NOT NULL DEFAULT now(),
    created_by      text
);
CREATE INDEX IF NOT EXISTS consultations_interview_idx ON consultations (interview_id);
-- Una sola consulta VIGENTE por entrevista (2026-10-23, problems.md №1): un
-- segundo juego de receta/orden sólo nace de un «Reemplazar» explícito, que
-- anula el anterior (`superseded_at` + `superseded_by` = la que lo reemplazó)
-- en la misma transacción; su link /r/ /o/ imprime «ANULADA». El índice
-- parcial lo hace regla de la base (Alembic 0027).
ALTER TABLE consultations ADD COLUMN IF NOT EXISTS superseded_at timestamptz;
ALTER TABLE consultations ADD COLUMN IF NOT EXISTS superseded_by bigint;
CREATE UNIQUE INDEX IF NOT EXISTS consultations_vigente_uniq
    ON consultations (interview_id) WHERE superseded_at IS NULL;
-- Resumen del paciente (2026-08-01): resumen pre-generado por IA que el
-- médico ve al abrir la ficha (chart_summary.py). Una fila por (dni, médico):
-- el resumen se filtra por la especialidad de ESE médico, así que no es
-- compartible entre médicos. `fingerprint` es el hash de las fuentes con el
-- que se decide si hay que regenerar; el JSON del resumen nunca se edita a
-- mano — se regenera entero.
CREATE TABLE IF NOT EXISTS patient_summaries (
    id           bigserial PRIMARY KEY,
    dni          text NOT NULL,
    doctor_id    text NOT NULL,
    especialidad text,
    resumen      jsonb NOT NULL,
    fingerprint  text,
    ai_model     text,
    generated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dni, doctor_id)
);
CREATE INDEX IF NOT EXISTS patient_summaries_dni_idx ON patient_summaries (dni);

-- Registros clínicos históricos normalizados. Ésta es una copia de lectura
-- para preparar la consulta, no la historia clínica autoritativa: cada fila
-- conserva su sistema de origen y el JSON importado. `record_hash` hace la
-- importación idempotente; el DNI es la clave de enlace acordada para los
-- archivos Alepo/Marqui y los archivos privados de una instalación.
CREATE TABLE IF NOT EXISTS clinical_history_records (
    id            bigserial PRIMARY KEY,
    dni           text NOT NULL,
    source_system text NOT NULL,
    record_hash   text NOT NULL,
    occurred_on   date,
    record        jsonb NOT NULL,
    imported_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dni, source_system, record_hash)
);
CREATE INDEX IF NOT EXISTS clinical_history_records_dni_date_idx
    ON clinical_history_records (dni, occurred_on DESC);

-- Lectura del paciente de sus propios laboratorios (lab_analysis.py,
-- 2026-08-06): una fila por informe de laboratorio ya leído por la IA (o por
-- el armado determinístico). `report_hash` identifica el CONTENIDO del
-- informe (así un informe corregido es una fila nueva, no un pisado);
-- `prompt_hash` cubre el texto resuelto de los dos prompts editables
-- (laboratorio_paciente + laboratorio_sensible) — al cambiar cualquiera de
-- los dos, las filas viejas dejan de "estar frescas" y se re-analizan de a
-- poco (lab_analysis._BATCH/_MAX_BATCHES). `sensible` decide server-side si
-- `valores`/`tipo_estudio`/`resumen` llegan al paciente.
CREATE TABLE IF NOT EXISTS lab_report_analysis (
    id           bigserial PRIMARY KEY,
    dni          text NOT NULL,
    report_hash  text NOT NULL,
    prompt_hash  text NOT NULL,
    fecha        date,
    sistema      text,
    tipo_estudio text,
    resumen      text,
    tono         text,
    sensible     boolean NOT NULL DEFAULT false,
    valores      jsonb,
    modelo       text,
    created      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dni, report_hash)
);
CREATE INDEX IF NOT EXISTS lab_report_analysis_dni_idx ON lab_report_analysis (dni);

-- Resultados de laboratorio que sube el PROPIO paciente desde su cuenta
-- (docs/architecture/laboratorio-paciente.md, «Resultados aportados por el
-- paciente»). No son historia clínica autoritativa: el conector
-- `patient_uploads` los devuelve marcados `tercero: true` para que el
-- resumen del médico los presente como aportados por el paciente y sin
-- verificar. `uploaded_by_dni` es el DNI de la sesión (el adulto a cargo
-- cuando el informe es de una persona a cargo) y es el único que puede
-- borrarlos; `dni` es el DNI clínico al que pertenece el informe.
-- `sin_lectura` marca el archivo cuya transcripción todavía no se hizo
-- (sin IA disponible) — la cuenta dice que se va a leer más tarde.
CREATE TABLE IF NOT EXISTS patient_lab_uploads (
    id              bigserial PRIMARY KEY,
    dni             text NOT NULL,
    uploaded_by_dni text NOT NULL,
    filename        text,
    mime            text,
    archivo         bytea,
    texto           text,
    fecha           date,
    sin_lectura     boolean NOT NULL DEFAULT false,
    created         timestamptz NOT NULL DEFAULT now(),
    deleted         timestamptz
);
CREATE INDEX IF NOT EXISTS patient_lab_uploads_dni_idx
    ON patient_lab_uploads (dni, created DESC);

-- The three tables below were historically created only by the numbered
-- legacy migrations (002_translations, 003_legal_docs, 004_chat_compactions),
-- which ran on every boot before Alembic became the sole normal-boot schema
-- authority. With that runner retired to a one-time bridge, a fresh install
-- gets them from the baseline (this script) like every other table; the DDL
-- is verbatim from those migration files so a bridged legacy database and a
-- fresh one stay byte-identical. Alembic revision 0006 re-creates them
-- IF NOT EXISTS for the rare already-Alembic database that predates this
-- block and never met the legacy runner.
CREATE TABLE IF NOT EXISTS translations (
    source_key   text NOT NULL,
    lang         text NOT NULL,
    source_hash  text NOT NULL,
    content      text NOT NULL,
    model        text,
    override     boolean NOT NULL DEFAULT false,
    created      timestamptz NOT NULL DEFAULT now(),
    updated      timestamptz NOT NULL DEFAULT now(),
    updated_by   text,
    PRIMARY KEY (source_key, lang)
);

CREATE TABLE IF NOT EXISTS legal_docs (
    doc         text PRIMARY KEY,
    body        text NOT NULL,
    created     timestamptz NOT NULL DEFAULT now(),
    updated     timestamptz NOT NULL DEFAULT now(),
    updated_by  text
);

CREATE TABLE IF NOT EXISTS chat_compactions (
    patient_key    text PRIMARY KEY,
    summary        text NOT NULL,
    message_count  integer NOT NULL DEFAULT 0,
    covers_until   timestamptz NOT NULL,
    created        timestamptz NOT NULL DEFAULT now(),
    updated        timestamptz NOT NULL DEFAULT now()
);
