"""account_photo.py — patient profile photo (Pinamar Turnos).

Where the photo comes from, by priority: (1) uploaded by hand from My
account, or cropped from the DNI PORTRAIT at registration — the crop is done
by account-form.js in the browser with the bbox /api/ocr-dni returns (the
image is never processed here); (2) if nothing was uploaded, the account
email's GRAVATAR (responsable del producto 2026-07-18: "en cuanto tipean el email, traé la
imagen de Gravatar; si la traés, la usás"). Gravatar comes from the email's
MD5 hash and is only queried if the email has no photo of its own — so the
avatar "just appears" on the rail AND on the menu's "My account" button
without the person uploading anything. Always the session's OWNER: the file
key is their DNI and there's no way to touch someone else's photo.

Native routes owned by this module through ``account_photo_router.py``:
  GET    /api/account/photo          serves the photo (upload > gravatar; 404 if none)
  GET    /api/account/photo?probe=1  JSON {source: upload|gravatar|none} (for the rail)
  POST   /api/account/photo          uploads (or replaces) the photo  {data_b64}
  DELETE /api/account/photo          removes the UPLOAD (if there's a gravatar, falls back to it)

Storage: `data/cuenta-fotos/<dni>.<ext>` — `data/` is the persistent volume
at runtime (same criteria as doctor_photos.py / data/ordenes). A user has ONE
file: replacing it deletes the other extensions.

The photo is personal data: served ONLY to the session's owner (there's
never a public URL by DNI — it would be enumerable) and with
Cache-Control: private (cached in the owner's own browser, never in a
shared/proxy cache).

Upload body = JSON `{data_b64}` — same base64 contract as
booking_requests.py/doctor_photos.py (no multipart: cgi died in py3.13). The mime
isn't declared, and magic bytes alone were never enough: the upload goes through
`core/photo_guard.normalize()`, which decodes the actual bytes (EXIF orientation
applied, pixels bounded, long edge capped at 600) and stores a real upright JPEG
— a broken file, an SVG or an oversized dump never lands.

Import-safe: accounts/db are imported lazily; no DB → clean 401
(current_user already degrades to None). Never takes down server startup.
"""

import base64
import hashlib
import os
import re
import time
import urllib.request

from app.cuentas import session_user
from app.cuentas.native_result import Result
from app.core import ids as _ids
from app.core import paths

# Tier 2 batch G: this file now lives one level deeper (cuentas/), not at
# the repo root — PHOTOS_DIR has to keep pointing at the repo's real
# `data/` (the persistent volume at runtime), so go up TWO dirnames, not
# one.
ROOT = paths.PROJECT_ROOT
PHOTOS_DIR = os.path.join(ROOT, "data", "cuenta-fotos")

MAX_BYTES = 5 * 1024 * 1024  # 5 MB (the client already shrinks to <=600px)

# Gravatar: turned off with TURNOS_GRAVATAR_OFF=1. In-memory cache keyed by
# email hash (including the negative: "this email has no gravatar") so we
# don't hit the network on every avatar load. Short TTL: if someone just
# created their gravatar, it shows up within minutes.
_GRAVATAR_OFF = os.environ.get("TURNOS_GRAVATAR_OFF", "") in ("1", "true", "yes")
_GRAV_TTL = 600  # 10 min
_grav_cache = {}  # md5(email) -> (expires_ts, (data, ext, mime) | None)

_EXTS = ("jpg", "jpeg", "png", "webp")
_MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

_SAFE_DNI_RE = re.compile(r"^\d{7,9}$")


# Session's user, or None. Deliberate indirection (same pattern as booking_requests._get_user): the lazy
# import lives in `session_user` and tests monkeypatch this name.
_user = session_user.current_or_none


def _attr(u, k):
    return (u.get(k) if isinstance(u, dict) else getattr(u, k, None)) if u else None


def _dni_of(u):
    dni = _ids.dni(_attr(u, "dni"))
    return dni if _SAFE_DNI_RE.match(dni) else None


def _gravatar(email):
    """Bytes of the email's gravatar (data, ext, mime) or None if the email
    doesn't have one (or Gravatar is off/down). d=404: Gravatar responds 404
    instead of serving a generic avatar, so we can tell "has one" from
    "doesn't". Best-effort with an in-memory cache — never raises upward."""
    if _GRAVATAR_OFF:
        return None
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None
    h = hashlib.md5(email.encode("utf-8")).hexdigest()  # noqa: S324 (Gravatar's own spec)
    now = time.time()
    hit = _grav_cache.get(h)
    if hit and hit[0] > now:
        return hit[1]
    result = None
    try:
        url = "https://www.gravatar.com/avatar/%s?d=404&s=400&r=g" % h
        req = urllib.request.Request(url, headers={"User-Agent": "sana-turnos"})
        with urllib.request.urlopen(req, timeout=3) as r:  # noqa: S310 (fixed https host)
            if getattr(r, "status", 200) == 200:
                data = r.read(MAX_BYTES + 1)
                if data and len(data) <= MAX_BYTES:
                    sniff = _sniff(data)
                    if sniff:
                        result = (data, sniff[0], sniff[1])
    except Exception:  # noqa: BLE001  (404 = HTTPError; any failure => no gravatar)
        result = None
    _grav_cache[h] = (now + _GRAV_TTL, result)
    return result


