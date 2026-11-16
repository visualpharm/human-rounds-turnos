#!/usr/bin/env python3
"""
analizar_resenas.py — analiza el corpus de reseñas de Google Maps de cada centro
de salud (<private-town-pack>/fuentes/resenas-raw/<slug>.json, escrito por
fetch_resenas.py) y produce resenas-analisis.json en ese pack: temas,
tendencia, personal nombrado, triage arreglar/declinar y un chequeo rápido de
autenticidad. Determinístico, sin LLM (regex/keywords en español), método
calcado de <workspace>/gatto-nero (scripts/lib/distill.js +
.claude/skills/review-authenticity/classify.js).

Si NINGÚN centro tiene corpus crudo (Apify y serper fallaron), usa como corpus
degradado las citas curadas de centros-resenas.json en el pack privado y
marca _meta.source = "curado" (nunca inventa reseñas).

Uso:
  python3 scripts/analizar_resenas.py
"""
import json
import math
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.core import data_packs, sitedata  # noqa: E402

PINAMAR_PACK = data_packs.private_pack_dir("ar-b-pinamar")
RAW_DIR = os.path.join(PINAMAR_PACK, "fuentes", "resenas-raw")
CENTROS_FILE = sitedata.path("centros-salud.json")
CURADO_FILE = sitedata.path("centros-resenas.json")
OUT_FILE = sitedata.path("resenas-analisis.json")


def log(msg):
    print(msg, flush=True)


# =============================================================================
# utilidades de texto
# =============================================================================

def sin_acentos(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn"
    )


def normalizar(s):
    return sin_acentos(str(s or "")).lower()


def limpiar_cita(s, maximo=220):
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    if len(t) > maximo:
        t = t[:maximo].rstrip() + "…"
    return t


def contar_palabras(s):
    t = normalizar(s)
    return len(re.findall(r"[a-z0-9ñ]+", t))


# =============================================================================
# paso 1 — distillado: crudo (apify/serper) -> lean
# {author, rating, date, text, lang, author_review_count?, local_guide?, likes?}
# =============================================================================

ES_STOP = set(
    (
        "que de la el los las un una con para por muy mas está estar pero como del al "
        "es son fue sin sus este esta esto ya tambien buena bueno excelente atencion "
        "servicio nosotros porque cuando donde nos les fueron muchas gracias todo nada"
    ).split()
)
EN_STOP = set(
    (
        "the and was for with very they this that our you have had were are food great "
        "good service place but not all their there where when because staff would"
    ).split()
)


def detectar_idioma(texto):
    toks = re.findall(r"[a-záéíóúñü]+", normalizar(texto))
    es = sum(1 for t in toks if t in ES_STOP)
    en = sum(1 for t in toks if t in EN_STOP)
    if es == 0 and en == 0:
        return "es"  # corpus es abrumadoramente en español; default razonable
    return "es" if es >= en else "en"


def _primer_iso(*vals):
    for v in vals:
        if not v:
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(v))
        if m:
            return m.group(1)
    return None


def distillar_item(item):
    """Normaliza UN item crudo (shape de Apify compass/google-maps-reviews-scraper,
    o de serper.dev /reviews) a la forma lean. Defensivo: prueba varios nombres de
    campo porque las dos fuentes difieren."""
    if not isinstance(item, dict):
        return None

    texto = item.get("text") or item.get("snippet") or item.get("originalText") or ""
    if isinstance(texto, dict):
        texto = texto.get("text") or ""
    texto = str(texto).strip()

    rating = item.get("stars")
    if rating is None:
        rating = item.get("rating")
    try:
        rating = int(round(float(rating))) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    autor = item.get("name") or item.get("author") or item.get("reviewerName")
    if isinstance(autor, dict):
        autor = autor.get("name")
    if autor:
        autor = str(autor).strip().split(" (")[0]  # "Nombre (handle)" -> "Nombre"
        autor = autor.split()[0] if autor.split() else autor  # solo el nombre de pila

    fecha = _primer_iso(item.get("publishedAtDate"), item.get("iso_date"), item.get("date"))

    rec = {"author": autor, "rating": rating, "date": fecha, "text": texto}
    if texto:
        rec["lang"] = detectar_idioma(texto)
    n_resenas_autor = item.get("reviewerNumberOfReviews")
    if isinstance(n_resenas_autor, (int, float)):
        rec["author_review_count"] = int(n_resenas_autor)
    guia_local = item.get("isLocalGuide")
    if isinstance(guia_local, bool):
        rec["local_guide"] = guia_local
    likes = item.get("likesCount")
    if isinstance(likes, (int, float)):
        rec["likes"] = int(likes)
    return rec if (rec["rating"] is not None or rec["text"]) else None


