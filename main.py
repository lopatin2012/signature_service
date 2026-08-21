import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi import status as fastapi_status
from pydantic import BaseModel

from signers import get_signer

# Логирование.
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


class SignRequest(BaseModel):
    data: str
    serial_number: str


class SignResponse(BaseModel):
    signed_data: str
    status: str


# FactAPI.
app = FastAPI(title='Signature Service', description='Микросервис для работы с подписями')

# Инициализация подписывающего модуля (Windows/Linux).
signer = get_signer()


@app.get("/")
async def root():
    return JSONResponse({
        "service": "Signature Service",
        "version": "1.1",
        "platform": "windows" if os.name == "nt" else "linux",
        "endpoints": {
            "sign_attached": "/api/sign/attached (POST)",
            "sign_unpinned": "/api/sign/unpinned (POST)",
            "list_certificates": "/api/certificates (GET)",
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi_schema": "/openapi.json",
        },
    })


@app.get("/api/certificates/")
async def list_certificates():
    """Получить список доступных сертификатов."""
    try:
        certificates = signer.get_list_certificates()
        return {"certificates": certificates, "count": len(certificates)}
    except Exception as e:
        logger.error(f"Ошибка при получении списка сертификатов: {e}")
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при получении списка сертификатов: {str(e)}",
        )


@app.post('/api/sign/attached/', response_model=SignResponse)
async def sign_attached(request: SignRequest):
    """Подписать данные прикреплённой подписью."""
    try:
        logger.info(f'Получен запрос на прикреплённую подпись для серийного номера: {request.serial_number}.')
        signed_data = signer.attached_signed_data(request.data, request.serial_number)
        logger.info('Прикреплённая подпись успешно создана.')
        return SignResponse(signed_data=signed_data, status='success')

    except ValueError as ve:
        logger.error(f'Ошибка валидации при подписании прикреплённой подписью: {ve}')
        raise HTTPException(
            status_code=fastapi_status.HTTP_404_NOT_FOUND,
            detail=f'Ошибка при создании прикреплённой подписи: {str(ve)}',
        )

    except Exception as e:
        logger.error(f'Общая ошибка при подписании прикреплённой подписью: {e}')
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Общая ошибка при подписании прикреплённой подписью: {str(e)}',
        )


@app.post('/api/sign/unpinned/', response_model=SignResponse)
async def sign_unpinned(request: SignRequest):
    """Подписать данные откреплённой подписью."""
    try:
        logger.info(f'Получен запрос на откреплённую подпись для серийника: {request.serial_number}')
        signed_data = signer.unpinned_signed_data(request.data, request.serial_number)
        logger.info('Откреплённая подпись успешно создана.')
        return SignResponse(signed_data=signed_data, status='success')

    except ValueError as ve:
        logger.error(f'Ошибка валидации при подписании откреплённой подписью: {ve}')
        raise HTTPException(
            status_code=fastapi_status.HTTP_404_NOT_FOUND,
            detail=f'Ошибка валидации при подписании откреплённой подписью: {str(ve)}',
        )

    except Exception as e:
        logger.error(f"Общая ошибка при подписании (откреплённая): {e}")
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Ошибка при подписании откреплённой подписью: {str(e)}',
        )


def run_selftest() -> bool:
    """Запуск авто-тестов перед стартом сервиса (`python main.py --selftest`).

    Выполняет быстрые API-тесты (без криптопровайдера). При падении любого теста
    возвращает False, чтобы сервер не стартовал.
    """
    try:
        import pytest
    except ImportError:
        logger.error('Авто-тесты требуют pytest: pip install pytest httpx')
        return False

    logger.info('Запуск авто-тестов (selftest)...')
    code = pytest.main(['-q', '-m', 'not cert', '--no-header', 'tests/'])
    return code == 0


# Точка запуска.
if __name__ == '__main__':
    import sys
    import uvicorn

    if '--selftest' in sys.argv:
        if not run_selftest():
            logger.error('Авто-тесты не пройдены — сервис не запущен.')
            raise SystemExit(1)
        logger.info('Авто-тесты пройдены.')

    HOST = os.getenv('SERVICE_HOST', '0.0.0.0')
    PORT = int(os.getenv('SERVICE_PORT', 8101))
    uvicorn.run(app=app, host=HOST, port=PORT, log_level='debug')
