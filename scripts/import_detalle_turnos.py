#!/usr/bin/env python3
"""Importa el "Detalle de turnos" oficial del hospital (xlsx de reference-deployment counterpart,
2026-07-12) a servicios.json + medicos.json (configured private town pack)
+ especialidades nuevas en
especialidades.json (packs/global/). Resuelve cada uno vía sitedata.path().

Fuente: <private-town-pack>/fuentes/detalle-turnos-2026-07-12.xlsx — 145 filas, una por
(prestación, efector, profesional, franja). Mapeo:

  servicio  = grupo (Prestación, Efector). Modalidad→modo: espontáneo/grupo →
              walkin[_con_orden]; turno programado → solicitud[_con_orden]
              según "Requiere derivación/Orden". Grupo etario→edad, palabras
              clave→palabras (merge), consultorio, horario compuesto de las
              franjas (parseable por horarios.js), agenda real en modos
              solicitud* (single-window si es uniforme, {franjas} si no).
  medico    = grupo (Profesional). Franjas por centro (forma canónica
              centros[] + centro/agenda planos, igual que catalog.py);
              especialidad canonizada contra especialidades.json.

Los servicios/médicos existentes se actualizan (match por centro + nombre
normalizado / tokens del nombre) preservando lo curado (id, top, cluster,
categoria, pami, jtbd, sede, fotos); los que el hospital no mandó quedan como
están (el mail dice "primera parte de la información").

Uso: python3 scripts/import_detalle_turnos.py [--dry]
"""
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.core import data_packs, sitedata  # noqa: E402


def _argval(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


# --file <ruta> y --sheet <hoja> permiten importar planillas nuevas (la de
# 2026-07-14 "completo" trae dos hojas; Hoja2 es la versión final curada).
XLSX = _argval(
    "--file",
    os.path.join(
        data_packs.private_pack_dir("ar-b-pinamar"),
        "fuentes",
        "detalle-turnos-2026-07-12.xlsx",
    ),
)
SHEET = _argval("--sheet", "Turnos")
# "detalle-turnos-completo-2026-09-07.xlsx" -> "Detalle de turnos 07-09-26"
_m = re.search(r"(\d{4})-(\d{2})-(\d{2})", os.path.basename(XLSX))
FUENTE_NOMBRE = ("Detalle de turnos " + (f"{_m.group(3)}-{_m.group(2)}-{_m.group(1)[2:]}" if _m else "")
                 + " (Secretaría de Salud)").replace("  ", " ")
DRY = "--dry" in sys.argv

EFECTOR_SLUG = {
    "CAPS Ostende": "caps-ostende",
    # nuevo CAPS (mail de la contraparte técnica 2026-09-07): Esparza y Pueyrredón, Ostende.
    "Centro de Salud Integral de Ostende": "caps-integral-ostende",
    "CAPS Valeria del Mar": "caps-valeria",
    "Hospital Comunitario de Pinamar": "hospital-pinamar",
    "CAPS Pinamar": "caps-pinamar",
    "DiabetPin": "diabetpin",
    # sedes de CAPS Ostende (no son centros propios): el efector es la sede.
    "Centro Cultural de Ostende": "caps-ostende",
    "Desarrollo Infantil": "caps-ostende",
}

# Efectores que en realidad son una SEDE dentro de otro centro (arriba mapeados
# al centro): el nombre del efector queda como `sede` del servicio para
# distinguir filas del mismo centro (ver invariante `sede` en CLAUDE.md).
EFECTOR_SEDE = {
    "Centro Cultural de Ostende": "Centro Cultural de Ostende",
    "Desarrollo Infantil": "Desarrollo Infantil",
}

# Efectores que NO son un centro sino una página informativa de enlaces.json
# (2026-09-07: "Telemedicina" viene como fila propia con sus palabras clave).
# Sus palabras se vuelcan en `palabras` del enlace; las palabras de la fila
# EXCLUSION_DE[enlace] (la guardia: lo que NO va a telemedicina) se vuelcan en
# `palabras_excluidas`.
EFECTOR_ENLACE = {"Telemedicina": "telemedicina"}
EXCLUSION_DE = {"telemedicina": ("Guardia - Emergencias", "hospital-pinamar")}

# especialidades del xlsx que faltan en el catálogo canónico -> cluster
ESPECIALIDADES_NUEVAS = {
    "Cirugía": "otras",
    "Dermatología": "otras",
    "Enfermería": "vacunas-prevencion",
    "Gastroenterología": "otras",
    "Infectología": "otras",
    "Infectología infantil": "pediatria",
    "Laboratorio": "diagnostico",
    "Lic. Psicomotricidad": "salud-mental",
    "Medicina General - Trabajo Social": "clinica",
    "Neumología": "otras",
    "Neurología": "otras",
    "Neurología infantil": "pediatria",
    "Oftalmología": "otras",
    "Terapia ocupacional": "salud-mental",
    "Urología": "otras",
    "Psicomotricidad": "salud-mental",
    "Psicopedagogía": "salud-mental",
    # "Demanda espontánea" aparece en la columna Especialidad de filas de
    # medicina general por demanda espontánea; no es una especialidad, pero
    # canon_esp necesita mapearla (el título sale de Prestación, no de acá).
    "Demanda espontánea": "clinica",
    "Trabajo Social": "clinica",
}

# nombres del xlsx que son la misma especialidad canónica con otro nombre
ESPECIALIDAD_ALIAS = {"clinica medica": "Medicina general"}  # claves normalizadas

GRUPO_ETARIO_EDAD = {
    "todos": (None, None),
    "ninos": (None, 14),
    "menores de 15 anos": (None, 14),
    "mayor de 16 anos": (16, None),
    "adultos": (18, None),
    "adultos mayores": (60, None),
    "adolescentes": (13, 17),
    "adolescentes y adultos": (13, None),
    "gestantes": (None, None),  # no es edad; queda en el cartel
}

DIA_NUM = {"domingo": 0, "lunes": 1, "martes": 2, "miercoles": 3,
           "jueves": 4, "viernes": 5, "sabado": 6}


def norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.lower()).strip()