def cargar_corpus_centro(slug):
    path = os.path.join(RAW_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        crudo = json.load(f)
    items = crudo.get("reviews_raw", [])
    lean = [r for r in (distillar_item(it) for it in items) if r]
    return lean, crudo.get("_meta", {})


# =============================================================================
# paso 2 — clasificador de temas (regex/keywords en español)
# =============================================================================

TEMAS = [
    {
        "id": "espera_guardia",
        "nombre": "Espera en guardia",
        "categoria": "acceso",
        "kw": ["espera", "esperar", "guardia", "hora de espera", "horas esperando", "cola", "demora", "demoraron", "tardar", "tardaron", "tardanza"],
    },
    {
        "id": "trato_personal",
        "nombre": "Trato del personal",
        "categoria": "trato",
        "kw": ["atencion", "amable", "amables", "amabilidad", "calido", "calida", "trato", "contencion", "carino", "humano", "humanidad", "empatia", "cordial"],
    },
    {
        "id": "personal_nombrado",
        "nombre": "Personal nombrado por su nombre",
        "categoria": "personal",
        "kw": ["dr.", "dra.", "doctor", "doctora", "enfermera", "enfermero", "enfermeria"],
    },
    {
        "id": "turnos_acceso",
        "nombre": "Turnos y acceso telefónico",
        "categoria": "acceso",
        "kw": ["turno", "telefono", "no atienden", "no contestan", "conseguir turno", "linea ocupada", "no responden"],
    },
    {
        "id": "limpieza_instalaciones",
        "nombre": "Limpieza e instalaciones",
        "categoria": "infraestructura",
        "kw": ["limpio", "limpia", "limpieza", "instalaciones", "edificio", "bano", "sucio", "sucia", "mantenido", "moderno", "nuevo"],
    },
    {
        "id": "pediatria_vacunas",
        "nombre": "Pediatría y vacunas",
        "categoria": "especialidad",
        "kw": ["pediatria", "pediatra", "vacuna", "vacunas", "vacunatorio", "bebe", "bebes", "nene", "nena"],
    },
    {
        "id": "estudios_diagnostico",
        "nombre": "Estudios y diagnóstico",
        "categoria": "estudios",
        "kw": ["ecografia", "radiografia", "laboratorio", "analisis", "rayos", "tomografia", "mamografia", "estudios"],
    },
    {
        "id": "resolucion",
        "nombre": "Resolución del problema",
        "categoria": "resultado",
        "kw": ["operaron", "operacion", "diagnostico", "solucionaron", "solucion", "derivaron", "salvaron", "cirugia", "internado", "internaron"],
    },
    {
        "id": "gratuidad_valor",
        "nombre": "Gratuidad y valor",
        "categoria": "valor",
        "kw": ["gratis", "gratuito", "gratuita", "obra social", "sin cargo", "nada que envidiarle", "no hay que pagar"],
    },
]
TEMAS_POR_ID = {t["id"]: t for t in TEMAS}

# Ángulo editorial (report-design SKILL.md, "Editorial angle"): el lector es la
# Secretaría, no un paciente que busca mimos. arreglar = SOLO lo que le saca carga
# operativa al personal o evita una revisita evitable (fricción de acceso, vacíos
# de información); nunca se plantea el trato del personal como algo a "corregir" —
# el personal siempre se lee con simpatía, nunca como falla. declinar = confort/
# amenities esperables de un sistema público austero por diseño: se declinan con
# una línea neutra, sin login de queja. La espera de guardia NO es una falla de
# atención: es la señal de que el triage por gravedad está funcionando — se anota
# como señal de carga, no como reclamo.
TRIAGE_MAPA = {
    "espera_guardia": {
        "tipo": "declinar",
        "accion": "Señal de carga, no de atención: el triage prioriza los casos graves y la espera sube con la demanda de urgencias del partido, no con una demora operativa corregible desde el sistema de turnos.",
    },
    "turnos_acceso": {
        "tipo": "arreglar",
        "accion": "Cada llamada que no se atiende es tiempo de personal administrativo: correr el turno online en el centro señalado le saca esa carga de encima.",
    },
    "limpieza_instalaciones": {
        "tipo": "declinar",
        "accion": "Confort de instalaciones esperable en un sistema público austero por diseño; no es una acción del sistema de turnos.",
    },
    "estudios_diagnostico": {
        "tipo": "arreglar",
        "accion": "Un vacío de información sobre el resultado genera una consulta de vuelta: prolijizar el circuito de entrega ahorra esa revisita.",
    },
}

REGEX_TITULO = re.compile(
    r"\b(Dr\.?|Dra\.?|Doctor|Doctora|Lic\.?|Enfermera|Enfermero)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
)
# forma inversa, muy común en español: "Romina Mamani enfermera", "Ezequiel Navarro,
# la pediatra" — nombre propio seguido (a distancia corta) del rol.
REGEX_NOMBRE_ROL = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?"
    r"[,\s]{1,3}(?:la\s|el\s)?(enfermera|enfermero|pediatra|doctor|doctora)\b",
)
# palabras que la regex de título+nombre agarra por error (pronombres/artículos
# capitalizados por estar después de un punto, o el inicio de la frase siguiente:
# "la Dra. Un mensaje aparte" no es un nombre de pila).
STOP_FALSOS_NOMBRES = {
    "un", "una", "uno", "el", "la", "los", "las", "que", "muy", "tan", "mi", "su",
    "les", "nos", "todo", "toda", "todos", "todas", "gracias", "por", "para",
}


