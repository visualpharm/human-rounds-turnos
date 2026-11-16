# Rename map — Spanish → English identifiers

**2026-08-01 update: the `sacoturno.css` pin below is LIFTED.** the product owner asked
explicitly for the legacy fork brand to be out of the repo, not just the
public site — the file is now `human-rounds.css`. Every "do not rename
sacoturno.css" note in this document is historical context for why that
decision was made THEN, not current guidance. The `stz-*` CSS class family,
`STZ`/`window.STZ`, `SacoNav` and `stz-ui.js` are a SEPARATE, still-open
decision (unaffected by the filename rename) — see the open questions below,
still unresolved.

Produced by reading the actual repo state on 2026-07-29, not from the task
brief's assumptions. Two corrections to that brief up front, because they
change how big this job actually is:

1. **A prior rename pass already happened (2026-07-17, see `legacy_urls.py`
   header).** Nearly every public **URL** (pages and `/api/*` paths) is
   already English; `legacy_urls.py` 301s/rewrites the old Spanish paths for
   external bookmarks and cached clients. The residual Spanish API surface is
   ~10 endpoints, not ~23 (§4).
2. **Some env vars are already mid-rename**, via a per-call `_env2(new, old)`
   / `os.environ.get(new) or os.environ.get(old)` pattern (e.g.
   `costos_ia.py`, `recordatorios.py`, `lista_espera.py`, `puco.py`,
   `busquedas.py`) — there is no central `config.py`; that file does not
   exist. §7 documents the pattern to continue, not invent.

Everything below was read from source, not guessed: `db.py`'s
`_SCHEMA_SQL` (lines 38–692), every `.py`/`.js` file's actual routes/imports,
and a real dependency scan of the 41 target modules.

---

## 1. Python modules

