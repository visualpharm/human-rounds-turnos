# Architecture docs: the full index

Read the file for the area you are touching before non-trivial work; `CLAUDE.md` keeps a short form of this table. Anything marked **do not relitigate** in these files is settled.

| Area | File |
|---|---|
| Repository layout: what lives where, what the 2026-11 move changed, carrying an old branch across | [LAYOUT.md](../LAYOUT.md) |
| Routing, module ownership, what server.py may NOT do | [routing-y-modulos.md](routing-y-modulos.md) |
| Admin login, roles, sessions, passkeys, 2FA, Turnstile, «Sistema», Hoja de ruta, login gate panel | [admin-panel-y-auth.md](admin-panel-y-auth.md) |
| Clinical access log (`app/core/access_log.py`, `clinical_access_log`), own-patient rule for doctors, «Quién abrió sus datos», app DB role without schema rights (`scripts/db_app_role.py`, `TURNOS_DB_MIGRATION_URL`) | [registro-de-accesos.md](registro-de-accesos.md) |
| Data-subject rights (Ley 25.326 arts. 14-16): the personal-data registry (`app/core/personal_data.py`), the read-only export of one person's data (`app/core/subject_export.py`, `scripts/subject_export.py`, admin `data-export`), the ledger of requests with their 10-calendar / 5-working-day deadlines (`app/core/subject_requests.py`), erase / anonymise / block (`app/core/subject_erase.py`, dry-run by default), the same erasures applied again after a restore (`scripts/reapply_erasures.py`), the questions for counsel | [derechos-de-los-titulares.md](derechos-de-los-titulares.md) |
| Patient wizard, service pages, slot holds, Confirmá, «Orden de derivación» flow | [wizard-y-servicio.md](wizard-y-servicio.md) |
| Accounts, DNI photo sign-up, PUCO/coverage, age routing | [cuentas-y-cobertura.md](cuentas-y-cobertura.md) |
| Admin queue, «Pacientes de hoy», doctor agenda/calendar, Pacientes, table engines | [admin-cola-y-agenda.md](admin-cola-y-agenda.md) |
| Catalog overlay, data package (`sitedata.py`/`site_identity.py`), services/centers/doctors data, walk-in rule | [catalogo-y-datos.md](catalogo-y-datos.md) |
| Layered data packs (`packs/`, `data_packs.py`), pack-chain resolution, override vs merge, legacy `TURNOS_WEBDATA_DIR` mode | [data-packs.md](data-packs.md) |
| Legal texts (terms + privacy, `legal.py`) and the PII redaction (`pii.py`) applied before any external AI call | [legal.md](legal.md) |
| Multi-language (`i18n.py`, `translate.py`, `pack_l10n.py`, `translate_warmup.py`): UI-string catalogs, `{{t.*}}` pipeline, request-scoped language, switcher modes, which pack fields are translatable, the cache-filling sweep | [i18n.md](i18n.md) |
| Settings admin section (`settings_admin.py`/`admin-settings.js`): Location/Languages/API keys, secret masking, `TURNOS_CONFIG_LOCK`, `ai_client.py`'s OpenRouter backup-key failover, Sistema clusters ↔ Settings deep links | [settings.md](settings.md) |
| First-run installer wizard (`setup.py`): fresh-install gate, `/setup` steps, pack scaffolding, why the wizard state never touches the DB before the final commit | [setup-wizard.md](setup-wizard.md) |
| App modules (`app/modules/<id>/module.json`): what `turnos`, `intake`, and `chat` own, runtime enable/disable (`app_modules.py`, `manage.py mod`), platform-core boundary, and how another vertical module plugs in | [modules.md](../modules.md) |
| Entrevista previa (`app/modules/intake/`): Pre-Consult AI chat, triage + red-flag floor, doctor confirm/compose/printables, the 3 editable prompts, QR door entry | [entrevista-previa.md](entrevista-previa.md) |
| Resumen del paciente (`chart_summary.py`): pre-visit AI chart summary, source tiers, code-extracted trend series, pre-generation daemon, alepo/marqui demo fakes | [resumen-paciente.md](resumen-paciente.md) |
| Lab results in the patient account (`lab_results.py`/`lab_analysis.py`): per-report AI reading + cache, staff-editable sensitivity filter, list + Evolución views, cost model, robustness evaluation | [laboratorio-paciente.md](laboratorio-paciente.md) |
| Connectors (`app/connectors/<id>/`): eight capability ABCs (incl. `email_transport`: Mailgun default, Mailjet selectable) implemented across fifteen connector directories, degradation contract, retired Phase 6b compat shims, `manage.py conn` CLI, and how to write one for a new country | [connectors.md](../connectors.md) |
| Database schema as code and query style: `app/core/schema_model.py` (SQLAlchemy Core tables), autogenerate as a hint, `app/core/sqlcore.py` (Core statements on the pooled psycopg cursor), the conversion pattern and order | [sqlalchemy-core.md](sqlalchemy-core.md) |
| Listing column registry, ordering/colors, search & stemming, nav, talleres, UI tokens, design-loop | [listados-y-busqueda.md](listados-y-busqueda.md) |
| Every admin write and the e2e that proves «save → cold reload → shown» (TASK-3 audit, what is still OPEN and why) | [admin-forms-audit.md](admin-forms-audit.md) |
| Claude Design mockup icons (Icons8 sf-regular, pixel-perfect sizes) + the two live themes (`data-theme="pinamar"` vs `"humanrounds"`, their separate Claude Design projects) | [design-icons-y-temas.md](design-icons-y-temas.md) |
| Order AI reading, editable prompts, rejection remedies, abuse cooldowns | [ia-y-ordenes.md](ia-y-ordenes.md) |
| Booking chat engine, on-site widget, WhatsApp (WAHA + Meta), handoff code | [chat-y-whatsapp.md](chat-y-whatsapp.md) |
| HSI push, resync daemon, conflict resolver | [hsi.md](hsi.md) |
| Chrome extension that loads «Pacientes de hoy» turnos into HSI's screen (fill-don't-save, earned «guardar solo», test HSI by default) | [extensions/hsi-humanizador/README.md](../../extensions/hsi-humanizador/README.md) |
| Patient emails, reminders, cancellation, waitlist | [notificaciones-y-lista-espera.md](notificaciones-y-lista-espera.md) |
| Reports (story-driven), recupero/SUMAR/estadística/capacidad, admissions ranking | [reportes.md](reportes.md) |
| Coolify deploy, cache busting, env renames | [deploy.md](deploy.md) |
| Foreign keys (`core.db_accounts.FOREIGN_KEYS`, Alembic 0032): the database refuses to delete a parent with children, what is deliberately NOT a key, the NOT VALID → VALIDATE safety, `validate_foreign_keys.py` | [claves-foraneas.md](claves-foraneas.md) |
