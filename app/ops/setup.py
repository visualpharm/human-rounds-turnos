"""setup.py — first-run installer wizard (phase 5 of the open-source rework,
2026-07-30). Boots the moment a checkout is genuinely unconfigured: zero
operators AND zero `catalog` rows of type "config" (see `is_fresh_install()`
below). Walks the deployer through location → data pack → language(s) →
admin account → API keys, writing everything through the SAME seams the rest
of the app uses (`config.py`'s DB layer, `data_packs.py`'s pack registry,
`db_operators.operator_create` — the identical code path `admin_users.py` uses to
create a panel account) and creating no new persistence or auth mechanism.

## The gate

`is_fresh_install()` decides "fresh" with a fail-closed default and a
one-way LATCH: the moment either an operator or a config row is observed,
this process remembers "configured" forever and never queries the DB for
this again — after the wizard finishes (normal operation, forever after)
this check costs nothing. Before that point it re-checks the DB on every
request (cheap: two `SELECT`s), because a second browser tab, another
process, or a human finishing the wizard in a different window must be
picked up immediately, not after some cache TTL.

DB-down degrades to "not fresh" (`db.DbUnavailable` -> False) on purpose —
forcing every route into a wizard nobody can complete because the very
storage layer the wizard writes to is unreachable would turn a transient
outage into a total outage. A real fresh install with DB down just behaves
like today's degraded mode elsewhere in the app.

The public demo (`demo_installation.enabled()`) is EXPLICITLY excluded: it
mints admin sessions for a synthetic "demo" user with no operator row and,
being legacy `TURNOS_WEBDATA_DIR` mode, may have zero config rows either —
by the literal DB check it would look "fresh" and hijack every demo route
into a self-install wizard. The deployer already configured that
installation by setting `TURNOS_DEMO_MODE=1`; the wizard has nothing to add.

## The wizard itself

Server-rendered, no framework, one `<form>` per step (`wizard-design`: a
step shows only its own task; `form-design`: content-width fields, labels
before hints, one primary action). State travels between steps in a small
HMAC-signed cookie (`st_setup`) — never in the DB, so an abandoned wizard
never contributes a stray config row that would incorrectly flip
`is_fresh_install()` to False before an operator actually exists. The
admin account's password is hashed by the shared password library the moment
it's submitted and only the hash rides in the cookie afterwards — the same
Argon2 hash `operator_create()` would have produced, just computed one step
earlier so the plaintext doesn't sit in a cookie across steps.

The final POST (`/setup/apikeys`) is the one atomic commit: scaffolds a new
town pack if one was requested, writes `PACK_CHAIN`/`LANGUAGES`/
`OPENROUTER_API_KEY`/etc via `config.guardar()`, and creates the operator
via `db_operators.operator_create(..., pass_hash=...)` — the exact function
`admin_users.create_user()` ultimately calls, just with a pre-computed hash instead of a
plaintext password. Nothing about admin-account creation is reinvented.
"""

import html
import json
import os
import re
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from app.auth import admin_auth
from app.auth import password_hashing
from app.core import config
from app.core import data_packs
from app.core import db
from app.core import db_operators
from app.core import db_auth
from app.core import db_catalog
from app.core import i18n
from app.core import static_fingerprint
from app.core import text_fold

from app.core import usernames  # la regla del nombre de usuario, compartida con el panel
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_STEPS = ("location", "packs", "language", "admin", "backups", "apikeys")

# Common CF-IPCountry codes this project is likely to see first (AR
# deployments, the reference install, plus regional/major ones). Unknown
# codes still work — the raw code is shown so nothing silently breaks, it
# just isn't spelled out in words.
_COUNTRY_NAMES = {
    "AR": "Argentina", "UY": "Uruguay", "CL": "Chile", "PY": "Paraguay",
    "BO": "Bolivia", "PE": "Perú", "BR": "Brasil", "MX": "México",
    "CO": "Colombia", "EC": "Ecuador", "VE": "Venezuela", "ES": "España",
    "US": "Estados Unidos", "IN": "India", "PT": "Portugal", "IT": "Italia",
    "FR": "Francia", "DE": "Alemania", "GB": "Reino Unido",
}

_LANG_NAMES = {"es": "Español", "en": "English"}


# ============================================================== the gate

_LATCH = {"configured": False}


def is_fresh_install():
    """True only when nothing has ever been configured: zero operators AND
    zero `catalog` rows of type "config". See module docstring for the
    latch/demo/DB-down reasoning."""
    if _LATCH["configured"]:
        return False
    from app.ops import demo_installation
    if demo_installation.enabled():
        return False
    # An installation whose deployer already set the owner's password in the environment is
    # configured, whatever its database holds: an emptied or restored database must not hand the
    # admin account to the first visitor of /setup (security review 2026-11, L-1).
    if admin_auth._admin_pass():
        return False
    try:
        if db_operators.operators_list():
            _LATCH["configured"] = True
            return False
        if db_catalog.catalog_list("config"):
            _LATCH["configured"] = True
            return False
    except db.DbUnavailable:
        return False
    return True


