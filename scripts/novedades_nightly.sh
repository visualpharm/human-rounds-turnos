#!/bin/bash
# Actualiza /novedades con los commits del último día.
# Corre como LaunchAgent com.sana-turnos.novedades todas las noches a las 03:04
# (plist en ~/Library/LaunchAgents; log en ~/Library/Logs/sana-turnos-novedades.log).
# Sesión headless de Claude (Sonnet): curaduría editorial, no un dump del git log.
set -euo pipefail
export PATH="$HOME/.nvm/versions/node/v20.19.2/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"
cd "$HOME/projects/sana-turnos"

exec claude -p --model sonnet --dangerously-skip-permissions "$(cat <<'EOF'
Actualizá la página /novedades de Human Rounds — Turnos con los commits del último día.

1. git fetch origin y mirá los commits de origin/main de las últimas 24 horas
   (git log --since='24 hours ago' origin/main).
2. Curá con criterio editorial: entran SOLO los cambios que un paciente o el
   personal del hospital notan. Descartá bumps de caché/?v=, retoques de
   espaciado o CSS menores, tests, docs, merges, reverts y refactors internos.
   Si después de curar no queda nada, terminá SIN tocar nada (ni commit vacío
   ni sección vacía).
3. Editá _NEWS en site_news.py: cada novedad va bajo la fecha del día
   calendario de su commit, formato "16 de julio de 2026", más nuevo primero.
   Si la sección de esa fecha ya existe, completala sin repetir lo ya dicho
   (y sin re-redactar entradas existentes). Copiá el estilo de las entradas
   que ya están: español rioplatense al paciente, una <b>palabra clave</b>
   por ítem, sin jerga técnica, sin nombres de archivos ni de funciones.
4. Nunca toques trabajo sin commitear: si el checkout está sucio o a mitad de
   un rebase, hacé el cambio en un worktree temporal de origin/main
   (git worktree add) y borralo al terminar.
5. Verificá con TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py".
   Con la suite verde, commiteá ("Novedades: <fecha>") y pusheá a origin main
   (rebaseá sobre origin/main si el push rebota). El deploy a producción es
   automático al pushear.
EOF
)"
