# Real-participant usability study

## Run

Use the isolated fictional demo, never the hospital installation. Enable
`TURNOS_DEMO_MODE=1` and `TURNOS_STUDY=1` on the demo. The default is off.
Alembic revision `0020_real_study` follows `0019_ai_register_changes`; normal boot
applies it. The feature introduces no dependencies or external AI calls.

Give the coordination team **one link**: https://demo.humanrounds.org/study . Its first question
is «¿Quién sos?» (Paciente / Médico/a / Administrador/a). The optional fallback
links are `/study?role=patient`, `/study?role=doctor`, `/study?role=admin`.
These select a role but still require consent. No data is captured on the
landing page. Consent creates a random, private, resumable `/study/s/<token>`
link. Do not distribute a participant's session link to other participants.

Use synthetic data and images. Never type actual DNI, names, contact details,
clinical information or credentials into the demo. Seeded demo access for
staff is passwordless: administrators use `/admin/demo-access`, doctors use
`/admin/demo-doctor-access`. Both staff demo identities reject mutations,
including PATCH. Patient tasks start at `/`. Follow every stop-before-save
instruction, even though staff mutations are blocked on the server.

Each task has «Terminé / Me trabé», an optional skip, ease 1–5 and an optional
comment. «Terminé» also permits «Con dudas». A saved task cannot be rewritten
by a retry. Refresh resumes the first unanswered task. Finishing records
F1–F3 and marks remaining tasks skipped. Closing the page retains already
submitted responses and buffered events that reach the server. «Dejar de
participar» stops capture immediately and finishes the session. Participants
can retry a failed send without losing their current answers.

## Review and decisions

Open `/admin/usability`. «Resultados» shows participant and finish counts by
role, task completion, mean ease, comments and stuck tasks. «Hallazgos» uses
U-<number> ids, status/role filters, evidence, severity, fix notes, live links
and commits. «Enlaces» contains the single distribution link and role fallback
links. An authenticated administrator can read, including the read-only demo
cookie. Only the configured owner can run analysis, add a manual finding, or
change status, individually or for selected findings. Use an owner session in a separate browser profile from the
participant runner; demo entry deliberately replaces that profile's staff
cookie with the sandbox identity.

«Analizar respuestas» derives candidate problems from stuck/doubt responses,
median task duration above the data-defined threshold (default 120 seconds),
non-interactive clicks with no navigation within 1.5 seconds, repeated A-B-A
navigation, a begun task without a completed result after finishing or 30 minutes of
inactivity, and ease of 1–2. Every
candidate is deduplicated by task and signal. Re-analysis updates evidence
without overwriting decisions, notes, links or commits. These are candidates
for review, not proof of a usability defect or a measured product outcome.

Statuses: `found`, `in_progress`, `fixed`, `not_done`, `dismissed`. A reviewer
records the fix note, commit and published page when resolving a finding.

## Storage and retention

PostgreSQL stores a session id, SHA-256 token hash, role, es/en locale, consent,
start/finish timestamps and only a mobile/tablet/desktop user-agent class.
Task results contain outcome, ease, optional sanitized comment and duration.
F1–F3 use the same task-result table. Event records contain task id, elapsed
milliseconds, server receive time, event kind, a safe path and a structural target descriptor
(tag, sibling index, interactive flag). They never contain input values,
DOM text, element ids, query strings, fragments, names or DNI from URLs.
No IP, raw user agent, participant name or DNI field is collected.

Optional comments are free text: the UI warns against personal data, and the
server removes common identity numbers, emails, phone numbers and name
patterns. This cannot guarantee removal of every identifying phrase. Treat
comments as private research material. No raw event log or session token is
returned by the admin report. Session links carry bearer authority; responses
are no-store, no-index and no-referrer. Deployment HTTP access logs are a
separate operational retention surface; avoid logging request bodies and
redact `/study/s/<token>` and `/api/study/s/<token>` segments there.

