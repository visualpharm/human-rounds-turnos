#!/usr/bin/env python3
"""Deriva las variantes de la pestaña (favicon) de Human Rounds desde UN master.

Master: `static/human-rounds-favicon-mark.png` — el doctor volador solo (no las
tres figuras en ronda: ese es `human-rounds-mark.png`/`logo.png`, el isotipo
grande de la marca, que NO pasa por este script porque a tamaño de pestaña las
tres figuras se leen como una mancha — de ahí el mark separado, más simple,
sólo para acá). Todo lo demás se genera acá y no se edita a mano:

  human-rounds-mark-padded.png     master reencuadrado con margen grande (1024)
  human-rounds-mark-32.png         pestaña pública, barra clara (marca negra)
  human-rounds-mark-32-dark.png    pestaña pública, barra oscura (marca blanca)
  human-rounds-mark-180.png        apple-touch-icon
  human-rounds-mark-32-admin.png       pestaña /admin, barra clara (azul marca)
  human-rounds-mark-32-admin-dark.png  pestaña /admin, barra oscura (azul claro)

Por qué hay dos colores por pestaña: la barra del navegador no la pinta la
página. Con la barra en oscuro una marca negra sobre transparente desaparece, y
con la barra en claro desaparece la blanca (ídem el par azul del admin). Se
sirven las dos y decide `media="(prefers-color-scheme: dark)"` en los <link>
que arma `theme.favicon()` para el público, y los que admin.html escribe
directo para el panel.

El apple-touch-icon va aparte y NO tiene variante oscura: iOS ignora
prefers-color-scheme para el ícono de la pantalla de inicio y rellena la
transparencia con negro, así que ese lleva fondo blanco sólido.

El favicon de /admin usa el MISMO master que el público, recoloreado en los
dos azules de marca (`#296eaa` / `#8fb8de`) en vez de negro/blanco — así la
pestaña del panel se distingue de la del sitio público sin ser un ícono
distinto. `static/logo-admin.png` (círculo azul noche) sigue existiendo para
el mark de la UI interna (drawer, header del panel) — eso NO es un favicon y
no lo toca este script.

Uso: python3 scripts/build_brand_marks.py
"""
import os

from PIL import Image, ImageChops

AQUI = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(AQUI), "static")

MASTER = os.path.join(STATIC, "human-rounds-favicon-mark.png")

# Qué fracción del lienzo ocupa la marca. El master venía casi al ras (0.93) y
# en la pestaña se leía apretado contra los bordes; 0.62 le deja ~19% de aire
# por lado, que es el margen con el que respira el resto de la identidad —
# válido a 1024/180px, donde sobra resolución. A 32px ese mismo aire deja la
# marca reducida a un borrón diminuto en el centro del ícono de pestaña
# (the product owner, 2026-08-05: "why is it so tiny... make the figure full 32x32 without
# margins"), así que la pestaña usa una ocupación aparte, casi al ras.
OCUPACION = 0.62
OCUPACION_PESTANIA = 0.96


def _recuadrar(im, lado, ocupacion=OCUPACION):
    """Devuelve la marca centrada en un lienzo cuadrado de `lado`, escalada para
    que su lado mayor ocupe `ocupacion` del lienzo. Recorta primero el aire que
    ya traiga el master, así el margen final es el que pide esta función y no la
    suma accidental de dos."""
    caja = im.getchannel("A").getbbox()
    marca = im.crop(caja)
    objetivo = int(round(lado * ocupacion))
    escala = objetivo / max(marca.size)
    nuevo = (max(1, int(round(marca.width * escala))),
             max(1, int(round(marca.height * escala))))
    marca = marca.resize(nuevo, Image.LANCZOS)
    lienzo = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    lienzo.paste(marca, ((lado - nuevo[0]) // 2, (lado - nuevo[1]) // 2))
    return lienzo


def _pintar(im, rgb):
    """Recolorea la tinta (negra) al color pedido y trata el blanco (el
    guardapolvo del doctor) como un HUECO transparente, no como relleno
    sólido: el master no es una silueta de un solo color, tiene tinta negra Y
    guardapolvo blanco opacos los dos, y pintar todo el alfa de un solo color
    fundía el guardapolvo con el cuerpo — a 32px eso lo dejaba como una
    mancha sin forma (the product owner, 2026-08-05: "the white lab coat is black/filled
    now"). La luminancia decide: negro (L=0) → tinta opaca; blanco (L=255) →
    hueco (alfa 0, se ve la barra del navegador atrás, igual que el fondo);
    los grises intermedios (antialias entre tinta y guardapolvo) se
    interpolan solos porque la multiplicación es continua."""
    tinta = ImageChops.invert(im.convert("L"))       # negro→255, blanco→0
    alfa = ImageChops.multiply(im.getchannel("A"), tinta)
    solido = Image.new("RGBA", im.size, rgb + (255,))
    solido.putalpha(alfa)
    return solido


NEGRO = (17, 17, 17)
BLANCO = (255, 255, 255)
AZUL_MARCA = (0x29, 0x6e, 0xaa)
AZUL_CLARO = (0x8f, 0xb8, 0xde)


def main():
    master = Image.open(MASTER).convert("RGBA")
    salidas = []

    def guardar(nombre, im):
        ruta = os.path.join(STATIC, nombre)
        im.save(ruta, optimize=True)
        salidas.append((nombre, im.size))

    guardar("human-rounds-mark-padded.png", _recuadrar(master, 1024))

    pestania = _recuadrar(master, 32, OCUPACION_PESTANIA)
    guardar("human-rounds-mark-32.png", _pintar(pestania, NEGRO))
    guardar("human-rounds-mark-32-dark.png", _pintar(pestania, BLANCO))
    guardar("human-rounds-mark-32-admin.png", _pintar(pestania, AZUL_MARCA))
    guardar("human-rounds-mark-32-admin-dark.png", _pintar(pestania, AZUL_CLARO))

    apple = Image.new("RGBA", (180, 180), BLANCO + (255,))
    apple.alpha_composite(_pintar(_recuadrar(master, 180), NEGRO))
    guardar("human-rounds-mark-180.png", apple)

    for nombre, tam in salidas:
        print("%-34s %dx%d" % (nombre, tam[0], tam[1]))


if __name__ == "__main__":
    main()
