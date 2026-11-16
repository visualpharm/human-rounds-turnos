"""test_services_memo.py — the _services() memoization boundary.

site_catalog._services() is called hundreds of times per page pair and used
to redo the whole overlay merge + normalization + localization on every
call. These tests pin the memo's contract:

  - the performance win: N calls run the pipeline once (and an unmemoizable
    source — a fresh doc per call, like the pre-memo behavior — runs it N
    times, the A/B control);
  - mutation isolation: every hand-out is fresh shallow row dicts (a
    top-level field write dies with the caller's own call, exactly the
    legacy semantics) and nested lists/dicts are shared READ-ONLY frozen
    storage — a nested mutation RAISES instead of silently poisoning the
    memo or the source/overlay caches. (A per-hit recursive deep copy was
    the first design; it made the memo slower than legacy on the real
    120-row pack, so the boundary is freeze-at-publish/thaw-at-hit.);
  - invalidation: a pack/source change, an admin overlay write
    (catalog.invalidate()), a DB failure -> recovery, and a translation
    refresh (pack_l10n.invalidate_memo()) all show up on the next call;
  - coherency under churn (the 2026-08-16 review): a source replacement, a
    catalog invalidation or an l10n refresh landing INSIDE the pipeline
    never publishes a result under state it did not read, and no memo entry
    is ever owned by an informationless state ('none'/'expired'/'absent')
    that a later hit would never refresh;
  - localization behavior is preserved: canonical language returns pack
    values untouched, a non-canonical language localizes and follows the
    translation cache's refreshes;
  - the performance CONTRACT is a real-pack benchmark: the memo's median
    must beat the exact pre-change pipeline's median over the REAL shipped
    catalog (>= 100 services) — the tiny synthetic count-proofs above cannot
    catch a per-call cost regression — in the pack's OWN language AND
    localized into a noncanonical one (the first cut benchmarked only the
    canonical path, which localize() turns into a no-op: the 2026-08-16
    review caught it proving nothing about the localized pipeline the memo
    replaces, so the noncanonical race asserts its output row by row).

Hermetic: no DB, no network, no pack files (except the benchmark, which
reads the repo's own shipped pack through the real loader — a file read,
never a service). `db_catalog.catalog_list` is replaced with a fake, `site_base._load`
serves a synthetic doc, `translate.cached_text` is swapped where a
translation is needed.
"""
import contextlib
import copy
import json
import statistics
import time
import unittest
from unittest import mock

from app.core import db
from app.core import db_catalog
from app.core import i18n
from app.core import data_packs
from app.core import pack_l10n
from app.core import translate
from app.patient_web import catalog
from app.patient_web import site_base, site_catalog


def _base_doc():
    """Two services: one that trips the 'clínica médica' normalization (its
    `palabras` gets rewritten into a fresh nested list — the isolation test's
    main target) and one plain, with a nested dict field."""
    return {"servicios": [
        {"id": "clinica", "nombre": "Clínica Médica",
         "palabras": ["médico clínico"], "cartel": "Atención por orden de llegada"},
        {"id": "lab", "nombre": "Laboratorio",
         "palabras": ["análisis", "extracción"],
         "info": {"nota": "Sin ayuno"}},
    ]}


def _overlay_row(nombre):
    """A catalogo-shaped overlay row overriding the `lab` service."""
    return {"id": "lab", "borrado": False,
            "doc": {"id": "lab", "nombre": nombre, "palabras": ["análisis"]},
            "created": None, "updated": None}


def _fake_catalog_list(state):
    """A stand-in for `db_catalog.catalog_list` reading `state` ({"rows": [...], "fail": bool}, live, mutable)."""
    def catalog_list(tipo):
        if state["fail"]:
            raise db.DbUnavailable("down")
        # A fresh list per call, like a real DB read: a new read must be a
        # new object for catalog.overlay_state()'s identity comparison.
        return [dict(r) for r in state["rows"]]
    return catalog_list


@contextlib.contextmanager
def _counting(obj, name):
    """Swap obj.name for a pass-through that counts calls — the perf proof
    counts pipeline executions instead of wall time (deterministic)."""
    real = getattr(obj, name)
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(name)
        return real(*args, **kwargs)

    setattr(obj, name, wrapper)
    try:
        yield calls
    finally:
        setattr(obj, name, real)


