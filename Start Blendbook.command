#!/bin/bash
# Double-click to run Blendbook from source (the Mac app needs none of this).
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Blendbook (needs internet, ~1 min)…"
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || { echo "Setup failed."; exit 1; }
fi
exec .venv/bin/python app.py
