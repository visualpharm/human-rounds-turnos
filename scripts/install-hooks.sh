#!/bin/bash
# install-hooks.sh — one-time setup for the Tier 3 run ladder's pre-commit
# rung (docs/roadmap/tier3-harness-design.md, Area 4). Wires
# scripts/pre-commit.sh into .git/hooks/pre-commit so it runs on every
# `git commit`. Idempotent — safe to re-run.
#
# Usage:
#   scripts/install-hooks.sh

set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

GIT_DIR="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)"
HOOKS_DIR="$GIT_DIR/hooks"
mkdir -p "$HOOKS_DIR"

TARGET="$HOOKS_DIR/pre-commit"
cat >"$TARGET" <<'EOF'
#!/bin/bash
# Installed by scripts/install-hooks.sh — do not edit directly, edit
# scripts/pre-commit.sh instead and re-run the installer.
REPO="$(git rev-parse --show-toplevel)"
exec "$REPO/scripts/pre-commit.sh"
EOF
chmod +x "$TARGET"

echo "install-hooks.sh: .git/hooks/pre-commit instalado (-> scripts/pre-commit.sh)."
echo "  Bypass de emergencia: SKIP_PRECOMMIT=1 git commit ..."
