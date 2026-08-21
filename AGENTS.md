# Agent Guide

## What this is

FastAPI microservice (`main.py`) for signing data with CAdES-BES digital signatures.
Cross-platform: Windows (win32com/CAdESCOM) and Linux (pycades/КриптоПро CSP).

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
       └─ signers/linux.py   (pycades/КриптоПро CSP — GOST certs; NOT endesive)
```

`enums.py` is a top-level module (imported by `windows.py` and `linux.py`), not part of the
`signers` package.

## Key gotchas

### Windows (`signers/windows.py`)
- **COM threading**: `_com_context()` calls `pythoncom.CoInitialize`/`CoUninitialize`.
  Every signing call runs in a COM context. Do not reorder or remove this.
- **Certificate store**: Signing looks up a certificate by serial number (case-insensitive
  match) in `CAPICOM_CURRENT_USER_STORE` → `CAPICOM_MY_STORE`. The cert must already be
  installed in the Windows user's certificate store.
- `get_list_certificates()` drops certs with fewer than 1 remaining valid day.

### Linux (`signers/linux.py`)
- **pycades/КриптоПро CSP required**: on Linux the signer uses `pycades` (official CryptoPro
  wrapper) against a **licensed, installed CryptoPro CSP** — not endesive. The container
  installs CSP from `distros/linux-amd64_deb.tgz` (packages `kc1`, `lsb-cprocsp-devel`,
  `cprocsp-legacy`, `cprocsp-pki-cades` per the official CryptoPro/pycades Dockerfile) and
  imports the PFX into the CryptoPro **machine** store at startup (`docker-entrypoint.sh`
  via `certmgr -install -pfx -store mMy`).
- **Import style**: `import pycades` (classes on the top level), NOT `from pycades import
  pycades`. Object names differ from CAdESCOM: `pycades.Signer`, `pycades.SignedData`,
  `pycades.Attribute`, `pycades.Store` — there is no `CPSigner`/`CadesSignedData`.
- **Build from source, not PyPI wheel**: the PyPI wheel (`pycades==1.1.4`, KirillOgleznev)
  bundles its own isolated `.so` libs and cannot see the system CSP store. `linux.py` works
  only with a pycades built from `git clone https://github.com/CryptoPro/pycades` + `pip install .`
  (this is what the Dockerfile does).
- **Machine store**: `_open_store()` uses `CAPICOM_LOCAL_MACHINE_STORE` (1). The user store
  (`CURRENT_USER`) fails with 0x80070002 unless `uMy` exists. Dates from
  `ValidFromDate`/`ValidToDate` are **strings** (`DD.MM.YYYY HH:MM:SS`); `Certificates.Count`
  + `Item(i+1)` (1-based), not `ItemByIndex`.
- **No signing-time attribute**: adding `AuthenticatedAttributes2` (signing-time attribute)
  crashes the process with `ATL::CAtlException` on Linux — skip it (CAdES-BES doesn't need it).
  `Value` must be a string if you do set attributes.
- **PFX must already be imported**: the signer reads the machine store, it does not load
  `.pfx` in memory. Import happens in the entrypoint; `serial_number` is matched against the
  store. The PFX may contain several certs — only the one with `HasPrivateKey()` signs.

### Both platforms
- **`data` is a plain string**: the API receives raw text, not base64. Each signer
  base64-encodes it internally (`windows.py:109/139`, `linux.py:92/116`) before signing.
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
