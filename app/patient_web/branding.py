"""branding.py — the municipality's color and logo, editable without touching code.

Hospital meeting 2026-07-24: "un permiso tipo custom que permita editar
colores/logo sin necesidad de tocar el backend ni redeployar" — next year the
municipality's color changes and nobody wants to depend on a deploy.

Deliberate scope: **one** color and **one** logo.

- The color is the ACCENT (`--accent`). The three tokens that go with it
  —`--accent-hover`, `--accent-soft`, `--accent-bd`— are DERIVED
  (`_derived_tokens`), not asked for: they're shades and tints of the same
  color, and asking for four colors guarantees someone picks four that don't
  match. The rest of the identity (typography, grays, status greens) still
  comes from the theme chosen in Settings — this paints over it, not replace
  it.
- The logo is saved at `data/marca/logo.<ext>` and served under the SAME url
  as always, `/logo.png`, which is already written into every page's CSS
  (nav.js and the live patient/admin shells). So changing the logo doesn't
  touch a single line of markup. With no logo uploaded, the native
  `/logo.png` route falls through to the shipped static file.

Where the color gets applied: `style()` returns a `<style>` with the tokens
and `theme.inject` drops it into the `<head>` of every page served
(patient_web/site_*.py builds its own shell, so it inserts it itself). A `<style>` in
the head wins over human-rounds.css without depending on Cloudflare revalidating
the CSS — which caches 4h and ignores the origin's no-cache
(docs/solutions/deploy/cloudflare-js-cache.md). That's why the color never
travels in a .css file.

Storage: the `catalogo` table (tipo="config", id="marca"), same pattern and
same 3s cache as theme.py. DB down or nothing saved = no override, the site
keeps the theme's colors. Never raises.
"""

import logging
import os
import re
import time

from app.core.status_error import BodyStatusError
from app.core import paths

ROOT = paths.PROJECT_ROOT
BRANDING_DIR = os.path.join(ROOT, "data", "marca")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                "svg": "image/svg+xml"}

_cache = {"v": None, "t": 0.0}
_TTL = 3.0


# ---- color -------------------------------------------------------------------

def _rgb(hex_):
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(r, g, b):
    return "#%02x%02x%02x" % (max(0, min(255, int(round(r)))),
                              max(0, min(255, int(round(g)))),
                              max(0, min(255, int(round(b)))))


def _mix(hex_, toward, amount):
    """`hex_` moved `amount` (0..1) toward the `toward` color."""
    r1, g1, b1 = _rgb(hex_)
    r2, g2, b2 = toward
    return _hex(r1 + (r2 - r1) * amount, g1 + (g2 - g1) * amount, b1 + (b2 - b1) * amount)


def _derived_tokens(accent):
    """The three tokens that go with the accent, computed from it: hover =
    22% darker, soft = a very light tint for backgrounds, bd = an
    intermediate tint for borders. Same proportions that separate the
    hand-written values of the 'pinamar' and 'humanrounds' themes in human-rounds.css."""
    return {
        "accent": accent,
        "accent_hover": _mix(accent, (0, 0, 0), 0.22),
        "accent_soft": _mix(accent, (255, 255, 255), 0.90),
        "accent_bd": _mix(accent, (255, 255, 255), 0.72),
    }


def normalize_color(valor):
    """'#0E8B50' / '0e8b50' -> '#0e8b50'. None if it's not a 6-digit hex."""
    v = str(valor or "").strip().lower()
    if v and not v.startswith("#"):
        v = "#" + v
    return v if _HEX_RE.match(v) else None


# ---- saved state ---------------------------------------------------------

def current():
    """`{"color": "#rrggbb"|None, "logo": bool}` — what's configured.
    Cached ~3s (read on every page render, same as the theme)."""
    now = time.time()
    if _cache["v"] is not None and now - _cache["t"] < _TTL:
        return _cache["v"]
    val = {"color": None, "logo": _logo_path() is not None}
    try:
        from app.core import db_catalog
        row = db_catalog.catalog_get("config", "marca")
        if row:
            val["color"] = normalize_color((row.get("doc") or {}).get("color"))
    except Exception:  # noqa: BLE001 — DB down = no override, never a 500
        pass
    _cache["v"] = val
    _cache["t"] = now
    return val


def invalidate():
    _cache["v"] = None
    _cache["t"] = 0.0


def save_color(color):
    """Persists the accent (or clears it with None/"" to fall back to the theme's)."""
    c = normalize_color(color)
    if color not in (None, "") and c is None:
        raise ValueError("color inválido: %r" % (color,))
    from app.core import db_catalog
    db_catalog.catalog_upsert("config", "marca", {"color": c} if c else {})
    invalidate()
    return c


# The three classes the site draws the logo in on the nav bar: nav.js
# (`.stznav-mark`) and the retired WebTurno/analytics shells (`.stz-mark`,
# `.brand-mark`). All point at /logo.png by default, but the
# "pinamar" theme overrides `.stznav-mark` with the municipality's emblem
# (human-rounds.css) — that's why the custom logo also needs to win here.
_BRAND_MARK_SELECTORS = ".stznav-mark,.stz-mark,.brand-mark,.adm5-brand::before"


