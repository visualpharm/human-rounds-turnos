"""Public sandbox mode for the real Pinamar Turnos installation.

The demo is the production application with a fictional data package and its
own database.  It is deliberately opt-in: none of these routes or presentation
changes exist unless TURNOS_DEMO_MODE=1.

Safety defaults are applied before the rest of the route registry imports its
feature modules.  A deployment can add stricter values, but demo mode never
enables integrations that could touch a real health system or contact a real
person.
"""

import html as _html
import os
import re
import json

from app.core.status_error import BodyStatusError


_TRUE = {"1", "true", "yes", "on"}
_DEMO_USER = "demo"
_DEMO_DOCTOR_USER = "gonzalez"
_DEMO_PATIENT_DNI = "99000001"

_LEGACY_PAGES = {"/app", "/app.html", "/panel", "/panel.html"}
_LEGACY_API = {
    "GET": {
        "/api/whoami",
        "/api/availability",
        "/api/appointments",
        "/api/watch",
        "/api/watch/state",
        "/api/calendar/status",
        "/api/confirmation",
        "/api/logs",
        "/api/vault",
    },
    "POST": {
        "/api/login",
        "/api/refresh",
        "/api/book",
        "/api/cancel",
        "/api/watch",
        "/api/calendar",
        "/api/vault",
    },
    "DELETE": {"/api/vault"},
}

_SAFETY_DEFAULTS = {
    "TURNOS_SKIP_HSI": "1",
    # Anyone may try the sign-up on the demo: no desk checks the document behind a number there
    # (TASK-68); every other installation keeps the check on.
    "TURNOS_DNI_CLAIMS": "0",
    "TURNOS_HSI_SYNC_OFF": "1",
    "TURNOS_HSI_RESYNC_OFF": "1",
    "TURNOS_REMINDERS_OFF": "1",
    "TURNOS_WAITLIST_OFF": "1",
    "TURNOS_BACKUP_OFF": "1",
    "TURNOS_AI_HEALTH_OFF": "1",
    "TURNOS_FAKE_AI": "1",
    # The public demo must demonstrate the real conversational assistant.
    # Keep document/OCR/intake AI deterministic through TURNOS_FAKE_AI, but
    # let chat_agent use its own real-model switch.  This avoids sending a
    # visitor's uploaded document to a provider while still making the demo
    # conversation and catalogue tools genuine.
    "TURNOS_FAKE_CHAT_AI": "0",
    # Same carve-out for catalogue translation: it only ever sends the
    # installation's own published service names and center descriptions, so
    # there is no visitor data to protect and a faked translation would show
    # every English visitor "[en] Clínica médica".
    "TURNOS_FAKE_TRANSLATE": "0",
    "TURNOS_FAKE_PUCO": "1",
    "TURNOS_FAKE_ALEPO": "1",
    "TURNOS_FAKE_MARQUI": "1",
    "TURNOS_PUCO_PUBLIC": "0",
    "MAIL_ENABLED": "0",
}

# Patient WhatsApp notices (confirmation + day-before reminder) are the one
# outbound channel the demo may use, and only towards the deployment's own
# test phones (`otp_send.notificacion_permitida`). Both keys are env-only
# here: the demo admin is public, and a visitor saving another phone into
# Settings must never widen who receives a message.
_ENV_ONLY = ("TURNOS_WA_NOTIFY", "TURNOS_WA_TEST_RECIPIENTS")

_DEFAULT_PUBLIC_URL = "https://humanrounds.org/"

_STYLE = """
<style id="demo-installation-style">
.demo-installation{background:color-mix(in srgb,var(--accent) 8%,var(--panel));
  color:var(--text-2);font:500 15px/1.35 var(--font,system-ui,sans-serif)}
.demo-installation__inner{max-width:1120px;margin:0 auto;padding:9px 24px;
  display:grid;grid-template-columns:minmax(190px,1fr) auto auto;
  align-items:center;gap:12px 24px}
.demo-installation__notice{margin:0;min-width:0}
.demo-installation__links,.demo-installation__lang{display:flex;align-items:center}
.demo-installation__links{gap:18px}
.demo-installation__lang{gap:9px;font-size:15px}
.demo-installation strong{color:var(--text);font-weight:700}
/* Los links de la banda siguen al tema y NO se ponen en negrita: heredan el
   peso del texto de la banda (responsable del producto, 2026-09-14 — "if it's in regular text,
   regular"), y el subrayado tenue lo pinta el tema, así que acá no se apaga. */
.demo-installation a{color:var(--link,var(--accent));
  white-space:nowrap;display:inline-flex;align-items:center;min-height:24px}
.demo-installation a:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.demo-installation__lang select{font:500 15px/1.3 var(--font,system-ui,sans-serif);
  color:var(--link,var(--accent));background:transparent;border:0;padding:2px 0;min-height:24px;
  cursor:pointer;max-width:9em}
.demo-installation__lang select:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.demo-installation~#app #adm-menu-toggle{
  top:calc(var(--demo-installation-height,0px) + 12px)}
"""

