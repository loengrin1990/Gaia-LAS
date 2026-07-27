#!/bin/zsh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
port="18787"
diagnostics_path="$(mktemp /private/tmp/gaia-native-smoke.XXXXXX)"
backend_log="$(mktemp /private/tmp/gaia-native-backend.XXXXXX)"
host_pid=""
backend_pid=""

cleanup() {
  [[ -n "$host_pid" ]] && kill "$host_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$host_pid" ]] && wait "$host_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && wait "$backend_pid" 2>/dev/null || true
  rm -f "$diagnostics_path" "$backend_log"
}
trap cleanup EXIT

zsh "$root/scripts/build_macos_host.sh" >/dev/null
python3 "$root/native/macos/GaiaHost/Tests/synthetic_backend.py" "$port" >"$backend_log" 2>&1 &
backend_pid=$!
for _ in {1..30}; do
  grep -q '^ready$' "$backend_log" && break
  sleep 0.1
done
grep -q '^ready$' "$backend_log"

app="$root/native/macos/build/DerivedData/Build/Products/Debug/Gaia.app/Contents/MacOS/Gaia"
GAIA_NATIVE_HOST_DIAGNOSTICS=1 \
GAIA_NATIVE_HOST_DIAGNOSTICS_PATH="$diagnostics_path" \
GAIA_REPOSITORY_ROOT="$root" \
GAIA_BACKEND_URL="http://127.0.0.1:$port" \
"$app" >/dev/null 2>&1 &
host_pid=$!

for _ in {1..50}; do
  if rg -q '"event":"native_entry_reached"' "$diagnostics_path" &&
     rg -q '"event":"application_did_finish_launching"' "$diagnostics_path" &&
     rg -q '"event":"loading_window_created"' "$diagnostics_path" &&
     rg -q '"event":"backend_coordinator_started"' "$diagnostics_path" &&
     rg -q '"event":"backend_attached"' "$diagnostics_path"; then
    print "Native host launch smoke passed."
    exit 0
  fi
  sleep 0.1
done

print -u2 "Native host launch smoke did not reach the expected lifecycle events."
exit 1