class _MemoTest(unittest.TestCase):
    def setUp(self):
        site_catalog._SERVICES_MEMO.clear()
        catalog.invalidate()
        pack_l10n.invalidate_memo()
        self._load_bak = site_base._load
        self._lang_token = None
        # The overlay cache expires after catalog._TTL (3 s) of WALL time. A loaded CI runner can take
        # longer than that between two calls of a counting test, and the expiry looks like a memo miss
        # (3 != 1). These tests count pipeline runs, not seconds: the overlay lives for the whole test,
        # and the ones that need it expired age the entry themselves (catalog._TTL + 1).
        ttl = mock.patch.object(catalog, "_TTL", 3600.0)
        ttl.start()
        self.addCleanup(ttl.stop)
        self.doc = _base_doc()
        self.db_state = {"rows": [], "fail": False}
        fake_list = mock.patch.object(db_catalog, "catalog_list", new=_fake_catalog_list(self.db_state))
        fake_list.start()
        self.addCleanup(fake_list.stop)
        site_base._load = self._fake_load

    def _fake_load(self, name):
        if name == "servicios.json":
            return self.doc
        return self._load_bak(name)

    def _bind(self, lang):
        self._lang_token = i18n.bind_lang(lang)[1]

    def _canonical_lang(self):
        lang = translate.pack_language()
        self.assertNotEqual(translate._primary(lang),
                            translate._primary("en"),
                            "this suite needs an 'en'-distinct pack language")
        return lang

    def _by_id(self, rows):
        return {r["id"]: r for r in rows}

    def tearDown(self):
        if self._lang_token is not None:
            i18n.unbind(self._lang_token)
        site_base._load = self._load_bak
        catalog.invalidate()
        pack_l10n.invalidate_memo()
        site_catalog._SERVICES_MEMO.clear()


class TestPerformanceWin(_MemoTest):
    def test_hundreds_of_calls_run_the_pipeline_once(self):
        self._bind(self._canonical_lang())
        site_catalog._services()  # cold warm BEFORE the counters: absorbs the
        # cold-start pre/post retry (first ever call: overlay 'none' -> 'live')
        with _counting(site_base, "_with_overlay") as merges, \
                _counting(pack_l10n, "localize") as locs:
            site_catalog._services()  # first call under the counting seams
            self.assertEqual(len(merges), 1)
            for _ in range(300):
                site_catalog._services()
            self.assertEqual(len(merges), 1, "overlay merge re-ran on a memo hit")
            self.assertEqual(len(locs), 1, "localize re-ran on a memo hit")

    def test_unmemoizable_source_runs_it_every_time(self):
        """The A/B control: when the source doc is a fresh object per call
        (what every call effectively was before the memo), the pipeline runs
        on every call — twice per call, in fact: the pre/post stability check
        can never match, so each call burns its one retry too. Proves the
        memo, not chance, skips the work."""
        self._bind(self._canonical_lang())
        site_base._load = lambda name: (copy.deepcopy(_base_doc())
                                           if name == "servicios.json"
                                           else self._load_bak(name))
        with _counting(site_base, "_with_overlay") as merges:
            for _ in range(50):
                site_catalog._services()
            self.assertEqual(len(merges), 100)