# Phone layout of the PUBLIC strip (demo review 2026-11-15): one line — the
# lead and the three links in a row, the detail sentence dropped (the lead
# «Demo.» already says it), each link a 44px tap target. When it does not fit
# (320px, long translations) the links wrap as one block, never one by one.
_PHONE_PUBLIC = """
@media(max-width:760px){
  .demo-installation__inner{display:flex;flex-wrap:wrap;align-items:center;
    padding:0 14px;gap:0 16px}
  .demo-installation__detail{display:none}
  .demo-installation__links{gap:0 16px;flex-wrap:wrap}
  .demo-installation a{min-height:44px}
}
</style>
"""

# Phone layout of the admin strip: unchanged (the panel is out of the review).
_PHONE_ADMIN = """
@media(max-width:760px){
  .demo-installation__inner{grid-template-columns:minmax(0,1fr) auto;gap:8px 16px}
  .demo-installation__links{grid-column:1/-1;display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;text-align:center}
  /* El texto de cada link se centra en su columna y, si no cabe, baja de línea:
     antes quedaba pegado a la izquierda de la celda y «Consola médica» (nowrap,
     más ancha que un tercio de 360px) tocaba a «Gestión» («Consola médicaGestión»,
     estudio de usabilidad 2026-10-16). Entre textos vecinos hay siempre el hueco. */
  .demo-installation__links a{justify-content:center;white-space:normal;text-align:center}
  .demo-installation__lang{grid-column:2;grid-row:1;justify-self:end;width:auto}
}
@media(max-width:420px){
  .demo-installation__inner{padding:8px 14px;gap:7px 12px}
  .demo-installation__links{gap:6px;font-size:15px}
  .demo-installation__notice{font-size:15px}
}
</style>
"""


def enabled():
    return str(os.environ.get("TURNOS_DEMO_MODE") or "").strip().lower() in _TRUE


def demo_user():
    return (os.environ.get("TURNOS_DEMO_ADMIN_USER") or _DEMO_USER).strip() or _DEMO_USER


def demo_doctor_user():
    return _DEMO_DOCTOR_USER


def _wa_notices_to_test_phones():
    """True when the deployment turned on patient WhatsApp notices AND named
    its test phones — the only case the demo runs its reminder sweep. Its
    email half stays silent (MAIL_ENABLED=0) and every synthetic patient's
    phone is outside the list, so only the test phones receive anything."""
    on = str(os.environ.get("TURNOS_WA_NOTIFY") or "").strip().lower() in _TRUE
    return on and bool(str(os.environ.get("TURNOS_WA_TEST_RECIPIENTS") or "").strip())


def _safety_values():
    values = dict(_SAFETY_DEFAULTS)
    if _wa_notices_to_test_phones():
        values["TURNOS_REMINDERS_OFF"] = "0"
    return values


def apply_safety_defaults():
    """Force sandbox-safe values before feature modules read their config."""
    if not enabled():
        return
    for key, value in _safety_values().items():
        os.environ[key] = value


def locked_value(key):
    """Return the non-overridable sandbox value for a protected setting."""
    if not enabled():
        return None
    if key in _ENV_ONLY:
        return str(os.environ.get(key) or "").strip()
    return _safety_values().get(key)


def _public_url():
    return (os.environ.get("TURNOS_DEMO_PUBLIC_URL") or "").strip() or _DEFAULT_PUBLIC_URL


def _lang_switch_links(lang):
    """`?lang=` pair for the banner itself — reuses i18n's own switch
    mechanism (i18n.py:_maybe_switch_lang), so this needs no new plumbing:
    it's a no-op unless an admin actually enabled 2+ languages + a switcher
    mode. Demo mode ships both on by default (see seed_demo_all.py)."""
    from app.core import i18n  # deferred: config.py imports demo_installation, i18n imports
    # config — importing i18n at module level here would cycle back.

    enabled = i18n.enabled_languages()
    if i18n.switcher_mode() == "off" or len(enabled) < 2:
        return ""
    # One <select> of every enabled language, each option in its own name
    # (2026-09-11, ten languages: a row of two-letter codes stopped fitting
    # next to the three demo links on a phone, and "IT"/"UK" mean nothing to
    # the person who needs them). Same control humanrounds.org uses.
    items = []
    for code in enabled:
        selected = " selected" if code == lang else ""
        items.append(f'<option value="{code}" lang="{code}"{selected}>'
                     f'{_html.escape(i18n.language_name(code))}</option>')
    label = i18n.t("demo.banner.language_label", lang)
    return ('<nav class="demo-installation__lang" aria-label="' + label + '">'
            '<select data-demo-lang-select aria-label="' + label + '">'
            + "".join(items) + "</select></nav>")


