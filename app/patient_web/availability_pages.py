"""Public doctor-profile and specialty-listing pages.

This module owns the late, read-only dynamic URL family historically rendered
inside server.py: /<specialty>/ and /<specialty>/<doctor>. The native caller
reaches this owner only after all registered routes and the /api fallback
decline the request, preserving the broad matcher\'s exact precedence.
"""

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

from app.core import i18n
from app.core import site_identity
from app.core import static_fingerprint
from app.core import theme
from app.patient_web import availability_directory
from app.patient_web import seo

# Legacy directory data now lives in patient_web.availability_directory.
# ---- gendered specialty titles ----
_SPECIALTY_GENDERED = {
    'cardiologia': ('cardiólogo', 'cardióloga'),
    'pediatria': ('pediatra', 'pediatra'),
    'dermatologia': ('dermatólogo', 'dermatóloga'),
    'clinica-medica': ('médico clínico', 'médica clínica'),
    'clinica-y-medicina-general': ('médico clínico', 'médica clínica'),
    'medicina-familiar': ('médico de familia', 'médica de familia'),
    'ginecologia': ('ginecólogo', 'ginecóloga'),
    'ginecologia-y-obstetricia': ('ginecólogo', 'ginecóloga'),
    'traumatologia': ('traumatólogo', 'traumatóloga'),
    'traumatologia-y-kinesiologia': ('traumatólogo', 'traumatóloga'),
    'neurologia': ('neurólogo', 'neuróloga'),
    'salud-mental': ('psiquiatra/psicólogo', 'psiquiatra/psicóloga'),
    'psiquiatria': ('psiquiatra', 'psiquiatra'),
    'psicologia': ('psicólogo', 'psicóloga'),
    'cirugia': ('cirujano', 'cirujana'),
    'cirugia-plastica': ('cirujano plástico', 'cirujana plástica'),
    'endocrinologia': ('endocrinólogo', 'endocrinóloga'),
    'endocrinologia-diabetes-y-nutricion': ('endocrinólogo', 'endocrinóloga'),
    'gastroenterologia': ('gastroenterólogo', 'gastroenteróloga'),
    'oftalmologia': ('oftalmólogo', 'oftalmóloga'),
    'otorrinolaringologia': ('otorrinolaringólogo', 'otorrinolaringóloga'),
    'otorrinolaringologia-y-fono': ('otorrinolaringólogo', 'otorrinolaringóloga'),
    'urologia': ('urólogo', 'uróloga'),
    'reumatologia': ('reumatólogo', 'reumatóloga'),
    'oncologia': ('oncólogo', 'oncóloga'),
    'oncologia-y-hematologia': ('oncólogo/hematólogo', 'oncóloga/hematóloga'),
    'infectologia': ('infectólogo', 'infectóloga'),
    'hematologia': ('hematólogo', 'hematóloga'),
    'nefrologia': ('nefrólogo', 'nefróloga'),
    'neumologia': ('neumólogo', 'neumóloga'),
    'alergia-e-inmunologia': ('alergólogo', 'alergóloga'),
    'nutricion': ('nutricionista', 'nutricionista'),
    'kinesiologia': ('kinesiólogo', 'kinesióloga'),
    'fonoaudiologia': ('fonoaudiólogo', 'fonoaudióloga'),
    'odontologia': ('odontólogo', 'odontóloga'),
    'diagnostico': ('médico imagenólogo', 'médica imagenóloga'),
    'diagnostico-imagen': ('médico imagenólogo', 'médica imagenóloga'),
    'diagnostico-/-imagen': ('médico imagenólogo', 'médica imagenóloga'),
}

def _gendered_title(esp_slug, gender):
    pair = _SPECIALTY_GENDERED.get(esp_slug)
    if pair:
        return pair[1] if gender == 'f' else pair[0]
    return 'médica' if gender == 'f' else 'médico'

