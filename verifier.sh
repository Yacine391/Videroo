#!/usr/bin/env bash
# Vérifications locales : aucun quota IA consommé.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet -r requirements.txt
exec ./.venv/bin/python -m unittest discover -s tests -v
