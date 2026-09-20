#!/usr/bin/env bash
# Daily recipe pipeline: ingest -> publish -> git -> (site files served by Caddy)
set -euo pipefail

export RECIPE_DEST="${RECIPE_DEST:-$HOME/Documents/Recipes}"
export RECIPE_REPO="${RECIPE_REPO:-$HOME/opencode-workspace/recipes}"
export RECIPE_LEDGER="${RECIPE_LEDGER:-$HOME/.config/recipe-agent/processed.log}"
export RECIPE_CACHE="${RECIPE_CACHE:-$HOME/.cache/recipe-agent}"

python3 "$HOME/bin/recipe-ingest.py" || true

python3 "$HOME/bin/recipe-publish.py"

cd "$RECIPE_REPO"
if git diff --quiet && git diff --cached --quiet; then
  echo "recipes: nothing to commit"
else
  git add -A
  git -c user.name="vonninnyhammer" -c user.email="vonninnyhammer@users.noreply.github.com" \
      commit -m "recipes: daily update $(date +%F)"
  git push origin main || echo "recipes: push failed" >&2
fi