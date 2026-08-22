"""Platform-aware signer factory for CAdES-BES signatures."""

import sys
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_signer() -> Any:
    """Factory returning signer appropriate for current platform.

    Windows -> WindowsSigner (win32com/CAdESCOM / Endesive)
    Linux   -> LinuxSigner (pycades + CryptoPro CSP)
    Raises SystemExit if no provider is available.
    """
    if sys.platform == "win32":
        from .windows import WindowsSigner as _WindowsSigner
        logger.info("[WindowsSigner] Initializing signer with win32com/CAdESCOM...")
        try:
            instance = _WindowsSigner()
            certs_count = len(instance.get_list_certificates())
            logger.info(f"[WindowsSigner] Initialized. Found {certs_count} certificates.")
            return instance
        except Exception as e:
            logger.critical(f"[WindowsSigner] Failed to initialize: {e}")
            raise SystemExit(1)
    else:
        from .linux import LinuxSigner as _LinuxSigner
        logger.info("[LinuxSigner] Initializing signer with pycades/CryptoPro...")
        instance = _LinuxSigner()
        certs_count = len(instance.get_list_certificates())
        logger.info(f"[LinuxSigner] Initialized. Found {certs_count} certificates.")
        return instance