def clasificar_temas(texto):
    t = normalizar(texto)
    encontrados = []
    for tema in TEMAS:
        if tema["id"] == "personal_nombrado":
            continue  # se detecta aparte, por regex de nombre propio
        for kw in tema["kw"]:
            if normalizar(kw) in t:
                encontrados.append(tema["id"])
                break
    return encontrados


_TITULO_NORM = {
    "dr": "Dr.", "dr.": "Dr.", "doctor": "Dr.",
    "dra": "Dra.", "dra.": "Dra.", "doctora": "Dra.",
    "lic": "Lic.", "lic.": "Lic.",
    "enfermera": "Enfermera", "enfermero": "Enfermero",
    "pediatra": "Pediatra",
}


def extraer_personal_nombrado(texto, stop_extra=frozenset()):
    """Título + PRIMER nombre solamente (nunca el apellido completo) — son figuras
    públicas mencionadas en una reseña pública, pero igual recortamos a lo mínimo.
    Prueba las dos formas usuales en español: "Dra. López" y "Romina, la enfermera".
    stop_extra: tokens del nombre oficial del centro — una reseña que escribe
    "Hospital Dr. Pepe Olaechea" nombra al edificio, no a un profesional."""
    texto = texto or ""
    nombres = []
    vistos = set()
    for m in REGEX_TITULO.finditer(texto):
        titulo, nombre = m.group(1), m.group(2)
        if nombre.lower() in STOP_FALSOS_NOMBRES or nombre.lower() in stop_extra or len(nombre) < 3:
            continue
        titulo_norm = _TITULO_NORM.get(titulo.lower().rstrip("."), titulo)
        clave = (titulo_norm, nombre)
        if clave in vistos:
            continue
        vistos.add(clave)
        nombres.append(f"{titulo_norm} {nombre}")
    for m in REGEX_NOMBRE_ROL.finditer(texto):
        nombre, rol = m.group(1), m.group(2)
        if nombre.lower() in STOP_FALSOS_NOMBRES or nombre.lower() in stop_extra or len(nombre) < 3:
            continue
        titulo_norm = _TITULO_NORM.get(rol.lower(), rol.capitalize())
        clave = (titulo_norm, nombre)
        if clave in vistos:
            continue
        vistos.add(clave)
        nombres.append(f"{titulo_norm} {nombre}")
    return nombres


# =============================================================================
# paso 3 — autenticidad (puerto liviano de review-authenticity/classify.js)
# =============================================================================

def _dia(iso):
    return iso[:10] if iso else None


def _mes(iso):
    return iso[:7] if iso else None


