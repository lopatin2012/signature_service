#!/bin/sh
# Скрипт входа контейнера: активация лицензии КриптоПро и импорт ВСЕХ PFX-сертификатов
# из каталога $CERTS_DIR (по умолчанию /certs) в машинное хранилище "Мой" (mMy).
# Имя файла не важно — импортируются все *.pfx из каталога.
# Пароль для файла cert.pfx берётся из соседнего cert.pfx.pw, если он есть,
# иначе используется глобальная переменная CERT_PASSWORD.

set -e

# Инструменты КриптоПро не попадают в PATH в shell-контейнере — добавляем явно.
export PATH="/opt/cprocsp/bin/amd64:/opt/cprocsp/sbin/amd64:$PATH"

# Активация лицензии CSP, если задана переменная CSP_LICENSE.
if [ -n "$CSP_LICENSE" ]; then
    echo "Активация лицензии КриптоПро CSP..."
    cpconfig -license -set "$CSP_LICENSE" || echo "Предупреждение: не удалось активировать лицензию"
fi

# Импорт всех PFX-сертификатов в машинное хранилище "Мой" (mMy).
# pycades открывает CAPICOM_LOCAL_MACHINE_STORE + "My" — импортируем именно туда.
# Флаг -pfx обязателен: без него certmgr не читает PFX-файл.
CERTS_DIR="${CERTS_DIR:-/certs}"
if [ -d "$CERTS_DIR" ]; then
    imported=0
    for f in "$CERTS_DIR"/*.pfx; do
        [ -e "$f" ] || continue
        base="${f%.pfx}"
        pw="$CERT_PASSWORD"
        if [ -f "$base.pw" ]; then
            # Соседний файл <имя>.pfx.pw даёт пароль именно для этого сертификата.
            pw="$(tr -d '\r\n' < "$base.pw")"
        fi
        echo "Импорт $f в хранилище КриптоПро (mMy)..."
        if [ -n "$pw" ]; then
            if certmgr -install -pfx -store mMy -file "$f" -pin "$pw" -silent; then
                imported=$((imported + 1))
            else
                echo "Предупреждение: не удалось импортировать $f (возможно, уже установлен)"
            fi
        else
            if certmgr -install -pfx -store mMy -file "$f" -silent; then
                imported=$((imported + 1))
            else
                echo "Предупреждение: не удалось импортировать $f (возможно, уже установлен)"
            fi
        fi

        # Доверие к самоподписанным сертификатам: если subject == issuer,
        # публичный сертификат добавляется в корневое хранилище mRoot.
        # Иначе IsValid()/SignCades() КриптоПро отвергают цепочку (0x800B0109)
        # — подпись не создаётся. Нужно для тестовых/локальных сертификатов.
        if command -v python3 >/dev/null 2>&1; then
            _root_cer="/tmp/_csp_root_$$.cer"
            if python3 - "$f" "$pw" "$_root_cer" <<'PY'
import sys
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
src, pw, out = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    _key, cert, _cas = pkcs12.load_key_and_certificates(open(src, "rb").read(), pw.encode() if pw else None)
except Exception:
    raise SystemExit(0)
if cert.subject.rfc4514_string() == cert.issuer.rfc4514_string():
    with open(out, "wb") as fh:
        fh.write(cert.public_bytes(Encoding.DER))
else:
    raise SystemExit(0)
PY
            then
                if [ -f "$_root_cer" ]; then
                    if certmgr -install -cert -store mRoot -file "$_root_cer" -silent >/dev/null 2>&1; then
                        echo "Установлен доверенный корень для $f"
                    fi
                    rm -f "$_root_cer"
                fi
            fi
        fi
    done
    echo "Импортировано PFX-сертификатов: $imported"
else
    echo "Каталог $CERTS_DIR не найден — сертификаты не импортируются."
fi

# Запуск основного процесса.
exec "$@"