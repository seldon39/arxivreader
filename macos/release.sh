#!/bin/bash
# Publie une nouvelle version sur GitHub : macos/release.sh [notes.md]
# Avant : augmenter VERSION dans app/version.py, committer. Le script construit l'application, crée
# l'étiquette vX.Y.Z, pousse le dépôt et publie dist/arXivReader.dmg ; les applications installées
# le signalent ensuite (Réglages → Mises à jour).
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' app/version.py)
TAG="v$VERSION"
if [ -n "$(git status --porcelain)" ]; then echo "Des modifications ne sont pas committées."; exit 1; fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then echo "$TAG existe déjà : augmentez VERSION dans app/version.py."; exit 1; fi
ARXIVREADER_NO_ALERTS=1 ARXIVREADER_PORT=8711 .venv/bin/python -m unittest discover -s tests -t . 2>&1 | tail -1
macos/build.sh
git tag -a "$TAG" -m "arXivReader $VERSION"
git push origin HEAD "$TAG"
if [ -n "${1:-}" ]; then NOTES=(--notes-file "$1"); else NOTES=(--generate-notes); fi
gh release create "$TAG" dist/arXivReader.dmg --title "arXivReader $VERSION" "${NOTES[@]}"
