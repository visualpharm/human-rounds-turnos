"""Qué sirve como nombre de usuario para entrar al panel.

UNA regla, un solo lugar: la usan el alta de cuentas del panel
(`admin_panel/admin_users.py`) y el asistente de instalación (`ops/setup.py`),
y el navegador la espeja en `static/platform/admin-users.mjs` (`validUsername`).
Si cambia acá, cambia allá.

Historia: hasta 2026-09-05 la regla era `^[a-zA-Z0-9_.-]{3,40}$`. Ese tope de
40 y ese alfabeto ASCII dejaban afuera un apellido con tilde o con ñ y obligaban
a la mesa de admisión a inventar abreviaturas (responsable del producto: "no limites el usuario por
cantidad de caracteres, dales más libertad para nombrarlos"). Ahora sólo pedimos
lo que el login necesita de verdad: al menos 2 caracteres visibles y ningún
espacio ni carácter de control — un espacio al final es un login que falla sin
que nadie entienda por qué. El tope de 120 existe para que el nombre entre en
una tabla, no como límite de diseño: `operators.username` es `text`.
"""

import re

MIN = 2
MAX = 120

# Espacios de cualquier tipo (\s cubre también el no-rompible: en Python 3 re
# es Unicode por defecto) y caracteres de control.
_BAD = re.compile(r"[\s\x00-\x1f\x7f]")


def valid(usuario):
    """True si `usuario` sirve como nombre de login."""
    usuario = usuario or ""
    if not (MIN <= len(usuario) <= MAX):
        return False
    return not _BAD.search(usuario)
