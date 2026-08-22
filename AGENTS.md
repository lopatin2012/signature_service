# Signature Service — Quick Reference

FastAPI microservice for creating CAdES-BES (detached and attached) electronic signatures, cross-platform: Windows (win32com/CAdESCOM + Endesive) or Linux (pycades + КриптоПро CSP). Singleton signer instantiated at startup; all signing requests share the same instance.

---

## Architecture Overview

```
┌─────────────────────────────────────────┐
│              main.py                     │
│  ┌──────────────┐  ┌──────────────┐    │
│  │ FastAPI App  │←→│   Signer     │    │
│  │ Router       │  │ Singleton    │    │
│  └──────────────┘  │ Instance     │    │
│                    └───┬─────────┘    │
│                        │               │
│        ┌───────────────┴──────────────┤
│        ▼                               ▼
│  WindowsSigner                   LinuxSigner
│  ─────────────────            ──────────────────
│  win32com → CAdESCOM            pycades.Signer
│  CERT_CURRENT_USER_STORE       CryptoPro CSP (mMy)
│  pydantic.ValidationError      Cryptonets.dll
│                              └─> cprocsp --version check
└─────────────────────────────────────────┘
```

| Platform | Signer Module | Crypto Provider | Cert Store | Notes |
|----------|---------------|-----------------|------------|-------|
| Windows  | `WindowsSigner` | Endesive (`CAdESCOM`) | CurrentUser\My | No build needed |
| Linux    | `LinuxSigner` | CryptoPro CSP (`pycades`) | Machine mMy | Must source-install pycades |

**Key architectural decision:** The signer is a **singleton created at startup**. All requests share the same instance, which means:
- A failure during `__init__` takes down the entire service — this is intentional (`--selftest` prevents starting if checks fail).
- Long-running signing operations block all other requests until completion. Consider increasing `SIGNING_TIMEOUT_SECONDS`.

### Files

- `main.py` — FastAPI entrypoint, routers, retry policy, health check, self-test gate.
- `signers/__init__.py` — platform factory (`get_signer()` returns either `WindowsSigner` or `LinuxSigner`).
- `signers/windows.py` — CAdESCOM via COM automation; attaches attributes (timestamp) per request.
- `signers/linux.py` — pycades + Cryptonets.dll against machine store `mMy`; does NOT support signing-time attributes on Linux due to pycades limitations.
- `tests/test_api.py` — unit tests with mocked signer (no crypto provider required).
- `tests/test_certificate.py` — integration tests requiring a real CryptoPro installation and valid certificate in store. Marked with `-m cert`.
- `.env.example` / `requirements.txt` — dev dependencies including `tenacity` for exponential backoff retries.

---

## Commands

| Command | Description |
|---------|-------------|
| `python main.py` | Start server (default host/port from env) |
| `SERVICE_HOST=... SERVICE_PORT=80 python main.py` | Override defaults |
| `python main.py --selftest` | Run API tests **before** starting; refuses to start if they fail |
| `pytest -q -m 'not cert' tests/` | Fast API-only test run (no crypto provider needed) |
| `pytest tests/test_certificate.py -m cert` | Integration test against CryptoPro |

Default: `http://0.0.0.0:8101/`  
Endpoints: `/api/sign/attached`, `/api/sign/unpinned`, `/api/certificates`, `/api/health`. Docs at `/docs`.

---

## Environment Variables

The app loads `.env` from the project root via `python-dotenv` at startup (already-set environment variables take precedence and are not overridden), then reads `os.getenv()`. Set variables in `.env`, your shell, or via Docker CMD substitution (`"${CERT_PASSWORD}"`).

```bash
export SERVICE_HOST="0.0.0.0"
export SERVICE_PORT=8101
export SIGNING_TIMEOUT_SECONDS=60      # wall-clock timeout for signing (seconds)
export MAX_SIGNING_RETRIES=3           # max retry attempts on transient failures
export BASE_RETRY_DELAY_SEC=1.0        # base delay (s) for exponential backoff
export CERT_PATH="/path/to/certificate.pfx"  # Linux only, optional
export CERT_PASSWORD="your-password"       # Linux only, optional
```

On Linux, dates from `ValidFromDate` / `ValidToDate` are strings in `DD.MM.YYYY HH:MM:SS` format. Certificates with `< 1` remaining valid day are filtered by `get_list_certificates()`.

