"""plan.py — the repository layout move, as data: what moves where, stage by stage.

The single source of truth for `scripts/layout/move.py` (the mechanical move), `port_branch.py`
(carrying an open branch across it) and `clean_stale.py` (leftovers in an old checkout). Nothing
here touches the filesystem.

Three stages, applied in this order by the layout chain (docs/LAYOUT.md):

  root   — the repository root is cleaned without moving any Python package: policy documents,
           design notes, root web assets, `tools/`, the usability configs.
  app    — the twelve top-level packages and the four root modules move into one `app/` package.
  tests  — `test/` becomes `tests/`.

A move is (old, new); a directory moves with everything under it. `PYTHON_RENAMES` gives the
dotted-name rule of a stage: an import, a `mock.patch` target or a manifest entry that names `old`
(or anything under it) is rewritten to `new`.
"""

PACKAGES = (
    "admin_panel", "ai", "auth", "connectors", "core", "cuentas", "modules",
    "notifications", "ops", "patient_web", "routers", "services",
)

# Root modules that join the package; the old `app.py` becomes `app.main` because the
# package `app/` shadows a root `app.py` of the same name.
ROOT_MODULES = {
    "app": "app.main",
    "asgi_main": "app.asgi_main",
    "worker": "app.worker",
    "manage": "app.manage",
}

# Files served at the root of the site (`/admin-app.js`, `/logo.png`) and fingerprinted under
# `/static/<name>` — their public names never change, only the directory that holds them.
ROOT_WEB_ASSETS = (
    "admin-app.js", "admin-direct.css", "admin-gate.css", "admin-shell.css", "admin-shell.js",
    "human-rounds.css", "tokens.css", "logo.png", "logo.svg", "pinamar-emblema.svg",
)
ROOT_WEB_ASSETS_DIR = "static/root"

POLICY_DOCS = (
    "CLINICAL_SAFETY.md", "DATA_MAP.md", "DPA.md", "HIGH_AVAILABILITY.md",
    "INCIDENT_RESPONSE.md", "LICENSING.md", "MAINTAINERS.md", "RELEASES.md",
)
GITHUB_COMMUNITY_DOCS = ("GOVERNANCE.md", "SUPPORT.md")

STAGES = ("root", "app", "tests")

MOVES = {
    "root": tuple(
        [(name, "docs/policies/" + name) for name in POLICY_DOCS]
        + [(name, ".github/" + name) for name in GITHUB_COMMUNITY_DOCS]
        + [(name, ROOT_WEB_ASSETS_DIR + "/" + name) for name in ROOT_WEB_ASSETS]
        + [
            ("robots.txt", "templates/robots.txt"),
            ("llms.txt", "templates/llms.txt"),
            ("design-qa.md", "docs/design/design-qa.md"),
            ("design-plans", "docs/design/plans"),
            ("tools", "scripts/tools"),
            ("usability.config.json", "scripts/usability/usability.config.json"),
            ("usability.pinamar.config.json", "scripts/usability/usability.pinamar.config.json"),
        ]
    ),
    "app": tuple(
        [(pkg, "app/" + pkg) for pkg in PACKAGES]
        + [(old + ".py", new.replace(".", "/") + ".py") for old, new in ROOT_MODULES.items()]
    ),
    "tests": (("test", "tests"),),
}

# Tracked files a stage removes. Each one carries the evidence in docs/LAYOUT.md.
DELETES = {
    "root": ("human-rounds-mark-padded.png",),
    "app": (),
    "tests": (),
}

# Files a stage creates, with their exact content.
CREATES = {
    "root": {},
    "app": {"app/__init__.py": '"""Human Rounds — Turnos: the application package."""\n'},
    "tests": {},
}

PYTHON_RENAMES = {
    "root": {},
    "app": dict([(pkg, "app." + pkg) for pkg in PACKAGES] + list(ROOT_MODULES.items())),
    "tests": {},
}