def señal_dias_burst(reviews):
    fechados = [r for r in reviews if _dia(r.get("date"))]
    n = len(fechados)
    if n < 5:
        return {"computable": False, "nota": "menos de 5 reseñas con fecha"}
    por_dia = defaultdict(list)
    for r in fechados:
        por_dia[_dia(r["date"])].append(r)
    dias = sorted(por_dia.items(), key=lambda kv: -len(kv[1]))
    dia_pico, resenas_pico = dias[0]
    share = len(resenas_pico) / n
    ratings_pico = [r["rating"] for r in resenas_pico if r.get("rating") is not None]
    dominante = Counter(ratings_pico).most_common(1)
    sesgado = bool(dominante) and dominante[0][1] / len(ratings_pico) >= 0.9 and len(ratings_pico) >= 4
    burst = len(resenas_pico) >= 5 and share >= 0.15 and sesgado
    return {
        "computable": True,
        "dia_pico": dia_pico,
        "resenas_dia_pico": len(resenas_pico),
        "share_dia_pico": round(share, 3),
        "burst_flag": burst,
    }


def señal_cadencia(reviews):
    fechados = [r for r in reviews if _dia(r.get("date"))]
    n = len(fechados)
    if n < 8:
        return {"computable": False, "nota": "menos de 8 reseñas con fecha"}
    por_mes = Counter(_mes(r["date"]) for r in reviews if _mes(r.get("date")))
    meses = por_mes.most_common()
    mes_pico, cnt_pico = meses[0]
    share = cnt_pico / n
    dias = sorted({_dia(r["date"]) for r in fechados})
    span = (datetime.fromisoformat(dias[-1]) - datetime.fromisoformat(dias[0])).days
    compra_unica = span > 60 and share >= 0.6
    return {
        "computable": True,
        "meses_activos": len(meses),
        "mes_pico": mes_pico,
        "share_mes_pico": round(share, 3),
        "span_dias": span,
        "compra_unica_flag": compra_unica,
    }


def _shingles(s, n=4):
    t = re.sub(r"\s+", " ", normalizar(s)).strip()
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def señal_duplicados(reviews, min_palabras=8, umbral=0.5):
    largos = [
        {"i": i, "texto": r.get("text", ""), "autor": r.get("author"), "sh": _shingles(r.get("text", ""))}
        for i, r in enumerate(reviews)
        if contar_palabras(r.get("text", "")) >= min_palabras
    ]
    pares_cruzados = 0
    for a in range(len(largos)):
        for b in range(a + 1, len(largos)):
            if largos[a]["autor"] and largos[a]["autor"] == largos[b]["autor"]:
                continue  # mismo autor: no es señal de granja
            if _jaccard(largos[a]["sh"], largos[b]["sh"]) >= umbral:
                pares_cruzados += 1
    flag = pares_cruzados >= 2 or pares_cruzados >= max(3, math.ceil(len(largos) * 0.03))
    return {
        "computable": len(largos) >= 2,
        "n_textos_largos": len(largos),
        "pares_casi_identicos_entre_autores": pares_cruzados,
        "flag": flag,
    }


def señal_autores(reviews):
    con_historial = [r for r in reviews if isinstance(r.get("author_review_count"), int)]
    if not con_historial:
        return {"computable": False, "nota": "la fuente no trae historial del autor"}
    n = len(con_historial)
    unicos = sum(1 for r in con_historial if r["author_review_count"] <= 1)
    share_unicos = unicos / n
    mediana = statistics.median(r["author_review_count"] for r in con_historial)
    return {
        "computable": True,
        "share_cuenta_unica": round(share_unicos, 3),
        "mediana_resenas_autor": mediana,
        "flag": share_unicos >= 0.5,
    }


def chequeo_autenticidad(reviews):
    dias = señal_dias_burst(reviews)
    cadencia = señal_cadencia(reviews)
    dup = señal_duplicados(reviews)
    autores = señal_autores(reviews)

    razones = []
    score = 10
    flags = 0
    if dias.get("burst_flag"):
        flags += 1
        razones.append(f"pico de reseñas el {dias['dia_pico']} con calificación pareja — posible ráfaga")
    if cadencia.get("compra_unica_flag"):
        flags += 1
        razones.append(f"la mayoría cae en un solo mes ({cadencia['mes_pico']}) dentro de un período largo")
    if dup.get("flag"):
        flags += 1
        razones.append(f"{dup['pares_casi_identicos_entre_autores']} pares de texto casi idéntico entre autores distintos")
    if autores.get("flag"):
        flags += 1
        razones.append(f"{round(autores['share_cuenta_unica'] * 100)}% de los autores tienen una sola reseña en su historial")

    score -= 3 * flags
    n_no_computable = sum(
        1 for s in (dias, cadencia, dup, autores) if s.get("computable") is False
    )
    if n_no_computable and flags == 0:
        score -= 1
        razones.append(f"{n_no_computable} señal(es) no calculable(s) con este corpus (muestra chica)")
    if not razones:
        razones.append("sin señales de granja: fechas repartidas, textos propios, sin ráfagas")
    score = max(0, min(10, score))
    banda = "limpio" if score >= 8 else ("mixto" if score >= 5 else "sospechoso")
    return {"score": score, "band": banda, "reasons": razones}


