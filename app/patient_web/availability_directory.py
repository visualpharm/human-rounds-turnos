"""Read-only availability-directory data plane.

The WebTurno snapshot is intentionally read-only.  This module owns the
public directory projection used by the canonical ``GET /api/specialties``
contract and the legacy specialty/profile pages.  It deliberately does not
own ``/datos`` and does not depend on ``patient_web.catalog``: those are a
separate historical dashboard and the editable municipality catalog.
"""

import copy
import json
import math
import os
import re
import statistics
import threading
from datetime import datetime

from app.core import sitedata
from app.core import paths


ROOT = paths.PROJECT_ROOT
DATA_PATH = os.path.join(ROOT, "data", "availability.json")

_JSON_CACHE = {}
_JSON_CACHE_LOCK = threading.Lock()


def _json_cacheado(path, si_falla):
    try:
        st = os.stat(path)
        clave = (st.st_mtime_ns, st.st_size)
    except OSError:
        if si_falla is None:
            raise
        return si_falla
    with _JSON_CACHE_LOCK:
        entrada = _JSON_CACHE.get(path)
        if entrada is not None and entrada[0] == clave:
            return entrada[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError):
        if si_falla is None:
            raise
        return si_falla
    with _JSON_CACHE_LOCK:
        _JSON_CACHE[path] = (clave, datos)
    return datos


def _load_db(copia=False):
    """Return the read-only WebTurno snapshot, optionally copied for mutation."""
    datos = _json_cacheado(DATA_PATH, None)
    return copy.deepcopy(datos) if copia else datos


def _load_photos():
    return _json_cacheado(sitedata.path("photos.json"), {})


def _load_featured_sprite():
    return _json_cacheado(sitedata.path("featured-medic-sprite.json"), {})


def _load_doctor_photo_sprite():
    return _json_cacheado(sitedata.path("doctor-photo-sprite.json"), {})


def _load_doctor_sprite():
    return _json_cacheado(os.path.join(ROOT, "data", "doctor-sprite.json"), {})


def _sprite_style(manifest, entry, display):
    image = entry.get("image") or manifest.get("image") or ""
    tile = int(manifest.get("tile") or entry.get("tile") or 0)
    x = int(entry.get("x", 0))
    y = int(entry.get("y", 0))
    if not image or not tile:
        return {}
    width = int(manifest.get("width") or 0)
    height = int(manifest.get("height") or 0)
    if not width or not height:
        columns = int(manifest.get("columns") or 1)
        count = int(manifest.get("count") or len(manifest.get("entries") or {}) or 1)
        width = columns * tile
        height = math.ceil(count / columns) * tile
    scale = display / tile
    return {
        "image": image,
        "bg_size": f"{width * scale:.3f}px {height * scale:.3f}px",
        "bg_pos": f"{-x * scale:.3f}px {-y * scale:.3f}px",
    }


ESP_GROUPS = [
    ("Diagnóstico / imagen", re.compile(r"imagen|radiolog|tomograf|ecograf|mamograf|laboratorio|estudios|doppler")),
    ("Cirugía", re.compile(r"cirug")),
    ("Cardiología", re.compile(r"cardio")),
    ("Pediatría", re.compile(r"pediatr|neonat")),
    ("Ginecología y obstetricia", re.compile(r"ginec|obstet|fetal")),
    ("Dermatología", re.compile(r"dermat")),
    ("Oftalmología", re.compile(r"oftalmo")),
    ("Traumatología y kinesiología", re.compile(r"trauma|ortoped|kinesi|columna")),
    ("Neurología", re.compile(r"neurolog")),
    ("Salud mental", re.compile(r"psicolog|psiquiatr|salud mental")),
    ("Clínica y medicina general", re.compile(r"cl[ií]nica m[eé]d|medicina general|generalista|cl[ií]nica general|familiar")),
    ("Endocrinología, diabetes y nutrición", re.compile(r"endocrin|diabet|nutri|metaboli")),
    ("Gastroenterología", re.compile(r"gastro")),
    ("Urología", re.compile(r"urolog|sexolog|nefro")),
    ("Otorrinolaringología y fono", re.compile(r"otorrin|fonoaud|(^|[^a-z])orl([^a-z]|$)")),
    ("Odontología", re.compile(r"odontolog|maxilofacial|implante|odontograma")),
    ("Oncología y hematología", re.compile(r"oncolog|hematolog")),
]


def _esp_group_one(part):
    s = (part or "").lower()
    for name, rx in ESP_GROUPS:
        if rx.search(s):
            return name
    return None