class TestRealPackBenchmark(_MemoTest):
    """The 2026-08-16 performance review, as a contract: a per-hit recursive
    deep copy made the memo SLOWER than the exact legacy pipeline on the
    real 120-row pack (median 0.847 s vs 0.210 s per 2,000 calls), and the
    synthetic count-proof in TestPerformanceWin could not see it — counting
    pipeline executions says nothing about per-hit cost. So both tests run
    BOTH implementations against the REAL shipped pack (through the real
    loader — a file read, not a service) and fail unless the memo's median
    round beats the legacy pipeline's.

    Two languages, because the review's follow-up found the first cut
    benchmarked ONLY the canonical one — and localize() is a NO-OP in the
    pack's own language, so that benchmark never exercised the localization
    pass the memo also has to survive:

      - canonical (pack language): the merge + normalization cost;
      - noncanonical ('en'): merge + normalization + the localize pass,
        against a deterministic WARM translation cache (a fake cached_text
        that answers for EVERY entry — the state a completed
        scripts/translate_pack.py sweep leaves behind), with the localized
        output ASSERTED row by row so a silent fallback to canonical fails
        loudly instead of benchmarking the wrong path."""

    ROUNDS = 6
    CALLS = 400

    @classmethod
    def setUpClass(cls):
        if ("ar-b-pinamar" not in data_packs._required_pack_ids()
                or not data_packs.private_packs_dir()):
            raise unittest.SkipTest(
                "external private Pinamar pack is not configured")
        data_packs.verify_required_packs()

    def _legacy_services(self):
        """A verbatim replica of the base-commit (232e33b4) _services(True):
        load, overlay merge, normalize, localize — rebuilt on every call,
        which is exactly the behavior the memo exists to retire."""
        d = site_base._load("servicios.json") or {}
        out = site_base._with_overlay("servicios", d.get("servicios", []))
        normalizados = []
        for s in out:
            if str(s.get("nombre") or "").strip().casefold() in ("clínica médica", "clinica medica"):
                s = dict(s, nombre="Medicina General",
                         palabras=list(s.get("palabras") or []) + ["clínica médica", "médico clínico"])
            normalizados.append(s)
        return pack_l10n.localize("servicios", normalizados)

    def test_real_pack_memo_median_beats_the_legacy_pipeline(self):
        """Canonical half: merge + normalization only — localize() returns
        the rows untouched in the pack's own language, so the localization
        cost is covered by the sibling test below, not this one."""
        site_base._load = self._load_bak  # the REAL shipped pack
        lang = self._canonical_lang()
        self._bind(lang)
        rows = site_catalog._services()
        self.assertGreaterEqual(
            len(rows), 100,
            f"the shipped pack has only {len(rows)} services — this benchmark "
            "needs the real catalog to stay representative; if the pack "
            "shrank for real, re-tune the thresholds, don't delete the test")
        site_catalog._services()  # warm: absorb the cold-start retry
        memo_t, legacy_t = [], []
        for _ in range(self.ROUNDS):
            # interleaved rounds: whatever the machine is doing to one
            # implementation this instant, it does to the other next
            t0 = time.perf_counter()
            for _ in range(self.CALLS):
                site_catalog._services()
            memo_t.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            for _ in range(self.CALLS):
                self._legacy_services()
            legacy_t.append(time.perf_counter() - t0)
        memo_med = statistics.median(memo_t)
        legacy_med = statistics.median(legacy_t)
        self.assertLess(
            memo_med, legacy_med,
            f"memo median {memo_med/self.CALLS*1e6:.0f} us/call NOT faster "
            f"than legacy {legacy_med/self.CALLS*1e6:.0f} us/call on the "
            f"real {len(rows)}-row pack ({self.ROUNDS} interleaved rounds of "
            f"{self.CALLS} calls)")

    def test_real_pack_localized_memo_median_beats_the_legacy_pipeline(self):
        """Noncanonical half (2026-08-16 review follow-up): the same real-pack
        A/B in 'en', through the localize pass the canonical benchmark
        bypasses. The translation cache is WARM and deterministic — a fake
        cached_text that answers for every entry, the state a completed
        scripts/translate_pack.py sweep leaves behind — and before any timing
        the localized output is ASSERTED row by row and checked for equality
        against the exact legacy pipeline's, so a benchmark that silently
        fell back to canonical (the review's finding) fails here, loudly."""
        site_base._load = self._load_bak  # the REAL shipped pack
        self.assertNotEqual(translate._primary(translate.pack_language()), "en",
                            "the pack's language must stay 'en'-distinct, or "
                            "this benchmark silently runs canonical again")
        self._bind("en")
        mark = "[en] "

        def fake_cached_text(key, text, lang, pack_lang=None):
            return mark + text  # deterministic, warm for EVERY entry

        with mock.patch.object(pack_l10n.translate, "cached_text",
                               fake_cached_text):
            rows = site_catalog._services()
            self.assertGreaterEqual(
                len(rows), 100,
                f"the shipped pack has only {len(rows)} services — this benchmark "
                "needs the real catalog to stay representative; if the pack "
                "shrank for real, re-tune the thresholds, don't delete the test")
            # ASSERTED LOCALIZED OUTPUT: every service name and every search
            # synonym must carry the translated marker — canonical values
            # here mean the race below measured the wrong path
            for row in rows:
                self.assertTrue(
                    str(row.get("nombre") or "").startswith(mark),
                    f"service {row.get('id')!r} nombre came back un-localized "
                    "— the benchmark is running the canonical path")
                for palabra in (row.get("palabras") or []):
                    if isinstance(palabra, str) and palabra.strip():
                        self.assertTrue(
                            palabra.startswith(mark),
                            f"service {row.get('id')!r} palabras came back "
                            "un-localized")
            # …and the memo's localized rows are exactly what the legacy
            # pipeline computes from the same warm cache — no behavioral
            # divergence hiding behind the performance win
            self.assertEqual(rows, self._legacy_services(),
                             "memo output diverged from the exact legacy "
                             "pipeline under localization")
            site_catalog._services()  # warm: absorb the cold-start retry
            memo_t, legacy_t = [], []
            for _ in range(self.ROUNDS):
                # interleaved rounds: whatever the machine is doing to one
                # implementation this instant, it does to the other next
                t0 = time.perf_counter()
                for _ in range(self.CALLS):
                    site_catalog._services()
                memo_t.append(time.perf_counter() - t0)
                t0 = time.perf_counter()
                for _ in range(self.CALLS):
                    self._legacy_services()
                legacy_t.append(time.perf_counter() - t0)
            memo_med = statistics.median(memo_t)
            legacy_med = statistics.median(legacy_t)
            self.assertLess(
                memo_med, legacy_med,
                f"memo median {memo_med/self.CALLS*1e6:.0f} us/call NOT faster "
                f"than legacy {legacy_med/self.CALLS*1e6:.0f} us/call on the "
                f"real {len(rows)}-row pack localized to 'en' "
                f"({self.ROUNDS} interleaved rounds of {self.CALLS} calls)")


