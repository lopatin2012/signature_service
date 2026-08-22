import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import status as fastapi_status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from signers import get_signer
from signers.base import (
    BaseSigner,
    SigningError,
    SigningErrorCategory,
    classify_signing_error,
    is_retryable,
)

# Логирование.
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


class SignRequest(BaseModel):
    """Запрос на подпись данных."""
    data: str = Field(..., min_length=1, max_length=10_000_000, description="Данные для подписания (max 10MB)")
    serial_number: str = Field(..., min_length=8, max_length=64, description="Серийный номер сертификата")


class SignResponse(BaseModel):
    status: str = "success"
    signed_data: str
    signing_time_ms: Optional[int] = None


class HealthStatus(BaseModel):
    status: str
    signer_available: bool
    certificates_count: int | None = None


class CertificateListResponse(BaseModel):
    certificates: list[dict]
    count: int


# --- Настройки надёжности ---

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
except ImportError:  # pragma: no cover — python-dotenv есть в requirements.txt
    pass

SIGNING_TIMEOUT_SECONDS = int(os.getenv('SIGNING_TIMEOUT_SECONDS', '60'))
MAX_RETRIES = int(os.getenv('MAX_SIGNING_RETRIES', '3'))
BASE_RETRY_DELAY = float(os.getenv('BASE_RETRY_DELAY_SEC', '1.0'))
API_KEY_HEADER_NAME = os.getenv('API_KEY_HEADER_NAME', 'X-API-Key')
ALLOWED_API_KEYS = set(
    os.getenv('API_KEYS', '').split(',') if os.getenv('API_KEYS') else [os.getenv('SERVICE_API_KEY', '')]
)

# Singleton-подписант: создаётся при старте (или подменяется в тестах).
signer: Optional[BaseSigner] = None
signer_available: bool = False


def _auth_configured() -> bool:
    """True, если задан хотя бы один API-ключ (пустая строка не считается)."""
    return bool(ALLOWED_API_KEYS - {''})


api_key_header_scheme = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def api_key_check(api_key_header: Optional[str] = Depends(api_key_header_scheme)) -> dict[str, bool]:
    """Проверка API key для всех эндпоинтов (опционально).

    Если ключи не сконфигурированы — аутентификация не требуется.
    Если сконфигурированы — запрос без валидного ключа отклоняется (401).
    """
    if _auth_configured() and api_key_header not in ALLOWED_API_KEYS:
        raise HTTPException(
            status_code=fastapi_status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий API-ключ.",
        )
    return {'authenticated': api_key_header in ALLOWED_API_KEYS}


