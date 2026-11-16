"""translate.py — machine translation of PACK CONTENT (service names,
preparation summaries, center descriptions, knowledge articles), cached in
Postgres so toggling a visitor's language never re-translates the same text
twice (phase 3a of the open-source rework, 2026-07-30).

This is the sibling of `prompts.py`'s canonical/edited engine, generalized
and with the roles reversed: `prompts.py` keeps ENGLISH canonical (AI
instructions are open-source-friendly in English, staff edit the Spanish
translation); this module keeps whatever the PACK's own language is
canonical (today, always Spanish — `packs/*/pack.json`'s `language` field)
and produces machine translations INTO every other enabled language on
demand. It reuses the same OpenRouter call shape and `TURNOS_FAKE_AI`
contract as `prompts.py`, deliberately NOT importing it (see that module's
own docstring on why: avoiding an import cycle, and because the two engines
solve different problems — this one caches by content hash, `prompts.py`
does a paragraph-aligned 3-way merge for an EDITED document).

Cache contract (table `translations`, Alembic baseline):

    (source_key, lang) -> {source_hash, content, model, override}

  - `source_key` names WHAT is being translated (e.g.
    "modules.turnos.preparation.laboratorio-sangre.resumen") — stable across content
    edits, so a re-translation replaces the row instead of orphaning it.
  - `source_hash` is a hash of the CURRENT source text. A cache hit only
    happens when the stored hash matches: the source changed -> the cache
    is stale -> re-translate. The source DIDN'T change -> the row is
    reused, zero model calls, no matter how many times a visitor toggles
    the language.
  - `override=true` rows are an admin's manual edit (a future admin
    surface, not built this slice — `db_catalog.translation_upsert(...,
    override=True)` is the write path already there for it) and win
    regardless of `source_hash`, same as `catalog`'s DB-row-over-JSON-base
    contract: hand-edited always beats machine-generated.

Degradation: no OPENROUTER_API_KEY, the network down, or a DB outage never
raise past this module's own callers in a way that breaks a page — a
missing translation falls back to the original-language text (see
`localized_text()`), same "the site never goes down over an AI feature"
criterion as `ai_referral.py`/`prompts.py`.
"""

import hashlib
import re
import urllib.error

from app.core import ai_policy, config, i18n
from app.ai import ai_client

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
from app.core.ai_policy import DEFAULT_MODEL

_LANG_NAMES = {
    "es": "Rioplatense Spanish",
    "en": "English",
    "pt": "Brazilian Portuguese",
    "fr": "French",
    "it": "Italian",
    "de": "German",
    "ru": "Russian",
    "uk": "Ukrainian",
    "ja": "Japanese",
    "zh": "Simplified Chinese",
}

# Bumped every time the machine-translation path actually runs (fake or
# real) — never on a cache hit. Tests use this to assert "toggle twice,
# translate once" without mocking the network.
_call_count = {"n": 0}


class TranslationUnavailable(Exception):
    """No AI available to translate (no key, down, or an unusable response)."""


def call_count():
    return _call_count["n"]


def reset_call_count():
    _call_count["n"] = 0


def _hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24]


def _fake():
    """`TURNOS_FAKE_TRANSLATE` first, `TURNOS_FAKE_AI` as the fallback — the
    same per-feature escape hatch `chat_agent` uses for
    `TURNOS_FAKE_CHAT_AI`, and for the same reason.

    `TURNOS_FAKE_AI` exists so a demo installation never sends a VISITOR's
    document or message to a provider. Translation has nothing to do with a
    visitor: it translates the installation's own public catalog, which is
    already on the open internet. Faking it there just serves "[en] Clínica
    médica" to everyone who switches language (2026-08-04, caught on the live
    demo), so demo mode turns this one back on."""
    override = config.get("TURNOS_FAKE_TRANSLATE")
    if override not in (None, ""):
        return override == "1"
    return config.get("TURNOS_FAKE_AI") == "1"


def _language_name(lang):
    return _LANG_NAMES.get((lang or "").lower(), (lang or "").upper())


def _fake_translate(text, lang):
    """No network (tests): tags the target language and keeps the text —
    enough to prove the cache/hash machinery, which is what this module is
    actually testing; the wording itself is prompts.py's problem."""
    return f"[{lang}] {text}"


