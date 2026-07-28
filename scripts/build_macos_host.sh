#!/bin/zsh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
project="$root/native/macos/GaiaHost/GaiaHost.xcodeproj"
build_root="$root/native/macos/build"

if ! xcodebuild -version >/dev/null 2>&1; then
  print -u2 "Не найдена полная Xcode. Установите Xcode и выберите её через xcode-select."
  exit 2
fi

xcodebuild -project "$project" -scheme GaiaHost -configuration Debug -derivedDataPath "$build_root/DerivedData" build
app="$build_root/DerivedData/Build/Products/Debug/Gaia.app"
if [[ ! -d "$app" ]]; then
  print -u2 "Сборка завершилась без Gaia.app."
  exit 2
fi
print "Готово: $app"
if [[ "${1:-}" == "--open" ]]; then
  open "$app"
fi
