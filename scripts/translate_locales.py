#!/usr/bin/env python3
"""translate_locales.py — generates the UI catalogs for every language that
isn't hand-kept (`locales/<lang>/*.json`), from the two that are.

`locales/es/` is canonical and `locales/en/` is its hand-kept counterpart.
Every other language in `i18n.LANGUAGE_NAMES` is a machine translation of
those two, produced here with the same OpenRouter model `translate.py` uses
for pack content — one call per batch of keys, JSON in, JSON out, so a
string with a newline or a leading number never breaks the parse the way
`translate._machine_translate_batch`'s numbered-list shape would.

Both sources go into the prompt: the Spanish for the meaning and register
(it is the text a patient actually reads), the English as a pivot for
languages the model handles better from English. Placeholders (`{name}`,
`{{sitio.x}}`, `[[1]]`), HTML tags and `%s`-style markers are checked after
every batch and a mismatch retries that batch once, then leaves the key out
(a missing key falls back to Spanish at render time — visibly wrong, never
blank — and the next run picks it up again).

Incremental: an existing target key whose Spanish source hasn't changed
since it was generated is kept (a `_source_hash` sidecar file per language
remembers what each key was translated from), so re-running after adding
20 keys costs 20 strings, not 3,300.

Usage:
    python3 scripts/translate_locales.py                    # every language
    python3 scripts/translate_locales.py --lang it --lang fr
    python3 scripts/translate_locales.py --dry-run          # count only
    python3 scripts/translate_locales.py --json in.json --lang it --out out.json
        # generic mode: translate one flat {key: text} JSON (humanrounds.org's
        # copy objects use it), Spanish/English pivot resolved from the same
        # file when keys end in `.es`/`.en`, else the text is treated as
        # English.

Needs a configured AI key (the same one the app uses; `TURNOS_FAKE_AI=1`
produces `[xx] text` markers instead of calling out).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from app.core import ai_policy, config  # noqa: E402,F401 — loads .env
from app.core import i18n  # noqa: E402
from app.core import translate  # noqa: E402
from app.ai import ai_client  # noqa: E402

BATCH = 40
_HTML_TAGS = frozenset("""
a abbr b bdi bdo blockquote br button caption cite code col colgroup data dd del details dfn div dl dt
em fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr i img input ins kbd label legend li
main mark nav ol optgroup option output p picture pre q s samp section select small source span strong
sub summary sup svg table tbody td template textarea tfoot th thead time tr u ul var video wbr
""".split())
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}|\{[a-zA-Z0-9_]+\}|\[\[\d+\]\]|%[sd]|</?[a-zA-Z][^>]*>")
_LOCK = threading.Lock()
# At most this many model calls in flight: OpenRouter holds the worst-case
# cost of every open request against the balance, and 16 parallel batches
# turned a healthy key into 402s (2026-09-11).
_INFLIGHT = threading.BoundedSemaphore(4)
_API_KEY = {"value": None}  # --key-command override, read once at startup


def _key_from_command(command):
    """The OpenRouter key printed on stdout by an operator-supplied command,
    never echoed. For a sweep that must not draw on the app's own capped key:
    the command is whatever this installation's secret store offers."""
    import shlex
    import subprocess
    raw = subprocess.run(shlex.split(command), capture_output=True, text=True, check=True).stdout
    key = (raw or "").strip()
    if not key:
        raise SystemExit("--key-command printed no key")
    return key


def _log(msg):
    with _LOCK:
        print(msg, flush=True)


def _hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _placeholders(text):
    """The tokens a translation must carry through, in canonical form: an
    HTML tag counts by name only (`<a`, `</a`) — its alt/aria-label/title
    attributes are prose the model is expected to translate."""
    out = []
    for tok in _PLACEHOLDER_RE.findall(text or ""):
        if tok.startswith("<"):
            m = re.match(r"</?([a-zA-Z0-9]+)", tok)
            # Angle brackets around a word that is no HTML element ("nombre
            # <email>") are prose: the model rightly translates them
            # ("Name <E-Mail>"), and counting them as a tag left the string
            # in Spanish forever.
            if not m or m.group(1).lower() not in _HTML_TAGS:
                continue
            tok = m.group(0).lower()
        out.append(tok)
    return sorted(out)