def mark_configured():
    """Called the instant the wizard commits — closes the gate immediately
    in THIS process without waiting for the next DB round-trip."""
    _LATCH["configured"] = True


def _reset_cache_para_tests():
    _LATCH["configured"] = False


# ============================================================== cookie state

_COOKIE_NAME = "st_setup"
_COOKIE_MAX_AGE = 3600  # an hour is plenty for a first-run wizard session

# Per-process signing key. There is no admin password yet to derive a key
# from (that's exactly what this wizard is about to create), and deriving
# from DATABASE_URL would leak a DB credential fragment into a cookie
# signature. TURNOS_SETUP_SECRET lets a multi-process deployment pin one
# (this app runs a single uvicorn process per the Dockerfile (asgi_main.py),
# so the default random-per-process key is fine for the common case).
_SECRET = (os.environ.get("TURNOS_SETUP_SECRET") or "").encode("utf-8") or secrets.token_bytes(32)


def _sign(payload_b64):
    import hashlib
    import hmac
    return hmac.new(_SECRET, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def _b64u(data):
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_dec(s):
    import base64
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _state(request):
    """The wizard's in-progress answers, decoded from the signed cookie —
    {} if there is none, it's malformed, or its signature doesn't match
    (never raises: a broken cookie just restarts the wizard from scratch)."""
    raw = (request.headers.get("Cookie") or "")
    token = None
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == _COOKIE_NAME:
                token = v.strip()
    if not token:
        return {}
    try:
        p, sig = token.split(".", 1)
    except ValueError:
        return {}
    import hmac
    if not hmac.compare_digest(_sign(p), sig):
        return {}
    try:
        return json.loads(_b64u_dec(p).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _state_cookie(state, secure):
    p = _b64u(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    token = f"{p}.{_sign(p)}"
    parts = [f"{_COOKIE_NAME}={token}", f"Max-Age={_COOKIE_MAX_AGE}", "Path=/",
              "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _clear_cookie(secure):
    parts = [f"{_COOKIE_NAME}=", "Max-Age=0", "Path=/", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _is_secure(request):
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    return proto == "https"


# ============================================================== helpers

def _name_key(text):
    """What a place name is compared by: no accents, no case, no parenthetical
    note. A deployer types «Buenos Aires»; the province's pack is called
    «Buenos Aires (provincia)» (the note keeps it apart from the city)."""
    plain = text_fold.ascii_fold(str(text or ""))
    plain = re.sub(r"\([^)]*\)", " ", plain)
    return " ".join(plain.lower().split())


def _slugify(text):
    s = text_fold.ascii_fold(text or "")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "localidad"


def _unique_slug(base):
    slug = base
    n = 2
    while data_packs.manifest(slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _propose_country(request):
    """(code, name) proposed from CF-IPCountry when Cloudflare is in front,
    else a best-effort free geo-IP lookup — never raises, never blocks past
    a short timeout. None, None if nothing usable came back."""
    cf = (request.headers.get("CF-IPCountry") or "").strip().upper()
    if cf and cf not in ("", "XX", "T1"):  # XX/T1 = Cloudflare's own "unknown"/Tor markers
        return cf, _COUNTRY_NAMES.get(cf, cf)
    if os.environ.get("TURNOS_SETUP_SKIP_GEOIP") == "1":
        return None, None
    try:
        client = request.client
        ip = admin_auth.turnstile.client_ip_from(
            request.headers, client.host if client else "")
        code = _geoip_lookup(ip) if ip and ip not in ("127.0.0.1", "::1") else None
    except Exception:  # noqa: BLE001 — a proposal is a courtesy, never load-bearing
        code = None
    if not code:
        return None, None
    return code, _COUNTRY_NAMES.get(code, code)


def _geoip_lookup(ip):
    """A free, keyless geo-IP lookup — factored out so tests can monkeypatch
    it instead of hitting the network. Short timeout, swallows everything."""
    url = f"https://ip-api.com/json/{urllib.parse.quote(ip)}?fields=countryCode"
    req = urllib.request.Request(url, headers={"User-Agent": "sana-turnos-setup/1"})
    with urllib.request.urlopen(req, timeout=1.5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("countryCode") or "").strip().upper() or None


def _propose_chain(country, province, town):
    """(prefix_chain, town_pack_id, parent_for_scaffold). `prefix_chain` is
    every matched pack BELOW town level (global + country + province, each
    only if a pack with that exact display name exists); `town_pack_id` is
    the matched town-level pack id, or None if none matched (scaffold
    time)."""
    packs = data_packs.list_packs()

    def _match(level, name):
        n = _name_key(name)
        if not n:
            return None
        for p in packs:
            if p.get("level") == level and _name_key(p.get("name")) == n:
                return p.get("id")
        return None

    chain = []
    if any(p.get("id") == "global" for p in packs):
        chain.append("global")
    country_id = _match(2, country)
    if country_id:
        chain.append(country_id)
    province_id = _match(3, province)
    if province_id:
        chain.append(province_id)
    town_id = _match(4, town)
    parent = province_id or country_id or (chain[0] if chain else None)
    return chain, town_id, parent


def _scaffold_pack(display_name, province, parent_id):
    """Scaffolds an install-owned town pack ON THE PERSISTENT VOLUME
    (``data_packs.install_packs_dir()``), never the image-tree ``packs/``, so
    a clinic's data survives a container rebuild that wipes the image. Writes
    ``pack.json`` + ``sitio.json`` (example-town shape, town name substituted)
    plus a frozen ``INVENTORY.json`` so the reviewed readiness gate admits the
    pack on the next boot. Returns the new pack's id; ``data_packs``
    invalidation lets it resolve on the very next request."""
    base_slug = _slugify(display_name)
    slug = _unique_slug(base_slug)
    install_root = data_packs.install_packs_dir()
    os.makedirs(install_root, exist_ok=True)
    pack_dir = os.path.join(install_root, slug)
    os.makedirs(pack_dir, exist_ok=True)
    manifest = {
        "id": slug,
        "level": 4,
        "parent": parent_id,
        "language": "es",
        "name": display_name,
        "description": (
            f"Localidad creada por el asistente de instalación para {display_name}. "
            "Reemplazá centros, servicios y médicos por los datos reales desde el "
            "panel de administración — ver docs/architecture/data-packs.md."
        ),
    }
    with open(os.path.join(pack_dir, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    sitio = {
        "city": display_name,
        "province": province or "",
        "hospital": display_name,
        "network": display_name,
        "network_long": display_name,
        "main_center": "",
        "emergency_number": "911",
        # Deliberately NOT set: an explicit "" here would override
        # site_identity._DEFAULTS's neutral fallback and print a broken
        # sentence on the home page ("...encontrá los servicios . Urgencias:
        # ...", empty phrase + dangling period) since there are no centers
        # yet to link to. Omitting the key lets `get()` fall through to the
        # generic "de los centros de salud" phrasing until the deployer
        # loads real centers and can author their own network_links_html.
    }
    with open(os.path.join(pack_dir, "sitio.json"), "w", encoding="utf-8") as f:
        json.dump(sitio, f, ensure_ascii=False, indent=2)
        f.write("\n")
    # Freeze the pack's inventory so the required-pack readiness gate admits
    # it on every later boot. The pack is registered as required (and the
    # install root as TURNOS_PRIVATE_PACKS_DIR) by the commit step below.
    data_packs.write_inventory(pack_dir)
    data_packs.invalidate()
    return slug


def _t(key, **kw):
    # The request's language, like every other string of this file: without
    # it `i18n.t` reads the canonical catalog and the wizard stays Spanish
    # in English.
    return i18n.t(key, i18n.active(), **kw)


# ============================================================== HTML shell

def _shell(step_key, step_num, title, body_html, back_href=None, error=None):
    step_names = {
        "welcome": _t("setup.step.welcome"),
        "location": _t("setup.step.location"),
        "packs": _t("setup.step.packs"),
        "language": _t("setup.step.language"),
        "admin": _t("setup.step.admin"),
        "apikeys": _t("setup.step.apikeys"),
        "backups": i18n.t_or("backup.title", "Copias de seguridad", i18n.active()),
        "done": _t("setup.step.done"),
    }
    total = len(_STEPS)
    progress = (
        f'<p class="setup-progress">{_t("setup.progress", n=step_num, total=total)} '
        f'&middot; {html.escape(step_names.get(step_key, ""))}</p>'
        if step_num else ""
    )
    back = (
        f'<p class="setup-back"><a href="{html.escape(back_href)}">&larr; {_t("setup.back")}</a></p>'
        if back_href else ""
    )
    err = (
        f'<p class="setup-error" role="alert">{html.escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} &middot; {_t("setup.brand")}</title>
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<style>
body{{background:#f6f7f5;margin:0;color:#20231f}}
.setup-wrap{{width:min(100% - 32px,560px);margin:6vh auto;background:#fff;
  border:1px solid #dfe3dd;border-radius:16px;padding:32px;box-sizing:border-box}}
.setup-progress{{margin:0 0 4px;font-size:13px;font-weight:650;color:#6b736a;
  text-transform:uppercase;letter-spacing:.03em}}
.setup-back{{margin:0 0 18px}}
.setup-back a{{color:#5b625b;font-size:14px;}}
.setup-wrap h1{{font-size:26px;margin:0 0 10px}}
.setup-wrap p.lede{{line-height:1.5;color:#5b625b;margin:0 0 20px}}
.setup-wrap label{{display:block;font-weight:650;margin:18px 0 6px}}
.setup-wrap .hint{{font-weight:500;color:#6b736a;font-size:13px;margin:-3px 0 6px}}
.setup-wrap input[type=text],.setup-wrap input[type=password],
.setup-wrap input[type=email],.setup-wrap select{{
  width:100%;box-sizing:border-box;font:inherit;font-size:16px;padding:12px;
  border:1px solid #b9c0b8;border-radius:8px}}
.setup-wrap .field-narrow{{width:auto;max-width:320px}}
.setup-wrap fieldset{{border:1px solid #dfe3dd;border-radius:10px;padding:12px 14px;margin:18px 0 0}}
.setup-wrap legend{{font-weight:650;padding:0 6px}}
.setup-wrap .opt{{display:flex;align-items:flex-start;gap:8px;margin:8px 0;font-weight:500}}
/* flex:0 0 auto: el checkbox no debe achicarse si la etiqueta es larga */
.setup-wrap .opt input{{margin-top:4px;flex:0 0 auto}}
.setup-wrap button{{margin-top:24px;width:100%}}
.setup-wrap .skip{{display:block;text-align:center;margin-top:12px;font-size:14px}}
.setup-error{{background:#fbeaea;border:1px solid #e3b6b6;color:#8a2d2d;
  border-radius:8px;padding:10px 12px;margin:0 0 18px;font-weight:600}}
.setup-note{{font-size:13px;color:#6b736a;margin-top:10px}}
.setup-otp{{background:#f6f7f5;border:1px dashed #b9c0b8;border-radius:8px;
  padding:12px;margin-top:14px;font-family:ui-monospace,monospace;word-break:break-all}}
</style></head><body>
<main class="setup-wrap">
{progress}{back}
<h1>{html.escape(title)}</h1>
{err}
{body_html}
</main></body></html>"""


def _parse_form_multi(raw):
    """Pure: raw x-www-form-urlencoded bytes -> dict of LISTS (for repeated
    fields like checkbox groups). Used by `setup_router.py` after
    `await request.body()`."""
    return urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)


# ============================================================== steps

def page_welcome():
    """Pure: the welcome step's full HTML shell."""
    body = f"""<p class="lede">{html.escape(_t("setup.welcome.lede"))}</p>
<a class="btn btn-accent" href="/setup/location" style="display:block;text-align:center;text-decoration:none;padding:12px">
{html.escape(_t("setup.welcome.cta"))} &rarr;</a>"""
    return _shell("welcome", 0, _t("setup.welcome.title"), body)


def page_location(values, error=None, proposed_note=""):
    """Pure: the location step's HTML shell."""
    body = f"""<p class="lede">{html.escape(_t("setup.location.lede"))}</p>
<form method="post" action="/setup/location">
<label for="su-country">{html.escape(_t("setup.location.country"))}</label>
<input id="su-country" name="country" type="text" required value="{html.escape(values.get('country', ''))}">
{proposed_note}
<label for="su-province">{html.escape(_t("setup.location.province"))}</label>
<div class="hint">{html.escape(_t("setup.location.province_hint"))}</div>
<input id="su-province" name="province" type="text" value="{html.escape(values.get('province', ''))}">
<label for="su-town">{html.escape(_t("setup.location.town"))}</label>
<input id="su-town" name="town" type="text" required value="{html.escape(values.get('town', ''))}">
<button class="btn btn-accent" type="submit">{html.escape(_t("setup.continue"))}</button>
</form>"""
    return _shell("location", 1, _t("setup.step.location"), body, error=error)


def _location_values_and_note(state, propose_country_result):
    """Pure: state dict + the `_propose_country()` result (a caller concern —
    it needs the Request to read `CF-IPCountry`/client IP, see that
    function's own docstring on why it stays duck-type-compatible instead of
    being split further) -> (values, proposed_note) for a fresh GET."""
    values = {
        "country": state.get("country", ""),
        "province": state.get("province", ""),
        "town": state.get("town", ""),
    }
    proposed_note = ""
    if not values.get("country"):
        _code, name = propose_country_result
        if name:
            values["country"] = name
            proposed_note = f'<p class="setup-note">{html.escape(_t("setup.location.proposed", country=name))}</p>'
    return values, proposed_note


def process_location(form):
    """Pure: submitted form -> ("error", message, values) or ("ok", state_updates)."""
    country = form.get("country", "").strip()
    province = form.get("province", "").strip()
    town = form.get("town", "").strip()
    if not country or not town:
        return ("error", _t("setup.location.error_required"),
                {"country": country, "province": province, "town": town})
    return ("ok", {"country": country, "province": province, "town": town})


def page_packs(state, error=None):
    """Pure: the pack-matching step's HTML shell."""
    country, province, town = state.get("country", ""), state.get("province", ""), state.get("town", "")
    prefix, town_id, parent = _propose_chain(country, province, town)
    if town_id:
        chain_preview = ", ".join(prefix + [town_id])
        body = f"""<p class="lede">{html.escape(_t("setup.packs.matched", town=town, pack=town_id))}</p>
<p class="setup-note">{html.escape(_t("setup.packs.chain_preview", chain=chain_preview))}</p>
<form method="post" action="/setup/packs">
<input type="hidden" name="mode" value="match">
<input type="hidden" name="town_id" value="{html.escape(town_id)}">
<button class="btn btn-accent" type="submit">{html.escape(_t("setup.continue"))}</button>
</form>"""
    else:
        default_name = state.get("pack_name") or town or ""
        chain_preview = ", ".join(prefix + ["<" + _slugify(default_name or "localidad") + ">"])
        body = f"""<p class="lede">{html.escape(_t("setup.packs.none_found", town=town))}</p>
<label for="su-packname">{html.escape(_t("setup.packs.name"))}</label>
<div class="hint">{html.escape(_t("setup.packs.name_hint"))}</div>
<form method="post" action="/setup/packs">
<input id="su-packname" name="pack_name" type="text" required value="{html.escape(default_name)}">
<input type="hidden" name="mode" value="scaffold">
<p class="setup-note">{html.escape(_t("setup.packs.chain_preview", chain=chain_preview))}</p>
<button class="btn btn-accent" type="submit">{html.escape(_t("setup.continue"))}</button>
</form>"""
    return _shell("packs", 2, _t("setup.step.packs"), body,
                  back_href="/setup/location", error=error)


def process_packs(form, state):
    """Pure: submitted form + current state -> ("error", message) or
    ("ok", state_updates)."""
    country, province, town = state.get("country", ""), state.get("province", ""), state.get("town", "")
    prefix, town_id, parent = _propose_chain(country, province, town)
    mode = form.get("mode", "")
    if mode == "match" and town_id:
        return ("ok", {"pack_scaffold": False, "pack_chain": prefix + [town_id]})
    pack_name = form.get("pack_name", "").strip() or town
    if not pack_name:
        return ("error", _t("setup.packs.error_name"))
    return ("ok", {"pack_scaffold": True, "pack_name": pack_name,
                   "pack_prefix": prefix, "pack_parent": parent})


def page_language(state, error=None):
    """Pure: the language step's HTML shell."""
    selected = state.get("languages") or ["es"]
    default = state.get("lang_default") or (selected[0] if selected else "es")
    opts = []
    for code in i18n.available_locales():
        name = _LANG_NAMES.get(code, code)
        checked = " checked" if code in selected else ""
        opts.append(
            f'<label class="opt"><input type="checkbox" name="languages" value="{html.escape(code)}"{checked}> '
            f'{html.escape(name)} ({html.escape(code)})</label>'
        )
    default_opts = "".join(
        f'<option value="{html.escape(c)}"{" selected" if c == default else ""}>'
        f'{html.escape(_LANG_NAMES.get(c, c))}</option>'
        for c in i18n.available_locales()
    )
    body = f"""<p class="lede">{html.escape(_t("setup.language.lede"))}</p>
<form method="post" action="/setup/language">
<fieldset><legend>{html.escape(_t("setup.language.enabled"))}</legend>
{"".join(opts)}
</fieldset>
<label for="su-langdefault">{html.escape(_t("setup.language.default"))}</label>
<select id="su-langdefault" name="lang_default">{default_opts}</select>
<button class="btn btn-accent" type="submit">{html.escape(_t("setup.continue"))}</button>
</form>"""
    return _shell("language", 3, _t("setup.step.language"), body,
                  back_href="/setup/packs", error=error)


def process_language(parsed_multi):
    """Pure: `_parse_form_multi()`'s dict-of-lists -> ("error", message) or
    ("ok", state_updates)."""
    languages = [c for c in parsed_multi.get("languages", []) if c in i18n.available_locales()]
    lang_default = (parsed_multi.get("lang_default") or [""])[0]
    if not languages:
        return ("error", _t("setup.language.error_none"))
    if lang_default not in languages:
        lang_default = languages[0]
    return ("ok", {
        "languages": languages,
        "lang_default": lang_default,
        "lang_switcher_mode": "nav" if len(languages) > 1 else "off",
    })


def page_admin(values=None, error=None):
    """Pure: the admin-account step's HTML shell."""
    values = values or {}
    body = f"""<p class="lede">{html.escape(_t("setup.admin.lede"))}</p>
<form method="post" action="/setup/admin">
<label for="su-user">{html.escape(_t("setup.admin.username"))}</label>
<div class="hint">{html.escape(_t("setup.admin.username_hint"))}</div>
<input id="su-user" name="username" type="text" required autocomplete="username"
 class="field-narrow" value="{html.escape(values.get('username', ''))}">
<label for="su-pass">{html.escape(_t("setup.admin.password"))}</label>
<input id="su-pass" name="password" type="password" minlength="8" required autocomplete="new-password">
<label for="su-pass2">{html.escape(_t("setup.admin.password_confirm"))}</label>
<input id="su-pass2" name="password2" type="password" minlength="8" required autocomplete="new-password">
<label for="su-email">{html.escape(_t("setup.admin.email"))}</label>
<div class="hint">{html.escape(_t("setup.admin.email_hint"))}</div>
<input id="su-email" name="email" type="email" autocomplete="email" value="{html.escape(values.get('email', ''))}">
<fieldset><legend>{html.escape(_t("setup.admin.totp_legend"))}</legend>
<label class="opt"><input type="radio" name="totp" value="later" checked>
{html.escape(_t("setup.admin.totp_later"))}</label>
<label class="opt"><input type="radio" name="totp" value="now">
{html.escape(_t("setup.admin.totp_now"))}</label>
</fieldset>
<button class="btn btn-accent" type="submit">{html.escape(_t("setup.continue"))}</button>
</form>"""
    return _shell("admin", 4, _t("setup.step.admin"), body,
                  back_href="/setup/language", error=error)


def process_admin(form):
    """Pure w.r.t. transport (touches only `db`, never an HTTP response):
    submitted form -> ("error", message, values) or ("ok", state_updates)."""
    username = form.get("username", "").strip()
    password = form.get("password", "")
    password2 = form.get("password2", "")
    email = form.get("email", "").strip()
    totp = form.get("totp", "later")
    if not usernames.valid(username):
        return ("error", _t("setup.admin.error_username"), form)
    if len(password) < 8:
        return ("error", _t("setup.admin.error_password"), form)
    if password != password2:
        return ("error", _t("setup.admin.error_match"), form)
    if email and not _EMAIL_RE.match(email):
        return ("error", _t("setup.admin.error_email"), form)
    try:
        if db_operators.operator_get(username):
            return ("error", _t("setup.admin.error_taken"), form)
    except db.DbUnavailable:
        return ("error", _t("setup.error_db"), form)
    # Enrollment is owned by Account, which proves the six-digit code before
    # persisting a secret. The installer only chooses whether that verified
    # flow is mandatory on first sign-in; it never activates an unconfirmed
    # secret or renders a second, weaker setup surface of its own.
    updates = {
        "admin_username": username,
        "admin_pass_hash": password_hashing.hash_password(password),
        "admin_email": email or None,
        "admin_totp_required": totp == "now",
    }
    return ("ok", updates)


def page_backups(state, error=None, reveal=None, probed=False):
    from app.core import backup_setup, backup_settings
    public = backup_setup.public(state.get("backup_stage"))
    def tr(key, fallback):
        return html.escape(i18n.t_or("backup." + key, fallback, i18n.active()))
    fields = []
    for key in ("TURNOS_BACKUP_S3_ENDPOINT", "TURNOS_BACKUP_S3_BUCKET", "TURNOS_BACKUP_S3_KEY_ID",
                "TURNOS_BACKUP_S3_SECRET", "TURNOS_BACKUP_S3_PREFIX", "TURNOS_BACKUP_S3_REGION"):
        default = backup_settings.DEFAULTS[key]
        label = tr("field." + key.lower(), backup_settings.FIELD_LABELS[key][1])
        secret = key in backup_settings.PROTECTED
        value = "" if secret else public.get("values", {}).get(key, default)
        fields.append(f'<label for="{key}">{label}</label><input id="{key}" name="{key}" type="{"password" if secret else "text"}" value="{html.escape(value, quote=True)}" autocomplete="off">')
    key_block = ""
    if reveal:
        key_block = f'<div data-recovery-panel><label for="backup-recovery">{tr("save_key", "Guardá esta clave fuera del servidor")}</label><textarea id="backup-recovery" data-recovery-key readonly style="width:100%;box-sizing:border-box;font:inherit;overflow-wrap:anywhere" rows="4">{html.escape(reveal["recovery_key"])}</textarea><button type="button" data-recovery-copy>{tr("copy", "Copiar")}</button><button type="button" data-recovery-download>{tr("download", "Descargar clave")}</button></div>'
    elif public.get("fingerprint"):
        key_block = '<p>' + tr("fingerprint", "Huella de la clave") + ': ' + html.escape(public["fingerprint"]) + '</p>'
    else:
        key_block = '<button type="submit" name="action" value="generate">' + tr("generate", "Generar clave de recuperación") + '</button>'
    confirmed = ('<label class="opt"><input type="checkbox" name="key_saved" value="1"> ' +
                 tr("saved_outside", "Guardé la clave fuera de este servidor") + '</label>') if public.get("fingerprint") else ""
    notice = '<p role="status">' + tr("probe_ok", "Conexión comprobada") + '</p>' if probed else ""
    body = f'<form method="post" action="/setup/backups">{"".join(fields)}{key_block}{notice}<button type="submit" name="action" value="probe">{tr("probe", "Probar conexión")}</button>{confirmed}<button class="btn btn-accent" type="submit" name="action" value="continue">{tr("continue", "Guardar destino")}</button><button type="submit" name="action" value="skip">{tr("skip", "Sin copias externas por ahora")}</button></form><script type="module" src="{static_fingerprint.url("platform/backup-key.mjs")}"></script>'
    return _shell("backups", 5, tr("title", "Copias de seguridad"), body, error=error)


def page_apikeys(error=None):
    """Pure: the API-keys step's HTML shell."""
    body = f"""<p class="lede">{html.escape(_t("setup.apikeys.lede"))}</p>
<form method="post" action="/setup/apikeys">
<label for="su-orkey">{html.escape(_t("setup.apikeys.openrouter"))}</label>
<div class="hint">{html.escape(_t("setup.apikeys.openrouter_hint"))}</div>
<input id="su-orkey" name="openrouter_key" type="text" autocomplete="off">
<button class="btn btn-accent" type="submit">{html.escape(_t("setup.apikeys.finish"))}</button>
</form>
<p class="setup-note">{html.escape(_t("setup.apikeys.skip_note"))}</p>"""
    return _shell("apikeys", 6, _t("setup.step.apikeys"), body,
                  back_href="/setup/backups", error=error)


def page_done(username, totp_required):
    """Pure: the final "done" shell — when the installer made 2FA mandatory,
    it points the first sign-in at the verified Account enrollment instead
    of showing a raw secret (enrollment proves the six-digit code before
    persisting anything)."""
    otp_block = (
        f'<p class="setup-note">{html.escape(_t("setup.done.totp_required"))}</p>'
        if totp_required else ""
    )
    cta_href = "/admin/account?setup=2fa" if totp_required else "/admin"
    backup_warning = "" if config.get("TURNOS_BACKUP_ENABLED") == "1" else (
        '<p role="status">' + html.escape(i18n.t_or("backup.disabled_warning", "Sin copias externas configuradas", i18n.active())) + '</p>')
    body = f"""{backup_warning}<p class="lede">{html.escape(_t("setup.done.lede", user=username))}</p>
{otp_block}
<a class="btn btn-accent" href="{cta_href}" style="display:block;text-align:center;text-decoration:none;padding:12px">
{html.escape(_t("setup.done.cta"))} &rarr;</a>
<p class="setup-note">{html.escape(_t("setup.done.settings_note"))}</p>"""
    return _shell("done", 0, _t("setup.step.done"), body)


def process_apikeys(form, state):
    """The atomic commit — touches `db`/`config`/`data_packs`/`theme`, never
    an HTTP response, so this stays transport-neutral even though it isn't
    a pure function (real side effects, by design — see the module
    docstring's "The final POST" section). Returns ("error", message) or
    ("ok", username, totp_required); the caller renders `page_done()` and
    clears the wizard cookie."""
    openrouter_key = form.get("openrouter_key", "").strip()
    from app.core import backup_setup, managed_secrets
    backup_values, backup_secrets = {"TURNOS_BACKUP_ENABLED": "0"}, {}
    if state.get("backup_stage"):
        try:
            backup_values, backup_secrets = backup_setup.commit(state["backup_stage"])
        except ValueError:
            return ("error", i18n.t_or("backup.expired", "Volvé a Copias de seguridad y completá el destino", i18n.active()))

    username = state.get("admin_username")
    pass_hash = state.get("admin_pass_hash")
    if not username or not pass_hash:
        return ("error", _t("setup.apikeys.error_missing_admin"))

    try:
        if db_operators.operator_get(username):
            return ("error", _t("setup.admin.error_taken"))
    except db.DbUnavailable:
        return ("error", _t("setup.error_db"))

    # 1) Pack chain — scaffold a new town pack if the deployer asked for one.
    scaffolded = bool(state.get("pack_scaffold"))
    pack_chain = list(state.get("pack_chain") or [])
    if scaffolded:
        try:
            slug = _scaffold_pack(state.get("pack_name") or state.get("town") or "localidad",
                                  state.get("province"), state.get("pack_parent"))
        except OSError as exc:
            return ("error", _t("setup.apikeys.error_scaffold", detail=str(exc)))
        pack_chain = list(state.get("pack_prefix") or []) + [slug]
        # The scaffolded town IS this installation's own clinic data. Three
        # boot-time properties move TOGETHER, all read from process/.env only
        # (never the DB — see core.settings.InstallationSettings and the required-pack
        # readiness solution note), so they go through config.save_env_keys,
        # not config.guardar:
        #   * TURNOS_PRIVATE_PACKS_DIR / TURNOS_REQUIRED_PACKS — the resolver
        #     finds the town on the volume and the boot gate validates it;
        #   * PACK_CHAIN — the chain that NAMES that required town, so a
        #     locked-config restart (TURNOS_CONFIG_LOCK=1 — DB overrides
        #     ignored) still SERVES the town instead of falling through to the
        #     bundled public default. Without this, the gate would validate a
        #     private pack the chain never serves (validate-private,
        #     serve-public) — the configuration-ownership mismatch this closes.
        # The same chain is also written to the DB below for normal-operation
        # admin edits; under the lock the boot-key copy wins. Idempotent on a
        # re-run; a non-writable .env surfaces a clear error before any
        # operator or config row is committed.
        if not config.save_env_keys({
            "TURNOS_PRIVATE_PACKS_DIR": data_packs.install_packs_dir(),
            "TURNOS_REQUIRED_PACKS": slug,
            "PACK_CHAIN": ",".join(pack_chain),
        }):
            return ("error", _t("setup.apikeys.error_env_write"))
    if not pack_chain:
        pack_chain = ["global"] if any(p.get("id") == "global" for p in data_packs.list_packs()) else []

    # 2) Config: location + languages + (optional) OpenRouter key — one
    # partial-merge write, same as settings_admin.py's own _guardar_* helpers.
    overrides = dict(backup_values)
    # New installations do not start the legacy plaintext producer. Existing
    # installations' B2 behavior and code are unchanged.
    overrides["TURNOS_BACKUP_OFF"] = "1"
    if pack_chain:
        overrides["PACK_CHAIN"] = ",".join(pack_chain)
    if state.get("languages"):
        overrides["LANGUAGES"] = ",".join(state["languages"])
    if state.get("lang_default"):
        overrides["LANG_DEFAULT"] = state["lang_default"]
    if state.get("lang_switcher_mode"):
        overrides["LANG_SWITCHER_MODE"] = state["lang_switcher_mode"]
    if openrouter_key:
        overrides["OPENROUTER_API_KEY"] = openrouter_key
    try:
        if backup_secrets:
            managed_secrets.save_installation_backup_values(backup_secrets)
        # Generate TURNOS_ADMIN_SECRET if not already set (same pattern as
        # TURNOS_SETUP_SECRET). This ensures the admin session cookie signing
        # key is never the fixed literal that anyone can compute.
        if not os.environ.get("TURNOS_ADMIN_SECRET") and not os.environ.get("TURNOS_ADMIN_PASS"):
            overrides["TURNOS_ADMIN_SECRET"] = secrets.token_hex(32)
        if overrides:
            config.guardar(overrides)
        data_packs.invalidate()

        # 3) The operator — same INSERT admin_users.create_user() drives, just
        # with a pre-hashed password (see module docstring).
        # Treat a pre-upgrade in-progress cookie carrying a generated secret
        # as a request for first-sign-in enrollment, but never activate that
        # unconfirmed secret. Normal cookies use the boolean field.
        totp_required = bool(
            state.get("admin_totp_required") or state.get("admin_totp_secret")
        )
        totp_secret = None
        db_operators.operator_create(
            username, rol="admin", totp_secret=totp_secret,
            email=state.get("admin_email"), pass_hash=pass_hash,
        )
        db_auth.admin_security_set_deadline(
            username,
            datetime.now(timezone.utc) if totp_required
            else datetime.now(timezone.utc) + timedelta(days=7),
        )
    except db.DbUnavailable:
        return ("error", _t("setup.error_db"))
    except Exception as exc:  # noqa: BLE001 — a UNIQUE-race is the only realistic case left
        return ("error", _t("setup.apikeys.error_generic", detail=str(exc)[:200]))

    # Persist the neutral engine theme for a newly scaffolded town. This is
    # explicit even though it matches theme.py's fresh-checkout default: an
    # installation must keep its chosen identity if a future engine release
    # changes that default. A matched pre-existing pack remains operator-owned.
    # Best-effort only: the operator account already exists at this point.
    if scaffolded:
        try:
            from app.core import theme
            theme.save("humanrounds")
        except Exception:  # noqa: BLE001
            pass

    mark_configured()
    backup_setup.discard(state.get("backup_stage"))
    return ("ok", username, totp_required)