def _sniff(data):
    """(ext, mime) by magic bytes — only for the GRAVATAR fetch (it never
    stores user uploads). Uploads themselves go through photo_guard, which
    decodes; this stays for the network edge, where a claim is all we need.
    The family is `photo_guard.sniff`'s; Gravatar never serves HEIC, so that
    one is not taken."""
    from app.core import photo_guard
    kind = photo_guard.sniff(data)
    return (kind, _MIME_BY_EXT[kind]) if kind in ("png", "jpg", "webp") else None


def _existing(dni):
    for ext in _EXTS:
        p = os.path.join(PHOTOS_DIR, f"{dni}.{ext}")
        if os.path.isfile(p):
            return p
    return None


def photo_paths(dni):
    """Every uploaded-photo file this DNI has on disk (a user has ONE, but a
    replaced extension can linger): what the data-subject export copies and the
    erasure removes (core/subject_export.py, core/subject_erase.py)."""
    return [p for p in (os.path.join(PHOTOS_DIR, f"{dni}.{ext}") for ext in _EXTS)
            if os.path.isfile(p)]


def _resolve_identity(request):
    """Resolve identity from the native request's session cookie.
    Returns `(dni, email, None)` on success, or `(None, None, Result)` with
    the ready-made 401."""
    u = _user(request)
    dni = _dni_of(u)
    if not dni:
        return None, None, Result.json({"error": "no_autenticado"}, status=401)
    email = str(_attr(u, "email") or "")
    return dni, email, None


def _probe_core(dni, email):
    """Avatar source, so the rail knows whether "Remove" makes sense (only an
    UPLOADED photo can be removed from here; the gravatar comes from the
    email). Pure: `dni`/`email` already resolved, `Result` out."""
    if _existing(dni):
        source = "upload"
    elif _gravatar(email):
        source = "gravatar"
    else:
        source = "none"
    return Result.json({"ok": True, "source": source})


def _serve_core(dni, email):
    """Pure."""
    path = _existing(dni)
    data = ext = None
    grav = False
    if path:
        ext = path.rsplit(".", 1)[1]
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return Result.json({"error": "no se pudo leer la foto"}, status=500)
    else:
        # No own photo: try the account email's gravatar.
        g = _gravatar(email)
        if g:
            data, ext, _mime = g
            grav = True
    if data is None:
        return Result.json({"error": "sin_foto"}, status=404)
    # personal data: "private" so only the owner's own browser cache keeps it
    # (never a shared/proxy cache) — but DO cache it, otherwise the rail's
    # avatar repeats the download on every /account load (the product owner 2026-07-17).
    # The client cache-busts with ?t= only when a new photo is uploaded. The
    # gravatar caches shorter: if the person changes it on Gravatar, or just
    # set their email, the avatar catches up within minutes.
    return Result.raw(
        data, content_type=_MIME_BY_EXT.get(ext, "application/octet-stream"),
        extra_headers={"Cache-Control": "private, max-age=" + ("600" if grav else "86400")})


def _upload_core(dni, body):
    """Pure: `dni` + already-parsed `body` in, `Result` out."""
    data_b64 = body.get("data_b64") or ""
    if data_b64.strip().startswith("data:") and "," in data_b64:
        data_b64 = data_b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(data_b64, validate=False)
    except Exception:  # noqa: BLE001
        return Result.json({"error": "la foto no se pudo decodificar"}, status=400)

    # The shared gate decodes what the bytes actually are (a JPEG header with
    # a broken body never passes), applies EXIF orientation, bounds pixels
    # and re-encodes — what lands on disk is always a real, upright, <=600px
    # JPEG, whatever the phone produced (HEIC included, when the server has
    # the decoder; otherwise the guard answers with the export-to-JPG remedy).
    from app.core import photo_guard
    try:
        raw, ext, _mime = photo_guard.normalize(raw, max_dim=600, max_bytes=MAX_BYTES)
    except photo_guard.PhotoRejected as e:
        return Result.json(e.error, status=e.status)

    os.makedirs(PHOTOS_DIR, exist_ok=True)
    # One user = one file: an earlier upload in a different extension gets
    # deleted (otherwise _existing would always serve the first of _EXTS).
    for old_ext in _EXTS:
        if old_ext == ext:
            continue
        old = os.path.join(PHOTOS_DIR, f"{dni}.{old_ext}")
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass

    dest = os.path.join(PHOTOS_DIR, f"{dni}.{ext}")
    tmp = dest + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, dest)  # atomic: never left half-written
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return Result.json(
            {"ok": False, "error": "No se pudo guardar la foto (disco lleno)."}, status=507)

    return Result.json({"ok": True})


def move(viejo, nuevo):
    """The uploaded photo follows a DNI change (`db_accounts.change_dni`): the file is
    named by DNI, so left behind it would greet the next person who
    registers with the old one. Best-effort and never overwrites a photo the
    new DNI already has. Returns True if a file moved."""
    src = _existing(viejo)
    if not src or _existing(nuevo):
        return False
    ext = src.rsplit(".", 1)[1]
    try:
        os.replace(src, os.path.join(PHOTOS_DIR, f"{nuevo}.{ext}"))
        return True
    except OSError:
        return False


def _remove_core(dni):
    """Pure."""
    for ext in _EXTS:
        p = os.path.join(PHOTOS_DIR, f"{dni}.{ext}")
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return Result.json({"ok": True})