def slug(s):
    s = norm(s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def parse_dias(raw):
    """'Lunes a Viernes' / 'Miércoles y viernes' / 'Martes' -> [getDay 0..6]."""
    t = norm(raw)
    if not t or "orden de derivacion" in t:
        return None
    m = re.match(r"^([a-z]+) a ([a-z]+)$", t)
    if m and m.group(1) in DIA_NUM and m.group(2) in DIA_NUM:
        a, b = DIA_NUM[m.group(1)], DIA_NUM[m.group(2)]
        order = [1, 2, 3, 4, 5, 6, 0]
        ia, ib = order.index(a), order.index(b)
        return [order[i % 7] for i in range(ia, ib + 1 if ib >= ia else ib + 8)]
    dias = []
    for tok in re.split(r",| y ", t):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in DIA_NUM:
            return None
        dias.append(DIA_NUM[tok])
    return sorted(set(dias)) or None


def hhmm(t):
    if t is None:
        return None
    if isinstance(t, str):
        t = t.strip()
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?$", t)
        return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else None
    if isinstance(t, (int, float)):  # Excel guarda horas como fracción del día
        mins = round(float(t) * 24 * 60)
        return f"{mins // 60:02d}:{mins % 60:02d}"
    return t.strftime("%H:%M")


def hora_texto(t):
    """'08:00'->'8', '15:30'->'15:30' (formato de los horarios existentes)."""
    if not t:
        return ""
    h, m = t.split(":")
    return str(int(h)) if m == "00" else f"{int(h)}:{m}"


def leer_filas():
    ws = openpyxl.load_workbook(XLSX, data_only=True)[SHEET]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h).strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(r):
            continue
        d = dict(zip(hdr, r))
        efector = str(d["Efector"]).strip()
        if efector not in EFECTOR_SLUG and efector not in EFECTOR_ENLACE:
            raise SystemExit(f"efector sin mapear: {efector!r} (agregalo a EFECTOR_SLUG)")
        out.append({
            "prestacion": str(d["Prestación"]).strip(),
            "centro": EFECTOR_SLUG.get(efector),
            "enlace": EFECTOR_ENLACE.get(efector),
            "sede": EFECTOR_SEDE.get(efector),
            "modalidad": norm(d["Modalidad"]),
            "acceso": str(d["Modo de acceso"] or "").strip(),
            "dias_raw": str(d["Días"] or "").strip(),
            "dias": parse_dias(d["Días"]),
            "desde": hhmm(d["Hora inicio"]),
            "hasta": hhmm(d["Hora fin"]),
            "etario": norm(d["Grupo etario"]),
            "orden": norm(d["Requiere derivación/Orden"]) in ("si", "orden medica"),
            "profesional": str(d["Profesional"]).strip() if d["Profesional"] else None,
            "especialidad": str(d["Especialidad"]).strip() if d["Especialidad"] else "",
            "consultorio": d["Consultorio"],
            "descripcion": str(d["Descripción breve"] or "").strip(),
            "palabras": [p.strip().lower() for p in re.split(r"[;,]", str(d["Palabras clave / motivos"] or "")) if p.strip()],
        })
    return out


