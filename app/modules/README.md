# modules/

Reserved structure for pluggable app modules (Phase 6a of the open-source
rework). See `docs/modules.md` for the full design and how a second module
(intake/triage, teleconsulta, historia-clinica) would plug in.

`turnos/module.json` is the only module manifest today — declarative only:
it describes what the turnos app owns (routes, admin sections, nav entries,
DB tables, config-key prefixes), verified against the real code by
`tests/test_module_manifest.py`. The files themselves still live at the repo
root, not physically under `app/modules/turnos/` — that move is future work, not
this slice's.
