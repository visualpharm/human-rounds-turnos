"""avatar_thumb.py — the portrait on a doctor's OWN page, at the size it is drawn.

Why this exists (measured on pinamar.humanrounds.org, 2026-09-17): the
/doctor/<id> hero painted its 112 px portrait (88 px on mobile) by cropping
it out of the pack sprite `medico-photo-sprite.webp` — 1536x3072 px, 525 KB,
the whole roster — so every doctor page downloaded and decoded 79% of its
weight to show one face. Mobile Lighthouse put LCP at 4.1-4.3 s on the runs
where first paint landed after that download.

This module cuts ONE thumbnail per doctor (224 px = 2x the desktop avatar)
from the same source `_photo_style` would use — admin upload first, then the
pack's full portrait `medico-photos/<id>.webp`, then the pack sprite tile —
and serves it as `/doctor-thumb/<id>.<hash>.webp`, ~10 KB.

Same contract as avatar_sprite.py (read its docs/solutions note before
touching this): the hash is of the THUMBNAIL BYTES, never of the source
mtimes, so an identical re-copy keeps the same URL; a stale hash is answered
with the current bytes instead of a 404; and any failure returns None so the
page falls back to the style it used before.

Owner of:
  GET /doctor-thumb/<id>.<hash>.webp   (public, no session)
"""

import hashlib
import io
import os
import re
import threading
import urllib.parse

from app.core import sitedata
from app.patient_web import doctor_photos

SIZE = 224
QUALITY = 82

_NAME_RE = re.compile(r"^([A-Za-z0-9_-]{1,120})\.([0-9a-f]{16})$")

_lock = threading.Lock()
_cache = {}  # mid -> (fingerprint, hash, bytes)


def _source(mid):
    """(fingerprint, loader) for this doctor's best portrait, or None."""
    subido = doctor_photos._existing(mid)
    if subido:
        path = subido[0]
    else:
        path = sitedata.path("medico-photos", f"{mid}.webp")
    if os.path.isfile(path):
        st = os.stat(path)
        return (f"f|{path}|{st.st_mtime_ns}|{st.st_size}",
                lambda: _open(path))
    from app.patient_web import avatar_sprite
    sprite_path, man = avatar_sprite._pack_sprite()
    e = (man.get("entries") or {}).get(mid)
    if not (e and sprite_path):
        return None
    tile = int(man.get("tile") or 128)
    x, y = int(e["x"]), int(e["y"])
    st = os.stat(sprite_path)
    return (f"t|{sprite_path}|{st.st_mtime_ns}|{x}|{y}|{tile}",
            lambda: _open(sprite_path).crop((x, y, x + tile, y + tile)))


def _open(path):
    from PIL import Image
    return Image.open(path).convert("RGB")


def _get(mid):
    """(hash, bytes) of the current thumbnail, or None. Never raises."""
    if not mid or not _NAME_RE.match(f"{mid}.{'0' * 16}"):
        return None
    try:
        src = _source(mid)
        if not src:
            return None
        huella, cargar = src
        c = _cache.get(mid)
        if c and c[0] == huella:
            return c[1], c[2]
        from PIL import Image, ImageOps
        # Centred square crop = what `background-size:cover` +
        # `background-position:center` did: same framing, lighter delivery.
        cara = ImageOps.fit(cargar(), (SIZE, SIZE), Image.LANCZOS,
                            centering=(0.5, 0.5))
        buf = io.BytesIO()
        cara.save(buf, "WEBP", quality=QUALITY, method=6)
        datos = buf.getvalue()
        h = hashlib.sha1(datos).hexdigest()[:16]
        with _lock:
            _cache[mid] = (huella, h, datos)
        return h, datos
    except Exception:
        return None


def url(mid):
    """Public URL of this doctor's page portrait, or None (caller falls back)."""
    r = _get(mid)
    if not r:
        return None
    return f"/doctor-thumb/{urllib.parse.quote(mid)}.{r[0]}.webp"


def thumb_bytes(name):
    """(bytes, current) for `<id>.<hash>`, or None. `current` is False when the
    hash is stale — the caller serves the fresh bytes with a short cache
    instead of a 404, because an already-open page still points at it."""
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    r = _get(m.group(1))
    if not r:
        return None
    return r[1], r[0] == m.group(2)
