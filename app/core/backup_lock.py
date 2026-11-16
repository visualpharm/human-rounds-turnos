"""The lock on deleting or overwriting backups on the municipal installation.

On a `*.gob.ar` TURNOS_PUBLIC_ORIGIN (`core.config.government_installation`)
the application may write backups, but it has no permission to delete them or
to overwrite them, by any path: the remote purge, the rotation of the
encrypted full backups, the manual tools of the app, the operator helpers
(the municipality, through the product owner, Teams 2026-10-26 19:48: «La app puede
escribir respaldos; lo que no puede es borrarlos ni sobrescribirlos por ningún
camino»). Everywhere else (test stands, demo) nothing changes.

The lock sits in the two remote clients, before any byte leaves:

- the B2 native API of the data/ backups (`ops.data_backups._req_json`) asks
  `check_b2_call`: only the calls in `B2_ALLOWED` go out (authorize, upload
  URL, upload, list, download) — `b2_delete_file_version`, `b2_hide_file`,
  `b2_copy_file`, `b2_cancel_large_file` and anything unknown are refused;
  an upload goes out only under a content-addressed name (`addressed_name`:
  the name ends in `~` + the SHA-256 of the bytes), so one name can never
  hold two different contents;
- the S3 transport of the encrypted full backups (`core.backup_s3.S3.request`)
  asks `check_s3_request`: no DELETE of any kind (an S3 DELETE by name is a
  hide marker on B2 and needs only writeFiles), no multi-object delete, no
  server-side copy, and a new object (PUT or the start of a multipart upload)
  only when a HEAD says the name does not exist yet.

A refusal raises `BackupWriteForbidden` («sin permiso en esta instalación»)
and logs one ERROR line (never a value, a name or a key). The upper paths
check `locked()` first, so nothing is left half done: the rotation and the
cleanup of incomplete uploads are skipped, the connection probe keeps its
test object, the settings refuse to switch the rotation on. Light on purpose
(only `core.config` and the standard library): `core.backup_s3` and the
backup modules import it at import time.
"""

import hashlib
import re
import sys
import urllib.parse

from app.core import config

MESSAGE = "sin permiso en esta instalación"
# The panel's texts (locale keys `backup.forbidden`, `backup.probe_ok_kept`), in Spanish.
FORBIDDEN_ES = ("Sin permiso en esta instalación: la aplicación no borra ni sobrescribe copias de "
                "seguridad. La rotación y la limpieza de copias las hace el municipio.")
PROBE_KEPT_ES = ("Conexión comprobada. El objeto de prueba queda en el bucket: en esta instalación "
                 "la aplicación no borra.")

# The B2 native calls a government installation may make: read, list, upload.
B2_ALLOWED = frozenset({
    "b2_authorize_account", "b2_get_upload_url", "b2_upload_file",
    "b2_list_file_versions", "b2_list_file_names", "b2_get_file_info",
    "b2_download_file_by_id",
})

_B2_CALL = re.compile(r"/b2api/v\d+/(b2_[a-z_]+)")
_ADDRESSED = re.compile(r"~([0-9a-f]{64})$")


class BackupWriteForbidden(PermissionError):
    """A delete or an overwrite of a backup asked on the municipal
    installation. `action` names what was refused (no value, name or key)."""

    def __init__(self, action):
        super().__init__(f"{action}: {MESSAGE} (the application does not delete or overwrite backups here)")
        self.action = action


def log(msg):
    """The `[backups]` line on stderr that the backup modules write (`data_backups`, `purge_guard`)."""
    print(f"[backups] {msg}", file=sys.stderr, flush=True)


_log = log


def locked():
    """True on the municipal installation: no delete, no overwrite."""
    return config.government_installation()


def warn(action):
    """Logs the ERROR line for a refused `action` (a path that must not raise)."""
    _log(f"ERROR: {action} refused: {MESSAGE} — the application does not delete or "
         f"overwrite backups on this installation")


def refuse(action):
    """Logs the ERROR line and raises `BackupWriteForbidden` for `action`."""
    warn(action)
    raise BackupWriteForbidden(action)


def addressed_name(remote_name, sha256):
    """The name a data/ backup takes on the municipal installation: the
    plain name plus `~` and the SHA-256 of its bytes, so a name in use is
    never given different bytes."""
    return f"{remote_name}~{sha256}"


def plain_name(remote_name, sha256=None):
    """`remote_name` without the content suffix of `addressed_name`. With
    `sha256` the suffix is removed only when it is exactly that digest (a
    restore knows the bytes it expects); without it, any 64-hex suffix."""
    m = _ADDRESSED.search(remote_name or "")
    if not m or (sha256 is not None and m.group(1) != str(sha256).lower()):
        return remote_name
    return remote_name[:m.start()]


def b2_call_name(url):
    """The B2 native call a URL names (`b2_upload_file` for an upload URL),
    or None when it names none."""
    m = _B2_CALL.search(urllib.parse.urlsplit(url).path)
    return m.group(1) if m else None


def check_b2_call(url, headers=None, payload=None):
    """The lock for one B2 native request; nothing to check off government.

    A call outside `B2_ALLOWED` is refused. An upload must carry an
    `X-Bz-File-Name` that ends in `~` + the SHA-256 of `payload`."""
    if not locked():
        return
    name = b2_call_name(url)
    if name not in B2_ALLOWED:
        refuse(f"B2 {name or 'request'}")
    if name == "b2_upload_file":
        file_name = urllib.parse.unquote(str((headers or {}).get("X-Bz-File-Name") or ""))
        m = _ADDRESSED.search(file_name)
        if payload is None or not m or m.group(1) != hashlib.sha256(payload).hexdigest():
            refuse("B2 upload under a name that does not carry the SHA-256 of its bytes (overwrite)")


def check_s3_request(method, query=None, extra=None, exists=None):
    """The lock for one S3 request; nothing to check off government.

    `exists()` answers whether the object name is already taken (a HEAD); it
    is asked only for a new object: a PUT that is not a part of a multipart
    upload, or the start of a multipart upload."""
    if not locked():
        return
    query = query or {}
    method = method.upper()
    if method == "DELETE":
        refuse("S3 DELETE")
    if method == "POST" and "delete" in query:
        refuse("S3 multi-object delete")
    if any(str(k).lower() == "x-amz-copy-source" for k in (extra or {})):
        refuse("S3 copy")
    new_object = ((method == "PUT" and "partNumber" not in query and "uploadId" not in query)
                  or (method == "POST" and "uploads" in query))
    if new_object:
        if exists is None or exists():
            refuse("S3 upload onto an existing object name (overwrite)")