def especialidad_de(fs):
    """Especialidad del grupo; si la planilla la deja vacía (estudios de
    diagnóstico, talleres, admisiones), se deriva de la prestación."""
    for f in fs:
        e = str(f.get("especialidad") or "").strip()
        if e:
            return e
    p = norm(fs[0]["prestacion"])
    if any(k in p for k in ("ecodoppler", "ecocardio", "ecografia", "electrocardiograma",
                            "doppler", "radiograf", "tomograf", "holter", "ergometr", "rx")):
        return "Diagnóstico por imágenes"
    if "trabajo social" in p:
        return "Medicina General - Trabajo Social"
    if "psicolog" in p or "taller" in p:
        return "Psicología"
    return "Medicina General"


def modo_de(filas):
    modalidad = filas[0]["modalidad"]
    con_orden = any(f["orden"] for f in filas)
    if modalidad.startswith("espontaneo") or modalidad.startswith("grupo"):
        return "walkin_con_orden" if con_orden else "walkin"
    return "solicitud_con_orden" if con_orden else "solicitud"


def ventanas_de(filas):
    """Franjas distintas (dias, desde, hasta) con días parseables."""
    seen, out = set(), []
    for f in filas:
        if not (f["dias"] and f["desde"] and f["hasta"]) or f["desde"] >= f["hasta"]:
            continue
        key = (tuple(f["dias"]), f["desde"], f["hasta"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"dias": f["dias"], "desde": f["desde"], "hasta": f["hasta"],
                    "cada_min": 20, "_raw": f["dias_raw"]})
    return out


def horario_texto(ventanas):
    partes = []
    for v in ventanas:
        dias = v["_raw"].strip().capitalize()
        partes.append(f"{dias} de {hora_texto(v['desde'])} a {hora_texto(v['hasta'])} h")
    return "; ".join(dict.fromkeys(partes)) or None


def es_boilerplate(desc):
    # "Cardiología en CAPS Ostende. Modalidad: Turno programado." = autogenerado
    return bool(desc) and len(desc) < 140 and "Modalidad:" in desc and "\n" not in desc


def match_nombre(nombre):
    """Nombre normalizado para matchear contra los servicios existentes:
    saca los sufijos de modo/urgencia que el título limpio no lleva."""
    t = norm(nombre)
    for suf in (" - demanda espontanea", " sin turno", " (grupo)", " - emergencias"):
        t = t.replace(suf, "")
    t = re.sub(r"^consultorio (de )?", "", t)
    # "Infantil / Desarrollo" y "Infantil/Desarrollo" son el mismo nombre: sin
    # esto la variante con espacios alrededor de la barra creaba un duplicado
    # (admision-psicologia-infantil-*-valeria, 2026-07-19).
    t = re.sub(r"\s*/\s*", "/", t)
    # "Clínica médica" en el hospital es el servicio existente "Medicina
    # General" (misma agenda; 2026-09-07 la planilla lo renombró).
    t = t.replace("clinica medica", "medicina general")
    return t.strip(" -")


def titulo_limpio(nombre):
    """Invariante 'títulos limpios': el nombre no repite el modo (eso ya lo
    dice la columna Turno); lo que se saca queda cubierto por `palabras`."""
    t = re.sub(r"\s*-\s*Demanda espontánea$", "", nombre, flags=re.I)
    t = re.sub(r"^Demanda espontánea\s*-\s*", "", t, flags=re.I)
    t = re.sub(r"\s+sin turno$", "", t, flags=re.I)
    t = re.sub(r"\s*\(grupo\)$", "", t, flags=re.I)
    return t.strip()


