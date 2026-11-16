"""theme.py — the site's active visual theme (green "pinamar" vs Human Rounds paper+vermillion).

A SINGLE global setting (not per-user): the operator changes it from admin
(Settings) and it applies to ALL pages. All visual identity —palette +
typography— comes from CSS tokens (human-rounds.css), so the only dynamic data
per page is the `data-theme` attribute of <html>: the server injects it
server-side (no flash) by replacing the `{{tema}}` placeholder.

Storage: the `catalogo` table (tipo="config", id="tema", doc={"tema": id}),
reusing the db_catalog.catalog_get/upsert helpers — no new table. If the DB is down
or nothing's saved, it falls back to the default (env TURNOS_THEME_DEFAULT,
legacy TURNOS_TEMA_DEFAULT, or "humanrounds").
Never raises: a down DB = default theme, the site never goes down over this.
"""

import os

from app.core import catalog_setting
from app.core import paths

# id -> metadata for the admin selector. The id is the data-theme value and
# the key of the token block in human-rounds.css.
THEMES = {
    "pinamar": {
        "id": "pinamar",
        "label": "Pinamar",
        "desc": "Verde + Fira Sans — el rediseño de Claude Design.",
    },
    "humanrounds": {
        "id": "humanrounds",
        "label": "Human Rounds",
        "desc": "Papel + bermellón + Manrope — la marca pública de humanrounds.org.",
    },
}
DEFAULT = "humanrounds"

# id -> nombre de marca visible (título de página, barra de navegación, panel
# admin). Único lugar donde vive esta tabla: platform/nav.mjs, chat-widget.mjs
# y upload-order.mjs tienen su propia copia en JS (no pueden importar Python),
# pero server-side todo pasa por acá — ver patient_web/site_*.py.
_BRAND_NAMES = {
    "pinamar": "Turnos de salud pública en Pinamar",
    "humanrounds": "Human Rounds",
}

# Temas cuyo nombre de marca ya dice de qué trata el sitio: la portada usa la
# marca sola como título de pestaña y og:title, sin repetirle adelante la frase
# genérica «Turnos y servicios públicos en {ciudad}».
_BRAND_IS_PAGE_TITLE = frozenset({"pinamar"})


def brand_name(theme_id=None):
    """Visible brand for the active theme (or the supplied ``theme_id``).

    An unknown id falls back to the neutral engine brand, never to one
    deployment's municipal identity.
    """
    tid = theme_id or current()
    return _BRAND_NAMES.get(tid, _BRAND_NAMES[DEFAULT])


def brand_is_page_title(theme_id=None):
    """True when the active theme's brand already describes the site, so the
    home page title is the brand alone (see ``_BRAND_IS_PAGE_TITLE``)."""
    return (theme_id or current()) in _BRAND_IS_PAGE_TITLE

# Google Fonts families each theme USES. Pages used to request all four
# together (Inter + JetBrains Mono + Ubuntu + Ubuntu Mono) regardless of the
# active theme, so Pinamar downloaded two families no selector ever uses —
# and that <link> is render-blocking: it delays the first paint to fetch
# typefaces that don't get painted. Each theme requests only its own.
_FONT_FAMILIES = {
    "pinamar": ("Fira+Sans:ital,wght@0,400;0,500;0,700;1,400",
                "Ubuntu+Mono"),
    # Las mismas dos familias, con los mismos cortes, que pide humanrounds.org
    # (landing.html). Antes acá había Inter + JetBrains Mono, que no es la
    # tipografía de la marca desde el rediseño del sitio público.
    "humanrounds": ("Manrope:wght@400;500;600;700;800",
                    "IBM+Plex+Mono:wght@400;500"),
}

def _default():
    d = (os.getenv("TURNOS_THEME_DEFAULT") or os.getenv("TURNOS_TEMA_DEFAULT") or DEFAULT).strip().lower()
    return d if d in THEMES else DEFAULT


_setting = catalog_setting.CatalogSetting("tema", "tema", THEMES, lambda: _default(), "tema inválido: %r")
_cache = _setting.cache  # the {"v", "t"} dict the cached read keeps (tests seed and reset it)


