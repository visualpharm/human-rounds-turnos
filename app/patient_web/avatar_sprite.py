"""avatar_sprite.py — ONE image for every small round doctor portrait.

Why this exists (measured on demo.humanrounds.org, 2026-08-05): the home
page's directory cards show up to 6 faces per cluster — 48 of them on the
demo catalog — and every one was its own `/doctor-photo/<id>` request for a
full-resolution portrait. 48 requests and 1.4 MB of JPEG, to paint circles
44 px wide, from an origin ~0.8 s away from the patient. The page painted
the hero and the search bar and then sat there while the faces trickled in.

This module packs every portrait the catalog has into a SINGLE WebP atlas of
96 px tiles (2x the largest avatar the site draws, which is 44 px) and hands
each call site a `background-image` + `background-position` pair, so the
browser crops with CSS. The same 71 Pinamar faces: 84 KB in one request.

The URL is content-addressed — `/avatar-sprite/<hash>.webp`, where the hash
is of the ATLAS BYTES themselves — so the response is `immutable`-cacheable
AND invalidates itself the instant any photo actually changes. Nothing to
bump by hand, unlike the `?v=` on the site's JS and CSS.

Naming it after the SOURCES instead would look equivalent and isn't: the
demo re-copies its 50 portraits every night, identical bytes with fresh
mtimes, and that gave a brand new URL to an unchanged image every single
night. Hashing the output means an identical re-copy resolves to the
identical URL. Old atlases are also kept for hours rather than deleted the
moment a new one is built (RETENCION_S), and the current one is held in
memory, because pages already open in somebody's browser still point at the
URL they were served — the day this was written the eager version of both
took every face off the live demo.

Priority per doctor is the SAME one `site_doctors._photo_style` applies, and
has to stay that way: admin upload (doctor_photos.py, `data/medico-fotos/`)
first, offline pack sprite (`medico-photo-sprite.webp`) second, nothing
third — a doctor with no portrait is not in the atlas at all and keeps
falling through to the initials avatar / the card's "+N" count.

The walk-in card's round center photos ride in the same atlas (`extra`,
keyed `center:<slug>`), each cut around a focus the data pack names, since a
whole building at 44 px is unreadable (the product owner, 2026-10-27).

Degradation is the rule, never an exception: no Pillow, an unreadable
source, a full disk, anything at all -> `styles()` returns {} and
patient_web/site_*.py goes back to the per-photo URLs it used before. A slow page is
a bug. A page with no faces on it is a worse one.

Owner of:
  GET /avatar-sprite/<hash>.webp   the atlas (public, no session)
"""

import hashlib
import io
import json
import math
import os
import re
import threading
import time

from app.patient_web import doctor_photos
from app.core import sitedata
from app.core import paths

ROOT = paths.PROJECT_ROOT
# `data/` is the persistent runtime volume; the atlas is a derived artifact
# and is rebuilt from the sources whenever the hash changes, so losing it
# costs one rebuild and nothing else.
SPRITE_DIR = os.path.join(ROOT, "data", "avatar-sprite")

# 96 px = 2x the largest avatar the site draws (44 px in the directory cards
# and in listing.js's widest breakpoint), so it stays crisp on a retina
# screen without paying for portrait resolution nobody sees.
TILE = 96
QUALITY = 80
# Above this many portraits the atlas stops being a win for a page that only
# shows a handful of them, and we go back to per-photo URLs. Pinamar's real
# catalog is 70; the cap is here so a 500-doctor installation degrades to the
# old behaviour instead of shipping a multi-megabyte sheet.
MAX_TILES = 240

_HASH_RE = re.compile(r"^/avatar-sprite/([0-9a-f]{16})\.webp$")

_lock = threading.Lock()
_cache = None  # (clave, {mid: css}) — recomputed when the fingerprint moves
# Fingerprinting the sources means one os.stat per doctor, and styles() is
# called once per avatar drawn — 50 faces on the home page would be 2500
# stats for a set of files that changes when somebody uploads a photo, which
# is roughly never. So the fingerprint is trusted for TTL seconds, and
# doctor_photos calls invalidate() the moment it writes or deletes one, which
# makes the staleness window zero in the only case that matters.
#
# 5 s was too short to ever help a real visitor: nobody loads the home twice
# within 5 s, so EVERY arrival paid the stat sweep — measured on the demo at
# ~53 ms of the ~60 ms that /api/directory spent server-side, six times the
# cost of the answer itself. The window only ever guarded photo changes made
# behind doctor_photos.py's back (which calls invalidate()), and a pack
# redeploy restarts the process anyway, so it can be minutes instead of
# seconds (2026-08-05).
TTL = 300.0
_deadline = 0.0
_epoch = 0


