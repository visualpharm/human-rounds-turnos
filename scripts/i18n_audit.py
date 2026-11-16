#!/usr/bin/env python3
"""i18n_audit.py — el portón de la traducción del sitio.

Traducir un sitio entero tiene dos formas de salir mal, y ninguna de las dos
la ve el que la está escribiendo:

  1. Se rompe el castellano. Extraer un literal a una clave es reescribir el
     render de una página que hoy anda. Un `{name}` mal puesto, una clave que
     todavía no existe, un `t_or()` sin fallback: el que traduce navega en
     inglés y no lo ve nunca.
  2. No se traduce del todo. Queda media frase en castellano en una tabla que
     se dibuja del lado del cliente, o peor, una mezcla ("Sacar on mañana,
     08:00"), que es lo que originó todo este trabajo.

Así que el audit chequea las dos direcciones sobre el sitio corriendo:

    Paridad    el render en `es` es byte a byte el de la rama base. El sitio
               en castellano no se mueve un byte. Es lo único que hace segura
               una extracción masiva hecha por muchas manos en paralelo.
    Fuga       el render en `en` no trae castellano (fuera de la lista de
               nombres propios que se pasa con --allow).
    Claves     ninguna página muestra una clave cruda (`listing.col.proximo`)
               donde tendría que haber texto.
    Literales  los .js que la unidad declara suya no tienen más literales en
               castellano.

Uso típico dentro de un worktree, con el server propio ya levantado:

    python3 scripts/i18n_audit.py --base http://127.0.0.1:7801 \\
            --pages / /centers /service/cardiologia-sampleton-hospital \\
            --js static/platform/listing.mjs static/platform/directory.mjs \\
            --baseline-ref origin/main

`--baseline-ref` es opcional: sin él, el chequeo de paridad se saltea (y lo
dice). Con él, se levanta un SEGUNDO server desde un checkout limpio de esa
ref y se comparan las dos respuestas.

Salida 0 = verde. Cualquier hallazgo = 1.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- castellano

# Un acento no alcanza como evidencia: "Adrián Acosta" es un nombre propio y
# "café" es una palabra que el inglés también usa. Lo que delata al castellano
# es el vocabulario funcional — artículos, preposiciones, conjugaciones — que
# no aparece en una frase inglesa por accidente.
SPANISH_WORDS = {
    # artículos y preposiciones
    "el", "la", "los", "las", "un", "una", "unos", "unas", "del", "al",
    "de", "en", "con", "sin", "por", "para", "sobre", "entre", "hasta",
    "desde", "hacia", "segun", "durante",
    # conjunciones y pronombres
    "que", "como", "cuando", "donde", "porque", "pero", "aunque", "tambien",
    "su", "sus", "tu", "tus", "mi", "mis", "te", "se", "lo", "les", "nos",
    # verbos frecuentes del producto
    "es", "son", "esta", "estan", "hay", "tiene", "tenes", "podes", "queres",
    "necesitas", "sacar", "pedir", "elegir", "elegi", "traer", "trae", "trae",
    "ver", "vas", "va", "van", "ir", "anda", "avisa", "avisar", "mostrar",
    "buscar", "busca", "cargar", "guardar", "confirmar", "confirma",
    # sustantivos del dominio
    "turno", "turnos", "medico", "medicos", "medica", "paciente", "pacientes",
    "hora", "horas", "horario", "horarios", "dia", "dias", "semana", "mes",
    "ano", "anos", "edad", "orden", "ordenes", "consulta", "consultorio",
    "especialidad", "especialidades", "centro", "centros", "sede",
    "disponibilidad", "proxima", "proximo", "cuenta", "clave", "correo",
    "telefono", "documento", "cobertura", "estudio", "estudios",
    # días y meses, que son la fuente del bug original
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
    "hoy", "manana", "ayer",
}

# Palabras que también son inglesas o son idénticas en los dos idiomas. Sin
# esta lista, "no", "a", "e", "final" o "hospital" harían fallar toda página.
NOT_EVIDENCE = {"no", "a", "e", "o", "y", "u", "final", "hospital", "total",
                "normal", "control", "general", "local", "personal", "email"}

WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")
# Una clave cruda: dos o más segmentos punteados, todo minúscula, sin espacios.
RAW_KEY_RE = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}\b")
# Un dominio real (contacto@sampleton.example, hospital.pinamar.gob.ar) tiene la
# MISMA forma que una clave (2+ segmentos punteados, todo minúscula) — sin este
# filtro, cualquier email/dominio .gob.ar/.com.ar de 3 segmentos que un centro
# publique en su ficha de contacto sale como falso positivo de "clave cruda"
# (hallado 2026-08-05 con hospital@sampleton.example, pack example-town).
DOMAIN_TAIL_RE = re.compile(
    r"\.(?:ar|com|net|org|edu|gob|gov|info|io)(?:\.[a-z]{2})?$")
TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S)


def fold(word):
    """minúsculas y sin tildes — 'Próxima' y 'proxima' son la misma palabra."""
    n = unicodedata.normalize("NFD", word.lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")


def visible_text(html):
    """El texto que un visitante lee: sin tags, sin script, sin style."""
    return TAG_RE.sub(" ", html or "")


def spanish_hits(text, allow):
    """Las palabras castellanas del texto, con un poco de contexto cada una."""
    allowed = {fold(a) for a in allow}
    out = {}
    for m in WORD_RE.finditer(text):
        w = fold(m.group(0))
        if w in NOT_EVIDENCE or w in allowed:
            continue
        if w not in SPANISH_WORDS:
            continue
        if w in out:
            continue
        lo, hi = max(0, m.start() - 45), min(len(text), m.end() + 45)
        out[w] = " ".join(text[lo:hi].split())
    return out


# ---------------------------------------------------------------- literales JS

STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"' r"|'((?:[^'\\\n]|\\.)*)'")

# Un valor de alambre, no una frase: un id de sección ("cuenta"), una clase
# CSS (".stznav-cuenta-av"), un selector, una ruta. Son castellanos y tienen
# que seguir siéndolo — el código los compara contra sí mismos.
WIRE_RE = re.compile(r"^[.#]?[a-z0-9_-]+$")
PATHY_RE = re.compile(r"^(/|\w+://|data:|#[0-9a-f]{3,8}$)")


def looks_like_prose(value):
    """¿Es una frase que un visitante lee, o un valor que el código compara?

    Lo que separa una cosa de la otra no es el idioma sino la forma: una
    frase tiene un espacio, una mayúscula o un acento. `cuenta` no tiene
    ninguna de las tres y es el id de una sección; `Mañana` tiene dos y es
    lo que dice el botón."""
    if not value or WIRE_RE.match(value) or PATHY_RE.match(value):
        return False
    # Un selector CSS compuesto ("#sec-mis-turnos .mt-day table") es varios
    # tokens de alambre separados por espacio — el espacio no lo convierte en
    # frase, cada tramo sigue siendo un id/clase/tag que el código compara.
    # La marca real de que ES un selector (y no una oración en minúsculas que
    # por casualidad no lleva mayúscula ni acento) es que AL MENOS un tramo
    # empiece con "." o "#" — nunca pasa en una frase.
    tokens = [t for t in value.split(" ") if t]
    if (len(tokens) > 1 and any(t[0] in ".#" for t in tokens)
            and all(WIRE_RE.match(t) for t in tokens)):
        return False
    # Un bloque de CSS no es texto por más que la clase se llame `.lst-orden`.
    if "{" in value and "}" in value and ":" in value:
        return False
    # Un fragmento de HTML aporta como texto sólo lo que queda fuera de los
    # tags: `<a class="stznav-cuenta"` no dice nada, `<b>Hoy</b>` sí. Se corta
    # desde `<` hasta `>` o hasta el final, porque los fragmentos que arma el
    # JS por concatenación abren un tag y lo cierran tres líneas después.
    if "<" in value:
        value = re.sub(r"<[^>]*(>|$)", " ", value)
    if "=" in value:
        value = re.sub(r"[\w-]+\s*=\s*(\"[^\"]*\"?|'[^']*'?|\S*)", " ", value)
    if not value.strip():
        return False
    return (" " in value.strip()
            or any(c.isupper() for c in value)
            or any(unicodedata.category(c) == "Mn"
                   for c in unicodedata.normalize("NFD", value)))


def js_spanish_literals(path, allow):
    """Literales de string del archivo que todavía suenan a castellano.

    Mira SOLO strings, no comentarios: los comentarios de este repo están en
    castellano a propósito y van a seguir estándolo."""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        return [(0, f"no se pudo leer: {e}")]
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    allowed = {fold(a) for a in allow}
    out = []
    for m in STRING_RE.finditer(src):
        value = m.group(1) if m.group(1) is not None else m.group(2)
        if not value or len(value) < 3 or not looks_like_prose(value):
            continue
        line = src.count("\n", 0, m.start()) + 1
        words = {fold(w) for w in WORD_RE.findall(value)}
        hit = (words & SPANISH_WORDS) - NOT_EVIDENCE - allowed
        if hit:
            out.append((line, f"{sorted(hit)} → {value[:70]}"))
    return out


_T_CALL_RE = re.compile(r'\bT\(\s*["\'][a-z0-9_.]+["\']\s*,')


def _find_call_close(src, open_paren_idx):
    """Índice del ')' que cierra el '(' en open_paren_idx, contando profundidad
    y saltando literales de string — igual que tests/i18n-fallbacks.test.mjs:
    calls(), porque un `.*?\\)` no balanceado corta en el PRIMER paréntesis que
    encuentra (p.ej. el de `quien(t)` adentro de `{ who: quien(t) }`) y deja el
    fallback en castellano sin tapar, que es justo lo que este stripper existe
    para evitar."""
    depth = 1
    i = open_paren_idx + 1
    in_str = None
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("'", '"'):
            in_str = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def strip_t_fallbacks(path):
    """El texto del archivo sin los fallbacks de T(...) — para no contar como
    deuda el castellano que YA está detrás de una clave."""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return None
    out = []
    pos = 0
    for m in _T_CALL_RE.finditer(src):
        if m.start() < pos:
            continue  # ya cubierto por una llamada T(...) anterior
        open_paren = m.end() - 1
        close = _find_call_close(src, open_paren)
        if close < 0:
            continue
        out.append(src[pos:m.start()])
        out.append("T()")
        pos = close + 1
    out.append(src[pos:])
    return "".join(out)


# ---------------------------------------------------------------- HTTP

def get(base, path, lang):
    req = urllib.request.Request(
        base.rstrip("/") + path,
        headers={"Cookie": f"lang={lang}", "User-Agent": "i18n-audit"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"<!-- {e} -->"


def wait_up(base, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, _ = get(base, "/", "es")
        if code and code < 500:
            return True
        time.sleep(0.5)
    return False


# El HTML lleva cosas que cambian entre dos renders de la MISMA build y no son
# una regresión: un token CSRF, una hora, un id de sesión. Se normalizan antes
# de comparar, si no la paridad no pasa nunca.
#
# El `?v=N` de cada <script src> es la MISMA clase de ruido: el invariante del
# repo (CLAUDE.md, "Cloudflare cachea .js/.css 4h") EXIGE bumpearlo apenas el
# archivo cambia — que es exactamente lo que hace la unidad que está pasando
# este mismo audit. Sin esta normalización, cualquier unidad que traduzca un
# .js referenciado en la página auditada (admin.html enlaza los ~20 módulos
# del panel en un solo template) reprueba "paridad" por el número de versión,
# nunca por una palabra en castellano que se movió — falso positivo
# descubierto haciendo el audit de la unidad 26 (admin-settings/users/system/
# account/theme/roadmap/alerts.js), confirmado diffeando el HTML byte a byte:
# la única diferencia contra origin/main eran los ?v= de los archivos propios.
VOLATILE = [
    (re.compile(r'name="csrf"[^>]*value="[^"]*"'), 'name="csrf" value="X"'),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "HH:MM:SS"),
    (re.compile(r'"(?:nonce|token|sid)":\s*"[^"]*"'), '"nonce":"X"'),
    (re.compile(r"nonce-[A-Za-z0-9+/=]+"), "nonce-X"),
    (re.compile(r'(\.(?:js|css))\?v=\d+'), r"\1?v=N"),
]


def normalise(html):
    for pattern, repl in VOLATILE:
        html = pattern.sub(repl, html)
    return html


# ---------------------------------------------------------------- baseline

def start_baseline(ref, port):
    """Un checkout limpio de `ref` corriendo en su propio puerto.

    Vale la pena el worktree en vez de un `git show` por archivo: la paridad
    que importa es la de la PÁGINA RENDERIZADA, y eso son treinta módulos
    colaborando, no un template."""
    tmp = tempfile.mkdtemp(prefix="i18n-baseline-")
    work = os.path.join(tmp, "tree")
    # `git worktree add` toma el lock del repo común. Con muchas unidades
    # auditando a la vez el lock está tomado la mitad del tiempo, y fallar por
    # eso convertiría el portón en una moneda al aire.
    for attempt in range(6):
        r = subprocess.run(["git", "worktree", "add", "--detach", work, ref],
                           cwd=ROOT, capture_output=True, text=True)
        if not r.returncode:
            break
        if attempt == 5:
            return None, None, f"no se pudo crear el worktree de {ref}: {r.stderr.strip()}"
        time.sleep(2 + attempt * 3)
    env = dict(os.environ, PORT=str(port), TURNOS_FAKE_AI="1")
    # Un ref anterior al retiro del topología legacy trae server.py; uno
    # posterior sólo trae asgi_main.py (que exige TURNOS_ASGI=1). Elegir por
    # lo que el worktree realmente contiene mantiene el audit usable con
    # cualquier baseline.
    if os.path.exists(os.path.join(work, "server.py")):
        cmd = [sys.executable, "server.py"]
    else:
        cmd = [sys.executable, "asgi_main.py"]
        env["TURNOS_ASGI"] = "1"
    proc = subprocess.Popen(cmd, cwd=work, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    if not wait_up(base):
        proc.terminate()
        return None, work, f"el server de {ref} no levantó en el puerto {port}"
    return proc, work, None


def stop_baseline(proc, work):
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if work:
        subprocess.run(["git", "worktree", "remove", "--force", work],
                       cwd=ROOT, capture_output=True)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="el server de este worktree, ya levantado")
    ap.add_argument("--pages", nargs="*", default=[], help="rutas que esta unidad toca")
    ap.add_argument("--js", nargs="*", default=[], help=".js que esta unidad declara suyos")
    ap.add_argument("--allow", nargs="*", default=[],
                    help="palabras que SON castellano pero están bien (nombres propios)")
    ap.add_argument("--baseline-ref", default="",
                    help="ref contra la que comparar el render en es (ej. origin/main)")
    ap.add_argument("--baseline-port", type=int, default=7899)
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args()

    findings = []
    checked = {"pages": 0, "js": 0, "parity": 0}

    # --- el server propio tiene que estar arriba
    if not wait_up(args.base, timeout=10):
        print(f"FALLA: no hay nada escuchando en {args.base}", file=sys.stderr)
        return 2

    # --- fuga de castellano y claves crudas, en inglés
    for path in args.pages:
        code, html = get(args.base, path, "en")
        checked["pages"] += 1
        if code != 200:
            findings.append(("http", path, f"lang=en devolvió {code}"))
            continue
        text = visible_text(html)
        for word, context in sorted(spanish_hits(text, args.allow).items()):
            findings.append(("castellano", path, f'"{word}" en …{context}…'))
        for key in sorted(set(RAW_KEY_RE.findall(text))):
            if DOMAIN_TAIL_RE.search(key):
                continue
            findings.append(("clave cruda", path, f"{key} se está mostrando como texto"))

    # --- paridad del castellano contra la rama base
    proc = work = None
    if args.baseline_ref and args.pages:
        proc, work, err = start_baseline(args.baseline_ref, args.baseline_port)
        if err:
            findings.append(("baseline", args.baseline_ref, err))
        else:
            baseline = f"http://127.0.0.1:{args.baseline_port}"
            for path in args.pages:
                checked["parity"] += 1
                _, mine = get(args.base, path, "es")
                _, theirs = get(baseline, path, "es")
                if normalise(mine) != normalise(theirs):
                    findings.append((
                        "paridad", path,
                        "el render en castellano cambió respecto de "
                        f"{args.baseline_ref} ({len(theirs)} → {len(mine)} bytes)"))
        stop_baseline(proc, work)
    elif args.pages:
        print("aviso: sin --baseline-ref no se chequea la paridad del castellano",
              file=sys.stderr)

    # --- literales que quedaron sin traducir
    for name in args.js:
        path = name if os.path.isabs(name) else os.path.join(ROOT, name)
        checked["js"] += 1
        src = strip_t_fallbacks(path)
        if src is None:
            findings.append(("js", name, "no se pudo leer"))
            continue
        tmp = path + ".audit-tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(src)
            for line, detail in js_spanish_literals(tmp, args.allow):
                findings.append(("literal js", f"{name}:{line}", detail))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    if args.json:
        print(json.dumps([{"kind": k, "where": w, "detail": d}
                          for k, w, d in findings], ensure_ascii=False, indent=2))
    else:
        for kind, where, detail in findings:
            print(f"  [{kind}] {where}: {detail}")
        print(f"\n{checked['pages']} páginas en inglés, {checked['parity']} comparadas "
              f"en castellano, {checked['js']} archivos js · "
              + ("VERDE" if not findings else f"{len(findings)} hallazgos"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
