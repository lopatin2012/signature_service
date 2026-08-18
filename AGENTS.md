# Agent Guide

## What this is

Single-file FastAPI microservice (`main.py`) that signs data using Windows CAdESCOM/CryptoPro certificates via `pywin32` COM. Windows-only — will not work on Linux/macOS.

## Run

```
python main.py
```

Serves on `0.0.0.0:8101` by default. `.env` overrides with `SERVICE_HOST` and `SERVICE_PORT` (note: `.env` currently sets port `8001`, `main.py` defaults to `8101` — the `os.getenv` call wins at runtime).

Auto-generated API docs at `/docs`.

## Key gotchas

- **COM threading**: `_com_context()` calls `pythoncom.CoInitialize`/`CoUninitialize`. Every signing call runs in a COM context. Do not reorder or remove this.
- **Certificate store**: Signing looks up a certificate by serial number in `CAPICOM_CURRENT_USER_STORE` → `CAPICOM_MY_STORE`. The cert must already be installed in the Windows user's certificate store.
- **Base64 double-encode**: `attached_signed_data` re-encodes input to base64 before signing; `unpinned_signed_data` also base64-encodes. The raw `signed_data()` method expects base64 input. Keep this in mind when modifying input handling.
- **.env loading**: `python-dotenv` is listed in requirements but never called in `main.py` — env vars are read directly via `os.getenv`. If you add `.env` support, wire it through `pythoncom` or the top of `main.py`.

## Project structure

- `main.py` — app entrypoint, FastAPI routes, `HelperSignature` class
- `enums.py` — `SignatureEnum` with CAdESCOM/CAPICOM constants
- `requirements.txt` — pinned deps (UTF-16 encoded)
- `.env` — runtime host/port config
