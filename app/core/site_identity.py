"""White-label site identity and the canonical public-page renderer.

Identity values come from ``sitio.json`` in the active data-pack chain.
Python callers use :func:`get`; trusted fragments can still use
:func:`render` to resolve ``{{sitio.*}}`` placeholders. The four public HTML
pages live only under ``templates/`` and the native router renders those
files through the shared Jinja environment. Plain-text ``llms.txt`` and
``robots.txt`` retain their small token pipeline.

Missing identity data degrades to neutral defaults instead of preventing
the site from booting.
"""

import html as _html
import json
import os
import re

from app.ops import demo_installation
from app.core import i18n
from app.core import paths
from app.patient_web import legal
from app.core import sitedata
from app.patient_web import seo
from app.core import static_fingerprint
from app.core import theme

_CACHE = {}

# Neutral fallback when sitio.json or the requested key is missing — the
# site never goes down over this, it just looks generic instead of naming
# the real thing.
_DEFAULTS = {
    "city": "la localidad",
    "province": "",
    # Overridable per-installation (sitio.json: "country") — the jurisdiction
    # concept the referral-analysis prompt uses to reject an order issued by
    # a doctor/institution outside the network this installation serves
    # (ai_referral.py's "{jurisdiccion}"). Defaults to Argentina because
    # every deployment so far is an Argentine public-health installation
    # (MP/MN licences, obra social/PAMI), never assumed in the prompt code
    # itself — a non-Argentine pack overrides it here.
    "country": "Argentina",
    "hospital": "el hospital",
    "network": "los centros de salud",
    "network_long": "el hospital y los centros de salud (CAPS) de la zona",
    "main_center": "",
    "emergency_number": "112",
    "public_origin": seo.DEFAULT_ORIGIN,
    # carries its own preposition ("del …" / "de …"): interpolated after
    # "los servicios" on the home page
    "network_links_html": 'de <a class="stz-link" href="/centers">los centros de salud</a>',
}

_PLACEHOLDER_RE = re.compile(r"\{\{sitio\.([a-zA-Z0-9_.]+)\}\}")

# A curated subset of TOP-LEVEL sitio.json keys, admin-editable from Settings
# (fase 4, 2026-07-30) without touching the data pack's files — stored as
# individual config.py DB overrides (SITE_ID_<KEY>), same precedence rule as
# everything else in config.py (DB > env > .env > default, and
# TURNOS_CONFIG_LOCK still applies). Deliberately NOT every dotted path in
# sitio.json — those stay data-pack content, edited by shipping a new pack;
# this is only the handful of identity strings a deployer needs to fix on
# day one without forking a pack. `main_center` is a center slug — the place
# the patient's hints name when a problem needs someone in person (the product owner
# 23.10); Settings offers it as a list of the installation's centers.
OVERRIDABLE_KEYS = (
    "hospital", "city", "province", "network", "network_long", "emergency_number",
    "main_center",
)

# Identity a deployment declares in its environment (SITE_ID_<KEY>, same
# precedence as above) or in its pack, but that is NOT a Settings field.
# `data_controller` names who is responsible for the patients' data (Ley
# 25.326) — "Municipalidad de Pinamar — Secretaría de Salud" on
# salud.pinamar.gob.ar. Its presence switches the terms and the privacy policy
# to the institutional version (`legal.institucional()`); a prototype
# installation leaves it unset and keeps the prototype text. Two
# installations can share one pack, so the env key is what tells them apart.
DEPLOYMENT_KEYS = ("data_controller",)


