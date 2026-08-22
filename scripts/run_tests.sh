"""Simple test runner for signature service."""

import subprocess
import sys


def run_test(cmd: str, label: str) -> bool:
    """Run a single command and report result."""
    print(f"\n{'='*60}")
    print(f"  Test: {label}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[PASS] {label}")
        return True
    else:
        print(f"[FAIL] {label}")
        print(result.stderr)
        return False


if __name__ == "__main__":
    results = []

    # Syntax check imports
    results.append(run_test("python -c 'from signers.base import *'", "base module"))
    results.append(run_test("python -c 'from signers.windows import WindowsSigner'", "Windows signer"))
    results.append(run_test("python -c 'from signers.linux import LinuxSigner, csp_available'", "Linux signer"))
    results.append(run_test("python main.py --selftest", "self-test (skips if tests fail)", timeout=30))
    results.append(run_test("python -m pytest tests/test_api.py -v", "API unit tests"))

    print(f"\n{'='*60}")
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} checks passed")
    sys.exit(0 if all(results) else 1)
