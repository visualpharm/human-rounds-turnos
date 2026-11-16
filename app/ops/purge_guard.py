"""The lock on the remote purge of backups (TURNOS_BACKUP_PURGE_REMOTE), and
the signals for the deletions asked where they are locked.

The application has no right to delete backup copies on the municipal
installation (a `*.gob.ar` TURNOS_PUBLIC_ORIGIN, `core.config.government_installation`):
the municipality runs that purge itself. Everywhere else (test stands, demo)
the purge is on unless `=0`. The lock on every other delete or overwrite there
(the rotation of the full backups included) is `core.backup_lock`; this module
only says loudly when the rotation is switched on where it cannot run.

Light on purpose — only `core.config` and the standard library — so the web
entrypoint can report at start without importing the backup module (and the
database layer behind it) ahead of the required-pack check. The backup sweep,
the worker and the «Sistema» panel read the same three functions.
"""

from app.core import config
from app.core.backup_lock import log as _log


def _env(key, default=""):
    return config.get(key, default)


def remote_purge_enabled():
    """Whether this installation may delete remote copies of backups.

    Always False on the municipal installation, whatever the environment or
    the panel says. Elsewhere on unless TURNOS_BACKUP_PURGE_REMOTE=0."""
    if config.government_installation():
        return False
    return _env("TURNOS_BACKUP_PURGE_REMOTE") != "0"


def remote_purge_ignored():
    """True when somebody asked for the remote purge (TURNOS_BACKUP_PURGE_REMOTE
    other than empty or `0`) on the municipal installation, where the lock in
    `remote_purge_enabled` keeps it off. The service still starts — a wrong
    line in the environment must not take a live installation down — so this
    is what the loud signals read: the ERROR at start and in every backup
    pass (`warn_purge_ignored`) and the warning in «Sistema»."""
    return (config.government_installation()
            and _env("TURNOS_BACKUP_PURGE_REMOTE").strip() not in ("", "0"))


def warn_purge_ignored(where):
    """Logs the ERROR line for `remote_purge_ignored` (never a value); True
    when it did."""
    if not remote_purge_ignored():
        return False
    _log(f"ERROR: TURNOS_BACKUP_PURGE_REMOTE asks for the remote purge of backups, which is ignored on "
         f"this installation ({where}): the application does not delete backups here. "
         f"Remove the variable or set it to 0.")
    return True


def rotation_ignored():
    """True when the rotation of the encrypted full backups is switched on
    (TURNOS_BACKUP_RETENTION_APPLY=1, environment or panel) on the municipal
    installation, where `core.backup_lock` keeps it off: the backups still
    run, nothing is deleted, and this is what the loud signals read (the
    ERROR at start and in every full backup, `warn_rotation_ignored`, and the
    warning in the «Sistema» block of the backups)."""
    return (config.government_installation()
            and _env("TURNOS_BACKUP_RETENTION_APPLY").strip() == "1")


def warn_rotation_ignored(where):
    """Logs the ERROR line for `rotation_ignored` (never a value); True when
    it did."""
    if not rotation_ignored():
        return False
    _log(f"ERROR: TURNOS_BACKUP_RETENTION_APPLY=1 asks for the rotation of the full backups, which is "
         f"ignored on this installation ({where}): the application does not delete backups here. "
         f"Set it to 0.")
    return True
