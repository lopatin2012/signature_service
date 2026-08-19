# Agent Guide

## What this is

FastAPI microservice (`main.py`) for signing data using CAdES-BES digital signatures.
Cross-platform: Windows (win32com/CAdESCOM) and Linux (endesive).

## Run

```
python main.py
```

Serves on `0.0.0.0:8101` by default. `SERVICE_HOST` / `SERVICE_PORT` env vars override via
`os.getenv` (`main.py:119-120`).

> Trap: the repo's `.env` says `SERVICE_PORT = 8001`, but `python-dotenv` is never called
> in `main.py` — the file is ignored. The server listens on 8101 unless the env var is
> exported in the shell. See ".env loading" below.

Auto-generated API docs at `/docs`.

No test suite, no linter/formatter/typecheck config. Verification is manual: run the server
and hit the endpoints, or `python -m py_compile` the touched files.

## Architecture

OS detection happens once at startup in `signers/__init__.py:get_signer()`. The returned
`BaseSigner` instance is created at module import time (`main.py:34`) and used for all
requests.

```
main.py
  └─ signers/__init__.py   (factory: picks Windows or Linux signer)
       ├─ signers/base.py   (BaseSigner ABC)
       ├─ signers/windows.py (win32com/CAdESCOM — reads Windows cert store)
       └─ signers/linux.py   (endesive — reads PKCS#12 file from CERT_PATH)
```

`enums.py` is a top-level module (imported only by `windows.py`), not part of the `signers`
package.

## Key gotchas

### Windows (`signers/windows.py`)
- **COM threading**: `_com_context()` calls `pythoncom.CoInitialize`/`CoUninitialize`.
  Every signing call runs in a COM context. Do not reorder or remove this.
- **Certificate store**: Signing looks up a certificate by serial number (case-insensitive
  match) in `CAPICOM_CURRENT_USER_STORE` → `CAPICOM_MY_STORE`. The cert must already be
  installed in the Windows user's certificate store.
- `get_list_certificates()` drops certs with fewer than 1 remaining valid day.

### Linux (`signers/linux.py`)
- **PKCS#12 required**: Set env vars `CERT_PATH` (path to .p12/.pfx file) and
  `CERT_PASSWORD`. Without these, the Linux signer raises `ValueError` at call time.
- **Serial number ignored**: The Linux signer loads a single configured certificate. The
  `serial_number` parameter in the API is accepted but not matched against (the .p12 file is
  the only cert). It is still a required field in `SignRequest`.

### Both platforms
- **`data` is a plain string**: the API receives raw text, not base64. Each signer
  base64-encodes it internally (`windows.py:109/139`, `linux.py:82/101`) before signing.
  The signature output is also base64 with `\r`/`\n` stripped. Keep this in mind when
  modifying input handling.
- **`.env` loading**: `python-dotenv` is listed in requirements but never called in
  `main.py` — env vars are read directly via `os.getenv`. The `.env` file has no effect.
- **pywin32 on Linux**: `pywin32` is in requirements but only works on Windows. On Linux,
  pip will install it but it's never imported (the Linux signer module uses endesive instead).
- **Error mapping**: `ValueError` raised by a signer (e.g. cert not found, missing
  `CERT_PATH`) is mapped to HTTP 404 in `main.py`; any other exception → HTTP 500.

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