def _doctor_groups(esp):
    out = set()
    for part in str(esp or "").split("/"):
        group = _esp_group_one(part.strip())
        if group:
            out.add(group)
    if not out:
        out.add("Otras especialidades")
    return out


_ESP_ABBR = {
    "inf": "infantil", "juv": "juvenil", "adu": "adultos", "ado": "adolescentes",
    "obst": "obstetricia", "ginec": "ginecología", "trauma": "traumatología",
    "gral": "general", "ped": "pediatría", "cardio": "cardiología",
    "neuro": "neurología", "dermato": "dermatología", "guv": "general",
}


def _norm_esp_part(part):
    words = []
    for word in str(part or "").split():
        lower = word.lower().strip(".")
        words.append(_ESP_ABBR.get(lower, lower))
    text = " ".join(words).strip()
    return text[:1].upper() + text[1:] if text else text


def normalize_esp(esp):
    parts = [part for part in re.split(r"\s*/\s*", str(esp or "")) if part.strip()]
    return " / ".join(_norm_esp_part(part) for part in parts)


RATINGS = {
    "Pinamed": 2.8, "DIMAP": 4.9, "Cefem": 4.4, "CEM": 4.2,
    "CEMPI Centro Médico Pinamar": 4.1, "Centro EQIS": 4.5, "Kinergia": 4.7,
    "Policlínica Madariaga": 3.9,
}


def _rating_of(clinica):
    return RATINGS.get(clinica, 4.0)


PROF_GROUPS = [
    ("Odontólogo/a", re.compile(r"odontolog|maxilofacial|implante|odontograma")),
    ("Kinesiólogo/a", re.compile(r"kinesi|fisioterap")),
    ("Psicólogo/a", re.compile(r"psicolog")),
    ("Nutricionista", re.compile(r"nutri")),
    ("Fonoaudiólogo/a", re.compile(r"fonoaud")),
    ("Obstétrica", re.compile(r"obstetr|partera")),
]


def _prof_of(esp):
    text = str(esp or "").lower()
    for name, rx in PROF_GROUPS:
        if rx.search(text):
            return name
    return "Médico/a"


