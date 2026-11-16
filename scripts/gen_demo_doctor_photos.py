#!/usr/bin/env python3
"""gen_demo_doctor_photos.py — retratos SINTÉTICOS para los médicos del pack
de demo (packs/example-town/fotos-medicos/<id>.jpg).

Por qué existe: el pack de demo trae 50 médicos ficticios y sólo 3 retratos,
así que casi todas las fichas caían al avatar de iniciales. El pack real
(ar-b-pinamar) usa fotos reales hunteadas + sprite; la demo es pública y
ficticia, así que sus caras se GENERAN y nunca deben parecerse a una persona
real (es el requisito que hace que la demo se pueda mostrar sin permiso de
nadie).

Modelo: gpt-image-2 (roster ~/.claude/ref/model-roster.json, categoría
text-to-image). Se pide 1024x1024 y se baja a 512x512 JPEG, que es el formato
que ya tenían los 3 retratos originales del pack.

Uso:
    python3 scripts/gen_demo_doctor_photos.py            # sólo los que faltan
    python3 scripts/gen_demo_doctor_photos.py --force    # regenera todos
    python3 scripts/gen_demo_doctor_photos.py --only ruiz-sergio
    python3 scripts/gen_demo_doctor_photos.py --dry-run  # imprime prompts

La API key se lee de `OPENAI_API_KEY` o del archivo local nombrado por
`TURNOS_DEMO_IMAGE_ENV_FILE`; nunca se pasa por línea de comandos.
"""
import argparse
import base64
import io
import json
import os
import random
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDICOS = os.path.join(ROOT, "packs", "example-town", "medicos.json")
OUT_DIR = os.path.join(ROOT, "packs", "example-town", "fotos-medicos")
ENV_FILE = os.environ.get("TURNOS_DEMO_IMAGE_ENV_FILE", "")

MODEL = "gpt-image-2"
GEN_SIZE = "1024x1024"
OUT_SIZE = 512
QUALITY = "medium"

# Nombres de pila del pack, separados por género — el retrato tiene que
# coincidir con el nombre que se muestra al lado, si no la demo se ve rota.
FEMENINOS = {
    "ana", "maría", "laura", "patricia", "silvia", "claudia", "mariana",
    "valentina", "camila", "florencia", "julieta", "gabriela", "verónica",
    "lucía", "carolina", "marina", "fernanda", "paula", "cecilia", "agustina",
    "rocío", "victoria", "antonella", "milagros", "sabrina",
}

# Variación deliberada: sin esto gpt-image-2 devuelve 50 veces la misma cara
# de 40 años, pelo oscuro, fondo gris.
EDADES = [
    "in their early 20s", "in their late 20s",
    "in their early 30s", "in their mid 30s", "in their late 30s",
    "in their early 40s", "in their mid 40s", "in their early 50s",
    "in their late 50s", "in their early 60s", "in their late 60s",
    "in their early 70s",
]
RASGOS_F = [
    "long dark hair tied back", "short grey bob", "shoulder-length auburn hair",
    "curly black hair", "straight brown hair", "short blonde hair",
    "dark hair in a bun", "wavy chestnut hair with glasses",
    "a shaved head", "short natural curly hair", "long straight silver hair",
]
# Deliberate weight toward a full head of hair, but a real minority bald or
# balding — a demo roster that's 50 people with 50 different hairlines
# should include a few, not zero.
RASGOS_M = [
    "short greying hair and a trimmed beard", "clean-shaven with dark hair",
    "bald with glasses", "salt-and-pepper hair", "short black hair",
    "light brown hair and stubble", "receding grey hairline",
    "dark hair with thick-rimmed glasses", "fully bald, clean-shaven",
    "shaved head with a short beard", "balding with grey hair at the sides",
]
# Body type varies independently of everything else — mostly average/fit,
# a real minority heavier-set, same "a few, not zero" logic as baldness.
CONSTITUCIONES = [
    "slim build", "slim build", "athletic build", "average build",
    "average build", "stocky build", "heavyset build", "obese build",
]
TONOS = [
    "light olive skin", "tan skin", "fair skin", "medium brown skin",
    "olive skin", "light skin with freckles",
]
# Argentina de la costa: mezcla criolla, italiana, española, con presencia
# de comunidades del interior y países limítrofes. Fallback para apellidos
# que no están en ORIGEN_POR_APELLIDO (abajo).
ORIGENES = [
    "of Italian-Argentine descent", "of Spanish-Argentine descent",
    "of criollo Argentine background", "of mestizo Argentine background",
    "of Argentine background with indigenous features",
]