def _load():
    # Already keyed by the RESOLVED path (not the bare filename) — the
    # contract catalog._load_json/patient_site._load also follow as of the
    # 2026-07-30 layered-data-packs slice, so a pack-chain change or a file
    # moving between levels invalidates correctly here too.
    path = sitedata.path("sitio.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    _CACHE[path] = (mtime, data)
    return data


# sitio.json values that are PROSE, not identity. `network_links_html` is the
# worst offender: it carries Spanish grammar glue around the links ("del …
# y el …"), so an English page read "find the del Sampleton Regional Hospital
# y el Riverside Clinic services" (2026-08-04). The city, the province and the
# hospital's own name are proper nouns and stay put in every language.
#
# `hero_title`/`hero_lede` replace the home page's generic headline when the
# installation's own press office wrote one (Pinamar, 2026-10-22: «Encontrá la
# atención que necesitás»); `priority_notice` is a legal notice the footer of
# every page carries (Pinamar's Decreto 1116/26 on who has priority). All three
# are optional: a pack without them renders the engine's own copy and no notice.
LOCALIZED_KEYS = ("network", "network_long", "network_links_html",
                  "hero_title", "hero_lede", "priority_notice")


def _localized(dotted_key):
    """The pack's value for `dotted_key`, in this request's language when the
    sweep has cached one. Same cache-only contract as pack_l10n.py: a render
    never waits on a model call, and a miss shows the pack's own language."""
    node = _load()
    value = node.get(dotted_key) if isinstance(node, dict) else None
    if not isinstance(value, str) or not value.strip():
        return _DEFAULTS.get(dotted_key, "")
    try:
        from app.core import translate
        return translate.cached_text(f"sitio.{dotted_key}", value,
                                     i18n.active())
    except Exception:  # noqa: BLE001 — identity never takes the site down
        return value


def sitio_entries():
    """[(cache_key, text)] for the sweep — the mirror of pack_l10n.entries()
    for this file's handful of prose values."""
    node = _load()
    return [(f"sitio.{k}", node[k]) for k in LOCALIZED_KEYS
            if isinstance(node.get(k), str) and node[k].strip()] + info_link_entries()


_lang_primary = i18n.primary_language


def _search_examples(node):
    """The chip list for `i18n.active()` (2026-08-04): the pack's own
    `search_examples` when the visitor reads the site in the PACK's own
    language (Spanish, for every installation so far) — else
    `search_examples_<lang>`, a set `scripts/search_examples.py` generates
    and verifies SEPARATELY for that language (own corpus, own probe run,
    see that script's module docstring), falling back to the canonical list
    when the pack never generated one for this language.

    That fallback is the whole point: a pack that hasn't been regenerated
    for a new language renders EXACTLY what it rendered before this existed
    (Spanish chips under English chrome) rather than an empty row — still
    the wrong language, but never a dead end, and the moment
    scripts/search_examples.py runs for that language it takes over."""
    canonical = node.get("search_examples")
    canonical = canonical if isinstance(canonical, list) else []
    lang = _lang_primary(i18n.active())
    try:
        from app.core import translate  # lazy: keeps this module's import-time footprint small
        pack_lang = _lang_primary(translate.pack_language())
    except Exception:  # noqa: BLE001 — identity never takes the site down
        pack_lang = "es"
    if lang == pack_lang:
        return canonical
    alt = node.get(f"search_examples_{lang}")
    return alt if isinstance(alt, list) and alt else canonical


def search_examples_html():
    """The home page's example search chips, in the visitor's language when
    `scripts/search_examples.py` generated and verified a set for it — see
    `_search_examples()`.

    The terms themselves are never MACHINE-translated as UI copy: each
    language's list is generated from and verified against THAT language's
    own search corpus (real service/doctor data, in that language, run
    through the real matcher) — never a translation of the Spanish chips,
    which would just as often land on a query the English corpus doesn't
    match. A pack with no list at all (for any language) renders no chips,
    which is the honest degradation — a chip that finds nothing teaches the
    opposite of what the row is for."""
    terms = _search_examples(_load())
    if not isinstance(terms, list):
        return ""
    return "\n    ".join(
        f'<a href="/?q={_html.escape(str(t), quote=True)}">'
        f'{_html.escape(str(t))}</a>'
        for t in terms if str(t).strip()
    )


_OLD_CENTER_HREF_RE = re.compile(r'(href\s*=\s*["\'])/centro/')


def _canonical_center_links(html):
    """The pack's `network_links_html` may still carry the old Spanish center
    URL (`/centro/<slug>`), which only 301s to `/center/<slug>`. Print the
    canonical address so the home links straight to the page that ranks; the
    pack data itself is never touched."""
    if not isinstance(html, str):
        return html
    return _OLD_CENTER_HREF_RE.sub(r"\1/center/", html)


_INFO_HREF_RE = re.compile(r"^/[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$")


def info_links():
    """The installation's informational pages, listed on the home page
    (sitio.json "info_links": [{"href": "/vacunacion", "label": "Calendario de vacunación"}]).

    Google finds a page it is never linked to only from the sitemap and may
    leave it "discovered, not crawled" for weeks (Pinamar, 2026-10-15: five
    of these pages, 0 internal links from the home page or the menu). The
    list lives in the pack because only the pack knows which pages exist:
    a pack without the key renders no block. An entry
    with a malformed href (absolute URL, query, trailing slash) or an empty
    label is dropped, never rendered half-way."""
    rows = _load().get("info_links")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "").strip()
        label = str(row.get("label") or "").strip()
        if label and _INFO_HREF_RE.match(href):
            out.append({"href": href, "label": _info_label(href, label)})
    return out


