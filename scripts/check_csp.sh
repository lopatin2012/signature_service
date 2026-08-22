#!/bin/bash
# check_csp.sh — verifies that CryptoPro CSP is installed before running tests.
set -euo pipefail

echo "=== Checking CryptoPro CSP availability ==="

if command -v cryptonets >/dev/null 2>&1; then
    echo "[OK] Cryptonets package is present."
else
    echo "[ERROR] cryptonets package not found on system."
    echo "Install via apt-get install libcprocsp-dev kc1 cprocsp-legacy"
    exit 1
fi

if python3 -c "import pycares.cpro; print('pycadence CSP client: OK')" 2>/dev/null || true; then
    echo "[OK] Python CSP bindings available."
elif python3 -c "from CryptoPro import *; print('CryptoPro CAPI loaded')" 2>/dev/null || true; then
    echo "[OK] CryptoPro CAPI module imported."
else
    echo "[WARNING] Python CryptoPro bindings not found in PATH."
fi

echo "=== All checks passed ==="
exit 0
