# Use-case suite (46 acceptance cases)

Browser acceptance suite for the use-case matrix (46 cases, kept with the private design plans)
(groups A–E). Needs the dev server already running on `http://127.0.0.1:7700`.

```sh
node tests/usecases/run.mjs      # seeds fixtures (safe: local dev DB only), drives all 46 cases
node tests/usecases/report.mjs   # writes artifacts/usecases/report.html from results.json
```

- `fixtures.py` — idempotent seed (own doctors/services/centros via the catalog
  overlay; suite-owned `uc.suite` admin + patient creds in
  `artifacts/usecases/.creds.json`).
- `run.mjs` — Playwright: per case Action/Expected, desktop PNG 1280x900 +
  phone PNG 390x844, `artifacts/usecases/results.json`
  (`id/group/title/verdict/assertion/observed`; verdicts pass · FAIL ·
  known-defect · blocked).
- `report.mjs` — self-contained `report.html` (five groups, thumbnails with a
  lightbox; ← → between cases).
