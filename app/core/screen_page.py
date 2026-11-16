"""screen_page.py - the page of a server-routed admin screen, assembled once (2026-10-28).

Twenty-odd `*_screen.py` (`admin_panel/`, `modules/*/`) repeated the same hundred nodes: render the
template with the sidebar and the active section, tag the language, add the client catalogs, run the
theme, answer `text/html` with `Cache-Control: no-store`. What a screen decides stays in the screen: who may
open it (the `Depends`), what it adds to the template context, which catalog namespaces beyond `admin` it
needs and the early exits (a hidden section is a 404, a role a 403...).
`tests/test_admin_screen_page_contract.py` pins what each screen answers and the order of the steps.
"""

from fastapi import Response

from app.core import i18n, jinja_env, screen_nav, theme


def admin_html(identity, template, section, /, *, namespaces=("admin",), **context):
    """The finished HTML of the screen `section` for `identity`: `context` goes to the template next to
    the sidebar (`nav_items`) and `active_section`; the client catalogs are the shared ones plus `namespaces`.
    The first three are positional-only so that a context may be named `identity`."""
    lang = i18n.active()
    rendered = jinja_env.render(
        template,
        nav_items=screen_nav.nav_items(identity.get("rol")),
        active_section=section,
        **context,
    )
    rendered = i18n.inject_html_lang(rendered, lang)
    rendered = i18n.inject_bootstrap(
        rendered, None, lang, namespaces=list(i18n.client_namespaces()) + list(namespaces))
    return theme.inject(rendered)


def admin_page(identity, template, section, /, *, namespaces=("admin",), **context):
    """`admin_html` as the response every admin screen sends."""
    body = admin_html(identity, template, section, namespaces=namespaces, **context)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
