import base64
import gc
import logging
import os
import subprocess
import sys
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

import pythoncom
import win32com.client

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


CERTS_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "certs"


def _ps_single_quote(value: str) -> str:
    return value.replace("'", "''")


def _import_pfx_via_powershell(file_path: Path, password: str | None) -> bool:
    cmd = (
        f"Import-PfxCertificate -FilePath '{_ps_single_quote(str(file_path))}' "
        "-CertStoreLocation Cert:\\CurrentUser\\My -ErrorAction Stop"
    )
    if password:
        cmd = (
            f"$pw = ConvertTo-SecureString '{_ps_single_quote(password)}' -AsPlainText -Force; "
            f"Import-PfxCertificate -FilePath '{_ps_single_quote(str(file_path))}' "
            "-CertStoreLocation Cert:\\CurrentUser\\My -Password $pw -ErrorAction Stop"
        )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode == 0


def _import_pfx_via_certutil(file_path: Path, password: str | None) -> bool:
    args = ["certutil.exe", "-user", "-importPFX", str(file_path)]
    if password:
        args.append(password)
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    return proc.returncode == 0


def import_certs_from_dir(
    certs_dir: Path | None = None,
    global_password: str | None = None,
) -> int:
    """Импортирует все *.pfx из каталога в хранилище CurrentUser\\My (только Windows).

    Пароль для файла <name>.pfx берётся из соседнего <name>.pfx.pw (в приоритете),
    иначе из global_password (обычно CERT_PASSWORD). Повторный импорт уже
    установленного сертификата не считается ошибкой. Возвращает число успешно
    импортированных сертификатов.
    """
    if sys.platform != "win32":
        return 0
    if certs_dir is None:
        certs_dir = Path(os.getenv("CERTS_DIR", str(CERTS_DIR_DEFAULT)))
    certs_dir = certs_dir.expanduser().resolve()
    if global_password is None:
        global_password = os.getenv("CERT_PASSWORD")
    if not certs_dir.is_dir():
        logger.warning(f"[WindowsSigner] Каталог {certs_dir} не найден — импорт PFX пропущен.")
        return 0
    imported = 0
    for pfx in sorted(certs_dir.glob("*.pfx")):
        password = global_password
        pw_file = pfx.with_suffix(pfx.suffix + ".pw")
        if pw_file.is_file():
            password = pw_file.read_text(encoding="utf-8").strip()
        logger.info(f"[WindowsSigner] Импорт {pfx} в хранилище CurrentUser\\My...")
        if _import_pfx_via_powershell(pfx, password) or _import_pfx_via_certutil(pfx, password):
            imported += 1
        else:
            logger.warning(f"[WindowsSigner] Не удалось импортировать {pfx} (возможно, уже установлен).")
    logger.info(f"[WindowsSigner] Импортировано PFX-сертификатов: {imported}")
    return imported


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
