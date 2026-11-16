# Repository layout

Since 2026-11 the application is one Python package, `app/`, and the tests live in `tests/`. The root keeps
only what a tool expects there (Docker, git, CI, npm, Alembic) and the documents a reader looks for first.
`tests/test_repo_layout.py` holds the root to the list below: a new file or directory at the root, a new
top-level package, or a `.py` beside the three temporary shims is red.

## What is where

| Path | What |
|---|---|
| `app/` | All application Python. `app/main.py` is the FastAPI composition (was `app.py`); `app/asgi_main.py` the process (uvicorn, the supervised worker); `app/worker.py` the queue worker; `app/manage.py` the CLI; `app/__main__.py` makes `python3 -m app` start the service. The twelve packages (`admin_panel ai auth connectors core cuentas modules notifications ops patient_web routers services`) sit inside it unchanged. |
| `app/core/paths.py` | The one place that derives a directory from `__file__`: `PROJECT_ROOT` (the repository), `CODE_ROOT` (`app/`), `ROOT_WEB_ASSETS` (the files the site serves at `/`, kept in `static/root/`). |
| `tests/` | Was `test/`. Backend (`tests/test_*.py`, `tests/routes/`), frontend (`tests/*.test.mjs`), browser (`tests/e2e/`), fixtures, cassettes. |
| `static/root/` | The ten files the site serves from the URL root (`/admin-app.js`, `/tokens.css`, `/logo.svg`, …): their URLs did not change. |
| `templates/` | As before, plus `robots.txt` and `llms.txt` (templates with `{{sitio.*}}` tokens). |
| `docs/policies/`, `.github/` | The policy documents that were at the root (`DATA_MAP.md`, `DPA.md`, `CLINICAL_SAFETY.md`, …); GitHub's own files go to `.github/`. |
| `docs/design/` | Was `design-plans/` and `design-qa.md`. |
| `scripts/tools/`, `scripts/usability/` | Was `tools/` and the usability files at the root. |
| `scripts/layout/` | The tools that made this move (`move.py`, `apply.py`, `port_branch.py`, `clean_stale.py`, `import_all.py`) — used to carry an open branch across (below). |
| `asgi_main.py`, `worker.py`, `manage.py` (root) | Temporary three-line shims that run `app.asgi_main`, `app.worker`, `app.manage`, so a start command written before the move keeps working. They are removed by a later slice, once every installation starts with the new commands. |

## Names that did not change

- **URLs.** Every public URL is the same: `/admin-app.js`, `/static/...`, `/modules/<id>/<file>` (a module asset is still named relative to the code package), `/robots.txt`.
- **Queue task names.** The fourteen procrastinate task names are durable rows in the database and stay as they were (`modules.turnos.waitlist.sweep`, `worker.maintenance.remove_old_jobs`, …); `tests/test_queue_task_names.py` pins them.
- **Names inside the code package.** A template name (`modules/chat/admin_chats.html`), a manifest's `locales`, a module asset name are relative to `app/`, like the imports.
- **Environment keys, the database, Alembic revisions, the data volume** (`/app/data` in the container is the same mount).

## What changes for you

- **Start commands.** `python3 -m app` (service), `python3 -m app.worker`, `python3 -m app.manage <command>`. The old `python3 asgi_main.py` / `worker.py` / `manage.py` keep working until the shims are removed. The image's `CMD` is `python3 -m app`.
- **Imports.** `from app.core import db`, `from app.modules.turnos import booking_requests`; a `mock.patch` target is `"app.core.db.connect"`.
- **Tests.** `python3 -m unittest discover -s tests -p "test_x.py"`; `node --test tests/x.test.mjs`. The managed executor's profiles take `--test tests/test_x.py`. A hook refuses `discover -s test` (it would discover the standard library's own suite) and suggests `-s tests`.
- **Log lines** name the logger by its module, now with the package: `app.core.api_body`, not `core.api_body`. A log search or alert keyed on a logger name needs the prefix.
- **The static fallback** serves an explicit list only — `/static/`, `/vendor/`, `/webdata/`, the root web assets and the assets module manifests declare. Source, tests and documents answer 404 under any path (before, any checkout file with a served extension answered, e.g. `/templates/about.html` on the demo). A directory-shaped path outside the asset trees (`/admin-app.js/`) is a 404 too, it was a 403 (inside `/static/` it stays 403); a dot segment and `/data/` stay 403. Inside the asset trees a document or a source is not served either (`/static/platform/AGENTS.md`, the `.py`/`.ts`/`.toml` of a vendored kit — 200 before), and a root web asset has its root URL only (`/static/root/admin-app.js` is a 404). `/human-rounds-mark-padded.png` is a 404: the stray copy at the root (nothing linked it; the logs of pinamar and the demo show no request for it) is deleted, the working copy stays at `/static/human-rounds-mark-padded.png`.
- **Paths on your machine and in your hub.** The LaunchAgent that runs `tools/msd_refresh.sh` → `scripts/tools/msd_refresh.sh`. The skills that write `design-plans/` or `design-qa.md` at the root (improve-ui, handoff, design-approach, and the global CLAUDE.md) → `docs/design/plans/` and `docs/design/design-qa.md`; the old root paths stay in `export-ignore` and in the public cut's patterns, so a note written there by an old skill never ships, and `tests/test_repo_layout.py` is red if one is committed. The restart-on-edit hook that watches `test/` → `tests/`. `deliver_remote.py` needs the one-line change that works with both layouts. Briefs and one-off scripts that name `core/...`, `test/...` or a root asset by path.

## Carrying an open branch across

A branch cut before the move: `python3 scripts/layout/port_branch.py <branch>` rebases it onto the `main`
the move was applied to, replays the move on top (the same `move.py`, so the branch's own imports and paths
are rewritten too) and puts the branch's change, now in the new layout, as one commit on a new branch
`<branch>-layout`. The original branch is never touched and nothing is pushed; a step that needs a person
stops with the message and the temporary worktree's path.

After switching an old checkout to the new layout, `python3 scripts/layout/clean_stale.py` lists what
keeps the emptied directories alive (`core/__pycache__/` makes `import core` an empty namespace package
that fails late); `--apply` removes the caches and the empty directories, and only lists any other file
with the place it belongs now.
