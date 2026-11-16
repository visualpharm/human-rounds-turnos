"""sitemap.py — GET /sitemap.xml, armado desde el catálogo vivo.

Lo referencia robots.txt. Lista SÓLO lo público e indexable, con el mismo
criterio que robots.txt y que el `noindex` de seo.py: catálogo y páginas
informativas sí; trámite, panel y cualquier URL cuyo token sea la credencial,
no. Si el catálogo no está disponible (Postgres caído), devuelve igual las
rutas fijas en vez de un 500 — un sitemap corto es mejor que ninguno.
"""

import xml.sax.saxutils as _sx

from app.core import data_packs
from app.ops import demo_installation
from app.patient_web import catalog
from app.patient_web import seo

# Rutas fijas. La home primero; después los catálogos que son la puerta de
# entrada real; al final lo institucional. Sin <priority> (Google lo ignora,
# auditoría SEO 2026-10-01) y sin <lastmod>: no tienen una fecha de cambio
# real que informar.
_FIJAS = [
    "/",
    "/doctors",
    "/centers",
    "/d/diagnostico",
    "/d/grupos",
    # /upload-order NO va: la página se sirve con noindex a propósito
    # (preparation.py, index=False) y un sitemap que lista una URL
    # noindex es una contradicción que Search Console reporta.
    # /news NO va: el changelog se sirve con noindex (patient_web/site_*.py.
    # _page_news) — mismo criterio que /upload-order.
    # Informational search results (2026-08-27 hospital meeting): public,
    # indexable, no booking affordance.
    "/telemedicine",
    "/zoonosis",
    "/blood-donation",
    "/about.html",
    "/privacy.html",
    "/terms.html",
]


def _urls():
    # The demo (fictional data, noindex on every page) lists nothing: a
    # sitemap of noindex URLs is an error in Search Console.
    if demo_installation.enabled():
        return []
    origin = seo.origin().rstrip("/")
    vistas = set()
    out = []

    def add(path, lastmod=None):
        if path in vistas:
            return
        vistas.add(path)
        out.append((origin + path, lastmod))

    for path in _FIJAS:
        add(path)

    try:
        # Every specialty/cluster page that has something to show (/d/<id>:
        # same rule as site_page_doctors._page_cluster — a known cluster with at
        # least one service or one professional). /d/clinica, the general-
        # medicine entry point, was missing (SEO audit 2026-10-01, Pinamar
        # item 2) because only the two navigation clusters were listed here.
        con_contenido = {s.get("cluster") for s in catalog.services()}
        con_contenido |= {cid for m in catalog.public_doctors() if not m.get("rol")
                          for cid in catalog.doctor_clusters(m)}
        # The walk-in page («Demanda espontánea», a `"kind": "walkin"`
        # cluster) has content whenever any walk-in service exists.
        hay_walkin = any(s.get("modo") == "walkin" and "grupos" not in
                         (s.get("categoria"), s.get("cluster"))
                         for s in catalog.services())
        for cl in data_packs.compose("clusters.json"):
            if cl.get("id") and (cl["id"] in con_contenido
                                 or (cl.get("kind") == "walkin" and hay_walkin)):
                add(f"/d/{cl['id']}")
        # <lastmod> = the day the admin last edited that item's data
        # (schedule, access rules…) — only when there IS such an edit; the
        # pack's base JSON has no real modification date to report.
        editado = {t: catalog.edited_dates(t) for t in ("centros", "servicios", "medicos")}
        # A center whose one page is an informational page (Zoonosis →
        # /zoonosis) is listed there, never at the /center/ address that
        # answers 301.
        from app.patient_web import site_centers
        paginas = site_centers._center_pages()
        for c in catalog.centers():
            slug = c.get("slug") or c.get("id")
            if slug and slug not in paginas:
                add(f"/center/{slug}", editado["centros"].get(slug))
        for s in catalog.services():
            if s.get("id"):
                add(f"/service/{s['id']}", editado["servicios"].get(s["id"]))
        for m in catalog.public_doctors():
            # Las filas de ROL del padrón ("Enfermería", "Administrativo") no
            # son personas con página propia: site_catalog._professionals las
            # filtra de todas las vistas, así que tampoco van al sitemap.
            if m.get("id") and not m.get("rol"):
                add(f"/doctor/{m['id']}", editado["medicos"].get(m["id"]))
    except Exception:  # noqa: BLE001 — sin catálogo, al menos las fijas
        pass
    try:
        # One page per preparation guide the catalog offers (SEO audit
        # 2026-10-01, Pinamar item 17); same set patient_web/site_*.py serves. Its
        # own try, last: if the guide index fails, the doctors, services
        # and centers above stay listed.
        from app.patient_web import site_preparation
        for pid in site_preparation._prep_page_ids():
            add(f"/preparation/{pid}")
    except Exception:  # noqa: BLE001 — sin guías, el resto del catálogo igual
        pass
    try:
        # Information pages from the knowledge packs (/<slug>, type
        # "pagina"): one URL per slug with at least one sourced block.
        from app.patient_web import info_pages
        for slug in info_pages.slugs():
            add(f"/{slug}")
    except Exception:  # noqa: BLE001 -- sin páginas, el resto igual
        pass
    return out


def xml():
    filas = "".join(
        f"<url><loc>{_sx.escape(u)}</loc>"
        + (f"<lastmod>{_sx.escape(f)}</lastmod>" if f else "")
        + "</url>"
        for u, f in _urls())
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{filas}</urlset>").encode("utf-8")
