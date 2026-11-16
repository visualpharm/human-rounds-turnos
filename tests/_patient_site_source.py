"""The code of the public patient site as one text, whichever module of the split it lives in.

`patient_web/site_*.py` hold the pages and the data views, one module per domain (`site_base` is the shared
plumbing). A source guard that pins a detail of "the patient site" reads all of it, so a function that moves
between those modules does not turn the guard red.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "app", "patient_web")


def files():
    names = sorted(n for n in os.listdir(PKG) if n.startswith("site_") and n.endswith(".py"))
    return names


def source():
    parts = []
    for name in files():
        with open(os.path.join(PKG, name), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)