# ---- logo.dev for insurance companies ----
_INSURER_DOMAINS = {
    'osde': 'osde.com.ar',
    'swiss medical': 'swissmedical.com.ar',
    'pami': 'pami.gob.ar',
    'ioma': 'ms.gba.gov.ar',
    'omint': 'omint.com.ar',
    'galeno': 'galenomed.com.ar',
    'medife': 'medife.com.ar',
    'sancor salud': 'sancorsalud.com.ar',
    'federada salud': 'federadasalud.com.ar',
    'medicus': 'medicus.com.ar',
    'hospital italiano': 'hospitalitaliano.org.ar',
    'hospital aleman': 'hospitalaleman.org.ar',
    'hospital alemán': 'hospitalaleman.org.ar',
    'avalian': 'avalian.com.ar',
    'prevencion salud': 'prevencionsalud.com.ar',
    'prevención salud': 'prevencionsalud.com.ar',
    'jerarquico salud': 'jerarquicosalud.com.ar',
    'jerarquico': 'jerarquicosalud.com.ar',
    'osecac': 'osecac.com.ar',
    'ospedyc': 'ospedyc.com.ar',
    'smata': 'smata.org.ar',
    'luz y fuerza': 'luzyfuerza.org.ar',
    'circulo medico': 'circulomedico.org.ar',
    'círculo médico': 'circulomedico.org.ar',
    'banco pcia': 'bancociudad.com.ar',
}
# The publishable logo.dev token is installation configuration, not source:
# without TURNOS_LOGODEV_KEY the coverage chips fall back to their initial.
LOGODEV_KEY_ENV = "TURNOS_LOGODEV_KEY"


def _logodev_key():
    return os.environ.get(LOGODEV_KEY_ENV, "").strip()


@dataclass(frozen=True)
class HtmlResult:
    """Transport-neutral late-page response owned by this renderer."""

    body: bytes
    status: int = 200
    content_type: str = "text/html; charset=utf-8"

def _insurer_domain(obra):
    return _INSURER_DOMAINS.get(obra.lower().strip())

def _insurer_logo_tag(obra, size=28):
    domain = _insurer_domain(obra)
    key = _logodev_key()
    if domain and key:
        url = f'https://img.logo.dev/{domain}?token={urllib.parse.quote(key, safe="")}&size={size}&format=png'
        return f'<img src="{_esc(url)}" alt="{_esc(obra)}" width="{size}" height="{size}" style="border-radius:4px;object-fit:contain;" loading="lazy" onerror="this.style.display=\'none\'">'
    return ''


