# Fictional starter installation

This package is the clean starting point for a municipality evaluating
Human Rounds — Turnos. It contains no Pinamar records or real health data.

- Locality: Sampleton, a fictional medium-sized Argentine coastal town.
- Institutions: 5 centers — Sampleton, Riverside Clinic, Lakeview Clinic,
  Maternity Center and the Diagnostic Center.
- Services: 70 fictional services across 18 specialties/13 clusters,
  covering all 4 modes (walk-in, walk-in-with-order, appointment,
  appointment-with-order), age-routed pediatric/geriatric bounds, PAMI
  notes, same-day opening, per-service appointment limits and a service
  running at an informal annex (`sede`).
- Professionals: 50 fictional doctors (`fuente: "ficticio"` on every
  matrícula — never a real registry) across 9 schedule templates, so no two
  doctors read as an identical Mon-Fri 08-12.
- Map: the home's black-and-white map of the centers (`centros-mapa.svg` +
  `centros-mapa.json`, built by `scripts/build_centers_map.py`). The streets
  are a real small town's from OpenStreetMap (© OpenStreetMap contributors,
  ODbL, credited on the map), the pins and area names are invented.

All of the above is generated — see
`scripts/generate_example_town_catalog.py`'s module docstring before hand-
editing any of `centros-salud.json`, `especialidades.json`, `clusters.json`,
`medicos.json`, `servicios.json` or `example-localities.json`: it is the
source of truth, and re-running it overwrites hand edits.

Run it either way:

- **Pack mode** (default when `packs/` exists): `PACK_CHAIN=global,example-town`.
  This is what the demo deployment and `TestExampleTownChainIsGeneric`
  (`tests/test_golden_pages.py`) use.
- **Legacy single-dir mode**: `TURNOS_WEBDATA_DIR=packs/example-town` — this
  directory is a self-contained package on its own (all 7 files a data
  package needs, see the repo README's "Un paquete de datos necesita"), so it
  works standalone too, same as it did as `webdata-ejemplo/` before
  2026-07-30 (see docs/architecture/data-packs.md — same content, `pack.json`
  is the only addition, and legacy mode ignores files it doesn't recognize).

Use it only with a separate database. `TURNOS_DEMO_MODE=1` adds the public
demo notice, one-click sandbox admin access and the integration safety
defaults documented in `docs/demo-installation.md`.
