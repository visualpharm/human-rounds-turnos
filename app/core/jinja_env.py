"""Shared Jinja environment for public, account, intake, and admin pages.

The four public identity pages are canonical under ``templates/`` and both
HTTP transports reach them through :func:`site_identity.render_page`. The
globals preserve the established contracts instead of duplicating them:
``t`` delegates to ``i18n.t_or``, ``sitio`` to ``site_identity.get``,
``legal_doc`` to ``legal.body_html``, and ``static`` to the fingerprint URL
resolver.

Translated and legal fragments may themselves contain ``{{sitio.*}}``.
Those globals resolve the nested identity token before returning so a
single Jinja pass remains byte-compatible with the former two-pass renderer.

Autoescape is ON for every template (2026-09-27, cierre del XSS almacenado
en /admin/cola — auditoría de seguridad): a context value interpolated with
``{{ ... }}`` is HTML-escaped by Jinja itself, so a patient-controlled string
can never reach the admin panel (or any page) as live markup. The trusted
globals that pre-escape or author their own markup (``t``, ``sitio``,
``legal_doc``) and the script-payload globals (``static_manifest``,
``country_phone_json``) return ``markupsafe.Markup``, which autoescape
passes through untouched — one escape, same entity spelling
(``html.escape(..., quote=True)``) as the former hand-escaping convention,
so the rendered bytes of every pre-existing page are unchanged.

The loader accepts both ``templates/`` and repository-relative module
templates. See ``docs/architecture/i18n.md`` for the full contract.
"""

import html as _html
import os

import jinja2
from markupsafe import Markup

from app.auth import admin_auth
from app.core import format as display_format
from app.core import paths
from app.core import i18n
from app.patient_web import legal
from app.core import site_identity
from app.core import static_fingerprint

_DIR = paths.PROJECT_ROOT

# Lista, no string: ver el docstring de arriba, "Dónde viven las plantillas".
#
# Tier 5 slice 4 (docs/roadmap/tier5-frontend-design.md, "admin.html
# decomposition into /admin/<screen>"): la promesa de arriba ("un módulo
# podrá agregar el suyo a esa misma lista") se resuelve agregando la RAÍZ
# del repo como segundo directorio de búsqueda, en vez de una registración
# por-módulo — el propio ejemplo del diseño nombra la plantilla de un
# módulo con su path COMPLETO relativo al repo
# ("modules/intake/admin_entrevistas.html"), no un nombre corto que
# necesite que ese módulo primero anuncie su propio subdirectorio.
# `FileSystemLoader` prueba cada directorio en orden con el `name` pedido
# tal cual (nunca lo recorta), así que "templates/" sigue resolviendo
# "index.html" como siempre y la raíz del repo resuelve
# "modules/intake/admin_entrevistas.html" sin que este archivo tenga que
# cambiar de forma cuando un segundo módulo migre su propia pantalla —
# cero edición central, mismo contrato que `router_attr`/`screen` ya
# tienen en app_modules.py. `templates/admin/base.html` (el layout común)
# vive bajo el primer directorio, así que el `{% extends %}` de una
# plantilla de módulo también resuelve sin cambios acá.
#
# A module's own templates ("modules/intake/...") are named relative to the
# application code root, CODE_ROOT; a name such as "templates/admin/x.html"
# stays relative to the project root. While the packages sit at the root the
# two are the same directory, listed once.
TEMPLATE_DIRS = list(dict.fromkeys([os.path.join(_DIR, "templates"), paths.CODE_ROOT, _DIR]))


def _t(key, default, **variables):
    """`{{ t('about.hero.intro', 'Acerca de') }}` — gemelo Jinja de
    `i18n.t_or()`. Escapa igual que `i18n.render()`'s `_sub()` (un
    `html.escape(str(valor), quote=True)` sobre el string ENTERO resuelto,
    ANTES de que un `{{sitio.*}}` que ese mismo string traiga adentro se
    resuelva) y después corre `site_identity.render()` sobre el resultado
    para resolver justamente ese `{{sitio.*}}` incrustado — mismo orden,
    mismo resultado byte a byte, que el pipeline legacy de dos pasadas
    (`i18n.render()` seguido de `site_identity.render()` sobre el documento
    entero) produce para ESTE fragmento puntual. Ver el docstring del
    módulo para por qué esto tiene que pasar por valor y no se puede dejar
    para una segunda pasada global sobre toda la plantilla ya renderizada."""
    lang = i18n.active()
    text = i18n.t_or(key, default, lang, **variables)
    escaped = _html.escape(str(text), quote=True)
    # Markup: ya escapado exactamente una vez — ver el docstring del módulo
    # (autoescape lo deja pasar tal cual, sin una segunda pasada).
    return Markup(site_identity.render(escaped))