class TestMutationIsolation(_MemoTest):
    def test_mutations_never_reach_the_memo(self):
        self._bind(self._canonical_lang())
        first = site_catalog._services()
        clin = self._by_id(first)["clinica"]
        lab = self._by_id(first)["lab"]
        self.assertEqual(clin["nombre"], "Medicina General")  # normalization ran
        # top-level field writes are call-private (fresh shallow row dict per
        # hand-out — the same semantics as the fresh dicts legacy built)
        clin["nombre"] = "HACKED"
        lab["nuevo"] = True
        # nested mutations RAISE: the shared storage is read-only frozen
        with self.assertRaises(TypeError):
            clin["palabras"].append("hacked")  # the normalized nested list
        with self.assertRaises(TypeError):
            clin["palabras"][0] = "hacked"
        with self.assertRaises(TypeError):
            lab["palabras"] += ["hacked"]
        with self.assertRaises(TypeError):
            lab["palabras"].extend(["hacked"])
        with self.assertRaises(TypeError):
            lab["info"]["nota"] = "HACKED"  # nested plain dict field
        with self.assertRaises(TypeError):
            lab["info"].pop("nota")
        # `|=` is a C-level dict slot inherited from dict, NOT routed through
        # update()/__setitem__ — without _FrozDict.__ior__ = _readonly it
        # merged IN PLACE into the shared memo storage (review 2026-08-18)
        with self.assertRaises(TypeError):
            lab["info"] |= {"poisoned": True}
        # the documented copy idiom still works and yields ordinary mutables
        editable = list(clin["palabras"])
        editable.append("fine")
        self.assertNotIn("fine", clin["palabras"])
        second = self._by_id(site_catalog._services())
        self.assertEqual(second["clinica"]["nombre"], "Medicina General")
        self.assertNotIn("hacked", second["clinica"]["palabras"])
        self.assertNotIn("hacked", second["lab"]["palabras"])
        self.assertEqual(second["lab"]["info"]["nota"], "Sin ayuno")
        self.assertNotIn("poisoned", second["lab"]["info"],
                         "the |= attempt poisoned the shared memo storage")
        self.assertNotIn("nuevo", second["lab"])
        # frozen containers serialize exactly like their plain counterparts
        payload = json.dumps(second, ensure_ascii=False, sort_keys=True)
        self.assertIn("Medicina General", payload)
        self.assertIn("Sin ayuno", payload)

    def test_localize_false_variant_is_isolated_too(self):
        self._bind(self._canonical_lang())
        plain = site_catalog._services(localize=False)
        plain[0]["nombre"] = "HACKED"  # top-level write: call-private
        with self.assertRaises(TypeError):
            plain[0]["palabras"].append("hacked")
        again = site_catalog._services(localize=False)
        self.assertNotIn("hacked", again[0]["palabras"])
        self.assertEqual(again[0]["nombre"], "Medicina General")


