"""translate_warmup.py — keeps the pack-content translation cache filled, so
a visitor who switches language sees translated content immediately.

The render path (`pack_l10n.localize` → `translate.cached_text`) never calls
a model: a page shows hundreds of pack strings and translating them inside
the request would put hundreds of sequential OpenRouter round trips in front
of the first byte. Something has to fill the cache instead, and on a running
installation that something is this daemon — the same job
`scripts/translate_pack.py` does by hand, on a timer.

Applies ONLY where it can possibly matter: an installation with a second
language enabled. With `LANGUAGES` unset (every installation that hasn't
opted in) `start()` returns False and no thread exists — the same "true
no-op until you opt in" contract the rest of i18n keeps.

Cost is a first sweep of roughly one model call per 40 strings, then almost
nothing: a re-sweep only touches entries whose source text changed.
"""

import os
import threading
import time
from app.core import log_pii

INTERVAL_S = 6 * 60 * 60          # re-sweep four times a day
FIRST_RUN_DELAY_S = 30            # let the server finish booting first


def _log(msg):
    print(f"[translate] {msg}", flush=True)


def pending_languages():
    """The enabled languages that are not the pack's own — the ones with
    anything to translate at all."""
    from app.core import i18n
    from app.core import translate
    pack = translate._primary(translate.pack_language())
    return [lang for lang in i18n.enabled_languages()
            if translate._primary(lang) != pack]


def _drop_fake_rows(lang):
    """Clear the fake provider's markers when this installation is no longer
    faking translation.

    A demo installation runs with TURNOS_FAKE_AI=1, which used to fake
    translation too, so its cache filled with "[en] Clínica médica" — and
    because those rows carry a MATCHING source hash, a later real sweep saw
    nothing to do and every English visitor kept reading them (2026-08-04, on
    the live demo). Deleting them is what makes the switch self-healing
    instead of needing someone to go into the database."""
    from app.core import translate
    if translate._fake():
        return 0
    try:
        from app.core import db_catalog
        dropped = db_catalog.translations_drop_prefix(lang, f"[{lang}] ")
        if dropped:
            translate.invalidate_rows(lang)
        return dropped
    except Exception:  # noqa: BLE001 — never let housekeeping break the sweep
        return 0


def sweep(lang, stop=None):
    """Fill the cache for one language. Returns (strings, translated).

    `stop` reaches `translate.translate_batch` (the queue worker's stop
    request, `core.worker_stop`); the thread twin and the scripts pass none.

    Note (2026-08-04): used to also sweep `catalog.preparations()`'s
    `resumen` field — dropped, same reasoning as `scripts/translate_pack.py`'s
    module docstring: that field doesn't exist in the demo pack (0 of 30
    entries in `packs/example-town/estudios-preparacion.json`) and its proof
    surface (`translate.localized_preparation_summary()`) is called from no
    live page, so this was a silent zero-coverage line item on the
    installation that matters. See docs/architecture/i18n.md, "Filling the
    cache"."""
    from app.core import pack_l10n
    from app.patient_web import site_catalog, site_news
    from app.core import site_identity
    from app.core import translate

    dropped = _drop_fake_rows(lang)
    if dropped:
        _log(f"{lang}: dropped {dropped} placeholder translation(s) left by "
             f"the fake provider")

    pack_lang = translate.pack_language()
    entries = []
    for kind in pack_l10n.FIELDS:
        entries.extend(pack_l10n.entries(kind, site_catalog.canonical_rows(kind)))
    # especialidades.json has no pack_l10n kind of its own (it isn't a
    # per-row catalog), but the chat reads its names through the SAME
    # cache-only contract (chat_agent._specialty_names,
    # chat_tools._etiquetas_de_especialidad): without these entries the
    # sweep never warmed them and a pt/ru conversation listed specialties in
    # Spanish (residual leak 1 of the 2026-09-11 simulation).
    from app.patient_web import catalog
    for item in catalog.specialties() or []:
        nombre = str((item or {}).get("nombre") or "").strip()
        if nombre:
            entries.append((pack_l10n.key("especialidades", nombre, "nombre"),
                            nombre))
    entries.extend(site_identity.sitio_entries())
    # Las lecturas de IA de «Resultados de laboratorio» (frases cautelosas, sin
    # DNI): se guardan en castellano y el paciente las lee en su idioma.
    from app.ai import lab_language
    entries.extend(lab_language.entradas_barrido())

    stale = [(k, t) for k, t in entries
             if translate.needs_translation(k, t, lang, pack_lang=pack_lang)]

    # /news entries are literal Spanish in the code, independent of the
    # active pack's own language field — always translate.NEWS_SOURCE_LANG
    # (patient_web/site_*.py), never `pack_lang`.
    news_entries = site_news.news_translation_entries()
    entries = entries + news_entries
    stale = stale + [(k, t) for k, t in news_entries
                      if translate.needs_translation(
                          k, t, lang, pack_lang=site_news.NEWS_SOURCE_LANG)]

    if not stale:
        return len(entries), 0
    done = translate.translate_batch(stale, lang, stop=stop)
    return len(entries), len(done)


def _loop():
    time.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            for lang in pending_languages():
                total, done = sweep(lang)
                if done:
                    _log(f"{lang}: {done} of {total} pack strings translated")
        except Exception as e:  # noqa: BLE001 — degrade, never take the site down
            _log(f"sweep failed ({log_pii.exc(e)}); retrying in {INTERVAL_S}s")
        time.sleep(INTERVAL_S)


def _off():
    """THE one resolution of TURNOS_TRANSLATE_WARMUP_OFF — start() and the
    worker registry (worker._translate_warmup_enabled) both read it here,
    never a second parse. Plain-environ on purpose: this module never went
    through core.config."""
    return os.getenv("TURNOS_TRANSLATE_WARMUP_OFF") == "1"


def _interval_min():
    """THE one cadence truth: the thread loop's INTERVAL_S expressed in the
    minutes the queue registration (worker.py) feeds to the exact-or-reject
    cron derivation. A module constant, deliberately not an env knob —
    same value in every topology."""
    return INTERVAL_S // 60


def start():
    """Launches the daemon thread. Returns False when it does not apply.

    Tier 4 worker-topology (periodic-daemon batch 3): with
    TURNOS_QUEUE_WORKER=1 the SEPARATE worker
    process owns the sweep via ops/translate_warmup_queue.py and no
    thread starts here — exactly one mechanism per topology
    (TestDelegationMatrix). The OFF flag wins over delegation, same as
    the batch-2 twins; delegation is checked BEFORE the language no-op
    (matching chart_summary.start()'s order) because in worker mode
    eligibility is the worker's per-tick business — re-resolved on
    every sweep — and evaluating it here would only spend a redundant
    config/DB read on a decision this process no longer owns."""
    if _off():
        return False
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"sweep delegated to the worker "
             f"(TURNOS_QUEUE_WORKER={modo_worker}) — no thread here")
        return False
    try:
        if not pending_languages():
            return False
    except Exception:  # noqa: BLE001
        return False
    threading.Thread(target=_loop, name="translate-warmup", daemon=True).start()
    return True


if __name__ == "__main__":  # manual run: python3 -m translate_warmup
    for _lang in pending_languages():
        print(_lang, sweep(_lang))
