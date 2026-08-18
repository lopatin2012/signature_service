import os
import logging
import base64
import json
from datetime import datetime

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat import backends
from endesive import plain
from endesive import signer as endesive_signer

from signers.base import BaseSigner

logger = logging.getLogger(__name__)

# Пути к сертификату и ключу из переменных окружения.
# На Linux сертификаты хранятся в PKCS#12 (.p12/.pfx) файле.
CERT_PATH = os.getenv("CERT_PATH", "")
CERT_PASSWORD = os.getenv("CERT_PASSWORD", "")


def _load_p12():
    """Загрузить сертификат и ключ из PKCS#12 файла."""
    if not CERT_PATH:
        raise ValueError(
            "Переменная CERT_PATH не задана. "
            "Укажите путь к PKCS#12 файлу (.p12/.pfx) в переменной окружения."
        )
    if not os.path.isfile(CERT_PATH):
        raise FileNotFoundError(f"Файл сертификата не найден: {CERT_PATH}")

    password = CERT_PASSWORD.encode() if CERT_PASSWORD else None
    with open(CERT_PATH, "rb") as fp:
        key, cert, othercerts = pkcs12.load_key_and_certificates(
            fp.read(), password, backends.default_backend()
        )
    return key, cert, othercerts or []


class LinuxSigner(BaseSigner):
    """Реализация подписания через endesive (Linux)."""

    def get_list_certificates(self) -> list[dict]:
        try:
            _key, cert, _othercerts = _load_p12()
            subject = cert.subject
            # Извлекаем CN (ФИО) из subject.
            fio = ""
            inn = "000000000000"
            for attr in subject:
                if attr.oid.dotted_string == "2.5.4.3":  # CN
                    fio = attr.value
                # endesive не парсит кириллические OID (ИНН),
                # но если сертификат содержит custom extension — можно добавить позже.

            serial_number = format(cert.serial_number, "X")
            valid_from = cert.not_valid_before_utc.strftime("%d-%m-%Y")
            valid_for = cert.not_valid_after_utc.strftime("%d-%m-%Y")
            valid_days = (cert.not_valid_after_utc - datetime.now().astimezone()).days

            if valid_days < 1:
                logger.warning("Сертификат истёк или истекает сегодня.")
                return []

            return [{
                "serial_number": serial_number,
                "fio": fio,
                "inn": inn,
                "valid_from": valid_from,
                "valid_for": valid_for,
                "valid_days": valid_days,
            }]

        except Exception as e:
            logger.error(f"Ошибка при чтении сертификата: {e}", exc_info=True)
            return []

    def attached_signed_data(self, data: str, serial_number: str) -> str:
        try:
            key, cert, othercerts = _load_p12()

            data_bytes = base64.b64encode(data.encode("utf-8"))
            signed_value = endesive_signer.sign(
                data_bytes, key, cert, othercerts,
                "sha256", attrs=True,
            )
            sSignedData = base64.b64encode(signed_value).decode("ascii")
            sSignedData = sSignedData.replace("\r", "").replace("\n", "")
            return sSignedData

        except Exception as e:
            logger.error(f"Ошибка при создании прикреплённой подписи: {e}", exc_info=True)
            raise

    def unpinned_signed_data(self, data: str, serial_number: str) -> str:
        try:
            key, cert, othercerts = _load_p12()

            message = str(data).replace(" ", "\u0020").replace("\n", "").replace("\r", "")
            message_bytes = message.encode()
            data_bytes = base64.b64encode(message_bytes)

            signed_value = endesive_signer.sign(
                data_bytes, key, cert, othercerts,
                "sha256", attrs=True,
            )
            sSignedData = base64.b64encode(signed_value).decode("ascii")
            sSignedData = sSignedData.replace("\r", "").replace("\n", "")
            return sSignedData

        except Exception as e:
            logger.error(f"Ошибка при создании откреплённой подписи: {e}", exc_info=True)
            raise
