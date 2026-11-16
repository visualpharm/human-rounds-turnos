"""doctor_photos.py — admin-uploaded profile photo for a doctor (Pinamar Turnos).

Owner of this module:
  GET    /api/admin/doctors/<id>/photo   current status (for the editor's preview)
  POST   /api/admin/doctors/<id>/photo   uploads (or replaces) the photo
  DELETE /api/admin/doctors/<id>/photo   removes it (falls back to the offline sprite / initials)
  GET    /doctor-photo/<id>              serves the uploaded file (public, no session)
  GET    /api/mi/foto                    a 'medico' session's OWN photo status
  POST   /api/mi/foto                    a 'medico' session uploads/replaces THEIR OWN photo
  DELETE /api/mi/foto                    a 'medico' session removes THEIR OWN photo (falls
                                          back to the offline sprite / initials — doing
                                          nothing at all leaves whatever photo they had)

The three `/api/mi/foto` routes (2026-08-27, hospital meeting: "a doctor should
be able to manage their own photo") are the SAME business logic as the admin
routes above (`_preview_body`/`_upload_body`/`_remove_body`), reached through a
different gate: `_mine_identity()` requires a 'medico' session
(`permitir_medico=True`) and resolves `mid` from `db_operators.operator_doctor_id(usuario)`
— the id NEVER comes from the URL or the request body (identity always from the
session, repo invariant). A medico account with no doctor linked gets
`cuenta_sin_medico`, same shape as `/api/admin/my-appointments`
(doctor_appointments.py) uses for the same situation. Because it's the exact
same body functions, the sprite invalidation on upload/remove
(`_invalidar_sprite()`) is automatic — no separate code path to keep in sync.

Storage: `data/medico-fotos/<id>.<ext>` — `data/` is the persistent runtime
volume (webdata/ is baked into the image and can't be written there, see
CLAUDE.md "Deploy"). A doctor has ONE file: replacing it deletes whatever
other extensions a previous upload in a different format might have left.

Portrait priority: admin upload (this module) > offline sprite
(webdata/medico-photo-sprite.webp, generated offline) > initials. The one
that applies that priority is `site_doctors._photo_style` — it imports
`photo_url()` from here. This module does NOT import patient_web/site_*.py at module
level (to avoid the cycle); the only use that needs hospital's helper (the
admin preview, which wants the portrait ALREADY resolved with the same
priority) imports it inside the function itself, when the request arrives —
by then both modules have finished loading, so there's no real cycle.

Upload body = JSON `{filename, mime, data_b64}` — same contract as
booking_requests.py/preparation.py (no multipart: cgi is dead in py3.13). The
mime the client declares is NOT trusted: both photo surfaces decode actual
bytes through `core/photo_guard`; doctor portraits retain the private original
and their separate portrait rendering (portrait_processing.py). Center photos
are normalized into an upright, bounded JPEG before storage.

Caching for `/doctor-photo/<id>`: the public URL carries `?v=<mtime epoch>`
(same pattern as the sprite's `?v=N` and the site's JS/CSS). Since the mtime
changes as soon as the file is replaced, the URL changes with it — so the
response can be declared cacheable "forever" (`immutable`) without risking
Cloudflare keeps serving an old photo under a URL that no longer maps to it
(see docs/solutions/deploy/cloudflare-js-cache.md: the edge ignores origin's
no-cache, so the only reliable way to invalidate is to change the URL).
"""

import base64
import os
import re
import json
import fcntl
import tempfile
import shutil
from contextlib import contextmanager
from urllib.parse import quote

from app.patient_web import catalog
from app.core.photo_guard import PhotoRejected
from app.core.status_error import BodyStatusError
from app.core import paths

ROOT = paths.PROJECT_ROOT
PHOTOS_DIR = os.path.join(ROOT, "data", "medico-fotos")

# Same mechanism for the two entities that show a portrait (the product owner,
# 2026-07-24: "for new center, there should be a photo"). A center has
# neither an offline sprite nor initials — it either has an uploaded photo,
# or it has nothing — so only what the preview returns changes, not storage.
CENTER_PHOTOS_DIR = os.path.join(ROOT, "data", "centro-fotos")