# El roster de example-town mezcla apellidos de comunidades inmigrantes
# reales de Argentina (Levante, Europa del Este, Asia, comunidad judía,
# oeste de África, quechua) además de la base ítalo-española — el retrato
# tiene que coincidir con el ORIGEN del apellido igual que ya coincide con
# el género del nombre, si no la mitad del punto de tener apellidos
# diversos se pierde. Clave en minúscula, sin tilde en las que no las
# llevan.
ORIGEN_POR_APELLIDO = {
    "wu": ("of Chinese-Argentine descent, East Asian features",
           ["light tan skin", "fair skin"]),
    "chen": ("of Chinese-Argentine descent, East Asian features",
             ["light tan skin", "fair skin"]),
    "park": ("of Korean-Argentine descent, East Asian features",
             ["fair skin", "light tan skin"]),
    "kim": ("of Korean-Argentine descent, East Asian features",
            ["fair skin", "light tan skin"]),
    "kowalski": ("of Polish-Argentine descent, Slavic features",
                 ["fair skin", "light skin with freckles"]),
    "petrenko": ("of Ukrainian-Argentine descent, Slavic features",
                 ["fair skin", "light skin with freckles"]),
    "nowak": ("of Polish-Argentine descent, Slavic features",
              ["fair skin", "light skin with freckles"]),
    "haddad": ("of Syrian-Lebanese-Argentine descent, Levantine Arab features",
               ["olive skin", "tan skin"]),
    "nazar": ("of Syrian-Lebanese-Argentine descent, Levantine Arab features",
              ["olive skin", "tan skin"]),
    "karam": ("of Syrian-Lebanese-Argentine descent, Levantine Arab features",
              ["olive skin", "tan skin"]),
    "katz": ("of Ashkenazi Jewish-Argentine descent",
             ["fair skin", "light olive skin"]),
    "levin": ("of Ashkenazi Jewish-Argentine descent",
              ["fair skin", "light olive skin"]),
    "grynberg": ("of Ashkenazi Jewish-Argentine descent",
                 ["fair skin", "light olive skin"]),
    "singh": ("of Indian-Argentine descent, South Asian features",
              ["medium brown skin", "tan brown skin"]),
    "sharma": ("of Indian-Argentine descent, South Asian features",
               ["medium brown skin", "tan brown skin"]),
    "diallo": ("of Senegalese-Argentine descent, Black West African features",
               ["dark brown skin", "deep brown skin"]),
    "walsh": ("of Irish-Argentine descent",
              ["fair skin", "light skin with freckles"]),
    "schmidt": ("of German-Argentine descent",
                ["fair skin", "light tan skin"]),
    "quispe": ("of Andean-Argentine descent, Quechua indigenous features",
               ["medium brown skin", "tan brown skin"]),
}
# La ropa la marca la especialidad: bata para clínicas, ambo para las
# quirúrgicas/estudios, saco para salud mental.
AMBO = {"Traumatología", "Odontología", "Diagnóstico por imágenes", "Laboratorio",
        "Ginecología y Obstetricia", "Otorrinolaringología"}
SACO = {"Psicología", "Psiquiatría", "Nutrición", "Fonoaudiología"}


def _vestimenta(esp):
    if esp in AMBO:
        return "wearing medical scrubs"
    if esp in SACO:
        return "wearing a smart casual blazer over a plain shirt"
    return "wearing a white medical coat over a plain shirt"


def _origen_y_tono(apellido, rnd):
    """Apellido -> (frase de origen, tono de piel). Usa ORIGEN_POR_APELLIDO
    cuando el apellido señala una comunidad específica; si no, cae al pool
    genérico ítalo-criollo de siempre."""
    mapeado = ORIGEN_POR_APELLIDO.get((apellido or "").strip().lower())
    if mapeado:
        origen, tonos = mapeado
        return origen, rnd.choice(tonos)
    return rnd.choice(ORIGENES), rnd.choice(TONOS)


def _prompt(med):
    """Prompt determinístico por médico: mismo id -> misma cara siempre, así
    que regenerar el pack no baraja las caras de nuevo."""
    rnd = random.Random(med["id"])
    pila = (med.get("nombre_pila") or "").strip().lower()
    fem = pila in FEMENINOS
    rasgos = rnd.choice(RASGOS_F if fem else RASGOS_M)
    genero = "woman" if fem else "man"
    origen, tono = _origen_y_tono(med.get("apellido"), rnd)
    constitucion = rnd.choice(CONSTITUCIONES)
    return (
        f"Professional headshot portrait photograph of a fictional {genero} "
        f"{rnd.choice(EDADES)}, {origen}, with {tono}, a {constitucion}, "
        f"and {rasgos}. A healthcare professional {_vestimenta(med.get('especialidad',''))}, "
        "photographed from the shoulders up, facing the camera with a calm, "
        "friendly, approachable expression. Soft even studio lighting, plain "
        "light neutral grey-green background, shallow depth of field, sharp "
        "focus on the face, centred composition with headroom. "
        "Photorealistic corporate portrait. "
        "IMPORTANT: this is a completely fictional, computer-generated person "
        "invented for a software demo. It must NOT resemble, depict or be a "
        "likeness of any real, existing or public person. No text, no watermark, "
        "no logo, no name badge, no lanyard text, no stethoscope brand."
    )


