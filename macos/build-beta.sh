#!/bin/bash
# Construit la version d'essai, à part de la version stable, sans toucher à /Applications :
#   macos/build-beta.sh
# Résultat : ~/Applications/arXivReader Beta.app et dist/arXivReader-beta.dmg. Autre identifiant d'application, donc
# autres réglages ; bibliothèque dans ~/Library/Application Support/arXivReader Beta, port 8730, pas de boucles de fond.
# Une version « 1.12.0-beta.1 » n'est jamais comparée aux versions publiées : aucune proposition de mise à jour.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
BUILD=$HOME/Library/Caches/arXivReader-beta-build
STABLE=$HOME/Library/Caches/arXivReader-build
DEST=$HOME/Applications/arXivReader\ Beta.app
mkdir -p "$BUILD" "$HOME/Applications"
# Python autonome déjà construit pour la version stable : repris tel quel
[ -x "$BUILD/python/bin/python3" ] || { [ -x "$STABLE/python/bin/python3" ] && ditto "$STABLE/python" "$BUILD/python"; }
ARXIVREADER_BUILD=$BUILD macos/build.sh
rm -f "$ROOT/dist/arXivReader.dmg"  # image portant le nom de la version stable
APP=$BUILD/arXivReader.app
PLIST=$APP/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier local.arxivreader.beta" \
  -c "Set :CFBundleName arXivReader Beta" -c "Set :CFBundleDisplayName arXivReader Beta" "$PLIST"
[ -f "$ROOT/BETA.md" ] && cp "$ROOT/BETA.md" "$APP/Contents/Resources/BETA.md"  # menu Aide → Notes de la bêta (si la bêta en a)
xattr -cr "$APP"
codesign --force --sign - "$APP"
codesign --verify "$APP"
rm -rf "$DEST"
ditto "$APP" "$DEST"
STAGE=$BUILD/dmg
rm -rf "$STAGE"; mkdir -p "$STAGE"
ditto "$APP" "$STAGE/arXivReader Beta.app"
hdiutil create -quiet -volname "arXivReader Beta" -srcfolder "$STAGE" -ov -format UDZO "$ROOT/dist/arXivReader-beta.dmg"
rm -rf "$STAGE"
du -sh "$DEST" "$ROOT/dist/arXivReader-beta.dmg"
echo "Version d'essai : $DEST"