def _slugify(text):
    text = str(text or "").lower().strip()
    repl = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
    text = "".join(repl.get(char, char) for char in text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "medico"


def _obra_popularity(medicos):
    counts = {}
    for medico in medicos or []:
        for obra in medico.get("obras_sociales") or []:
            key = str(obra).strip().lower()
            if not key or "todas" in key or "particular" in key:
                continue
            counts[key] = counts.get(key, 0) + 1
    order = sorted(counts, key=lambda key: (-counts[key], key))
    return {key: index for index, key in enumerate(order)}


def _obra_label(obras, pop=None):
    pop = pop or {}
    vals = [str(obra).strip() for obra in (obras or []) if str(obra).strip()]
    low = [value.lower() for value in vals]
    if any("todas" in value for value in low):
        return ("Cualquier prepaga", True)

    def _key(value):
        lowered = value.lower()
        return (1 if "particular" in lowered else 0, pop.get(lowered, 10_000), lowered)

    vals.sort(key=_key)
    if vals:
        shown = vals[0] if len(vals) == 1 else f"{vals[0]} +{len(vals) - 1}"
        return (shown, True)
    return ("Particular", False)


_COPAGO_RE = re.compile(r"co-?pago|co-?seguro|coseguro", re.I)
_COPAGO_MONTO_RE = re.compile(r"(?:co-?pago|co-?seguro|coseguro)[^$0-9]{0,40}\$\s*([\d][\d.\s]*\d|\d)", re.I)
_TODAS_TEXT_RE = re.compile(r"\btodas\s+las\s+(?:obras?\s+sociales?|os|o\.?\s*s\.?)\b", re.I)


def _copago_info(medico):
    blob = f"{medico.get('mensaje') or ''}  {medico.get('pie') or ''}"
    if not _COPAGO_RE.search(blob):
        return (False, None)
    monto = None
    match = _COPAGO_MONTO_RE.search(blob)
    if match:
        try:
            amount = int(re.sub(r"[.\s]", "", match.group(1)))
        except ValueError:
            amount = 0
        if 1000 <= amount <= 2_000_000:
            monto = "$" + f"{amount:,}".replace(",", ".")
    return (True, monto)


def _acepta_todas(medico, raw_obras):
    if any("todas" in obra.lower() or "cualquier" in obra.lower() for obra in raw_obras):
        return True
    blob = f"{medico.get('mensaje') or ''}  {medico.get('pie') or ''}  {medico.get('horario') or ''}"
    return bool(_TODAS_TEXT_RE.search(blob))


def _display_name(nombre):
    if "," in nombre:
        parts = nombre.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return nombre


_FEMALE_NAMES = {
    'maria', 'ana', 'andrea', 'claudia', 'patricia', 'cristina', 'silvia', 'graciela',
    'gabriela', 'cecilia', 'miriam', 'laura', 'rosa', 'norma', 'susana', 'alejandra',
    'marina', 'natalia', 'valeria', 'marcela', 'beatriz', 'martha', 'marta', 'virginia',
    'paula', 'carolina', 'lorena', 'romina', 'liliana', 'elena', 'diana', 'lucia',
    'sandra', 'veronica', 'adriana', 'monica', 'analia', 'karina', 'irene', 'paola',
    'florencia', 'daniela', 'noemi', 'stella', 'silvana', 'soledad', 'viviana', 'ivana',
    'eva', 'bibiana', 'elisa', 'ines', 'isabel', 'leonor', 'lilia', 'luisa', 'mabel',
    'nilda', 'olga', 'sara', 'alicia', 'estela', 'gladys', 'haydee', 'hilda', 'nora',
    'lilian', 'magali', 'carina', 'pamela', 'victoria', 'alejandrina', 'celeste',
    'sabrina', 'mariana', 'pilar', 'agustina', 'camila', 'antonella', 'valentina',
    'sofia', 'luciana', 'micaela', 'malena', 'melisa', 'renata', 'brenda', 'cintia',
    'noelia', 'vanesa', 'barbara', 'gisella', 'yamila',
}


def _guess_gender(nombre):
    display = _display_name(nombre)
    first = _slugify(display.split()[0]) if display.split() else ''
    first_clean = first.rstrip('s').rstrip('-')
    if first_clean in _FEMALE_NAMES or first in _FEMALE_NAMES:
        return 'f'
    if first.endswith('a') and first not in ('matia', 'elba', 'luca', 'andrea'):
        return 'f'
    return 'm'


EXCLUDED_CLINICS = {
    "Consultorios Madariaga", "Medicina Integral Madariaga", "Neumología Madariaga",
    "Policlínica Madariaga", "Modo Spa - Masaje y\xa0Cosmetología",
}


def _turno_friendly(pt):
    try:
        date = datetime.strptime(str(pt)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    days = (date.date() - datetime.now().date()).days
    time = date.strftime("%H:%M")
    if days <= 0:
        return "Hoy " + time
    if days == 1:
        return "Mañana " + time
    if days < 7:
        return ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][date.weekday()].capitalize() + " " + time
    return ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][date.weekday()].capitalize() + " " + date.strftime("%d/%m") + " · " + time