Full root listing has 76 `.py` files. 41 are Spanish-named per the brief;
reading the listing surfaced **3 more** it missed: `chat_agente.py`,
`formato.py`, `voz.py`. `hospital.py` is English-named but domain-wrong
(it's the patient-site router, not literally "hospital" logic) — included
per the brief's own flag.

| Old | New | Why / collision note |
|---|---|---|
| `abuso.py` | `abuse_guard.py` | Shared rate-limit + Turnstile gate for `/api/analyze-order` and `/api/preparaciones`. "abuse.py" alone is too vague next to `ai_orden`/`costos_ia`; keep the `_guard` suffix. |
| `admin_contenido.py` | `admin_content.py` | Pairs with the already-renamed `admin-content.js`. |
| `admin_pacientes.py` | `admin_patients.py` | |
| `admin_preferencias.py` | `admin_preferences.py` | Pairs with the canonical `admin-preferences.mjs` ES module. |
| `admin_turnos.py` | `admin_appointments.py` | Operator manually books a slot for a patient (`/api/admin/appointments`, `/api/admin/appointment-slots`) — distinct from `medico_turnos.py` below. |
| `admin_usuarios.py` | `admin_users.py` | Pairs with canonical `static/platform/admin-users.mjs`; the classic script is retired. |
| `ai_orden.py` | `ai_referral.py` | Reads/analyzes the uploaded medical **referral** (`orden` = referral order, not sort order — see §6). Not `ai_order.py`: keep "referral" explicit since "order" is the ambiguous word. |
| `auth_modo.py` | `auth_mode.py` | Global auth-method toggle (password vs RENAPER). |
| `backups_datos.py` | `data_backups.py` | |
| `busquedas.py` | `searches.py` | Matches already-live `/api/search-log` and the already-aliased `TURNOS_SEARCHES_PER_DAY` env var. |
| `calendario.py` | `calendar_feed.py` | **Never `calendar.py`** — `renaper.py` does `import calendar` (stdlib) TODAY; a local `calendar.py` would shadow it for every module on that import path. Also avoid clashing with the existing unrelated `calendar_busy.py`. |
| `catalogo.py` | `catalog.py` | Admin content overlay (Postgres `catalogo` table merged over `webdata/*.json`). No stdlib/pip collision. |
| `charla.py` | `charla_chat.py`… **see open question #1** | On-site chat-widget backend (`/api/chat`, `/api/chat-transcripts`). "chat.py" collides in spirit with `chat_agente.py`/`chat_tools.py`; needs a disambiguating name — flagged below, don't guess. |
| `cobertura.py` | `health_coverage.py` | **Not `coverage.py`** — that's the near-universal `coverage` pytest/test-coverage package name; a local `coverage.py` risks shadowing it for any tool invoked from the repo root. |
| `comprimir.py` | `compression.py` | Origin→Cloudflare gzip bridge. |
| `confirmacion.py` | `confirmations.py` | Day-before attendance-confirmation replies (matches `solicitudes.confirmacion*` columns). |
| `conocimiento.py` | `knowledge.py` | Chat's reference-content module. |
| `costos_ia.py` | `ai_costs.py` | |
| `cuenta_foto.py` | `account_photo.py` | |
| `cuentas.py` | `accounts.py` | Patient accounts (register/login/dependents). No collision. |
| `datos.py` | `sitedata.py` | **Not `data.py`** — the repo already has a runtime `data/` directory (`/app/data`, PHI volume) AND a `webdata/` static directory. A bare `data.py` sitting next to a `data/` dir is a live PEP 420 namespace-package collision risk (`import data` can resolve to the directory, not the file, depending on sys.path order) — same class of bug as the documented `test/` stdlib shadow. `sitedata.py` reads clearly as "the site's base data accessor" and collides with neither directory. |
| `enfriamiento_ia.py` | `ai_cooldown.py` | |
| `estado_sistema.py` | `system_status.py` | |
| `ia_salud.py` | `ai_health.py` | |
| `lista_espera.py` | `waitlist.py` | The waitlist daemon/engine. |
| `lista_espera_api.py` | `appointment_offer.py` | The offer page + accept/reject API (`/appointment/offer/<token>`) — kept distinct from `waitlist.py` (the engine) since they're genuinely two responsibilities in two files today. |
| `marca.py` | `branding.py` | Editable colors/logo. |
| `medico_fotos.py` | `doctor_photos.py` | |
| `medico_turnos.py` | `doctor_appointments.py` | Owns `/api/admin/day-queue`, `/api/admin/my-appointments`, `/api/admin/doctors/stats`. Broader than "doctor's own appointments" (it's also the admin day-queue-by-doctor view) but this is the closest accurate single name; flag if a better split emerges. |
| `notificaciones.py` | `notifications.py` | |
| `preparacion.py` | `preparation.py` | |
| `prep_modelo.py` | `prep_model.py` | Shared normalizer for `webdata/estudios-preparacion.json`, read from 5 call sites. |
| `ranking_pacientes.py` | `patient_ranking.py` | |
| `recordatorios.py` | `reminders.py` | Matches the already-preferred `TURNOS_REMINDERS_OFF` env var. |
| `reportes.py` | `reports.py` | |
| `reservas.py` | `slot_holds.py` | Matches the already-live `/api/slot-hold` endpoint and the `turno_holds` table (→ `slot_holds`, §3). |
| `sitio.py` | `site_identity.py` | Per the brief's own suggestion; matches its own docstring ("white-label engine, phase 2"). |
| `solicitudes.py` | `booking_requests.py` | **Never `requests.py`** — `requests` is the single most common third-party Python package (HTTP client). Not currently imported anywhere in this repo (verified), but a local `requests.py` would silently shadow it the moment anyone does `pip install requests` and runs a script from repo root — a textbook footgun the brief explicitly warned against. The **API path** `/api/requests` is unaffected (URLs aren't Python identifiers) and does not need to change. |
| `soluciones.py` | `remedies.py` | Translates a rejection cause into the next concrete step. "solutions.py" was considered and rejected — too easily confused with the unrelated `docs/solutions/` compound-memory folder. |
| `tema.py` | `theme.py` | No collision; matches the already-preferred `TURNOS_THEME_DEFAULT`. |
| `webturno.py` | **no rename — keep** | `webturno.com.ar` is a real third-party system name (proper noun), like PUCO/REFEPS/RENAPER. Translating it would misname what it actually is. |
| `chat_agente.py` *(missed by brief)* | `chat_agent.py` | Chat tool-calling core (distinct from `charla.py`, the widget's HTTP surface, and `chat_tools.py`, already English). |
| `formato.py` *(missed by brief)* | `format.py` or `format_utils.py` — **see open question #2** | The one-formatter-per-datum module (phones/emails), parity-tested against `formato.js`. `format.py` reads cleanest and has no *module*-level collision, but any file that does `import format` then calls the builtin `format(x, spec)` bare would have that call silently resolve to the module instead of the builtin inside that file only — low severity (fails loudly with `TypeError: 'module' object is not callable`), but real. Recommend `format_utils.py` unless a grep confirms no file mixes both. |
| `voz.py` *(missed by brief)* | `voice.py` | Chat audio transcription (`/api/chat/transcribe`). |
| `hospital.py` | `patient_site.py` | Per the brief's own flag: it's the patient-facing router (home, `/service/<id>`, `/centers`, `/my-requests`), not "hospital" logic. |

**Confirmed collision, not renamed:** `renaper.py` does `import calendar`
(stdlib) at line 11 today — proof the `calendario.py`→`calendar.py` trap in
the brief was real, not theoretical.

**No rename needed (already English), for completeness:** `admin_account.py`,
`admin_auth.py`, `admin_password_reset.py`, `auth_renaper.py` (RENAPER =
proper noun), `calendar_busy.py`, `chat_tools.py`, `db.py`,
`email_mailgun.py`, `gmail_confirm.py`, `hsi_client.py`, `hsi_resolver.py`,
`hsi_resync.py`, `hsi_sync.py`, `legacy_urls.py`, `mcp_api.py`,
`mcp_guard.py`, `msd_data.py` (MSD = proper noun), `ocr_dni.py`,
`otp_send.py`, `panel_login.py`, `passkeys.py`, `prompts.py`, `puco.py`
(proper noun), `refeps.py` (proper noun), `seo.py`, `server.py` (stays per
CLAUDE.md), `site_auth.py`, `sitemap.py`, `turnstile.py` (proper noun),
`watcher.py`, `whatsapp_chat.py`.

---

## 2. JS files and globals

### Files

| Old | New |
|---|---|
| `admin-cola.js` | `admin-queue.js` |
| `admin-conocimiento.js` | `admin-knowledge.js` |
| `admin-crear-turno.js` | `admin-create-appointment.js` (classic root retired 2026-09-05 → `static/platform/admin-create-appointment.mjs`) |
| `admin-pacientes.js` | `admin-patients.js` |
| `admin-preparaciones.js` | `admin-preparations.js` |
| `admin-sistema.js` | `admin-system.js` |
| `admin-tabla.js` | `admin-table.js` |
| `formato.js` | `format.js` (JS has no builtin-shadow risk; fine as-is even if `formato.py` becomes `format_utils.py` for the reason above — the pair doesn't have to share a stem) |
| `rubricador.js` | `specialty-directory.js` — see §6/open question, do not confuse with `directory.js` |
| `stz-ui.js` | **flag, don't blind-rename** — "STZ" = Sacoturno, the pre-fork brand. This is shared low-level UI plumbing (`STZ.esc`, `STZ.menuGroup`) still used by `rubricador.js`/`app.html`. Functionally it should become e.g. `ui-core.js` / `window.UICore`, but because `sacoturno.css` is explicitly pinned by AGENTS.md ("retain `sacoturno.css` tokens; do not rename the file"), renaming the *sibling* `STZ` global needs an explicit go/no-go — see open question #3. |
| `prep.js` | `prep.mjs` (canonical ES module; classic global retired). Final placement: `static/platform/prep.mjs` (2026-09-06). |
| `tiles.js` | `static/platform/tiles.mjs` (canonical ES renderer; classic global retired) |
| `directory.js` | `static/platform/directory.mjs` (canonical ES renderer; classic global retired) |
| `islands.js` | `static/platform/islands.mjs` (canonical ES bootstrap; classic global retired) |
| `turnstile.js` | `static/platform/turnstile.mjs` (canonical ES client; classic global retired) |
| `passkeys.js` | `static/platform/passkeys.mjs` (canonical WebAuthn client; classic global retired) |

Already-English JS files needing no rename (confirmed by directory listing,
for completeness): `account-form.js`, `admin-account.js`, `admin-charts.js`,
`admin-content.js`, `static/platform/admin-control-fit.mjs`, `admin-my-appointments.js`,
`static/platform/admin-preferences.mjs`, `admin-prompts.js`, `admin-reports.js`,
`admin-roadmap.js`, `admin-theme.js`, `static/platform/admin-users.mjs`, `static/platform/age.mjs`,
`auth-test.js`, `static/platform/calendar-grid.mjs`, `chat-widget.js`, `static/platform/dates.mjs`,
`dependents.js`, `static/platform/directory.mjs`, `static/platform/dni-barcode.mjs`, `static/platform/dni-scan.mjs`, `static/platform/islands.mjs`,
`listing.js`, `my-requests.js`, `nav.js`, `static/platform/passkeys.mjs`,
`photo-zoom.js`, `request-detail.js`, `search.js`, `service.js`, `static/platform/tiles.mjs`,
`static/platform/turnstile.mjs`, `upload-order.js`, `week.js`, `wz-upload.js`.

**`sacoturno.css` — do not rename** (explicit AGENTS.md instruction).

### Globals (verified by grep, not assumed — actual list differs from the brief's)

| Old | New |
|---|---|
| `window.SanaBuscar` | `window.SanaSearch` |
| `window.SanaEdad` | `age.mjs` named exports (`window.SanaAge` retired); final placement: `static/platform/age.mjs` (2026-09-06) |
| `window.SanaFechas` | `static/platform/dates.mjs` named exports (compatibility global retired) |
| `window.SanaHorarios` | `static/platform/hours.mjs` named exports (compatibility global retired) |
| `window.SanaListado` | `window.SanaListing` |
| `window.SanaSemana` | `window.SanaWeek` |
| `window.SanaTabla` | `window.SanaTable` |
| `window.SanaSolicitudes` | `window.SanaRequests` |
| `window.SanaCuenta` | `window.SanaAccount` |
| `window.SanaDependientes` | `window.SanaDependents` |
| `window.SanaDirectorio` | `static/platform/directory.mjs` named `render` export (compatibility global retired) |
| `window.SanaUsuarios` | `window.SanaUsers` |
| `window.SanaPreferencias` | `static/platform/admin-preferences.mjs` named `load`/`get`/`set` exports (compatibility global retired) |
| `window.SanaCrearTurno` | `static/platform/admin-create-appointment.mjs` named `abrir` export (compatibility global retired) |
| `window.SanaPrep` | `prep.mjs` named exports (compatibility global retired); final placement: `static/platform/prep.mjs` (2026-09-06) |
| `window.SanaTiles` | `static/platform/tiles.mjs` named exports (compatibility global retired) |
| `window.SanaIslands` | `static/platform/islands.mjs` named `mount`/`scan`/`update` exports (compatibility global retired) |
| `window.SanaPasskeys` | `static/platform/passkeys.mjs` named WebAuthn exports (compatibility global retired) |
| `window.SanaResumenPaciente` | `static/platform/admin-patient-summary.mjs` named `mount` export (compatibility global retired) |
| `Rubricador` | `SpecialtyDirectory` (or agreed name from open question #1 — must not collide with `SanaDirectorio`→`SanaDirectory`, a *different* component) |
| `SacoNav` | `SacoNav` *(no change — see open question #4: "Saco" is the Sacoturno brand remnant, same family as `stz-ui.js`)* |
| `STZ` | see open question #3 |
| `CURRENT_ROL` | `CURRENT_ROLE` |
| `CURRENT_USUARIO` | `CURRENT_USER` |

Already-English globals, no rename (confirmed present, listed for
completeness — the brief didn't ask about these but an executor grepping
`window.Sana` will hit them): `SanaFormato`
(→ rename to `SanaFormat` if `formato.js`→`format.js`, trivial pairing),
`SanaDniScan`, `SanaCharts`. `SanaControlFit` was retired when
`static/platform/admin-control-fit.mjs` became the canonical self-booting module.

---

## 3. DB schema

Read from `db.py:_SCHEMA_SQL` (lines 38–692) directly — this is the full,
authoritative column list, not a sample.

### Table names

| Old | New |
|---|---|
| `solicitudes` | `booking_requests` |
| `operadores` | `operators` |
| `usuarios` | `users` |
| `dependientes` | `dependents` |
| `sesiones` | `sessions` |
| `catalogo` | `catalog` |
| `asistencia_eventos` | `attendance_events` |
| `admin_preferencias` | `admin_preferences` |
| `admin_reset_tokens` | *(no change — already English)* |
| `admin_account_security` | *(no change — already English)* |
| `passkeys` | *(no change — already English)* |
| `reset_tokens` | *(no change — already English; the `usuarios`/patient counterpart of `admin_reset_tokens`)* |
| `otp_codigos` | `otp_codes` |
| `otp_verificados` | `otp_verified` |
| `refeps_cache` | *(no change — REFEPS is a proper noun)* |
| `lista_espera` | `waitlist` |
| `ofertas_turno` | `appointment_offers` |
| `busquedas` | `searches` |
| `turno_holds` | `slot_holds` |
| `costos_ia` | `ai_costs` |
| `mcp_ai_budget` | *(no change — already English)* |
| `panel_login_eventos` | `login_panel_events` |
| `chat_mensajes` | `chat_messages` |
| `chat_handoffs` | *(no change — already English)* |
| `prompts_ia` | `ai_prompts` |
| `prompts_ia_historial` | `ai_prompts_history` |
| `hsi_outbox`, `hsi_inbox`, `hsi_sync_conflicts`, `hsi_patients`, `hsi_doctor_schedules`, `hsi_appointments`, `hsi_checkpoints` | *(no change — preserve `hsi_*` English tables as-is, per instruction)* |

### Columns, per table

**`solicitudes` → `booking_requests`** (the highest-risk table — 34 columns, touched by nearly every module):

| Old | New |
|---|---|
| `id` | *(no change)* |
| `dni` | *(no change — DNI is the Argentine national ID, a domain proper noun, never translate)* |
| `nombre` | `first_name` |
| `apellido` | `last_name` |
| `nacimiento` | `birth_date` |
| `sexo` | `sex` |
| `contacto` | `contact` |
| `servicio_id` | `service_id` |
| `centro_slug` | `center_slug` |
| `estado` | `status` |
| `orden_path` | `referral_path` |
| `orden_mime` | `referral_mime` |
| `ai_json` | *(no change — already English)* |
| `motivo_rechazo` | `rejection_reason` |
| `turno_dt` | `appointment_at` |
| `hsi_patient_id`, `hsi_appointment_id` | *(no change — already English)* |
| `hsi_estado` | `hsi_status` |
| `asistencia` | `attendance` |
| `asistencia_actualizada_en` | `attendance_updated_at` |
| `asistencia_actualizada_por` | `attendance_updated_by` |
| `sobreturno` | `overbooking` — **not** "walk-in-extra": confirmed by reading `admin_turnos.py`/`medico_turnos.py`, it's staff explicitly squeezing an extra patient into an already-full slot/day, unrelated to the walk-in (`walkin`) service mode |
| `created` | *(no change)* |
| `preferencia_horario` | `time_preference` |
| `solicitante_dni` | `requester_dni` |
| `medico_asignado` | `assigned_doctor` |
| `medico_asignado_id` | `assigned_doctor_id` |
| `recordatorio_dt` | `reminder_at` |
| `resuelta_por` | `resolved_by` |
| `resuelta_en` | `resolved_at` |
| `confirmacion` | `confirmation` |
| `confirmacion_en` | `confirmation_at` |
| `confirmacion_canal` | `confirmation_channel` |
| `cancelada_en` | `cancelled_at` |

**`asistencia_eventos` → `attendance_events`**: `id`·`created` (no change); `solicitud_id`→`request_id`; `dni`(no change); `de_asistencia`→`attendance_from`; `a_asistencia`→`attendance_to`; `operador`→`operator`; `origen`→`source`.

**`operadores` → `operators`**: `id`·`totp_secret`·`activo→active`·`feed_token`(no change)·`ultimo_acceso→last_access`·`email`(no change)·`creado→created`. `usuario`→`username`. `pass_hash`(no change). `rol`→`role`. `medico_id`→`doctor_id`. `centros`→`centers`.

**`admin_preferencias` → `admin_preferences`**: `usuario`→`username`; `clave`→`key`; `valor`→`value`; `updated`(no change).

**`admin_reset_tokens`** (no table rename): `token_hash`(no change); `usuario`→`username`; `expira`→`expires`; `usado`→`used`; `creado`→`created`.

**`admin_account_security`** (no table rename): `usuario`→`username`; `owner_ultimo_acceso`→`owner_last_access`; rest already English.

**`passkeys`** (no table rename): `usuario`→`username`; `nombre`→`name`; `creado`→`created`; `ultimo_uso`→`last_used`; rest already English.

**`usuarios` → `users`**: `dni`(no change); `nombre`→`first_name`; `apellido`→`last_name`; `nacimiento`→`birth_date`; `sexo`→`sex`; `contacto`→`contact`; `email`/`pass_hash`/`created`/`feed_token`/`mcp_token`(no change); `activo`→`active`; `cobertura`→`coverage` (column string, no code-import collision — only the *module* `cobertura.py` needed the `health_coverage.py` dodge); `domicilio`→`address`; `avisos`→`notifications`; `renaper`(no change — proper noun).
  - Inside the `cobertura`/`coverage` jsonb blob: `obra_social`→`union_insurer` (mandatory union/labor health-insurance scheme — legally distinct from `prepaga`), `prepaga`→`private_plan`, `nombre`→`name`, `rnos`(no change — proper-noun registry code), `origen`→`source`.
  - Inside the `domicilio`/`address` jsonb blob: `calle`→`street`, `numero`→`number`, `piso`→`floor`, `localidad`→`city`, `provincia`→`province`, `cp`→`zip_code`, `texto`→`text`.

**`dependientes` → `dependents`**: `id`·`created`·`updated`(no change); `adulto_dni`→`adult_dni`; `dni`(no change); `nombre`→`first_name`; `apellido`→`last_name`; `nacimiento`→`birth_date`; `sexo`→`sex`; `vinculo`→`relationship`; `declaracion_jurada_at`→`affidavit_at` ("declaración jurada" = sworn statement/affidavit, a specific legal term — don't flatten to "declared_at").

**`sesiones` → `sessions`**: `token`/`created`/`last_seen`(no change); `dni`(no change).

**`reset_tokens`** (no table rename, already English): `dni`(no change); `expira`→`expires`; `usado`→`used`; `token`/`created`(no change).

**`catalogo` → `catalog`**: `tipo`→`type`; `id`/`doc`/`created`/`updated`(no change); `borrado`→`deleted`.

**`otp_codigos` → `otp_codes`**: `id`/`code_hash`/`created`(no change); `contacto`→`contact`; `proposito`→`purpose` (values `'registro'|'recuperar'`→`'register'|'recover'` too — this is data, not just schema, flag for a data-migration step); `intentos`→`attempts`; `usado`→`used`; `expira`→`expires`.

**`otp_verificados` → `otp_verified`**: `contacto`→`contact`; `proposito`→`purpose`; `verificado_hasta`→`verified_until`.

**`refeps_cache`** (no table rename, proper noun): `matricula`→`license_number`; `items`/`ok`/`checked`(no change).

**`lista_espera` → `waitlist`**: `id`/`email`/`created`(no change — no `token` column on this table); `solicitud_id`→`request_id`; `dni`(no change); `servicio_id`→`service_id`; `centro_slug`→`center_slug`; `turno_dt`→`appointment_at`; `activo`→`active`.

**`ofertas_turno` → `appointment_offers`**: `id`/`token`/`sent_at`/`expires_at`/`created`(no change); `slot_dt`→`slot_at`; `servicio_id`→`service_id`; `centro_slug`→`center_slug`; `lista_espera_id`→`waitlist_id`; `estado`→`status` (values `'ofrecida'|'aceptada'|'expirada'|'vacante'`→`'offered'|'accepted'|'expired'|'vacant'` — data migration, not just DDL).

**`busquedas` → `searches`**: `id`/`ts`/`q`(no change); `resultados`→`results`; `origen`→`source`.

**`turno_holds` → `slot_holds`**: `id`/`token`/`created`(no change); `expira`→`expires`; `servicio_id`→`service_id`; `dia`→`day`; `hora`→`time`; `solicitud_id`→`request_id`.

**`costos_ia` → `ai_costs`**: `id`/`ts`/`ip`(no change); `operacion`→`operation`; `costo_usd`→`cost_usd`; `usuario_dni`→`user_dni`; `modelo`→`model`; `reconocido`→`recognized`.

**`mcp_ai_budget`** (no table rename, already English): `dia`→`day`; `usos`→`uses`; `actualizado`→`updated`.

**`panel_login_eventos` → `login_panel_events`**: `id`/`ts`/`sid`/`tab`/`item`/`ms`(no change); `variante`→`variant`; `evento`→`event`.

**`chat_mensajes` → `chat_messages`**: `id`/`created`(no change); `canal`→`channel`; `clave`→`key`; `rol`→`role`; `contenido`→`content`.

**`chat_handoffs`** (no table rename, already English): `clave_web`→`web_key`; `creado`→`created`; `usado`→`used`; `usado_tel`→`used_phone`; `usado_en`→`used_at`; `code`/`expira→expires`(no change to name, translate value).

**`prompts_ia` → `ai_prompts`**: `clave`→`key`; `texto_en`→`text_en` (Alembic `0031_ai_prompts_text_en`, 2026-10-25; first left as-is, then renamed so the table stops mixing languages — the API keeps answering `texto_en`/`texto_es`); `texto_es`→`text_es`; `editado`→`edited`; `actualizado`→`updated`; `actualizado_por`→`updated_by`.

**`prompts_ia_historial` → `ai_prompts_history`**: same column treatment as `ai_prompts` plus `id`(no change).

**Indexes**: every index name embeds the old table/column name (e.g.
`solicitudes_estado_created_idx`, `usuarios_contacto_tel_idx`,
`lista_espera_cola_idx`) — these must be renamed in lockstep with their
table, they are not a separate pass. `lista_espera_cola_idx` in particular
mixes two vocab words (`lista_espera`=waitlist, `cola`=queue — the index is
about the waitlist's own ordering, not the admin appointment queue) →
`waitlist_created_idx` reads more accurately than a literal
`waitlist_queue_idx`, which would misleadingly suggest it's the same "queue"
as `admin-cola.js`.

---

## 4. API paths

**Correction to the brief: there are not ~23 live Spanish endpoints.** Only
these are still Spanish, verified by grepping every `path ==` / route
literal across every `.py` file that registers a route (not just the 8 the
brief named) and cross-checking against `legacy_urls.py`'s rewrite tables to
rule out already-aliased dead code:

| Old (live) | New | Notes |
|---|---|---|
| `GET /api/preparaciones` | `GET /api/preparation-studies` | `preparacion.py`. Careful: this is a **different, still-live** endpoint from the already-renamed `/upload-order`/`/api/preparacion`(→`/api/analyze-order`) flow — don't conflate them. |
| `GET /api/especialidades` | `GET /api/specialties` | `server.py:3006`. **Not** in `legacy_urls.py`'s rewrite tables — genuinely live (called by `rubricador.js`'s `fetchData()`), unlike the dead-code case below. Needs a new `legacy_urls.py` entry when renamed. |
| `POST /api/admin/hsi-sync/conflictos` | `POST /api/admin/hsi-sync/conflicts` | `solicitudes.py`. |
| `GET /api/admin/reports/resenas` | `GET /api/admin/reports/reviews` | `reportes.py` + mirrored in `admin-reports.js` and `admin.html` — all three must change together. |
| `GET /api/admin/reports/recupero` + `.csv` | `GET /api/admin/reports/cost-recovery` + `.csv` | Same 3-file mirroring requirement. "recupero" = cost recovery / reimbursement tracking, confirmed by report content, not literal "recovery". |
| `GET /api/admin/reports/estadistica` + `.csv` | `GET /api/admin/reports/statistics` + `.csv` | Same 3-file mirroring requirement. |
| `GET /api/admin/reports/capacidad` | `GET /api/admin/reports/capacity` | Same 3-file mirroring requirement. |
| `GET /api/admin/reports/sumar` + `.csv` | **no change** | SUMAR is the actual name of the Argentine national health-financing program being reported on — a proper noun, translating it would misname the report. |
| `POST /api/puco` | **no change** | PUCO (Padrón Único de Coberturas) is a proper noun — the national coverage registry this endpoint queries. |

**Landmine — dead code, do not treat as live:** `server.py:2993` still has
`if path == "/api/medicos":` and `path == "/api/especialidades"` sits right
next to genuinely dead branches (`/medicos`, `/clinicas`, `/obras-sociales`,
`/medico/`, `/clinica/`, `/cobertura/` at lines 2940–3140). `/api/medicos` **is**
shadowed by `legacy_urls.py`'s `_API_EXACT` rewrite (`/api/medicos`→`/api/doctors`,
applied before `server.py` ever sees the request) — that branch is
unreachable. `/api/especialidades` is **not** in that rewrite table, so it
*is* reachable, unlike its neighbors. An executor must check
`legacy_urls.py`'s tables before assuming any `server.py` route literal is
live — grepping route strings alone will misclassify several of these.

**Dated correction (2026-08-18):** the line numbers above (2940–3140) and the
route list are both stale now. The server.py retirement series has since
DELETED the dead dispatch blocks this landmine warned about rather than
leaving them dormant: `/cobertura/<slug>` + `/obras-sociales` (round 1),
`/medicos` + `/medico/<slug>` (round 3), and `/clinicas` + `/clinica/<slug>`
(this slice) no longer exist in server.py at all — there is nothing left to
misclassify for those six. `/api/medicos` (still unreachable, still dead via
the `legacy_urls.py` shadow above) was deliberately left alone; see
`docs/roadmap/server-retirement-slice4-clinicas.md` §7.

All other `/api/*` paths found by an exhaustive grep (`cuentas.py`,
`solicitudes.py`, `medico_turnos.py`, `admin_turnos.py`, `preparacion.py`,
`cuenta_foto.py`, `puco.py`, `charla.py`, plus every other root `.py`) are
**already English** — confirmed by direct inspection, not inference from
`legacy_urls.py`'s existence. `otp_send.py`'s `/api/sendText` and
`hsi_client.py`/`hsi_sync.py`'s `/api/auth` are **not ours** — they're paths
on external systems (WAHA, HSI) and must never be touched.

---

## 5. CSS class families

| Old prefix | New prefix | Where | Notes |
|---|---|---|---|
| `cola-*` | `queue-*` | `admin-cola.js` (all ~45 classes: `.cola-ahora`, `.cola-tabla`, `.cola-asis-*`, `.cola-grupo-*`, `.cola-orden-*`, `.cola-sobreturno`, etc.), one stray `.cola-serv-filtro` in `admin-content.js` | `.cola-orden-*` classes are about the **medical referral** attached to a queued patient, not sort order — rename to `.queue-referral-*`, not `.queue-order-*` (same ambiguity as §6). |
| `pac-*` | `pat-*` | `admin.html` (`.pac-cob-*`, `.pac-dep-*`, `.pac-dni`, `.pac-nombre`, `.pac-cuenta`, etc.), `admin-pacientes.js` | "pac" = paciente (3 letters) → "pat" = patient (3 letters), preserves the abbreviation length/feel. |
| `prep-*` | *(no change)* | `upload-order.js` (`.prep-cards`, `.prep-ayuno`, `.prep-bullets`, etc.) | Already English abbreviation for "preparation" — leave as-is. |
| `mtc-*` and `ms-prep-*` | `req-*` and `req-prep-*` | both in `my-requests.js` only | These are two prefix families used on the **same page** (My requests): `mtc-*` is card chrome, `ms-prep-*` is the preparation sub-block inside a request's detail. Unify both under one `req-` namespace rather than transliterating each abbreviation separately (`mtc`/`ms` don't have a clean 1:1 English abbreviation and aren't used anywhere else). |
| `.ficha` | `.record-card` | `app.html` only, single class | "ficha" = index/record card (insurance/coverage summary panel). Single low-usage class, safe to fully spell out rather than abbreviate. |
| `.hsi-titulo` | `.hsi-title` | `admin.html`, single class | |
| `.ct-hora`, `.ct-horas`, `.ct-hora--sobre` | `.slot-time`, `.slot-times`, `.slot-time--over` | `admin-crear-turno.js` | "ct" = crear-turno; these classes style the time-slot pills specifically, so name them for what they show (`slot-time`) rather than transliterating the abbreviation. `--sobre` = "sobreturno" (overbooking, see §6) → `--over`. |

Class-family renames must be done as a **whole-string match on the prefix**,
not word-by-word — e.g. `cola-grupo-warn` → `queue-group-warn` (translate
every segment), not just the first token.

---

## 6. Cross-cutting vocabulary

Use this table for every module/column/class/string translation so the same
Spanish word doesn't get 3 different English spellings across files.

| Spanish | English | Notes |
|---|---|---|
| turno | appointment | |
| solicitud | request (booking request) | Table/module → `booking_requests`/`booking_requests.py`, **never bare `requests`** (Python HTTP-library collision, §1). |
| médico | doctor | |
| centro | center | |
| cola | queue | |
| **orden** | **ambiguous — read context before translating** | Two unrelated meanings coexist: (1) the **medical referral document** (`orden_path`, `orden_mime`, `/api/analyze-order`, `ai_orden.py`, `.cola-orden-*`) → **`referral`**; (2) **display/sort order** (`listing.js:ORDEN`, `COL_ORDER`, `AGENTS.md`'s "canonical listing-column registry") → **`order`** (as in sort order — this one already reads fine in English and is mostly already-English code). Never use bare "order" for the referral meaning — that's the collision the brief warned about, and it's real: both meanings are live in the same codebase (`solicitudes.py` and `listing.js` respectively). |
| preparación | preparation | |
| conocimiento | knowledge | |
| cobertura | coverage (as data/column); `health_coverage.py` (as module, to dodge the `coverage` pip package) | |
| obra social | union/labor health insurer | Distinct from `prepaga`; see `usuarios.cobertura` jsonb note in §3. Don't collapse both into one generic "insurer". |
| prepaga | private (prepaid) health plan | |
| **sobreturno** | **overbooking** | Brief suggested "walk-in-extra" — **confirmed wrong** by reading `admin_turnos.py`/`medico_turnos.py`/`db.py`: it's staff explicitly booking an extra patient into an already-full slot/day (a capacity exception), unrelated to the `walkin` service mode (no-appointment-needed). Do not conflate with `walkin`. |
| asistencia | attendance | |
| lista de espera | waitlist | |
| oferta | offer | |
| búsqueda | search | |
| usuario | user (patient accounts) / username (as a login-identifier column, e.g. `operadores.usuario`) | Context-dependent — see §3 column notes; don't blindly map every `usuario` column to `user_id` or similar. |
| operador | operator | |
| paciente | patient | |
| sesión | session | |
| novedades | news | Already renamed at the URL level (`/novedades`→`/news`); `hospital.py:_NOVEDADES` internal name still Spanish, see §8 landmine. |
| talleres | workshops | |
| dependiente | dependent | |
| declaración jurada | affidavit / sworn statement | Don't flatten to "declared" — it's a specific legal term (`dependientes.declaracion_jurada_at`). |
| matrícula | license number (professional license, REFEPS) | |
| nacimiento | birth date | |
| domicilio | address | |
| vínculo | relationship (to a dependent) | |
| motivo de rechazo | rejection reason | |
| ausente / presente | absent / present | (`asistencia` CHECK values — a data migration, not just a column rename) |

---

## 7. Env vars

**Keep every `TURNOS_*` name working as-is for now** — installed-base
compat, per the brief. No central `config.py` exists; do not invent one.

**What actually exists today** (verified by grep, corrects the brief's
assumption of a single "legacy mechanism"): a **per-call-site dual-read
pattern**, `os.environ.get("TURNOS_<ENGLISH>") or os.environ.get("TURNOS_<SPANISH>")`,
sometimes wrapped in a tiny local helper (`_env2(new, old)` in
`recordatorios.py`/`lista_espera.py`, `_int_env(new, old, default)` in
`costos_ia.py`). Already-aliased pairs confirmed live: `TURNOS_REMINDERS_OFF`
(ex `TURNOS_RECORDATORIOS_OFF`), `TURNOS_REMINDER_INTERVAL_MIN` (ex
`TURNOS_RECORDATORIO_INTERVAL_MIN`), `TURNOS_WAITLIST_OFF`/`_INTERVAL_MIN`
(ex `TURNOS_LISTA_ESPERA_*`), `TURNOS_AI_ABUSE_FAILS`/`_MIN`/`_OFF` (ex
`TURNOS_IA_ABUSO_*`), `TURNOS_AI_FORCE` (ex `TURNOS_IA_FORZAR`),
`TURNOS_AI_HEALTH_OFF` (ex `TURNOS_IA_SALUD_OFF`), `TURNOS_SEARCHES_PER_DAY`
(ex `TURNOS_BUSQUEDAS_PER_DAY`), `TURNOS_THEME_DEFAULT` (ex
`TURNOS_TEMA_DEFAULT`), `TURNOS_SISA_USER` (ex `TURNOS_SISA_USUARIO`).

**Future work (not blocking, continue the established pattern):** these are
still Spanish-only, no English alias yet —
`TURNOS_RANKING_DUPLICADO_DIAS`/`_PRIOR`/`_PRIOR_PESO`/`_SEMIVIDA_DIAS`/`_VENTANA_DIAS`,
`TURNOS_COSTO_HORA_OPERADOR`, `TURNOS_DOLAR_BLUE_REPORTE`, `TURNOS_DOMINIO`,
`TURNOS_CONTEXTO`, `TURNOS_WA_NUMERO`, `TURNOS_WA_HANDOFF_PER_HOUR`,
`TURNOS_PUCO_GWT_PERM_DISP`. When these get English aliases, follow the exact
same `_env2`/`os.environ.get(new) or os.environ.get(old)` pattern already in
use — don't introduce a new mechanism for consistency's sake.

---

## 8. Execution notes

### Suggested order (real dependency scan, not guessed)

Computed by grepping every target module's top-level `import`/`from` lines
against the other 40 target modules. Presented as dependency tiers — within
a tier, order doesn't matter; a module must not be renamed (import sites
updated) before everything in its own row's dependencies is done.

- **Tier 0** (no target-module deps — rename independently, any order):
  `cobertura`, `comprimir`, `confirmacion`, `datos`, `prep_modelo`, `tema`,
  `auth_modo`, `enfriamiento_ia`, `marca`, `backups_datos`,
  `ranking_pacientes`, `recordatorios`, `cuenta_foto`, `formato`,
  `soluciones`, `webturno` (no rename, but touch its imports if any target
  renames land inside it — it had none), `charla`, `ai_orden`, `voz`,
  `estado_sistema`, `chat_agente`, `busquedas`, `abuso`, `lista_espera`,
  `admin_usuarios`, `admin_preferencias`.
- **Tier 1** (depend only on Tier 0): `catalogo` (→ `datos`, `prep_modelo`),
  `sitio` (→ `comprimir`, `datos`, `tema`), `lista_espera_api` (→
  `lista_espera`), `costos_ia` (→ `ai_orden`), `solicitudes` (→ `abuso`,
  `ai_orden`, `datos`, `tema` — note it does **not** import `hospital` or
  `cuentas` despite feeling "central").
- **Tier 2** (depend on Tier 1): `calendario` (→ `datos`, `sitio`),
  `conocimiento` (→ `catalogo`), `medico_fotos` (→ `catalogo`),
  `admin_contenido` (→ `catalogo`), `admin_turnos` (→ `catalogo`), `cuentas`
  (→ `formato`, `sitio`, `tema`), `medico_turnos` (→ `sitio`).
- **Tier 3** (depend on Tier 2): `hospital` (→ `comprimir`, `datos`,
  `formato`, `medico_fotos`, `msd_data`, `prep_modelo`, `sitio`, `tema`),
  `admin_pacientes` (→ `cuentas`).
- **Tier 4** (depend on Tier 3): `preparacion` (→ `ai_orden`, `datos`,
  `hospital`, `prep_modelo`, `sitio`), `reportes` (→ `datos`, `hospital`,
  `medico_turnos`), `notificaciones` (→ `prep_modelo`, could run in Tier 1
  too — placed here only because it's low-risk to do last).

**Caveat:** this is a static top-level-import scan; it will miss lazy
imports (the codebase uses these deliberately — e.g. `hsi_client` is
imported lazily inside `solicitudes._push_hsi` specifically so a missing
module degrades gracefully). A module rename still requires a **repo-wide**
grep-replace of every reference (import statements AND lazy
`import x`-inside-a-function AND `x.foo(...)` attribute access), done
atomically per module, regardless of tier — the tiers just make it safe to
do that atomically one module at a time instead of all 41 at once, and make
a broken test suite easy to bisect.

### The `import X as Y` transition trick

For any module where an all-at-once rename is too risky (e.g. `solicitudes.py`,
imported by many others and itself route-critical), land the rename in two
steps instead of one: (1) `git mv old.py new.py`, add
`import new_module_name as old_module_name` — or, cleaner given Python's
actual mechanics, keep `old.py` as a 2-line shim
(`from new_module_name import *` plus `sys.modules['old_module_name'] = ...`
if anything does `import old_module_name` directly) — then update call sites
incrementally; (2) delete the shim once grep confirms zero remaining
references to the old name. Only worth doing for the highest fan-in modules
(`solicitudes`/`booking_requests`, `catalogo`/`catalog`, `sitio`/`site_identity`,
`tema`/`theme`, `datos`/`sitedata`); everything else is low-fan-in enough to
rename in one atomic commit.

### Test-file rename pairs

Direct 1:1 pairs (module name embedded in test filename) — 33 confirmed by
listing `test/test_*.py`:

`test_abuso.py`→`test_abuse_guard.py`, `test_admin_contenido.py`→`test_admin_content.py`,
`test_admin_pacientes.py`→`test_admin_patients.py`, `test_admin_turnos.py`→`test_admin_appointments.py`,
`test_admin_usuarios.py`→`test_admin_users.py`, `test_auth_modo.py`→`test_auth_mode.py`,
`test_backups_datos.py`→`test_data_backups.py`, `test_busquedas.py`→`test_searches.py`,
`test_calendario.py`→`test_calendar_feed.py`, `test_catalogo.py`→`test_catalog.py`,
`test_charla.py`→(pairs with `charla.py`'s final name, open question #1),
`test_chat_agente.py`→`test_chat_agent.py`, `test_cobertura.py`→`test_health_coverage.py`,
`test_confirmacion.py`→`test_confirmations.py`, `test_conocimiento.py`→`test_knowledge.py`,
`test_costos_ia.py`→`test_ai_costs.py`, `test_cuenta_foto.py`→`test_account_photo.py`,
`test_cuentas.py`→`test_accounts.py`, `test_enfriamiento_ia.py`→`test_ai_cooldown.py`,
`test_estado_sistema.py`→`test_system_status.py`, `test_ia_salud.py`→`test_ai_health.py`,
`test_lista_espera.py`→`test_waitlist.py`, `test_marca.py`→`test_branding.py`,
`test_medico_fotos.py`→`test_doctor_photos.py`, `test_medico_turnos.py`→`test_doctor_appointments.py`,
`test_notificaciones.py`→`test_notifications.py`, `test_preparacion.py`→`test_preparation.py`,
`test_puco.py`→(no change), `test_ranking_pacientes.py`→`test_patient_ranking.py`,
`test_reportes.py`, `test_reportes_insights.py`, `test_reportes_nuevos.py`→`test_reports*.py`,
`test_reservas.py`→`test_slot_holds.py`, `test_sitio.py`→`test_site_identity.py`,
`test_solicitudes.py`→`test_booking_requests.py`, `test_solicitud_detalle.py`→`test_booking_request_detail.py`,
`test_soluciones.py`→`test_remedies.py`, `test_tema.py`→`test_theme.py`, `test_voz.py`→`test_voice.py`.

**Feature-named tests (not 1:1 with a module, still Spanish, rename by
content not by mechanical mapping):** `test_agenda_avanzada.py`,
`test_agenda_consultorio.py`, `test_apertura_mismo_dia.py`,
`test_cola_dia.py`, `test_demo_agenda.py`, `test_ejemplos_busqueda.py`,
`test_estudios_tabla.py`, `test_fecha_amigable.py`, `test_no_ofrecidos.py`,
`test_orden_paginas.py`, `test_centros_tiles.py`, `test_novedades.py`,
`test_invariantes_datos.py`, `test_datos_dir.py`,
`test_scrape_conocimiento.py` — each needs its own read to name accurately
(e.g. `test_orden_paginas.py` is almost certainly about §6's *sort-order*
meaning of "orden", not the referral meaning — verify per-file, don't
assume).

### Known landmines

1. **Stdlib `test` package shadowing** (already documented in CLAUDE.md):
   `python3 -m unittest test.test_x` silently resolves to the stdlib `test`
   package, not the local `test/` directory. Always use `discover`. Not
   caused by this rename, but any executor running tests mid-rename will hit
   it if they forget.
2. **`hospital.py`'s `_NOVEDADES` is frozen patient-facing history.** Per
   `novedades-changelog` skill, this list is curated Spanish copy shown to
   patients — it must **never** be touched by an identifier-rename pass. Its
   *variable name* can become `_NEWS` when `hospital.py`→`patient_site.py`,
   but its *string content* (Spanish changelog entries) is product copy, out
   of scope entirely — the brief's own AGENTS.md rule (patient-facing copy
   stays Spanish) applies.
3. **`legacy_urls.py` must keep every OLD (Spanish) URL string working.**
   This file is the one place in the repo where Spanish string literals are
   the *point*, not tech debt — don't "clean up" its `_PAGES_EXACT`/`_API_EXACT`
   dicts during this pass. It needs **additions** as new endpoints get
   renamed (`/api/preparaciones`, `/api/especialidades`, the 3 `reportes.py`
   subpaths, `/api/admin/hsi-sync/conflictos` — none of these are in its
   tables today and all need a new entry when renamed), never removals.
4. **`server.py` route literals include both live and dead Spanish code in
   the same block** (§4) — verify against `legacy_urls.py`'s rewrite tables
   before assuming any given `path == "..."` line in `server.py` is reachable.
   Renaming a dead branch is harmless but wasted effort; deleting one that
   turns out to be live (misjudged from a stale mental model of "everything
   was already renamed in 2026-07-17") is a regression.
5. **Data migrations hide inside "just a rename".** Several CHECK-constrained
   text columns store Spanish *values*, not just Spanish *names* —
   `solicitudes.estado` (`'pendiente'|'aprobada'|...`), `asistencia`
   (`'presente'|'ausente'`), `confirmacion` (`'si'|'no'`), `otp_codigos.proposito`
   (`'registro'|'recuperar'`), `ofertas_turno.estado`
   (`'ofrecida'|'aceptada'|'expirada'|'vacante'`). Renaming the *column* is a
   DDL change; translating the *stored values* is a data migration touching
   every row and every CHECK constraint and every string comparison in code
   (`if estado == "pendiente"`). These are two different, sequenced pieces of
   work — don't let "rename the column" silently expand into "and also
   migrate 41 tables' worth of data" without flagging it as its own step.
6. **CSS class renames need whole-prefix + whole-suffix translation**, not
   just the prefix token (§5 example: `cola-grupo-warn`→`queue-group-warn`).
   A grep-replace that only swaps `cola-`→`queue-` and leaves `-grupo-`
   untranslated produces half-English half-Spanish classes, which is worse
   than not renaming at all (looks finished, isn't).

---

## Naming decisions worth flagging (genuine ambiguities — RESOLVED 2026-07-29)

**Decisions (binding for executors; rationale in each item below):**
1. `charla.py` → **`chat_api.py`**. The chat set reads as: `chat_api.py` (HTTP surface) / `chat_agent.py` (tool-calling engine) / `chat_tools.py` (tools).
2. `rubricador.js` → **`specialty-browser.js`**, global `Rubricador` → **`SpecialtyBrowser`**. Avoids a second "directory" name; `directory.js` keeps sole ownership of that word.
3. `stz-ui.js` / `window.STZ` **IS renamed in this pass** — only the `sacoturno.css` FILE is pinned by AGENTS.md; behavior files are in scope.
4. `window.SacoNav` → **`SiteNav`**. De-brand all Saco/STZ remnants in JS globals consistently.
5. `medico_turnos.py` → **`doctor_appointments.py`** accepted as-is; the future split of its admin day-queue half is out of scope and noted, not solved.

Original discussion:

1. **`charla.py`'s new name.** It's the on-site chat-widget's HTTP surface
   (`/api/chat`, `/api/chat-transcripts`), sitting alongside `chat_agent.py`
   (the tool-calling core, formerly `chat_agente.py`) and `chat_tools.py`
   (already English). `chat.py` alone is too generic given two siblings
   already use the `chat_` prefix; something like `chat_api.py` or
   `chat_widget_api.py` both work but change the file's apparent scope
   slightly. Didn't pick one — needs a decision on how the three chat
   modules should read as a set.
2. **`rubricador.js`'s new name and whether it collides conceptually with
   `directory.js`/`SanaDirectorio`→`SanaDirectory`.** Proposed
   `specialty-directory.js`/`SpecialtyDirectory`, but `directory.js` already
   owns "the doctor directory" as a concept — two files both saying
   "directory" in their name for genuinely different UI components (one is
   the specialty picker used on the home page and inside the account area,
   the other is the doctor listing) risks executors conflating them later.
   An alternative that avoids the word entirely: `specialty-browser.js`.
3. **Whether `stz-ui.js`/`window.STZ` gets renamed at all in this pass.**
   `sacoturno.css` is explicitly pinned (AGENTS.md: "retain `sacoturno.css`
   tokens; do not rename the file") because it's a CSS token contract other
   files depend on by literal filename. `stz-ui.js` is different — it's
   behavior, not tokens — but it shares the "STZ"/Sacoturno-brand naming and
   an executor might reasonably assume the same do-not-touch instruction
   covers it. Recommend treating it as in-scope for renaming (only the CSS
   *file* is pinned) but flagging this explicitly since it's exactly the
   kind of thing a mechanical executor would get wrong by over-applying the
   `sacoturno.css` exception.
4. **`SacoNav`/`nav.js`.** Same brand-remnant question as #3 — "Saco" =
   Sacoturno. `nav.js` itself is already English-named; only the global
   `window.SacoNav` carries the old brand. Low risk either way (it's an
   internal global, not user-facing), but worth a single explicit decision
   applied consistently rather than 41 independent executors guessing
   differently on the same question.
5. **`medico_turnos.py`'s scope vs. its new name.** It owns both "a doctor's
   own appointments" (role-scoped) and "the admin's day-queue grouped by
   doctor" (admin-scoped, broader) under one file. `doctor_appointments.py`
   reads correctly for the first responsibility and only approximately for
   the second. Not blocking — just flagging that a future split (not part of
   this rename) might land under two different English names, so don't be
   surprised if `doctor_appointments.py` later needs to shed its day-queue
   half into something like `admin_appointments.py` (careful: that name is
   already claimed by `admin_turnos.py`'s rename in §1 — a genuine future
   collision to keep in mind, not solved here).