# =============================================================================
# paso 4 — analisis por centro
# =============================================================================

def top_quotes(reviews, ids_tema, n=3):
    candidatas = []
    for r in reviews:
        texto = r.get("text") or ""
        if not texto or contar_palabras(texto) < 4:
            continue
        temas_r = clasificar_temas(texto)
        if any(t in temas_r for t in ids_tema):
            candidatas.append(r)
    # preferir reseñas ni muy cortas ni gigantes, ordenadas por longitud media primero
    candidatas.sort(key=lambda r: abs(contar_palabras(r["text"]) - 20))
    vistas = set()
    out = []
    for r in candidatas:
        c = limpiar_cita(r["text"])
        if c in vistas:
            continue
        vistas.add(c)
        out.append({"text": c, "rating": r.get("rating")})
        if len(out) >= n:
            break
    return out


def _stop_tokens_centro(slug, centro_meta):
    """Tokens del nombre oficial del centro (webdata + ficha de Places), para que
    "Hospital Dr. Pepe Olaechea" en una reseña no se lea como un profesional."""
    nombres = [centro_meta.get("name") or ""]
    ruta = os.path.join(PINAMAR_PACK, "centros-brila-info", f"{slug}.json")
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                nombres.append(json.load(f).get("name") or "")
        except Exception:
            pass
    toks = set()
    for n in nombres:
        for t in re.split(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]+", n):
            if len(t) >= 3:
                toks.add(t.lower())
    return toks


