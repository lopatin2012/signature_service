import sys
import logging
from typing import Optional

from signers.base import BaseSigner

logger = logging.getLogger(__name__)


def get_signer() -> BaseSigner:
    """Фабрика: возвращает подходящий signer в зависимости от ОС."""
    if sys.platform == "win32":
        from signers.windows import WindowsSigner
        logger.info("Обнаружена Windows — используется win32com/CAdESCOM signer.")
        return WindowsSigner()
    else:
        from signers.linux import LinuxSigner
        logger.info("Обнаружена Linux — используется endesive signer.")
        return LinuxSigner()
