# Installing Human Rounds — Turnos

Two paths: Docker Compose (recommended, 5 minutes) or bare metal (Python +
Postgres directly on the host).

Either way, a fresh install with no configured operator lands on `/setup` —
the first-run wizard walks you through location, data pack, language(s), an
admin account, and (optionally) an OpenRouter key. See
[architecture/setup-wizard.md](architecture/setup-wizard.md) for what it
does under the hood.

## Docker Compose

Requirements: [Docker](https://docs.docker.com/get-docker/) with the
`docker compose` plugin (not the old standalone `docker-compose` binary).

```bash
git clone <repository-url>
cd sana-turnos
./install.sh
```

`install.sh` checks Docker is present and running, copies `.env.example` to
`.env` if you don't have one yet, runs `docker compose up -d --build`, waits
for the app container to report healthy, and prints the setup URL. If you'd
rather run the steps yourself:

```bash
cp .env.example .env
docker compose up -d --build
open http://localhost:7700/setup   # or just visit it in a browser
```

`docker-compose.yml` builds the app from the repo's own `Dockerfile` and
boots a `postgres:16-alpine` next to it, with a named volume for the
database and another for `/app/data` (uploaded referral files — not the
data pack, see below). `DATABASE_URL` is wired to point at the compose
network automatically; anything else you put in `.env` (Mailgun or Mailjet, WhatsApp,
HSI, an OpenRouter key...) flows straight into the container.

Useful commands:

```bash
docker compose logs -f app     # tail the app's logs
docker compose down            # stop, keep the data
docker compose down -v         # stop and wipe the database + upload volume
```

## Bare metal

Requirements: Python 3.12+, a reachable Postgres 14+ instance.

```bash
git clone <repository-url>
cd sana-turnos
python3 -m pip install --break-system-packages 'psycopg[binary]' 'Pillow>=11.0'
```

Two more OS packages the AI order-reading path needs (referral PDFs get
converted to PNG before they're sent to the model):

```bash
# Debian/Ubuntu
sudo apt-get install -y poppler-utils ffmpeg
# macOS
brew install poppler ffmpeg
```

Point it at a database and run:

```bash
cp .env.example .env
# edit .env: at minimum DATABASE_URL
DATABASE_URL=postgresql://user:pass@localhost:5432/sana_turnos TURNOS_ASGI=1 PORT=7700 python3 asgi_main.py
```

The schema creates itself on first boot (Alembic, driven by `db.py` —
`alembic_version` tracks it, see [UPGRADING.md](UPGRADING.md)). Open
`http://localhost:7700/setup`.

### Keeping it running (systemd / launchd)

**systemd** (Linux), `/etc/systemd/system/sana-turnos.service`:

```ini
[Unit]
Description=Human Rounds — Turnos
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/opt/sana-turnos
EnvironmentFile=/opt/sana-turnos/.env
Environment=TURNOS_ASGI=1
ExecStart=/usr/bin/python3 asgi_main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now sana-turnos
```

**launchd** (macOS), `~/Library/LaunchAgents/org.humanrounds.sana-turnos.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.humanrounds.sana-turnos</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/opt/sana-turnos/asgi_main.py</string>
  </array>
  <key>WorkingDirectory</key><string>/opt/sana-turnos</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DATABASE_URL</key><string>postgresql://user:pass@localhost:5432/sana_turnos</string>
    <key>TURNOS_ASGI</key><string>1</string>
    <key>PORT</key><string>7700</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/org.humanrounds.sana-turnos.plist
```

Put it behind a real reverse proxy (Caddy, nginx, Traefik) for TLS — the app
itself speaks plain HTTP on `PORT`.

## Environment variables

Every variable the app reads is documented in [`.env.example`](../.env.example),
grouped by cluster, with the default it falls back to when unset. Only
`DATABASE_URL` is required to boot — everything else (email, WhatsApp, AI
order-reading, HSI, identity/insurance/license lookups) degrades gracefully
when missing, per this repo's "nothing breaks because of email or HSI"
invariant. Runtime config that doesn't need a redeploy (pack chain,
languages, API keys) can also be set live from `/admin` → Settings once an
admin account exists — see [architecture/settings.md](architecture/settings.md).

## Loading your own town's data

The engine ships with `packs/example-town/` (a fictional "Sampleton")
and the real Pinamar reference deployment's data. To bring your own town,
copy `packs/example-town/` as a starting point and follow
[architecture/data-packs.md](architecture/data-packs.md) — the setup
wizard's Location step can also scaffold an empty pack for you if your
town doesn't match anything already on disk.