def cartel_template(modo, horario):
    h = f": {horario[0].lower()}{horario[1:]}." if horario else "."
    if modo == "walkin":
        return "No necesitás turno. Se atiende por orden de llegada" + h
    if modo == "walkin_con_orden":
        return "Vení con tu orden médica. Se atiende por orden de llegada" + h
    if modo == "solicitud":
        return "Pedí tu turno acá y el hospital te lo confirma. Se atiende" + (h if horario else " en el centro.")
    return "Necesitás una orden de derivación. Subí la orden acá y el hospital te confirma el turno."


def categoria_de(filas, esp):
    if filas[0]["modalidad"].startswith("grupo"):
        return "grupos"
    if esp in ("Laboratorio", "Diagnóstico por imágenes", "Ecografía"):
        return "estudios"
    kw = norm(filas[0]["prestacion"])
    if re.search(r"ecograf|ecocard|ergometr|holter|electrocard|radiograf|laborat|mamograf|rayos", kw):
        return "estudios"
    if esp in ("Enfermería", "Vacunación"):
        return "consultorios"
    return "especialidades"


def sync_enlaces(filas):
    """Telemedicina (y futuros enlaces informativos): palabras clave del xlsx
    -> enlaces.json `palabras`; palabras de la fila de exclusión (guardia) ->
    `palabras_excluidas`. Merge, nunca borra lo curado."""
    with open(sitedata.path("enlaces.json")) as f:
        doc = json.load(f)
    enlaces = doc["enlaces"] if isinstance(doc, dict) else doc
    rep = []
    for f in filas:
        eid = f.get("enlace")
        if not eid:
            continue
        e = next((x for x in enlaces if x.get("id") == eid), None)
        if e is None:
            raise SystemExit(f"enlace {eid!r} no existe en enlaces.json")
        antes = len(e.get("palabras", []))
        e["palabras"] = list(dict.fromkeys([p.lower() for p in e.get("palabras", [])] + f["palabras"]))
        excl_key = EXCLUSION_DE.get(eid)
        if excl_key:
            excl = [p for g in filas if (g["prestacion"], g["centro"]) == excl_key for p in g["palabras"]]
            antes_x = len(e.get("palabras_excluidas", []))
            e["palabras_excluidas"] = list(dict.fromkeys([p.lower() for p in e.get("palabras_excluidas", [])] + excl))
            rep.append(f"{eid}: palabras {antes}->{len(e['palabras'])}, excluidas {antes_x}->{len(e['palabras_excluidas'])}")
        else:
            rep.append(f"{eid}: palabras {antes}->{len(e['palabras'])}")
    return doc, rep


