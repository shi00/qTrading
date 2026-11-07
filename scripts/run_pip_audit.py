#!/usr/bin/env python
"""
pip-audit wrapper with structured allowlist support.

This script reads a YAML allowlist file and runs pip-audit with the appropriate
--ignore-vuln flags. It also checks if any ignored vulnerabilities have expired
(reevaluate_at date passed) and fails the CI if so.

Usage:
    python scripts/run_pip_audit.py --requirements requirements.txt requirements-optional.txt requirements-dev.txt --allowlist .security/audit-allowlist.yml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import typing
from datetime import date, datetime
from io import TextIOWrapper
from pathlib import Path

import yaml

# 以 `python scripts/run_pip_audit.py` 方式运行时 sys.path[0] 指向 scripts/，
# utils 包位于仓库根目录，需显式补齐（同 check_theme_contrast.py 等脚本惯例）
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from utils.time_utils import get_now  # noqa: E402 - sys.path 注入后导入

# pip-audit 默认 socket 超时 15s；PyPI 偶发握手/读超时会把安全门禁误判为失败
# （run 42335350136：requirements-dev.txt 报 ReadTimeoutError，前两个文件均通过）。
_HTTP_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 10
# 仅传输层故障可重试；命中漏洞时 stderr 不含这些标记，直接失败，避免把真实发现重试掉。
_TRANSIENT_NETWORK_MARKERS = (
    "TimeoutError",
    "ReadTimeoutError",
    "ConnectTimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "NewConnectionError",
    "SSLError",
    "handshake operation timed out",
    "RemoteDisconnected",
    "Temporary failure in name resolution",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pip-audit with structured allowlist")
    parser.add_argument(
        "--requirements",
        nargs="+",
        required=True,
        help="One or more requirements files to audit",
    )
    parser.add_argument(
        "--allowlist",
        required=True,
        help="Path to YAML allowlist file",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["pypi"],
        help="Vulnerability data sources (default: pypi)",
    )
    return parser.parse_args()


def load_allowlist(allowlist_path: Path) -> dict:
    if not allowlist_path.exists():
        print(f"ERROR: Allowlist file not found: {allowlist_path}")
        sys.exit(1)

    with open(allowlist_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_expired_vulnerabilities(allowlist: dict) -> list[str]:
    expired = []
    today = get_now().date()

    vulns = allowlist.get("ignored_vulnerabilities", [])
    for vuln in vulns:
        vuln_id = vuln.get("id", "UNKNOWN")
        reevaluate_at_str = vuln.get("reevaluate_at")

        if not reevaluate_at_str:
            print(f"WARNING: Vulnerability {vuln_id} has no reevaluate_at date")
            continue

        try:
            if isinstance(reevaluate_at_str, date):
                reevaluate_at = reevaluate_at_str
            else:
                reevaluate_at = datetime.strptime(reevaluate_at_str, "%Y-%m-%d").date()  # noqa: DTZ007  # 人工填写的 YYYY-MM-DD 业务日期字符串无时区语义
        except ValueError:
            print(f"ERROR: Invalid date format for {vuln_id}: {reevaluate_at_str}")
            sys.exit(1)

        if today >= reevaluate_at:
            expired.append(vuln_id)
            print(f"ERROR: Vulnerability {vuln_id} has expired (reevaluate_at: {reevaluate_at_str})")
            print(f"  Package: {vuln.get('package', 'UNKNOWN')}")
            print(f"  Reason: {vuln.get('reason', 'No reason provided')[:100]}...")
            print(f"  Reviewer: {vuln.get('reviewer', 'UNKNOWN')}")

    return expired


def get_ignore_flags(allowlist: dict) -> list[str]:
    flags = []
    vulns = allowlist.get("ignored_vulnerabilities", [])
    for vuln in vulns:
        vuln_id = vuln.get("id")
        if vuln_id:
            flags.extend(["--ignore-vuln", vuln_id])
    return flags


def _is_transient_network_failure(stderr: str) -> bool:
    return any(marker in stderr for marker in _TRANSIENT_NETWORK_MARKERS)


def run_pip_audit_with_retry(cmd: list[str], req_file: str) -> int:
    """执行 pip-audit，仅对传输层故障重试；漏洞命中与参数错误立即返回。"""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        result = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode == 0:
            return 0
        if attempt == _MAX_ATTEMPTS or not _is_transient_network_failure(result.stderr):
            return result.returncode
        delay = _RETRY_BASE_DELAY_SECONDS * attempt
        print(f"WARNING: pip-audit 传输层故障（{req_file}，第 {attempt} 次），{delay}s 后重试")
        time.sleep(delay)
    return 1


def run_pip_audit(
    requirements_files: list[str],
    sources: list[str],
    ignore_flags: list[str],
) -> int:
    exit_code = 0

    for req_file in requirements_files:
        req_path = Path(req_file)
        if not req_path.exists():
            print(f"WARNING: Requirements file not found: {req_file}")
            continue

        print(f"\n{'=' * 60}")
        print(f"Auditing: {req_file}")
        print(f"Sources: {', '.join(sources)}")
        print(f"{'=' * 60}")

        cmd = ["pip-audit"]
        for source in sources:
            cmd.extend(["-s", source])
        cmd.extend(["-r", req_file])
        cmd.append("--desc")
        cmd.extend(["--timeout", str(_HTTP_TIMEOUT_SECONDS)])
        cmd.extend(ignore_flags)

        print(f"Running: {' '.join(cmd)}")
        try:
            returncode = run_pip_audit_with_retry(cmd, req_file)
        except FileNotFoundError:
            print("ERROR: pip-audit not found. Install with: pip install pip-audit")
            sys.exit(1)
        except subprocess.SubprocessError as e:
            print(f"ERROR: Failed to run pip-audit: {e}")
            sys.exit(1)

        if returncode != 0:
            exit_code = returncode

    return exit_code


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        typing.cast(TextIOWrapper, sys.stdout).reconfigure(line_buffering=True)

    args = parse_args()

    allowlist_path = Path(args.allowlist)
    allowlist = load_allowlist(allowlist_path)

    print(f"Loaded allowlist from: {allowlist_path}")
    vulns = allowlist.get("ignored_vulnerabilities", [])
    print(f"Ignored vulnerabilities: {len(vulns)}")
    for vuln in vulns:
        print(f"  - {vuln.get('id')}: {vuln.get('package')} (expires: {vuln.get('reevaluate_at')})")

    expired = check_expired_vulnerabilities(allowlist)
    if expired:
        print(f"\nERROR: {len(expired)} vulnerability ignore(s) have expired!")
        print("Please review and either:")
        print("  1. Update the reevaluate_at date if the vulnerability is still acceptable")
        print("  2. Remove the ignore if the vulnerability has been fixed")
        print("  3. Update the package to a fixed version")
        sys.exit(1)

    ignore_flags = get_ignore_flags(allowlist)
    exit_code = run_pip_audit(args.requirements, args.sources, ignore_flags)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