def _call_openrouter(text, lang):
    if not ai_client.available():
        raise TranslationUnavailable("falta configuración de IA")
    system = (
        "You translate short pieces of medical-appointment website content "
        "(a service name, a preparation summary, a center description) for "
        "an Argentine public hospital's appointment system. Translate "
        "faithfully — this is patient-facing informational content, not "
        "marketing copy: never summarise, never invent, never drop a detail "
        "(a fasting requirement, a document to bring). Return ONLY the "
        f"translated text, no preamble, no quotes. Target language: "
        f"{_language_name(lang)}. If the text contains bracketed tokens "
        "like [[1]], [[2]], copy them through exactly, unchanged, in the "
        "same relative position — they stand in for markup a caller "
        "restores afterwards. When the text reads like editorial prose for "
        "patients (e.g. a product update) rather than a short catalog "
        "field, translate it so it reads naturally to a native speaker."
    )
    body = {
        "model": ai_policy.model(),
        "temperature": 0,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": text}],
    }
    try:
        data = ai_client.call(body, timeout=60)
    except (urllib.error.URLError, OSError, ValueError):
        raise TranslationUnavailable("la inferencia no estuvo disponible")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise TranslationUnavailable("respuesta sin contenido")
    content = (content or "").strip()
    if not content:
        raise TranslationUnavailable("respuesta vacía")
    return content


def _machine_translate(text, lang):
    _call_count["n"] += 1
    if _fake():
        return _fake_translate(text, lang)
    return _call_openrouter(text, lang)


# ============================================================== the cache

def translate(source_key, text, lang):
    """The cached translation of `text` (identified by `source_key`) into
    `lang`. Reuses the cached row unless the source changed; an admin
    override row always wins. Raises `TranslationUnavailable` only when
    there is NO cache to fall back on AND the model call itself failed —
    callers that can't afford an exception should go through
    `localized_text()` instead, which never raises."""
    text = text or ""
    h = _hash(text)
    row = None
    try:
        from app.core import db_catalog
        row = db_catalog.translation_get(source_key, lang)
        if _poisoned(row):
            db_catalog.translations_drop_pii_placeholders(lang)
            invalidate_rows(lang)
            row = None
    except Exception:  # noqa: BLE001 — DB down: no cache, fall through to a fresh translation
        row = None

    if row and row.get("override"):
        return row["content"]
    if row and row.get("source_hash") == h:
        return row["content"]

    translated = _machine_translate(text, lang)

    try:
        from app.core import db_catalog
        db_catalog.translation_upsert(
            source_key, lang, h, translated,
            model=(ai_policy.model()))
        invalidate_rows(lang)
    except Exception:  # noqa: BLE001 — translation succeeded, caching didn't: still return it
        pass
    return translated


# ------------------------------------------------------- the read cache
#
# `cached_text()` is called once per translatable string, and a page render
# asks for hundreds: every service name, every cluster title, every center
# description. One `db_catalog.translation_get()` each means one query — and one
# CONNECTION — per string. Measured against the live demo, an English home
# page issued 587 of them and took 11 s of pure database round trips, with
# nothing left to translate; the same page in Spanish returns before any of
# this and took 1 s. Worse, the four home APIs miss together, so the misses
# multiply instead of queueing.
#
# `db_catalog.translations_all()` was written for exactly this and never wired up —
# its own docstring says "translate.py reads the whole language at once and
# memoizes it instead". This is that memo. One query per language per TTL,
# and a language is ~1100 rows, so it is small enough to just hold.
#
# The TTL is the same 30 s bound `pack_l10n`'s localized-row memo already
# accepts, and every write in this module drops it, so the only staleness
# left is a write from ANOTHER process — the same window that was there
# before, since the alternative was no cache at all.
_ROWS_TTL_S = 30.0
_rows_memo = {}  # lang -> (expires_at, {source_key: row})


def invalidate_rows(lang=None):
    """Drop the read cache — after any write, or in a test."""
    if lang is None:
        _rows_memo.clear()
    else:
        _rows_memo.pop(lang, None)


_PII_PLACEHOLDER = re.compile(r'\[(?:teléfono|telefono|phone|telephone|email|correo|e-mail|documento|document|nombre|name)\s+\d+\]', re.I)


def _poisoned(row):
    return bool(row and not row.get('override') and _PII_PLACEHOLDER.search(row.get('content') or ''))


def _rows(lang):
    """{source_key: row} for `lang`, or None if the DB could not be read.

    A DB blip serves the last map it had rather than degrading every string
    on the page to Spanish at once — the rows are a cache of a cache, and
    stale English beats a page that flips language mid-blip."""
    import time
    hit = _rows_memo.get(lang)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    try:
        from app.core import db_catalog
        rows = db_catalog.translations_all(lang)
        if any(_poisoned(row) for row in rows.values()):
            # One guarded cleanup only when an affected cache row is observed.
            db_catalog.translations_drop_pii_placeholders(lang)
            rows = {key: row for key, row in rows.items() if not _poisoned(row)}
    except Exception:  # noqa: BLE001 — DB down: keep serving what we had
        return hit[1] if hit else None
    _rows_memo[lang] = (now + _ROWS_TTL_S, rows)
    return rows


def warm_rows(lang):
    """Refill the read cache off-request, so no visitor pays the query."""
    _rows(lang)


