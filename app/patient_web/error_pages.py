"""Patient-facing error documents for the legacy HTTP compatibility shell."""

from app.core import theme
from app.patient_web import site_shell


# The 404 is three lines long: the shared footer follows them instead of being
# pushed to the bottom of the window, which left a screen-high empty band
# between the message and the footer (demo review 2026-11-15).
NOT_FOUND_CSS = ("<style>body>main.wrap>footer,#stzc-page>main.wrap>footer"
                 "{margin-top:0;}</style>")


def not_found_body():
    """Render the branded Spanish 404 document."""
    from app.core import jinja_env
    return site_shell._shell(
        f"Página no encontrada | {theme.brand_name()}",
        "",
        jinja_env.render("fragments/not-found.html"),
        desc="La página que buscás no existe.",
        index=False,
        head_extra=NOT_FOUND_CSS,
    )
