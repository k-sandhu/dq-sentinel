#!/usr/bin/env bash
# One-shot dev bootstrap (macOS/Linux).
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
venv="$HOME/.venvs/dq-sentinel"

[ -d "$venv" ] || python3 -m venv "$venv"
"$venv/bin/pip" install --upgrade pip --quiet
"$venv/bin/pip" install -e "$repo/backend[dev]" --quiet
echo "Backend deps installed."

[ -f "$repo/samples/shopdb.sqlite" ] || "$venv/bin/python" "$repo/data/generate_sample_data.py"
[ -f "$repo/.env" ] || { cp "$repo/.env.example" "$repo/.env"; echo "Created .env (add ANTHROPIC_API_KEY for AI features)."; }

(cd "$repo/frontend" && [ -d node_modules ] || npm install)

cat <<EOF

Ready. Start each in its own terminal:
  1) API:      $venv/bin/uvicorn app.main:app --reload --port 8000 --app-dir $repo/backend
  2) Worker:   cd $repo/backend && $venv/bin/python -m app.worker
  3) Frontend: cd $repo/frontend && npm run dev

UI: http://localhost:5173   Login: admin@example.com / admin123
Sample source DSN: sqlite:///$repo/samples/shopdb.sqlite
EOF
