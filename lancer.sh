#!/usr/bin/env bash
# Lance Videroo en local. Cree l'environnement et installe les dependances au besoin.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ Création de l'environnement Python…"
  python3 -m venv .venv
fi

if [ ! -f .venv/.deps_ok ] || [ requirements.txt -nt .venv/.deps_ok ]; then
  echo "→ Installation des dépendances…"
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  touch .venv/.deps_ok
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠ ffmpeg est absent. Installe-le : sudo apt install ffmpeg"
fi

PORT="${VIDEROO_PORT:-8077}"
echo "→ Videroo démarre sur http://127.0.0.1:${PORT}"
exec ./.venv/bin/python -m uvicorn videroo.app:app --host 127.0.0.1 --port "${PORT}" "$@"