_ENTITIES = {
    "doctors": {
        "tipo": "medicos",
        "publico": "doctor-photo",
        "no_encontrado": "médico no encontrado",
        "avatar": True,      # sprite + initials as fallback
    },
    "centers": {
        "tipo": "centros",
        "publico": "center-photo",
        "no_encontrado": "centro no encontrado",
        "avatar": False,
    },
}


def _dir(ent):
    """Entity's folder, read from the module global on EVERY CALL — not
    cached inside _ENTITIES: tests patch `PHOTOS_DIR` to write into a tmpdir,
    and freezing the path at import time would break that seam (same issue
    as `catalog._DIR`, see CLAUDE.md)."""
    return CENTER_PHOTOS_DIR if ent == "centers" else PHOTOS_DIR

MAX_BYTES = 20 * 1024 * 1024  # professional originals; decoded pixels are separately bounded

_EXTS = ("jpg", "jpeg", "png", "webp")
_MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _invalidar_sprite():
    """The small round avatars all come out of one shared atlas
    (avatar_sprite.py) keyed by the sources' mtimes, and that key is trusted
    for a few seconds so it isn't re-stat'd on every face drawn. Replacing a
    photo has to show up NOW, not in five seconds. Imported here and not at
    module level: avatar_sprite imports THIS module."""
    try:
        from app.patient_web import avatar_sprite
        avatar_sprite.invalidate()
    except Exception:
        pass


def _existing(mid, ent="doctors"):
    """(path, mtime epoch) of the currently uploaded file for `mid`, or None."""
    if not mid or not _SAFE_ID_RE.match(mid):
        return None
    carpeta = _dir(ent)
    for ext in (("webp", "jpg", "jpeg", "png") if ent == "doctors" else _EXTS):
        p = os.path.join(carpeta, f"{mid}.{ext}")
        if os.path.isfile(p):
            try:
                return p, int(os.path.getmtime(p))
            except OSError:
                return None
    return None


def photo_url(mid, ent="doctors"):
    """Public URL of the admin-uploaded photo for `mid`, or None if there's
    no upload — this is what patient_web/site_*.py consumes to decide the portrait's
    priority (admin > sprite > initials)."""
    info = _existing(mid, ent)
    if not info:
        return None
    path, mt = info
    mt = os.stat(path).st_mtime_ns
    return f"/{_ENTITIES[ent]['publico']}/{quote(mid, safe='')}?v={mt}"


def photo_url_center(slug):
    """Public URL of center `slug`'s photo, or None. A center has no
    fallback (no sprite, no initials): without an upload, there's no
    portrait."""
    return photo_url(slug, "centers")


def _doc(mid, ent="doctors"):
    """Merged doc (json+overlay) for entity `mid`, or None. Strict mode
    (catalog.admin_list) — if the DB goes down, lets db.DbUnavailable
    propagate so the router's 503 guard answers instead of letting a photo pass or
    be rejected against an id that might no longer exist."""
    tipo = _ENTITIES[ent]["tipo"]
    key = catalog.id_key(tipo)
    for it in catalog.admin_list(tipo):
        if it.get(key) == mid:
            return it
    return None


_ENTITY_BY_PUBLIC = {"doctor": "doctors", "center": "centers"}


class DoctorPhotoError(BodyStatusError):
    """Carries the exact `(status, body)` a domain function raises.
    `doctor_photos_router.py`'s native endpoints catch this and build their
    response bytes from it — `db.DbUnavailable` is deliberately NOT wrapped
    here: the router has its own 503 guard (a try/except) around the domain
    call."""


# ---- pure domain functions (the native router's entry points) ------

def photo_bytes(mid, ent="doctors"):
    """(data, mime) for the admin-uploaded file `mid`/`ent`. Raises
    `DoctorPhotoError` when there's no upload or it can't be read."""
    info = _existing(mid, ent)
    if not info:
        raise DoctorPhotoError(404, {"error": "sin foto"})
    path, _mt = info
    ext = path.rsplit(".", 1)[1]
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    try:
        with open(path, "rb") as f:
            return f.read(), mime
    except OSError:
        raise DoctorPhotoError(500, {"error": "no se pudo leer la foto"})


