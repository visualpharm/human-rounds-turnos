"""Web-process background lifecycle for the legacy HTTP runtime.

The process entrypoint composes serving; this module owns the optional
background services that run beside it. Every starter remains fail-open and
keeps its existing feature flag. Queue-owned jobs still delegate from their
own ``start()`` functions, so this extraction does not choose a worker
topology or run business work twice.
"""

from __future__ import annotations

import os
import threading

from app.core import log_pii


def _start_observability() -> None:
    """AI register baseline and error tracking."""
    try:
        from app.core import ai_register
        ai_register.observe_inventory(ai_register.inventory())
    except Exception as exc:  # noqa: BLE001 — dossier reports unavailable until baselined
        print(f"AI register startup refresh unavailable: {type(exc).__name__}", flush=True)

    try:
        from app.core import error_tracking

        if error_tracking.init():
            print("Rastreo de errores (Sentry) iniciado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        # The type only: a malformed DSN in the message is a credential.
        print(f"Rastreo de errores no iniciado: {type(exc).__name__}", flush=True)


def _start_periodic_jobs() -> None:
    """Reminders, sweeps, regeneration and warm-up jobs of the web process."""
    try:
        from app.notifications import reminders

        reminders.start()
        print("Recordatorios de turno iniciados.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Recordatorios no iniciados: {log_pii.exc(exc)}", flush=True)

    try:
        from app.modules.turnos import waitlist

        waitlist.start()
        print("Barrido de lista de espera iniciado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Barrido de lista de espera no iniciado: {log_pii.exc(exc)}", flush=True)

    try:
        from app.modules.turnos import booking_referrals

        booking_referrals.start_referral_sweep()
        print("Retención de órdenes iniciada.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Retención de órdenes no iniciada: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ops import housekeeping

        housekeeping.start()
        print("Higiene periódica de tablas iniciada.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Higiene periódica de tablas no iniciada: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ops import launch_digest

        if launch_digest.start():
            print("Resumen diario de señales de lanzamiento iniciado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Resumen diario de señales no iniciado: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ops import demo_refresh

        if demo_refresh.start():
            print("Regeneración diaria de la demo iniciada.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Regeneración diaria de la demo no iniciada: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ai import chart_summary

        if chart_summary.start():
            print("Pre-generación de resúmenes del paciente iniciada.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Pre-generación de resúmenes no iniciada: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ops import translate_warmup

        if translate_warmup.start():
            print("Traducción del catálogo en segundo plano iniciada.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Traducción del catálogo no iniciada: {log_pii.exc(exc)}", flush=True)

    try:
        from app.patient_web import site_doctors

        threading.Thread(
            target=site_doctors.warm_avatar_sprite,
            name="avatar-warmup",
            daemon=True,
        ).start()
        print("Atlas de avatares en precalentamiento.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Precalentamiento de avatares no iniciado: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ops import cache_warmup

        if cache_warmup.start():
            print("Caches del camino público en precalentamiento.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Precalentamiento de caches no iniciado: {log_pii.exc(exc)}", flush=True)


def _start_emr_sync() -> None:
    """The EMR lifecycle selected by the registry, if any."""
    # The durable EMR journal supersedes the compatibility fallback. Do the
    # registry lookup before importing either lifecycle module: an unselected
    # installation has no EMR runtime at all, not an imported HSI path that
    # happens to no-op or misleadingly claims it started.
    from app import connectors

    emr_id, _source = connectors.resolve(connectors.EMR_SYNC)

    if emr_id:
        if os.environ.get("TURNOS_HSI_SYNC_OFF") == "1":
            try:
                from app.modules.turnos import hsi_fallback

                hsi_fallback.start()
                print("Resincronización EMR de compatibilidad iniciada.", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"Resincronización EMR de compatibilidad no iniciada: {log_pii.exc(exc)}", flush=True)
        else:
            try:
                from app.modules.turnos import hsi_journal

                hsi_journal.start()
                print("Sincronización bidireccional EMR iniciada.", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"Sincronización bidireccional EMR no iniciada: {log_pii.exc(exc)}", flush=True)


def _start_backup_producers() -> None:
    """The data/ backup loop and the encrypted full-backup producer."""
    try:
        from app.ops import data_backups

        data_backups.start()
    except Exception as exc:  # noqa: BLE001
        print(f"Backup de data/ no iniciado: {log_pii.exc(exc)}", flush=True)

    try:
        from app.ops import backup_full
        backup_full.start()
    except Exception:  # noqa: BLE001 — System exposes configuration/freshness
        print("Encrypted backup producer not started", flush=True)


def _start_ai_health() -> None:
    try:
        from app.ai import ai_health

        ai_health.start()
        print("Monitor de salud de IA iniciado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Monitor de salud de IA no iniciado: {log_pii.exc(exc)}", flush=True)


def _start_analytics() -> None:
    try:
        from app.modules.turnos import analytics_export

        if analytics_export.start():
            print("Flujo de analítica anónima iniciado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Flujo de analítica no iniciado: {log_pii.exc(exc)}", flush=True)


def _start_app_modules() -> None:
    # Module entrypoints run after platform services and route registration.
    # A broken module is marked unhealthy by app_modules and never prevents
    # the platform from accepting traffic.
    try:
        from app.core import app_modules

        app_modules.startup_all()
        print("Ciclo de vida de app modules iniciado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Ciclo de vida de app modules no iniciado: {log_pii.exc(exc)}", flush=True)


def start_background_services() -> None:
    """Start every optional web-side service in the established order."""
    # The limiter and the log filter stay inline: test_concurrency_settings pins that this
    # function applies the limiter first, by reading its source.
    try:
        # Runs inside the app lifespan, i.e. in the event loop the limiter belongs to.
        from app.core import concurrency, db
        threads = concurrency.apply_threadpool_size()
        print(f"Concurrencia: {threads} hilos, pool de {db._pool_size()} conexiones, "
              f"espera de {db._pool_timeout():g} s por una conexión libre.", flush=True)
    except Exception as exc:  # noqa: BLE001 — a bad limiter must not keep the site down
        print(f"Tamaño del pool de hilos no aplicado: {type(exc).__name__}", flush=True)

    try:
        # Here, after uvicorn configured its loggers: the access line loses
        # the query string (`?dni=…`) before the first request is served.
        log_pii.install_access_log_filter()
    except Exception as exc:  # noqa: BLE001
        print(f"Filtro del log de accesos no instalado: {type(exc).__name__}", flush=True)

    _start_observability()
    _start_periodic_jobs()
    _start_emr_sync()
    _start_backup_producers()
    _start_ai_health()
    _start_analytics()
    _start_app_modules()


def stop_app_modules() -> None:
    """Stop enabled app-module entrypoints without blocking server teardown."""
    try:
        from app.core import app_modules

        app_modules.shutdown_all()
        print("Ciclo de vida de app modules: apagado.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Ciclo de vida de app modules: shutdown falló: {log_pii.exc(exc)}", flush=True)
