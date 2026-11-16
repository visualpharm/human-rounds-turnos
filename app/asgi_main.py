"""Human Rounds process entrypoint for the Tier 4 coexistence scaffold.

Rollback-artifact retirement (family W3b, 2026-08-26,
docs/roadmap/native-migration-campaign-20260826.md — see its
"Lifecycle decisions" section): this entrypoint no longer has a dark-mode
fallback. `TURNOS_ASGI=1` serves `app.app` through one Uvicorn listener on
the public `HOST:PORT` — FastAPI owns every route and the web-process
lifecycle, no internal legacy listener or server thread exists. Any other
value (unset, "0", a typo) is a hard configuration error: `main()` exits
loudly instead of silently falling back to running the pre-refactor
topology, and this module never imports the legacy entrypoint module at
all — that topology is gone from this file's call graph entirely.

The demo has used ASGI mode since 2026-08-08; on 2026-08-19 the current
Coolify configuration and exact-SHA deployments verified that production and
demo both use `TURNOS_ASGI=1` — nothing running through THIS entrypoint has
depended on the fallback for weeks. The final campaign wave then retired the
legacy `server.py` topology itself (dispatcher, transport, entrypoint): the
native composition is the only executable topology, and this file is the
one process entrypoint. Full design and risk history live in
`docs/roadmap/tier4-replatform-design.md`.
"""
import os
import sys
import threading

if not __package__:  # run as a file (`python3 app/asgi_main.py`): the project root comes first
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import log_pii  # noqa: E402
from app.core import paths  # noqa: E402
from app.core import settings  # noqa: E402

ROOT = paths.PROJECT_ROOT
# The queue worker's own process, by its file path as before the layout move, so a process
# table still names it (the suite's worker-leak gate, an operator's `ps`).
WORKER_ARGV = (sys.executable, os.path.join(paths.CODE_ROOT, "worker.py"))


