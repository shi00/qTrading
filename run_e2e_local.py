"""本地 E2E 测试启动器（绕过 PowerShell ExecutionPolicy 限制）。

用法: python run_e2e_local.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    workroot = Path(__file__).resolve().parent
    sidecar_exe = workroot / "sidecars" / "qtrading-pg-sidecar" / "target" / "release" / "qtrading-pg-sidecar.exe"
    if not sidecar_exe.is_file():
        print(f"[ERROR] sidecar binary not found: {sidecar_exe}", file=sys.stderr)
        return 2

    sha256 = hashlib.sha256(sidecar_exe.read_bytes()).hexdigest()
    artifact_dir = workroot / "e2e-artifacts"
    artifact_dir.mkdir(exist_ok=True)

    env = os.environ.copy()
    env["QTRADING_DATABASE_MODE"] = "embedded"
    env["SIDECAR_BINARY_PATH"] = str(sidecar_exe)
    env["SIDECAR_SHA256"] = sha256
    env["E2E_TIMEOUT_MULTIPLIER"] = "2.0"
    env["E2E_ARTIFACT_DIR"] = str(artifact_dir)
    env["E2E_HEADED"] = "1"

    print(f"SIDECAR_BINARY_PATH={sidecar_exe}")
    print(f"SIDECAR_SHA256={sha256}")
    print(f"Workroot={workroot}")
    print("-" * 80)

    test_targets = sys.argv[1:] if len(sys.argv) > 1 else ["tests/e2e/"]

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_targets,
        "-o",
        "addopts=",
        "-p",
        "no:xdist",
        "-p",
        "no:randomly",
        "-v",
        "--tb=long",
        "--junitxml=junit-e2e.xml",
        "--timeout=600",
        "--timeout-method=thread",
        "-o",
        "log_cli=true",
        "--log-cli-level=DEBUG",
        "--capture=no",
    ]
    return subprocess.call(cmd, cwd=workroot, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