def _esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# Page-level CSS and the photo-lightbox script: fixed text, no interpolation.
_DOCTOR_PAGE_CSS = """  /* tokens compartidos → /human-rounds.css */
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 var(--font);}
  .stz-mark{width:45px;height:45px;background:url(/logo.png) center/contain no-repeat;flex-shrink:0;}
  .stz-rate{display:inline-flex;align-items:center;gap:2px;font:600 13px/1 var(--font-mono);color:var(--warn);}
  .stz-link{color:var(--accent);text-decoration:none;}
  .stz-link:hover{color:var(--accent-hover);}
  .wrap{max-width:1000px;margin:0 auto;padding:26px 24px 80px;}
  .crumb{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font-size:13px;margin-bottom:22px;}
  .crumb a{color:var(--accent);text-decoration:none;font-weight:500;}
  .crumb a:hover{color:var(--accent-hover);}
  .crumb .sep{color:var(--text-4);}
  .pf-hero{display:flex;align-items:center;gap:20px;margin-bottom:22px;}
  .pf-photo-btn{display:block;border:0;background:transparent;padding:0;margin:0;border-radius:50%;cursor:zoom-in;line-height:0;
    transition:transform .14s,filter .14s;}
  .pf-photo-btn:hover{transform:scale(1.035);filter:brightness(1.02);}
  .pf-photo-btn:focus-visible{outline:3px solid var(--focus-ring,rgba(201,99,66,.18));outline-offset:5px;}
  .pf-av{flex-shrink:0;width:76px;height:76px;border-radius:50%;background:var(--bg-2) center/cover no-repeat;object-fit:cover;
    border:1px solid var(--border);box-shadow:0 0 0 3px var(--panel),0 8px 22px -14px rgba(40,30,20,.3);}
  .pf-av-i{display:flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent);
    font:650 27px/1 Inter,sans-serif;letter-spacing:-.02em;border:1px solid color-mix(in oklch,var(--accent) 16%,transparent);box-shadow:none;}
  /* estudio: icono neutro de diagnóstico por imágenes (cuadrado redondeado) */
  .pf-av-estudio{border-radius:18px;display:flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent);
    border:1px solid color-mix(in oklch,var(--accent) 16%,transparent);box-shadow:none;}
  .pf-badge{display:inline-flex;align-items:center;gap:7px;font:600 13px/1 var(--font-mono);
    padding:6px 9px;border-radius:6px;margin-top:11px;}
  .pf-badge-on{color:var(--done);background:var(--state-done-bg);border:1px solid var(--state-done-bd);}
  .pf-badge-off{color:var(--text-3);background:var(--bg-2);border:1px solid var(--border);}
  .pf-dot{width:6px;height:6px;border-radius:50%;background:var(--done);}
  /* banda de reserva (Option A) — a todo el ancho, bajo el encabezado */
  .pf-band{display:flex;align-items:center;gap:18px;flex-wrap:wrap;background:var(--accent-soft);
    border:1px solid color-mix(in oklch,var(--accent) 22%,transparent);border-radius:13px;padding:14px 16px;margin-bottom:11px;}
  .pf-band-off{background:var(--bg-2);border-color:var(--border);}
  .pf-band-ico{flex-shrink:0;width:42px;height:42px;border-radius:11px;background:var(--panel);color:var(--accent);display:flex;
    align-items:center;justify-content:center;border:1px solid color-mix(in oklch,var(--accent) 22%,transparent);}
  .pf-band-off .pf-band-ico{color:var(--text-3);border-color:var(--border-strong);}
  .pf-band-k{font:600 13px/1 var(--font-mono);color:var(--accent-hover);}
  .pf-band-off .pf-band-k{color:var(--text-3);}
  .pf-band-v{margin-top:5px;font-size:18px;font-weight:680;letter-spacing:-.01em;color:var(--text);}
  .pf-band-cta{margin-left:auto;display:inline-flex;align-items:center;gap:8px;background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);text-decoration:none;
    font:600 14.5px var(--font);letter-spacing:-.01em;padding:12px 20px;border-radius:var(--btn-radius,11px);transition:filter .12s,background .12s;}
  .pf-band-cta:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}
  .pf-band-note{margin:0 0 26px;font-size:13px;color:var(--text-3);line-height:1.5;}
  .pf-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-bottom:18px;box-shadow:0 1px 0 rgba(0,0,0,.02);}
  .pf-h{font-size:14px;font-weight:600;color:var(--text-3);margin-bottom:13px;}
  .pf-row{display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:center;padding:14px 0;border-bottom:1px solid var(--border-soft);}
  .pf-row:last-child{border-bottom:0;}
  .pf-k{font-size:14px;font-weight:600;color:var(--text-3);}
  .pf-cob-wrap{display:flex;flex-wrap:wrap;gap:8px;}
  .pf-cob{display:inline-flex;align-items:center;gap:8px;background:var(--chip-bg);border:1px solid var(--border);border-radius:8px;
    padding:5px 11px 5px 6px;text-decoration:none;color:var(--text-2);transition:border-color .12s,color .12s;}
  .pf-cob:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);}
  .pf-cob img{width:22px;height:22px;border-radius:5px;object-fit:contain;background:var(--panel);}
  .pf-cob-i{width:22px;height:22px;border-radius:5px;background:var(--panel);border:1px solid var(--border-strong);display:flex;
    align-items:center;justify-content:center;font:600 13px/1 var(--font-mono);color:var(--text-3);}
  .pf-cob span{font-size:13px;font-weight:500;white-space:nowrap;}
  .pf-todas{display:inline-flex;align-items:center;font-size:13px;font-weight:600;color:var(--done);
    background:color-mix(in oklch,var(--done) 13%,transparent);border:1px solid color-mix(in oklch,var(--done) 30%,transparent);
    border-radius:999px;padding:4px 12px;white-space:nowrap;}
  .pf-copay{display:inline-flex;align-items:center;font-size:13px;font-weight:600;color:var(--warn);margin-top:9px;
    background:color-mix(in oklch,var(--warn) 13%,transparent);border:1px solid color-mix(in oklch,var(--warn) 30%,transparent);
    border-radius:7px;padding:4px 10px;white-space:nowrap;}
  .pf-rel{display:inline-flex;align-items:center;gap:6px;font-size:13.5px;font-weight:500;color:var(--accent);text-decoration:none;}
  .pf-rel:hover{color:var(--accent-hover);}
  .pf-lightbox{position:fixed;inset:0;z-index:80;display:none;align-items:center;justify-content:center;background:rgba(20,18,14,.58);
    backdrop-filter:blur(8px);padding:32px;}
  .pf-lightbox.is-open{display:flex;}
  .pf-lightbox-img{width:auto;height:auto;max-width:min(384px,calc(100vw - 48px));max-height:calc(100vh - 80px);
    border-radius:14px;background:var(--panel);box-shadow:0 24px 80px -26px rgba(0,0,0,.56);}
  .pf-lightbox-x{position:fixed;top:18px;right:18px;border-radius:50%;background:rgba(255,255,255,.92);
    color:var(--text);box-shadow:0 8px 26px -16px rgba(0,0,0,.5);}
  .pf-lightbox-x:focus-visible{outline:3px solid rgba(255,255,255,.55);outline-offset:3px;}
  @media(max-width:560px){
    .pf-band{gap:12px;}
    .pf-band-cta{margin-left:0;width:100%;justify-content:center;}
    .pf-hero h1{font-size:27px !important;}
  }
"""

