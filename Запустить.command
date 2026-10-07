#!/bin/zsh
cd "${0:A:h}" || { print "Не найдена папка проекта."; read "?Нажми Enter, чтобы закрыть окно."; exit 1; }
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source scripts/python.zsh
"$task_python" -m parking_watch.manage start
read "?Нажми Enter, чтобы закрыть окно."
