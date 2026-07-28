#!/bin/zsh
set -u

source_path="$0"
while [[ -h "$source_path" ]]; do
  source_dir="$(cd -P "$(dirname "$source_path")" && pwd)"
  source_path="$(readlink "$source_path")"
  [[ "$source_path" != /* ]] && source_path="$source_dir/$source_path"
done
repository_root="$(cd -P "$(dirname "$source_path")" && pwd)"
log_dir="$HOME/Library/Logs"
log_file="$log_dir/GaiaLauncher.log"
app_path="$repository_root/native/macos/build/DerivedData/Build/Products/Debug/Gaia.app"

mkdir -p "$log_dir"
exec >>"$log_file" 2>&1
print -- "$(date '+%Y-%m-%d %H:%M:%S') launcher_started"

show_error() {
  print -u2 -- "$(date '+%Y-%m-%d %H:%M:%S') launcher_error=$1"
  /usr/bin/osascript -e "display alert \"Gaia не запущена\" message \"$2\" as critical" >/dev/null 2>&1 || true
}

if [[ ! -d "$app_path" ]]; then
  print -- "$(date '+%Y-%m-%d %H:%M:%S') native_app_missing_build_started"
  if ! /bin/zsh "$repository_root/scripts/build_macos_host.sh"; then
    show_error "native_build_failed" "Не удалось собрать Gaia.app. Откройте Терминал и выполните: zsh scripts/build_macos_host.sh"
    exit 1
  fi
fi

if [[ ! -d "$app_path" ]]; then
  show_error "native_app_missing_after_build" "Gaia.app не найдена после сборки. Откройте Терминал и выполните: zsh scripts/build_macos_host.sh"
  exit 1
fi

if ! /usr/bin/open "$app_path"; then
  show_error "native_app_open_failed" "Не удалось открыть Gaia.app. Повторите запуск или пересоберите приложение командой: zsh scripts/build_macos_host.sh"
  exit 1
fi

print -- "$(date '+%Y-%m-%d %H:%M:%S') native_app_open_requested"