def _cached_row(source_key, lang):
    rows = _rows(lang)
    return None if rows is None else rows.get(source_key)


def cached_text(source_key, text, lang, pack_lang=None):
    """`text` in `lang` IF the cache already has it — otherwise `text`
    unchanged, with NO model call. This is the only translation entry point a
    page render is allowed to use.

    `localized_text()` translates on demand, which is right for one string
    behind one request; a page renders hundreds (every service name, every
    center description, every cluster title), and a cold cache would turn one
    page load into hundreds of sequential OpenRouter round trips. So the
    render reads the cache and the CACHE IS FILLED AHEAD OF TIME by
    scripts/translate_pack.py — a missing entry degrades to the pack's own
    language for that one string, exactly like a missing catalog key
    degrades to Spanish, and the next sweep fills it in.

    An admin's `override` row wins here too, and a stale `source_hash` (the
    pack text was edited after the sweep) counts as a miss: showing the
    current Spanish beats showing a translation of text nobody says any
    more."""
    text = text or ""
    pack_lang = pack_lang if pack_lang is not None else pack_language()
    if not text or _primary(lang) == _primary(pack_lang):
        return text
    row = _cached_row(source_key, lang)
    if not row:
        return text
    if row.get("override"):
        return row["content"] or text
    if row.get("source_hash") == _hash(text):
        return row["content"] or text
    return text


def needs_translation(source_key, text, lang, pack_lang=None):
    """True when `cached_text` would fall back — i.e. the sweep has work to
    do for this entry. Same checks, no content."""
    text = text or ""
    pack_lang = pack_lang if pack_lang is not None else pack_language()
    if not text or _primary(lang) == _primary(pack_lang):
        return False
    rows = _rows(lang)
    if rows is None:  # DB unreadable: no sweep work can be decided
        return False
    row = rows.get(source_key)
    if not row:
        return True
    if row.get("override"):
        return False
    return row.get("source_hash") != _hash(text)


# ============================================================== batch sweep

# How many strings go into one model call. Big enough that a 600-string
# catalog is ~15 calls instead of 600; small enough that one bad response
# costs little to retry one-by-one.
BATCH_SIZE = 40


def translate_batch(items, lang, stop=None):
    """[(source_key, text)] -> {source_key: translated}, filling the cache.

    One model call per `BATCH_SIZE` strings instead of one per string —
    what makes a full-catalog sweep finish in a minute rather than an hour.
    The model is asked for a numbered list back and the response must have
    exactly as many lines as the request had items; anything else falls back
    to translating that batch one string at a time through `translate()`,
    so a malformed batch degrades in cost, never in correctness (a
    mis-aligned batch would silently label a cardiology service as a blood
    test, which is the one failure mode worth paying to avoid). A provider
    that does not answer at all is a different case: that batch is skipped
    whole, never retried string by string.

    `stop` (`core.worker_stop.check()`, passed only by the queue worker):
    asked before every batch and every one-by-one string; once it is set the
    sweep returns what it already wrote, and the strings left stay stale for
    the next sweep (the job's re-run contract), so a SIGTERM never waits for
    the whole catalog."""
    from app.core import worker_stop
    out = {}
    pending = [(k, t) for k, t in items if (t or "").strip()]
    for i in range(0, len(pending), BATCH_SIZE):
        if worker_stop.halted(stop):
            break
        chunk = pending[i:i + BATCH_SIZE]
        try:
            translated = _machine_translate_batch([t for _k, t in chunk], lang)
        except TranslationUnavailable:
            # The provider did not answer: it is down, not this request.
            # Asking again string by string only multiplies the failure (40
            # calls per batch, each with its own timeout), so this batch is
            # left stale for the next sweep.
            continue
        if translated is None or len(translated) != len(chunk):
            for n, (key, text) in enumerate(chunk):  # one by one, aligned by construction
                if n and worker_stop.halted(stop):
                    break
                try:
                    out[key] = translate(key, text, lang)
                except TranslationUnavailable:
                    pass
            continue
        for (key, text), content in zip(chunk, translated):
            content = (content or "").strip()
            if not content:
                continue
            out[key] = content
            try:
                from app.core import db_catalog
                db_catalog.translation_upsert(
                    key, lang, _hash(text), content,
                    model=(ai_policy.model()))
            except Exception:  # noqa: BLE001 — cache write is best-effort
                pass
    if out:
        invalidate_rows(lang)
    return out


_BATCH_SEP = "\n"