def invalidate():
    """Forces the next styles() to re-read the sources. Called by
    doctor_photos.py whenever a portrait is uploaded or removed."""
    global _epoch, _deadline
    _epoch += 1
    _deadline = 0.0


# ---------------------------------------------------------------- sources

_man_cache = None  # (path, mtime_ns, manifest) — the offline manifest barely
                   # changes, but styles() runs on every request that draws a
                   # face, so it is not re-parsed for nothing.


def _pack_sprite():
    """(image path, manifest) of the offline pack sprite, or (None, {})."""
    global _man_cache
    try:
        mp = sitedata.path("medico-photo-sprite.json")
        st = os.stat(mp)
        c = _man_cache
        if c and c[0] == mp and c[1] == st.st_mtime_ns:
            man = c[2]
        else:
            with open(mp, "r", encoding="utf-8") as f:
                man = json.load(f) or {}
            _man_cache = (mp, st.st_mtime_ns, man)
    except Exception:
        return None, {}
    if not man.get("entries"):
        return None, {}
    img = man.get("image") or "/webdata/medico-photo-sprite.webp"
    p = sitedata.path(os.path.basename(img))
    return (p if os.path.isfile(p) else None), man


def _sources(ids):
    """[(mid, kind, ...)] for the ids that have a portrait, in the given
    order. `kind` is 'file' (admin upload) or 'tile' (offline pack sprite),
    matching site_doctors._photo_style's priority exactly."""
    sprite_path, man = _pack_sprite()
    entries = man.get("entries") or {}
    tile = int(man.get("tile") or 128)
    out = []
    for mid in ids:
        if not mid:
            continue
        subido = doctor_photos._existing(mid)
        if subido:
            path, _mt = subido
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append((mid, "file", path, st.st_mtime_ns, st.st_size))
            continue
        e = entries.get(mid)
        if e and sprite_path:
            out.append((mid, "tile", sprite_path, int(e["x"]), int(e["y"]), tile))
    return out


def _crop_sources(extra):
    """[(key, 'crop', path, mtime_ns, size, fx, fy, zoom)] for the PLACES that
    share the atlas — the centers' photos on the walk-in card. A building is
    not a face: the whole façade squeezed into 44 px reads as a smudge, so
    each one is cut to a square around its focus (fx, fy as fractions of the
    photo, zoom = how many times narrower than the photo's short side) before
    it becomes a tile. The focus travels in the fingerprint, so moving it
    rebuilds the atlas like a new photo would."""
    out = []
    for key, path, foco in extra:
        if not key or not path:
            continue
        try:
            st = os.stat(path)
            fx, fy, zoom = (float(v) for v in foco)
        except (OSError, TypeError, ValueError):
            continue
        out.append((key, "crop", path, st.st_mtime_ns, st.st_size,
                    min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0),
                    min(max(zoom, 1.0), 6.0)))
    return out


def _focus_box(w, h, fx, fy, zoom):
    """The square (left, top, right, bottom) of side min(w, h) / zoom centred
    on (fx·w, fy·h), slid back inside the photo when the focus sits near an
    edge — the tile is never padded with background."""
    lado = max(1, int(round(min(w, h) / zoom)))
    x = int(round(fx * w - lado / 2))
    y = int(round(fy * h - lado / 2))
    x = min(max(x, 0), w - lado)
    y = min(max(y, 0), h - lado)
    return (x, y, x + lado, y + lado)


def _huella(fuentes):
    """Cheap CHANGE DETECTOR — mtimes and sizes, no reading of pixels. Only
    decides whether to rebuild; it is NOT the URL. See _clave."""
    h = hashlib.sha1()
    h.update(f"v2|{TILE}|{QUALITY}|".encode())
    for f in fuentes:
        h.update(("|".join(str(x) for x in f) + "\n").encode("utf-8"))
    return h.hexdigest()[:16]


