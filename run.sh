#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_PORT="${API_PORT:-8765}"
WEB_DIR="$ROOT_DIR/web"

cd "$ROOT_DIR"

for command_name in uv npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Error: %s is required but was not found in PATH.\n' "$command_name" >&2
    exit 1
  fi
done

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
  printf 'Installing web dependencies...\n'
  (cd "$WEB_DIR" && npm install)
fi

printf 'Starting API on http://127.0.0.1:%s...\n' "$API_PORT"
uv run tutorial-ai serve --port "$API_PORT" &
API_PID=$!

cleanup() {
  trap - EXIT INT TERM
  kill "$API_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

printf 'Starting web UI on http://localhost:3100...\n'
cd "$WEB_DIR"
npm run dev