#!/bin/bash
# Construit l'application Mac et son image disque dist/arXivReader.dmg (Mac Apple Silicon).
#   macos/build.sh
# L'application embarque son Python (conda-forge, déplaçable : bibliothèques en chemins relatifs), les
# paquets de .venv (même version de Python) et le code d'app/ et static/. Rien à installer pour s'en servir.
# Prérequis pour construire : outils en ligne de commande Xcode (swiftc), miniforge (conda), .venv à jour.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
# Construction hors de Documents : synchronisé par iCloud, il ajoute aux fichiers des attributs qui
# empêchent de signer l'application (et téléverserait des centaines de Mo)
BUILD=${ARXIVREADER_BUILD:-$HOME/Library/Caches/arXivReader-build}
DIST=$ROOT/dist
APP=$BUILD/arXivReader.app
RES=$APP/Contents/Resources
CONDA=${CONDA:-$HOME/miniforge3/bin/conda}
PYVER=$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PYFULL=$(.venv/bin/python -c 'import platform; print(platform.python_version())')  # même Python que .venv
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' app/version.py)  # app/version.py
BUILD_NUMBER=$(git rev-list --count HEAD 2>/dev/null || echo 1)

rm -rf "$APP" "$DIST/arXivReader.dmg"
mkdir -p "$APP/Contents/MacOS" "$RES" "$BUILD" "$DIST"

# 1. Python autonome, créé une fois depuis le cache de conda (sans réseau)
if [ ! -x "$BUILD/python/bin/python3" ]; then
  echo "Python $PYVER (conda-forge)…"
  "$CONDA" create --offline --yes --quiet --override-channels -c conda-forge -p "$BUILD/python" "python=$PYFULL" >/dev/null
fi
echo "Copie de Python…"
rsync -a --delete \
  --exclude conda-meta --exclude include --exclude share --exclude 'lib/pkgconfig' --exclude '*.a' \
  --exclude "lib/python$PYVER/test" --exclude "lib/python$PYVER/idlelib" --exclude "lib/python$PYVER/ensurepip" \
  --exclude "lib/python$PYVER/tkinter" --exclude "lib/python$PYVER/turtledemo" --exclude 'lib/tcl*' --exclude 'lib/tk*' \
  --exclude 'lib/libtcl*' --exclude 'lib/libtk*' --exclude __pycache__ \
  "$BUILD/python/" "$RES/python/"
find "$RES/python" -type l ! -exec test -e {} \; -delete  # liens vers les dossiers écartés (share/terminfo…)

# 2. Paquets de .venv (sans pip)
SITE=lib/python$PYVER/site-packages
rsync -a --exclude 'pip' --exclude 'pip-*' --exclude __pycache__ ".venv/$SITE/" "$RES/python/$SITE/"

# 3. Code de l'application
mkdir -p "$RES/arxivreader"
rsync -a --exclude __pycache__ app static "$RES/arxivreader/"

# Python et l'application précompilés : sans cela, tout est recompilé à chaque lancement (0,6 s de plus).
# « unchecked-hash » : les .pyc servent tels quels, quelles que soient les dates des fichiers copiés
"$RES/python/bin/python3" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$RES/python/lib" "$RES/arxivreader/app" >/dev/null

# 4. Programme de l'application (fenêtre WebKit)
echo "Compilation…"
swiftc -O -target arm64-apple-macos13.0 -o "$APP/Contents/MacOS/arXivReader" macos/main.swift

# 5. Icône
ICONSET=$BUILD/arXivReader.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
swift macos/icon.swift "$BUILD/icon.png"
for size in 16 32 128 256 512; do
  sips -z $size $size "$BUILD/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$BUILD/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/arXivReader.icns"

# 6. Description de l'application
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>arXivReader</string>
  <key>CFBundleDisplayName</key><string>arXivReader</string>
  <key>CFBundleIdentifier</key><string>local.arxivreader</string>
  <key>CFBundleExecutable</key><string>arXivReader</string>
  <key>CFBundleIconFile</key><string>arXivReader</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_NUMBER</string>
  <key>CFBundleDevelopmentRegion</key><string>fr</string>
  <key>CFBundleLocalizations</key><array><string>fr</string><string>en</string></array>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Usage personnel.</string>
</dict></plist>
PLIST

# 7. Signature locale (sans compte développeur Apple) : suffit sur ce Mac ; ailleurs, voir le README
xattr -cr "$APP"  # attributs étendus (provenance…) refusés par codesign
codesign --force --sign - "$APP"

# 8. Image disque à distribuer : l'application et un raccourci vers Applications
STAGE=$BUILD/dmg
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname arXivReader -srcfolder "$STAGE" -ov -format UDZO "$DIST/arXivReader.dmg"
rm -rf "$STAGE"

codesign --verify "$APP"
du -sh "$APP" "$DIST/arXivReader.dmg"
echo "Application : $APP"