def _clave(datos):
    """The URL's name: the hash of the ATLAS ITSELF, not of the mtimes that
    produced it. This distinction is load-bearing. The demo re-copies its 50
    portraits every night (demo_refresh.py): identical bytes, brand new
    mtimes. Naming the file after the mtimes gave every one of those nights a
    new URL for a byte-identical image — and since the old atlas was then
    deleted, any page a patient already had open pointed at a 404 and lost
    every face on it (seen live on the demo, 2026-08-05). Hashing the output
    means an identical re-copy resolves to the identical URL and nothing
    moves at all."""
    return hashlib.sha1(datos).hexdigest()[:16]


# ---------------------------------------------------------------- build

def _build(fuentes):
    """Encodes the atlas for `fuentes` IN MEMORY. Returns
    (bytes, cols, rows, fallidos) — `fallidos` being the ids whose source
    could not be read — or None if nothing could be built at all. It never
    raises: the caller falls back. Nothing is written here, because the file's
    name is the hash of these bytes and isn't known until they exist."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    n = len(fuentes)
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = max(1, math.ceil(n / cols))
    fallidos = set()
    try:
        hoja = Image.new("RGB", (cols * TILE, rows * TILE), "#efece6")
        abiertas = {}
        for i, f in enumerate(fuentes):
            mid, kind = f[0], f[1]
            # One unreadable portrait costs THAT doctor his face (he falls
            # back to his own file, then to initials) and nobody else theirs.
            # It must never cost the whole page its avatars. The slot is left
            # empty rather than closed up, so every other doctor keeps the
            # position already computed for him.
            try:
                if kind == "crop":
                    src = ImageOps.exif_transpose(Image.open(f[2])).convert("RGB")
                    src = src.crop(_focus_box(src.width, src.height, f[5], f[6], f[7]))
                elif kind == "file":
                    src = Image.open(f[2]).convert("RGB")
                else:
                    path, x, y, tile = f[2], f[3], f[4], f[5]
                    hoja_src = abiertas.get(path)
                    if hoja_src is None:
                        hoja_src = Image.open(path).convert("RGB")
                        abiertas[path] = hoja_src
                    src = hoja_src.crop((x, y, x + tile, y + tile))
                # centering=(0.5, 0.5) is not a preference, it's the CONTRACT:
                # what these avatars did before was `background-size:cover` +
                # `background-position:center`, which is exactly a centred
                # square crop. This changes how the portrait is DELIVERED, not
                # how it is framed — nobody should see the difference.
                cara = ImageOps.fit(src, (TILE, TILE), Image.LANCZOS,
                                    centering=(0.5, 0.5))
            except Exception:
                fallidos.add(mid)
                continue
            hoja.paste(cara, ((i % cols) * TILE, (i // cols) * TILE))
        if len(fallidos) == n:
            return None
        buf = io.BytesIO()
        hoja.save(buf, "WEBP", quality=QUALITY, method=6)
    except Exception:
        return None
    return buf.getvalue(), cols, rows, fallidos


def _persistir(clave, datos):
    """Drops the atlas next to its siblings so it survives a restart. Written
    under a temp name and renamed, so a request arriving mid-write can only
    ever see the whole file or no file."""
    try:
        os.makedirs(SPRITE_DIR, exist_ok=True)
        final = os.path.join(SPRITE_DIR, f"{clave}.webp")
        if os.path.isfile(final):
            return
        tmp = final + f".{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(datos)
        os.replace(tmp, final)
    except OSError:
        pass


def _sidecar(clave):
    return os.path.join(SPRITE_DIR, f"{clave}.empty.json")


def _escribir_fallidos(clave, fallidos):
    """Records the slots that came out empty, next to the atlas they belong
    to, so a process that finds the .webp already built knows about them."""
    try:
        with open(_sidecar(clave), "w", encoding="utf-8") as f:
            json.dump(sorted(fallidos), f)
    except OSError:
        pass


def _leer_fallidos(clave):
    try:
        with open(_sidecar(clave), "r", encoding="utf-8") as f:
            return set(json.load(f) or [])
    except (OSError, ValueError):
        return set()


# An atlas is kept well past the moment it stops being current. Deleting the
# previous one the instant a new one is built is what broke the demo on
# 2026-08-05: every page a patient already had open still referenced the old
# URL, and lost every face on it. A stale atlas costs ~60 KB of disk; a 404
# costs the page its portraits. Two other things also hand out old URLs for a
# while — the browser cache, and a second container sharing the volume during
# a deploy — and this window covers both.
RETENCION_S = 6 * 3600


def _purgar():
    """Drops atlases nobody has asked for in a long time."""
    try:
        corte = time.time() - RETENCION_S
        for f in os.listdir(SPRITE_DIR):
            p = os.path.join(SPRITE_DIR, f)
            try:
                if os.path.getmtime(p) < corte:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------- public

def styles(ids, extra=()):
    """{mid: css} — the `background-*` triplet that crops this doctor's face
    out of the shared atlas. Missing from the dict = no portrait, or the
    atlas could not be built; either way the caller falls back.

    `extra` = [(key, image path, (fx, fy, zoom))] — other small round
    pictures that ride in the same atlas, cut around a focus instead of
    centred (the walk-in card's center photos, keyed `center:<slug>`)."""
    global _cache, _deadline
    ids = [m for m in ids if m]
    extra = [e for e in (extra or ()) if e and e[0]]
    if (not ids and not extra) or len(ids) + len(extra) > MAX_TILES:
        return {}

    cached = _cache
    epoca = _epoch
    if cached and cached[3] == epoca and time.monotonic() < _deadline:
        return cached[1]

    try:
        fuentes = _sources(ids) + _crop_sources(extra)
    except Exception:
        return {}
    if not fuentes:
        return {}
    huella = _huella(fuentes)

    if cached and cached[0] == huella:
        _deadline = time.monotonic() + TTL
        _cache = (huella, cached[1], cached[2], epoca)
        return cached[1]

    with _lock:
        cached = _cache
        if cached and cached[0] == huella:
            _deadline = time.monotonic() + TTL
            _cache = (huella, cached[1], cached[2], epoca)
            return cached[1]
        built = _build(fuentes)
        if not built:
            _cache = (huella, {}, None, epoca)
            _deadline = time.monotonic() + TTL
            return {}
        datos, cols, rows, fallidos = built
        clave = _clave(datos)
        _persistir(clave, datos)
        _escribir_fallidos(clave, fallidos)
        _purgar()
        url = f"/avatar-sprite/{clave}.webp"
        out = {}
        for i, f in enumerate(fuentes):
            if f[0] in fallidos:
                continue
            cx, cy = i % cols, i // cols
            px = cx / (cols - 1) * 100 if cols > 1 else 0
            py = cy / (rows - 1) * 100 if rows > 1 else 0
            out[f[0]] = (f"background-image:url('{url}');"
                         f"background-size:{cols * 100}% {rows * 100}%;"
                         f"background-position:{px:.4f}% {py:.4f}%")
        # The bytes stay in memory (tens of KB) so the route can answer even
        # if the file underneath disappears — a purge from another container
        # sharing the volume, a disk sweep, a wiped volume. The image the page
        # was just told to fetch must always be fetchable.
        _cache = (huella, out, (clave, datos), epoca)
        _deadline = time.monotonic() + TTL
        return out


# ---------------------------------------------------------------- route

class AtlasNotFound(Exception):
    """Raised by `atlas_bytes()` when `clave` is a well-formed hash but no
    atlas exists under it (evicted by `_purgar()`, or never built) — the
    "handled" 404 (`{"error": "sprite no encontrado"}`)."""


def atlas_bytes(clave):
    """The atlas bytes for a hash `_HASH_RE` already
    validated — memory first (the atlas pages being served right now point
    at, answering even if the file underneath is gone), then disk. Raises
    `AtlasNotFound` if neither has it."""
    cached = _cache
    if cached and cached[2] and cached[2][0] == clave:
        return cached[2][1]
    try:
        with open(os.path.join(SPRITE_DIR, f"{clave}.webp"), "rb") as f:
            return f.read()
    except OSError:
        raise AtlasNotFound(clave)
