"""config.py — un único punto de lectura de configuración runtime.

Antes cada módulo copiaba y pegaba su propio `_env(key, default)` (~25
copias, algunas con un `_env2`/`_envnew` extra para el fallback a un nombre
viejo de env — ver el rename 2026-07-17 de TURNOS_IA_* a TURNOS_AI_*). Este
módulo reemplaza todas esas copias por un único `get()`.

Precedencia (la más específica gana):

    override en DB (catalogo, tipo="config", id="settings")
    > os.environ
    > .env del repo
    > default

Escape hatch de operaciones: `TURNOS_CONFIG_LOCK=1` en el entorno INVIERTE
esa precedencia — con el lock puesto, env/.env ganan SIEMPRE, aunque haya un
valor guardado desde el panel de Settings (fase 4, 2026-07-30). Pensado para
un despliegue gestionado por Coolify/credential vault donde quien opera la infra
necesita poder pisar lo que un admin guardó desde la UI sin tener que borrar
la fila de la base — se prende el env y listo, ninguna llamada a config.get()
en todo el código cambia.

`legacy` es un nombre viejo de la MISMA clave, consultado DESPUÉS del nuevo
en CADA nivel (nuevo en DB, viejo en DB, nuevo en env, viejo en env, nuevo en
.env, viejo en .env) — así un valor nuevo en un nivel más alto sigue ganando
aunque el viejo esté seteado en un nivel más bajo (p. ej. TURNOS_AI_FORCE en
el .env local pisa a TURNOS_IA_FORZAR en el entorno de Coolify).

Claves de ARRANQUE (DATABASE_URL, HOST, PORT) NUNCA consultan la DB: hace
falta la DB URL para poder conectarse a la DB, así que resolverla mirando la
DB sería circular. Quedan env/.env-only.

Mismo patrón de cache que theme.py/auth_mode.py: TTL corto (3 s) para no
pegarle a la DB en cada lectura, y si la DB está caída o no hay fila, cae en
silencio a env/.env — la config nunca tumba el server.

`TURNOS_ENV_FILE` (env real del proceso, nunca DB/.env — leerlo no puede
depender de sí mismo) REEMPLAZA por completo qué archivo cuenta como ".env
del repo" en la precedencia de arriba. Existe para el harness E2E hermético
(tests/e2e/lib/harness.mjs): antes de esto, un `.env` de desarrollo con
TOTP/Turnstile reales se filtraba igual al server bajo test porque un env
var explícito puesto en "" es falsy en Python y el código caía al `.env`
de todos modos (`os.environ.get(key)` -> "" -> sigue buscando). Con
`TURNOS_ENV_FILE` apuntando a un archivo mínimo escrito por el harness (o a
uno inexistente), el `.env` real del checkout nunca se abre — no hace falta
que el desarrollador lo blanquee a mano. Ver `tests/test_config.py`."""

import os
import stat
import tempfile
from contextvars import ContextVar
import threading
import time

try:  # POSIX-only (dev macOS, deploy Linux); ausente sólo en un port no-POSIX.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _fcntl = None

from app.core import paths
from app.core import settings as _settings
from app.ops import demo_installation

# Seam de test solamente: si está seteado, se invoca una vez por save_env_keys()
# ANTES de tomar el lock, así la regresión de concurrencia suelta a todos los
# escritores juntos y ejercita la ventana de lost-update en forma determinista.
# Nunca se setea en producción.
_SAVE_ENV_KEYS_TEST_HOOK = None

ROOT = paths.PROJECT_ROOT
ENV_PATH = os.path.join(ROOT, ".env")

# Se necesitan ANTES de poder abrir una conexión a la DB — jamás pasan por la
# capa de overrides (evita la circularidad "para leer la config hace falta
# la DB, para conectar a la DB hace falta la config").
_INSTALLATION_ONLY = frozenset({"TURNOS_BACKUP_RECOVERY_KEY", "TURNOS_BACKUP_OLD_KEYS_SECRET"})
_BOOTSTRAP = frozenset({"DATABASE_URL", "HOST", "PORT"}) | _INSTALLATION_ONLY

_WRITE_OVERLAY = ContextVar('config_write_overlay', default=None)

_TTL = 3.0  # mismo orden de magnitud que theme.py / auth_mode.py / catalog.py

