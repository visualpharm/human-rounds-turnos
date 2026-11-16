"""Traduce el texto DE ADENTRO de cada meme al castellano rioplatense con
nano-banana-2/edit (fal.ai). Guarda <slug>-es.jpg al lado del original.
Se puede correr por-item: python3 translate_memes.py [slug ...]"""
import base64
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMOR = os.path.join(REPO, "static", "humor")

if not os.path.exists(os.path.join(HUMOR, "humor.json")):
    # The meme images are third-party content and stay out of the public cut.
    sys.exit("static/humor/humor.json not found: this checkout ships without the login memes")

# FAL_KEY from the environment, or from an explicitly selected local file.
KEY = os.environ.get("FAL_KEY", "")
key_file = os.environ.get("TURNOS_FAL_ENV_FILE", "")
if not KEY and key_file:
    with open(key_file, encoding="utf-8") as f:
        for line in f:
            if line.startswith("FAL_KEY="):
                KEY = line.split("=", 1)[1].strip().strip('"')
if not KEY:
    sys.exit("FAL_KEY no encontrada")

# slug -> lista de reemplazos (EN exacto -> ES exacto)
MAP = {
    "osito-quirofano": [
        ("Patient asks if I can also fix teddy bear just before being put off to sleep... how could I say no?",
         "El paciente me pidió, justo antes de la anestesia, si de paso le podía operar el osito… ¿cómo le iba a decir que no?"),
    ],
    "soy-paciente": [
        ("Sorry for the waiting", "Perdón por la espera"),
        ("No problem, I'm patient", "No hay problema, soy paciente"),
    ],
    "letra-de-medico": [
        ("DOCTORS BE LIKE..", "LA LETRA DEL MÉDICO:"),
        ("GO BUY THIS", "COMPRE ESTO"),
        ("So I bought it", "Y bueno, lo compré"),
    ],
    "mi-columna": [
        ("Doctor: This is your spine", "Médico: Esta es tu columna"),
        ("Me: wtf put it back", "Yo: ¡¿Qué?! Ponela de vuelta"),
    ],
    "primer-hijo": [
        ("My wife is going into labour what should i do?", "¡Mi esposa está por dar a luz! ¿Qué hago?"),
        ("Is this her first child?", "¿Es el primer hijo?"),
        ("No, this is her husband", "No, soy el marido"),
    ],
    "sin-cargo": [
        ("No charge bro", "Sin cargo, amigo"),
        ("You've got enough problems", "Ya tenés bastantes problemas"),
    ],
    "estas-bien-afuera": [
        ("Hi, how are you?", "Hola, ¿cómo anda?"),
        ("I'm fine thank you.", "Bien, gracias."),
        ("Alright then, get out.", "Listo, entonces afuera."),
    ],
    "capitulo-optativo": [
        ("Your case is quite complicated.", "Su caso es bastante complicado."),
        ("Why doctor? What happened?", "¿Por qué, doctor? ¿Qué tengo?"),
        ("You got a disease from the chapter which I left as optional during my studies...",
         "Le tocó una enfermedad del capítulo que dejé como optativo cuando estudiaba…"),
    ],
    "tranquilo-david": [
        ("Relax David it's just a small surgery, don't panic.", "Tranquilo, David, es una cirugía chica. No te asustes."),
        ("My name is not David.", "Mi nombre no es David."),
        ("I know. I am David.", "Ya sé. David soy yo."),
    ],
    "dr-google-whisky": [
        ("CAN I CURE THIS COLD WITH WHISKEY?", "¿ESTE RESFRÍO SE CURA CON WHISKY?"),
        ("THANKS DR. GOOGLE", "GRACIAS, DOCTOR GOOGLE"),
    ],
    "enfermedad-con-tu-nombre": [
        ("WANT THE GOOD NEWS OR THE BAD NEWS FIRST?", "¿LE DOY PRIMERO LA BUENA O LA MALA NOTICIA?"),
        ("GOOD NEWS, PLEASE, DOCTOR", "LA BUENA, DOCTOR"),
        ("WELL, THEY'RE NAMING A DISEASE AFTER YOU", "LE VAN A PONER SU NOMBRE A UNA ENFERMEDAD"),
    ],
    "no-soy-tu-novio": [
        ("what's wrong with you?", "¿Qué le pasa?"),
        ("It's nothing..", "No, nada…"),
        ("I'm your doctor, not your boyfriend", "Soy su médico, no su novio"),
    ],
    "despertate-a-la-tarde": [
        ("Doctor, my back hurts when I wake up in the morning",
         "Doctor, me duele la espalda cuando me despierto a la mañana"),
        ("Wake up in the afternoon then", "Y bueno, despertate a la tarde"),
    ],
    "lo-que-le-dijiste-a-la-enfermera": [
        ("SO........", "BUENO..."),
        ("TELL ME EVERYTHING YOU TOLD THE NURSE 5 MINUTES AGO",
         "CONTAME TODO LO QUE LE DIJISTE A LA ENFERMERA HACE 5 MINUTOS"),
    ],
    "primer-paciente": [
        ("First day of practice", "Primer día de prácticas"),
        ("My first patient", "Mi primer paciente"),
    ],
    "enfermera-sentada": [
        ("NURSE SITTING DOWN", "UNA ENFERMERA SENTADA"),
        ("CLEARLY PHOTOSHOPPED", "CLARAMENTE PHOTOSHOP"),
    ],
    "enfermeraaa": [
        ("Doctor: How are you feeling now?", "Médico: ¿Cómo se siente ahora?"),
        ("Patient: Much better", "Paciente: Mucho mejor"),
        ("Doctor: Let me know if there's anything I can do for you.",
         "Médico: Cualquier cosa que necesite, avíseme."),
        ("Patient: Can you help me with a glass of water, please?",
         "Paciente: ¿Me alcanza un vaso de agua, por favor?"),
        ("Doctor: Sure, I will... NURSE!", "Médico: Claro, ya mismo… ¡ENFERMERA!"),
    ],
    "cama-libre": [
        ("- Doctor, what happens when we die?", "— Doctor, ¿qué pasa cuando nos morimos?"),
        ("- We clean the bed and admit a new patient", "— Limpiamos la cama y entra el siguiente paciente"),
    ],
    "muerto-de-curiosidad": [
        ("Doctor, are the results of the test ready? I'm dying from curiosity",
         "Doctor, ¿están los resultados del análisis? Me muero de curiosidad"),
        ("heh", "je"),
        ("not only from curiosity", "no solo de curiosidad"),
    ],
    "tres-pastillas": [
        ("Doctor: You need to take one of this pills everyday for the rest of your life",
         "Médico: Tiene que tomar una de estas pastillas por día, por el resto de su vida"),
        ("Him: But there's only 3 pills doctor", "Paciente: Pero acá hay tres nomás, doctor"),
        ("Doctor: Exactly", "Médico: Exacto"),
    ],
}

