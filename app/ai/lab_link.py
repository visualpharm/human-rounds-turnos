"""lab_link.py — signed link from the patient's lab-results section to the
provider's own laboratory page (Pinamar: laboratorio.pinamar.gob.ar).

The provider's nginx requires an nginx `secure_link` signature only on
`/ingresos/hc-paciente/`:

    secure_link_md5 "$secure_link_expires$uri KEY";
    URL = <base>/<DNI>?md5=<base64url(MD5("<expires><uri> KEY"))>&expires=<unix>

`<uri>` is the URL path (what nginx calls `$uri`, no query), `base64url` is the
unpadded URL-safe alphabet nginx expects (`+`→`-`, `/`→`_`, no `=`). The link
lives 5 minutes; the patient never sees it in a page — a button leads to
`/account/lab-results/open`, which signs for the DNI of the logged-in account
and answers 302.

Installation environment only (process env, then the env file), never a DB
setting — the key is a secret:
  TURNOS_LAB_LINK_SECRET  the shared key; unset = no button, no route (degrade)
  TURNOS_LAB_LINK_BASE    the address without the DNI (default below)
Docs: docs/architecture/laboratorio-paciente.md, «Provider link»."""

import base64
import hashlib
import os
import time
from urllib.parse import urlsplit

from app.core import config

DEFAULT_BASE = "https://laboratorio.pinamar.gob.ar/ingresos/hc-paciente/false"
TTL_SECONDS = 5 * 60
OPEN_PATH = "/account/lab-results/open"


def _env(name):
    return os.environ.get(name, config.dotenv_values().get(name, "")).strip()


def secret():
    return _env("TURNOS_LAB_LINK_SECRET")


def base():
    return (_env("TURNOS_LAB_LINK_BASE") or DEFAULT_BASE).rstrip("/")


def enabled():
    return bool(secret())


def _digits(value):
    return "".join(c for c in str(value or "") if c.isdigit())


def signature(expires, uri, key):
    """The `md5` query value nginx recomputes from `expires`, `$uri` and its key."""
    digest = hashlib.md5(f"{expires}{uri} {key}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def signed_url(dni, now=None):
    """The signed provider URL for ONE DNI, or None without a key or a DNI.
    The DNI is the only thing that varies per link and it is always the
    caller's own (the route reads it from the session, never the request)."""
    key = secret()
    dni = _digits(dni)
    if not key or not dni:
        return None
    url = f"{base()}/{dni}"
    expires = int(now if now is not None else time.time()) + TTL_SECONDS
    return f"{url}?md5={signature(expires, urlsplit(url).path, key)}&expires={expires}"