def _info_label_key(href):
    return f"sitio.info_links.{href}.label"


def _info_label(href, label):
    """The label in this request's language when the sweep cached one (same
    cache-only contract as `_localized`: a render never waits on a model, a
    miss shows the pack's own words). The product owner's review of PR 55, 2026-10-15: an
    English page read the Spanish names."""
    try:
        from app.core import translate
        return translate.cached_text(_info_label_key(href), label, i18n.active())
    except Exception:  # noqa: BLE001 — identity never takes the site down
        return label


def info_link_entries():
    """[(cache_key, text)] for the sweep: every well-formed info_links label,
    in the pack's own language."""
    rows = _load().get("info_links")
    out = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "").strip()
        label = str(row.get("label") or "").strip()
        if label and _INFO_HREF_RE.match(href):
            out.append((_info_label_key(href), label))
    return out


def info_related(path):
    """The informational pages a given page sends its reader to next
    (sitio.json "info_related": {"/d/gineco": ["/salud-reproductiva"]}).

    Hrefs only: the label comes from "info_links", so one page has one name
    on the whole site, and an href that is not listed there (a page the
    installation does not have) is never linked. A page never links itself."""
    mapping = _load().get("info_related")
    if not isinstance(mapping, dict) or not path:
        return []
    wanted = mapping.get(path)
    if not isinstance(wanted, list):
        return []
    by_href = {row["href"]: row for row in info_links()}
    # A malformed entry (a dict, a nested list, None) is skipped, never
    # hashed: `[] in by_href` raises TypeError and took the page down
    # (the product owner's review of PR 55, 2026-10-15).
    return [by_href[h] for h in wanted if isinstance(h, str) and h in by_href and h != path]


# The main center as the patient's hints show it (name, address, page,
# contact). Resolving it needs the live catalog, which belongs to the public
# site (patient_web): it registers its resolver here at import, so a domain
# package (cuentas) reads it without importing its own consumer.
_main_center_place = None


def register_main_center_place(fn):
    global _main_center_place
    _main_center_place = fn


def main_center_place():
    """`site_centers.main_center_place()` once the public site is loaded; None
    before that or when the installation names no main center."""
    return _main_center_place() if _main_center_place else None


def get(dotted_key, default=None):
    """Value of sitio.json at `dotted_key` (e.g. "hospital" or
    "hero.titulo").

    Missing key (or the whole file missing) -> `default` if one was passed,
    else the neutral default from `_DEFAULTS` for the top-level key, else
    -> "" (never None, always safe to interpolate into an f-string)."""
    if dotted_key == "public_origin":
        return seo.origin()
    if dotted_key == "brand":
        return theme.brand_name()
    if dotted_key == "search_examples_html":
        return search_examples_html()
    if dotted_key == "emergency_number_link_html":
        number = emergency_number()
        href = re.sub(r"[^0-9+]", "", number)
        return (f'<a class="stz-link" href="tel:{_html.escape(href, quote=True)}">'
                f'{_html.escape(number)}</a>') if href else ""
    if dotted_key == "network_links_html":
        return _canonical_center_links(_localized(dotted_key))
    if dotted_key in LOCALIZED_KEYS:
        return _localized(dotted_key)
    if dotted_key in OVERRIDABLE_KEYS or dotted_key in DEPLOYMENT_KEYS:
        from app.core import config  # lazy: keeps this module's import-time footprint small
        override = config.get("SITE_ID_" + dotted_key.upper())
        if override:
            return override
    node = _load()
    for part in dotted_key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node = None
            break
    if node is not None:
        return node
    if default is not None:
        return default
    return _DEFAULTS.get(dotted_key.split(".", 1)[0], "")


def render(text):
    """Replaces every `{{sitio.<dotted>}}` in `text` with `get(dotted)`,
    HTML-escaped for attribute use too (titles, descriptions and canonical
    URLs use the same placeholders). Exception: a key ending in `_html` is
    inserted RAW — it's authored markup from the data package (e.g. the
    home page's sentence linking to each center), the same trust level as
    the static HTML around it."""
    def _sub(m):
        key = m.group(1)
        val = str(get(key))
        if key.rsplit(".", 1)[-1].endswith("_html"):
            return val
        return _html.escape(val, quote=True)
    return _PLACEHOLDER_RE.sub(_sub, text)


