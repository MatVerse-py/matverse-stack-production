from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8099"


def _wait_ready(timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=1.0)
            if response.ok and response.json().get("ok") is True:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"service did not become ready: {last_error}")


def _start(data_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["MATVERSE_V19_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = "src"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "matverse_stack.deployed_homeostasis_service:app", "--host", "127.0.0.1", "--port", "8099"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_ready()
    return process


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_preflight() -> dict:
    with tempfile.TemporaryDirectory(prefix="matverse-v19-http-") as tmp:
        data_dir = Path(tmp)
        first = _start(data_dir)
        try:
            run = requests.post(f"{BASE_URL}/experiment/run", timeout=120).json()
            health_before_restart = requests.get(f"{BASE_URL}/health", timeout=5).json()
            receipts_before = requests.get(f"{BASE_URL}/receipts", timeout=10).json()
        finally:
            _stop(first)

        second = _start(data_dir)
        try:
            health_after_restart = requests.get(f"{BASE_URL}/health", timeout=5).json()
            state_after_restart = requests.get(f"{BASE_URL}/state", timeout=10).json()
            receipts_after = requests.get(f"{BASE_URL}/receipts", timeout=10).json()
            ledger_after = requests.get(f"{BASE_URL}/ledger/verify", timeout=5).json()
        finally:
            _stop(second)

        restart_readback_ok = (
            receipts_after["count"] == receipts_before["count"]
            and receipts_after["integrity"] is True
            and state_after_restart["last_report"] is not None
            and state_after_restart["last_report"].get("preflight_result") == "PASS"
        )
        final = {
            "preflight_run": run,
            "health_before_restart": health_before_restart,
            "health_after_restart": health_after_restart,
            "receipts_before_restart_count": receipts_before["count"],
            "receipts_after_restart_count": receipts_after["count"],
            "ledger_after_restart": ledger_after,
            "restart_readback_ok": restart_readback_ok,
            "service_process_restarted": True,
            "deployed_primary_result": "HOLD",
            "deployment_reason": "No externally hosted persistent provider was available without an account upgrade; Replit required active subscription and Base44 external sandbox required Builder plan.",
        }
        final["preflight_result"] = (
            "PASS"
            if run.get("preflight_result") == "PASS"
            and health_after_restart.get("ok") is True
            and ledger_after.get("integrity") is True
            and restart_readback_ok
            else "FAIL"
        )
        canonical = json.dumps(final, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        final["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return final


if __name__ == "__main__":
    print(json.dumps(run_preflight(), sort_keys=True, ensure_ascii=False, indent=2))
