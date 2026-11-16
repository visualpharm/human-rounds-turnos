# `scripts/audit_cassettes.py` only understands network-connector cassettes

**Symptom:** `ci_local.sh`'s cassette-audit surface went RED after merging in
an unrelated commit, with no code change of our own to that surface:

```
audit_cassettes.py: 5 cassette(s), 0 warning(s) de antigüedad, 1 error(es) de forma.
audit_cassettes.py: BLOQUEADO — hay cassette(s) con la forma cambiada
```

**Root cause:** `audit_cassettes.py` globs `test/cassettes/<connector>/*.json`
and expects every file it finds there to be a *recorded network interaction*
cassette — `{"connector": ..., "interactions": [...]}` — with a shape checker
registered in its `_CHECKS` dict for exactly four connectors (HSI, PUCO,
RENAPER, Meta). A commit added
`test/test_knowledge_pack_split.py`'s golden regression snapshot
(`pre_split_pinamar_chain.json`, a byte-for-byte pre-refactor copy of
`conocimiento.json`'s composed articles — `{"articles": [...]}`, no
`connector`/`interactions` keys at all) under
`test/cassettes/conocimiento/`, reusing the word "cassette" loosely for
"golden data file." The audit script has no entry for a `"conocimiento"`
connector, so it fell into `_audit_one`'s `else` branch: "no hay checker de
forma registrado — agregar uno antes de confiar en este cassette", counted
as a hard error, exit 1.

**Fix:** the file was never a network cassette — move it out of
`test/cassettes/` entirely (`test/golden/conocimiento/`, a new directory
outside the audit script's glob scope), update the test's path constant and
docstring, and the one architecture-doc cross-reference. Do **not** register
a fake `"conocimiento"` checker in `audit_cassettes.py` — that would imply a
real connector shape contract exists for it, which is false and would
mislead the next reader of that file.

**General rule:** `test/cassettes/<connector>/` is reserved for real
recorded-network-interaction replay fixtures with a registered shape
checker. A golden/snapshot fixture for a non-network regression test (data
migration, refactor safety net) belongs in a differently-named directory
(`test/golden/<topic>/`), even if "cassette" feels like the generic word for
"recorded expected data" — the audit script's directory-name-based glob has
no way to tell the difference, and will hard-fail the CI gate the moment a
misplaced file lands there.

Discovered 2026-08-07 integrating Tier 2 Batch D onto `origin/main` after a
merge with unrelated chat/wiki work — see
[../ai/chat-nunca-fabrica-horarios.md](../ai/chat-nunca-fabrica-horarios.md)
for that commit's own content, and
[../../architecture/data-packs.md](../../architecture/data-packs.md) for
`test_knowledge_pack_split.py`'s design.