# Retratos que NO son médicos y por eso no salen de medicos.json. Sofía
# Herrera es la paciente del recorrido de la demo (seed_demo_all.DEMO_PATIENT,
# nacida en 1953): antes reusaba la foto de una médica y la ficha del paciente
# mostraba a una profesional de guardapolvo.
EXTRA = {
    "paciente-demo": (
        "Portrait photograph of a fictional woman in her early 70s, of "
        "Spanish-Argentine descent, with light olive skin, short wavy grey hair "
        "and reading glasses, wearing a plain lilac blouse and a light cardigan. "
        "Photographed from the shoulders up, facing the camera with a warm, "
        "relaxed smile. Soft even natural lighting, plain light neutral "
        "grey-green background, shallow depth of field, sharp focus on the face, "
        "centred composition with headroom. Photorealistic everyday portrait of "
        "an ordinary retired person, NOT a medical professional, no white coat, "
        "no scrubs, no uniform. "
        "IMPORTANT: this is a completely fictional, computer-generated person "
        "invented for a software demo. It must NOT resemble, depict or be a "
        "likeness of any real, existing or public person. No text, no watermark, "
        "no logo."
    ),
}


def _api_key():
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    if ENV_FILE:
        try:
            with open(ENV_FILE, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("OPENAI_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    sys.exit("falta OPENAI_API_KEY (env o TURNOS_DEMO_IMAGE_ENV_FILE)")


def _generar(prompt, key, intentos=3):
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "size": GEN_SIZE,
        "quality": QUALITY, "n": 1,
    }).encode()
    ultimo = None
    for i in range(intentos):
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
            return base64.b64decode(data["data"][0]["b64_json"])
        except urllib.error.HTTPError as e:
            ultimo = f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
        except Exception as e:  # noqa: BLE001 — red/timeout: reintentar
            ultimo = repr(e)
    raise RuntimeError(ultimo)


def _guardar(png_bytes, destino):
    """1024 PNG -> 512 JPEG, que es lo que ya servía el pack."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    im = im.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
    im.save(destino, "JPEG", quality=88, optimize=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerar los que ya existen")
    ap.add_argument("--only", action="append", help="sólo estos ids")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    with open(MEDICOS, encoding="utf-8") as fh:
        raw = json.load(fh)
    medicos = raw if isinstance(raw, list) else raw.get("medicos", raw)
    # Los EXTRA viajan con el mismo pipeline (mismo tamaño, mismo recorte),
    # sólo que su prompt es fijo en vez de derivarse del catálogo.
    trabajos = [(m["id"], m.get("nombre"), m.get("especialidad"), _prompt(m)) for m in medicos]
    trabajos += [(k, k, "extra", p) for k, p in EXTRA.items()]
    if args.only:
        solo = set(args.only)
        trabajos = [t for t in trabajos if t[0] in solo]

    os.makedirs(OUT_DIR, exist_ok=True)
    pendientes = []
    for mid, nombre, esp, prompt in trabajos:
        destino = os.path.join(OUT_DIR, mid + ".jpg")
        if os.path.isfile(destino) and not args.force:
            continue
        pendientes.append((mid, nombre, esp, prompt, destino))

    if args.dry_run:
        for mid, nombre, esp, prompt, _ in pendientes:
            print(f"--- {mid} ({nombre}, {esp})\n{prompt}\n")
        print(f"{len(pendientes)} retratos pendientes")
        return 0
    if not pendientes:
        print("nada que generar (usá --force para regenerar)")
        return 0

    key = _api_key()
    print(f"generando {len(pendientes)} retratos con {MODEL} ({QUALITY})…")
    fallos = []

    def tarea(par):
        mid, _nombre, _esp, prompt, destino = par
        _guardar(_generar(prompt, key), destino)
        return mid

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(tarea, p): p for p in pendientes}
        for n, fut in enumerate(as_completed(futs), 1):
            mid = futs[fut][0]
            try:
                fut.result()
                print(f"  [{n}/{len(pendientes)}] ok {mid}")
            except Exception as e:  # noqa: BLE001
                fallos.append(mid)
                print(f"  [{n}/{len(pendientes)}] FALLÓ {mid}: {e}", file=sys.stderr)

    print(f"listo: {len(pendientes) - len(fallos)} generados, {len(fallos)} fallidos")
    if fallos:
        print("reintentá con: --only " + " --only ".join(fallos), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