class TestInvalidation(_MemoTest):
    def test_source_change_invalidates(self):
        self._bind(self._canonical_lang())
        with _counting(site_base, "_with_overlay") as merges:
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Laboratorio")
            self.assertEqual(len(merges), 2)  # cold start: 'none'->'live' retry, then publish
            self.doc = _base_doc()  # a re-parsed pack file is a NEW object
            self.doc["servicios"][1]["nombre"] = "Laboratorio Central"
            rows = site_catalog._services()
            self.assertEqual(self._by_id(rows)["lab"]["nombre"], "Laboratorio Central")
            self.assertEqual(len(merges), 3)  # one stable pipeline, published

    def test_admin_overlay_change_invalidates(self):
        self._bind(self._canonical_lang())
        self.db_state["rows"] = [_overlay_row("Lab Editado")]
        with _counting(site_base, "_with_overlay") as merges:
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Lab Editado")
            self.assertEqual(len(merges), 2)  # cold start retry pair
            # the admin saves another edit: fresh DB read + invalidate()
            self.db_state["rows"] = [_overlay_row("Lab Re-Editado")]
            catalog.invalidate()
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Lab Re-Editado")
            self.assertEqual(len(merges), 4)  # 'none'->'live' retry pair again

    def test_invalidate_alone_refreshes_even_with_same_rows(self):
        self._bind(self._canonical_lang())
        self.db_state["rows"] = [_overlay_row("Lab Editado")]
        with _counting(site_base, "_with_overlay") as merges:
            site_catalog._services()
            self.assertEqual(len(merges), 2)
            catalog.invalidate()  # no new data, just the cache drop
            site_catalog._services()
            self.assertEqual(len(merges), 4, "invalidate() left a stale memo hit")

    def test_db_failure_falls_back_and_recovery_never_serves_stale(self):
        self._bind(self._canonical_lang())
        self.db_state["rows"] = [_overlay_row("Lab Editado")]
        self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                         "Lab Editado")
        # the DB goes down: public read degrades to base-only (documented
        # catalog behavior) and that fallback is itself served coherently
        self.db_state["fail"] = True
        catalog.invalidate()
        with _counting(site_base, "_with_overlay") as merges:
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Laboratorio")
            site_catalog._services()
            # 2 = 'none'->'db-fail' retry pair; the second call HIT the
            # published ('db-fail', ts) entry instead of re-running per call
            self.assertEqual(len(merges), 2, "failure fallback re-ran per call")
            # recovery: cooldown elapsed (no sleep — clear the timestamp the
            # same way waiting 3 s would) and the DB answers with new rows
            self.db_state["fail"] = False
            self.db_state["rows"] = [_overlay_row("Lab Recuperado")]
            catalog._DB_FAIL_UNTIL.clear()
            rows = site_catalog._services()
            self.assertEqual(self._by_id(rows)["lab"]["nombre"], "Lab Recuperado",
                             "memo kept serving the failure's base-only fallback")

    def test_seam_swap_invalidates(self):
        """A replaced merge/localize implementation (every other test file
        patches these) must never be fed the previous implementation's
        output."""
        self._bind(self._canonical_lang())
        rows = site_catalog._services()
        self.assertEqual(len(rows), 2)
        real_merge = catalog.merge_with_overlay
        catalog.merge_with_overlay = lambda tipo, base: ([{"id": "x"}], {})
        try:
            swapped = site_catalog._services()
            self.assertEqual(self._by_id(swapped), {"x": {"id": "x"}})
        finally:
            catalog.merge_with_overlay = real_merge
        self.assertEqual(len(site_catalog._services()), 2)