def analizar_centro(slug, centro_meta, reviews, meta_crudo):
    stop_centro = _stop_tokens_centro(slug, centro_meta)
    ratings = [r["rating"] for r in reviews if r.get("rating") is not None]
    forma = {str(i): 0 for i in range(1, 6)}
    for r in ratings:
        if 1 <= r <= 5:
            forma[str(r)] += 1

    # tendencia mensual
    por_mes = defaultdict(list)
    for r in reviews:
        m = _mes(r.get("date"))
        if m and r.get("rating") is not None:
            por_mes[m].append(r["rating"])
    tendencia = [
        {"month": m, "n": len(vals), "avg_rating": round(statistics.mean(vals), 2)}
        for m, vals in sorted(por_mes.items())
    ]

    # temas
    temas_out = {}
    por_tema_reviews = defaultdict(list)
    for r in reviews:
        for tid in clasificar_temas(r.get("text", "")):
            por_tema_reviews[tid].append(r)
    # Zoonosis atiende animales: sus menciones de "vacuna"/"bebé" son mascotas,
    # no pediatría — el tema entero es un falso positivo del clasificador ahí.
    if slug == "zoonosis":
        por_tema_reviews.pop("pediatria_vacunas", None)
    for tid, revs in por_tema_reviews.items():
        rts = [r["rating"] for r in revs if r.get("rating") is not None]
        temas_out[tid] = {
            "name": TEMAS_POR_ID[tid]["nombre"],
            "category": TEMAS_POR_ID[tid]["categoria"],
            "n": len(revs),
            "avg_rating": round(statistics.mean(rts), 2) if rts else None,
            "quotes": top_quotes(revs, [tid]),
        }

    # personal nombrado — las figuras públicas que agradecen las reseñas, de punta a
    # punta el ángulo es de reconocimiento, nunca de reclamo (report-design: "mucha
    # simpatía hacia el personal médico"). "elogiados_en" guarda, por nombre, alguna
    # cita positiva breve para poder liderar el hero con eso.
    conteo_personal = Counter()
    elogios_por_nombre = defaultdict(list)
    for r in reviews:
        for nombre in extraer_personal_nombrado(r.get("text", ""), stop_centro):
            conteo_personal[nombre] += 1
            if r.get("rating") is not None and r["rating"] >= 4 and r.get("text"):
                elogios_por_nombre[nombre].append(limpiar_cita(r["text"], 160))
    # cada persona sale con su conteo de reseñas que la nombran y, si existe,
    # una cita positiva breve (4-5★) para la sección "Personal reconocido".
    # Una reseña que agradece a varios a la vez aporta su cita UNA sola vez
    # (la primera persona la muestra; repetirla tres veces lee a copy-paste).
    personal_nombrado = []
    citas_usadas = set()
    for nombre, n in conteo_personal.most_common(8):
        item = {"name": nombre, "n": n}
        for cita in elogios_por_nombre.get(nombre, []):
            if cita not in citas_usadas:
                citas_usadas.add(cita)
                item["quote"] = cita
                break
        personal_nombrado.append(item)
    # solo cuentan como "elogio destacable" los nombres que aparecen en al menos una
    # reseña de 4-5★ (evita liderar con un nombre mencionado en una queja de 1★)
    personal_elogiado = [
        {"nombre": nombre, "n": conteo_personal[nombre], "cita": elogios_por_nombre[nombre][0]}
        for nombre in conteo_personal
        if elogios_por_nombre.get(nombre)
    ]
    personal_elogiado.sort(key=lambda p: -p["n"])
    if personal_nombrado:
        n_mentions = sum(c["n"] for c in personal_nombrado)
        temas_out["personal_nombrado"] = {
            "name": TEMAS_POR_ID["personal_nombrado"]["nombre"],
            "category": TEMAS_POR_ID["personal_nombrado"]["categoria"],
            "n": n_mentions,
            "avg_rating": None,
            "quotes": [],
        }

    # triage arreglar / declinar
    arreglar, declinar = [], []
    for tid, t in temas_out.items():
        if tid not in TRIAGE_MAPA or t["n"] < 2 or t["avg_rating"] is None:
            continue
        if t["avg_rating"] >= 3.5:
            continue
        entry = {"topic": t["name"], "n": t["n"], "avg_rating": t["avg_rating"], "action": TRIAGE_MAPA[tid]["accion"]}
        (arreglar if TRIAGE_MAPA[tid]["tipo"] == "arreglar" else declinar).append(entry)

    autenticidad = chequeo_autenticidad(reviews)

    rating_centro = round(statistics.mean(ratings), 2) if ratings else centro_meta.get("rating")
    count_centro = len(reviews) or centro_meta.get("count")

    # hero: 2-3 oraciones EN CLAVE DE EFICIENCIA (round 2026-07-22, responsable del producto:
    # "don't concentrate it around the stars — rework it around efficiency").
    # Nada de promedios de estrellas en el copy: las estrellas quedan como
    # metadato chico en el header de la tarjeta. El hero cuenta (1) a quién
    # agradece la gente por su nombre — simpatía hacia el personal —, (2) qué
    # CARGA absorbe el centro (el tema más mencionado como señal de demanda) y
    # (3) la guardia como señal de triage, o lo que más destacan (el rating se
    # usa sólo para ELEGIR ese tema, nunca se imprime como hallazgo).
    hero = []
    temas_positivos = sorted(
        (
            t for tid, t in temas_out.items()
            if tid not in ("espera_guardia", "personal_nombrado")
            and t.get("avg_rating") is not None and t["n"] >= 3
        ),
        key=lambda t: -t["avg_rating"],
    )

    # trato del personal = elogio, no carga: se cuenta junto con los nombres
    # agradecidos, nunca como "demanda que el centro absorbe"
    trato = temas_out.get("trato_personal")
    if personal_elogiado:
        p = personal_elogiado[0]
        frase = f"En Google, quienes se atendieron en {centro_meta['name']} agradecen por su nombre a {p['nombre']}"
        if trato and trato["n"] >= 3:
            frase += f"; el trato del personal aparece en {trato['n']} de {count_centro} reseñas"
        hero.append(frase + ".")
    elif trato and trato["n"] >= 3:
        hero.append(f"El tema más mencionado en {centro_meta['name']} es el trato del personal: {trato['n']} de {count_centro} reseñas.")

    espera = temas_out.get("espera_guardia")
    linea_guardia = bool(espera and espera["n"] >= 10)

    # qué demanda absorbe el centro: el tema de SERVICIO con más menciones —
    # sólo categorías que nombran algo que la gente viene a buscar (una
    # especialidad, un estudio, una resolución), nunca percepciones como
    # trato/limpieza/valor ni fricciones de acceso (la guardia y el teléfono
    # ya tienen sus propias líneas, acá serían dobles)
    _CATS_DEMANDA = {"especialidad", "estudios", "resultado"}
    temas_por_volumen = sorted(
        (t for t in temas_out.values() if t["category"] in _CATS_DEMANDA and t["n"] >= 3),
        key=lambda t: -t["n"],
    )
    if temas_por_volumen:
        top = temas_por_volumen[0]
        hero.append(
            f"Lo que más se viene a buscar, según las reseñas: {top['name'].lower()} ({top['n']} menciones)."
        )

    if linea_guardia:
        share_espera = round(espera["n"] / max(1, len(reviews)) * 100)
        hero.append(
            f"La guardia concentra {share_espera}% de las reseñas ({espera['n']} de {len(reviews)}): "
            "ahí pesa el triage por gravedad, más que la atención en sí."
        )
    elif temas_positivos and len(hero) < 3 and temas_positivos[0]["avg_rating"] >= 4.0:
        mejor = temas_positivos[0]
        if not any(mejor["name"].lower() in h.lower() for h in hero):
            hero.append(f"Lo que más destacan quienes se atienden ahí: {mejor['name'].lower()} ({mejor['n']} menciones).")

    if not hero:
        hero.append(f"{count_centro} reseñas de Google analizadas para {centro_meta['name']}.")

    return {
        "name": centro_meta["name"],
        "rating": rating_centro,
        "count": count_centro,
        "maps": centro_meta.get("maps_url"),
        "rating_distribution": forma,
        "monthly_trend": tendencia,
        "themes": temas_out,
        "named_staff": personal_nombrado,
        "triage": {"fix": arreglar, "decline": declinar},
        "authenticity": autenticidad,
        "hero": hero,
        "_data_source": (meta_crudo or {}).get("fuente"),
    }


