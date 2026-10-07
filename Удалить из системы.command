#!/bin/zsh
cd "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source scripts/python.zsh
if [[ ! -x "$task_python" ]]; then
    print "Не найдена среда установленного сервиса. Запусти этот файл на Mac, где работает Parking Watch, до переноса папки."
else
    "$task_python" -m parking_watch.manage uninstall
fi
read "?Нажми Enter, чтобы закрыть окно. "
