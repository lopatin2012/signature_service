# Базовый образ: Python 3.13 (Debian, совместим с .deb-пакетами КриптоПро).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Установка Python-зависимостей.
# pycades (обёртка КриптоПро CAdES) ставится отдельно, т.к. её установка
# требует уже развёрнутого CSP. pywin32/pykcs11 исключаем: это Windows/endesive-мусор.
COPY requirements.txt .
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc g++ libc-dev ca-certificates curl && \
    grep -iv '^pywin32' requirements.txt > requirements.linux.txt && \
    grep -iv '^pykcs11' requirements.linux.txt > requirements.linux2.txt && \
    mv requirements.linux2.txt requirements.linux.txt && \
    pip install --no-cache-dir -r requirements.linux.txt && \
    rm requirements.linux.txt && \
    apt-get purge -y gcc g++ libc-dev && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Установка КриптоПро CSP из локального дистрибутива.
# Дистрибутив скачан с cryptopro.ru и положен в distros/linux-amd64_deb.tgz.
# install.sh требует root и собирает базовый набор пакетов (kc1).
# Дополнительно ставятся пакеты, нужные для сборки pycades из исходников:
# lsb-cprocsp-devel, cprocsp-legacy, cprocsp-pki-cades (по официальной
# инструкции CryptoPro/pycades).
COPY distros/linux-amd64_deb.tgz /tmp/csp.tgz
RUN mkdir -p /tmp/csp && \
    tar -xzf /tmp/csp.tgz -C /tmp/csp && \
    cd /tmp/csp/linux-amd64_deb && \
    ./install.sh kc1 --yes > /tmp/csp-install.log 2>&1 || \
        (echo "Ошибка установки КриптоПро CSP, лог:"; cat /tmp/csp-install.log; exit 1) && \
    apt-get install -y --no-install-recommends \
        ./lsb-cprocsp-devel* \
        ./cprocsp-legacy* \
        ./cprocsp-pki-cades* > /tmp/csp-extra.log 2>&1 || \
        (echo "Ошибка установки доп. пакетов КриптоПро, лог:"; cat /tmp/csp-extra.log; exit 1) && \
    rm -rf /tmp/csp /tmp/csp.tgz /tmp/csp-install.log /tmp/csp-extra.log

# pycades собирается из официального репозитория CryptoPro/pycades:
# PyPI-колесо (KirillOgleznev) несёт собственные изолированные .so и не видит
# системное хранилище КриптоПро. Сборка из исходников использует системный CSP.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git cmake build-essential libboost-all-dev && \
    git clone --depth 1 https://github.com/CryptoPro/pycades.git /tmp/pycades && \
    cd /tmp/pycades && \
    pip install --no-cache-dir . && \
    cd / && \
    rm -rf /tmp/pycades && \
    apt-get purge -y git cmake build-essential && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Код приложения.
COPY main.py enums.py ./
COPY signers ./signers
COPY tests ./tests

# Скрипт входа: импорт PFX в хранилище КриптоПро перед запуском.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8101

# КриптоПро CSP работает с хранилищем /var/opt/cprocsp, запускается от root.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# python main.py --selftest запускает авто-тесты перед стартом сервера
# (main.py сам читает SERVICE_HOST/SERVICE_PORT из окружения).
CMD ["python", "main.py", "--selftest"]

# Проверка живости через корневой эндпоинт.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os, sys; r = urllib.request.urlopen('http://127.0.0.1:%s/' % os.getenv('SERVICE_PORT', '8101'), timeout=3); sys.exit(0 if r.status == 200 else 1)" || exit 1