def public_medicos():
    try:
        db = _load_db()
    except (OSError, json.JSONDecodeError):
        return []
    medicos_raw = db.get("medicos") or []
    photos = _load_photos()
    sprite_manifest = _load_featured_sprite()
    sprite_entries = sprite_manifest.get("entries") or {}
    sprite_source = sprite_manifest.get("source") or ""
    sprite_source_tile = int(sprite_manifest.get("source_tile") or 0)
    photo_sprite = _load_doctor_photo_sprite()
    photo_sprite_entries = photo_sprite.get("entries") or {}
    doctor_sprite = _load_doctor_sprite()
    doctor_sprite_img = doctor_sprite.get("image") or ""
    doctor_sprite_entries = doctor_sprite.get("entries") or {}
    pop = _obra_popularity(medicos_raw)
    out, seen = [], {}
    for medico in medicos_raw:
        nombre = (medico.get("nombre") or "").strip()
        if not nombre:
            continue
        clinica = (medico.get("clinica") or "").strip()
        if clinica in EXCLUDED_CLINICS:
            continue
        esp_raw = (medico.get("especialidad") or "").strip()
        esp = normalize_esp(esp_raw) or "Otras especialidades"
        slug = _slugify(nombre)
        if slug in seen:
            slug = slug + "-" + _slugify(clinica or str(seen[slug]))
        seen[slug] = seen.get(slug, 0) + 1
        label, takes = _obra_label(medico.get("obras_sociales"), pop)
        raw_obras = [str(obra).strip() for obra in (medico.get("obras_sociales") or []) if str(obra).strip()]
        acepta_todas = _acepta_todas(medico, raw_obras)
        copago, copago_monto = _copago_info(medico)
        obras = [obra for obra in raw_obras if "--" not in obra and "todas" not in obra.lower()
                 and "cualquier" not in obra.lower() and not obra.lower().startswith("particular")]
        obras.sort(key=lambda obra: pop.get(obra.lower(), 999))
        display = _display_name(nombre)
        gender = _guess_gender(nombre)
        groups_raw = _doctor_groups(esp_raw)
        primary_group = sorted(groups_raw)[0] if groups_raw else esp
        esp_slug = _slugify(primary_group)
        item = {
            "nombre": nombre, "slug": slug, "especialidad": esp, "especialidad_raw": esp_raw,
            "grupos": sorted(groups_raw), "profesion": _prof_of(esp_raw),
            "clinica": clinica, "clinica_slug": _slugify(clinica), "rating": _rating_of(clinica),
            "obra_label": label, "obra_ok": takes, "acepta_todas": acepta_todas,
            "copago": copago, "copago_monto": copago_monto, "online": bool(medico.get("online")),
            "foto": photos.get(nombre, ""), "obras_sociales": obras,
            "proximo_turno": medico.get("proximo_turno"), "proximo_turno_fmt": medico.get("proximo_turno_fmt"),
            "turnos_libres": medico.get("turnos_libres", 0) or 0, "esp_slug": esp_slug,
            "display_name": display, "gender": gender, "doc_url": f"/{esp_slug}/{slug}",
        }
        photo_entry = photo_sprite_entries.get(slug)
        if photo_entry:
            item["profile_sprite"] = _sprite_style(photo_sprite, photo_entry, 100)
            item["avatar"] = _sprite_style(photo_sprite, photo_entry, 34)
            item["avatar32"] = _sprite_style(photo_sprite, photo_entry, 32)
            item["avatar36"] = _sprite_style(photo_sprite, photo_entry, 36)
            if photo_entry.get("zoom"):
                item["photo_zoom"] = photo_entry.get("zoom")
        entry = sprite_entries.get(slug)
        if "profile_sprite" not in item and sprite_source and sprite_source_tile and entry:
            col = int(entry.get("x", 0)) // int(sprite_manifest.get("tile") or 96)
            row = int(entry.get("y", 0)) // int(sprite_manifest.get("tile") or 96)
            profile_size = 100
            scale = profile_size / sprite_source_tile
            source_cols = int(sprite_manifest.get("columns") or 8)
            source_w = source_cols * sprite_source_tile
            source_h = math.ceil((int(sprite_manifest.get("count") or 0) or 1) / source_cols) * sprite_source_tile
            item["profile_sprite"] = {"image": sprite_source, "bg_size": f"{source_w * scale:.3f}px {source_h * scale:.3f}px", "bg_pos": f"{-(col * profile_size):.3f}px {-(row * profile_size):.3f}px"}
        avatar = doctor_sprite_entries.get(slug)
        if "avatar" not in item and doctor_sprite_img and avatar:
            item["avatar"] = {"image": avatar.get("image") or doctor_sprite_img, "bg_size": avatar.get("bg_size"), "bg_pos": avatar.get("bg_pos")}
        out.append(item)
    out.sort(key=lambda doctor: doctor["nombre"].lower())
    return out


def medico_by_url(esp_slug, name_slug):
    esp_slug = str(esp_slug or '').strip().lower()
    name_slug = str(name_slug or '').strip().lower()
    for medico in public_medicos():
        if medico['esp_slug'] == esp_slug and medico['slug'] == name_slug:
            return medico
    return None


def medicos_by_esp(esp_slug):
    esp_slug = str(esp_slug or '').strip().lower()
    return [medico for medico in public_medicos() if medico['esp_slug'] == esp_slug]


