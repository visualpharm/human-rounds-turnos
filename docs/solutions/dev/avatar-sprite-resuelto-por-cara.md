# El atlas de avatares se resolvía UNA VEZ POR CARA, y cada vuelta rearmaba el padrón entero

2026-08-05. Síntoma reportado: en `demo.humanrounds.org` el directorio de la home
tardaba cerca de un minuto en aparecer debajo del buscador.

## Qué pasaba

`site_doctors._avatar_sprite()` no tenía caché propia:

```python
return avatar_sprite.styles([m.get("id") for m in _professionals()])
```

`avatar_sprite.styles()` **sí** cachea (5 s de TTL, invalidada por época), así que
la parte cara no era esa: era construir el ARGUMENTO. `_professionals()` →
`_doctors()` → `_load("medicos.json")` + `catalog.merge_with_overlay()`, cada vez.

Y los llamadores piden **por cara**, no por página:

| endpoint | llamadas a `_avatar_sprite()` por request |
|---|---|
| `/api/directory` | 62 (una por médico con foto, en 13 clusters) |
| `/api/doctors` | 90 (`_doctor_avatar_fields` por fila) |

Medido con `cProfile`: ~1400 `stat()` y ~200 `listdir()` por request de directorio,
y el padrón completo rearmado ~63 veces para pintar una sola página.

## Por qué se veía en producción y no en la laptop

7,2 ms de mediana en local — invisible. Pero es trabajo de **I/O**, no de CPU: en el
Vultr de 2 vCPU, con el `manage.py demo seed` copiando 50 fotos por encima, esos
miles de syscalls se ponen en la cola detrás del seed y el endpoint se va a decenas
de segundos. **Un N+1 de syscalls no se nota hasta que el disco tiene otro dueño.**

## El arreglo

Memoizar la búsqueda del atlas en `patient_web/site_*.py` con la MISMA ventana que ya usa
`avatar_sprite.styles()` para sostener su resultado, y bajarla con el mismo
`invalidate()` — comparten `avatar_sprite._epoch`, así que la foto que sube un admin
sigue entrando al instante y no hay una segunda política de frescura que mantener.

`/api/directory`: 7,2 ms → 0,67 ms.

## La regla

**Si una función auxiliar se llama una vez por ítem de una lista, mirá qué hace para
armar sus argumentos, no sólo si su resultado está cacheado.** Una caché que devuelve
en O(1) no salva nada si calcular la clave cuesta una relectura del catálogo. Acá el
patrón sigue vivo en `/api/doctors` (93 ms) y `/api/services` (40 ms): `_services()`
se rearma ~390 veces por par de requests, misma forma, todavía sin tocar — el arreglo
prolijo es una memo por request, no un TTL, porque los tests monkeypatchean
`site_base._load` y un TTL los dejaría inertes (lo advierte el docstring de
`catalog.merge_with_overlay`).

Relacionado: [data-packs-resolved-path-cache-key.md](data-packs-resolved-path-cache-key.md).