def _machine_translate_batch(texts, lang):
    """The raw batched call: a numbered list in, the same numbered list out."""
    _call_count["n"] += 1
    if _fake():
        return [_fake_translate(t, lang) for t in texts]
    if not ai_client.available():
        raise TranslationUnavailable("falta configuración de IA")
    numbered = _BATCH_SEP.join(f"{n + 1}. {t}" for n, t in enumerate(texts))
    system = (
        "You translate short pieces of medical-appointment website content "
        "(service names, schedules, center descriptions, specialty names) "
        "for an Argentine public hospital's appointment system. Translate "
        "faithfully — this is patient-facing informational content, not "
        "marketing copy: never summarise, never invent, never drop a detail "
        "(a fasting requirement, a document to bring). Keep proper nouns, "
        "place names and people's names EXACTLY as they are. If an item "
        "contains bracketed tokens like [[1]], [[2]], copy them through "
        "exactly, unchanged, in the same relative position — they stand in "
        "for markup a caller restores afterwards. When an item reads like "
        "editorial prose for patients (e.g. a product update) rather than "
        "a short catalog field, translate it so it reads naturally to a "
        "native speaker.\n"
        f"Target language: {_language_name(lang)}.\n"
        f"The input is a numbered list of {len(texts)} items. Return exactly "
        f"{len(texts)} lines, each one 'N. <translation>', same numbers, same "
        "order, nothing else — no preamble, no blank lines, no commentary."
    )
    body = {
        "model": ai_policy.model(),
        "temperature": 0,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": numbered}],
    }
    try:
        data = ai_client.call(body, timeout=180)
    except (urllib.error.URLError, OSError, ValueError):
        raise TranslationUnavailable("la inferencia no estuvo disponible")
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise TranslationUnavailable("respuesta sin contenido")
    return _parse_numbered(content, len(texts))


def _parse_numbered(content, expected):
    """['first', 'second', …] from the model's 'N. text' lines, or None when
    the shape doesn't match what was asked for."""
    lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
    out = []
    for n, line in enumerate(lines):
        prefix = f"{n + 1}."
        if not line.startswith(prefix):
            return None
        out.append(line[len(prefix):].strip())
    return out if len(out) == expected else None


def set_override(source_key, lang, content, updated_by=None):
    """An admin's manual correction — wins over the machine translation
    regardless of source_hash, until `clear_override()` restores it."""
    from app.core import db_catalog
    db_catalog.translation_upsert(source_key, lang, _hash(content), content,
                          model=None, override=True, updated_by=updated_by)
    invalidate_rows(lang)


def clear_override(source_key, lang):
    from app.core import db_catalog
    db_catalog.translation_clear_override(source_key, lang)
    invalidate_rows(lang)


# ============================================================== the gate
#
# "requested lang != pack language" — the whole point of this module is
# to be a no-op (zero DB row, zero model call, return the original text
# untouched) when the visitor's language already matches what the active
# pack ships. `pack_language()` reads the highest-level pack's own
# `language` field (packs/*/pack.json), the same field data-packs.md
# documents as metadata for a future admin chain-picker — this is its
# first real consumer.

def pack_language():
    """Primary language subtag ("es", "en"...) of the active pack chain's
    highest-level pack, or "es" if there's no active chain (legacy
    single-dir mode) or the manifest doesn't say."""
    try:
        from app.core import data_packs
        chain = data_packs.active_chain()
        if chain:
            manifest = data_packs.manifest(chain[-1]) or {}
            lang = str(manifest.get("language") or "").strip()
            if lang:
                return lang.split("-", 1)[0].lower()
    except Exception:  # noqa: BLE001
        pass
    return "es"


_primary = i18n.primary_language


def localized_text(source_key, text, lang, pack_lang=None):
    """`text` translated into `lang`, UNLESS `lang` already matches the
    pack's own language (nothing to do) — never raises: a translation
    failure falls back to the original text, same degrade-gracefully
    criterion as the rest of this codebase's AI features."""
    pack_lang = pack_lang if pack_lang is not None else pack_language()
    if _primary(lang) == _primary(pack_lang):
        return text
    try:
        return translate(source_key, text, lang)
    except TranslationUnavailable:
        return text


# ============================================================== proof surface
#
# ONE data surface, per the phase 3a task: preparation summaries
# (catalog.preparations(), `estudios-preparacion.json`'s `resumen` field —
# "how do I prepare for this study", the single sentence patients actually
# read on /upload-order and the preparation pages). Not wired into any live
# page yet — this is the proof the cache/gate work end to end against REAL
# pack content, the same "prove it, don't sweep it" scope the i18n UI-string
# proof slice used for about.html.

def localized_preparation_summary(item, lang, pack_lang=None):
    """`item` is one entry from `catalog.preparations()` (has `id` and
    `resumen`). Returns the summary in `lang`, translating+caching only
    when `lang` differs from the pack's own language."""
    item_id = item.get("id") or ""
    summary = item.get("resumen") or ""
    return localized_text(f"modules.turnos.preparation.{item_id}.resumen", summary, lang,
                          pack_lang=pack_lang)
