import logging
import base64
from datetime import datetime

import pycades

from signers.base import BaseSigner
from enums import SignatureEnum

logger = logging.getLogger(__name__)


class LinuxSigner(BaseSigner):
    """Реализация подписания через pycades (КриптоПро CSP, Linux)."""

    def _open_store(self):
        # pycades на Linux корректно открывает машинное хранилище
        # (CAPICOM_LOCAL_MACHINE_STORE). Пользовательское (CURRENT_USER) падает
        # с 0x80070002, т.к. хранилище uMy не создаётся автоматически.
        oStore = pycades.Store()
        oStore.Open(
            SignatureEnum.CAPICOM_LOCAL_MACHINE_STORE.value,
            SignatureEnum.CAPICOM_MY_STORE.value,
            SignatureEnum.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED.value,
        )
        return oStore

    def get_list_certificates(self) -> list[dict]:
        list_certificates = []
        try:
            oStore = self._open_store()
            try:
                for i in range(oStore.Certificates.Count):
                    val = oStore.Certificates.Item(i + 1)
                    str_serial_number = val.SerialNumber
                    subject_parts = val.SubjectName.split(", ")
                    fio = subject_parts[0][3:] if subject_parts[0].startswith("CN=") else val.SubjectName
                    lst_inn = [i[4:] for i in subject_parts if i.startswith("ИНН=")]
                    inn = lst_inn[0] if lst_inn else "000000000000"

                    str_valid_from = datetime.strptime(val.ValidFromDate, "%d.%m.%Y %H:%M:%S").strftime("%d-%m-%Y")
                    str_valid_for = datetime.strptime(val.ValidToDate, "%d.%m.%Y %H:%M:%S").strftime("%d-%m-%Y")
                    valid_days = (datetime.strptime(str_valid_for, "%d-%m-%Y") - datetime.now()).days

                    if valid_days >= 1 and val.IsValid().Result:
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

            finally:
                try:
                    oStore.Close()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Ошибка при чтении хранилища сертификатов: {e}", exc_info=True)
            return []

    def _find_certificate(self, serial_number: str):
        """Найти сертификат по серийному номеру в хранилище."""
        oStore = self._open_store()
        try:
            for i in range(oStore.Certificates.Count):
                val = oStore.Certificates.Item(i + 1)
                if val.SerialNumber.lower() == serial_number.lower():
                    return val
            raise ValueError(f"Сертификат с серийным номером {serial_number} не найден")
        finally:
            try:
                oStore.Close()
            except Exception:
                pass

    def attached_signed_data(self, data: str, serial_number: str) -> str:
        try:
            oCert = self._find_certificate(serial_number)

            oSigner = pycades.Signer()
            oSigner.Certificate = oCert

            oSignedData = pycades.SignedData()
            oSignedData.ContentEncoding = pycades.CADESCOM_BASE64_TO_BINARY
            oSignedData.Content = base64.b64encode(data.encode("utf-8")).decode("ascii")

            sSignedData = oSignedData.SignCades(
                oSigner,
                pycades.CADESCOM_CADES_BES,
                False,
                pycades.CADESCOM_ENCODE_BASE64,
            )
            sSignedData = sSignedData.replace("\r", "").replace("\n", "")
            return sSignedData

        except Exception as e:
            logger.error(f"Ошибка при создании прикреплённой подписи: {e}", exc_info=True)
            raise

    def unpinned_signed_data(self, data: str, serial_number: str) -> str:
        try:
            oCert = self._find_certificate(serial_number)

            oSigner = pycades.Signer()
            oSigner.Certificate = oCert

            message = str(data).replace(" ", "\u0020").replace("\n", "").replace("\r", "")
            message_bytes = message.encode()
            base64_message = base64.b64encode(message_bytes).decode()

            oSignedData = pycades.SignedData()
            oSignedData.ContentEncoding = pycades.CADESCOM_BASE64_TO_BINARY
            oSignedData.Content = base64_message

            sSignedData = oSignedData.SignCades(
                oSigner,
                pycades.CADESCOM_CADES_BES,
                True,
                pycades.CADESCOM_ENCODE_BASE64,
            )
            sSignedData = sSignedData.replace("\r", "").replace("\n", "")
            return sSignedData

        except Exception as e:
            logger.error(f"Ошибка при создании откреплённой подписи: {e}", exc_info=True)
            raise