def _system_prompt(lang, n):
    name = translate._language_name(lang)
    return (
        "You translate the user-interface strings of a public hospital's "
        "appointment website (patients book appointments, staff manage a "
        "queue). Each item gives you the canonical Spanish text (Argentina; "
        "informal 'vos' register, warm and plain) and, when available, its "
        "English counterpart. Produce the " + name + " version.\n"
        "Rules:\n"
        "- Faithful and complete: never summarise, never add, never drop a "
        "detail. Short strings stay short (button labels, column headers).\n"
        "- Register: plain, friendly, addressed to one person; the natural "
        "informal/neutral form of the target language that a public service "
        "would use with the general public (elderly readers included).\n"
        "- Keep EXACTLY, unchanged and in a natural position: placeholders "
        "like {name}, {n}, {{sitio.nombre}}, [[1]], %s; HTML tags and their "
        "attributes; URLs; proper nouns, people's and place names; product "
        "names ('Human Rounds', 'HSI', 'WhatsApp', 'DNI', 'PUCO').\n"
        "- A placeholder ending in _lower ({day_lower}) is the same word as "
        "the one without it ({day}), written in lower case for the middle of "
        "a Spanish sentence: where the target language capitalizes that word "
        "mid-sentence (German nouns and weekdays, English weekdays), use the "
        "plain one ({day}) instead.\n"
        "- Medical and administrative terms: the usual term in the target "
        "language's public health system.\n"
        "- Empty text stays empty.\n"
        "- An item with a \"plural_form\" field: the es/en texts are the "
        "plural phrase; write the phrase exactly as it reads with the numbers "
        "that field names (the grammatical number and case of the noun and "
        "its adjectives), keeping {n} where the number goes.\n"
        f"Input: a JSON object of {n} items, key -> {{\"es\": ..., \"en\": ...}}. "
        "Output: ONLY a JSON object with the same keys, each mapped to the "
        "translated string. No markdown fences, no commentary."
    )


def _call(lang, items):
    """items: {key: {"es": str, "en": str|None}} -> {key: str} or None."""
    if translate._fake():
        return {k: f"[{lang}] {v['es']}" for k, v in items.items()}
    if not ai_client.available():
        raise SystemExit("no AI key configured — cannot translate")
    body = {
        "model": ai_policy.model(),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _system_prompt(lang, len(items))},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ],
    }
    with _INFLIGHT:
        data = ai_client.call(body, timeout=240, api_key=_API_KEY["value"])
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        _log(f"  [{lang}] response without content: {json.dumps(data)[:300]}")
        return None
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", content).strip()
    try:
        out = json.loads(content)
    except ValueError:
        finish = None
        try:
            finish = data["choices"][0].get("finish_reason") or data["choices"][0].get("native_finish_reason")
        except Exception:  # noqa: BLE001
            pass
        _log(f"  [{lang}] unparseable response (finish={finish}, {len(content)} chars): {content[:120]!r} … {content[-80:]!r}")
        return None
    return out if isinstance(out, dict) else None


def _validate(items, out):
    """Keep only the keys whose translation preserved every placeholder."""
    good, bad = {}, []
    for key, src in items.items():
        val = out.get(key) if out else None
        # The model sometimes echoes the whole item and nests the translation
        # under the target code ({"es": …, "en": …, "it": …}) — take it.
        if isinstance(val, dict):
            val = next((v for k, v in val.items() if k not in ("es", "en", "plural_form") and isinstance(v, str)), None)
        if not isinstance(val, str):
            bad.append(key)
            continue
        if not src["es"].strip():
            good[key] = ""
            continue
        if not val.strip():
            # A grammatical particle with no counterpart (`date.article` "el"
            # in Japanese) legitimately translates to nothing.
            if len(src["es"].strip()) <= 3:
                good[key] = ""
                continue
            bad.append(key)
            continue
        if _placeholders(src["es"]) != _placeholders(val) and _placeholders(src.get("en") or "") != _placeholders(val):
            bad.append(key)
            continue
        good[key] = val
    return good, bad


# The opening «¿» and «¡» are Spanish only. The model sometimes keeps them
# from the source (two Russian questions of Solicitudes, «¿Revenir au logo…»
# in French, 2026-11-06); no generated language writes them, so every value is
# cleaned on the way into the catalog, the ones kept from earlier runs too.
_SPANISH_MARKS = re.compile(r"[¿¡]")


def _no_spanish_marks(lang, text):
    """`text` without «¿»/«¡» for any language but Spanish."""
    if not isinstance(text, str) or lang.split("-")[0].lower() == "es":
        return text
    return _SPANISH_MARKS.sub("", text)


