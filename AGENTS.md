# Agent Guide

## What this is

FastAPI microservice (`main.py`) for signing data with CAdES-BES digital signatures.
Cross-platform: Windows (win32com/CAdESCOM) and Linux (endesive).

## Run & verify

```
python main.py
```

- Serves on `0.0.0.0:8101` by default; `SERVICE_HOST`/`SERVICE_PORT` env vars override via
  `os.getenv` (`main.py:119-120`).
- Trap: the repo's `.env` says `SERVICE_PORT = 8001`, but `python-dotenv` is never called —
  the file is ignored. Env vars must be exported in the shell.
- No test suite, no linter/formatter/typecheck config. Verification is manual: run the server
  and hit the endpoints, or `python -m py_compile` the touched files.
- Deps are pinned in `requirements.txt`; a Python 3.13 venv exists at `.venv/`.

## Architecture

OS detection happens once at startup in `signers/__init__.py:get_signer()`. The returned
`BaseSigner` instance is created at module import time (`main.py:34`) and used for all requests.

```
main.py
  └─ signers/__init__.py   (factory: picks Windows or Linux signer)
       ├─ signers/base.py    (BaseSigner ABC)
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
  `CERT_PASSWORD`. Missing `CERT_PATH` → `ValueError` at call time; missing file →
  `FileNotFoundError`.
- **Env vars are read at module import time** (`linux.py:18-19`), not per request —
  changing them requires a process restart.
- **Serial number ignored**: The Linux signer loads a single configured certificate. The
  `serial_number` parameter is accepted but never matched against (the .p12 file is the
  only cert). It is still a required field in `SignRequest`.

### Both platforms
- **`data` is a plain string**: the API receives raw text, not base64. Each signer
  base64-encodes it internally (`windows.py:109/139`, `linux.py:82/101`) before signing.
  The signature output is also base64 with `\r`/`\n` stripped.
- **Unpinned endpoints mangle input**: `unpinned_signed_data` strips `\n`/`\r` from `data`
  before signing; `attached_signed_data` signs it verbatim.
- **Cert listing never fails**: `get_list_certificates()` catches all exceptions and
  returns `[]` (HTTP 200) on both platforms — a broken cert store looks like "no
  certificates".
- **Error mapping** (`main.py`): a signer `ValueError` (e.g. cert not found, missing
  `CERT_PATH`) → HTTP 404 on the sign endpoints; any other exception → HTTP 500.
  `/api/certificates/` maps everything to 500.
- **pywin32 on Linux**: `pywin32` is in requirements but only works on Windows. It is
  never imported on Linux — the factory (`signers/__init__.py`) guards the import by
  platform.

## Conventions

- Comments, log messages, README, and commit messages are in Russian — match that.