class TestUncachedPathIsolation(_MemoTest):
    """2026-08-16 final review: the uncached returns (sustained-invalidation
    fallthrough, unreadable fingerprint) used to hand out the raw pipeline
    result, whose nested fields are shared with the source/overlay caches —
    a caller could poison the caches and every later memoized read. Both
    paths now freeze/thaw like every other return: the nested containers a
    caller would have mutated are read-only, and the freeze that produced
    them is CONSTRUCTIVE (new containers — the source doc's own lists/dicts
    are never wrapped or touched).

    "Concurrent" reader+invalidation is reproduced as a DETERMINISTIC
    choreography (an armed seam wrapper dropping the catalog cache inside
    every pipeline run) rather than threads — same interleaving, no flake."""

    def _pristine_lab(self, rows):
        return self._by_id(rows)["lab"]

    def test_churn_fallthrough_result_never_contaminates_source_or_later_reads(self):
        self._bind(self._canonical_lang())
        site_catalog._services()  # warm (publishes)
        real = site_base._with_overlay
        armed = {"on": False}

        def churning_overlay(tipo, base):
            if armed["on"]:
                catalog.invalidate()  # a write lands inside EVERY pipeline
            return real(tipo, base)

        site_base._with_overlay = churning_overlay
        try:
            armed["on"] = True
            site_catalog._SERVICES_MEMO.clear()
            rows = site_catalog._services()
            # both stability attempts saw the cache drop mid-pipeline: nothing
            # was published, so `rows` provably came from the uncached
            # fallthrough — exactly the path that used to leak
            self.assertEqual(len(site_catalog._SERVICES_MEMO), 0)
            # the caller tries to mutate the fallthrough result: top-level
            # writes are call-private, nested mutations RAISE
            clin = self._by_id(rows)["clinica"]
            lab = self._pristine_lab(rows)
            clin["nombre"] = "HACKED"
            with self.assertRaises(TypeError):
                clin["palabras"].append("hacked")
            with self.assertRaises(TypeError):
                lab["palabras"].append("hacked")
            with self.assertRaises(TypeError):
                lab["info"]["nota"] = "HACKED"
            # the freeze was constructive: the SOURCE doc's own containers
            # are still plain (and unpoisoned)
            for srv in self.doc["servicios"]:
                for v in srv.values():
                    self.assertNotIsInstance(
                        v, (site_catalog._FrozList, site_catalog._FrozDict),
                        "freeze wrapped a SOURCE container in place")
            self.assertNotIn("hacked", self.doc["servicios"][1]["palabras"])
            self.assertEqual(self.doc["servicios"][1]["info"]["nota"], "Sin ayuno")
            self.assertEqual(self.doc["servicios"][0]["nombre"], "Clínica Médica")
            # …and later reads (still churning, then settled) are pristine
            again = self._pristine_lab(site_catalog._services())
            self.assertNotIn("hacked", again["palabras"])
            self.assertEqual(again["info"]["nota"], "Sin ayuno")
            self.assertEqual(self._by_id(site_catalog._services())["clinica"]["nombre"],
                             "Medicina General")
            armed["on"] = False
            settled = self._pristine_lab(site_catalog._services())
            self.assertNotIn("hacked", settled["palabras"])
            self.assertEqual(settled["info"]["nota"], "Sin ayuno")
        finally:
            site_base._with_overlay = real

    def test_unreadable_fingerprint_result_is_isolated_too(self):
        """servicios.json gone (d falsy -> fp None): the rows come from the
        overlay alone, sharing nested fields with catalog's raw-rows cache —
        still never handed out unfrozen."""
        self._bind(self._canonical_lang())
        self.db_state["rows"] = [_overlay_row("Lab Solo Overlay")]
        site_base._load = lambda name: (None if name == "servicios.json"
                                           else self._load_bak(name))
        rows = site_catalog._services()
        lab = self._pristine_lab(rows)
        self.assertEqual(lab["nombre"], "Lab Solo Overlay")
        lab["info"] = {"nota": "HACKED"}  # top-level write: call-private
        with self.assertRaises(TypeError):
            lab["palabras"].append("hacked")
        # catalog's raw-rows cache (the data this result was built from) is
        # still plain and unpoisoned
        for row in catalog._RAW_CACHE["servicios"][1]:
            for v in row["doc"].values():
                self.assertNotIsInstance(
                    v, (site_catalog._FrozList, site_catalog._FrozDict))
        again = self._pristine_lab(site_catalog._services())
        self.assertNotIn("hacked", again["palabras"])
        self.assertNotIn("info", again)  # overlay doc has no `info` field