manifest = json.load(open(os.path.join(HUMOR, "humor.json"), encoding="utf-8"))
ITEMS = {it["id"]: it for it in manifest["items"]}


def edit_one(slug):
    it = ITEMS[slug]
    src = os.path.join(HUMOR, it.get("file_original") or it["file"])
    dst = os.path.join(HUMOR, os.path.dirname(it["file"]),
                       slug + "-es.jpg")
    with open(src, "rb") as f:
        data_uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    pares = "\n".join('- "%s"  ->  "%s"' % (a, b) for a, b in MAP[slug])
    prompt = (
        "Replace the text in this meme with its Argentine Spanish translation. "
        "Keep the photos, people, composition, text positions, bubble shapes, font style, "
        "colors and sizes EXACTLY as in the original — change ONLY the words. "
        "Spelling must be perfect, including accents and signs (á é í ó ú ñ ü ¿ ¡ …). "
        "Exact replacements:\n" + pares
    )
    body = json.dumps({
        "prompt": prompt,
        "image_urls": [data_uri],
        "num_images": 1,
        "output_format": "jpeg",
    }).encode()
    req = urllib.request.Request(
        "https://fal.run/fal-ai/nano-banana-2/edit",
        data=body,
        headers={"Authorization": "Key " + KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.load(r)
    url = out["images"][0]["url"]
    with urllib.request.urlopen(url, timeout=120) as r:
        img = r.read()
    with open(dst, "wb") as f:
        f.write(img)
    return slug, len(img)


slugs = sys.argv[1:] or list(MAP)
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(edit_one, s): s for s in slugs}
    from concurrent.futures import as_completed
    for f in as_completed(futs):
        s = futs[f]
        try:
            slug, n = f.result()
            print("OK  %-26s %6.1f KB" % (slug, n / 1024))
        except Exception as e:  # noqa: BLE001
            print("ERR %-26s %s" % (s, e))
