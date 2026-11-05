#!/bin/zsh
set -e
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  /opt/homebrew/bin/uv sync --frozen
fi
exec .venv/bin/python scripts/launch.py