_dotenv_cache = {"path": None, "vals": None, "mtime": None}
_db_cache = {"v": None, "t": 0.0}
_db_cache_lock = threading.Lock()
_db_cache_generation = 0


def external_history_enabled():
    """Explicit opt-in only; a missing or malformed setting never enables reads."""
    return get("TURNOS_HSI_EXTERNAL_HISTORY", default="0") == "1"


def _env_path():
    """Ruta del .env efectivo. `TURNOS_ENV_FILE` (si está seteado en el
    proceso real) reemplaza el `.env` del checkout por completo — no lo
    completa, lo REEMPLAZA — para que un harness hermético (tests/e2e/lib/
    harness.mjs) pueda arrancar el server sin que éste consulte jamás el
    `.env` real del desarrollador (que puede tener TOTP/Turnstile reales).
    Sin esa var, comportamiento idéntico a antes: el `.env` del repo."""
    return _settings.ProcessSettings.current().env_file or ENV_PATH


def env_file_path():
    """The single effective local-config path.

    Callers which need bootstrap configuration (notably ``core.db``) must
    use this accessor rather than opening the checkout's ``.env`` themselves.
    ``TURNOS_ENV_FILE`` is an explicit replacement, not an overlay.
    """
    return _env_path()


def is_env_file_name(name):
    """Recognize env copies for backup redaction without exposing the checkout name.

    Include the effective config basename and conventional env-file suffixes.
    """
    return name.endswith(".env") or name == os.path.basename(env_file_path())


def _dotenv():
    """El .env efectivo (ver `_env_path()`), parseado una vez y cacheado por
    (ruta, mtime) — mismo parser mínimo que tenía cada _env: KEY=VALUE por
    línea, ignora comentarios/vacías."""
    path = _env_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if (
        _dotenv_cache["vals"] is not None
        and _dotenv_cache["path"] == path
        and _dotenv_cache["mtime"] == mtime
    ):
        return _dotenv_cache["vals"]
    env = {}
    if mtime is not None:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()
        except OSError:
            pass
    _dotenv_cache["vals"] = env
    _dotenv_cache["mtime"] = mtime
    _dotenv_cache["path"] = path
    return env


def dotenv_values():
    """Return a copy of the effective file values without exposing the cache.

    This is intentionally the only public file-reader for bootstrap config.
    It performs no logging and respects ``TURNOS_ENV_FILE`` exactly as
    ``get()`` does.
    """
    return dict(_dotenv())