_DOCTOR_LIGHTBOX_SCRIPT = """(() => {
  const trigger = document.querySelector('.pf-photo-btn[data-photo-zoom]');
  const box = document.getElementById('pfPhotoLightbox');
  const img = document.getElementById('pfPhotoZoom');
  const close = box && box.querySelector('.pf-lightbox-x');
  if (!trigger || !box || !img || !close) return;
  const open = () => {
    img.src = trigger.dataset.photoZoom || '';
    img.alt = trigger.dataset.photoAlt || '';
    box.classList.add('is-open');
    box.setAttribute('aria-hidden', 'false');
    close.focus();
  };
  const shut = () => {
    box.classList.remove('is-open');
    box.setAttribute('aria-hidden', 'true');
    img.removeAttribute('src');
    trigger.focus();
  };
  trigger.addEventListener('click', open);
  close.addEventListener('click', shut);
  box.addEventListener('click', (event) => { if (event.target === box) shut(); });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && box.classList.contains('is-open')) shut();
  });
})();
"""


def _doctor_avatar_html(m, display, initials):
    """Avatar: real photo (sprite or URL), otherwise circular initials."""
    ps = m.get("profile_sprite") or {}
    zoom_src = m.get("photo_zoom") or m.get("foto") or ""
    if ps:
        # The sprite uses 100px tiles; scale it to the 76px profile avatar.
        f = 76 / 100
        bsz = re.sub(r'(-?[\d.]+)px', lambda mo: f"{float(mo.group(1)) * f:.2f}px", ps['bg_size'])
        bps = re.sub(r'(-?[\d.]+)px', lambda mo: f"{float(mo.group(1)) * f:.2f}px", ps['bg_pos'])
        avatar_core = (f'<div class="pf-av" role="img" aria-label="{_esc(display)}" style="background-image:url({_esc(ps["image"])});'
                       f'background-size:{bsz};background-position:{bps};"></div>')
    elif m.get("foto"):
        # A real photo uses object-fit so arbitrary source dimensions stay centered.
        avatar_core = f'<img class="pf-av" src="{_esc(m["foto"])}" alt="{_esc(display)}" loading="lazy">'
    else:
        avatar_core = f'<div class="pf-av pf-av-i">{_esc(initials)}</div>'
    if zoom_src:
        return (f'<button class="pf-photo-btn" type="button" data-photo-zoom="{_esc(zoom_src)}" '
                f'data-photo-alt="{_esc(display)}" aria-label="Ampliar foto de {_esc(display)}">{avatar_core}</button>')
    return avatar_core


