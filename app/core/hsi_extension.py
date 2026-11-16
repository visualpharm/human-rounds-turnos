"""The desk's HSI extension (HSI Humanizador) as the panel sees it.

Without an EMR push the only way a turno reaches HSI is that extension, so
every screen that offers «Cargar en HSI» («Turno creado», Solicitudes,
«Pacientes de hoy») asks here whether THIS site is one the extension runs on
and where to install it.

Where to install it (contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa
pantalla»): the panel always says it while the extension is not on the
computer — the store page once the item is published, else the ZIP the desk
loads by hand; the desk's guide (PDF) in both cases. The ZIP and the PDF are
the ones `scripts/build_extension.py` and `scripts/build_hsi_extension_guide.mjs`
leave in `extensions/dist/`, served by `GET /api/admin/hsi-extension/<name>`.
"""

import fnmatch
import json
import os
import re

from app.core import paths

_ROOT = paths.PROJECT_ROOT
_EXT_DIR = os.path.join(_ROOT, "extensions", "hsi-humanizador")
_DIST_DIR = os.path.join(_ROOT, "extensions", "dist")

# The two files the panel hands out: URL name -> (file in extensions/dist,
# content type, download name).
FILES = {
    "guide.pdf": ("guia-turnos-a-hsi.pdf", "application/pdf", "guia-hsi-humanizador.pdf"),
    "extension.zip": ("hsi-humanizador.zip", "application/zip", "hsi-humanizador.zip"),
}
URL_PREFIX = "/api/admin/hsi-extension/"


def _read_item():
    """The extension's manifest matches, its store item and its optional sites,
    or None when one of them is missing or unreadable."""
    try:
        with open(os.path.join(_EXT_DIR, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        with open(os.path.join(_EXT_DIR, "store", "item.json"), encoding="utf-8") as f:
            item = json.load(f)
        with open(os.path.join(_EXT_DIR, "lib", "shared.js"), encoding="utf-8") as f:
            shared = f.read()
    except (OSError, ValueError):
        return None
    return manifest, item, shared


def _serves_this_site(manifest, shared):
    """Two lists, both the extension's own: its manifest's content_scripts,
    and `TURNOS_OPCIONALES` in its lib/shared.js — the sites it serves once
    the operator allows them (optional permission, so adding one never makes
    Chrome switch the extension off for the desk)."""
    from app.core import site_identity
    admin = str(site_identity.base_url() or "").rstrip("/") + "/admin/"
    matches = [m for cs in manifest.get("content_scripts") or [] for m in cs.get("matches") or []]
    bloque = re.search(r"TURNOS_OPCIONALES = \[(.*?)\];", shared, re.S)
    matches += [u.rstrip("/") + "/admin/*" for u in re.findall(r'url: "(https?://[^"]+)"', bloque.group(1) if bloque else "")]
    return any(fnmatch.fnmatchcase(admin, m) for m in matches)


def _published_store(item):
    """The store page when the item reaches the desk from the store: an
    address AND a published visibility (`scripts/publish_extension.py` sends
    «unlisted»/«public» to everyone, anything else only to testers)."""
    url = item.get("store_url") or None
    return url if url and item.get("visibility") in ("unlisted", "public") else None


def store_url():
    """The store page of the desk's HSI extension when THIS site is one of the
    sites it runs on, else None. The panel draws «Cargar en HSI» on it; where
    to install the extension is `install()`."""
    leido = _read_item()
    if not leido:
        return None
    manifest, item, shared = leido
    return (item.get("store_url") or None) if _serves_this_site(manifest, shared) else None


def file_path(name):
    """The file behind `GET /api/admin/hsi-extension/<name>`, or None."""
    entry = FILES.get(name)
    if not entry:
        return None
    path = os.path.join(_DIST_DIR, entry[0])
    return path if os.path.isfile(path) else None


def install():
    """How the desk installs the extension on THIS site, or None when the
    extension does not run here: {"tienda": store page or None, "zip": the ZIP
    while there is no published store page, "guia": the guide PDF}. A file
    missing from the image is left out, never linked."""
    leido = _read_item()
    if not leido:
        return None
    manifest, item, shared = leido
    if not _serves_this_site(manifest, shared):
        return None
    tienda = _published_store(item)
    zip_url = None if tienda or not file_path("extension.zip") else URL_PREFIX + "extension.zip"
    guia = URL_PREFIX + "guide.pdf" if file_path("guide.pdf") else None
    if not tienda and not zip_url:
        return None
    return {"tienda": tienda, "zip": zip_url, "guia": guia}