def installation_host():
    """Host of this installation's public origin (`TURNOS_PUBLIC_ORIGIN`), lower
    case, no port or trailing dot; "" when none is set. The one name an
    installation cannot share with another, and — like `government_installation`
    — read from the process environment and the .env file only, never from the
    database overrides, so nothing edited from the admin panel can move it."""
    from urllib.parse import urlsplit
    origin = _settings.InstallationSettings.current().public_origin
    if "//" not in origin:  # «salud.pinamar.gob.ar» without a scheme still names a host
        origin = "//" + origin
    try:
        return (urlsplit(origin).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def government_installation():
    """True when this process serves a government origin (`*.gob.ar`): the
    municipal installation (salud.pinamar.gob.ar), never a test stand.

    The one marker of the municipal installation: the test portal refuses to
    start on it and the remote purge of backups stays off on it. Read from
    the process environment and the .env file only (never the database
    overrides), so nothing edited from the admin panel can move it."""
    from urllib.parse import urlsplit
    origin = _settings.InstallationSettings.current().public_origin
    if "//" not in origin:  # «salud.pinamar.gob.ar» without a scheme still names a host
        origin = "//" + origin
    try:
        host = (urlsplit(origin).hostname or "").rstrip(".")
    except ValueError:
        return True
    return host == "gob.ar" or host.endswith(".gob.ar")


def _lock_path_matches_fd(lockpath, lockfd):
    """True sii ``lockpath`` es — **sin seguir symlinks** — un archivo regular
    cuyo inodo es el mismo que ``lockfd`` tiene abierto Y ese inodo tiene un
    único nombre (``st_nlink == 1``). Es el contrato completo de identidad
    no-follow/stable-lock; cualquier divergencia devuelve False (fail-closed):

    * la ruta se volvió un symlink, o dejó de ser regular;
    * ``os.replace`` externo del lock file por un inodo distinto;
    * un alias hardlink del inodo lockeado (``st_nlink > 1``);
    * la ruta dejó de resolverse del todo.

    El ``os.lstat`` (no-follow) es lo que cierra el bypass que un ``os.stat``
    dejaba abierto: reemplazar ``.lock`` por un symlink a un alias hardlink del
    MISMO inodo lockeado sigue resolviendo al inodo correcto bajo ``os.stat``,
    así que la versión vieja devolvía True y ``save_env_keys`` comiteaba el env
    mientras ``.lock`` ya era un symlink. La política de single-link
    (``st_nlink == 1``) es la frontera coherente: garantiza que ``.lock`` es el
    ÚNICO nombre del inodo, de modo que no existe alias hacia el que redirigir
    un symlink que matchee el inodo. Juntas, lstat + single-link cierran el
    contrato: la ruta es el archivo regular correcto Y es su único nombre."""
    try:
        path_st = os.lstat(lockpath)  # NO sigue symlinks — clave contra el bypass
        fd_st = os.fstat(lockfd)
    except OSError:
        return False
    # No-follow: la ruta misma no puede ser un symlink. os.stat seguiría el link
    # y compararía inodos iguales para un .lock vuelto symlink a un alias hardlink
    # del mismo inodo, dejando comitear el env con .lock ya siendo un symlink.
    if stat.S_ISLNK(path_st.st_mode):
        return False
    if not stat.S_ISREG(path_st.st_mode):
        return False
    if (fd_st.st_dev, fd_st.st_ino) != (path_st.st_dev, path_st.st_ino):
        return False
    # Single-link policy: el inodo lockeado tiene exactamente UN nombre. Si
    # st_nlink > 1 existe un alias (hardlink) que un actor con acceso de
    # escritura al directorio puede usar de target de un symlink para que la
    # ruta siga resolviendo al mismo inodo; nlink == 1 cierra esa puerta y es
    # la precondición que vuelve verdadero el "no existe alias" de arriba.
    if fd_st.st_nlink != 1:
        return False
    return True


def _acquire_write_lock(lockpath):
    """Open (creándolo si hace falta) un lock file estable y no-follow al lado
    del env, y toma un ``flock`` exclusivo sobre él. Levanta ``OSError`` en
    cualquier fallo (el llamador falla cerrado).

    El lock es un archivo ESTABLE separado del env, no el env mismo: el flock
    sobre el fd de datos no serializa a través de replaces. ``os.replace``
    cambia el inodo, así que un escritor que abrió y lockeó el inodo viejo no
    bloquea a un escritor posterior que abre el inodo nuevo. Un lock file que
    nunca se reemplaza le da a todos los escritores el mismo inodo por el que
    contender, de modo que el read-modify-replace entero corre un escritor a la
    vez y cada actualización exitosa se capa sobre el resultado anterior en
    lugar de pisarlo (lost-update).

    ``O_NOFOLLOW`` en la apertura del lock rechaza un symlink pre-colocado en
    ese nombre (ELOOP) en vez de lockear a través de un link que este código no
    es dueño.

    Tres contratos más, todos fail-closed (levantan para que el llamador
    devuelva ``False``):

    * **Política de lock file seguro.** Un lock pre-existente debe ser un
      archivo regular, propiedad de este proceso, sin bits de escritura para
      grupo/otros Y con un único nombre (``st_nlink == 1`` — sin aliases
      hardlink). Nuestro propio código sólo crea locks 0600 propiedad de este
      proceso con un único nombre, así que un archivo que viole cualquiera de
      estas fue colocado o manipulado externamente y no es un punto de
      contención confiable — se rechaza (el deployer puede borrarlo) antes
      que flockear un archivo que este código no controla.

    * **``fcntl`` es obligatorio.** El flock es el único mecanismo de
      serialización; si ``fcntl`` no está disponible no hay forma de correr el
      read-modify-replace un escritor a la vez. Saltarse el flock en silencio
      (el código viejo) vuelve a abrir el lost-update: dos escritores
      exitosos que intercalan pierden N-1 de N claves. Con ``_fcntl is None``
      se levanta en vez de escribir sin serializar.

    * **Validación no-follow/stable-lock del lock adquirido.** ``O_NOFOLLOW``
      sólo rechaza el swap en el instante del open; entre ese open y el
      ``flock`` un actor externo puede ``os.replace`` del lock file por otro
      inodo, o reemplazarlo por un symlink, y un sibling que abre la misma
      ruta lockearía ese otro inodo eludiendo la exclusión mutua. El lock file
      es ESTABLE bajo nuestro propio código (nunca lo reemplazamos), así que
      la única forma de que la ruta diverja del fd que acabamos de lockear es
      un reemplazo externo. ``_lock_path_matches_fd`` re-valida justo después
      del flock con ``lstat`` (no-follow) + archivo regular + mismo inodo +
      single-link, y fail-closed en cualquier divergencia. Esto cubre un swap
      entre el open y el flock; el llamador vuelve a validar en el commit para
      cubrir el reemplazo que caiga DESPUÉS del retorno de esta función, antes
      de que el env se comitee. Este checkeo de usuarios es una **instantánea**
      en el límite del directorio padre de confianza / mismo-UID — no una
      garantía absoluta (ver la nota de frontera en ``save_env_keys``).
    """
    fd = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    committed = False  # True sólo cuando el fd pasa todas las validaciones
    try:
        # Política de lock file seguro: un lock pre-existente tiene que ser un
        # archivo regular, nuestro, sin escritura grupo/otros y con un único
        # nombre (single-link). Nuestro código sólo crea locks 0600 propios con
        # un único nombre; cualquier otra cosa fue colocada o manipulada desde
        # afuera, no se flockea.
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("lock file is not a regular file")
        if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
            raise OSError("lock file not owned by caller")
        if st.st_mode & 0o022:
            raise OSError("lock file is group/other-writable")
        if st.st_nlink != 1:
            # Single-link policy (defensa en profundidad, coherente con el
            # checkeo del commit): un lock pre-existente con un alias hardlink
            # (st_nlink > 1) fue manipulado desde afuera — nuestro código sólo
            # crea locks 0600 con un único nombre. El alias es el requisito
            # previo del bypass symlink→alias→mismo-inodo, así que se rechaza
            # acá antes de flockear, además de re-validarse en cada gate.
            raise OSError("lock file has hardlink aliases")
        if _fcntl is None:
            # POSIX-only (dev macOS, deploy Linux); ausente sólo en un port
            # no-POSIX que no es destino de deploy. Sin flock no hay
            # serialización => fail-closed antes que escribir concurrente.
            raise OSError("fcntl unavailable: cannot serialize writer")
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        # Validación post-adquisición (no-follow/stable-lock): la ruta debe
        # seguir siendo, sin seguir symlinks, el mismo archivo regular e inodo
        # que lockeamos, con un único nombre. Cualquier divergencia — swap a
        # otro inodo, ruta vuelta symlink, o alias hardlink — => fail-closed.
        # Cubre un swap entre el open y el flock; el llamador vuelve a chequear
        # en el commit (ventana post-retorno→commit).
        if not _lock_path_matches_fd(lockpath, fd):
            raise OSError("lock file replaced after acquisition")
        committed = True
    finally:
        if not committed:
            try:
                os.close(fd)  # cerrar el fd libera el flock
            except OSError:
                pass
    return fd


def _read_existing_no_follow(path):
    """Lee los bytes actuales del env a través de un único descriptor no-follow.

    Devuelve ``(lines, mode)`` — ``lines`` es el contenido existente spliteado
    y ``mode`` el modo de archivo preservado a reaplicar. Un archivo
    genuinamente AUSENTE devuelve ``([], 0o600)``. Un symlink presente al abrir
    (ELOOP), un archivo ilegible (EACCES), uno no regular (FIFO/device) o bytes
    no decodificables levantan — el llamador falla CERRADO, así un env existente
    se preserva (nunca se borra) y los bytes detrás de un link que este código
    no es dueño nunca se consumen.

    El ``os.open(..., O_NOFOLLOW)`` único reemplaza el TOCTOU que dejaban un
    ``islink()`` por separado más un ``open()`` que sigue links: no hay ventana
    en la que el target pueda volverse un symlink y que sus bytes se lean.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return [], 0o600
    # Cualquier otro fallo de open (ELOOP symlink, EACCES ilegible, ...) =>
    # propagar: el llamador falla cerrado. Sólo un archivo genuinamente
    # ausente arranca de vacío.
    owned = False  # True una vez que fdopen se apropia del fd
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            # FIFO/device/socket: leerlo bloquearía o devolvería basura.
            raise OSError("refusing non-regular env target")
        mode = stat.S_IMODE(st.st_mode)
        f = os.fdopen(fd, encoding="utf-8")  # mode "r" por defecto
        owned = True
        with f:
            return f.read().splitlines(), mode
    finally:
        if not owned:
            try:
                os.close(fd)
            except OSError:
                pass


def _merge_env_lines(existing, wanted):
    """The new env text: ``wanted`` keys replace their ``KEY=`` lines in place
    (every duplicate), the rest are appended in order; unrelated lines and
    comments pass through byte-for-byte."""
    out = []
    written = set()
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                out.append(f"{key}={wanted[key]}")
                written.add(key)
                continue
        out.append(line)
    for key, value in wanted.items():
        if key not in written:
            out.append(f"{key}={value}")
    body = "\n".join(out)
    if body:
        body += "\n"
    return body


def _write_staging(fd, mode, body):
    """Fill the staging fd with ``body`` at ``mode``; False on any failure.

    ``fdopen`` is the one step that takes ownership of ``fd``: when its
    construction fails the fd is still ours and is closed here. Once it has
    succeeded, the ``with`` closes it."""
    try:
        # fdopen se apropia del fd SÓLO si la construcción no falla; si
        # falla, el fd sigue siendo nuestro y hay que cerrarlo abajo
        # para no filtrarlo — nunca escapa la excepción fuera del
        # contrato False-return del installer.
        f = os.fdopen(fd, "w", encoding="utf-8")
    except (OSError, ValueError):
        try:
            os.close(fd)
        except OSError:
            pass
        return False
    try:
        with f:
            os.fchmod(f.fileno(), mode)
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
    except (OSError, ValueError):
        return False
    return True


def _stage_and_commit(path, directory, body, mode, lockpath, lockfd):
    """Stage ``body`` in a unique same-directory temp and rename it onto ``path``.

    Staging/fdopen failures never leak a descriptor or escape the
    ``False``-return contract: ``tempfile.mkstemp``, ``os.fdopen``, the
    write/flush/fsync and the rename are all fail-closed. Any failure
    (including ``fdopen`` construction failing before it takes ownership of the
    staging fd) closes the owned fd, unlinks the staging temp, and returns
    ``False`` — never raises out of the writer after the lock is released,
    never leaves an owned fd or a staging file behind.

    The staging file is a UNIQUE same-directory temp (``tempfile.mkstemp`` —
    ``O_EXCL`` on a fresh random name, so concurrent writers each stage their
    own file and a pre-placed symlink at the old fixed ``path + ".tmp"`` cannot
    redirect the write), fsynced, then renamed into place atomically with
    ``os.replace`` (POSIX rename does not follow a symlink at the destination,
    so a last-instant link swap at ``path`` is replaced, not followed). The
    existing file's mode is preserved so a key update never silently
    re-permissions the env.
    """
    # Staging único mismo-directorio (preservado): mkstemp con O_EXCL sobre
    # un nombre random fresco => dos escritores nunca comparten staging, y
    # un symlink pre-colocado en un nombre fijo no puede redirigir el write.
    # El lock vuelve este replace no-contendido entre escritores que
    # cooperan; fsync del archivo antes del rename para que un crash justo
    # después no deje un archivo de longitud cero (fsync del DIRECTORIO es
    # opcional: este writer no reclama durabilidad ante corte de energía,
    # sólo atomicidad ante crash del proceso).
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".env-", suffix=".tmp")
    except OSError:
        # Staging imposible (sin espacio, dir pasó a read-only entre el
        # lock y el stage, ...): el contrato fail-closed del installer es
        # ``return False``, no dejar escapar la excepción con el lock ya
        # liberado por el finally del llamador.
        return False
    committed = False
    try:
        if not _write_staging(fd, mode, body):
            return False
        # Gate de commit (contrato no-follow/stable-link sobre la ventana
        # adquisición→commit): la ruta del lock debe seguir siendo, sin
        # seguir symlinks, el mismo archivo regular e inodo sobre el que
        # tenemos el flock, con un único nombre (st_nlink == 1). Un
        # reemplazo por otro inodo, un swap a symlink, o un alias hardlink
        # que caiga EN CUALQUIER punto entre la adquisición y este instante
        # — el caso que el chequeo post-adquisición dentro de
        # _acquire_write_lock no puede ver, porque esa función ya retornó —
        # se atrapa acá, fail-closed antes del rename atómico. Instantánea
        # de usuarios en el límite del directorio de confianza (ver
        # save_env_keys): no es garantía absoluta, pero cierra el bypass
        # symlink→alias→mismo-inodo que un os.stat-follow dejaba pasar.
        if not _lock_path_matches_fd(lockpath, lockfd):
            return False
        try:
            os.replace(tmp, path)
        except OSError:
            return False
        committed = True
    finally:
        if not committed:
            # El staging no llegó a renombrarse (fdopen/write/commit gate
            # o replace fallaron): limpiar el temp para no filtrarlo.
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return True


def _release_write_lock(lockfd):
    """Drop the flock and close the lock fd; neither step may raise."""
    if _fcntl is not None:
        try:
            _fcntl.flock(lockfd, _fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        os.close(lockfd)
    except OSError:
        pass


def save_env_keys(pairs):
    """Persist ``KEY=VALUE`` pairs into the effective ``.env`` file.

    This is the only writer for bootstrap/process keys, and it exists for the
    first-run installer: install-owned pack safety (``TURNOS_PRIVATE_PACKS_DIR``,
    ``TURNOS_REQUIRED_PACKS``) is deliberately process/.env-only (see
    ``core.settings.InstallationSettings``) — it can never go through ``guardar()``'s DB
    layer, because consulting the DB can run migrations before a bad required
    pack is rejected.  The installer is the one actor that legitimately writes
    those keys, at install time.

    Idempotent per key: an existing ``KEY=`` line is replaced in place, a new
    key is appended, and unrelated lines and comments are preserved.

    Two real races are closed, both fail-closed so the writer can never erase
    or corrupt the configuration it is asked to extend:

    * **Symlink swap (TOCTOU).** Reading happened as a separate ``islink()``
      check followed by a plain ``open()`` that follows links — between the two,
      the target could become a symlink and the read consumed the link's target
      bytes into the effective env. Now the read is a SINGLE no-follow
      descriptor open (``O_NOFOLLOW`` + ``fstat`` regular-file check): a symlink
      present at open time is refused (ELOOP), so there is no window and bytes
      behind a link this code does not own are never consumed.
    * **Lost update (concurrent read-modify-replace).** Two successful writers
      that interleave each read the same base, stage base+their-key, and the
      last ``os.replace`` silently wins — every other key is lost. Now the
      WHOLE read-modify-replace is serialized by an advisory ``flock`` on a
      stable, no-follow lock file in the same directory (stable across
      replaces, unlike flock on the data fd whose inode churns), so each
      writer re-reads the prior result and layers its key — every successful
      key update survives.

    Fail-closed guarantees preserved alongside:

    * an existing env that CANNOT be read (permission denied, I/O error,
      non-regular file, undecodable bytes) is left byte-for-byte untouched —
      only a genuinely MISSING file (``FileNotFoundError``) starts from empty;
    * the staging file stays a UNIQUE same-directory temp, fsynced, then renamed
      into place atomically; the existing mode is preserved (see
      ``_stage_and_commit``).

    Three more fail-closed contracts on the lock and the stage:

    * **A safe lock-file policy.** A pre-existing lock file must be a regular
      file owned by this process with no group/other write bits AND exactly one
      name (``st_nlink == 1`` — no hardlink aliases). Our own code only ever
      creates 0600 single-link locks owned by this process, so any file that
      violates the policy was placed or tampered with externally and is refused
      rather than flocked.
    * **``fcntl`` is required, not optional:** without it the writer returns
      ``False`` rather than write unserialized (see ``_acquire_write_lock``).
    * **A no-follow, stable, single-link lock identity across the
      acquisition-to-commit window — within a trusted parent directory.**
      ``O_NOFOLLOW`` only refuses a swap AT open time; an external actor who
      can create/rename entries in the lock's directory can ``os.replace`` it
      with a different inode, turn the path into a symlink, or hardlink an
      alias of the locked inode. The fd's identity is re-checked against the
      path both right after the flock AND again at the commit gate (immediately
      before the atomic rename) using ``lstat`` (no-follow) + regular-file +
      same-inode + ``st_nlink == 1``, so a replacement, a symlink swap, or a
      hardlink alias landing between acquisition and commit is detected and the
      write fails closed instead of committing unserialized bytes. This is the
      boundary of the contract, stated truthfully: it holds within a **trusted
      parent directory that only this process's UID can write** (the installer
      and the app run as one trusted user, and the deploy volume's ``/app/data``
      is not shared with an untrusted UID). The final commit-gate check is a
      userspace snapshot, not an absolute guarantee — an attacker who can
      already create/rename entries in that directory is inside the trust
      boundary, and a swap landing strictly between that check and the atomic
      rename is the inherent residual of a userspace advisory lock, not a
      bypass of the stated contract.
    * **Staging/fdopen failures never leak a descriptor or escape the
      ``False``-return contract** (see ``_stage_and_commit``).

    Returns ``True`` on success, ``False`` on any read/permission/symlink,
    lock-policy, lock-acquisition, lock-replacement, or staging/fdopen/rename
    failure so the installer surfaces a clear error instead of leaving config
    half-written or erased.  Resets the dotenv cache so the next
    ``dotenv_values()``/``InstallationSettings.current()`` re-reads immediately.
    """
    path = env_file_path()
    wanted = dict(pairs)
    directory = os.path.dirname(path) or "."
    lockpath = path + ".lock"

    # Seam de test: FUERA del lock, para que la regresión de concurrencia pueda
    # soltar a todos los escritores juntos antes de que contendan por el lock.
    if _SAVE_ENV_KEYS_TEST_HOOK is not None:
        try:
            _SAVE_ENV_KEYS_TEST_HOOK()
        except Exception:  # noqa: BLE001 — el hook nunca tumba un write real
            pass

    try:
        lockfd = _acquire_write_lock(lockpath)
    except OSError:
        # Directorio ausente/no-escribible, o un symlink plantado en el nombre
        # del lock: fallar cerrado antes que escribir sin serializar o a través
        # de un link.
        return False
    try:
        # Lectura no-follow: un symlink en el momento del open (incluido un
        # swap metido en el gap que dejaba el check-then-open) se rechaza, y un
        # env ilegible/no-regular se preserva. Falla cerrado.
        try:
            existing, mode = _read_existing_no_follow(path)
        except (OSError, UnicodeDecodeError):
            return False

        body = _merge_env_lines(existing, wanted)
        if not _stage_and_commit(path, directory, body, mode, lockpath, lockfd):
            return False
    finally:
        _release_write_lock(lockfd)

    _dotenv_cache["vals"] = None
    _dotenv_cache["mtime"] = None
    return True


def _db_overrides():
    """El dict de overrides guardado en catalogo (tipo="config", id="settings"),
    cacheado ~3 s. Si la DB está caída o no hay fila todavía, {} — nunca
    levanta (mismo try/except a lo `theme.current()`)."""
    overlay = _WRITE_OVERLAY.get()
    if overlay is not None:
        return overlay
    while True:
        now = time.time()
        with _db_cache_lock:
            if _db_cache["v"] is not None and now - _db_cache["t"] < _TTL:
                return _db_cache["v"]
            generation = _db_cache_generation
        val = {}
        # Never hold the cache lock over database I/O. A save must be able to
        # invalidate an older read even while that read is waiting for its DB.
        try:
            from app.core import db_catalog
            row = db_catalog.catalog_get("config", "settings")
            if row:
                doc = row.get("doc") or {}
                if isinstance(doc, dict):
                    val = doc
        except Exception:  # noqa: BLE001 — DB caída/ausente => sin overrides
            pass
        with _db_cache_lock:
            if generation != _db_cache_generation:
                # A completed save/reset superseded this read. Neither publish
                # nor return its stale settings to an in-flight caller.
                continue
            _db_cache["v"] = val
            _db_cache["t"] = now
            return val


def _invalidate_db_cache():
    global _db_cache_generation
    with _db_cache_lock:
        _db_cache_generation += 1
        _db_cache["v"] = None
        _db_cache["t"] = 0.0


def _en(d, key):
    v = d.get(key)
    return str(v) if v not in (None, "") else None


def locked():
    """True with TURNOS_CONFIG_LOCK=1 in the real process environment (never
    through .env or DB — reading THIS key can't itself be locked, that would
    be circular). The ops escape hatch: env/.env win over whatever an admin
    saved from Settings, without touching the DB row."""
    return _settings.ProcessSettings.current().config_lock


def get(key, default=None, legacy=None):
    """Lee `key` con precedencia DB > os.environ > .env > default — salvo
    con `locked()` (TURNOS_CONFIG_LOCK=1), donde el override de DB se
    ignora del todo y env/.env ganan siempre.

    `legacy` es un nombre viejo de la misma clave: se consulta DESPUÉS del
    nuevo en cada nivel (reemplaza los `_env2`/`_envnew` que había repetidos
    en ai_health.py, ai_costs.py, reminders.py y waitlist.py)."""
    # A public sandbox may be edited through the real admin. Its integration
    # locks must therefore beat DB overrides too, not only runtime env.
    lock = demo_installation.locked_value(key)
    if lock is not None:
        return lock

    bootstrap = key in _BOOTSTRAP

    if not bootstrap and not locked():
        overrides = _db_overrides()
        v = _en(overrides, key)
        if v is not None:
            return v
        if legacy:
            v = _en(overrides, legacy)
            if v is not None:
                return v

    v = os.environ.get(key)
    if v:
        return v
    if legacy:
        v = os.environ.get(legacy)
        if v:
            return v

    dotenv = _dotenv()
    v = dotenv.get(key)
    if v:
        return v
    if legacy:
        v = dotenv.get(legacy)
        if v:
            return v

    return default


def get_text(name, default=""):
    """`get(name, default)` without surrounding whitespace; `""` when it is unset or empty."""
    return (get(name, default) or "").strip()


def environ_text(name):
    """`name` from the process environment ONLY (no DB override, no env file), without surrounding whitespace;
    `""` when it is unset or empty — for the switches a test or the ops team flips on the process itself."""
    return (os.environ.get(name) or "").strip()


def get_int(key, default):
    """`get(key)` as an integer; `default` when it is unset, empty or not a number."""
    try:
        v = get(key, "")
        return int(v) if v else default
    except ValueError:
        return default


def guardar(overrides):
    """Persiste (merge parcial) los overrides de DB. `overrides` es un dict
    key->value; un valor None o "" borra esa clave. Invalida el cache local
    y devuelve el dict resultante completo."""
    if set(overrides) & _INSTALLATION_ONLY:
        raise ValueError("Installation recovery keys cannot be stored in the database")
    from sqlalchemy import func, select
    from app.core import db
    from app.core import db_catalog
    from app.core import db_ai
    from app.core import ai_register, sqlcore
    from app.core.schema_model import catalog
    # Serialize overlay writes and their evidence in one transaction.
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, select(func.pg_advisory_xact_lock(func.hashtext("config:settings"))))
        row = sqlcore.run(cur, select(catalog.c.doc).where(
            catalog.c.type == "config", catalog.c.id == "settings").with_for_update()).fetchone()
        before = dict(row['doc'] if row else {})
        actual = dict(before)
        for k, v in overrides.items():
            if v in (None, ""):
                actual.pop(k, None)
            else:
                actual[k] = v
        db_catalog.catalog_upsert("config", "settings", actual, connection=conn)
        events = ai_register.setting_events(before, actual)
        db_ai.ai_register_append(events, connection=conn)
        if events:
            token = _WRITE_OVERLAY.set(actual)
            try:
                ai_register.observe_inventory(ai_register.inventory(), connection=conn, record_changes=False)
            finally:
                _WRITE_OVERLAY.reset(token)
    _invalidate_db_cache()
    return actual


def _reset_cache_para_tests():
    """Sólo para tests: fuerza a releer .env y DB en la próxima llamada."""
    _dotenv_cache["vals"] = None
    _dotenv_cache["mtime"] = None
    _invalidate_db_cache()