def _start_queue_worker_supervisor():
    """Tier 4 worker-topology slice (docs/roadmap/tier4-replatform-design.md,
    "Worker topology"): when TURNOS_QUEUE_WORKER=1, the queue worker runs as
    a SEPARATE OS process (`python3 worker.py` — see its docstring for the
    whole topology story) spawned and supervised from here, the process
    entrypoint — never as a daemon thread inside the web process. The web
    side then delegates the queue-owned sweeps (the migrated jobs' start()
    functions start no queue work; housekeeping.start() still keeps its
    reduced web-side disk-check thread — round-8 design). Flag unset (the
    code default; pinamar and the demo run `=1`): nothing
    happens, zero cost — this function returns before importing anything.
    A value other than "1" (the retired "external" included) is off, with
    one line saying so.

    Supervision contract: restart with exponential backoff (1s → 60s cap,
    reset after a run that lived ≥60s) if the worker dies — a worker down
    NEVER affects the web process's own serving or health (sweeps degrade
    to "delayed", the same failure shape a crashed daemon thread always
    had). Shutdown: an atexit hook SIGTERMs the child and waits out its
    graceful drain (worker.py's own TURNOS_WORKER_GRACEFUL_TIMEOUT, default
    20s) — atexit rather than a signal handler because Uvicorn owns the
    process signals and this module must not fight it. This sole process
    entrypoint owns the optional in-process supervisor."""
    from app.core import queue_infra
    if queue_infra.worker_mode() != "1":
        crudo = (settings.ProcessSettings.current().queue_worker or "").strip()
        if crudo:
            print(f"[asgi_main] TURNOS_QUEUE_WORKER={crudo!r} no es un modo válido (sólo '1'): "
                  "los barridos corren como hilos del proceso web", file=sys.stderr, flush=True)
        return None

    import atexit
    import subprocess
    import time

    # El candado serializa (chequeo de stop + Popen + registrar el hijo)
    # contra _apagar: sin él, atexit podía correr ENTRE el chequeo y el
    # Popen y el hijo recién nacido quedaba huérfano (revisión Fable
    # 2026-08-08, hallazgo 3; cerrado con candado por pedido del responsable del producto en vez
    # de aceptar el residuo). proc.wait() largo queda FUERA del candado.
    candado = threading.Lock()
    estado = {"proc": None, "stop": False}

    def _log(msg):
        print(f"[asgi_main] {msg}", file=sys.stderr, flush=True)

    def _terminar(proc):
        # The SIGTERM→SIGKILL wait covers the longest interruptible phase,
        # procrastinate's async-pool close, core.db's atexit pool close, and
        # the final supervisor margin. It is computed in ONE place,
        # queue_infra.supervisor_sigkill_budget(), so the real-subprocess
        # test harness (WorkerProc.stop) reads the SAME ceiling and can't
        # re-derive the undersized 25s that SIGKILLed a worker mid-pool-open
        # (Fable 2026-08-08 hallazgo 4 + round-9 POOL_OPEN_TIMEOUT_S
        # finding — full rationale lives at that function). Un operador que
        # suba TURNOS_WORKER_GRACEFUL_TIMEOUT no debe ver a su worker
        # SIGKILLeado a mitad del drain; the single margin
        # (SUPERVISOR_SIGKILL_MARGIN_S) is the same one worker.py uses to
        # bound its own post-run_worker_async hsi_sync startup drain.
        from app.core import queue_infra as _queue_infra
        proc.terminate()
        try:
            proc.wait(_queue_infra.supervisor_sigkill_budget())
        except subprocess.TimeoutExpired:
            proc.kill()

    def _supervisar():
        # backoff arranca en 0.5 y se DUPLICA antes de cada espera — la
        # primera espera real es 1 s, como promete el doc ("1 s → 60 s").
        backoff = 0.5
        while True:
            t0 = time.monotonic()
            proc = None
            with candado:
                if estado["stop"]:
                    return
                try:
                    proc = subprocess.Popen(list(WORKER_ARGV), cwd=ROOT)
                except Exception as e:  # noqa: BLE001 — el web sigue sirviendo igual
                    backoff = min(max(backoff * 2, 1), 60)
                    _log(f"no pude lanzar worker.py: {log_pii.exc(e)} — reintento en {backoff:.0f}s")
                else:
                    estado["proc"] = proc
            if proc is not None:
                _log(f"worker de cola lanzado (pid {proc.pid})")
                code = proc.wait()  # fuera del candado — puede ser horas
                with candado:
                    estado["proc"] = None
                    if estado["stop"]:
                        return
                if code == 2:
                    # Contrato de worker.py (EXIT_CONFIG): configuración
                    # permanente inválida — reintentar no la arregla y un
                    # loop de reinicios cada 60 s sólo ensucia los logs
                    # (ronda Codex 2026-08-08). El web sigue sirviendo; los
                    # barridos NO corren hasta corregir la config y
                    # redeployar.
                    _log("worker salió con configuración inválida (exit 2) — "
                         "NO reintento; corregí la config y redeployá")
                    return
                vivio = time.monotonic() - t0
                backoff = 1 if vivio >= 60 else min(max(backoff * 2, 1), 60)
                _log(f"worker terminó (exit={code}, vivió {vivio:.0f}s) — reinicio en {backoff:.0f}s")
            for _ in range(max(1, int(backoff))):
                if estado["stop"]:
                    return
                time.sleep(1)

    def _apagar():
        # Bajo el candado: o el supervisor todavía no spawneó (verá stop y
        # no spawnea nunca más), o ya spawneó y registró el hijo (lo vemos
        # acá y lo bajamos). No queda ventana intermedia.
        with candado:
            estado["stop"] = True
            proc = estado["proc"]
        if proc is not None and proc.poll() is None:
            _terminar(proc)

    atexit.register(_apagar)
    threading.Thread(target=_supervisar, name="queue-worker-supervisor",
                     daemon=True).start()
    # Devuelve el hook de apagado (idempotente) — atexit ya lo tiene; los
    # tests lo usan para apagar sin esperar la salida del proceso.
    return _apagar


def _verify_required_packs():
    """Shared synchronous readiness gate for every ASGI entry path."""
    # Do not import server here: its legacy module imports can read catalog
    # data. This core-only preflight must reject a bad private pack first.
    from app.core import data_packs
    data_packs.verify_required_packs()


def _enable_web_db_limits():
    """The web process's database connections carry a statement and a lock limit
    (core/db.py, TURNOS_DB_STATEMENT_TIMEOUT / TURNOS_DB_LOCK_TIMEOUT). This is the one
    place that turns them on, reached only by `main(web_process=True)` from the `__main__`
    block below so that no test importing this module inherits them, and before any module
    can open the pool. The queue worker
    (a child process, `worker.py`), migrations, backups and scripts never reach it."""
    from app.core import db
    statement_ms, lock_ms = db.enable_web_timeouts()
    print("Límites de la base (proceso web): consulta %s, espera de bloqueo %s." % (
        "%g s" % (statement_ms / 1000) if statement_ms else "sin límite",
        "%g s" % (lock_ms / 1000) if lock_ms else "sin límite"), flush=True)


