#!/bin/bash
# Build dist/地道句子本.app. Run from anywhere:  scripts/build_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet -e ".[dev]"

if [ ! -f packaging/AppIcon.icns ]; then
  .venv/bin/python packaging/make_icon.py
fi

.venv/bin/pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/SentenceAgent.spec
echo
echo "Built: $(pwd)/dist/地道句子本.app"
