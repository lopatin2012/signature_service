import base64
import gc
import logging
from datetime import datetime
from contextlib import contextmanager, suppress

import pythoncom
import win32com.client

from signers.base import (
    BaseSigner, SigningResult, SigningError, SigningErrorCategory, classify_signing_error, CertificateInfo,
)
from enums import SignatureEnum

logger = logging.getLogger(__name__)


class WindowsSigner(BaseSigner):
    """Реализация подписания через win32com/CAdESCOM (Windows)."""

    @staticmethod
    @contextmanager
    def _com_context():
        pythoncom.CoInitialize()
        try:
            yield
        finally:
            gc.collect()
            pythoncom.CoUninitialize()

    @staticmethod
    def _create_com_object(name: str):
        return win32com.client.Dispatch(name)

    def get_list_certificates(self) -> list[CertificateInfo]:
        with self._com_context():
            return self._list_certificates_in_context()

    def _list_certificates_in_context(self) -> list[CertificateInfo]:
        """Читает хранилище сертификатов. Вызывается внутри _com_context()."""
        certificates: list[CertificateInfo] = []
        oStore = self._create_com_object("CAdESCOM.Store")
        try:
            oStore.Open(
                SignatureEnum.CAPICOM_CURRENT_USER_STORE.value,
                SignatureEnum.CAPICOM_MY_STORE.value,
                SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
            )

            for cert in oStore.Certificates:
                serial_number = cert.SerialNumber
                subject_parts = cert.SubjectName.split(", ")
                fio = subject_parts[0][3:] if subject_parts[0].startswith("CN=") else cert.SubjectName

                valid_from_str = cert.ValidFromDate.strftime("%d-%m-%Y")
                valid_to_str = cert.ValidToDate.strftime("%d-%m-%Y")
                try:
                    days_valid = (datetime.strptime(valid_to_str, "%d-%m-%Y") - datetime.now()).days
                except ValueError:
                    days_valid = 9999

                if days_valid >= 1 and cert.IsValid().Result:
                    certificates.append(CertificateInfo(
                        serial_number=serial_number,
                        subject=fio,
                        issuer=cert.IssuerName,
                        valid_from=valid_from_str,
                        valid_to=valid_to_str,
                        thumbprint=None,
                    ))

            logger.info(f"[WindowsSigner] Найдено {len(certificates)} валидных сертификатов.")
            return certificates

        except Exception as e:
            logger.error(f"[WindowsSigner] Ошибка чтения хранилища: {e}", exc_info=True)
            raise SigningError(str(e), category=SigningErrorCategory.CONFIGURATION_ERROR) from e

        finally:
            with suppress(Exception):
                oStore.Close()

    def attached_signed_data(self, data: bytes, serial_number: str) -> tuple[str, SigningResult]:
        """CAdES-BES прикреплённая подпись (содержимое внутри подписи)."""
        try:
            return self._do_sign(data, serial_number, detached=False)
        except SigningError:
            raise
        except Exception as e:
            category = classify_signing_error(e)
            logger.error(f"[WindowsSigner] Неудачная попытка подписи: {e}", exc_info=True)
            raise SigningError(str(e), category=category) from e

    def unpinned_signed_data(self, data: bytes, serial_number: str) -> tuple[str, SigningResult]:
        """CAdES-BES откреплённая подпись (без содержимого документа)."""
        try:
            return self._do_sign(data, serial_number, detached=True)
        except SigningError:
            raise
        except Exception as e:
            category = classify_signing_error(e)
            logger.error(f"[WindowsSigner] Неудачная попытка подписи: {e}", exc_info=True)
            raise SigningError(str(e), category=category) from e

    def _do_sign(self, data: bytes, serial_number: str, detached: bool) -> tuple[str, SigningResult]:
        """Находит сертификат и создаёт подпись CAdES-BES в одном COM-контексте."""
        with self._com_context():
            return self._sign_in_context(data, serial_number, detached)

    def _sign_in_context(self, data: bytes, serial_number: str, detached: bool) -> tuple[str, SigningResult]:
        """Выполняет подпись. Вызывается внутри _com_context()."""
        oStore = self._create_com_object("CAdESCOM.Store")
        try:
            oStore.Open(
                SignatureEnum.CAPICOM_CURRENT_USER_STORE.value,
                SignatureEnum.CAPICOM_MY_STORE.value,
                SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
            )

            oCert = None
            target_serial = serial_number.replace("-", "").lower()
            for cert in oStore.Certificates:
                if cert.SerialNumber.replace("-", "").lower() == target_serial:
                    oCert = cert
                    break
            if oCert is None:
                # Сертификат не найден — это ошибка входных данных, ретрайт бессмыслен.
                raise ValueError(
                    f"Сертификат с серийным номером '{serial_number}' не найден. "
                    f"Убедитесь, что сертификат установлен в хранилище 'Мой сертификат' и валиден."
                )

            oSigner = self._create_com_object("CAdESCOM.CPSigner")
            oSigner.Certificate = oCert

            oSigningTimeAttr = self._create_com_object("CAdESCOM.CPAttribute")
            # Name — это enum CADESCOM_ATTRIBUTE: 0 = CADESCOM_AUTHENTICATED_ATTRIBUTE_SIGNING_TIME.
            oSigningTimeAttr.Name = SignatureEnum.TIMER_ATTR_NAME.value
            oSigningTimeAttr.Value = pythoncom.MakeTime(datetime.now())
            oSigner.AuthenticatedAttributes2.Add(oSigningTimeAttr)

            oSignedData = self._create_com_object("CAdESCOM.CadesSignedData")
            oSignedData.ContentEncoding = SignatureEnum.CONTENT_ENCODING.value
            oSignedData.Content = base64.b64encode(data).decode("ascii")

            encoded_output = SignatureEnum.CAPICOM_ENCODE_BASE64.value

            sSignedData = oSignedData.SignCades(
                oSigner,
                SignatureEnum.CADES_BES.value,
                bool(detached),
                encoded_output,
            )

            result_bytes = sSignedData.encode()
            logger.info(f"[WindowsSigner] Подпись успешно создана. Длина (bytes): {len(result_bytes)}")
            return base64.b64encode(result_bytes).decode("ascii"), SigningResult(success=True)

        finally:
            with suppress(Exception):
                oStore.Close()
