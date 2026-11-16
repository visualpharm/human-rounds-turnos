# Alembic Multi-Head Prevention

**Symptom:** Site suddenly single-language + admin API endpoints return 503. Console shows `DbUnavailable: "Multiple head revisions"` or similar. Prod (`pinamar.humanrounds.org`) and demo (`demo.humanrounds.org`) both fail immediately after a push.

**Root Cause:** A merge renumbered an Alembic migration file (e.g., `0012_users_identity_kind.py` → `0013_users_identity_kind.py`) but left the old file in place. Alembic's `upgrade(head)` command fails because it cannot determine a single target revision, causing `db.py:_ensure_schema` to raise `DbUnavailable` on boot. Every database read then fails, including the config read that loads language settings and every admin API hit.

**Incident:** 2026-09-13, hotfix d7ba0e90 deleted the stale file after the fact.

## Fix

Delete or renumber the stale migration file in `alembic/versions/`. If you're unsure which file is stale:
- Check the git history: `git log --oneline --follow -- alembic/versions/0012*`
- The newer file (higher revision number) is the canonical one.
- The file it was renamed FROM should be deleted unless it diverged from the newer one (highly unlikely).

## Guard

**Pre-commit hook** (`scripts/pre-commit.sh`): Runs `scripts/check_alembic_single_head.py` as the FIRST step, <1s, before any other tests. Blocks the commit with a clear message naming the duplicate heads.

**Test** (`test/test_alembic_single_head.py`): Unit test verifying the repo's `alembic/versions/` has exactly one head. Runs in `TURNOS_FAKE_AI=1 python3 -m unittest discover`.

**CI** (`.github/workflows/ci.yml`): The backend test job includes the full test discovery, which will run the new Alembic head test on dispatch.

## How to Check a Live Database's Revision

To verify which revision a running database is stamped at (without printing passwords):

```sql
SELECT version_num FROM alembic_version;
```

In a container:

```bash
docker exec <container-id> psql -U <user> -d <dbname> -c "SELECT version_num FROM alembic_version;"
```

The result should match the current repo's single head (check with `python3 scripts/check_alembic_single_head.py`). If it's behind, the database needs to run Alembic's upgrade on next boot.

## Prevention

- **Always renumber or delete cleanly** when resolving a merge that touches migration files. Never leave both the old and new names in place.
- **Run pre-commit locally** before pushing. The hook catches this instantly.
- **Verify CI passes** (manual dispatch if auto-run is disabled).
