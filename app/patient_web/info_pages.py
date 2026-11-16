"""info_pages.py -- public information pages built from knowledge articles.

A knowledge article of ``type: "pagina"`` with a ``slug`` is one slice of the
public page ``/<slug>``. Every pack level (``ar`` national, ``ar-b``
provincial, the private town pack) contributes its own article with the SAME
slug and a DISTINCT id (``data_packs.compose`` replaces by id, so two levels
never overwrite each other); this module stacks their blocks into one page,
lowest level first: national, provincial, local.

Article shape (the existing ``articles`` format plus three optional keys)::

    {"id": "...", "type": "pagina", "slug": "vacunacion",
     "title": "...", "jurisdiction": "nacion|provincia|pinamar|l2|l3|l4",
     "summary": "...", "content": "...", "tags": [...], "status": "publicado",
     "source": {...},                       # article-level source, as usual
     "page": {"h1": "... {city} ...",       # any level may set it; the
              "title": "...", "description": "...",   # highest level wins
              "lede": "...",
              "related_clusters": ["vacunas-prevencion"]},
     "blocks": [{"h2": "...", "paragraphs": ["..."], "items": ["..."],
                 "links": [{"title": "...", "url": "https://..."}],
                 "source": {"title": "...", "url": "https://...",
                            "agency": "...", "verified_at": "YYYY-MM-DD"}}]}

Rules: a block WITHOUT a complete source (https url, title, verified_at date)
is never rendered -- every fact on the page carries its source and the date
it was checked. A slug with no published article is not a page (the caller
falls through to the normal 404). ``{city}`` in ``page`` texts comes from
``site_identity``, never from the text itself.
"""

import datetime as _dt
import json
import re

from app.core import i18n
from app.core import site_identity
from app.core import theme
from app.patient_web import catalog
from app.patient_web import knowledge
from app.patient_web import seo
from app.patient_web import site_base

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LEVEL_ORDER = {"l1": 0, "l2": 1, "l3": 2, "l4": 3}


def valid_slug(slug):
    return isinstance(slug, str) and bool(_SLUG.fullmatch(slug))


def _articles(slug=None):
    """Published, current ``pagina`` articles (optionally one slug), ordered
    national -> provincial -> local; ties keep the composed pack order."""
    out = []
    for position, article in enumerate(catalog.knowledge()):
        if article.get("type") != "pagina" or article.get("status") != "publicado":
            continue
        if not valid_slug(article.get("slug")) or not knowledge._current(article):
            continue
        if slug is not None and article["slug"] != slug:
            continue
        level = _LEVEL_ORDER.get(catalog.jurisdiction_level(article.get("jurisdiction")), 9)
        out.append((level, position, article))
    return [a for _l, _p, a in sorted(out, key=lambda row: row[:2])]


def slugs():
    """Slugs that currently render a page (sitemap + routing)."""
    return sorted({a["slug"] for a in _articles() if _blocks(a)})


def _source_ok(src):
    return (isinstance(src, dict)
            and str(src.get("url") or "").startswith("https://")
            and bool(str(src.get("title") or "").strip())
            and bool(_DATE.fullmatch(str(src.get("verified_at") or ""))))


def _texts(value):
    return [str(x).strip() for x in (value or []) if str(x or "").strip()]


def _blocks(article):
    out = []
    for block in article.get("blocks") or []:
        if not isinstance(block, dict) or not str(block.get("h2") or "").strip():
            continue
        if not (_texts(block.get("paragraphs")) or _texts(block.get("items"))):
            continue
        if not _source_ok(block.get("source")):
            continue
        out.append(block)
    return out


def _date_es(iso):
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def _this_year():
    return _dt.date.today().year


def _fill(text):
    """{city} from site_identity and {year} the current year: a page about a
    yearly topic (the flu campaign, the calendar) never shows last year's
    number in its title."""
    return (str(text or "").replace("{city}", site_identity.get("city"))
            .replace("{year}", str(_this_year())))


def _meta(articles):
    meta = {}
    for article in articles:  # low -> high: the highest level wins per key
        page = article.get("page")
        if isinstance(page, dict):
            meta.update({k: v for k, v in page.items() if v})
    return meta


def related_page_for_service(service):
    """``(slug, h1)`` of the page that lists this service's cluster in
    ``page.related_clusters``, or None. Lets a service page (vacunatorio)
    link to its information page without hardcoding either side."""
    cluster = (service or {}).get("cluster")
    if not cluster:
        return None
    for slug in slugs():
        articles = _articles(slug)
        meta = _meta(articles)
        if cluster in (meta.get("related_clusters") or []):
            return slug, _fill(meta.get("h1") or articles[-1].get("title"))
    return None