def current():
    """Id of the active theme. The value saved in DB wins; if missing (or the
    DB is unavailable), the default. Cached ~3s so page renders don't hit the
    DB every time."""
    return _setting.current()


def save(theme_id):
    """Persists the active theme. `theme_id` has to be a valid id. Invalidates
    the local cache. Returns the id saved."""
    return _setting.save(theme_id)


def _branding_style():
    """The `<style>` with the municipality's color (branding.py), or "". Lazy
    import, failure-proof: if branding fails, the page still comes out fine."""
    try:
        from app.patient_web import branding
        return branding.style()
    except Exception:  # noqa: BLE001
        return ""


# Ubuntu's .woff2 files live in the repo (static/fonts/ — an ENGINE asset,
# not locality data: it must survive any data-pack swap). They're served
# from our own domain and the @font-face rules go INLINE in the <head>, so
# the active theme costs zero connections to a third party: the <link> to
# fonts.googleapis.com was render-blocking (measured: 811 ms) and also forced
# resolving DNS+TLS for TWO origins (googleapis for the CSS, gstatic for the
# fonts) before text could paint — which is exactly the home page's LCP.
# Deliberate side effect on a public-health site: each patient's IP stops
# travelling to Google just for opening a page.
_FONTS_DIR = os.path.join(paths.PROJECT_ROOT,
                          "static", "fonts")
# Per theme: the @font-face file and the `latin` cuts the first viewport
# paints, preloaded. The rest (latin-ext, cyrillic, italic…) stays behind its
# unicode-range and the browser only requests it if the text needs it.
# "humanrounds" (Manrope + IBM Plex Mono, 2026-09-17) followed "pinamar":
# served by Google with display=swap, Manrope arrived after the first paint
# and every line re-flowed — the nav jumped from full menu to compact at
# 1280px and the doctor editor's tabs changed rows at 360px (CLS 0.16).
_SELF_HOSTED = {
    # Fira Sans (2026-10-16, la letra de pinamar.gob.ar); fira.css lleva también
    # Ubuntu Mono para los datos monoespaciados.
    "pinamar": ("fira.css", ("fira-sans-400-latin.woff2", "fira-sans-700-latin.woff2")),
    "humanrounds": ("humanrounds.css", ("manrope-variable-latin.woff2",)),
}
# Cache-buster for the .woff2 files. A font file never changes (the cut and
# weight are in the name), and with `?v=` the server sends them `immutable`
# instead of "revalidate in a day" — bump it only if the font package gets
# regenerated.
_FONTS_V = "1"
_faces_cache = {}


def _faces_inline(css_name="fonts.css"):
    """The self-hosted @font-face rules, cached by mtime (same pattern as the
    rest of the module). If the file isn't there, returns "" and the caller
    falls back to Google's <link> — the page never ends up without a
    typeface."""
    path = os.path.join(_FONTS_DIR, css_name)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return ""
    cached = _faces_cache.get(css_name)
    if cached is None or mt != cached[1]:
        try:
            with open(path, encoding="utf-8") as f:
                css = f.read().strip().replace(".woff2)", f".woff2?v={_FONTS_V})")
            cached = _faces_cache[css_name] = (css, mt)
        except OSError:
            return ""
    return cached[0] or ""


def fonts(theme_id=None):
    """The active theme's typefaces, ready to drop into the <head>.

    Both themes: inline @font-face + preload of the latin cuts — zero
    blocking requests and zero third parties. A theme without self-hosted
    files falls back to Google's <link> with preconnect to both origins."""
    tid = theme_id or current()
    css_name, preload = _SELF_HOSTED.get(tid, (None, ()))
    faces = _faces_inline(css_name) if css_name else ""
    if faces:
        pre = "".join(
            f'<link rel="preload" href="/static/fonts/{f}?v={_FONTS_V}" '
            f'as="font" type="font/woff2" crossorigin>' for f in preload)
        return pre + "<style>" + faces + "</style>"
    fams = _FONT_FAMILIES.get(tid) or _FONT_FAMILIES[DEFAULT]
    href = ("https://fonts.googleapis.com/css2?"
            + "&".join("family=" + f for f in fams) + "&display=swap")
    return ('<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="{href}" rel="stylesheet">')