def initialize_signer() -> BaseSigner:
    """Инициализирует signer, проверяя доступность криптопровайдера."""
    logger.info("Инициализация подписывающего модуля...")
    try:
        signer_instance = get_signer()
        logger.info(f"✓ Успешная инициализация signer. Платформа: {sys.platform}")
        return signer_instance
    except ImportError as e:
        logger.critical(f"✗ КРИТИЧЕСКАЯ ОШИБКА: провайдер подписи не найден ({e}). "
                        f"Установите CryptoPro CSP / Endesive.")
        raise SystemExit(1) from e
    except Exception as e:
        logger.critical(
            f"✗ Не удалось инициализировать signer: {type(e).__name__}: {e}. "
            f"Проверьте, что криптопровайдер установлен и хранилище сертификатов доступно."
        )
        raise SystemExit(1) from e


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: создаёт singleton-подписанта (если его ещё нет, напр. в тестах)."""
    global signer, signer_available
    logger.info("=" * 60)
    logger.info("Signature Service — startup sequence")
    if signer is None:
        signer = initialize_signer()
    signer_available = signer is not None
    logger.info("=" * 60)
    yield
    logger.warning("=" * 60)
    logger.warning("Shutting down")
    signer_available = False
    logger.warning("=" * 60)


app = FastAPI(title='Signature Service', version='1.2', description='Микросервис CAdES-BES для подписи данных.', lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """422 с понятной подсказкой: если тело не является JSON-объектом, объясняем, что делать.

    Не включаем исходные `input` в ответ — не эхо-им тело запроса клиенту.
    """
    errors = []
    body_not_json_object = False
    for err in exc.errors():
        errors.append({
            "type": err.get("type"),
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg"),
        })
        if err.get("type") == "model_attributes_type" and "body" in err.get("loc", []):
            body_not_json_object = True

    if body_not_json_object:
        errors.insert(0, {
            "type": "json_body_expected",
            "loc": ["body"],
            "msg": (
                "Тело запроса должно быть JSON-объектом, например: "
                '{"data": "текст", "serial_number": "AB12CD34EF56"}. '
                "В Postman: Body → raw → JSON (не form-data)."
            ),
        })

    return JSONResponse(
        status_code=fastapi_status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(errors)},
    )


# === Endpoints ===

@app.get("/")
async def root():
    return JSONResponse({
        "service": "Signature Service",
        "version": "1.2.0",
        "platform": sys.platform,
        "endpoints": {
            "sign_attached": "/api/sign/attached/",
            "sign_unpinned": "/api/sign/unpinned/",
            "list_certificates": "/api/certificates/",
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi_schema": "/openapi.json",
        },
        "health": "/api/health",
    })


@app.get("/api/health", response_model=HealthStatus)
async def health_check(authenticated: dict[str, bool] = Depends(api_key_check)):
    """Health check endpoint (GET)."""
    data = HealthStatus(
        status="healthy" if signer_available else "unavailable",
        signer_available=signer_available,
    )
    if signer_available and signer is not None:
        try:
            data.certificates_count = len(await asyncio.to_thread(signer.get_list_certificates))
        except Exception:
            pass  # сертификаты временно недоступны — это не повод для статуса 503
    return JSONResponse(
        content=data.model_dump(),
        status_code=fastapi_status.HTTP_200_OK if signer_available else fastapi_status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.get("/api/certificates/", response_model=CertificateListResponse)
async def list_certificates(authenticated: dict[str, bool] = Depends(api_key_check)):
    """Получить список валидных сертификатов."""
    if signer is None:
        raise HTTPException(
            status_code=fastapi_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Подписывающий модуль не инициализирован. Проверьте журнал запуска сервиса.",
        )
    try:
        certs = await asyncio.to_thread(signer.get_list_certificates)
    except SigningError as e:
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Не удалось прочитать хранилище сертификатов. "
                "Проверьте, что криптопровайдер установлен и хранилище доступно. "
                f"Ошибка: {e}"
            ),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось получить список сертификатов. Ошибка: {e}",
        ) from e
    logger.info(f"Retrieved {len(certs)} certificates from store.")

    certificates = jsonable_encoder(certs)
    return CertificateListResponse(certificates=certificates, count=len(certificates))


def _is_retryable_signing_error(exc: BaseException) -> bool:
    """Ретрайтим только транзиентные ошибки (классификация из base.py) и сетевые/IO сбои."""
    if isinstance(exc, SigningError):
        return is_retryable(exc.category)
    return isinstance(exc, OSError)


def _log_retry(retry_state) -> None:
    exc = retry_state.outcome.exception()
    logger.warning(
        f"Попытка #{retry_state.attempt_number} подписи не удалась: {type(exc).__name__}: {exc}. "
        f"Повторяем с экспоненциальной паузой."
    )


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=BASE_RETRY_DELAY, min=1.0, max=SIGNING_TIMEOUT_SECONDS * 2),
    retry=retry_if_exception(_is_retryable_signing_error),
    before_sleep=_log_retry,
)
async def _run_with_retries(sign_fn):
    """Выполняет блокирующую операцию подписи в потоке с жёстким таймаутом.

    COM/pycades — блокирующие вызовы; запускаем их в отдельном потоке, чтобы
    asyncio.wait_for действительно мог прервать зависшую операцию.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(sign_fn),
        timeout=SIGNING_TIMEOUT_SECONDS,
    )


# Подсказки, что делать при ошибке, на русском языке (по категории ошибки).
_SIGNING_ERROR_HINTS: dict[SigningErrorCategory, str] = {
    SigningErrorCategory.CERTIFICATE_VALIDATION: (
        "Сертификат не найден или недействителен. Проверьте серийный номер, "
        "убедитесь, что сертификат установлен в хранилище и его срок действия не истёк."
    ),
    SigningErrorCategory.INVALID_DATA: (
        "Переданные данные повреждены или имеют неподдерживаемый формат. Проверьте входные данные."
    ),
    SigningErrorCategory.NETWORK_OR_IO: (
        "Временный сбой сети или хранилища. Попробуйте повторить запрос позже."
    ),
    SigningErrorCategory.TEMPORARY_RESOURCE: (
        "Ресурс временно недоступен. Попробуйте повторить запрос позже."
    ),
    SigningErrorCategory.CONFIGURATION_ERROR: (
        "Ошибка конфигурации криптопровайдера или хранилища. Проверьте настройки сервиса."
    ),
    SigningErrorCategory.SYSTEM_UNAVAILABLE: (
        "Криптопровайдер недоступен или произошёл внутренний сбой. Проверьте журнал сервиса."
    ),
}


