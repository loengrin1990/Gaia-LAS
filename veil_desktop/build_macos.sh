#!/bin/zsh
set -euo pipefail

root="${0:A:h}"
python_bin="${VEIL_PYTHON:-python3.11}"
cd "$root"
export PYINSTALLER_CONFIG_DIR="$root/.build-cache"
"$python_bin" -m venv .build-venv
.build-venv/bin/python -m pip install -r requirements-build.txt
.build-venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed --name Veil app.py
arch="$(uname -m)"
case "$arch" in
  arm64) label="Apple-Silicon" ;;
  x86_64) label="Intel" ;;
  *) echo "Неизвестная архитектура macOS: $arch" >&2; exit 2 ;;
esac
package="dist/Veil-macOS-$label"
mkdir -p "$package"
ditto dist/Veil.app "$package/Veil.app"
cp README.md "$package/Инструкция.md"
ditto -c -k --keepParent "$package" "dist/Veil-macOS-$label.zip"
echo "Готово: $root/dist/Veil-macOS-$label.zip"