def preview_payload(mid, ent="doctors"):
    """The editor-preview body for `mid`/`ent`. Raises `DoctorPhotoError(404,
    ...)` when the entity doesn't exist in the catalog; may raise
    `db.DbUnavailable` (caller's guard turns that into a 503, unchanged)."""
    cfg = _ENTITIES[ent]
    m = _doc(mid, ent)
    if m is None:
        raise DoctorPhotoError(404, {"error": cfg["no_encontrado"]})
    url = photo_url(mid, ent)
    if cfg["avatar"]:
        from app.patient_web import site_doctors
        # the 64 px circle is one of the small round avatars: its tile comes from
        # the shared atlas; the full portrait only when the atlas can't be built
        style = site_doctors._photo_style(mid, chico=True)
        ini, color = ("", 0) if style else site_doctors._avatar(m.get("nombre"))
    else:
        # a center has no fallback: either there's an uploaded photo, or the
        # circle stays empty. The 64 px circle takes its tile from the shared
        # atlas (the same cut the public home shows); the upload itself, which
        # can be several MB, only when the atlas can't be built.
        style = ""
        if url:
            from app.patient_web import site_doctors
            style = (site_doctors.center_circle_style(mid)
                     or f"background-image:url('{url}');background-size:cover;"
                        "background-position:center")
        ini, color = "", 0
    return {
        "ok": True,
        "has_upload": url is not None,
        "foto_url": url,
        "style": style,
        "ini": ini,
        "color": color,
        **(_portrait_status(mid) if ent == "doctors" else {}),
    }


def _private_dir(mid):
    if not mid or not _SAFE_ID_RE.fullmatch(mid):
        raise DoctorPhotoError(404, {"error": "médico no encontrado"})
    return os.path.join(PHOTOS_DIR, '.originals', mid)


def original_bytes(mid):
    try:
        with open(os.path.join(_current_bundle(mid), 'original'), 'rb') as source:
            return source.read()
    except OSError as exc:
        raise DoctorPhotoError(404, {"error": "sin original"}) from exc


def _current_bundle(mid):
    private = _private_dir(mid)
    public = os.path.join(PHOTOS_DIR, mid + '.webp')
    target = os.path.realpath(public)
    if os.path.islink(public) and os.path.commonpath([target, os.path.realpath(private)]) == os.path.realpath(private):
        return os.path.dirname(target)
    return private  # previous flat metadata or a legacy public upload


def _portrait_status(mid):
    try:
        with open(os.path.join(_current_bundle(mid), 'metadata.json')) as source:
            meta = json.load(source)
        return {"status": meta['status'], "rotation": meta.get('rotation', 0), "has_original": True}
    except (OSError, ValueError, KeyError):
        return {"status": "original", "rotation": 0, "has_original": False}


