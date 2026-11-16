"""Shared SEO metadata for this engine's HTML pages.

Public informational pages get a canonical URL and social-preview metadata.
Private, transactional, test, and error pages get an explicit ``noindex``
directive and deliberately omit canonical/social metadata.
"""

import html
import re

from app.core import theme


# Development-safe fallback shared with ``site_identity.base_url(None)``.
# Every public deployment must set TURNOS_PUBLIC_ORIGIN; an unset fresh
# checkout must never advertise the reference deployment as its canonical.
DEFAULT_ORIGIN = "http://127.0.0.1:7700"


def _plain(value):
    """Collapse whitespace so metadata stays valid and readable."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _description(value, limit=160):
    """Keep descriptions within the usual search-snippet budget."""
    text = _plain(value)
    if len(text) <= limit:
        return text
    shortened = text[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return shortened + "…"


def origin():
    # Runtime configuration has one precedence contract (DB > process env >
    # effective .env > default). Import lazily to avoid a module cycle through
    # site_identity during boot, while still honoring operator-saved config.
    from app.core import config
    return (config.get("TURNOS_PUBLIC_ORIGIN", DEFAULT_ORIGIN)
            or DEFAULT_ORIGIN).strip().rstrip("/")


def origin_is_loopback():
    """True when the public origin is the development fallback (nothing set) or
    any loopback address: a link built from it only opens on this machine."""
    from urllib.parse import urlparse
    host = (urlparse(origin()).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0") or host.endswith(".localhost")


def canonical_url(path):
    path = "/" + str(path or "").lstrip("/")
    if path != "/":
        path = path.rstrip("/")
    return origin() + path


def head(title, description, path=None, index=True):
    """Return escaped title, description, robots, canonical, and social tags."""
    title = _plain(title)
    description = _description(description)
    if not title:
        raise ValueError("SEO title is required")
    if not description:
        raise ValueError(f"SEO description is required for {title!r}")

    esc_title = html.escape(title, quote=True)
    esc_desc = html.escape(description, quote=True)
    robots = "index,follow" if index else "noindex,nofollow"
    lines = [
        f"<title>{esc_title}</title>",
        f'<meta name="description" content="{esc_desc}">',
        f'<meta name="robots" content="{robots}">',
    ]
    if index:
        if path is None:
            raise ValueError(f"Canonical path is required for indexable page {title!r}")
        canonical = html.escape(canonical_url(path), quote=True)
        image = html.escape(canonical_url("/logo.png"), quote=True)
        lines.extend([
            f'<link rel="canonical" href="{canonical}">',
            '<meta property="og:type" content="website">',
            '<meta property="og:locale" content="es_AR">',
            f'<meta property="og:site_name" content="{html.escape(theme.brand_name(), quote=True)}">',
            f'<meta property="og:title" content="{esc_title}">',
            f'<meta property="og:description" content="{esc_desc}">',
            f'<meta property="og:url" content="{canonical}">',
            f'<meta property="og:image" content="{image}">',
            f'<meta property="og:image:alt" content="{html.escape(theme.brand_name(), quote=True)}">',
            '<meta name="twitter:card" content="summary">',
            f'<meta name="twitter:title" content="{esc_title}">',
            f'<meta name="twitter:description" content="{esc_desc}">',
            f'<meta name="twitter:image" content="{image}">',
        ])
    return "\n".join(lines)