def _doctor_coverage_html(m):
    """Coverage: logo.dev/name chips. Free-text copay shows its exact amount
    when the source includes one."""
    _copago_html = ""
    if m.get("copago"):
        _mt = m.get("copago_monto")
        _copago_html = (
            f'<div class="pf-copay" title="Según el texto del médico — confirmá el monto con la clínica.">'
            f'Coseguro/co-pago{(" de " + _esc(_mt)) if _mt else ""} en la consulta</div>')
    obras = m.get('obras_sociales') or []
    if m.get("acepta_todas"):
        # Accepts all insurers: show the broad claim, never infer one carrier.
        return (f'<div class="pf-cob-wrap"><span class="pf-todas">Todas las obras sociales</span></div>'
                f'{_copago_html}')
    if obras:
        _chips = []
        for o in obras:
            ic = _insurer_logo_tag(o, 22) or f'<span class="pf-cob-i">{_esc(o[:1].upper())}</span>'
            _chips.append(f'<a class="pf-cob" href="/cobertura/{_esc(availability_directory._slugify(o))}">{ic}<span>{_esc(o)}</span></a>')
        return f'<div class="pf-cob-wrap">{"".join(_chips)}</div>{_copago_html}'
    return (f'<div style="font-size:13.5px;color:var(--text-3);">Atiende de forma particular.</div>'
            f'{_copago_html}')


def _doctor_band_html(m):
    pt_fmt = availability_directory._turno_friendly(m.get('proximo_turno')) or m.get('proximo_turno_fmt')
    # Full-width availability band below the heading, not a right-side rail.
    _cal_svg = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                '<rect x="3.5" y="5" width="17" height="15" rx="2.4"></rect>'
                '<line x1="3.5" y1="9.5" x2="20.5" y2="9.5"></line>'
                '<line x1="8" y1="3" x2="8" y2="6.5"></line><line x1="16" y1="3" x2="16" y2="6.5"></line></svg>')
    if pt_fmt:
        band_k, band_v, band_off = "Próximo turno disponible", _esc(pt_fmt), ""
    else:
        band_k, band_v, band_off = "Turnos online", "Sin turnos online por ahora", " pf-band-off"
    # The "Ingresá para reservar" CTA used to deep-link into the WebTurno
    # app.html client (/app); that client and its whole booking API were
    # retired (server-retirement slice 3, 2026-08-16) — /app now 404s. This
    # band stays read-only info (next appointment / online status), with no
    # CTA and no promise of an account-based booking flow that no longer
    # exists on this page.
    return (
        f'<div class="pf-band{band_off}">'
        f'<span class="pf-band-ico" aria-hidden="true">{_cal_svg}</span>'
        f'<div style="min-width:0;"><div class="pf-band-k">{band_k}</div>'
        f'<div class="pf-band-v">{band_v}</div></div>'
        f'</div>'
    )


def _doctor_jsonld(m, name_full, canonical, loc, prov):
    ld = {
        "@context": "https://schema.org", "@type": "Physician",
        "name": name_full,
        "medicalSpecialty": m["especialidad"],
        "url": canonical,
        "memberOf": {"@type": "MedicalClinic", "name": m["clinica"],
                     "address": {"@type": "PostalAddress", "addressLocality": loc,
                                 "addressRegion": prov, "addressCountry": "AR"}},
        "areaServed": f"{loc}, {prov}, Argentina",
    }
    if m.get("foto"):
        ld["image"] = m["foto"]
    return ld


