import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SigningErrorCategory(Enum):
    """Классификация ошибок подписи — определяет возможность ретрайта."""

    NETWORK_OR_IO = "network_or_io"       # временные сбои сети/ввода-вывода — retryable
    TEMPORARY_RESOURCE = "temporary"      # ресурсы временно недоступны — retryable
    CERTIFICATE_VALIDATION = "certificate_validation"  # неверный серийник/невалидный срок — NOT retryable
    INVALID_DATA = "invalid_data"         # повреждённые данные — NOT retryable
    CONFIGURATION_ERROR = "configuration_error"  # ошибка конфигурации — NOT retryable
    SYSTEM_UNAVAILABLE = "system_unavailable"  # провайдер недоступен — NOT retryable


@dataclass(frozen=True)
class SigningResult:
    """Результат одной попытки подписи."""
    success: bool
    signed_data: str | None = None
    error_message: str | None = None
    category: SigningErrorCategory | None = None
    attempt_number: int = 1


class SigningError(Exception):
    """Исключение подписи с категорией для решения о ретрайте.

    Реализации signer'ов бросают именно это исключение при неудачной подписи
    (вместо попытки "поднять" значение enum или dataclass, что Python запрещает).
    Категория позволяет API-слою решить, ретрайтить ли ошибку и какой HTTP-статус вернуть.
    """

    def __init__(
        self,
        message: str,
        category: SigningErrorCategory = SigningErrorCategory.SYSTEM_UNAVAILABLE,
    ) -> None:
        super().__init__(message)
        self.category = category


@dataclass
class CertificateInfo:
    """Информация о сертификате из хранилища."""
    serial_number: str
    subject: str
    issuer: str
    valid_from: str
    valid_to: str
    thumbprint: Optional[str] = None


class BaseSigner(ABC):
    """Абстрактный базовый класс для подписания данных.

    Все реализации должны классифицировать ошибки через SigningErrorCategory,
    чтобы логика ретрайта могла принимать решения на основе типа ошибки,
    а не просто по типу исключения.
    """

    @abstractmethod
    def get_list_certificates(self) -> list[CertificateInfo]:
        """Получить список валидных сертификатов из хранилища.

        Возвращает только те, чей valid_from <= now <= valid_to (с запасом в 24ч).
        """

    @abstractmethod
    def attached_signed_data(
        self, data: bytes, serial_number: str,
        *, signer_params: dict[str, Any] | None = None,
    ) -> tuple[str, SigningResult]:
        """Создать прикреплённую подпись CAdES-BES для данных.

        Args:
            data: Байтовые данные для подписи.
            serial_number: Серийный номер сертификата (8 hex chars, e.g. 'AB12CD34EF56').
            signer_params: Дополнительные параметры алгоритма подписи (hashAlgorithm, digestEncryptionAlgorithm).

        Returns:
            Кортеж: (base64-кодированная подпись, SigningResult с категорией ошибки при неудаче).
        """

    @abstractmethod
    def unpinned_signed_data(
        self, data: bytes, serial_number: str,
        *, signer_params: dict[str, Any] | None = None,
    ) -> tuple[str, SigningResult]:
        """Создать откреплённую (detached) подпись CAdES-BES для данных.

        Возвращает только подпись (без содержимого документа).
        """


def classify_signing_error(exc: Exception) -> SigningErrorCategory:
    """Классифицировать ошибку по её типу и сообщению.

    Используется внутри retry-политики: категории NETWORK_OR_IO и TEMPORARY_RESOURCE
    подлежат ретрайту; остальные — нет.
    """
    error_msg = str(exc).lower()

    if isinstance(exc, OSError):
        return SigningErrorCategory.NETWORK_OR_IO

    if isinstance(exc, PermissionError):
        return SigningErrorCategory.SYSTEM_UNAVAILABLE

    # Типовые сообщения CAdESCOM / pycades (на русском и английском)
    if (
        "cannot find" in error_msg
        or "not found" in error_msg
        or "serial number" in error_msg
        or "не найден" in error_msg
        or "серийн" in error_msg
    ):
        return SigningErrorCategory.CERTIFICATE_VALIDATION

    if "already signed" in error_msg or "certificate already used" in error_msg:
        return SigningErrorCategory.SYSTEM_UNAVAILABLE

    if "invalid data" in error_msg or "malformed" in error_msg:
        return SigningErrorCategory.INVALID_DATA

    if "configuration" in error_msg or "key not available" in error_msg:
        return SigningErrorCategory.CONFIGURATION_ERROR

    # Неизвестная ошибка — считаем её системной и не ретрайтим.
    return SigningErrorCategory.SYSTEM_UNAVAILABLE


def is_retryable(category: SigningErrorCategory) -> bool:
    """Определяет, следует ли ретрайтить ошибку."""
    return category in (SigningErrorCategory.NETWORK_OR_IO, SigningErrorCategory.TEMPORARY_RESOURCE)