def main():
    filas = leer_filas()
    enl_doc, enl_rep = sync_enlaces(filas)
    filas = [f for f in filas if f["centro"]]

    with open(sitedata.path("especialidades.json")) as f:
        esp_doc = json.load(f)
    esp_cluster = {norm(e["nombre"]): e["cluster"] for e in esp_doc["specialties"]}
    esp_canon = {norm(e["nombre"]): e["nombre"] for e in esp_doc["specialties"]}
    agregadas = []
    for nombre, cluster in ESPECIALIDADES_NUEVAS.items():
        if norm(nombre) not in esp_cluster:
            esp_doc["specialties"].append({"nombre": nombre, "cluster": cluster})
            esp_cluster[norm(nombre)] = cluster
            esp_canon[norm(nombre)] = nombre
            agregadas.append(nombre)

    sin_mapear = sorted({str(f["especialidad"]).strip() for f in filas
                         if f["especialidad"] and norm(ESPECIALIDAD_ALIAS.get(norm(f["especialidad"]), f["especialidad"])) not in esp_canon})
    if sin_mapear:
        raise SystemExit(f"especialidades sin mapear (agregalas a ESPECIALIDADES_NUEVAS o ESPECIALIDAD_ALIAS): {sin_mapear}")

    def canon_esp(raw):
        n = norm(ESPECIALIDAD_ALIAS.get(norm(raw), raw))
        if n not in esp_canon:
            raise SystemExit(f"especialidad sin mapear: {raw!r}")
        return esp_canon[n], esp_cluster[n]

    # ---------------- servicios ----------------
    with open(sitedata.path("servicios.json")) as f:
        svc_doc = json.load(f)
    servicios = svc_doc["servicios"]
    por_clave = defaultdict(list)
    for s in servicios:
        por_clave[(s["centro"], match_nombre(s["nombre"]))].append(s)

    # Agrupo por título normalizado + centro + modo: "Pediatría" y
    # "Consultorio Pediatría" (mismo CAPS, mismo modo) son UN servicio con más
    # profesionales, no dos (2026-09-07). Modos distintos siguen separados
    # ("Odontología" vs "Odontología sin turno").
    grupos = defaultdict(list)
    for f in filas:
        grupos[(match_nombre(f["prestacion"]), f["centro"], modo_de([f]))].append(f)

    rep = {"svc_match": [], "svc_nuevo": [], "svc_franjas_multi": [], "med_match": [], "med_nuevo": []}
    ids_usados = {s["id"] for s in servicios}
    reclamados = set()  # existentes ya tomados por un grupo del xlsx

    for (clave, centro, _modo), fs in sorted(grupos.items()):
        modo = modo_de(fs)
        esp, cluster = canon_esp(especialidad_de(fs))
        ventanas = ventanas_de(fs)
        # título: la variante más corta (sin "Consultorio …")
        prestacion = min({f["prestacion"] for f in fs}, key=len)
        nombre = titulo_limpio(prestacion)
        edad = GRUPO_ETARIO_EDAD.get(fs[0]["etario"], (None, None))
        palabras = sorted(dict.fromkeys(p for f in fs for p in f["palabras"]))
        descs = sorted((f["descripcion"] for f in fs if f["descripcion"] and not es_boilerplate(f["descripcion"])),
                       key=len, reverse=True)
        consultorios = sorted({str(f["consultorio"]).strip() for f in fs if f["consultorio"] is not None})

        cands = [x for x in (por_clave.get((centro, clave)) or [])
                 if x["id"] not in reclamados]
        # dos prestaciones distintas pueden normalizar igual ("Odontología" y
        # "Odontología sin turno"): elegí la del mismo modo si existe.
        s = next((x for x in cands if x.get("modo") == modo), cands[0] if len(cands) == 1 else None)
        if s is None and cands:
            s = min(cands, key=lambda x: x.get("modo") != modo)
        if s is not None:
            reclamados.add(s["id"])

        nuevo = s is None
        if nuevo:
            sid = slug(nombre) + "-" + centro.replace("caps-", "").replace("hospital-", "")
            while sid in ids_usados:
                sid += "-2"
            ids_usados.add(sid)
            s = {"id": sid, "nombre": nombre, "centro": centro,
                 "cluster": cluster, "categoria": categoria_de(fs, especialidad_de(fs))}
            servicios.append(s)
            rep["svc_nuevo"].append(f"{sid} ({modo})")
        else:
            rep["svc_match"].append(f"{s['id']}: {s.get('modo')} -> {modo}")

        modo_cambio = s.get("modo") != modo
        s["modo"] = modo
        sede = next((f["sede"] for f in fs if f.get("sede")), None)
        if sede:
            s["sede"] = sede
        # "Todos"/vacío en Grupo etario = sin especificar, NO "sin límite": no
        # borrar un límite curado (p.ej. "Ecografía infantil" max 15 que la
        # planilla marca "Todos" por error). Sólo pisa si el sheet trae edad.
        prev_edad = s.get("edad") or {}
        if edad != (None, None) or prev_edad.get("min") is None and prev_edad.get("max") is None:
            s["edad"] = {"min": edad[0], "max": edad[1]}
        s["palabras"] = sorted(dict.fromkeys([p.lower() for p in s.get("palabras", [])] + palabras))
        ht = horario_texto(ventanas)
        if ht:
            s["horario"] = ht
        if descs:
            s["cartel"] = descs[0]
        elif not s.get("cartel") or modo_cambio:
            # un cartel heredado de otro modo ("No necesitás turno…" en un
            # servicio que ahora es programado) miente — se regenera.
            s["cartel"] = cartel_template(modo, s.get("horario"))
        if consultorios:
            s["consultorio"] = consultorios[0] if len(consultorios) == 1 else " y ".join(consultorios)
        for v in ventanas:
            v.pop("_raw", None)
        if modo in ("solicitud", "solicitud_con_orden") and ventanas:
            if len(ventanas) == 1:
                s["agenda"] = ventanas[0]
            else:
                s["agenda"] = {"franjas": ventanas, "excepciones": []}
                rep["svc_franjas_multi"].append(s["id"])
        else:
            s.pop("agenda", None)  # walk-in NUNCA ofrece turnos (invariante)

    # ---------------- médicos ----------------
    with open(sitedata.path("medicos.json")) as f:
        med_doc = json.load(f)
    medicos = med_doc["medicos"]
    por_tokens = {}
    for m in medicos:
        por_tokens[frozenset(norm(m["nombre"]).split())] = m

    def buscar_medico(prof):
        """Match por tokens del nombre, en cualquier orden; si no hay match
        exacto, por inclusión ('Galvan Martin' ⊆ 'Laura Diana Galvan')."""
        toks = frozenset(norm(prof).split())
        if toks in por_tokens:
            return por_tokens[toks]
        if len(toks) < 2:
            return None
        subset = [m for k, m in por_tokens.items() if toks <= k or k <= toks]
        return subset[0] if len(subset) == 1 else None

    prof_rows = defaultdict(list)
    for f in filas:
        if f["profesional"]:
            prof_rows[f["profesional"]].append(f)

    ids_med = {m["id"] for m in medicos}
    for prof, fs in sorted(prof_rows.items()):
        esp, cluster = canon_esp(especialidad_de(fs))
        m = buscar_medico(prof)
        if m is None:
            mid = slug(prof)
            while mid in ids_med:
                mid += "-2"
            ids_med.add(mid)
            m = {"id": mid, "nombre": prof}
            medicos.append(m)
            por_tokens[frozenset(norm(prof).split())] = m
            rep["med_nuevo"].append(mid)
        else:
            rep["med_match"].append(m["id"])
        m["especialidad"] = esp
        m["cluster"] = cluster
        m["fuente_nombre"] = FUENTE_NOMBRE

        por_centro = defaultdict(list)
        for f in fs:
            for v in ventanas_de([f]):
                v.pop("_raw", None)
                if v not in por_centro[f["centro"]]:
                    por_centro[f["centro"]].append(v)
        if por_centro:
            centros_list = [{"centro": c, "franjas": fr, "excepciones": []}
                            for c, fr in sorted(por_centro.items(), key=lambda kv: -len(kv[1]))]
            m["centros"] = centros_list
            m["centro"] = centros_list[0]["centro"]
            m["agenda"] = {"franjas": centros_list[0]["franjas"], "excepciones": []}
            m.pop("demo", None)
            m.pop("agenda_demo", None)
        elif not m.get("centro"):
            m["centro"] = fs[0]["centro"]

    # ---------------- reporte / escritura ----------------
    print(f"filas: {len(filas)}  grupos servicio: {len(grupos)}  profesionales: {len(prof_rows)}")
    print(f"especialidades agregadas: {len(agregadas)} {agregadas}")
    print(f"servicios actualizados: {len(rep['svc_match'])}")
    for x in rep["svc_match"]:
        print("   ~", x)
    print(f"servicios nuevos: {len(rep['svc_nuevo'])}")
    for x in rep["svc_nuevo"]:
        print("   +", x)
    print(f"servicios con agenda multi-franja (editor admin sólo single): {rep['svc_franjas_multi']}")
    print(f"médicos actualizados: {len(rep['med_match'])}  nuevos: {len(rep['med_nuevo'])}")
    print(f"enlaces: {enl_rep}")

    if DRY:
        print("(dry-run: no se escribió nada)")
        return

    # Resolver TODOS los destinos ANTES de escribir: el pack privado se
    # verifica contra INVENTORY.json en cada lookup, así que el primer write
    # lo invalida y los siguientes caerían a packs/global (pasó 2026-09-07).
    destinos = {f: sitedata.path(f) for f in ("especialidades.json", "servicios.json", "medicos.json", "enlaces.json")}

    def dump(fname, doc):
        with open(destinos[fname], "w") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")

    dump("especialidades.json", esp_doc)
    dump("servicios.json", svc_doc)
    dump("medicos.json", med_doc)
    dump("enlaces.json", enl_doc)
    for fname, dest in destinos.items():
        print(f"   {fname} -> {dest}")
    privado = data_packs.private_packs_dir()
    pack_dir = os.path.dirname(destinos["servicios.json"])
    if privado and pack_dir.startswith(privado):
        data_packs.write_inventory(pack_dir)
        print(f"INVENTORY.json regenerado en {pack_dir}")


if __name__ == "__main__":
    main()
