#!/bin/sh
# Скрипт входа контейнера: активация лицензии КриптоПро и импорт сертификата
# (PFX с приватным ключом) в личное хранилище КриптоПро перед запуском сервиса.

set -e

# Инструменты КриптоПро не попадают в PATH в shell-контейнере — добавляем явно.
export PATH="/opt/cprocsp/bin/amd64:/opt/cprocsp/sbin/amd64:$PATH"

# Активация лицензии CSP, если задана переменная CSP_LICENSE.
if [ -n "$CSP_LICENSE" ]; then
    echo "Активация лицензии КриптоПро CSP..."
    cpconfig -license -set "$CSP_LICENSE" || echo "Предупреждение: не удалось активировать лицензию"
fi

# Импорт сертификата PFX в машинное хранилище "Мой" (mMy), если задан CERT_PATH.
# pycades открывает CAPICOM_LOCAL_MACHINE_STORE + "My" — импортируем именно туда.
# Флаг -pfx обязателен: без него certmgr не читает PFX-файл.
if [ -n "$CERT_PATH" ] && [ -f "$CERT_PATH" ]; then
    echo "Импорт сертификата $CERT_PATH в хранилище КриптоПро (mMy)..."
    if [ -n "$CERT_PASSWORD" ]; then
        certmgr -install -pfx -store mMy -file "$CERT_PATH" -pin "$CERT_PASSWORD" -silent \
            || echo "Предупреждение: не удалось импортировать сертификат (возможно, уже установлен)"
    else
        certmgr -install -pfx -store mMy -file "$CERT_PATH" -silent \
            || echo "Предупреждение: не удалось импортировать сертификат (возможно, уже установлен)"
    fi
else
    echo "CERT_PATH не задан или файл не найден — сертификат не импортируется."
fi

# Запуск основного процесса.
exec "$@"