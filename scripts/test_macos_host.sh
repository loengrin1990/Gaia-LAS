#!/bin/zsh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python="$root/.venv/bin/python3"
[[ -x "$python" ]] || { print -u2 "Gaia native tests require $python (Python 3.11+)."; exit 1; }
TEST_RUNNER_GAIA_REPOSITORY_ROOT="$root" \
TEST_RUNNER_GAIA_PYTHON="$python" \
xcodebuild -project "$root/native/macos/GaiaHost/GaiaHost.xcodeproj" -scheme GaiaHostTests -sdk macosx -destination 'platform=macOS,arch=arm64' test CODE_SIGNING_ALLOWED=NO 2>&1 | tail -30
exit ${pipestatus[1]}
