# Signature Service

Микросервис для создания электронных подписей (CAdES-BES) через FastAPI. Кроссплатформенный: работает на Windows (win32com/CAdESCOM) и Linux (pycades/КриптоПро CSP).

## Запуск

```bash
python main.py
```

По умолчанию сервер слушает `0.0.0.0:8101`. Порт и хост настриваются через `.env`:

```
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8101
```

Документация API доступна по адресу `/docs`.

## API

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Информация о сервисе |
| `GET` | `/api/certificates/` | Список доступных сертификатов |
| `POST` | `/api/sign/attached/` | Прикреплённая подпись |
| `POST` | `/api/sign/unpinned/` | Откреплённая подпись |

### Пример запроса на подпись

```json
POST /api/sign/attached/
{
  "data": "данные для подписания",
  "serial_number": "AB12CD34EF56"
}
```

### Ответ

```json
{
  "signed_data": "MII...(base64 подпись)",
  "status": "success"
}
```

## Конфигурация

### Windows

Сертификаты берутся из хранилища Windows текущего пользователя (`Cert:\CurrentUser\My`). Сертификат должен быть установлен и валиден. Поиск по серийному номеру.

### Linux

На Linux используется КриптоПро CSP и библиотека pycades. PFX-сертификат
импортируется в машинное хранилище КриптоПро при старте контейнера. Задайте
переменные окружения:

```bash
export CERT_PATH="/path/to/certificate.pfx"
export CERT_PASSWORD="your_password"
```

Для Docker-развёртывания: положите `cert.pfx` в папку `certs/`, задайте пароль и
(при наличии) ключ лицензии КриптоПро в `.env`, затем:

```bash
docker compose up -d --build
```

## Установка зависимостей

```bash
pip install -r requirements.txt
```

## Тестирование

Зависимости для тестов (`pytest`, `httpx`) включены в `requirements.txt`.

### Быстрые API-тесты (не требуют криптопровайдера)

Проверяют маршрутизацию, маппинг ошибок и формат ответов, подменяя реальный
`signer` на фейк.

```bash
python -m pytest tests/test_api.py -v
```

### Интеграционные тесты сертификата (требуют криптопровайдер)

Проверяют реального подписанта (CAdESCOM на Windows / pycades+КриптоПро на
Linux) и наличие валидного сертификата в хранилище. По умолчанию пропускаются;
запускаются только с маркером `cert`:

```bash
python -m pytest tests/test_certificate.py -m cert -v
```

### Авто-тесты при запуске сервиса

Сервис умеет запускать быстрые тесты перед стартом и **не стартует**, если
хотя бы один тест не пройден:

```bash
python main.py --selftest
```

В Docker авто-тесты запускаются автоматически при подъёме контейнера
(`docker compose up` запускает `python main.py --selftest`).

## Структура проекта

```
main.py              — FastAPI приложение, маршруты, selftest
enums.py             — константы CAdESCOM/CAPICOM
signers/
  __init__.py        — фабрика: выбор signer по ОС
  base.py            — абстрактный базовый класс
  windows.py         — реализация для Windows
  linux.py           — реализация для Linux
tests/
  conftest.py        — регистрация маркера `cert`
  test_api.py        — быстрые API-тесты (с фейк signer'ом)
  test_certificate.py— интеграционные тесты сертификата (`-m cert`)
requirements.txt     — зависимости (вкл. pytest, httpx)
.env                 — переменные окружения (для docker compose)
```

## Как это работает

При старте `signers/__init__.py` определяет ОС и создаёт экземпляр `WindowsSigner` или `LinuxSigner`. Все запросы на подпись обрабатываются через единый интерфейс `BaseSigner`.

На **Windows** каждая операция подписания выполняется в COM-контексте (`CoInitialize`/`CoUninitialize`). Сертификат ищется в хранилище Windows по серийному номеру.

На **Linux** используется КриптоПро CSP: сертификат с приватным ключом
импортируется в машинное хранилище (`certmgr -install -pfx -store mMy`), подпись
создаётся через pycades.