def translate_items(lang, items, label="", batch=BATCH, on_progress=None):
    """{key: {"es", "en"}} -> {key: translation}; a failed batch is retried
    up to three times, what still fails is left out. `on_progress(result)`
    runs after every batch so a caller can persist partial work — a killed
    run keeps what it paid for."""
    result = {}
    keys = list(items)
    batches = [keys[i:i + batch] for i in range(0, len(keys), batch)]
    for n, batch_keys in enumerate(batches, 1):
        chunk = {k: items[k] for k in batch_keys}
        good, bad = {}, list(chunk)
        for attempt in range(3):
            todo = {k: chunk[k] for k in bad}
            if not todo:
                break
            try:
                out = _call(lang, todo)
            except Exception as exc:  # noqa: BLE001 — retry, then give up on the batch
                _log(f"  [{lang}] {label} batch {n}/{len(batches)} attempt {attempt + 1} failed: {exc}")
                time.sleep(2 + 3 * attempt)
                continue
            g, bad = _validate(todo, out)
            good.update({k: _no_spanish_marks(lang, v) for k, v in g.items()})
        result.update(good)
        if bad:
            _log(f"  [{lang}] {label} batch {n}: {len(bad)} keys left untranslated: {bad[:5]}")
        if on_progress and good:
            on_progress(result)
    return result


# ------------------------------------------------------------ repo catalogs

def _load_dir(lang):
    """{namespace: {key: text}} straight from the files (comments dropped)."""
    out = {}
    d = os.path.join(i18n.LOCALES_DIR, lang)
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            data = json.load(fh)
        out[f[:-5]] = {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str)}
    return out


def _comments(lang):
    out = {}
    d = os.path.join(i18n.LOCALES_DIR, lang)
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                out[f[:-5]] = json.load(fh).get("_comment")
    return out


def _hash_path(lang):
    return os.path.join(i18n.LOCALES_DIR, lang, "_source_hash.json")


# Languages whose numbers take more than two forms (CLDR, Intl.PluralRules):
# the plural key `k` of every «N + noun» pair (`k` + `k_one` in Spanish) gets
# its `k_few`/`k_many` here, read by i18n.mjs tn() and core/i18n.py tn_or().
# Without them Russian printed the 5–20 word after 2.
PLURAL_FORMS = {"ru": ("few", "many"), "uk": ("few", "many")}
_FORM_HINT = {
    "few": "the form this phrase takes with the numbers 2, 3, 4, 22, 23, 24 (CLDR plural category 'few')",
    "many": "the form this phrase takes with the numbers 0, 5-20, 25-30, 11, 12, 13, 14 (CLDR plural category 'many')",
}


_PLACEHOLDER = re.compile(r"\{[a-zA-Z0-9_]+\}")


def _counted(plural, singular):
    """Does a `k`/`k_one` pair change with the number? Yes when the plural
    carries a placeholder, or when both are a bare unit word (at most two
    words, no placeholder) printed next to a number by the caller."""
    if _PLACEHOLDER.search(plural):
        return True
    return not _PLACEHOLDER.search(singular) and len(plural.split()) <= 2


def _sources(lang, es, en):
    """{namespace: {key: {"es", "en"[, "plural_form"]}}}: every Spanish key,
    and for a language in PLURAL_FORMS, right after each pair's `k_one`, the
    derived `k_few`/`k_many` translated from the plural `k`."""
    forms = PLURAL_FORMS.get(lang, ())
    out = {}
    for ns, keys in es.items():
        items = {}
        for key, text in keys.items():
            items[key] = {"es": text, "en": en.get(ns, {}).get(key)}
            base = key[:-4] if key.endswith("_one") else None
            # Only a phrase with its number in it, or a bare unit word the
            # caller prints after the number («médicos», «turnos»), and never
            # over a key the Spanish catalog already names that way.
            if base and base in keys and _counted(keys[base], text):
                for form in forms:
                    if base + "_" + form in keys:
                        continue
                    items[base + "_" + form] = {"es": keys[base], "en": en.get(ns, {}).get(base),
                                                "plural_form": _FORM_HINT[form]}
        out[ns] = items
    return out


def _source_hash(src):
    """What a key was translated from: the Spanish, plus the plural form a
    derived `_few`/`_many` key asks for."""
    return _hash(src["es"] + ("\x00" + src["plural_form"] if src.get("plural_form") else ""))