def _map_signing_error(exc: Exception) -> HTTPException:
    """Преобразует ошибку подписи в HTTP-ответ с текстом ошибки и подсказкой по-русски.

    Сертификат не найден → 404, все остальные ошибки → 500.
    """
    if isinstance(exc, TimeoutError):
        return HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Операция подписи превысила таймаут ({SIGNING_TIMEOUT_SECONDS} с). "
                f"Попробуйте повторить запрос или увеличьте переменную окружения "
                f"SIGNING_TIMEOUT_SECONDS. Ошибка: {exc}"
            ),
        )

    if isinstance(exc, SigningError):
        category = exc.category
    elif isinstance(exc, ValueError):
        category = SigningErrorCategory.CERTIFICATE_VALIDATION
    else:
        category = classify_signing_error(exc)

    hint = _SIGNING_ERROR_HINTS.get(category, _SIGNING_ERROR_HINTS[SigningErrorCategory.SYSTEM_UNAVAILABLE])
    detail = f"{hint} Ошибка: {exc}"
    status_code = (
        fastapi_status.HTTP_404_NOT_FOUND
        if category == SigningErrorCategory.CERTIFICATE_VALIDATION
        else fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    return HTTPException(status_code=status_code, detail=detail)


async def _sign_and_build_response(request: SignRequest, method: str) -> SignResponse:
    """Общая логика подписи (attached/unpinned): вызов signer'а, маппинг ошибок, тайминг."""
    if signer is None:
        raise HTTPException(
            status_code=fastapi_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Подписывающий модуль не инициализирован. Проверьте журнал запуска сервиса.",
        )

    start = time.perf_counter()
    try:
        result = await _run_with_retries(
            lambda: getattr(signer, method)(request.data.encode("utf-8"), request.serial_number)
        )
    except HTTPException:
        raise
    except Exception as e:
        http_error = _map_signing_error(e)
        if http_error.status_code >= fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error(f"Ошибка подписи: {e}", exc_info=True)
        else:
            logger.warning(f"Ошибка подписи: {e}")
        raise http_error from e

    # Контракт BaseSigner: кортеж (base64-подпись, SigningResult)
    signed_data, signing_result = result
    if not signing_result.success:
        # Signer вернул неуспешный результат вместо исключения (например, в тестах).
        raise _map_signing_error(
            SigningError(
                signing_result.error_message or "Неизвестная ошибка подписи",
                category=signing_result.category or SigningErrorCategory.SYSTEM_UNAVAILABLE,
            )
        )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(f'✓ Подпись создана за {elapsed_ms}ms (сертификат: {request.serial_number})')
    return SignResponse(status="success", signed_data=signed_data, signing_time_ms=elapsed_ms)


@app.post('/api/sign/attached/', response_model=SignResponse)
async def sign_attached(request: SignRequest, authenticated: dict[str, bool] = Depends(api_key_check)):
    """Подписать данные прикреплённой подписью (CAdES-BES with digest attached)."""
    return await _sign_and_build_response(request, "attached_signed_data")


@app.post('/api/sign/unpinned/', response_model=SignResponse)
async def sign_unpinned(request: SignRequest, authenticated: dict[str, bool] = Depends(api_key_check)):
    """Подписать данные откреплённой подписью (CAdES-BES detached)."""
    return await _sign_and_build_response(request, "unpinned_signed_data")


# === Self-test mode ===

def run_selftest() -> bool:
    """Запуск авто-тестов перед стартом сервиса."""
    try:
        import pytest
    except ImportError:
        logger.error('Авто-тесты требуют pytest: pip install pytest httpx')
        return False

    logger.info('=' * 50)
    logger.info("Запуск самопроверки...")
    code = pytest.main(['-q', '-m', 'not cert', '--no-header', 'tests/'])
    if code != 0:
        logger.critical('Самопроверка не пройдена — отказываюсь запускаться.')
        return False
    logger.info('Все самопроверки пройдены.')
    return True


if __name__ == '__main__':
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description='Signature Service CAdES-BES')
    parser.add_argument('--selftest', action='store_true', help='Run tests before startup')
    parser.add_argument('--timeout', type=int, default=None, help='Signing timeout (secs)')
    args, _ = parser.parse_known_args()

    if args.timeout is not None:
        SIGNING_TIMEOUT_SECONDS = args.timeout

    if args.selftest and not run_selftest():
        raise SystemExit(1)

    HOST = os.getenv('SERVICE_HOST', '0.0.0.0')
    PORT = int(os.getenv('SERVICE_PORT', '8101'))
    logger.info(f"Server running on http://{HOST}:{PORT}/")
    uvicorn.run(app=app, host=HOST, port=PORT, log_level='debug')
