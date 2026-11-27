# Signature Service — AGENTS.md

FastAPI microservice for CAdES-BES attached/detached signatures. Windows uses
`win32com` + CAdESCOM COM objects; Linux uses `pycades` + КриптоПро CSP. There is
no pip `endesive` package involved. Python **≥3.11**.

## Setup and commands

- On Windows, install with `pip install -r requirements.txt` (already includes
  pytest, httpx, ruff, mypy, pylint).
- This Windows checkout has `.venv` (Python 3.13); bare `python` has no pytest.
  Use `.venv\Scripts\python.exe -m ...` or activate the venv.
- `make` is not installed in this shell, and Makefile targets assume POSIX tools
  (`find`, `|| true`). Run the underlying commands directly on Windows.

| Command | What it does |
|---------|--------------|
| `python main.py` | Serve on `0.0.0.0:8101`; loads `.env` from the repo root. |
| `python main.py --selftest` | Run `pytest -m 'not cert'` first; exit 1 if it fails. Run from repo root. |
| `python main.py --timeout N` | One-off `SIGNING_TIMEOUT_SECONDS` override (CLI-only). |
| `python -m pytest -q tests/ -m 'not cert'` | Fast API tests; no crypto provider needed. |
| `python -m pytest tests/test_api.py::test_sign_attached_success -q` | Run one test. |
| `python -m pytest tests/test_certificate.py -m cert -v` | Real signer integration; needs CSP + cert in store. |
| `make lint` | `ruff check .` → `mypy .` → `pylint signers/ main.py`, in that order. |
| `make format` | `ruff check --fix .` then `ruff format .`; no black. |

`make lint` is currently red on pre-existing issues (import order, intentional
enum duplicates, missing pywin32/pycades stubs, BaseSigner override signatures).
Do not treat existing findings as regressions or reformat unrelated files.

## Architecture (non-obvious)

- The signer is a **singleton created in FastAPI `lifespan`** (`main.py`), not
  `@app.on_event`. Startup imports PFX files (Windows only), then `get_signer()`
  opens the store and lists certificates; if signer initialization fails it
  raises `SystemExit(1)` and the service refuses to start. If `main.signer` is
  already set (tests), lifespan skips initialization.
- COM/pycades calls are blocking and run in `asyncio.to_thread` under
  `asyncio.wait_for` in `_run_with_retries`. Keep new crypto work on that path.
  `wait_for` bounds request latency, not the worker thread: a timed-out call may
  keep running.
- Signer contract (`signers/base.py`): `get_list_certificates()` plus
  `attached_signed_data(data, serial_number)` / `unpinned_signed_data(...)`,
  each returning `(base64_signature, SigningResult)`. Failures normally raise
  `SigningError(..., category=...)`. A missing serial is the deliberate 404
  path: `_do_sign` raises `ValueError`; Linux re-raises it, Windows wraps it as
  `SigningError(CERTIFICATE_VALIDATION)`. `main.py` dispatches by
  `getattr(signer, method)`.
- Both signers match serials after stripping `-` and lowercasing.
  - Windows (`signers/windows.py`): `CurrentUser\My`; fresh
    `CoInitialize`/`CoUninitialize` per operation; `gc.collect()` before
    `CoUninitialize()` (avoids COM IUnknown release errors); adds a signing-time
    attribute.
  - Linux (`signers/linux.py`): machine `mMy`; no signing-time attribute.
    `pycades.Certificates` is **not iterable** — use `.Count` / `.Item(i + 1)`.
    `SignCades(..., CADESCOM_ENCODE_BASE64)` returns a base64 **string with
    CRLF**, not raw bytes; strip whitespace and return it as-is (do not encode
    again).
- `BaseSigner` declares keyword-only `signer_params`, but neither implementation
  accepts it; that is why mypy reports override errors and `main.py` only passes
  two positional args.
- `enums.py` centralizes CAdESCOM/CAPICOM values. `SignatureEnum` deliberately
  has duplicate values (COM constants); ruff PIE796 is expected — do not renumber.
- `signers/linux.py` also contains dead code (`get_signer()`, `CERT_PATH` /
  `pfx_path`); the active factory is `signers.get_signer()` in
  `signers/__init__.py`, and Linux uses `mMy`, not a PFX path.

## HTTP API

- Exact routes (trailing slash matters): `GET /api/health`,
  `GET /api/certificates/` (only certs with ≥1 day left and `IsValid()` true),
  `POST /api/sign/attached/`, `POST /api/sign/unpinned/`. `GET /` returns service
  info; `/docs` is OpenAPI UI.
- Bodies must be JSON (`{"data": "...", "serial_number": "AB12CD34EF56"}`);
  form-data returns 422 with a JSON hint. `data` is capped at 10,000,000
  characters (not bytes; it is UTF-8-encoded before signing); `serial_number`
  is 8–64 chars.
- `/api/health` returns 200 when the signer is available, 503 otherwise;
  `certificates_count` may be null.