class TestAdversarialRaces(_MemoTest):
    """Deterministic reproductions of the two 2026-08-16 review findings.
    Each mid-pipeline event is injected through a seam wrapper installed
    BEFORE the warm-up (so the seam-identity fingerprint leg stays stable and
    the injected leg is the only thing that moves)."""

    def test_source_replaced_mid_pipeline_is_not_published_under_the_new_state(self):
        self._bind(self._canonical_lang())
        real = site_base._with_overlay
        state = {"calls": 0, "swap_on": None}
        doc3 = _base_doc()
        doc3["servicios"][1]["nombre"] = "Laboratorio Tercero"

        def wrapper(tipo, base):
            state["calls"] += 1
            if state["calls"] == state["swap_on"]:
                self.doc = doc3  # the pack file re-parses DURING the pipeline
            return real(tipo, base)

        site_base._with_overlay = wrapper
        try:
            site_catalog._services()  # cold warm, publishes under doc1
            doc2 = _base_doc()
            doc2["servicios"][1]["nombre"] = "Laboratorio Dos"
            self.doc = doc2
            state["swap_on"] = state["calls"] + 1
            # call under churn: attempt 0 computes from doc2 while the source
            # becomes doc3 -> pre != post -> discarded; the retry recomputes
            # under doc3 and publishes THAT. (The old code published the
            # doc2 result under doc3's fingerprint and served doc2 forever.)
            rows = site_catalog._services()
            self.assertEqual(self._by_id(rows)["lab"]["nombre"], "Laboratorio Tercero")
            calls_after = state["calls"]
            # and the published entry matches its own state: next call is a hit
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Laboratorio Tercero")
            self.assertEqual(state["calls"], calls_after, "hit re-ran the pipeline")
            stored = next(iter(site_catalog._SERVICES_MEMO.values()))
            self.assertEqual(self._by_id(stored[1])["lab"]["nombre"], "Laboratorio Tercero")
        finally:
            site_base._with_overlay = real

    def test_catalog_invalidate_mid_pipeline_never_pins_old_rows(self):
        """The 'none' collapse: with the admin write landing after the merge
        read the rows but before the memo published, the post-pipeline
        overlay state is the informationless 'none' — which a hit would never
        refresh, pinning whatever was computed. The publish gate must refuse
        it; only deadline-bearing states may own a memo entry."""
        self._bind(self._canonical_lang())
        self.db_state["rows"] = [_overlay_row("Lab Editado")]
        real_loc = pack_l10n.localize
        state = {"calls": 0, "fire_on": None}

        def loc_wrapper(kind, rows, lang=None):
            state["calls"] += 1
            out = real_loc(kind, rows, lang)
            if state["calls"] == state["fire_on"]:
                catalog.invalidate()  # admin write lands mid-pipeline
            return out

        pack_l10n.localize = loc_wrapper
        try:
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Lab Editado")
            self.db_state["rows"] = [_overlay_row("Lab Re-Editado")]
            catalog.invalidate()
            state["fire_on"] = state["calls"] + 1
            # churn call: stable-but-unpublishable ('none'), then a retry that
            # ends unstable -> served uncached, nothing poisoned stored
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Lab Re-Editado")
            # a later call publishes cleanly under 'live'
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Lab Re-Editado")
            # the pin probe: another admin write. The old code hit the 'none'
            # entry here and served "Lab Re-Editado" indefinitely.
            self.db_state["rows"] = [_overlay_row("Lab Tercero")]
            catalog.invalidate()
            self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                             "Lab Tercero")
            for fp, _rows in site_catalog._SERVICES_MEMO.values():
                self.assertIn(fp[4][0], ("live", "db-fail"),
                              "a memo entry was published under an informationless state")
        finally:
            pack_l10n.localize = real_loc

    def test_l10n_refresh_mid_pipeline_never_pins_the_old_translation(self):
        self._bind("en")
        trans = {"Laboratorio": "Laboratory"}

        def fake_cached_text(key, text, lang, pack_lang=None):
            return trans.get(text, text)

        real_loc = pack_l10n.localize
        state = {"calls": 0, "fire_on": None}

        def loc_wrapper(kind, rows, lang=None):
            state["calls"] += 1
            out = real_loc(kind, rows, lang)
            if state["calls"] == state["fire_on"]:
                pack_l10n.invalidate_memo()
                trans["Laboratorio"] = "Lab Final"  # cache-fill lands mid-pipeline
            return out

        with mock.patch.object(pack_l10n.translate, "cached_text", fake_cached_text):
            pack_l10n.localize = loc_wrapper
            try:
                self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                                 "Laboratory")
                trans["Laboratorio"] = "Lab Department"
                pack_l10n.invalidate_memo()
                state["fire_on"] = state["calls"] + 1
                # churn call: attempt 0 localizes "Lab Department" and the
                # state collapses back to 'absent' (stable but unpublishable);
                # the retry re-localizes under "Lab Final" and serves uncached
                self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                                 "Lab Final")
                # the pin probe: nothing changed, but the old code HIT the
                # poisoned 'absent' entry and served "Lab Department" here
                self.assertEqual(self._by_id(site_catalog._services())["lab"]["nombre"],
                                 "Lab Final")
            finally:
                pack_l10n.localize = real_loc