def _sitio(key):
    """`{{ sitio('hospital') }}` — gemelo Jinja de un `{{sitio.<key>}}`
    puntual, autorado directo en la plantilla (no incrustado en un valor
    traducido — ese caso lo cubre `_t()`/`_legal_doc()` arriba). Misma
    excepción que `site_identity.render()`'s `_sub()`: una clave que
    termina en `_html` es contenido autorado por el paquete de datos
    (markup de confianza, ej. `network_links_html`) y se devuelve CRUDA,
    sin escapar — todo lo demás pasa por `html.escape(quote=True)`."""
    val = str(site_identity.get(key))
    if key.rsplit(".", 1)[-1].endswith("_html"):
        return Markup(val)
    return Markup(_html.escape(val, quote=True))


def _legal_doc(doc_id):
    """`{{ legal_doc('terminos') }}` — gemelo Jinja de `{{legal.terminos}}`.
    `legal.body_html()` ya devuelve HTML de confianza (viene de
    `markdown_html()`, que escapa el texto del hospital adentro) con un
    `{{sitio.brand}}` posiblemente incrustado (el nombre de la instalación
    NUNCA se escribe literal en el cuerpo legal, ver legal.py) — mismo
    motivo que `_t()` para correr `site_identity.render()` sobre el
    resultado antes de devolverlo."""
    lang = i18n.active()
    frag = legal.body_html(doc_id, lang)
    return Markup(site_identity.render(frag))


ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATE_DIRS),
    # Ver el docstring del módulo — TODO valor de contexto se escapa acá;
    # los globals confiables devuelven Markup pre-escapado.
    autoescape=jinja2.select_autoescape(),
    # Un `f.read()` legacy nunca recorta el archivo — sin esto Jinja2 borra
    # el último salto de línea del archivo fuente, rompiendo la paridad byte
    # a byte contra un archivo que SÍ termina en `\n`.
    keep_trailing_newline=True,
)
ENV.globals["t"] = _t
ENV.globals["schedule_band"] = display_format.schedule_band
ENV.globals["sitio"] = _sitio
ENV.globals["legal_doc"] = _legal_doc
# Plain dicts of pack strings; templates autoescape them.
ENV.globals["info_links"] = site_identity.info_links
# `{{ lang() }}` — the request's resolved language (`i18n.active()`), bound
# as a callable so it is read at render time, never frozen at import. Used
# to hand the visitor's language across to humanrounds.org (`?lang=`), so
# a visitor never lands on the public site in the other language.
ENV.globals["lang"] = i18n.active


def _ai_chat_configured():
    """`{{ ai_chat_configured() }}` — True when the chat can exist at all (an
    AI key and no TURNOS_AI_DISABLED); the chat launcher is not painted
    otherwise. Lazy import: the AI package is not needed to build the env."""
    from app.ai import ai_health
    return ai_health.configured()


ENV.globals["ai_chat_configured"] = _ai_chat_configured
# `{{ static('nav.js') }}` — Tier 4 slice 6 (static_fingerprint.py). Bound
# directly to `url()`, no per-value escaping wrapper needed like `_t`/
# `_sitio`/`_legal_doc` have: a fingerprinted URL is `[a-zA-Z0-9_./-]` only,
# already attribute-safe, and never carries a `{{sitio.*}}`/translated
# fragment that would need a second substitution pass.


def _platform_credit():
    """`{% if platform_credit() %}` — the footer's «Funciona con Human Rounds»
    link (the product owner, 2026-10-17). Only on an installation whose visible brand is
    NOT Human Rounds itself (Pinamar); on the demo it would repeat the name
    the page already carries."""
    from app.core import theme
    return theme.brand_name() != "Human Rounds"


