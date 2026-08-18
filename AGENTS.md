# Agent Guide

## What this is

FastAPI microservice (`main.py`) for signing data using CAdES-BES digital signatures.
Cross-platform: Windows (win32com/CAdESCOM) and Linux (endesive).

## Run

```
python main.py
```

Serves on `0.0.0.0:8101` by default. `.env` overrides with `SERVICE_HOST` and `SERVICE_PORT`.

Auto-generated API docs at `/docs`.

## Architecture

OS detection happens once at startup in `signers/__init__.py:get_signer()`. The returned `BaseSigner` implementation is used for all signing requests.

```
main.py
  └─ signers/__init__.py   (factory: picks Windows or Linux signer)
       ├─ signers/base.py   (BaseSigner ABC)
       ├─ signers/windows.py (win32com/CAdESCOM — reads Windows cert store)
       └─ signers/linux.py   (endesive — reads PKCS#12 file from CERT_PATH)
```

## Key gotchas

### Windows (`signers/windows.py`)
- **COM threading**: `_com_context()` calls `pythoncom.CoInitialize`/`CoUninitialize`. Every signing call runs in a COM context. Do not reorder or remove this.
- **Certificate store**: Signing looks up a certificate by serial number in `CAPICOM_CURRENT_USER_STORE` → `CAPICOM_MY_STORE`. The cert must already be installed in the Windows user's certificate store.

### Linux (`signers/linux.py`)
- **PKCS#12 required**: Set env vars `CERT_PATH` (path to .p12/.pfx file) and `CERT_PASSWORD`. Without these, the Linux signer will raise `ValueError` at call time.
- **Serial number ignored**: The Linux signer loads a single configured certificate. The `serial_number` parameter in the API is accepted but not matched against (the .p12 file is the only cert). This differs from Windows where multiple certs live in the store.

### Both platforms
- **Base64 double-encode**: `attached_signed_data` re-encodes input to base64 before signing; `unpinned_signed_data` also base64-encodes. Keep this in mind when modifying input handling.
- **.env loading**: `python-dotenv` is listed in requirements but never called in `main.py` — env vars are read directly via `os.getenv`.
- **pywin32 on Linux**: `pywin32` is in requirements but only works on Windows. On Linux, pip will install it but it's never imported (the Linux signer module uses endesive instead).

## Project structure

- `main.py` — FastAPI app, routes, Pydantic models
- `enums.py` — `SignatureEnum` with CAdESCOM/CAPICOM constants (Windows-only)
- `signers/` — OS-abstracted signing layer
  - `base.py` — `BaseSigner` ABC
  - `windows.py` — win32com implementation
  - `linux.py` — endesive implementation
  - `__init__.py` — `get_signer()` factory
- `requirements.txt` — pinned deps
- `.env` — runtime config (SERVICE_HOST, SERVICE_PORT, CERT_PATH, CERT_PASSWORD)
