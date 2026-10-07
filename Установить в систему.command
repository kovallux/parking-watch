#!/bin/zsh
cd "${0:A:h}" || exit 1
export PATH="$PWD/.tools:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
print "Установка Parking Watch на этот Mac. Первый запуск: 3–10 минут."
if /bin/zsh scripts/install.zsh; then
    print "Открой Telegram и отправь /check."
else
    print "Установка не завершена. Сообщение выше указывает, на каком этапе она остановилась."
fi
read "?Нажми Enter, чтобы закрыть окно. "
