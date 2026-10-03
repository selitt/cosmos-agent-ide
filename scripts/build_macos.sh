#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$PROJECT_DIR/dist/Cosmos Agent IDE.app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
SDK_PATH="${COSMOS_SDK_PATH:-$(xcrun --show-sdk-path)}"
xcrun clang -fobjc-arc -fblocks -isysroot "$SDK_PATH" -mmacosx-version-min=12.0 "$PROJECT_DIR/macos/Cosmos.m" -o "$APP_DIR/Contents/MacOS/Cosmos" -framework Cocoa -framework WebKit
cp "$PROJECT_DIR/server.py" "$APP_DIR/Contents/Resources/server.py"
cp -R "$PROJECT_DIR/web" "$APP_DIR/Contents/Resources/"
cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Cosmos Agent IDE</string>
<key>CFBundleDisplayName</key><string>Cosmos Agent IDE</string>
<key>CFBundleIdentifier</key><string>dev.cosmos.agentide</string>
<key>CFBundleVersion</key><string>3</string>
<key>CFBundleShortVersionString</key><string>0.1.2</string>
<key>CFBundleExecutable</key><string>Cosmos</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSMinimumSystemVersion</key><string>12.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
codesign --force --deep --sign - "$APP_DIR"
ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$PROJECT_DIR/dist/Cosmos-Agent-IDE-macOS.zip"
echo "Built: $APP_DIR"