# =============================================================================
# corpus degradado: citas curadas (si NINGÚN centro tiene raw)
# =============================================================================

def reviews_desde_curado(datos_curado, slug):
    d = datos_curado.get(slug)
    if not d:
        return []
    out = []
    for q in d.get("quotes", []):
        out.append({"author": None, "rating": q.get("rating"), "date": None, "text": q.get("text", ""), "lang": "es"})
    return out


# =============================================================================
# main
# =============================================================================

def main():
    with open(CENTROS_FILE, "r", encoding="utf-8") as f:
        centros = json.load(f)["centros"]

    hay_raw = os.path.isdir(RAW_DIR) and any(
        os.path.exists(os.path.join(RAW_DIR, f"{c['slug']}.json")) for c in centros
    )
    fuente_global = "apify_o_serper" if hay_raw else "curado"
    datos_curado = None
    if not hay_raw:
        log("AVISO: no hay corpus crudo en el pack privado — degradando a citas curadas de centros-resenas.json")
        with open(CURADO_FILE, "r", encoding="utf-8") as f:
            datos_curado = json.load(f)

    resultado = {"_meta": {}, "centers": {}, "comparison": {}}
    por_centro_n = {}
    total_resenas = 0

    analisis_por_slug = {}
    for centro in centros:
        slug = centro["slug"]
        if not centro.get("maps_url"):
            continue

        if hay_raw:
            reviews, meta_crudo = cargar_corpus_centro(slug)
            if reviews is None:
                log(f"- {slug}: sin archivo raw, se omite")
                continue
        else:
            reviews = reviews_desde_curado(datos_curado, slug)
            meta_crudo = {"fuente": "curado"}
            centro = dict(centro)
            centro.setdefault("rating", (datos_curado.get(slug) or {}).get("rating"))

        if not reviews:
            log(f"- {slug}: 0 reseñas utilizables, se omite")
            continue

        analisis = analizar_centro(slug, centro, reviews, meta_crudo)
        resultado["centers"][slug] = analisis
        analisis_por_slug[slug] = analisis
        por_centro_n[slug] = analisis["count"]
        total_resenas += len(reviews)
        log(f"  {slug}: {len(reviews)} reseñas analizadas, {len(analisis['themes'])} temas, autenticidad {analisis['authenticity']['score']}/10")

    # ---- comparativa hospital vs CAPS -------------------------------------------------
    # Ángulo editorial (SKILL.md "Editorial angle" + round 2026-07-22: "rework it
    # around efficiency"): el hero comparativo NO habla de estrellas — cuenta
    # DÓNDE cae la carga (la guardia del hospital absorbe las urgencias del
    # partido bajo triage por gravedad), cuál es la palanca operativa más citada
    # (el teléfono que no se atiende = tiempo administrativo que el turno online
    # devuelve), y cierra en positivo con el trato. Los promedios por estrella
    # quedan como DATO en temas_gap, nunca como frase.
    hosp = analisis_por_slug.get("hospital-pinamar")
    caps_slugs = [s for s in analisis_por_slug if s.startswith("caps-")]
    comparativa_hero = []
    temas_gap = {}
    tarjeta = None

    if hosp and total_resenas:
        share_hosp = round(hosp["count"] / total_resenas * 100)
        hosp_espera = hosp["themes"].get("espera_guardia")
        if hosp_espera and hosp_espera["n"] >= 10:
            share_guardia = round(hosp_espera["n"] / hosp["count"] * 100)
            comparativa_hero.append(
                f"El hospital concentra el {share_hosp}% de las reseñas del partido ({hosp['count']} de "
                f"{total_resenas}), y dentro del hospital la guardia absorbe el {share_guardia}% "
                f"({hosp_espera['n']} de {hosp['count']}): ahí cae la demanda de urgencias, bajo triage "
                "por gravedad. Los CAPS trabajan con demanda programada, y se nota en sus reseñas."
            )
            tarjeta = {
                "value": f"{share_guardia}%",
                "finding": (
                    f"De las reseñas del hospital, {hosp_espera['n']} de {hosp['count']} hablan de la "
                    "guardia: señal de dónde cae la carga de urgencias, no de la atención. La palanca "
                    "más citada para descargar al equipo es el turno online."
                ),
            }

        # brecha por tema (bloque de DATOS para un slope/dumbbell, no para el hero)
        for tid, t_h in hosp["themes"].items():
            if t_h.get("avg_rating") is None or t_h["n"] < 2:
                continue
            vals_caps = []
            for s in caps_slugs:
                t_c = analisis_por_slug[s]["themes"].get(tid)
                if t_c and t_c.get("avg_rating") is not None and t_c["n"] >= 2:
                    vals_caps.append(t_c["avg_rating"])
            if vals_caps:
                prom_c = round(statistics.mean(vals_caps), 2)
                temas_gap[tid] = {
                    "name": t_h["name"],
                    "hospital": t_h["avg_rating"],
                    "caps_avg_rating": prom_c,
                    "gap": round(prom_c - t_h["avg_rating"], 2),
                }

    # palanca operativa: menciones de teléfono/turnos difíciles en TODOS los
    # centros — cada llamada sin atender es tiempo de personal administrativo
    menciones_tel = sum(
        (a["themes"].get("turnos_acceso") or {}).get("n", 0) for a in analisis_por_slug.values()
    )
    if menciones_tel >= 5:
        comparativa_hero.append(
            f"La palanca operativa más citada es el teléfono: {menciones_tel} reseñas mencionan llamadas "
            "sin atender o turnos difíciles de conseguir. Cada una de esas llamadas es tiempo "
            "administrativo que el turno online le devuelve al equipo."
        )

    # cierre positivo: si tanto el hospital como algún CAPS lideran su hero con
    # personal elogiado por nombre, la calidad humana no es lo que cambia
    if hosp:
        hosp_elogia = any("agradecen por su nombre" in h for h in hosp.get("hero", []))
        caps_elogia = any(
            any("agradecen por su nombre" in h for h in analisis_por_slug[s].get("hero", []))
            for s in caps_slugs
        )
        if hosp_elogia and caps_elogia:
            comparativa_hero.append(
                "Lo que no cambia es el trato: tanto en el hospital como en los CAPS, las reseñas "
                "agradecen por su nombre a los mismos profesionales."
            )

    if tarjeta is None and total_resenas:
        tarjeta = {
            "value": str(total_resenas),
            "finding": f"Reseñas de Google analizadas en {len(analisis_por_slug)} centros del partido.",
        }

    resultado["comparison"] = {"hero": comparativa_hero}
    if temas_gap:
        resultado["comparison"]["theme_gaps"] = temas_gap
    if tarjeta:
        resultado["comparison"]["card"] = tarjeta

    resultado["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": fuente_global,
        "total_reviews": total_resenas,
        "by_center": por_centro_n,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    log(f"\nGuardado {OUT_FILE} — {total_resenas} reseñas, {len(resultado['centers'])} centros")
    for line in resultado["comparison"].get("hero", []):
        log(f"  comparativa: {line}")


if __name__ == "__main__":
    main()