`TURNOS_STUDY_RETENTION_DAYS` defaults to 90 and is displayed in the consent
page. Existing housekeeping (thread or queue worker) deletes expired sessions
together with their results/events in one transaction, and deletes expired findings, in batches
of 1000. Purge still runs with the study disabled. Backups follow the
installation's separate backup-retention policy. Event batches are capped at
100, sessions at 3000 events, bodies at 64 KiB and comments at 2000 characters.

Screen sharing is deliberately unimplemented. The explicitly named
`screenCaptureExtension` export is the future integration point. No call to
getDisplayMedia, screen permission, recording, microphone or camera exists.

## Consent text (Spanish, default 90 days)

¿Quién sos?

Ayudanos a mejorar Human Rounds. Lleva unos 8 minutos. Usá datos de ejemplo,
nunca tus datos ni los de un paciente.

Qué se guarda:

- Tu rol, las páginas que visitás, los clics, el tiempo por tarea y tus respuestas.
- No guardamos tu nombre, DNI, IP, contraseñas, valores de formularios ni imágenes.
  No grabamos tu pantalla.
- Las respuestas se borran a los 90 días. No escribas datos personales en los
  comentarios.

Participar es voluntario y anónimo. Podés salir en cualquier momento con
«Dejar de participar» o cerrar esta página. Lo ya enviado se conserva hasta
su borrado.

Entendí qué se guarda y acepto participar.

## WhatsApp message for la coordinación (prepared, not sent)

la coordinación, armamos una prueba para mejorar Human Rounds. Lleva unos 8 minutos y es
anónima. ¿La podés compartir con pacientes, médicos y administradores del
hospital? Todos entran por el mismo enlace: https://demo.humanrounds.org/study
Usen datos de ejemplo. Gracias.

## Per-role task seed (verbatim)

# Estudio de usabilidad con personas reales — Pinamar Turnos (borrador de tareas)

Entorno: https://demo.humanrounds.org (datos de ejemplo; nada de lo que se haga afecta turnos reales).
Regla: las tareas con "guardar/confirmar" se hacen HASTA el botón, sin apretarlo (la demo es de solo lectura).
Tiempo total: 8-10 minutos por persona. Al final de cada tarea: ¿Pudiste? (Sí / Con dudas / No), qué tan fácil fue (1-5) y un comentario opcional.

## Paciente (5 tareas)
P1. Encontrá qué médicos atienden Pediatría y en qué centro.
P2. Pedí un turno de Clínica médica para la semana próxima (hasta el último paso, sin confirmar).
P3. Subí la foto de una orden médica (cualquier imagen) para pedir un estudio y mirá qué te dice.
P4. Creá tu cuenta con un DNI de ejemplo y volvé a entrar (hasta la pantalla de ingreso).
P5. Encontrá la dirección y el horario de la guardia del hospital.

## Médico/a (5 tareas) — entra con "Entrar a la demo como personal"
D1. Mirá tus pacientes de hoy e identificá el próximo turno.
D2. Pasá al próximo día en que tenés turnos usando el calendario.
D3. Abrí «Mis agendas» y fijate qué horarios tenés y en qué centros.
D4. Cambiá tu foto (hasta elegir la imagen, sin guardar) y revisá si queda bien.
D5. Buscá a un paciente por DNI y abrí su resumen.

## Administrador/a (6 tareas)
A1. Encontrá la solicitud más antigua sin atender y dejala lista para aprobar (sin confirmar).
A2. Cargá un turno nuevo por teléfono para un paciente de ejemplo (hasta el último paso).
A3. Cambiá el horario de un servicio y leé qué te avisa que va a pasar con los médicos.
A4. Agregá un cierre de agenda para el 12 de noviembre en un servicio (sin guardar).
A5. Fijá cuántos días de anticipación se pueden sacar turnos en un médico (sin guardar).
A6. Creá una cuenta nueva de médico con su email (hasta el último paso).

## Preguntas finales (todos)
F1. ¿Qué fue lo más confuso? (texto libre)
F2. ¿Qué cambiarías primero? (texto libre)
F3. ¿Lo usarías en tu trabajo / para sacar un turno real? (Sí / Tal vez / No)
