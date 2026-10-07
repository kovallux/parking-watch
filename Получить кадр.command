#!/bin/zsh
cd "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
source scripts/python.zsh
print "Получаем свежий кадр верхней камеры…"
if "$task_python" -m parking_watch.cli --config config.local.json capture --output data/camera-latest.jpg; then
    open data/camera-latest.jpg
    print "Готово. Кадр сохранён в папке data проекта."
else
    print "Кадр не получен. Проверь домашний Wi-Fi, питание камеры и доступ к Связке ключей."
fi
read "?Нажми Enter, чтобы закрыть окно. "