def _asgi_mode():
    """Serve the native composition on one public listener."""
    # Direct callers get the same fail-closed preflight as main(). Import the
    # composition only after the required-pack contract is known-good because
    # router imports may read pack-backed catalog data.
    _verify_required_packs()

    # Demo-sandbox safety before ANY feature module import: the retired
    # legacy dispatcher applied these env defaults at its own import, ahead
    # of the feature modules that read integration toggles at import/startup
    # time (see ops/demo_installation.apply_safety_defaults). The native
    # composition gets the same belt at the same point — before `import app`
    # pulls the routers in. A no-op outside demo mode.
    from app.ops import demo_installation
    demo_installation.apply_safety_defaults()

    import importlib
    import uvicorn
    # sys.modules first, as the old `import app` did (`from app import main` reads the package
    # attribute and would miss a composition planted in sys.modules)
    asgi_app = importlib.import_module("app.main")

    from app.cuentas import portal_identity
    from app.auth import staff_portal
    served_app = asgi_app.app
    proxy_settings = {}
    if staff_portal.enabled():
        # The staff portal login trusts nothing but the gateway boundary
        # below: without it the installation must not start at all.
        staff_portal.validate_configuration()
    portal_identity.validate_patient_login()
    from app.ops import purge_guard
    # The municipal installation may not hold the right to delete backups: the
    # lock is in ops/purge_guard (light: no database import before the packs); a request for the purge only
    # gets a loud line here (the service must start); so does the rotation of
    # the full backups switched on there (the lock is core/backup_lock).
    purge_guard.warn_purge_ignored("start")
    purge_guard.warn_rotation_ignored("start")
    from app.cuentas import test_portal
    if test_portal.enabled():
        # The test portal is the gateway, in this process: it writes the
        # peer itself, so forwarded-header handling stays as it was.
        test_portal.validate_configuration()
        portal_identity.validate_configuration()
        served_app = test_portal.TestPortalGateway(served_app)
    elif portal_identity.enabled():
        portal_identity.validate_configuration()
        served_app = portal_identity.SocketPeerApp(served_app)
        # The authentication boundary must see the socket peer, never XFF.
        proxy_settings["proxy_headers"] = False
    if demo_installation.enabled():
        # X-Robots-Tag: noindex on every demo response, not only HTML.
        served_app = demo_installation.NoindexHeaderApp(served_app)

    server = settings.ServerSettings.current()
    public_host = server.host
    public_port = server.port
    print(
        f"Human Rounds (ASGI) on http://{public_host}:{public_port}/",
        flush=True,
    )
    uvicorn.run(
        served_app,
        host=public_host,
        port=public_port,
        log_level="info",
        server_header=False,
        date_header=False,
        **proxy_settings,
    )


def main(web_process=False):
    # Neither worker supervision nor the HTTP composition may import data
    # consumers before a configured required pack is known-good.
    _verify_required_packs()
    if web_process:
        _enable_web_db_limits()
    if not settings.ProcessSettings.current().asgi:
        # No dark-mode fallback (rollback-artifact retirement, see module
        # docstring): this entrypoint refuses loudly instead of silently
        # running a topology that no longer exists. The legacy `server.py`
        # entrypoint itself was retired with the rest of the pre-refactor
        # transport (final wave of the native migration).
        raise SystemExit(
            "asgi_main.py requires TURNOS_ASGI=1 — the dark-mode fallback "
            "was retired (native-migration campaign, family W3b), and the "
            "legacy server.py topology no longer exists (final wave, "
            "docs/roadmap/native-migration-campaign-20260826.md). Set "
            "TURNOS_ASGI=1: the native composition is the only topology."
        )
    # A required-pack failure must happen before even the independent
    # worker supervisor can spawn a child. _asgi_mode repeats this for
    # direct callers and before importing the ASGI composition.
    _start_queue_worker_supervisor()
    _asgi_mode()


if __name__ == "__main__":
    # Every log record and uncaught exception of this process loses its
    # message: uvicorn's «Exception in ASGI application» included (core/log_pii).
    log_pii.install()
    main(web_process=True)
