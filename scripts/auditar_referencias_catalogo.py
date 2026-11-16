"""Ids que la base guarda y el catálogo ya no tiene — sólo lectura.

Revisión de esquema №10 (2026-10-24): médicos, servicios y centros son JSON
(el pack de datos más lo que edita el panel), así que la base no puede
verificar los ids que otras tablas guardan sobre ellos. El panel nunca cambia
un id; sólo desaparece cuando una actualización del pack lo renombra o lo
quita, y entonces sus turnos salen de las listas y de los reportes sin ningún
error. Correr este script ANTES de publicar un pack nuevo (o después de
publicarlo, para ver qué quedó colgando): lista cada id guardado que el
catálogo no conoce, con la tabla, la columna y cuántas filas lo usan.

Un médico, servicio o centro DESHABILITADO no cuenta como roto (una solicitud
conserva a su médico cuando se lo apaga); sólo cuenta un id que el catálogo
no tiene en absoluto. No escribe nada: no tiene modo --apply. Si el catálogo
o la base no se pueden leer, falla con un error — nunca responde «todo bien»
por falta de datos.

Correr desde el directorio de la app (en el contenedor, /app):
    python3 scripts/auditar_referencias_catalogo.py          # informe de texto
    python3 scripts/auditar_referencias_catalogo.py --json   # salida para máquinas
Código de salida: 0 sin referencias rotas, 1 con alguna, 2 si no pudo leer.
Imprime ids del catálogo y cantidades de filas — ningún dato del paciente.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args(argv)

    from app.patient_web import catalog_refs
    try:
        rotas = catalog_refs.broken_references()
    except Exception as e:  # noqa: BLE001 — sin datos no hay veredicto
        print(f"No se pudo revisar: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"referencias_rotas": rotas}, ensure_ascii=False, indent=2))
    elif not rotas:
        print("Todas las referencias de la base existen en el catálogo.")
    else:
        print(f"Referencias rotas: {len(rotas)} (id guardado que el catálogo no tiene)")
        for r in rotas:
            print(f"  {r['table']}.{r['column']}  {r['id']}  ({r['rows']} filas)  [{r['tipo']}]")
    return 1 if rotas else 0


if __name__ == "__main__":
    sys.exit(main())