def render_doctor_page(m):
    """Render one server-side doctor profile with Physician JSON-LD."""
    display = m.get('display_name') or m['nombre']
    gender = m.get('gender', 'm')
    esp_slug = m.get('esp_slug', availability_directory._slugify(m['especialidad']))
    title_prefix = 'Dra.' if gender == 'f' else 'Dr.'
    gendered = _gendered_title(esp_slug, gender)
    gendered_cap = gendered[:1].upper() + gendered[1:] if gendered else gendered
    loc, prov = site_identity.get("city"), site_identity.get("province")
    seo_title = f"{title_prefix} {display} — {gendered_cap} en {loc}, {prov} | {theme.brand_name()}"
    canonical_path = f"/{esp_slug}/{m['slug']}"
    canonical = seo.canonical_url(canonical_path)
    clinica_slug = availability_directory._slugify(m['clinica'])
    esp_display = m['grupos'][0] if m['grupos'] else m['especialidad']

    name_full = f"{title_prefix} {display}"
    initials = "".join(w[:1] for w in display.split()[:2]).upper() or display[:1].upper()

    avatar = _doctor_avatar_html(m, display, initials)

    online_badge = (
        '<span class="pf-badge pf-badge-on"><span class="pf-dot"></span>Tiene turnos online</span>'
        if m["online"] else
        '<span class="pf-badge pf-badge-off">Sin turnos online por ahora</span>'
    )

    cob_body = _doctor_coverage_html(m)

    band_html = _doctor_band_html(m)

    desc = (f'Pedí turno con {name_full}, {gendered} en {m["clinica"]}, {loc}. '
            f'Mirá el próximo turno disponible, en qué clínica atiende y qué obras sociales y prepagas acepta — gratis y sin registro.')

    ld = _doctor_jsonld(m, name_full, canonical, loc, prov)

    page = f"""<!DOCTYPE html>
<html lang="es" data-theme="{theme.current()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{theme.favicon()}
{seo.head(seo_title, desc, canonical_path)}
{theme.fonts()}
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
<style>
{_DOCTOR_PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <header id="stznav" data-section="medicos"></header>
  <script type="module" src="{static_fingerprint.url('platform/nav.mjs')}"></script>

  <nav class="crumb" aria-label="Ruta de navegación">
    <a href="/medicos">Directorio de médicos</a>
    <span class="sep">/</span>
    <a href="/{_esc(esp_slug)}/">{_esc(esp_display)}</a>
    <span class="sep">/</span>
    <span style="color:var(--text-3);">{_esc(display)}</span>
  </nav>

  <div class="pf-hero">
    {avatar}
    <div style="min-width:0;">
      <h1 style="margin:0;font-size:34px;font-weight:700;letter-spacing:-.03em;line-height:1.05;text-wrap:balance;">{_esc(name_full)}</h1>
      <div style="margin-top:7px;font-size:15px;color:var(--text-2);font-weight:500;">{_esc(gendered_cap)} en {_esc(loc)}</div>
      {online_badge}
    </div>
  </div>

  <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:var(--text-2);text-wrap:pretty;">{_esc(desc)}</p>

  {band_html}

  <section class="pf-card" style="padding:4px 18px;">
    <div class="pf-row"><div class="pf-k">Especialidad</div>
      <div><a class="stz-link" href="/{_esc(esp_slug)}/">{_esc(esp_display)}</a></div></div>
    <div class="pf-row"><div class="pf-k">Clínica</div>
      <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap;">
        <a href="/clinica/{_esc(clinica_slug)}" style="font-size:14px;font-weight:600;color:var(--text);text-decoration:none;border-bottom:1px solid transparent;">{_esc(m['clinica'])}</a>
        <span style="display:inline-flex;align-items:center;gap:4px;font-size:13px;color:var(--text-3);"><span class="stz-rate">★ {m['rating']:.1f}</span> Google</span>
      </div></div>
  </section>

  <section class="pf-card">
    <div class="pf-h">Cobertura</div>
    {cob_body}
  </section>

  <section class="pf-card" style="margin-bottom:0;">
    <div class="pf-h">Ver también</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px 22px;">
      <a class="pf-rel" href="/{_esc(esp_slug)}/">{_esc(esp_display)} en {_esc(loc)}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="18" y2="12"></line><polyline points="12 6 18 12 12 18"></polyline></svg></a>
      <a class="pf-rel" href="/clinica/{_esc(clinica_slug)}">Médicos en {_esc(m['clinica'])}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="18" y2="12"></line><polyline points="12 6 18 12 12 18"></polyline></svg></a>
    </div>
  </section>

  <p style="margin:22px 2px 0;font-size:13px;color:var(--text-4);line-height:1.5;">
    {theme.brand_name()} es un proyecto independiente, gratuito y sin fines de lucro. No está afiliado a WebTurno ni a las clínicas. Confirmá siempre tu turno con la clínica.
  </p>
</div>
<div class="pf-lightbox" id="pfPhotoLightbox" aria-hidden="true" role="dialog" aria-modal="true">
  <button class="pf-lightbox-x hr-x" type="button" aria-label="{_esc(i18n.t_or('doctor.close_photo', 'Cerrar foto', 'es'))}"></button>
  <img class="pf-lightbox-img" id="pfPhotoZoom" alt="">
</div>
<script>
{_DOCTOR_LIGHTBOX_SCRIPT}</script>
</body>
</html>"""
    return page.encode("utf-8")