# Favicon POR TEMA (2026-08-01). Es identidad de marca igual que el logo del
# nav, pero un <link rel="icon"> no lo puede resolver el CSS por [data-theme]:
# tiene que salir ya resuelto del servidor. Por eso vive acá y no en
# human-rounds.css, y por eso las páginas llevan `{{favicon}}` en vez del
# <link> escrito a mano que tenían antes (el mismo verde de Pinamar en las
# dos instalaciones — la demo pública mostraba la pestaña de otra marca).
#
# Las dos instalaciones salen del MISMO build (pinamar.humanrounds.org y
# demo.humanrounds.org), así que esto no se puede resolver eligiendo qué
# archivo se copia en el deploy: las dos tienen los dos archivos y el tema
# activo decide cuál se enlaza.
_FAVICONS = {
    "pinamar": '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">',
    # Los mismos PNG que sirve humanrounds.org (32 para la pestaña, 180 para
    # el ícono de "agregar a la pantalla de inicio" de iOS).
    # La marca es una silueta sobre transparente, así que el color lo tiene que
    # poner la barra del navegador: negra sobre barra clara, blanca sobre barra
    # oscura. El ORDEN importa: el navegador se queda con el ÚLTIMO <link
    # rel="icon"> que aplique, así que la variante oscura va primero y la clara
    # última. Un navegador que ignore `media` toma entonces la negra, que es el
    # default seguro (la barra clara sigue siendo la mayoría).
    "humanrounds": (
        '<link rel="icon" type="image/png" sizes="32x32"'
        ' media="(prefers-color-scheme: dark)"'
        ' href="/static/human-rounds-mark-32-dark.png?v=3">'
        '<link rel="icon" type="image/png" sizes="32x32"'
        ' media="(prefers-color-scheme: light)"'
        ' href="/static/human-rounds-mark-32.png?v=3">'
        '<link rel="apple-touch-icon"'
        ' href="/static/human-rounds-mark-180.png?v=3">'
    ),
}


def favicon(theme_id=None):
    """The active theme's `<link rel="icon">` tags, ready for the <head>.
    Falls back to the default theme's icon if the id is unknown — a page never
    ends up without a favicon."""
    tid = theme_id or current()
    return _FAVICONS.get(tid) or _FAVICONS[DEFAULT]


def inject(text):
    """Replaces `text`'s `{{tema}}`, `{{fuentes}}` and `{{favicon}}`
    placeholders with the active theme's id, typefaces and icon links and, if
    the municipality configured its color, drops the branding `<style>` at the
    start of the `<head>`. Used by the endpoints that serve HTML with a static
    placeholder (site_identity, accounts, admin). Accepts str or bytes; returns
    the same type. patient_web/site_*.py builds its shell separately and calls
    branding.style()/favicon() itself."""
    if isinstance(text, bytes):
        if (b"{{tema}}" not in text and b"{{fuentes}}" not in text
                and b"{{favicon}}" not in text):
            return text
        tid = current()
        out = text.replace(b"{{tema}}", tid.encode("utf-8"))
        out = out.replace(b"{{fuentes}}", fonts(tid).encode("utf-8"))
        out = out.replace(b"{{favicon}}", favicon(tid).encode("utf-8"))
        est = _branding_style()
        if est and b"<head>" in out:
            out = out.replace(b"<head>", b"<head>" + est.encode("utf-8"), 1)
        return out
    if not text or ("{{tema}}" not in text and "{{fuentes}}" not in text
                    and "{{favicon}}" not in text):
        return text
    tid = current()
    out = (text.replace("{{tema}}", tid).replace("{{fuentes}}", fonts(tid))
               .replace("{{favicon}}", favicon(tid)))
    est = _branding_style()
    if est and "<head>" in out:
        out = out.replace("<head>", "<head>" + est, 1)
    return out