- Error mapping: `SigningErrorCategory.CERTIFICATE_VALIDATION` / `ValueError` →
  404; timeout → 500 with a hint; other signing errors → 500; bad/missing key →
  401; request validation → 422.

## Config, auth, retries

- Env: `SERVICE_HOST` (0.0.0.0), `SERVICE_PORT` (8101),
  `SIGNING_TIMEOUT_SECONDS` (60), `MAX_SIGNING_RETRIES` (3),
  `BASE_RETRY_DELAY_SEC` (1.0), `API_KEY_HEADER_NAME` (X-API-Key),
  `API_KEYS` (comma list) / `SERVICE_API_KEY`,
  `CERTS_DIR` (Windows `./certs`, Linux `/certs`), `CERT_PASSWORD`; `CSP_LICENSE`
  is Docker-only. `.env` is loaded from the repo root and explicit env wins.
- Auth is off unless at least one non-empty key is set. `API_KEYS` takes
  precedence over `SERVICE_API_KEY` when both are set. Only `/api/*` routes use
  the auth dependency; `/`, `/docs`, `/openapi.json` stay public.
- `.env` is tracked in git despite `.gitignore` and contains a non-empty
  `CERT_PASSWORD`; never print or commit it. `certs/` is gitignored.
- `MAX_SIGNING_RETRIES` is total attempts, not extra retries. Only
  `NETWORK_OR_IO`, `TEMPORARY_RESOURCE`, and any `OSError` retry.
  `CERTIFICATE_VALIDATION`, `INVALID_DATA`, `CONFIGURATION_ERROR`, and
  `SYSTEM_UNAVAILABLE` do not.
- **`TimeoutError` subclasses `OSError`**, so timed-out attempts are retried;
  `SIGNING_TIMEOUT_SECONDS` is per attempt, not a total request budget. With the
  default 3 attempts a request can run well past 60s, and the timed-out worker
  thread may still be running.

## Testing quirks

- `tests/test_api.py` monkeypatches `main.signer` with a fake and uses
  `TestClient`; no crypto provider is needed. Any new `TestClient` test must set
  `main.signer` too, or lifespan will initialize the real signer and may exit.
- `tests/test_certificate.py` self-skips unless the pytest mark expression
  contains `cert`; it needs a real provider and a certificate with private key in
  the store. `-m cert` is required even when running the file directly.
- `python main.py --selftest` runs pytest in-process via `run_selftest`; it needs
  `tests/` in the current directory, so run it from the repo root.

## Linux / Docker gotchas

- Do **not** `pip install pycades`: the PyPI wheel bundles isolated `.so` files
  that cannot see the system CSP. Build from source
  (`https://github.com/CryptoPro/pycades`); the Dockerfile does this.
- `requirements.txt` still pins `pywin32` (the Dockerfile filters it) and does not
  include `pycades`; on Linux use the Docker build path or filter/install manually.
- Run with `docker compose up -d --build`. The build needs the tracked
  `distros/linux-amd64_deb.tgz`; compose mounts `./certs` read-only at `/certs`
  and imports every `*.pfx` into `mMy` on start. `CSP_LICENSE` is passed through
  if set.
- Self-signed PFX certs are trusted automatically: the entrypoint parses each
  PFX with `cryptography` (`python3`) and, when `subject == issuer`, installs
  the public cert into `mRoot` via `certmgr`. Without this, CryptoPro rejects
  the chain (`0x800B0109`) and `IsValid()` / `SignCades()` fail. This is needed
  for local/test certificates; `python3` and `cryptography` are in the image.
### 0x8007065B: expired CryptoPro license

`SignCades` returning `Internal error. (0x8007065B)` means the CryptoPro CSP
license is missing or expired (`ERROR_FUNCTION_FAILED`), not that the
certificate is bad. `/api/health` can still return 200 because listing
certificates does not sign anything.

- Check: `docker compose exec signature-service /opt/cprocsp/sbin/amd64/cpconfig -license -view`
- Fix: put a valid serial in `CSP_LICENSE` and recreate the container; the
  entrypoint runs `cpconfig -license -set` at startup.
- A license entered manually lives in `/var/opt/cprocsp`, which is not a
  volume, so it disappears when the container is recreated.
- The 90-day demo license also expires.
- **Password-file mismatch:** `docker-entrypoint.sh` strips `.pfx` and reads
  `<stem>.pw` (`cert.pw`), while README/compose and the Windows importer use
  `<file>.pfx.pw` (`cert.pfx.pw`). Check this first if container imports fail.
- `certs/` is gitignored; do not commit PFX files.

## Repo quirks

- `pyproject.toml` is not a usable setup path: it declares `pycadence` (not
  `pycades`) for non-Windows and maps the `signature-service` script to
  `main:run_selftest`, which only runs tests. Use `python main.py`.
- `scripts/run_tests.sh` is Python code in a `.sh` file and calls a nonexistent
  `timeout=` argument; `scripts/check_csp.sh` probes `pycares` / `CryptoPro`
  imports (neither is a project dependency) and masks failures with `|| true`.
  README lists both; use the pytest/make commands above instead.
