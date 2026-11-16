#!/usr/bin/env python3
"""Copy the end-to-end run's screenshots into the desk guide's pictures.

    python3 scripts/hsi_extension_guide_images.py <e2e outDir>

<e2e outDir> is the folder extensions/hsi-humanizador/test/e2e-hsi.mjs wrote: a run
against the HSI test copy with fictional patients. The two «Pacientes de hoy»
pictures are cropped to the list itself (macOS `sips`).
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "extensions", "guia", "img")

# (run file, guide file, crop as (height, width, top, left) or None)
PICTURES = [
    ("01-ajustes.png", "ajustes.png", (640, 760, 0, 340)),
    ("02-pacientes-de-hoy.png", "pacientes-de-hoy.png", (510, 1140, 20, 16)),
    ("03-hsi-listo-para-confirmar.png", "hsi-completo.png", None),
    # Only the saved row and its neighbour: the rows below carry other checks' states.
    ("05-fila-guardada.png", "fila-guardada.png", (180, 1140, 20, 16)),
    ("04-hsi-guardado-y-oferta.png", "guardar-solo.png", None),
]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run = sys.argv[1]
    os.makedirs(IMG, exist_ok=True)
    for src, dst, crop in PICTURES:
        out = os.path.join(IMG, dst)
        shutil.copyfile(os.path.join(run, src), out)
        if crop:
            h, w, top, left = crop
            subprocess.run(["sips", "-c", str(h), str(w), "--cropOffset", str(top), str(left), out],
                           check=True, stdout=subprocess.DEVNULL)
        print(os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
