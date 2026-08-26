# Подписант для Linux: pycades + КриптоПро CSP (машинное хранилище mMy).
#
# ВАЖНО: официальный wheel pycades не работает с системным CSP — ставьте из исходников:
#   git clone https://github.com/CryptoPro/pycades.git && cd pycades && pip install .

import base64
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from enums import SignatureEnum
from signers.base import (
    BaseSigner,
    CertificateInfo,
    SigningError,
    SigningErrorCategory,
    SigningResult,
    classify_signing_error,
)

logger = logging.getLogger(__name__)

try:
    import pycades
except ImportError:  # pragma: no cover — зависит от окружения
    pycades = None


def csp_available() -> bool:
    """Проверяет, доступен ли КриптоПро CSP."""
    if pycades is None:
        return False
    try:
        oStore = pycades.Store()
        oStore.Open(
            SignatureEnum.CAPICOM_LOCAL_MACHINE_STORE.value,
            SignatureEnum.CAPICOM_MY_STORE.value,
            SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
        )
        count = oStore.Certificates.Count
        oStore.Close()
        logger.debug(f"[LinuxSigner] CSP OK — {count} certs in mMy.")
        return True
    except SigningError:
        raise
    except Exception as e:
        raise SigningError(str(e), category=SigningErrorCategory.SYSTEM_UNAVAILABLE) from e


class LinuxSigner(BaseSigner):
    """Подпись на Linux через pycades + КриптоПро CSP."""

    def __init__(self) -> None:
        if pycades is None:
            raise ImportError(
                "pycades not installed. Run: pip install git+https://github.com/CryptoPro/pycades.git@master"
            )
        self._pfx_path: Optional[Path] = None

    @property
    def pfx_path(self) -> Path:
        path_str = os.getenv("CERT_PATH", "/certs/cert.pfx")
        return Path(path_str).expanduser().resolve()

    @staticmethod
    def _open_store():
        """Открывает машинное хранилище (mMy) сертификатов КриптоПро."""
        oStore = pycades.Store()
        try:
            oStore.Open(
                SignatureEnum.CAPICOM_LOCAL_MACHINE_STORE.value,
                SignatureEnum.CAPICOM_MY_STORE.value,
                SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
            )
        except SigningError:
            raise
        except Exception as e:
            raise SigningError(str(e), category=SigningErrorCategory.SYSTEM_UNAVAILABLE) from e
        return oStore

    def get_list_certificates(self) -> list[CertificateInfo]:
        certs: list[CertificateInfo] = []
        oStore = self._open_store()
        try:
            for i in range(oStore.Certificates.Count):
                cert = oStore.Certificates.Item(i + 1)
                subject_parts = str(cert.SubjectName).split(", ")
                fio = subject_parts[0][3:] if subject_parts and subject_parts[0].startswith("CN=") else str(cert.SubjectName)
                valid_to_str = datetime.strptime(str(cert.ValidToDate), "%d.%m.%Y %H:%M:%S").strftime("%d-%m-%Y")
                days_valid = (datetime.strptime(valid_to_str, "%d-%m-%Y") - datetime.now()).days
                if days_valid >= 1 and cert.IsValid().Result:
                    certs.append(CertificateInfo(
                        serial_number=str(cert.SerialNumber),
                        subject=fio, issuer=str(cert.IssuerName),
                        valid_from=datetime.strptime(str(cert.ValidFromDate), "%d.%m.%Y %H:%M:%S").strftime("%d-%m-%Y"),
                        valid_to=valid_to_str, thumbprint=None,
                    ))
            logger.info(f"[LinuxSigner] Found {len(certs)} valid certificates.")
        except SigningError:
            raise
        finally:
            oStore.Close()
        return certs

    def attached_signed_data(self, data: bytes, serial_number: str) -> tuple[str, SigningResult]:
        """CAdES-BES прикреплённая подпись (содержимое внутри подписи)."""
        try:
            return self._do_sign(data, serial_number, detached=False)
        except SigningError:
            raise
        except ValueError:
            # «Сертификат не найден» — ошибка входных данных; пробрасываем как есть,
            # чтобы выше она отобразилась как 404 (см. main._map_signing_error).
            raise
        except Exception as e:
            category = classify_signing_error(e)
            logger.error(f"[LinuxSigner] Неудачная попытка подписи: {e}", exc_info=True)
            raise SigningError(str(e), category=category) from e

    def unpinned_signed_data(self, data: bytes, serial_number: str) -> tuple[str, SigningResult]:
        """CAdES-BES откреплённая подпись (без содержимого документа)."""
        try:
            return self._do_sign(data, serial_number, detached=True)
        except SigningError:
            raise
        except ValueError:
            raise
        except Exception as e:
            category = classify_signing_error(e)
            logger.error(f"[LinuxSigner] Неудачная попытка подписи: {e}", exc_info=True)
            raise SigningError(str(e), category=category) from e

    def _do_sign(self, data: bytes, serial_number: str, detached: bool) -> tuple[str, SigningResult]:
        """Находит сертификат и создаёт подпись CAdES-BES в рамках одного открытого хранилища."""
        oStore = self._open_store()
        try:
            target_serial = serial_number.replace("-", "").lower()
            oCert = None
            # ВАЖНО: pycades.Certificates — не итерируемая коллекция,
            # перебор только через .Count / .Item(i + 1).
            for i in range(oStore.Certificates.Count):
                cert = oStore.Certificates.Item(i + 1)
                if str(cert.SerialNumber).replace("-", "").lower() == target_serial:
                    oCert = cert
                    break
            if oCert is None:
                # Сертификат не найден — это ошибка входных данных, ретрайт бессмыслен.
                raise ValueError(
                    f"Сертификат с серийным номером '{serial_number}' не найден. "
                    f"Убедитесь, что сертификат установлен в машинное хранилище mMy и валиден."
                )

            oSigner = pycades.Signer()
            oSigner.Certificate = oCert
            oSignedData = pycades.SignedData()
            oSignedData.ContentEncoding = pycades.CADESCOM_BASE64_TO_BINARY
            oSignedData.Content = base64.b64encode(data).decode("ascii")
            result_bytes = oSignedData.SignCades(
                oSigner, pycades.CADESCOM_CADES_BES, bool(detached), pycades.CADESCOM_ENCODE_BASE64
            )
            # ВАЖНО: SignCades с CADESCOM_ENCODE_BASE64 возвращает base64-СТРОКУ
            # с переносами (CRLF), а не сырые байты. Контракт ожидает однострочный
            # base64 — убираем переводы строк и повторное кодирование.
            signed_b64 = "".join(str(result_bytes).split())
            return signed_b64, SigningResult(success=True)
        finally:
            oStore.Close()


def get_signer() -> BaseSigner:
    """Фабрика: возвращает LinuxSigner на Linux."""
    logger.info("[LinuxSigner] Initializing signer with CSP...")
    try:
        if not csp_available():
            raise OSError("CryptoPro CSP unavailable or no certificates in mMy store.")
        signer_instance = LinuxSigner()
        certs_count = len(signer_instance.get_list_certificates())
        logger.info(f"[LinuxSigner] Initialized. Found {certs_count} certificates.")
        return signer_instance
    except ImportError as e:
        logger.critical(f"[LinuxSigner] Import error — install pycades from source! Error: {e}")
        raise SystemExit(1)
    except SigningError:
        logger.fatal("[LinuxSigner] Cannot connect to CryptoPro CSP — is it running? Run 'cprocsp --version'")
        raise SystemExit(1)
