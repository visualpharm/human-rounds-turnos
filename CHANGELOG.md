# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
No tagged releases yet — see [docs/UPGRADING.md](docs/UPGRADING.md)'s
"Release conventions" for when `v1.0.0` starts and what the version
numbers will mean once it does.

## [v0.9.0-pre] — 2026-07-30

Pre-release snapshot of the open-source rework. Honest state: this is
mid-migration, not finished — the Pinamar reference deployment runs this
code in real production throughout, and every layer below degrades
gracefully rather than blocking a working install. What's here:

- **English rename, in progress.** Python modules, JS files/globals, API
  paths, and DB tables/columns are being renamed from Spanish to English
  file by file — [docs/rename-map.md](docs/rename-map.md) tracks what's
  done and what's pending. Data-pack JSON keys (`nombre` → `name`, and so
  on) are a separate, slower-moving slice of the same rename — see
  "JSON key rename to English" in
  [docs/architecture/data-packs.md](docs/architecture/data-packs.md).
- **Layered data packs.** `packs/` replaces one flat data directory with
  five composable locality levels (global → country → province → town →
  center); Pinamar's own data and a fictional `example-town` starter pack
  both run through the same chain. Design → [docs/architecture/data-packs.md](docs/architecture/data-packs.md).
- **i18n core.** UI-string catalogs, a `t()` pipeline, per-visitor language
  resolution, and a cached machine-translation layer for pack content
  (service names, descriptions) so switching languages doesn't
  re-translate on every toggle. Off by default. → [docs/architecture/i18n.md](docs/architecture/i18n.md).
- **Runtime Settings.** An admin section for pack chain, languages, and
  API keys (with OpenRouter primary/backup failover), replacing hardcoded
  env-only configuration for anything an operator should be able to change
  without a redeploy. → [docs/architecture/settings.md](docs/architecture/settings.md).
- **First-run installer wizard.** A fresh install with no operator and no
  config lands on `/setup`: location → data pack → language(s) → admin
  account → API keys, then the site goes live. → [docs/architecture/setup-wizard.md](docs/architecture/setup-wizard.md).
- **Connectors.** Six capability interfaces (identity verification,
  insurance lookup, license lookup, EMR sync, third-party slot source,
  OTP/message transport) generalized from Argentina-specific code
  (RENAPER, PUCO/SISA, REFEPS, HSI, Mi Salud Digital, WAHA/Meta) into a
  swappable-per-deployment shape. A capability with no connector
  configured degrades instead of crashing. → [docs/connectors.md](docs/connectors.md).
- **Packaging.** `docker-compose.yml` + `install.sh` for a 5-minute Docker
  install, a bare-metal path, `.env.example` documenting every variable,
  and this rewritten README/CONTRIBUTING/SECURITY set for an external
  contributor cloning the repo cold.
- **This update model.** `schema_migrations` (forward-only, idempotent,
  applied at boot) plus the catalog-overlay contract that lets local admin
  edits survive an upstream pack update — see
  [docs/UPGRADING.md](docs/UPGRADING.md).

Known gaps, tracked rather than hidden: several data-pack JSON files are
still Spanish-keyed (rename-map.md has the list); `merged()`-style pack
composition for shared taxonomies (specialties/clusters) is designed and
tested but not wired into the two real call sites yet (a design gap, not a
bug — see data-packs.md's "Wiring especialidades.json/clusters.json
through merged()" for why); per-center (L5) packs and
`conocimiento.json`'s jurisdiction-weight generalization are deferred,
both flagged in data-packs.md with the reasoning.