def _internal_slug(url):
    """``/slug`` of another information page -> slug, else None."""
    url = str(url or "")
    return url[1:] if url.startswith("/") and valid_slug(url[1:]) else None


def _link_ok(url):
    return str(url or "").startswith("https://") or _internal_slug(url) is not None


def _link_li(x):
    title = site_base._esc(_fill(x.get("title")) or x["url"])
    if _internal_slug(x["url"]):
        return f'<li><a class="stz-link" href="{site_base._esc(x["url"])}">{title}</a></li>'
    return (f'<li><a class="stz-link" href="{site_base._esc(x["url"])}" target="_blank" '
            f'rel="noopener">{title}</a></li>')


def _block_html(block, lang):
    src = block["source"]
    paragraphs = "".join(f"<p>{site_base._esc(p)}</p>" for p in _texts(block.get("paragraphs")))
    items = _texts(block.get("items"))
    lst = ('<ul class="facts">' + "".join(f"<li>{site_base._esc(i)}</li>" for i in items) + "</ul>") if items else ""
    links = [x for x in (block.get("links") or []) if isinstance(x, dict) and _link_ok(x.get("url"))]
    links_html = ('<ul class="facts">' + "".join(_link_li(x) for x in links) + "</ul>") if links else ""
    link = (f'<a class="stz-link" href="{site_base._esc(src["url"])}" target="_blank" '
            f'rel="noopener">{site_base._esc(src["title"])}</a>')
    fuente = i18n.t_or("info_page.source", "Fuente: {link} (consultado el {date})", lang,
                       link=link, date=site_base._esc(_date_es(src["verified_at"])))
    return (f'<section class="campaign-section"><h2>{site_base._esc(block["h2"])}</h2>'
            f'{paragraphs}{lst}{links_html}'
            f'<p class="campaign-action-note info-source" style="margin-top:10px">{fuente}</p></section>')


def _ld(path, title, desc, blocks, lang):
    cites, seen = [], set()
    for b in blocks:
        s = b["source"]
        if s["url"] not in seen:
            seen.add(s["url"])
            cites.append({"@type": "CreativeWork", "name": s["title"], "url": s["url"]})
    ld = {"@context": "https://schema.org", "@type": "MedicalWebPage",
          "name": title, "description": desc, "url": seo.canonical_url(path),
          "inLanguage": "es-AR",
          "lastReviewed": max(b["source"]["verified_at"] for b in blocks),
          "isPartOf": {"@type": "WebSite", "name": theme.brand_name(),
                       "url": seo.origin()},
          "citation": cites}
    return ('<script type="application/ld+json">'
            + json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c") + "</script>")


def render(slug):
    """HTML bytes of ``/<slug>``, or None when no published page has it."""
    if not valid_slug(slug):
        return None
    articles = _articles(slug)
    blocks = [b for a in articles for b in _blocks(a)]
    if not blocks:
        return None
    from app.patient_web import site_shell
    lang = i18n.active()
    meta = _meta(articles)
    fallback_title = articles[-1].get("title") or slug
    h1 = _fill(meta.get("h1") or fallback_title)
    title = _fill(meta.get("title") or h1)
    lede = _fill(meta.get("lede") or "")
    desc = _fill(meta.get("description") or lede or articles[-1].get("summary") or h1)
    path = "/" + slug
    body = (f'\n  <div class="campaign-page"><h1 class="h1">{site_base._esc(h1)}</h1>\n'
            + (f'  <p class="lede">{site_base._esc(lede)}</p>\n' if lede else "")
            + "".join(_block_html(b, lang) for b in blocks)
            + "\n  </div>\n")
    return site_shell._shell(f"{title} | {theme.brand_name()}", "", body, desc=desc,
                               canonical=path,
                               head_extra=_ld(path, title, desc, blocks, lang))


def match(method, request_path):
    """Late-page hook for the terminal router: ``HtmlResult`` or None."""
    if method != "GET":
        return None
    parts = [p for p in request_path.strip("/").split("/") if p]
    if len(parts) != 1:
        return None
    try:
        body = render(parts[0])
    except Exception:  # noqa: BLE001 -- a broken pack must degrade to the 404
        return None
    if body is None:
        return None
    from app.patient_web.availability_pages import HtmlResult
    return HtmlResult(body)
