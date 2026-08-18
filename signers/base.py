from abc import ABC, abstractmethod
from typing import Optional


class BaseSigner(ABC):
    """Абстрактный базовый класс для подписания данных."""

    @abstractmethod
    def get_list_certificates(self) -> list[dict]:
        """Получить список валидных сертификатов из хранилища."""

    @abstractmethod
    def attached_signed_data(self, data: str, serial_number: str) -> str:
        """Создать прикреплённую подпись для данных."""

    @abstractmethod
    def unpinned_signed_data(self, data: str, serial_number: str) -> str:
        """Создать откреплённую подпись для данных."""