class TestPublishGate(_MemoTest):
    def test_informationless_states_are_not_publishable(self):
        lang = self._canonical_lang()
        self._bind(lang)
        catalog.invalidate()
        fp = site_catalog._services_fingerprint(self.doc, lang)
        self.assertEqual(fp[4][0], "none")
        self.assertFalse(site_catalog._services_fingerprint_publishable(fp))
        catalog._RAW_CACHE["servicios"] = (time.time() - catalog._TTL - 1, [])
        fp = site_catalog._services_fingerprint(self.doc, lang)
        self.assertEqual(fp[4][0], "expired")
        self.assertFalse(site_catalog._services_fingerprint_publishable(fp))
        catalog._RAW_CACHE["servicios"] = (time.time(), [])
        fp = site_catalog._services_fingerprint(self.doc, lang)
        self.assertEqual(fp[4][0], "live")
        self.assertTrue(site_catalog._services_fingerprint_publishable(fp))
        catalog._DB_FAIL_UNTIL["servicios"] = time.time() + 5
        fp = site_catalog._services_fingerprint(self.doc, lang)
        self.assertEqual(fp[4][0], "db-fail")
        self.assertTrue(site_catalog._services_fingerprint_publishable(fp))


class TestLocalizationPreserved(_MemoTest):
    def test_canonical_language_returns_pack_values(self):
        self._bind(self._canonical_lang())
        rows = self._by_id(site_catalog._services())
        self.assertEqual(rows["lab"]["nombre"], "Laboratorio")
        self.assertEqual(rows["clinica"]["nombre"], "Medicina General")

    def test_localized_language_follows_translation_refresh(self):
        self._bind("en")
        trans = {"Laboratorio": "Laboratory"}

        def fake_cached_text(key, text, lang, pack_lang=None):
            return trans.get(text, text)

        with mock.patch.object(pack_l10n.translate, "cached_text",
                               fake_cached_text):
            rows = self._by_id(site_catalog._services())
            self.assertEqual(rows["lab"]["nombre"], "Laboratory")
            # a cache-fill lands a better translation and pack_l10n drops its
            # memo — the services memo must not pin the older one
            trans["Laboratorio"] = "Lab Department"
            pack_l10n.invalidate_memo()
            rows = self._by_id(site_catalog._services())
            self.assertEqual(rows["lab"]["nombre"], "Lab Department")


if __name__ == "__main__":
    unittest.main()