def render_specialty_page(esp_slug):
    """Render the public ``/<specialty>/`` listing."""
    esp_slug = str(esp_slug or '').strip().lower()
    meds = availability_directory.medicos_by_esp(esp_slug)
    if not meds:
        return None
    loc = site_identity.get("city")
    esp_display = meds[0]['grupos'][0] if meds[0]['grupos'] else meds[0]['especialidad']
    online_count = sum(1 for m in meds if m['online'])
    total = len(meds)
    # «1 profesional», never «1 profesionales»: the one plural rule (core.i18n).
    profesionales = f"{total} profesional" if i18n.plural_category(total) == "one" else f"{total} profesionales"
    # Nearest available appointment across the listing.
    pts = [m['proximo_turno'] for m in meds if m.get('proximo_turno')]
    pts.sort()
    next_pt_fmt = None
    if pts:
        try:
            dt = datetime.strptime(pts[0], "%Y-%m-%d %H:%M:%S")
            next_pt_fmt = dt.strftime("%-d/%-m/%Y %H:%M")
        except (ValueError, TypeError):
            pass

    # Determine gendered plural title for specialty
    g_title = _gendered_title(esp_slug, 'm')
    g_title_cap = g_title[:1].upper() + g_title[1:] if g_title else g_title
    g_plural = g_title_cap + 's' if not g_title.endswith('a') and not g_title.endswith('e') else g_title_cap + 's'

    rows_html = ''
    for m in sorted(meds, key=lambda x: (not x['online'], x['nombre'].lower())):
        display = m.get('display_name') or m['nombre']
        gender = m.get('gender', 'm')
        tp = 'Dra.' if gender == 'f' else 'Dr.'
        pt_str = m.get('proximo_turno_fmt') or ''
        pt_html = f'<span style="font-size:13px;color:#4a7fb5;">{_esc(pt_str)}</span>' if pt_str else '<span style="font-size:13px;color:var(--text-4);">Sin turno próximo</span>'
        online_dot = '<span style="color:var(--done);font-size:13px;font-weight:700;">● </span>' if m['online'] else ''
        ps = m.get('avatar36') or m.get('avatar') or m.get('profile_sprite') or {}
        if ps:
            av = (f'<div style="width:36px;height:36px;border-radius:50%;flex-shrink:0;overflow:hidden;'
                  f'background-image:url({_esc(ps["image"])});background-size:{_esc(ps["bg_size"])};'
                  f'background-position:{_esc(ps["bg_pos"])};background-repeat:no-repeat;"></div>')
        elif m.get('foto'):
            av = f'<img style="width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0;" src="{_esc(m["foto"])}" alt="" loading="lazy">'
        else:
            av = (f'<div style="width:36px;height:36px;border-radius:50%;flex-shrink:0;background:var(--accent-soft);'
                  f'color:var(--accent);display:flex;align-items:center;justify-content:center;'
                  f'font:600 14px Inter,sans-serif;">{_esc(display[:1].upper())}</div>')
        rows_html += (
            f'<a href="{_esc(m["doc_url"])}" style="display:flex;align-items:center;gap:12px;padding:12px 16px;'
            f'text-decoration:none;color:var(--text);border-bottom:1px solid var(--border);'
            f'transition:background .1s;" onmouseover="this.style.background=\'var(--bg-2)\'" '
            f'onmouseout="this.style.background=\'\'">'
            f'{av}'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-weight:600;font-size:14px;">{online_dot}{_esc(tp + " " + display)}</div>'
            f'<div style="font-size:13px;color:var(--text-2);">{_esc(m["clinica"])}</div>'
            f'</div>'
            f'<div style="text-align:right;flex-shrink:0;">{pt_html}</div>'
            f'</a>'
        )

    ld = {
        "@context": "https://schema.org", "@type": "MedicalWebPage",
        "name": f"{g_plural} en {loc}",
        "url": seo.canonical_url(f"/{esp_slug}"),
        "description": f"Directorio de {total} {g_title}s en {loc}. Turnos online disponibles.",
        "specialty": esp_display,
    }

    page_title = f"{g_plural} en {loc} | Turnos online | {theme.brand_name()}"
    page_desc = (f"Encontrá {g_title}s en {loc}: {total} profesionales, "
                 "turnos online, centros y datos para atenderte.")
    page = f"""<!DOCTYPE html>
<html lang="es" data-theme="{theme.current()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{theme.favicon()}
{seo.head(page_title, page_desc, f"/{esp_slug}")}
{theme.fonts()}
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
<style>
  /* tokens compartidos → /human-rounds.css */
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 var(--font);}}
  .stz-mark{{width:45px;height:45px;background:url(/logo.png) center/contain no-repeat;flex-shrink:0;}}
  .stz-navlink{{font-size:13.5px;font-weight:500;color:var(--text-2);text-decoration:none;padding:7px 11px;border-radius:8px;}}
  .stz-navlink:hover{{background:var(--hover-bg);color:var(--hover-ink);}}
  .stz-enter{{display:inline-flex;align-items:center;gap:7px;background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);text-decoration:none;
    font:600 13.5px Inter,sans-serif;padding:8px 14px;border-radius:var(--btn-radius,9px);}}
  .stz-enter:hover{{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}}
  .stz-link{{color:var(--accent);text-decoration:none;}}
  .stz-link:hover{{color:var(--accent-hover);}}
  .wrap{{max-width:1000px;margin:0 auto;padding:26px 24px 80px;}}
</style>
</head>
<body>
<div class="wrap">
  <header id="stznav"></header>
  <script type="module" src="{static_fingerprint.url('platform/nav.mjs')}"></script>

  <div style="font-size:13px;color:var(--text-3);margin-bottom:14px;">
    <a href="/medicos" class="stz-link" style="border:0;">Directorio de médicos</a>
    <span style="color:var(--text-4);"> / </span>{_esc(esp_display)}
  </div>

  <h1 style="margin:0 0 6px;font-size:28px;font-weight:700;letter-spacing:-.03em;">{_esc(g_plural)} en {_esc(loc)}</h1>
  <p style="margin:0 0 20px;font-size:14px;color:var(--text-2);">{profesionales} · {online_count} con turnos online{" · próximo turno: " + _esc(next_pt_fmt) if next_pt_fmt else ""}</p>

  <div style="background:var(--panel);border:1px solid var(--border);border-radius:13px;overflow:hidden;margin-bottom:24px;">
    {rows_html}
    <div style="padding:12px 16px;">
      <a href="/medicos" class="stz-link" style="font-size:13px;">Ver todos los médicos de {_esc(loc)} →</a>
    </div>
  </div>

  <p style="margin:22px 2px 0;font-size:13px;color:var(--text-4);line-height:1.5;">
    {theme.brand_name()} es un proyecto independiente, gratuito y sin fines de lucro. No está afiliado a WebTurno ni a las clínicas. Confirmá siempre tu turno con la clínica.
  </p>
</div>
</body>
</html>"""
    return page.encode("utf-8")


def match(method, request_path):
    """Return an immutable late-page result, or ``None`` when not owned."""
    if method != "GET":
        return None
    known_prefixes = {
        "medico", "clinica", "cobertura", "api", "medicos", "static",
        "data", "panel", "app", "acerca", "privacidad", "terminos",
        "favicon.ico", "logo.png",
    }
    parts = [
        part for part in urllib.parse.unquote(request_path).strip("/").split("/")
        if part
    ]
    if not parts or parts[0].lower() in known_prefixes:
        return None
    if len(parts) == 2:
        doctor = availability_directory.medico_by_url(parts[0], parts[1])
        if doctor:
            return HtmlResult(render_doctor_page(doctor))
    elif len(parts) == 1:
        body = render_specialty_page(parts[0])
        if body:
            return HtmlResult(body)
    return None
