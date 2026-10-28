#!/bin/sh
# One-step setup on macOS or Linux: installs the server and gets your Moodle token.
#   sh setup.sh https://moodle.my-university.edu
set -e
cd "$(dirname "$0")"

URL="$1"
if [ -z "$URL" ]; then
  printf "Your Moodle address (e.g. https://moodle.my-university.edu): "
  read -r URL
fi

PY=""
for p in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$p"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed. Get it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

echo "Installing with $($PY --version) …"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "Installed."
echo

.venv/bin/python get_token.py "$URL"
