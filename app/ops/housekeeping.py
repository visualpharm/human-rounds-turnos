"""housekeeping.py — periodic DB hygiene sweep (audit 2026-08-06).

Same daemon-thread pattern as reminders.py: `tick()` is the pure,
testable sweep (no threading.Event/sleep inside it); `start()` just
schedules it on an interval. All best-effort — a failed purge never takes
the server down, and every pass is bounded by LIMIT so a big backlog wears
down over several ticks instead of locking a table in one go.

What it purges, and why each is safe to lose (detail in each db.py
function's own docstring):
  - `sessions` idle >30d (TURNOS_HOUSEKEEPING_SESSION_IDLE_DAYS) — the login
    cookie has no server-side expiry; this IS that expiry, applied lazily.
  - `searches` >180d (TURNOS_HOUSEKEEPING_SEARCH_DAYS) — raw search-box
    analytics, no value once stale.
  - `login_panel_events` >180d (TURNOS_HOUSEKEEPING_LOGIN_PANEL_DAYS) — the
    "Humor de guardia" A/B log, same shape as searches.
  - `ai_costs.ip` >180d (TURNOS_HOUSEKEEPING_AI_COSTS_IP_DAYS) — the IP of an
    AI call goes NULL; the row (cost, operation, model) stays for the reports.
  - `chat_messages` >90d (TURNOS_HOUSEKEEPING_CHAT_DAYS) whose conversation
    already has a `chat_summaries`/`chat_compactions` row proving it was
    captured — see db_slots.purge_summarized_chat_messages for the conservative
    matching rule (a conversation without that row is never touched).
  - `clinical_access_log` >3650d (TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS, never
    under 365) — who opened which patient's data (core/access_log.py). Not
    safe to lose: kept ten years by default, and the table's own trigger
    refuses to delete anything younger than a year.
  - `patient_lab_uploads.archivo` (the uploaded lab FILE, bytea) older than
    `lab_upload_retention_days()` — the file bytes go NULL, the transcription
    and the row stay (schema review №11). Follows the referral files' window
    unless TURNOS_LAB_UPLOAD_RETENTION_DAYS says otherwise; 0 = never.

Referral FILES (data/ordenes/) are a separate concern with their own
daemon — see booking_referrals.sweep_referrals/start_referral_sweep: that
one deletes disk files driven by booking_requests.status, not a table row
count, and lives next to the rest of the referral file handling.

Disk alert (added 2026-08-06): each pass also checks the data volume's
free space via system_status.medir_disco() — the SAME measurement the
admin «Sistema» panel shows, imported rather than duplicated. Below
TURNOS_DISK_WARN_PCT free (default 25%) it logs one line/day; below
TURNOS_DISK_ALERT_PCT free (default 15%) it additionally sends ONE admin
email/day via notifications.mail.send() (silently skips if no email
provider is configured — same degrade-with-grace contract as the rest of the repo).
The "once per calendar day" dedup is a durable AT-MOST-ONCE claim in
Postgres (`db_analytics.claim_daily_alert`, table `alert_claims`), taken BEFORE
emitting: the earlier JSON-file check-send-save had a real race (two
workers the same day, or a SIGKILL between the provider accepting the
mail and the state save, sent the alert twice — Codex gate 2026-08-08).
Claim-then-send means a failed send consumes the day's claim on purpose
(zero-or-one email per day — at-most-once, which can be zero; never
two), and with Postgres unreachable nothing is emitted at all (fail
closed). Because at-most-once can legitimately deliver ZERO after a
crash, the disk check is EXCLUDED from the queue batch outright
(round-8 gate — a missed daily alert is skipped work under the card's
literal bar, and "tomorrow re-alerts if still low" is a different
day's event): in every topology it runs on the WEB process's legacy
schedule — full tick() on the thread path, the reduced `_disk_loop`
under TURNOS_QUEUE_WORKER — and the queue task runs `tick_purges()`
only, whose DB mutations are the no-skip/no-duplicate-provable work. An old data/private/housekeeping-disk-alert.json left by a
previous version is read once per pass and its SAME-DAY marks are
migrated into consumed claims (`_migrate_legacy_disk_state`) so upgrade
day cannot double-alert; past that day the file is inert and left in
place. The claims table cleans up after itself: `tick()` purges
`alert_claims` rows older than TURNOS_HOUSEKEEPING_ALERT_CLAIM_DAYS
(default 180) along with the other tables.
"""