ENV.globals["platform_credit"] = _platform_credit


def _brand_is_page_title():
    """`{% if brand_is_page_title() %}` — the home <title>/og:title is the
    brand alone on a theme whose brand name already says what the site is."""
    from app.core import theme
    return theme.brand_is_page_title()


ENV.globals["brand_is_page_title"] = _brand_is_page_title
ENV.globals["static"] = static_fingerprint.url
ENV.globals["module_static"] = static_fingerprint.module_url
# `{{ static_manifest() }}` — Tier 4 slice 6, batch 3 (the client manifest):
# the inline <script> publishing `window.SANA_STATIC`, the name -> current
# fingerprinted URL map for the assets a STATIC JS file loads by URL at
# runtime (static_fingerprint.CLIENT_LOADED). Emitted ONCE in <head> by
# every template that loads a runtime loader (platform/chat-widget.mjs,
# platform/account-form.mjs, my-requests.js, lab-results.js,
# platform/admin-create-appointment.mjs — templates/index|about|privacy|terms|account|
# account-reset.html and templates/admin/base.html). Markup como los otros
# globals de confianza: es un <script> entero, NO texto a escapar (con
# autoescape activo un str plano quedaría entity-escapado y roto).
ENV.globals["static_manifest"] = lambda: Markup(static_fingerprint.manifest_script())
# The canonical browser formatter reads the same pack-derived country
# configuration as core.format. Direct admin screens expose it before their
# deferred controllers dynamically import format.mjs. Mismo razonamiento que
# static_manifest: JSON crudo para adentro de <script>, Markup para que
# autoescape no lo toque.
ENV.globals["country_phone_json"] = lambda: Markup(display_format.country_phone_json())
# `{{ screen_rol() }}` — the verified session role deps.require_admin bound
# for THIS request (admin_auth.bind_role/active_role, the same request-scoped
# ContextVar pattern as i18n.bind). templates/admin/base.html initializes the
# shell facilities the legacy shell gets from whoami (the triage bell) with
# it, so every direct screen reads the role the SERVER verified — never a
# per-route context variable an individual screen router can forget to pass,
# and never an undefined Jinja name rendering as empty. '' when no admin gate
# ran for this render; the bell's own JS guard ignores anything that is not
# admin/medico, and every fetch still goes through require_admin.
ENV.globals["screen_rol"] = admin_auth.active_role
# `{{ screen_permisos() }}` — the effective permissions the same gate verified
# (sorted ids, [] when no gate ran). Client controllers read it to show or
# hide a control; the server still authorizes every request on its own.
ENV.globals["screen_permisos"] = admin_auth.active_permissions
# `{{ screen_2fa() }}` — the 2FA-enrolment nudge the same gate verified for
# THIS request ({} when the account has 2FA, has no deadline, or no gate
# ran). base.html renders the "configurá la verificación en 2 pasos antes
# del {fecha}" banner from it on every direct screen; the Account screen is
# excluded there because it carries its own notice.
ENV.globals["screen_2fa"] = admin_auth.active_2fa


def render(template_name, **context):
    """El HTML que produce la plantilla Jinja2, SIN los pasos posteriores
    que `site_identity.render_page()` sigue aplicando por fuera de la
    sustitución de placeholders (`{{lang}}`/`{{tema}}`/`{{i18n_bootstrap}}`,
    el banner de demo) — esos corren igual para las dos implementaciones,
    ver `site_identity_router.py`.

    `**context` (Tier 5 slice 4): las cuatro páginas de site_identity no
    necesitaban datos por-request más allá de los globals ya bindeados
    (`t`/`sitio`/`legal_doc`/`static`), así que el `render()` original no
    aceptaba ninguno — un `screen_router` SÍ necesita pasar datos propios
    de la request (identidad de sesión, secciones del sidebar) a su
    plantilla. Default vacío: cada llamada existente (`render(name)`, sin
    kwargs) sigue rindiendo exactamente igual que antes de este cambio."""
    return ENV.get_template(template_name).render(**context)