Error mapping: signer `ValueError` → HTTP 404; any other exception → HTTP 500.

---

## Reliability & Observability

### Graceful Fail-Fast (Startup Validation)

On startup the service validates that the crypto provider is available and can list certificates. If not, it exits immediately instead of serving requests silently. This prevents "works locally but fails in production" surprises.

```python
# main.py
@app.on_event("startup")
async def on_startup():
    signer = get_signer()                 # raises if provider unavailable
    _ = signer.get_list_certificates()    # verifies store access
    logger.info("Service started successfully.")
```

### Retries with Exponential Backoff

Signing operations use `tenacity.retry()` — up to `MAX_SIGNING_RETRIES` attempts with exponential delay (base 1s, max = timeout × 2). Errors are classified as retryable (`NETWORK_OR_IO`, `TEMPORARY_RESOURCE`) vs non-retryable (`CERTIFICATE_VALIDATION`, `INVALID_DATA`). Only transient errors trigger retries.

### Timeout Enforcement

Every signing request has a hard wall-clock timeout (`SIGNING_TIMEOUT_SECONDS`, default 60s). The operation is aborted via `asyncio.wait_for(..., timeout=...)`. No request hangs indefinitely.

### Detailed Logging

Each endpoint logs start, success/failure, and elapsed time in milliseconds. Log format includes timestamp, PID, logger name, level, function, line number:

```
2025-08-23 14:22:09,133 - 12345 - uvicorn.access - INFO - {"method":"POST","path":"/api/sign/attached/",...}
2025-08-23 14:22:10,871 - 12345 - signers.windows - WARNING - Retry #1 for signing after: OSError: [WinError 123] ...
2025-08-23 14:22:12,871 - 12345 - main - INFO - ✓ Подпись создана за 1738ms (сертификат: AB12CD34EF56)
```

### API Key Authentication (Optional)

Set `SERVICE_API_KEY` or `API_KEYS=foo,bar,baz` to enable header-based auth via `X-API-Key` on all endpoints. Checksum validation is performed before any crypto operation runs.

### Health Check Endpoint

`GET /api/health` returns current signer state and certificate count. Use as liveness probe:

```yaml
livenessProbe:
  httpGet:
    path: /api/health
    port: 8101
  initialDelaySeconds: 5
  periodSeconds: 10
```

---

## Linux / pycades Gotchas (High Signal)

The official PyPI wheel for `pycades` bundles isolated `.so` files that **cannot** see the system CSP libraries. It will import but fail at runtime with cryptonets errors.

**Required steps on Linux:**

```bash
# 1. Install CryptoPro packages (Debian/Ubuntu)
sudo apt-get install lsb-cprocsp-devel cprocsp-legacy cprocsp-pki-cades kc1

# 2. Build pycades from source so it links against system libs
git clone https://github.com/CryptoPro/pycades.git
cd pycades && pip install .

# 3. Import PFX into mMy machine store BEFORE first request
cryptonets --store mMy -pfx import your-cert.pfx password

# 4. Verify provider is running
cprocsp --version
```

**Do NOT use:** `pip install pycades` — this installs a broken wheel.

Signing-time attributes are not supported on Linux due to pycades limitations. Windows via CAdESCOM supports them natively.

---

## Testing Strategy

| Test Type | Marker | Prerequisite | Command |
|-----------|--------|-------------|---------|
| API unit tests | none | pytest, httpx | `pytest -q -m 'not cert' tests/` |
| CryptoPro integration | `-m cert` | CSP installed + valid cert in store | `pytest tests/test_certificate.py -m cert -v` |

Pre-flight check script: `./scripts/check_csp.sh` — fails fast if CSP libraries are missing before running any test suite.

The fake signer in `test_api.py` replaces the real singleton at module level (`monkeypatch.setattr(main, "signer", fake)`), allowing full HTTP-layer testing without crypto dependencies.

---

## Docker Deployment

Dockerfile builds from source (to capture system CSP libs) and sets up the machine store with mounted PFX certificate.

```bash
# Build and run
docker compose up --build -d

# Logs show initialization sequence including certificate discovery
docker logs signature-service
```

Certificates are mounted at `/certs/cert.pfx`; password passed via docker-compose secrets or environment variable substitution `"${CERT_PASSWORD}"`. The container image includes official packages: `cprocsp-pki-cades`, `kc1`, `lsb-cprocsp-devel`.