def _load_clinicas():
    try:
        with open(sitedata.path("clinicas.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _canonical_clinic_slug(clinic_slug, clinics_db=None):
    clinics_db = clinics_db if clinics_db is not None else _load_clinicas()
    current, seen = str(clinic_slug or '').strip().lower(), set()
    while current and current not in seen:
        seen.add(current)
        alias = (clinics_db.get(current) or {}).get("alias_of")
        if not alias:
            break
        current = str(alias).strip().lower()
    return current


def all_clinicas():
    clinics_db, seen = _load_clinicas(), {}
    for medico in public_medicos():
        clinica = medico['clinica']
        slug = _canonical_clinic_slug(_slugify(clinica), clinics_db)
        info = clinics_db.get(slug) or {}
        if slug not in seen:
            seen[slug] = {'name': info.get('name') or clinica, 'slug': slug, 'count': 0,
                          'rating': info.get('google_rating') or info.get('rating') or medico['rating']}
        seen[slug]['count'] += 1
    for slug, info in clinics_db.items():
        if info.get('alias_de'):
            continue
        seen.setdefault(slug, {'name': info.get('name') or slug.replace('-', ' ').title(), 'slug': slug,
                               'count': 0, 'rating': info.get('google_rating') or info.get('rating') or _rating_of(info.get('name') or '')})
    return sorted(seen.values(), key=lambda clinic: -clinic['rating'])


def all_coberturas():
    seen = {}
    for medico in public_medicos():
        for obra in medico.get('obras_sociales', []):
            if obra and '--' not in obra and 'todas' not in obra.lower() and 'particular' not in obra.lower():
                if obra not in seen:
                    seen[obra] = {'name': obra, 'slug': _slugify(obra), 'count': 0}
                seen[obra]['count'] += 1
    return sorted(seen.values(), key=lambda coverage: -coverage['count'])


def especialidades_tree():
    try:
        db = _load_db()
    except (OSError, json.JSONDecodeError):
        return {"total_medicos": 0, "con_online": 0, "groups": [], "clinicas": [], "profesiones": []}
    medicos, cats, clin, prof = db.get("medicos") or [], {}, {}, {}
    con_online = 0
    for medico in medicos:
        if (medico.get("clinica") or "").strip() in EXCLUDED_CLINICS:
            continue
        esp = medico.get("especialidad") or ""
        if medico.get("online"):
            con_online += 1
        for group in _doctor_groups(esp):
            cats.setdefault(group, {"count": 0, "subs": {}})["count"] += 1
        for part in str(esp).split("/"):
            part = part.strip()
            if not part:
                continue
            group = _esp_group_one(part) or "Otras especialidades"
            sub = normalize_esp(part)
            category = cats.setdefault(group, {"count": 0, "subs": {}})
            category["subs"][sub] = category["subs"].get(sub, 0) + 1
        clinica = (medico.get("clinica") or "").strip()
        if clinica:
            clin[clinica] = clin.get(clinica, 0) + 1
        profession = _prof_of(esp)
        prof[profession] = prof.get(profession, 0) + 1
    groups = []
    for name, info in cats.items():
        subs = [{"name": sub_name, "count": sub_count} for sub_name, sub_count in info["subs"].items() if sub_name.lower() != name.lower()]
        subs.sort(key=lambda sub: (-sub["count"], sub["name"]))
        groups.append({"name": name, "count": info["count"], "subs": subs})
    groups.sort(key=lambda group: (-group["count"], group["name"]))
    group_names = {group["name"] for group in groups}
    faces = {name: [] for name in group_names}
    faces_total = {name: 0 for name in group_names}
    waits = {name: [] for name in group_names}
    today = datetime.now().date()
    for medico in public_medicos():
        try:
            turn_date = datetime.strptime(str(medico.get("proximo_turno") or "")[:16], "%Y-%m-%d %H:%M").date()
            wait_days = max(0, (turn_date - today).days)
        except ValueError:
            wait_days = None
        for group_name in medico.get("grupos") or []:
            if group_name not in group_names:
                continue
            if wait_days is not None:
                waits[group_name].append(wait_days)
            sprite = medico.get("avatar") or medico.get("profile_sprite")
            if sprite or medico.get("foto"):
                faces_total[group_name] += 1
                if len(faces[group_name]) < 6:
                    faces[group_name].append({"nombre": medico["nombre"], "slug": medico["slug"], "foto": medico.get("foto", ""), "sprite": sprite})
    for group in groups:
        name = group["name"]
        group["fotos"] = faces.get(name, [])
        group["fotos_total"] = faces_total.get(name, 0)
        if waits.get(name):
            group["wait_days"] = int(round(statistics.median(waits[name])))
    clinics_db, clinicas = _load_clinicas(), []
    for name, count in clin.items():
        slug = _canonical_clinic_slug(_slugify(name), clinics_db)
        info = clinics_db.get(slug) or {}
        raw_rating = info.get('google_rating')
        if raw_rating is None or raw_rating == '?':
            rating = _rating_of(name)
        else:
            try:
                rating = float(raw_rating)
            except (ValueError, TypeError):
                rating = _rating_of(name)
        clinicas.append({"name": name, "count": count, "rating": rating, "maps_url": info.get('google_maps_url') or ''})
    clinicas.sort(key=lambda clinic: (-clinic["rating"], clinic["name"]))
    profesiones = [{"name": name, "count": count} for name, count in prof.items()]
    profesiones.sort(key=lambda profession: (-profession["count"], profession["name"]))
    total = sum(1 for medico in medicos if (medico.get("clinica") or "").strip() not in EXCLUDED_CLINICS)
    return {"total_medicos": total, "con_online": con_online, "groups": groups,
            "clinicas": clinicas, "profesiones": profesiones, "coberturas": all_coberturas()}
