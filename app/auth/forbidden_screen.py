"""Standalone 403 page for direct admin screens (core/deps.py's screen gate).

Same shape as auth/password_reset.py: a pure `page_html(path)` that returns
bytes, no handler. The screen gate renders it when an AUTHENTICATED session
asks for a screen its role cannot see (admisionista on /admin/users, a
medico on /admin/reportes…). Raw JSON is for API clients; a person with a
stale bookmark deserves Spanish and a way back, not `{"error":
"solo_admin"}`.
"""

from markupsafe import Markup

from app.core import jinja_env
from app.core import theme
from app.core import static_fingerprint


def page_html(path):
    """Bytes puros del HTML 403 para `path` — sin tocar ningún handler."""
    safe = "/".join(seg for seg in str(path or "").split("/") if seg)[:120]
    shown = "/" + safe if safe else ""
    tid = theme.current()
    brand = theme.brand_name(tid)
    # Same primary button as the admin login (admin-gate.css .btn-ok.btn-lg): templates/forbidden-screen.html.
    return jinja_env.render(
        "forbidden-screen.html", tid=tid, brand=brand, shown=shown, fonts=Markup(theme.fonts(tid)),
        human_rounds_css=static_fingerprint.url("human-rounds.css"),
        admin_gate_css=static_fingerprint.url("admin-gate.css")).encode()
