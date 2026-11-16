#!/bin/sh
# install.sh — Human Rounds, one-command install.
#
# Checks Docker + the Compose plugin, creates .env from .env.example if it's
# missing, builds and starts the stack (docker-compose.yml: app + Postgres),
# waits for the app container to report healthy, then prints the /setup URL
# — a fresh install has zero operators and zero config rows, so the app
# lands on the first-run wizard automatically (docs/architecture/
# setup-wizard.md). No arguments, nothing to configure up front.
#
# POSIX sh only (no bashisms) — runs under dash, bash, and macOS's /bin/sh.

set -eu

info() {
    printf '%s\n' "$*"
}

err() {
    printf 'install.sh: %s\n' "$*" >&2
}

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd -- "$REPO_DIR"

# ---- 1. Docker + Compose plugin -------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    err "Docker not found. Install it first: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    err "The 'docker compose' plugin isn't available (the old standalone"
    err "'docker-compose' binary doesn't count). See: https://docs.docker.com/compose/install/"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    err "Docker is installed but the daemon isn't running. Start Docker Desktop"
    err "(or colima/similar) and run this again."
    exit 1
fi

# ---- 2. .env ----------------------------------------------------------------

if [ ! -f .env ]; then
    if [ ! -f .env.example ]; then
        err ".env.example is missing — is this a real checkout of the repo?"
        exit 1
    fi
    cp .env.example .env
    info "Created .env from .env.example."
    info "It runs as-is (Mailgun, WhatsApp, HSI, RENAPER... all degrade gracefully"
    info "when unset — see CLAUDE.md's 'nothing breaks because of email or HSI')."
    info "Edit .env later to wire up real integrations."
    info ""
fi

# ---- 3. Build + start ---------------------------------------------------

info "Building and starting Human Rounds (first run can take a few minutes)..."
docker compose up -d --build

# ---- 4. Wait for the app container to report healthy ----------------------

PORT=$(grep '^PORT=' .env 2>/dev/null | tail -n 1 | cut -d= -f2)
PORT=${PORT:-7700}

info "Waiting for the app to become healthy..."

attempt=0
max_attempts=60
healthy=0

while [ "$attempt" -lt "$max_attempts" ]; do
    cid=$(docker compose ps -q app 2>/dev/null || true)
    if [ -n "$cid" ]; then
        health=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")
        if [ "$health" = "healthy" ]; then
            healthy=1
            break
        fi
        if [ "$health" = "unhealthy" ]; then
            err "The app container reported unhealthy. Check the logs:"
            err "  docker compose logs app"
            exit 1
        fi
    fi
    attempt=$((attempt + 1))
    sleep 2
done

if [ "$healthy" -ne 1 ]; then
    err "The app didn't report healthy in time. Check the logs:"
    err "  docker compose logs app"
    exit 1
fi

info ""
info "Human Rounds is up."
info "Open the setup wizard: http://localhost:${PORT}/setup"
info ""
info "Stop it:     docker compose down"
info "Tear down:   docker compose down -v   (also deletes the database volume)"
info "Logs:        docker compose logs -f app"