def _atomic_file(path, raw):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(raw)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def _portrait_lock(mid):
    private = _private_dir(mid)
    os.makedirs(private, mode=0o700, exist_ok=True)
    with open(os.path.join(private, 'lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield private
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _save_portrait(mid, body, raw=None):
    from app.core import config
    from app.patient_web.portrait_processing import portrait
    action = body.get('action', 'upload')
    if action not in ('upload', 'restore', 'process', 'rotate'):
        raise DoctorPhotoError(400, {"error": "acción de foto inválida"})
    try:
        with _portrait_lock(mid) as private:
            prior = _portrait_status(mid)
            if raw is None:
                try:
                    raw = original_bytes(mid)
                except DoctorPhotoError:
                    # Adopt a legacy upload without changing a byte of its source.
                    raw, _mime = photo_bytes(mid)
            rotation = body.get('rotation', prior.get('rotation', 0) if action != 'upload' else 0)
            enhance = action != 'restore' and (action == 'process' or config.get('TURNOS_PHOTO_AUTO_ENHANCE', '1') != '0')
            try:
                output = portrait(raw, rotation, enhance)
            except PhotoRejected as exc:
                raise DoctorPhotoError(exc.status, exc.error) from exc
            except Exception as exc:
                # Invalid/oversized images are input failures, not disk errors.
                raise DoctorPhotoError(400, {"error": "La foto no se pudo procesar. Elegí un JPG, PNG o WEBP válido."}) from exc
            # Validate/process before any write; the uploaded original is never transformed.
            status = 'procesada' if enhance else 'original'
            bundle = tempfile.mkdtemp(prefix='portrait-', dir=private)
            published = False
            pointer = os.path.join(bundle, 'pointer')
            try:
                _atomic_file(os.path.join(bundle, 'original'), raw)
                _atomic_file(os.path.join(bundle, 'portrait.webp'), output)
                _atomic_file(os.path.join(bundle, 'metadata.json'), json.dumps({"status": status, "rotation": rotation}).encode())
                # One atomic pointer commits public image, status and original
                # together. Failed writes retain the previous complete bundle.
                os.symlink(os.path.relpath(os.path.join(bundle, 'portrait.webp'), PHOTOS_DIR), pointer)
                os.replace(pointer, os.path.join(PHOTOS_DIR, mid + '.webp'))
                published = True
            finally:
                if not published:
                    shutil.rmtree(bundle)
            for ext in _EXTS:
                if ext != 'webp':
                    path = os.path.join(PHOTOS_DIR, mid + '.' + ext)
                    if os.path.exists(path):
                        os.unlink(path)
    except DoctorPhotoError:
        raise
    except OSError as exc:
        raise DoctorPhotoError(507, {"ok": False, "error": "No se pudo guardar la foto (disco lleno)."}) from exc
    _invalidar_sprite()
    return {"ok": True, "foto_url": photo_url(mid), **_portrait_status(mid)}



def upload_payload(mid, body, ent="doctors"):
    """Validates + saves the upload from a parsed JSON `body`
    (`{data_b64, ...}`), returns `{"ok": True, "foto_url": ...}`. Raises
    `DoctorPhotoError` on any of the same 400/404/413/507 conditions the
    legacy `_upload` checked, in the SAME order; may raise
    `db.DbUnavailable` from the initial catalog lookup (caller's guard)."""
    cfg = _ENTITIES[ent]
    if _doc(mid, ent) is None:
        raise DoctorPhotoError(404, {"error": cfg["no_encontrado"]})

    if not isinstance(body, dict):
        raise DoctorPhotoError(400, {"error": "foto inválida"})
    if ent == 'doctors' and body.get('action') in ('restore', 'process', 'rotate'):
        return _save_portrait(mid, body)

    data_b64 = (body or {}).get("data_b64") or ""
    if not isinstance(data_b64, str):
        raise DoctorPhotoError(400, {"error": "la foto no se pudo decodificar"})
    if data_b64.strip().startswith("data:") and "," in data_b64:
        data_b64 = data_b64.split(",", 1)[1]
    limit = MAX_BYTES if ent == "doctors" else 5 * 1024 * 1024
    if len(data_b64) > 4 * ((limit + 2) // 3):
        raise DoctorPhotoError(413, {"error": f"La foto supera los {limit // (1024 * 1024)} MB."})
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:  # noqa: BLE001
        raise DoctorPhotoError(400, {"error": "la foto no se pudo decodificar"})

    if not _SAFE_ID_RE.match(mid):
        raise DoctorPhotoError(404, {"error": cfg["no_encontrado"]})

    if ent == 'doctors':
        if not raw:
            raise DoctorPhotoError(400, {"error": "falta la foto"})
        if len(raw) > MAX_BYTES:
            raise DoctorPhotoError(413, {"error": "La foto supera los 20 MB. Elegí una más liviana."})
        return _save_portrait(mid, body, raw)

    # Centers: the shared gate DECODES the actual bytes (magic bytes only say
    # what a file claims to be), applies EXIF orientation, bounds pixels and
    # re-encodes — the same robustness the staff photo picker already has via
    # portrait_processing, for the surface that used to store raw bytes
    # (a broken JPEG or an iPhone HEIC never survived this step intact).
    from app.core import photo_guard
    try:
        raw, ext, _mime = photo_guard.normalize(raw, max_dim=1600)
    except photo_guard.PhotoRejected as exc:
        raise DoctorPhotoError(exc.status, exc.error)
    carpeta = _dir(ent)
    dest = os.path.join(carpeta, f"{mid}.{ext}")
    try:
        os.makedirs(carpeta, exist_ok=True)
        _atomic_file(dest, raw)
    except OSError:
        raise DoctorPhotoError(507, {"ok": False, "error": "No se pudo guardar la foto (disco lleno)."})
    # Retire legacy extensions only after the new JPEG is committed. Failed
    # writes leave the previous photo intact; JPEG is first in _existing().
    for old_ext in _EXTS:
        if old_ext != ext:
            try:
                os.remove(os.path.join(carpeta, f"{mid}.{old_ext}"))
            except OSError:
                pass
    _invalidar_sprite()

    return {"ok": True, "foto_url": photo_url(mid, ent)}


def remove_payload(mid, ent="doctors"):
    """Deletes the upload for `mid`/`ent`, returns `{"ok": True}`. Raises
    `DoctorPhotoError(404, ...)` when the entity doesn't exist; may raise
    `db.DbUnavailable` from the initial catalog lookup (caller's guard)."""
    cfg = _ENTITIES[ent]
    if _doc(mid, ent) is None:
        raise DoctorPhotoError(404, {"error": cfg["no_encontrado"]})
    if ent == 'doctors':
        try:
            with _portrait_lock(mid) as private:
                for ext in _EXTS:
                    path = os.path.join(PHOTOS_DIR, mid + '.' + ext)
                    if os.path.lexists(path):
                        os.unlink(path)
                for name in os.listdir(private):
                    path = os.path.join(private, name)
                    if name.startswith('portrait-') and os.path.isdir(path):
                        shutil.rmtree(path)
                    elif name in ('original', 'metadata.json'):
                        os.unlink(path)
        except OSError as exc:
            raise DoctorPhotoError(507, {"error": "No se pudo quitar la foto."}) from exc
        _invalidar_sprite()
        return {"ok": True}
    for ext in _EXTS:
        p = os.path.join(_dir(ent), f"{mid}.{ext}")
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    _invalidar_sprite()
    return {"ok": True}


def erase(mid, ent="doctors", unless=None):
    """Removes every file kept for `mid` when its catalog record is deleted
    for good (2026-11-06): the public photo and, for a doctor, the whole
    private `.originals/<mid>/` (originals, bundles, metadata, lock). The id is
    derived from the name, so a photo left behind would be shown on the next
    record with the same name, and a private original would outlive its
    person (Ley 25.326). Returns how many public files and private folders
    went; nothing to remove is not an error, a removal that fails raises
    OSError. An id the uploads would refuse removes nothing: no path is ever
    built outside the entity's folder. `unless()`, when given, is asked right
    before anything goes (for a doctor, under the portrait lock): true keeps
    every file and returns 0 — the orphan sweep re-reads the catalog there."""
    if not isinstance(mid, str) or not _SAFE_ID_RE.fullmatch(mid):
        return 0
    carpeta = _dir(ent)
    if not os.path.isdir(carpeta):
        return 0
    removed = 0
    if ent != "doctors":
        if unless is not None and unless():
            return 0
        for ext in _EXTS:
            try:
                os.remove(os.path.join(carpeta, f"{mid}.{ext}"))
                removed += 1
            except FileNotFoundError:
                pass
        _invalidar_sprite()
        return removed
    originals = os.path.realpath(os.path.join(carpeta, ".originals"))
    had_private = os.path.isdir(os.path.join(carpeta, ".originals", mid))
    # The same lock `_save_portrait` publishes under: an upload already
    # running finishes first, and its bundle goes with the rest.
    with _portrait_lock(mid) as private:
        if os.path.dirname(os.path.realpath(private)) != originals:
            raise OSError(f"portrait folder outside {originals}")
        if unless is not None and unless():
            return 0
        for ext in _EXTS:
            path = os.path.join(carpeta, f"{mid}.{ext}")
            if os.path.lexists(path):
                os.unlink(path)
                removed += 1
        # The public pointer is gone first, so a failure here never leaves it
        # pointing into a half-removed bundle.
        shutil.rmtree(private)
        removed += int(had_private)
    _invalidar_sprite()
    return removed
