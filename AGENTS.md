# Signature Service — Quick Reference

## Architecture

FastAPI microservice running two signer backends depending on OS:

| Platform | Signer module | Crypto provider | Cert source          |
|----------|---------------|-----------------|----------------------|
| Windows  | CAdESCOM      | endesive        | Current user store   |
| Linux    | pycades       | КриптоПро CSP   | Machine store        |

Signers are a singleton created once at import time in `main.py`.

### Files

```
main.py                          # entrypoint, router, error mapping
signers/__init__.py              # platform factory (win32com vs pycades)
signers/windows.py               # CAPICOM_CURRENT_USER_STORE → MY
signers/linux.py                 # CAPICOM_LOCAL_MACHINE_STORE + pycades.Signer/Dadata/Attribute/Store
enums.py                         # common enums shared across signers
tests/test_api.py                # unit tests (no crypto dependency)
tests/test_certificate.py        # integration; requires installed CSP + valid cert
```

---

## Commands

| Command                              | Description                                |
|--------------------------------------|--------------------------------------------|
| `python main.py`                     | Start server (default host/port from env)  |
| `SERVICE_HOST=... SERVICE_PORT=80 python main.py` | Override defaults     |
| `python main.py --selftest`          | Run API tests before starting the service  |
| `pytest -m 'not cert'`               | Fast API-only test run (default)           |
| `pytest tests/test_certificate.py -m cert` | Crypto provider integration test   |

Default: `http://0.0.0.0:8101/`

Endpoints: `/api/sign/attached`, `/api/sign/unpinned`, `/api/certificates`. Full docs at `/docs`.

---

## Environment / Config

**Critical:** The app does **NOT** import `python-dotenv`. It only reads `os.getenv()` directly. Export vars in your shell or pass via Docker CMD substitution (`"${CERT_PASSWORD}"`).

- `SERVICE_HOST`: bind address, default `"0.0.0.0"`
- `SERVICE_PORT`: port number, default `8101`
- `.env` is effectively a no-op for the running process — do not rely on it.

On Linux, dates from `ValidFromDate`/`ValidToDate` are strings in `DD.MM.YYYY HH:MM:SS` format. Certs with fewer than 1 remaining valid day are filtered out by `get_list_certificates()`.

Error mapping: signer `ValueError` → HTTP 404; anything else → HTTP 500.

---

## Linux / pycades gotchas (high signal)

PyPI wheel for `pycades` bundles its own isolated `.so` libraries and cannot see the system CSP store. Only a build-from-source version works in production:

```bash
git clone https://github.com/CryptoPro/pycades.git && cd pycades && pip install .
```

The official CryptoPro distribution packages required by their Dockerfile are: `lsb-cprocsp-devel`, `cprocsp-legacy`, `cprocsp-pki-cades` plus `kc1`. The PFX must be imported into the machine store (`mMy`) before signing will succeed. Signing-time attributes fail on Linux if you try to add them.

Windows uses pywin32 + CAdESCOM against the current user certificate store — no source build needed.
