import logging
import base64
from datetime import datetime
from contextlib import contextmanager

import pythoncom
import win32com.client

from signers.base import BaseSigner
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
            pythoncom.CoUninitialize()

    @staticmethod
    def _create_com_object(name: str):
        return win32com.client.Dispatch(name)

    def get_list_certificates(self) -> list[dict]:
        list_certificates = []
        with self._com_context():
            try:
                oStore = self._create_com_object("CAdESCOM.Store")
                oStore.Open(
                    SignatureEnum.CAPICOM_CURRENT_USER_STORE.value,
                    SignatureEnum.CAPICOM_MY_STORE.value,
                    SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
                )
                for val in oStore.Certificates:
                    str_serial_number = val.SerialNumber
                    subject_parts = val.SubjectName.split(", ")
                    fio = subject_parts[0][3:] if subject_parts[0].startswith("CN=") else val.SubjectName
                    lst_inn = [i[4:] for i in subject_parts if i.startswith("ИНН=")]
                    inn = lst_inn[0] if lst_inn else "000000000000"

                    str_valid_from = val.ValidFromDate.strftime("%d-%m-%Y")
                    str_valid_for = val.ValidToDate.strftime("%d-%m-%Y")
                    valid_days = (datetime.strptime(str_valid_for, "%d-%m-%Y") - datetime.now()).days

                    if valid_days >= 1 and val.IsValid():
                        list_certificates.append({
                            "serial_number": str_serial_number,
                            "fio": fio,
                            "inn": inn,
                            "valid_from": str_valid_from,
                            "valid_for": str_valid_for,
                            "valid_days": valid_days,
                        })

                logger.info(f"Успешно найдено {len(list_certificates)} валидных сертификатов.")
                return list_certificates

            except Exception as e:
                logger.error(f"Ошибка при чтении хранилища сертификатов: {e}", exc_info=True)
                return []

            finally:
                try:
                    oStore.Close()
                except Exception:
                    pass

    def _find_certificate(self, serial_number: str):
        """Найти сертификат по серийному номеру в хранилище."""
        oStore = self._create_com_object("CAdESCOM.Store")
        oStore.Open(
            SignatureEnum.CAPICOM_CURRENT_USER_STORE.value,
            SignatureEnum.CAPICOM_MY_STORE.value,
            SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
        )
        try:
            for val in oStore.Certificates:
                if val.SerialNumber.lower() == serial_number.lower():
                    return val
            raise ValueError(f"Сертификат с серийным номером {serial_number} не найден")
        finally:
            try:
                oStore.Close()
            except Exception:
                pass

    def attached_signed_data(self, data: str, serial_number: str) -> str:
        with self._com_context():
            try:
                oCert = self._find_certificate(serial_number)

                oSigner = self._create_com_object("CAdESCOM.CPSigner")
                oSigner.Certificate = oCert

                oSigningTimeAttr = self._create_com_object("CAdESCOM.CPAttribute")
                oSigningTimeAttr.Name = SignatureEnum.TIMER_ATTR_NAME.value
                oSigningTimeAttr.Value = datetime.now()
                oSigner.AuthenticatedAttributes2.Add(oSigningTimeAttr)

                oSignedData = self._create_com_object("CAdESCOM.CadesSignedData")
                oSignedData.ContentEncoding = SignatureEnum.CONTENT_ENCODING.value
                oSignedData.Content = base64.b64encode(data.encode("utf-8")).decode("ascii")

                sSignedData = oSignedData.SignCades(
                    oSigner,
                    SignatureEnum.CADES_BES.value,
                    False,
                    SignatureEnum.CAPICOM_ENCODE_BASE64.value,
                )
                sSignedData = sSignedData.replace("\r", "").replace("\n", "")
                return sSignedData

            except Exception as e:
                logger.error(f"Ошибка при создании прикреплённой подписи: {e}", exc_info=True)
                raise

    def unpinned_signed_data(self, data: str, serial_number: str) -> str:
        with self._com_context():
            try:
                oCert = self._find_certificate(serial_number)

                oSigner = self._create_com_object("CAdESCOM.CPSigner")
                oSigner.Certificate = oCert

                oSigningTimeAttr = self._create_com_object("CAdESCOM.CPAttribute")
                oSigningTimeAttr.Name = SignatureEnum.TIMER_ATTR_NAME.value
                oSigningTimeAttr.Value = datetime.now()
                oSigner.AuthenticatedAttributes2.Add(oSigningTimeAttr)

                message = str(data).replace(" ", "\u0020").replace("\n", "").replace("\r", "")
                message_bytes = message.encode()
                base64_message = base64.b64encode(message_bytes).decode()

                oSignedData = self._create_com_object("CAdESCOM.CadesSignedData")
                oSignedData.ContentEncoding = SignatureEnum.CONTENT_ENCODING.value
                oSignedData.Content = base64_message

                sSignedData = oSignedData.SignCades(
                    oSigner,
                    SignatureEnum.CADES_BES.value,
                    True,
                    SignatureEnum.CAPICOM_ENCODE_BASE64.value,
                )
                sSignedData = sSignedData.replace("\r", "").replace("\n", "")
                return sSignedData

            except Exception as e:
                logger.error(f"Ошибка при создании откреплённой подписи: {e}", exc_info=True)
                raise