def style():
    """The `<style>` that paints the municipality's color and logo, or "" if
    none is configured. Goes in the <head>: wins over human-rounds.css without
    depending on the CDN revalidating the CSS."""
    est = current()
    color = est.get("color")
    partes = []
    if est.get("logo"):
        partes.append("%s{background-image:url(/logo.png)!important;}" % _BRAND_MARK_SELECTORS)
    if not color:
        return ("<style id=\"marca\">%s</style>" % "".join(partes)) if partes else ""
    d = _derived_tokens(color)
    # `!important` on purpose: human-rounds.css writes these tokens under
    # `:root[data-theme="pinamar"]` and this <style> gets inserted at the
    # start of the head (before the link), so at equal specificity the CSS
    # would win. Raising specificity by repeating `:root` would be a race the
    # next theme selector wins right back; the municipality's color is a
    # deliberate, final override, and it's declared as one.
    partes.append(":root{--accent:%(accent)s!important;"
                  "--accent-hover:%(accent_hover)s!important;"
                  "--accent-soft:%(accent_soft)s!important;"
                  "--accent-bd:%(accent_bd)s!important;}" % d)
    return "<style id=\"marca\">%s</style>" % "".join(partes)


# ---- logo --------------------------------------------------------------------

# An SVG is XML: it can carry <script>, an onload= handler, or a javascript:
# inside an href. Served from OUR OWN domain, opening /logo.png in a tab
# would run it as first-party code — and the 'marca' role exists precisely
# to be low-privilege (it sees no one's data), so that would be an escalation
# from 'marca' to 'admin'. Two independent defenses: it's rejected on upload
# (here) and, if something still slips through, the logo endpoint delivers
# it with a CSP that forbids it from executing anything
# (`CUSTOM_LOGO_HEADERS`).
_SVG_DANGEROUS = (b"<script", b"javascript:", b"<foreignobject", b"<use",
                  b"<iframe", b"<embed", b"<object", b"<animate", b"<set ")
_SVG_ON_RE = re.compile(rb"\son[a-z]+\s*=")


def _svg_safe(data):
    """False if the SVG carries anything that could run code. The whole file
    is checked lowercased: checking just the header isn't enough."""
    bajo = data.lower()
    if any(t in bajo for t in _SVG_DANGEROUS):
        return False
    return not _SVG_ON_RE.search(bajo)


def _sniff(data):
    """(ext, mime) by magic bytes — never the mime the client claims. Same
    criteria as core.photo_guard.sniff, plus SVG (an institutional logo is
    usually vector). An SVG with a script is rejected as if it weren't an
    image: no attempt is made to sanitize it (hand-trimming XML is an
    endless source of bypasses)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    cabeza = data[:512].lstrip()
    if cabeza[:5] == b"<?xml" or cabeza[:4] == b"<svg":
        return ("svg", "image/svg+xml") if _svg_safe(data) else None
    return None


def _logo_path():
    """Path of the uploaded logo, or None. The first one that exists (there
    can only be one: uploading replaces it)."""
    for ext in ("svg", "png", "webp", "jpg"):
        p = os.path.join(BRANDING_DIR, "logo." + ext)
        if os.path.exists(p):
            return p
    return None


def save_logo(data):
    """Replaces the logo. Returns the extension saved. ValueError if the
    file isn't an image we know how to serve."""
    sniff = _sniff(data)
    if not sniff:
        raise ValueError("formato no soportado")
    ext, _mime = sniff
    os.makedirs(BRANDING_DIR, exist_ok=True)
    for viejo in ("svg", "png", "webp", "jpg"):
        p = os.path.join(BRANDING_DIR, "logo." + viejo)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    with open(os.path.join(BRANDING_DIR, "logo." + ext), "wb") as f:
        f.write(data)
    invalidate()
    return ext


def remove_logo():
    """Falls back to the logo that ships with the site."""
    p = _logo_path()
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
    invalidate()


# ---- logo response payload (shared by legacy + ASGI transports) -------------

# The headers a CUSTOM-logo response carries. Second defense against a scripted
# SVG (the first is rejection on upload, `_svg_safe`): `sandbox` puts it in an
# opaque origin and `script-src 'none'` lets it run nothing — even if one slips
# through, opening /logo.png runs no code on the hospital's domain (as a CSS
# background-image an SVG never executes scripts anyway). `nosniff` because the
# URL is fixed (/logo.png) regardless of the real format. `no-cache,
# must-revalidate` because the URL can't carry ?v=<mtime>, so changing the logo
# has to show up on the next load, not hours later.
#
# ONE constant, read by the ASGI logo endpoint (`branding_router.py`) — so a
# header added here lands in the one place that serves the logo, instead of
# drifting the way inline copies would.
CUSTOM_LOGO_HEADERS = (
    ("Content-Security-Policy",
     "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-cache, must-revalidate"),
)

