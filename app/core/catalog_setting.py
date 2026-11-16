"""catalog_setting.py - one global setting kept in the `catalogo` table (tipo="config"), read through a short cache.

The visual theme (`core/theme.py`) and the patient sign-in mode (`auth/mode.py`) are the same thing: a single
installation-wide choice the operator edits from Settings, saved as `{field: id}` under `config/<doc_id>`, read
back at most every few seconds, and replaced by a default when nothing is saved or the database is down (a
setting must never be the reason a page fails). Each keeps its own ids, its own default and its own error text;
the read/validate/save/cache routine lives here once.

`cache` is the {"v": value, "t": read-at} dict; the owner module exposes it as its `_cache` so a test can seed or
reset it.
"""

import time


class CatalogSetting:
    def __init__(self, doc_id, field, valid, default, invalid_message, ttl=3.0):
        """`valid` is a container of the ids a setting may take; `default` a callable giving the id to use when
        nothing valid is saved; `invalid_message` the `%r` template of the ValueError `save` raises."""
        self.doc_id = doc_id
        self.field = field
        self.valid = valid
        self.default = default
        self.invalid_message = invalid_message
        self.ttl = ttl
        self.cache = {"v": None, "t": 0.0}

    def current(self):
        """The saved id if there is a valid one, else the default; cached for `ttl` seconds. Never raises."""
        now = time.time()
        if self.cache["v"] is not None and now - self.cache["t"] < self.ttl:
            return self.cache["v"]
        val = self.default()
        try:
            from app.core import db_catalog
            row = db_catalog.catalog_get("config", self.doc_id)
            if row:
                doc = row.get("doc") or {}
                cand = str(doc.get(self.field) or "").strip().lower()
                if cand in self.valid:
                    val = cand
        except Exception:  # noqa: BLE001 — DB down/missing => default, never 500
            pass
        self.cache["v"] = val
        self.cache["t"] = now
        return val

    def save(self, value):
        """Persists `value` (a valid id), forgets the cached read and returns the id saved."""
        vid = str(value or "").strip().lower()
        if vid not in self.valid:
            raise ValueError(self.invalid_message % (value,))
        from app.core import db_catalog
        db_catalog.catalog_upsert("config", self.doc_id, {self.field: vid})
        self.cache["v"] = None
        self.cache["t"] = 0.0
        return vid