import os
import sys
import threading
from datetime import datetime

from app.core import config
from app.core import log_pii

_DEFAULT_INTERVAL_H = 24
_DEFAULT_WARN_PCT = 25.0
_DEFAULT_ALERT_PCT = 15.0
_stop = threading.Event()


def _log(msg):
    print(f"[housekeeping] {msg}", file=sys.stderr, flush=True)


def _env(key, default=""):
    return config.get(key, default)


def _int_env(key, default):
    try:
        return int(_env(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


# Plazo de retención del chat (problems.md №12). Lo decide la institución: sin
# configurar NO se borra nada por fecha. El piso evita que un typo («3») borre
# las conversaciones de la semana.
CHAT_RETENTION_FLOOR_DAYS = 30
_CHAT_RETENTION_BATCH = 2000
_CHAT_RETENTION_MAX_BATCHES = 20


def _retention_days(key):
    """`key` como días enteros >= CHAT_RETENTION_FLOOR_DAYS, o None (apagado)
    si falta, no se entiende o está por debajo del piso (con una línea de
    log: un valor mal escrito no debe pasar por «apagado» sin que se note)."""
    raw = str(_env(key, "") or "").strip()
    if raw in ("", "0"):   # «0» = apagado a propósito, sin ruido en el log
        return None
    try:
        days = int(raw)
    except ValueError:
        days = 0
    if days < CHAT_RETENTION_FLOOR_DAYS:
        _log(f"{key}={raw!r} no es un plazo válido (días, mínimo "
             f"{CHAT_RETENTION_FLOOR_DAYS}): ese plazo queda apagado")
        return None
    return days


def chat_retention_days():
    """Días que se guardan los mensajes del chat (todos los canales de charla),
    o None = no se borra por fecha (el valor por defecto)."""
    return _retention_days("TURNOS_CHAT_RETENTION_DAYS")


def chat_summary_retention_days():
    """Días que se guardan los resúmenes y compactaciones del chat:
    TURNOS_CHAT_SUMMARY_RETENTION_DAYS; si la variable no está puesta, el mismo
    plazo de los mensajes. Si está puesta, manda ella: «0», un número por
    debajo del piso o ilegible significa «los resúmenes no se borran por
    fecha» (nunca se hereda el plazo de los mensajes por un valor mal
    escrito). None = no se borran por fecha."""
    if str(_env("TURNOS_CHAT_SUMMARY_RETENTION_DAYS", "") or "").strip():
        return _retention_days("TURNOS_CHAT_SUMMARY_RETENTION_DAYS")
    return chat_retention_days()


def _purge_in_batches(fn, days):
    """Corre `fn(days, limit)` en tandas hasta agotar lo vencido (con tope),
    para que activar el plazo con un atraso grande se vaya en un barrido y no
    en semanas, sin trabar la tabla en una sola sentencia."""
    total = 0
    for _ in range(_CHAT_RETENTION_MAX_BATCHES):
        n = fn(days, _CHAT_RETENTION_BATCH)
        total += n
        if n < _CHAT_RETENTION_BATCH:
            break
    return total


def purge_chat_by_retention():
    """(mensajes, resúmenes y compactaciones) borrados por el plazo del chat.
    Primero los mensajes, después los resúmenes. (0, 0) y sin tocar nada si el
    plazo no está configurado."""
    from app.core import db_chat
    mensajes = resumenes = 0
    dias = chat_retention_days()
    if dias:
        mensajes = _purge_in_batches(db_chat.purge_chat_by_age, dias)
    dias_resumen = chat_summary_retention_days()
    if dias_resumen:
        resumenes = _purge_in_batches(db_chat.purge_chat_summaries_by_age, dias_resumen)
    return mensajes, resumenes


def _float_env(key, default):
    try:
        return float(_env(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _hoy():
    """Fecha calendario de HOY en el huso de la instalación (tz.py) — el
    dedup de "una alerta por día" tiene que ser el día del paciente/staff,
    no el del proceso ni UTC."""
    from app.core import tz
    return datetime.now(tz.tzinfo()).date().isoformat()


def _migrate_legacy_disk_state(hoy):
    """Rollout-day bridge (Codex gate, round 5): versions before the
    Postgres claim persisted the dedup in
    data/private/housekeeping-disk-alert.json. If that file says TODAY
    already logged/emailed, pre-consume the matching claims so the first
    DB-backed pass cannot duplicate the alert on upgrade day. Race-safe
    by construction: the pre-consume IS db_analytics.claim_daily_alert's atomic
    INSERT ... ON CONFLICT — two processes migrating at once still yield
    one claim. Once today's date has passed, the file (left on disk,
    never rewritten) is inert forever."""
    try:
        import json
        from app.admin_panel import system_status
        path = os.path.join(system_status.data_dir(), "private",
                            "housekeeping-disk-alert.json")
        with open(path) as f:
            estado = json.load(f)
    except (OSError, ValueError):
        return
    if estado.get("last_log_date") == hoy:
        _claim_day("disk_low_log", hoy)
    if estado.get("last_email_date") == hoy:
        _claim_day("disk_low_email", hoy)


def _claim_day(alert_key, dia):
    """Durable at-most-once claim for the day, in Postgres
    (db_analytics.claim_daily_alert; Codex gate 2026-08-08 — the previous JSON
    check-send-save was a real race: two workers the same day, or a
    SIGKILL between the provider accepting the mail and the state save,
    duplicated the alert). FAIL CLOSED: if the claim cannot be taken
    (Postgres down), nothing is emitted — no durable claim, no send."""
    from app.core import db_analytics
    try:
        return db_analytics.claim_daily_alert(alert_key, dia)
    except Exception as e:  # noqa: BLE001 — no claim, no emission; never raises
        _log(f"disco: claim {alert_key} falló ({log_pii.exc(e)}) — sin claim no se emite")
        return False


def _send_disk_alert_email(medida, libre_pct):
    """Best-effort — mismo contrato que mail.send(): nunca levanta,
    devuelve False si no hay destinatario o si el proveedor de email no está
    configurado."""
    try:
        from app.auth import admin_auth
        from app.notifications import mail
        destinatario = admin_auth.owner_email()
        if not destinatario:
            _log("disco: sin email de dueño configurado, no se envía alerta")
            return False
        asunto = "Alerta: poco espacio libre en disco"
        cuerpo = (
            f"El volumen de datos quedó con {libre_pct:.0f}% libre "
            f"({medida['libre_gb']:.1f} GB de {medida['total_gb']:.0f} GB).\n\n"
            "Liberá espacio o ampliá el volumen antes de que se llene."
        )
        seam = (os.environ.get("TURNOS_TEST_MAIL_ACCEPT_FILE")
                if os.environ.get("TURNOS_TEST_SEAMS") == "1" else None)
        if seam:
            # Test seam ONLY, double-gated (kill-boundary proof, Codex
            # gate 2026-08-08): it takes TURNOS_TEST_SEAMS=1 AND the seam
            # var — one accidentally inherited env line cannot suppress a
            # real alert (same master-gate contract as the queue modules'
            # TURNOS_TEST_SWEEP_HOLD_S and worker.py's unsafe-threshold
            # override; see worker._test_seams_enabled):
            # stands in for the provider, durably recording the simulated
            # ACCEPTANCE, then optionally holding so the test can SIGKILL
            # the process after acceptance but before this function
            # returns. The claim was already taken before we got here, so
            # a rerun after the kill must find it consumed and never
            # produce a second acceptance line. Inert unless the env var
            # is set — real deployments never set it.
            with open(seam, "a") as f:
                f.write("accepted\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                hold = float(os.environ.get("TURNOS_TEST_MAIL_HOLD_S", "0") or 0)
            except ValueError:
                hold = 0
            if hold > 0:
                import time
                time.sleep(hold)
            _log(f"disco: alerta enviada a {log_pii.email(destinatario)}")
            return True
        ok = mail.send(destinatario, asunto, cuerpo)
        if ok:
            _log(f"disco: alerta enviada a {log_pii.email(destinatario)}")
        return ok
    except Exception as e:  # noqa: BLE001 — el email nunca debe tumbar el barrido
        _log(f"disco: no se pudo enviar la alerta ({log_pii.exc(e)})")
        return False


def check_disk():
    """Un pase del chequeo de espacio libre. Devuelve un dict con el
    resultado (para logging/tests) o None si no se pudo medir el disco.
    Nunca levanta — mismo contrato que tick()."""
    from app.admin_panel import system_status
    medida = system_status.medir_disco()
    if medida is None:
        return None
    warn_pct = _float_env("TURNOS_DISK_WARN_PCT", _DEFAULT_WARN_PCT)
    alert_pct = _float_env("TURNOS_DISK_ALERT_PCT", _DEFAULT_ALERT_PCT)
    libre_pct = medida["libre_pct"]
    resultado = {"libre_pct": libre_pct, "logged": False, "emailed": False}
    if libre_pct >= warn_pct:
        return resultado

    hoy = _hoy()
    _migrate_legacy_disk_state(hoy)
    if _claim_day("disk_low_log", hoy):
        _log(f"espacio libre bajo: {libre_pct:.1f}% libre "
             f"({medida['libre_gb']:.1f} GB de {medida['total_gb']:.0f} GB)")
        resultado["logged"] = True

    if libre_pct < alert_pct:
        if _claim_day("disk_low_email", hoy):
            # Claim BEFORE the send = at-most-once by construction (which
            # can also mean ZERO: a failed send consumes the day's claim
            # on purpose — zero or one email, never two). Neither two
            # workers the same day nor a SIGKILL after Mailgun accepted
            # can duplicate the alert.
            resultado["emailed"] = _send_disk_alert_email(medida, libre_pct)
            if not resultado["emailed"]:
                _log("disco: alerta no enviada; el claim del día queda consumido "
                     "(at-most-once) — reintento recién mañana")
        else:
            # Visibility on the claimed-but-not-by-us path (adversarial
            # review, 2026-08-08): a crash-recovered rerun, or the losing
            # worker of a race, must say WHY it is not alerting — without
            # this line the operator cannot distinguish "alert handled
            # earlier today" from "alert never attempted".
            _log("disco: la alerta de hoy ya fue reclamada antes "
                 "(enviada o intentada) — no se repite")
    return resultado


def lab_upload_retention_days():
    """Days a patient-uploaded lab file is kept: `TURNOS_LAB_UPLOAD_RETENTION_DAYS`
    when set, else the referral files' own window
    (`booking_referrals._referral_retention_days`, default 180) — so the
    municipal installation's `TURNOS_REFERRAL_RETENTION_DAYS=0` («do not
    clean») also keeps these. <=0 never purges."""
    raw = _env("TURNOS_LAB_UPLOAD_RETENTION_DAYS")
    if raw:
        return _int_env("TURNOS_LAB_UPLOAD_RETENTION_DAYS", 0)
    from app.modules.turnos import booking_referrals
    return booking_referrals._referral_retention_days()


def _purge_chat_retention_into(out):
    """Job de `tick_purges`: guarda los dos conteos en `out` y devuelve su
    suma (el nombre del job no es una clave de `out`)."""
    def run():
        out["chat_retention_messages"], out["chat_retention_summaries"] = purge_chat_by_retention()
        return out["chat_retention_messages"] + out["chat_retention_summaries"]
    return run


def tick_purges():
    """Runs one purge sweep across the five tables — the DB-only half of
    the housekeeping tick, and the ONLY half the queue worker runs
    (Codex gate, round 8: the disk check's advisory email is
    at-most-once and may legitimately deliver zero after a crash, which
    cannot meet the card's literal no-skipped-work bar for queued work —
    so it is excluded from the queue batch outright and stays on the
    web process's own legacy schedule; see start()). Returns
    {table: rows_purged}. Never raises — one broken purge never blocks
    the others."""
    from app.core import access_log
    from app.core import db_accounts
    from app.core import db_clinical
    from app.core import db_slots
    from app.core import db_analytics
    from app.core import db_ai
    out = {"study_sessions": 0, "sessions": 0, "searches": 0, "login_panel_events": 0,
           "ai_costs_ip": 0,
           "chat_messages": 0, "chat_retention_messages": 0, "chat_retention_summaries": 0,
           "alert_claims": 0, "clinical_access_log": 0,
           "lab_upload_files": 0}
    jobs = (
        ("sessions", lambda: db_accounts.purge_idle_sessions(
            _int_env("TURNOS_HOUSEKEEPING_SESSION_IDLE_DAYS", 30))),
        ("searches", lambda: db_analytics.purge_old_searches(
            _int_env("TURNOS_HOUSEKEEPING_SEARCH_DAYS", 180))),
        ("login_panel_events", lambda: db_analytics.purge_old_login_panel_events(
            _int_env("TURNOS_HOUSEKEEPING_LOGIN_PANEL_DAYS", 180))),
        ("ai_costs_ip", lambda: db_ai.blank_old_ai_cost_ips(
            _int_env("TURNOS_HOUSEKEEPING_AI_COSTS_IP_DAYS", 180))),
        ("chat_messages", lambda: db_slots.purge_summarized_chat_messages(
            _int_env("TURNOS_HOUSEKEEPING_CHAT_DAYS", 90))),
        # Retención del chat por fecha (problems.md №12): apagada hasta que la
        # institución fije el plazo; mensajes primero, resúmenes después.
        ("chat_retention", _purge_chat_retention_into(out)),
        # The claim feature must clean up after itself (adversarial
        # review 2026-08-08): a claim only blocks duplicates on its own
        # day; old rows are residue.
        ("alert_claims", lambda: db_analytics.purge_old_alert_claims(
            _int_env("TURNOS_HOUSEKEEPING_ALERT_CLAIM_DAYS", 180))),
        # Registro de accesos a datos clínicos (K10): diez años por default,
        # nunca menos de uno — access_log.retention_days y el trigger.
        ("clinical_access_log", lambda: db_clinical.purge_clinical_access_log(
            access_log.retention_days())),
        # Archivos de resultados de laboratorio que subió el paciente (bytea):
        # vencen como las órdenes de derivación (revisión de esquema №11).
        ("lab_upload_files", lambda: db_clinical.purge_lab_upload_files(
            lab_upload_retention_days())),
    )
    from app.modules.turnos import study_store
    jobs += (("study_sessions", study_store.purge),)
    for name, fn in jobs:
        try:
            result = fn()
            if name in out:
                out[name] = result
        except Exception as e:  # noqa: BLE001 — el server nunca cae por esto
            _log(f"{name}: fallo ({log_pii.exc(e)})")
    total = sum(out.values())
    if total:
        _log(f"barrido: {out['sessions']} sesion(es), {out['searches']} busqueda(s), "
             f"{out['login_panel_events']} evento(s) de panel, "
             f"{out['ai_costs_ip']} IP(s) de costos de IA vencida(s), "
             f"{out['chat_messages']} mensaje(s) de chat, "
             f"{out['chat_retention_messages']} mensaje(s) y "
             f"{out['chat_retention_summaries']} resumen(es) de chat por plazo, "
             f"{out['clinical_access_log']} acceso(s) clínico(s) vencido(s), "
             f"{out['lab_upload_files']} archivo(s) de laboratorio vencido(s)")
    return out


def tick():
    """The legacy full tick — purges + disk check, byte-identical
    behavior to before the queue migration. Runs on the thread path
    only; the queue task runs tick_purges() and never this. Never
    raises."""
    out = tick_purges()
    try:
        check_disk()
    except Exception as e:  # noqa: BLE001 — el chequeo de disco nunca tumba el barrido
        _log(f"disco: fallo ({log_pii.exc(e)})")
    return out


def _disk_loop(interval_h):
    """Web-side disk-check loop for the delegated topology: with
    TURNOS_QUEUE_WORKER set the worker owns the table purges, but the
    disk check (measurement + advisory email) deliberately stays HERE,
    on its legacy web-side schedule — its at-most-once delivery is not
    allowed to ride the queue batch (round-8 gate). Same cadence, same
    guard shape as _loop()."""
    while not _stop.is_set():
        try:
            check_disk()
        except Exception as e:  # noqa: BLE001 — el hilo nunca muere
            _log(f"disco: fallo ({log_pii.exc(e)})")
        _stop.wait(interval_h * 3600)


def _loop(interval_h):
    while not _stop.is_set():
        try:
            tick()
        except Exception as e:  # noqa: BLE001 — el hilo nunca muere
            _log(f"tick() fallo: {log_pii.exc(e)}")
        _stop.wait(interval_h * 3600)


def _interval_h():
    """THE one resolution of TURNOS_HOUSEKEEPING_INTERVAL_H — the thread
    loop and the queue registration (worker.py) both derive their cadence
    from this, never a second parse (same contract as
    reminders._interval_min())."""
    return _int_env("TURNOS_HOUSEKEEPING_INTERVAL_H", _DEFAULT_INTERVAL_H)


def start():
    """Launches the daemon thread, unless TURNOS_HOUSEKEEPING_OFF=1. Never
    raises (same contract as reminders.start()).

    Tier 4 worker-topology (periodic-daemon batch 2, narrowed by the
    round-8 gate): TURNOS_QUEUE_WORKER=1 means the
    SEPARATE worker process owns the TABLE PURGES via
    ops/housekeeping_queue.py — but the disk check (measurement +
    advisory email) stays on THIS web process, on its legacy schedule,
    in every topology: its at-most-once delivery contract cannot meet
    the queue batch's literal no-skipped-work bar, so it never rides the
    queue. Exactly one mechanism per concern, proven by
    tests/test_queue_worker.py::TestDelegationMatrix: purges = worker,
    disk = the reduced `_disk_loop` thread here."""
    if _env("TURNOS_HOUSEKEEPING_OFF") == "1":
        _log("desactivado (TURNOS_HOUSEKEEPING_OFF=1)")
        return
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        interval_h = _interval_h()
        try:
            th = threading.Thread(target=_disk_loop, args=(interval_h,),
                                  name="housekeeping-disco", daemon=True)
            th.start()
            _log(f"purgas delegadas al worker (TURNOS_QUEUE_WORKER={modo_worker}); "
                 f"el chequeo de disco sigue acá (cada {interval_h}h)")
        except Exception as e:  # noqa: BLE001
            _log(f"no se pudo iniciar el chequeo de disco: {log_pii.exc(e)}")
        return
    interval_h = _interval_h()
    try:
        th = threading.Thread(target=_loop, args=(interval_h,),
                              name="housekeeping", daemon=True)
        th.start()
        _log(f"iniciado (cada {interval_h}h)")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo iniciar: {log_pii.exc(e)}")