# Strings that look like module paths but are durable identities: procrastinate task names live
# in the database (`procrastinate_jobs.task_name`, periodic defers, `task:<name>` locks). A
# rewrite would orphan every job already queued, so they never change.
FROZEN_STRINGS = frozenset({
    "ops.backup_full.run",
    "ops.data_backups.sweep",
    "ops.housekeeping.sweep",
    "ops.translate_warmup.sweep",
    "modules.turnos.waitlist.sweep",
    "modules.turnos.referrals.retention_sweep",
    "connectors.hsi.hsi_sync.tick",
    "notifications.reminders.sweep",
    "ai.chart_summary.pregen_sweep",
    "worker.maintenance.remove_old_jobs",
    "worker.maintenance.retry_stalled_jobs",
    "worker.maintenance.hsi_sync_watchdog",
    "worker.maintenance.data_backups_watchdog",
    "worker.maintenance.launch_signals",
})

# Records that are only ever appended to: their old paths stay as written, docs/LAYOUT.md maps
# them. Everything else under version control is rewritten.
HISTORICAL_PREFIXES = (
    "CHANGELOG.md",
    "docs/decisions/", "docs/decisions.md",
    "docs/solutions/", "docs/roadmap/", "docs/qa/", "docs/operations/", "docs/benchmarks/",
    "docs/studies/", "docs/usability-study/", "docs/usability/",
    "docs/rename-map.md", "docs/requirements.md", "docs/requirements.json",
    "docs/branch-consolidation-2026-08-08.md", "docs/demo-vs-prod-pack-gaps-2026-08-07.md",
    "docs/sisa-lookup-diagnosis-2026-07-15.md", "docs/coneiturno-findings.md",
    "docs/medical-history-ocr-findings.md",
    "design-plans/", "design-qa.md", "docs/design/",
    "test/README-lineage.md", "tests/README-lineage.md",
)

# Never read or rewritten: third-party code, generated locale catalogs, and the move itself (its
# tools and their tests describe the old layout on purpose).
SKIPPED_PREFIXES = ("vendor/", "locales/", "node_modules/", "static/vendor/", "extensions/dist/",
                    "scripts/layout/", "test/test_layout_", "tests/test_layout_")

TEXT_SUFFIXES = (
    ".py", ".mjs", ".js", ".cjs", ".json", ".md", ".sh", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".txt", ".html", ".css", ".tpl", ".plist",
)
TEXT_NAMES = (
    "Dockerfile", ".gitattributes", ".gitignore", ".dockerignore", ".gitleaksignore",
    ".env.example", ".local-ci.json",
)

# Files whose entries are paths anchored at the repository root with a leading slash.
ROOT_ANCHORED_FILES = (".gitattributes", ".gitignore", ".dockerignore")


def stage_moves(stage):
    """The (old, new) pairs of one stage."""
    if stage not in MOVES:
        raise ValueError(f"unknown layout stage {stage!r}; known: {', '.join(STAGES)}")
    return MOVES[stage]


def map_path(stage, path):
    """New location of a repository-relative `path` after `stage`, or None if it does not move."""
    for old, new in stage_moves(stage):
        if path == old:
            return new
        if path.startswith(old + "/"):
            return new + path[len(old):]
    return None


def map_dotted(stage, name):
    """New dotted name for `name` after `stage`, or None when no rename rule covers it."""
    renames = PYTHON_RENAMES[stage]
    head = name.split(".", 1)[0]
    if head not in renames or name in FROZEN_STRINGS:
        return None
    rest = name[len(head):]
    return renames[head] + rest


def is_historical(path):
    return path.startswith(HISTORICAL_PREFIXES)


def is_skipped(path):
    return path.startswith(SKIPPED_PREFIXES)


def is_text(path):
    name = path.rsplit("/", 1)[-1]
    return name in TEXT_NAMES or name.endswith(TEXT_SUFFIXES)


def all_moves():
    """Every move of every stage, in order — the table docs/LAYOUT.md is checked against."""
    return [(stage, old, new) for stage in STAGES for old, new in MOVES[stage]]
