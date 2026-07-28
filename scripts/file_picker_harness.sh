#!/bin/zsh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
port="18788"
diagnostics_path="$(mktemp /private/tmp/gaia-file-picker-harness.XXXXXX)"
backend_log="$(mktemp /private/tmp/gaia-file-picker-backend.XXXXXX)"
host_pid=""
backend_pid=""

cleanup() {
  [[ -n "$host_pid" ]] && kill "$host_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$host_pid" ]] && wait "$host_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && wait "$backend_pid" 2>/dev/null || true
  print "Диагностика сохранена до завершения команды: $diagnostics_path"
}
trap cleanup EXIT INT TERM

zsh "$root/scripts/build_macos_host.sh" >/dev/null
python3 "$root/native/macos/GaiaHost/Tests/synthetic_backend.py" "$port" --file-picker-harness >"$backend_log" 2>&1 &
backend_pid=$!
for _ in {1..30}; do
  grep -q '^ready$' "$backend_log" && break
  sleep 0.1
done
grep -q '^ready$' "$backend_log"

app="$root/native/macos/build/DerivedData/Build/Products/Debug/Gaia.app/Contents/MacOS/Gaia"
GAIA_NATIVE_HOST_DIAGNOSTICS=1 GAIA_NATIVE_HOST_DIAGNOSTICS_PATH="$diagnostics_path" GAIA_REPOSITORY_ROOT="$root" GAIA_BACKEND_URL="http://127.0.0.1:$port" "$app" >/dev/null 2>&1 &
host_pid=$!
print "В открывшемся окне выберите только синтетический файл. Затем проверьте JSONL: selected_url_count=1, completion_call_count=1 и file_count=1. Нажмите Ctrl-C для завершения."
wait "$host_pid"
