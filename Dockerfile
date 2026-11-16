# Human Rounds — native FastAPI/uvicorn transport; the stdlib server is retired.
# Production and demo both require TURNOS_ASGI=1;
# see asgi_main.py and docs/architecture/deploy.md. The auth layer adds
# itsdangerous/pyotp/webauthn/cryptography: all four install standard manylinux
# wheels on this image (`python:3.12-slim`, x86_64/arm64), with no new system
# library — verified when building this slice, the apt-get line below was
# untouched.
FROM python:3.12-slim
# The build's git commit (Coolify passes SOURCE_COMMIT as a build arg). It
# stays in the image: `/health` answers it as "version" (app/core/version.py,
# "unknown" without it) and the label lets `docker inspect` say it too.
# Railway injects RAILWAY_GIT_COMMIT_SHA at runtime, read the same way.
ARG SOURCE_COMMIT
ENV SOURCE_COMMIT=${SOURCE_COMMIT}
LABEL org.opencontainers.image.revision=${SOURCE_COMMIT}

WORKDIR /app

# Postgres, referral-image normalization before model processing, and segno
# for intake door-poster QR codes. requirements.txt is the single pinned
# dependency source for Docker and CI.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# poppler-utils converts referral PDFs to PNG before model processing; ffmpeg
# handles the bounded voice preprocessing path.
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils ffmpeg postgresql-client \
 && rm -rf /var/lib/apt/lists/*

COPY . /app

# /app/data is the container's persistent volume (runtime): uploads
# (data/ordenes), the install-owned packs the first-run wizard scaffolds
# (data/packs — see docs/architecture/setup-wizard.md), and now also the
# installation's CANONICAL config. It survives an image rebuild PROVIDED the
# deploy mounts a volume over /app/data — docker-compose.yml does
# (app_data:/app/data) and Coolify must be configured the same way (a
# persistent storage path /app/data). Historically the Pinamar Coolify app did
# NOT have one (GET .../storages returned [], verified 2026-08-03; that is why
# the RENAPER identity register moved to Postgres); the ops repo
# humanrounds-pinamar is where that configuration is attested.
RUN mkdir -p /app/data /app/data/ordenes /app/logs \
 && echo "=== /app/packs in the image ===" && ls -la /app/packs

# The data pack (packs/, plus webdata/ for legacy installs using
# TURNOS_WEBDATA_DIR) ships baked into the image — see
# docs/architecture/data-packs.md. The app never writes install-owned packs
# here: setup._scaffold_pack puts them in data/packs (the volume), not packs/
# (the image), precisely so they survive a rebuild.

# TURNOS_ENV_FILE is the SINGLE effective .env path that config._env_path()
# consults (never an overlay, never a fallback to a second file). The image
# default points it ON THE VOLUME — /app/data/turnos.env — so the boot keys
# the installer writes (TURNOS_PRIVATE_PACKS_DIR / TURNOS_REQUIRED_PACKS /
# PACK_CHAIN, via config.save_env_keys) survive a redeploy like the
# install-owned packs do. Without this, those keys were written to
# config.ENV_PATH (/app/.env), which .dockerignore EXCLUDES from the image —
# so save_env_keys created it at RUNTIME in the ephemeral container filesystem,
# and the next build wiped it: the pack was on the volume but unreachable
# (validate-private, resolve-public).
#
# This is an image DEFAULT: an explicit override in the process (compose
# `environment:`, Coolify env, `docker run -e`) always wins, and direct local
# development through `asgi_main.py` without TURNOS_ENV_FILE reads the checkout
# .env as before — see app/core/config.py and docs/architecture/deploy.md.
ENV HOST=0.0.0.0 \
    PORT=7700 \
    TURNOS_ASGI=1 \
    PYTHONUNBUFFERED=1 \
    TURNOS_ENV_FILE=/app/data/turnos.env

EXPOSE 7700

# Liveness for the orchestrator (Docker, Coolify, Traefik): without it the
# container is reported "running" with health unknown, and a deploy swaps the
# old container out before knowing the new one answers. `/health` is
# process-up only — no database, exempt from the site Basic-Auth gate — so a
# down Postgres never takes the container out of rotation. The probe follows
# HOST (scripts/healthcheck.py): Coolify binds 0.0.0.0, Railway binds :: and
# there 127.0.0.1 is refused. Tolerant on purpose: five consecutive
# misses (~50 s) before "unhealthy", and the boot's schema check is covered by
# the start period.
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=5 \
  CMD ["python3", "/app/scripts/healthcheck.py"]

# Production and demo require TURNOS_ASGI=1. Clearing it refuses startup;
# there is no same-revision legacy rollback. The entrypoint is the application
# package (app/asgi_main.py); `python3 asgi_main.py` keeps working through the
# root shim until no installation starts it that way (docs/LAYOUT.md).
CMD ["python3", "-m", "app"]
