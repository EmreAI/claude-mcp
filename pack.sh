#!/bin/sh
# Rebuilds dist/<server>.zip for every server folder (local .venv and caches stay out).
set -e
cd "$(dirname "$0")"
mkdir -p dist
for d in */; do
  d=${d%/}
  [ -f "$d/server.py" ] || continue
  rm -f "dist/$d.zip"
  zip -rq -X "dist/$d.zip" "$d" -x '*.DS_Store' "$d/.venv/*" '*__pycache__*' '*.pyc' "$d/.env"
  echo "dist/$d.zip"
done
