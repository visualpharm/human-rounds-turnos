"""Mantiene calientes los caches de TTL corto del camino público.

Media docena de módulos (catalog, theme, branding, config, auth_mode,
turnstile) guardan su lectura durante 3 segundos para no repegarle a la base
ni al disco en cada request. El número es correcto para lo que protege — un
cambio hecho desde el panel tiene que verse enseguida, y `invalidate()` sólo
cubre las escrituras de ESTE proceso, no las de un segundo contenedor — pero
3 segundos son menos de lo que tarda una persona en abrir la página: nadie
entra dos veces seguidas en esa ventana, así que el cache no llegaba a
servirle a NADIE. Cada visitante encontraba todo vencido y pagaba el refill
completo en su propio request (medido contra la demo: ~104 ms en frío contra
~54 ms en caliente, sobre 42 ms de ida y vuelta de red).

La solución no es alargar el TTL — eso sí cambia cuánto tarda en verse una
edición del panel. Es sacar el refill del request: este hilo relee lo mismo
que releería un visitante, cada INTERVALO segundos, de fondo. La ventana de
staleness no se mueve ni un milisegundo: cada cache sigue venciendo a su
propio TTL y `invalidate()` sigue mandando. Lo único que cambia es QUIÉN
espera el refill, y pasa a ser un hilo que no tiene a nadie del otro lado.
"""

import os
import threading
import time

# Más corto que el TTL más corto que precalienta (3 s), para que el cache
# nunca llegue a vencer con la página en la mano de alguien.
INTERVALO = 2.0

_hilo = None


def _lecturas():
    """Las lecturas públicas que llenan cada cache, una por módulo.

    Son exactamente las que hace un request normal — nada de tocar internals:
    si mañana un módulo cambia cómo cachea, esto se sigue calentando solo.
    """
    from app.auth import mode as auth_mode
    from app.patient_web import branding
    from app.patient_web import catalog
    from app.core import config
    from app.core import theme
    from app.auth import turnstile

    return [
        # El overlay del catálogo es el único que consulta la base, y es el
        # que más pesa: una query por tipo.
        catalog.centers,
        catalog.services,
        catalog.doctors,
        catalog.specialties,
        catalog.preparations,
        catalog.knowledge,
        # Chrome de toda página servida.
        theme.current,
        branding.current,
        auth_mode.current,
        # `config.get` es el que llena el cache de la tabla de overrides; la
        # clave da igual, cualquier lectura sirve.
        lambda: config.get("site_name"),
        # Login y alta de cuenta.
        turnstile.encendido,
    ]


def _warm_once(lecturas):
    for leer in lecturas:
        try:
            leer()
        except Exception:  # noqa: BLE001 — un módulo caído no frena a los demás
            pass
    # El atlas de avatares tiene un TTL largo (avatar_sprite.TTL), pero se
    # invalida solo cuando demo_refresh.py recopia las fotos con mtimes
    # nuevos. Tocarlo acá vale lo mismo que un dict lookup mientras está
    # vigente, y evita que la reconstrucción caiga en el primer visitante de
    # después del refresh.
    try:
        from app.patient_web import site_doctors
        site_doctors.warm_avatar_sprite()
    except Exception:  # noqa: BLE001
        pass
    _warm_idiomas()


def _warm_idiomas():
    """Los dos caches del camino traducido, que son los que más duelen.

    Una página en un idioma que no es el del pack pide una traducción por
    cada nombre de servicio, cada título de rubro y cada descripción de
    centro. `translate` las lee todas de una y las memoiza 30 s, y
    `pack_l10n` memoiza las filas ya traducidas otros 30 s — pero 30 s le
    pasa lo mismo que a los 3 s de acá arriba: se vencen entre visitante y
    visitante, y el que llega paga la recarga entera. Con el pack de la demo
    en inglés eso eran 587 queries y 11 segundos de home (medido contra la
    demo, 2026-08-06).
    """
    try:
        from app.core import pack_l10n
        from app.patient_web import site_catalog
        from app.core import translate
        from app.ops import translate_warmup
        idiomas = translate_warmup.pending_languages()
    except Exception:  # noqa: BLE001
        return
    for lang in idiomas:
        try:
            translate.warm_rows(lang)
        except Exception:  # noqa: BLE001
            continue
        for kind in pack_l10n.FIELDS:
            try:
                pack_l10n.localize(kind, site_catalog.canonical_rows(kind), lang)
            except Exception:  # noqa: BLE001
                pass


def _loop():
    try:
        lecturas = _lecturas()
    except Exception:  # noqa: BLE001
        return
    while True:
        _warm_once(lecturas)
        time.sleep(INTERVALO)


def start():
    """Arranca el hilo. Devuelve False si ya estaba andando o está apagado."""
    global _hilo
    if os.environ.get("TURNOS_CACHE_WARMUP_OFF") == "1":
        return False
    if _hilo is not None and _hilo.is_alive():
        return False
    _hilo = threading.Thread(target=_loop, name="cache-warmup", daemon=True)
    _hilo.start()
    return True