def _banner(surface, lang, switchable=False):
    """Same links on every surface (public or admin) — only the lead-in
    sentence changes. No credentials shown anywhere: the admin entry is the
    one-click passwordless flow, never a printed user/pass.

    The banner NEVER resolves its own language: it renders the one the page
    around it actually rendered in. It used to call `i18n.resolve_lang`
    itself, which is how an English banner ended up glued on top of the
    all-Spanish admin panel (the product owner, 2026-08-01) — the panel has no i18n, so
    it is always Spanish, while the banner happily followed the browser's
    Accept-Language. Same reason `switchable` is explicit: offering a
    language switcher on a surface that cannot switch is a lie."""
    from app.core import i18n  # deferred, see _lang_switch_links

    # A public page's header already draws the language switch in mode «nav»
    # (nav.mjs langSwitchHtml): a second one in the strip above it was two
    # controls for one setting (demo review 2026-11-15). The admin panel has
    # no header switch, so its strip keeps its own.
    header_switch = surface != "admin" and i18n.switcher_mode() == "nav"
    key = "demo.banner.admin" if surface == "admin" else "demo.banner.public"
    copy_text = i18n.t(key, lang)
    lead, separator, detail = copy_text.partition(". ")
    copy = ('<strong class="demo-installation__lead">' + lead
            + ("." if separator else "") + '</strong>'
            + (' <span class="demo-installation__detail">' + detail + '</span>'
               if detail else ""))
    links = (
        '<div class="demo-installation__links">'
        f'<a href="/account/demo-access" data-demo-destination="patient">{i18n.t("demo.banner.link_patient_demo", lang)}</a>'
        f'<a href="/admin/demo-doctor-access" data-demo-destination="doctor">{i18n.t("demo.banner.link_doctor_demo", lang)}</a>'
        f'<a href="/admin/demo-access" data-demo-destination="admin">{i18n.t("demo.banner.link_admin", lang)}</a>'
        "</div>"
    )
    translations = {}
    for code in i18n.enabled_languages():
        translations[code] = {
            "notice": i18n.t(key, code),
            "aria": i18n.t("demo.banner.aria", code),
            "language": i18n.t("demo.banner.language_label", code),
            "patient": i18n.t("demo.banner.link_patient_demo", code),
            "doctor": i18n.t("demo.banner.link_doctor_demo", code),
            "admin": i18n.t("demo.banner.link_admin", code),
        }
    translations_json = json.dumps(
        translations, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    bar = (
        _STYLE
        + (_PHONE_ADMIN if surface == "admin" else _PHONE_PUBLIC)
        + '<aside class="demo-installation" aria-label="'
        + i18n.t("demo.banner.aria", lang)
        + '" data-demo-surface="' + surface + '" lang="'
        + lang
        + '"><div class="demo-installation__inner"><p class="demo-installation__notice">'
        + copy
        + "</p>"
        + links
        + (_lang_switch_links(lang) if switchable and not header_switch else "")
        + "</div></aside>"
    )
    # The admin hamburger is fixed to the viewport. Track the injected bar's
    # actual height so it starts below the bar at every translated/mobile
    # wrapping state instead of covering the first demo link.
    return bar + """<script id="demo-installation-layout">
(function(){
  var bar=document.querySelector('.demo-installation');
  if(!bar)return;
  var translations=""" + translations_json + """;
  function applyAdminLanguage(code){
    var tr=translations[code];
    if(!tr)return;
    var parts=tr.notice.split('. '),lead=bar.querySelector('.demo-installation__lead');
    var detail=bar.querySelector('.demo-installation__detail');
    if(lead)lead.textContent=parts.shift()+(parts.length?'.':'');
    if(detail)detail.textContent=parts.join('. ');
    bar.lang=code;bar.setAttribute('aria-label',tr.aria);
    var langNav=bar.querySelector('.demo-installation__lang');
    if(langNav)langNav.setAttribute('aria-label',tr.language);
    var sel=bar.querySelector('[data-demo-lang-select]');
    if(sel){sel.setAttribute('aria-label',tr.language);sel.value=code;}
    bar.querySelectorAll('[data-demo-destination]').forEach(function(link){
      link.textContent=tr[link.dataset.demoDestination];
    });
  }
  var select=bar.querySelector('[data-demo-lang-select]');
  if(select)select.addEventListener('change',function(){
    var code=select.value;
    if(bar.dataset.demoSurface==='admin'){
      document.cookie='lang='+encodeURIComponent(code)
        +'; Max-Age=31536000; Path=/; SameSite=Lax';
      applyAdminLanguage(code);
      return;
    }
    location.assign(location.pathname+'?lang='+encodeURIComponent(code)+location.hash);
  });
  function sync(){document.documentElement.style.setProperty(
    '--demo-installation-height',bar.offsetHeight+'px');}
  sync();
  if(window.ResizeObserver)new ResizeObserver(sync).observe(bar);
  else window.addEventListener('resize',sync);
})();
</script>"""


def inject_html(document, surface="public", lang="es", switchable=False):
    """Insert the sandbox notice immediately after <body>.

    `lang` is the language the CALLER already rendered the page in — the
    banner does not guess. `switchable` says whether that page can actually
    change language; only then does the switcher appear.

    Accepts str or bytes because the public shell is built as text while the
    existing admin template is read as bytes.
    """
    if not enabled():
        return document
    was_bytes = isinstance(document, (bytes, bytearray))
    text = bytes(document).decode("utf-8") if was_bytes else str(document)
    if 'class="demo-installation"' in text:
        return document
    rendered = re.sub(
        r"(<body(?:\s[^>]*)?>)",
        lambda m: m.group(1) + "\n" + _banner(surface, lang, switchable),
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    rendered = _noindex(rendered)
    return rendered.encode("utf-8") if was_bytes else rendered


_ROBOTS_META = re.compile(r'<meta\s+name="robots"\s+content="[^"]*"\s*/?>', re.IGNORECASE)
_NOINDEX_META = '<meta name="robots" content="noindex,follow">'


def _noindex(text):
    """The demo is a sandbox of fictional data on its own host: it must never
    compete in search with the real installation it imitates (SEO audit
    2026-10-01 — demo.humanrounds.org was served `index,follow`). Every page
    this banner reaches swaps its robots directive for `noindex,follow`
    (links still followed, so the crawler keeps reaching humanrounds.org);
    a page without one gets it right after <head>. robots.txt stays open on
    purpose — a crawler blocked there never sees the noindex."""
    out, n = _ROBOTS_META.subn(_NOINDEX_META, text, count=1)
    if n:
        return out
    return re.sub(r"(<head(?:\s[^>]*)?>)", lambda m: m.group(1) + _NOINDEX_META,
                  text, count=1, flags=re.IGNORECASE)


def robots_txt(text):
    """robots.txt on the demo: same rules, minus the `Sitemap:` line — the
    demo's sitemap is empty on purpose (sitemap.py), and pointing crawlers
    at a list of noindex URLs is what Search Console reports as an error.
    Unchanged outside demo mode."""
    if not enabled():
        return text
    return "".join(line for line in text.splitlines(keepends=True)
                   if not line.lstrip().lower().startswith("sitemap:"))


class NoindexHeaderApp:
    """ASGI wrapper that adds `X-Robots-Tag: noindex` to every demo response
    — sitemap, robots.txt, JSON, images and PDFs included, which the
    `<meta name="robots">` of `_noindex` never reaches. asgi_main wraps the
    app with it only in demo mode."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"x-robots-tag"]
                headers.append((b"x-robots-tag", b"noindex"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)


def _blocks_legacy(path, method):
    """Keep inherited WebTurno credentials and mutations outside the demo."""
    if path in _LEGACY_PAGES:
        return True
    if path in _LEGACY_API.get(method, set()):
        return True
    return path == "/api/owner/coverage" or path.startswith("/api/owner/coverage/")


class DemoAccessError(BodyStatusError):
    """Carries `(status, body)` from the domain layer to the native router."""


# ---- domain functions consumed by the native router -----------------------

def demo_metadata():
    return {
        "demo": True,
        "data": "ficticios",
        "admin_url": "/admin/demo-access",
        "doctor_url": "/admin/demo-doctor-access",
        "patient_url": "/account/demo-access",
    }


def patient_access_state():
    """(user, token) for the demo patient session. Raises `DemoAccessError
    (503, ...)` when the demo patient hasn't been seeded."""
    from app.core import db_accounts
    user = db_accounts.get_user(_DEMO_PATIENT_DNI)
    if not user:
        raise DemoAccessError(503, {"error": "demo_patient_not_seeded"})
    token = db_accounts.create_session(_DEMO_PATIENT_DNI)
    return user, token


def doctor_access_identity():
    """(usuario, rol) for the demo doctor login. Raises `DemoAccessError
    (503, ...)` when the demo doctor hasn't been seeded as a 'medico'."""
    from app.core import db_operators
    operador = db_operators.operator_get(_DEMO_DOCTOR_USER)
    if not operador or operador.get("rol") != "medico":
        raise DemoAccessError(503, {"error": "demo_doctor_not_seeded"})
    return _DEMO_DOCTOR_USER, "medico"
