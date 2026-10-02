#!/usr/bin/env bash
# Start the complete local Lab Console stack in its production-style mode.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -x api/.venv/bin/uvicorn ]]; then
  printf 'Dependências Python ausentes. Execute:\n'
  printf '  cd %s/api && uv sync --frozen --extra dev --cache-dir /private/tmp/mysqlconf-lab-uv-cache\n' "$SCRIPT_DIR"
  exit 1
fi

if [[ ! -x web/node_modules/.bin/next ]]; then
  printf 'Dependências Web ausentes. Execute:\n'
  printf '  cd %s/web && npm ci --cache /private/tmp/mysqlconf-lab-npm-cache\n' "$SCRIPT_DIR"
  exit 1
fi

if [[ ! -f web/.next/BUILD_ID ]]; then
  printf 'Build Web não encontrado; criando build de produção...\n'
  (
    cd web
    LAB_API_URL=http://127.0.0.1:8000 npm run build
  )
fi

exec python3 scripts/dev.py --production --integrated --allow-execute