def expand(value):
    """Pack PROSE with its `{{sitio.*}}` placeholders resolved, as plain text
    (the caller escapes it like any other pack string).

    A data pack writes «llamá al {{sitio.emergency_number}}» instead of a
    literal 107/112: one installation, one emergency number, from one source
    (sitio.json / Settings), on every page that says it (demo review
    2026-11-15: the footer said 112, telemedicine said 107). Works on a
    string, or recursively on a list/dict of them; returns the SAME object
    when nothing changed, a new one otherwise — never mutates the cached
    pack data it is given. A `*_html` key is markup, never pasted into prose:
    it resolves to an empty string here."""
    if isinstance(value, str):
        if "{{sitio." not in value:
            return value

        def _sub(m):
            key = m.group(1)
            if key.rsplit(".", 1)[-1].endswith("_html"):
                return ""
            return str(get(key))
        return _PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, list):
        out = [expand(v) for v in value]
        return value if all(a is b for a, b in zip(out, value)) else out
    if isinstance(value, dict):
        out = {k: expand(v) for k, v in value.items()}
        return value if all(out[k] is value[k] for k in value) else out
    return value


def emergency_number():
    """The installation's emergency number as written (sitio.json /
    Settings `emergency_number`) — the ONE source the footer, the
    informational pages and the expanded pack texts all read."""
    return str(get("emergency_number") or "").strip()


def base_url(handler=None):
    """Public origin of the site for building absolute links (reset emails,
    waitlist offers, ICS feeds). It deliberately shares the SEO origin:
    ``TURNOS_PUBLIC_ORIGIN`` in deployments and the loopback development
    origin in a fresh checkout. Client-controlled Host headers never select
    an application link destination."""
    return seo.origin()


def origin_is_loopback():
    """True when `base_url()` is the development fallback or any loopback
    address: a link built from it only opens on this machine."""
    return seo.origin_is_loopback()


# ---------------------------------------------------------------- routes
_DIR = paths.PROJECT_ROOT

# path relative to _DIR (the repo) per served public route
_PAGES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/about.html": "about.html",
    "/privacy.html": "privacy.html",
    "/terms.html": "terms.html",
    "/llms.txt": "llms.txt",
    # Without this file of our own, Cloudflare answers /robots.txt with its
    # "managed" robots.txt, which does NOT exclude /admin.
    "/robots.txt": "robots.txt",
}


def render_page(filename, handler=None, *, lang=None):
    """(body_bytes, ctype) for one of `_PAGES`' static files, fully
    rendered ({{sitio.*}} and {{t.*}} resolved, {{lang}}/{{tema}} injected,
    demo banner injected). The native router consumes this renderer directly,
    and tests can render `/` without constructing an HTTP request. None if the
    file is missing.

    `handler` is optional and ONLY used to resolve the active language
    (i18n.resolve_lang reads its Cookie/Accept-Language headers) — every
    existing call site (tests included) that calls this positionally with
    just `filename` keeps working, resolving to the canonical language
    ("es"), which is what keeps the golden-page byte-identity tests honest:
    with i18n unconfigured (the default), this is a no-op. Native callers may
    pass the already-bound `lang` explicitly so every native route consumes this
    one renderer without re-resolving request state."""
    lang = lang or i18n.resolve_lang(handler)
    is_text = filename.endswith(".txt")
    if is_text:
        # plain-text pages are templates with tokens, kept next to the HTML ones
        path = os.path.join(_DIR, "templates", filename)
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            return None
        # Plain-text public files retain the small legacy token pipeline; they
        # are not Jinja templates and never had duplicate template sources.
        pre = i18n.render(raw, lang)
        pre = legal.render(pre, lang)
        pre = static_fingerprint.render(pre)
        rendered = render(pre)
        rendered = i18n.inject_html_lang(rendered, lang)
        rendered = i18n.inject_bootstrap(rendered, handler, lang)
        if filename == "robots.txt":
            rendered = demo_installation.robots_txt(rendered)
        return rendered.encode("utf-8"), "text/plain; charset=utf-8"

    # Import lazily: jinja_env's globals intentionally call back into this
    # module, so importing it at module initialization would create a cycle.
    from app.core import jinja_env
    _bound_lang, token = i18n.bind_lang(lang)
    try:
        try:
            rendered = jinja_env.render(filename)
        except jinja_env.jinja2.TemplateNotFound:
            return None
    finally:
        i18n.unbind(token)
    rendered = i18n.inject_html_lang(rendered, lang)
    rendered = i18n.inject_bootstrap(rendered, handler, lang)
    themed = theme.inject(rendered)
    # These pages resolve request language, so the demo banner uses that
    # language and may expose the language switcher.
    body = demo_installation.inject_html(
        themed, "public", lang=lang, switchable=True).encode("utf-8")
    return body, "text/html; charset=utf-8"
