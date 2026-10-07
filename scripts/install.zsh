#!/bin/zsh
set -eu
cd "${0:A:h:h}"
task_root="$PWD"
task_local="$HOME/Library/Application Support/ParkingWatch"
mkdir -p "$task_local/tools"
export PATH="$task_local/tools:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
task_arch="$(/usr/bin/uname -m)"
case "$task_arch" in
    arm64) task_python="cpython-3.12-macos-aarch64-none" ;;
    x86_64)
        task_macos="$(/usr/bin/sw_vers -productVersion)"
        if (( ${task_macos%%.*} < 11 )); then
            print "Для установки на Intel нужен macOS 11 Big Sur или новее."
            exit 1
        fi
        task_python="cpython-3.12-macos-x86_64-none" ;;
    *) print "Этот тип Mac не поддерживается: $task_arch"; exit 1 ;;
esac
if [[ ! -f config.local.json || ! -f yolo11n-seg.pt ]]; then
    print "Не хватает config.local.json или yolo11n-seg.pt. Перенеси всю папку parking-watch."
    exit 1
fi
task_uv="$(command -v uv || true)"
if [[ -z "$task_uv" ]] || ! "$task_uv" --version >/dev/null 2>&1; then
    print "1/3. Загружаем средство установки Python."
    /usr/bin/curl --fail --location --silent --show-error https://astral.sh/uv/install.sh -o "$task_local/tools/uv-installer.sh"
    UV_UNMANAGED_INSTALL="$task_local/tools" /bin/sh "$task_local/tools/uv-installer.sh"
    task_uv="$task_local/tools/uv"
fi
print "1/3. Проверяем Python и архитектуру Mac."
task_env="$task_local/venvs/$task_arch"
if ! "$task_env/bin/python" -c 'import platform,sys; assert platform.machine()==sys.argv[1]; assert sys.version_info[:2]==(3,12)' "$task_arch" >/dev/null 2>&1; then
    if [[ -e "$task_env" ]]; then
        /bin/mv "$task_env" "$task_env-saved-$(/bin/date +%Y%m%d-%H%M%S)"
    fi
    UV_PYTHON_INSTALL_DIR="$task_local/python" "$task_uv" venv --python "$task_python" "$task_env"
fi
print "2/3. Устанавливаем распознавание и обработку камеры."
if [[ "$task_arch" == "x86_64" ]]; then
    "$task_uv" pip sync --python "$task_env/bin/python" --only-binary :all: requirements-intel.lock
else
    UV_PROJECT_ENVIRONMENT="$task_env" "$task_uv" sync --frozen --python "$task_env/bin/python"
fi
[[ "${1:-}" == "--prepare-only" ]] && exit 0
print "3/3. Подключаем коды доступа и автозапуск."
"$task_env/bin/python" -m parking_watch.install