def run_lang(lang, dry_run=False, limit=0, batch=BATCH):
    es = _load_dir("es")
    en = _load_dir("en")
    comments = _comments("es")
    existing = _load_dir(lang)
    try:
        with open(_hash_path(lang), encoding="utf-8") as fh:
            hashes = json.load(fh)
    except (OSError, ValueError):
        hashes = {}

    sources = _sources(lang, es, en)
    pending = {}
    kept = {}
    for ns, items in sources.items():
        for key, src in items.items():
            have = existing.get(ns, {}).get(key)
            if have is not None and hashes.get(key) == _source_hash(src):
                kept.setdefault(ns, {})[key] = _no_spanish_marks(lang, have)
                continue
            pending[key] = src
    n_keep = sum(len(v) for v in kept.values())
    _log(f"[{lang}] {n_keep} keys up to date, {len(pending)} to translate")
    if dry_run:
        return
    if limit:
        pending = dict(list(pending.items())[:limit])

    def _write(got):
        merged = {ns: dict(keys) for ns, keys in kept.items()}
        for ns, items in sources.items():
            for key, src in items.items():
                if key in got:
                    merged.setdefault(ns, {})[key] = got[key]
                    hashes[key] = _source_hash(src)
        # Keys gone from the canonical catalog leave the target too.
        for key in list(hashes):
            if not any(key in items for items in sources.values()):
                del hashes[key]
        d = os.path.join(i18n.LOCALES_DIR, lang)
        os.makedirs(d, exist_ok=True)
        for ns in es:
            data = {}
            if comments.get(ns):
                data["_comment"] = comments[ns]
            for key in sources[ns]:
                if key in merged.get(ns, {}):
                    data[key] = merged[ns][key]
            with open(os.path.join(d, ns + ".json"), "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        with open(_hash_path(lang), "w", encoding="utf-8") as fh:
            json.dump(dict(sorted(hashes.items())), fh, ensure_ascii=False, indent=0)
            fh.write("\n")
        return sum(len(v) for v in merged.values())

    got = translate_items(lang, pending, label="ui", batch=batch, on_progress=_write)
    total = _write(got)
    _log(f"[{lang}] done: {total}/{sum(len(v) for v in sources.values())} keys written")


# ------------------------------------------------------------ generic JSON

def run_json(path, lang, out_path, batch=BATCH):
    """`path` is a flat JSON: either {key: {"es": ..., "en": ...}} or
    {key: text} (treated as English, Spanish unknown)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = {}
    for key, val in data.items():
        if isinstance(val, dict):
            es = val.get("es") or val.get("en") or ""
            items[key] = {"es": es, "en": val.get("en")}
        else:
            items[key] = {"es": str(val), "en": str(val)}
    have = {}
    try:
        with open(out_path, encoding="utf-8") as fh:
            have = json.load(fh)
    except (OSError, ValueError):
        pass
    have = {k: v for k, v in have.items() if k in items}
    todo = {k: v for k, v in items.items() if k not in have}

    def _write(got):
        merged = dict(have)
        merged.update(got)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return len(merged)

    got = translate_items(lang, todo, label=os.path.basename(path), batch=batch, on_progress=_write)
    _log(f"[{lang}] {_write(got)}/{len(items)} -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", action="append", help="target language (repeatable); default: every generated language")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="smoke test: translate at most N pending keys")
    ap.add_argument("--json", help="generic mode: a flat JSON file to translate")
    ap.add_argument("--out", help="generic mode: output path")
    ap.add_argument("--batch", type=int, default=BATCH, help="keys per model call (smaller for long HTML bodies)")
    ap.add_argument("--key-command", help="command printing an OpenRouter key on stdout; it pays for this sweep instead of the app's own capped key")
    args = ap.parse_args()
    if args.key_command:
        _API_KEY["value"] = _key_from_command(args.key_command)

    if args.json:
        if not args.lang or not args.out:
            ap.error("--json needs --lang and --out")
        threads = []
        for lang in args.lang:
            out = args.out if len(args.lang) == 1 else args.out.replace(".json", f".{lang}.json")
            threads.append(threading.Thread(target=run_json, args=(args.json, lang, out, args.batch), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return

    langs = args.lang or [c for c in i18n.LANGUAGE_NAMES if c not in ("es", "en")]
    threads = [threading.Thread(target=run_lang, args=(lang, args.dry_run, args.limit, args.batch), daemon=True) for lang in langs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
