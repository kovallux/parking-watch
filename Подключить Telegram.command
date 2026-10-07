#!/bin/zsh
cd "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source scripts/python.zsh
"$task_python" -m parking_watch.telegram_setup
if [[ $? -ne 0 ]]; then
    read "?Нажми Enter, чтобы закрыть окно. "
fi
