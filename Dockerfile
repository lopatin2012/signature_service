# Базовый образ: Python 3.13 (та же версия, что в .venv проекта).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Зависимости ставим без pywin32: пакет существует только для Windows
# и ломает установку на Linux. В контейнере всегда работает LinuxSigner
# (endesive), которому pywin32 не нужен (см. signers/__init__.py).
COPY requirements.txt .
RUN grep -iv '^pywin32' requirements.txt > requirements.linux.txt && \
    pip install --no-cache-dir -r requirements.linux.txt && \
    rm requirements.linux.txt

# Код приложения.
COPY main.py enums.py ./
COPY signers ./signers

# Запуск от непривилегированного пользователя.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8101

# python main.py сам читает SERVICE_HOST/SERVICE_PORT из окружения (main.py:119-120).
CMD ["python", "main.py"]

# Проверка живости через корневой эндпоинт.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os, sys; r = urllib.request.urlopen('http://127.0.0.1:%s/' % os.getenv('SERVICE_PORT', '8101'), timeout=3); sys.exit(0 if r.status == 200 else 1)" || exit 1