# Cache header the legacy static fallback puts on an unversioned image
# (`server.py` Handler.end_headers: an image without `?v=` gets one day). The
# ASGI default-logo endpoint reproduces it byte-for-byte — otherwise the demo
# (`TURNOS_ASGI=1`) would serve a different caching contract than prod.
STATIC_IMAGE_CACHE = "public, max-age=86400"


def custom_logo_payload():
    """`(bytes, mime)` for the admin-uploaded logo, or None when no custom
    logo is set. The response headers are `CUSTOM_LOGO_HEADERS` (CSP/nosniff/
    no-cache) — the ASGI logo endpoint pairs this payload with that
    constant, as the retired legacy `_serve_logo` did."""
    p = _logo_path()
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            data = f.read()
    except OSError:
        return None
    ext = p.rsplit(".", 1)[1]
    return data, _MIME_BY_EXT.get(ext, "application/octet-stream")


def default_logo_path():
    """The logo that ships with the site — the static file /logo.png falls
    back to when no admin upload is present. Resolved against the repo ROOT
    (the legacy server's webroot — `Handler` is constructed with
    `directory=ROOT`), exactly as the legacy static fallback (`super().do_GET()`
    over ROOT) does. None when no shipped default exists at that root."""
    p = paths.root_web_asset("logo.png")
    return p if os.path.isfile(p) else None


# ---- routes -------------------------------------------------------------------

_MAX_LOGO = 2 * 1024 * 1024  # 2 MB: it's a logo, not a photo


class BrandingError(BodyStatusError):
    """Carries the exact `(status, body)` the API endpoints need — the two
    values `_save`/`_upload_logo` raise. `branding_router.py`'s native
    endpoints catch this and build their response bytes from it (family
    W2c of docs/roadmap/native-migration-campaign-20260826.md)."""


# ---- pure domain functions (the native router's entry points) ------

def state_payload():
    """The GET /api/admin/branding body — pure, no handler involved."""
    from app.core import theme
    est = current()
    return {
        "color": est.get("color"),
        "logo": est.get("logo"),
        # The accent actually in use right now: its own if set, else the
        # active theme's — so the selector starts on the real color instead
        # of some arbitrary white.
        "color_efectivo": est.get("color") or _ACCENT_BY_THEME.get(theme.current()) or "#0d7d48",
        "derivados": _derived_tokens(est["color"]) if est.get("color") else None,
    }


def apply_color(body):
    """`body`: parsed JSON dict (a request whose body failed to parse is
    passed in as `{}` by the caller — same leniency as the legacy
    `handler._read_json()` failure contract). Raises `BrandingError` on an
    invalid color or a DB failure; otherwise returns the `{"ok": True, ...}`
    payload."""
    from app.core import deps
    # Outside the catch: a JSON string or number raises here, and the caller's api_body.call answers the
    # body family's 400 — it is neither the database nor a save that failed.
    chosen = (body or {}).get("color")
    try:
        color = save_color(chosen)
    except ValueError:
        raise BrandingError(400, {"error": "color inválido"})
    except deps.DB_UNAVAILABLE_ERRORS:
        raise deps.answer_db_unavailable(lambda status: BrandingError(status, {"error": "base de datos no disponible"}))
    except Exception as exc:  # noqa: BLE001 — not the database: its own error, no db_unavailable
        from app.core import i18n, log_pii
        logging.getLogger(__name__).error("the brand colour was not saved: %s", log_pii.exc(exc))
        raise BrandingError(500, {"error": i18n.t_or(
            "admin.srv.error.no_guardado", "no se pudo guardar, probá de nuevo", i18n.active())})
    return {"ok": True, "color": color,
            "derivados": _derived_tokens(color) if color else None}


def apply_logo_upload(body):
    """`body`: parsed JSON dict, same leniency as `apply_color`. Raises
    `BrandingError` on a bad/oversized/unsupported upload."""
    import base64
    raw = str((body or {}).get("data_b64") or "")
    if "," in raw[:64] and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:  # noqa: BLE001
        data = b""
    if not data:
        raise BrandingError(400, {"error": "archivo vacío"})
    if len(data) > _MAX_LOGO:
        raise BrandingError(400, {"error": "el logo no puede pesar más de 2 MB"})
    try:
        save_logo(data)
    except ValueError:
        raise BrandingError(400, {"error": "El archivo tiene que ser una imagen PNG, JPG, WEBP o "
                                          "SVG. Un SVG con código adentro no se acepta: "
                                          "exportalo de nuevo como imagen plana."})
    except OSError:
        raise BrandingError(500, {"error": "no se pudo guardar el logo"})
    return {"ok": True, "logo": True}


def apply_logo_delete():
    remove_logo()
    return {"ok": True, "logo": False}


# Accent per theme (human-rounds.css) — only to show the starting color in the
# selector; the site doesn't read from here.
_ACCENT_BY_THEME = {"pinamar": "#0d7d48", "humanrounds": "#874f2e"}
