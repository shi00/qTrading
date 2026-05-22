#!/usr/bin/env python
"""
pip-audit wrapper with structured allowlist support.

This script reads a YAML allowlist file and runs pip-audit with the appropriate
--ignore-vuln flags. It also checks if any ignored vulnerabilities have expired
(reevaluate_at date passed) and fails the CI if so.

Usage:
    python scripts/run_pip_audit.py --requirements requirements.txt requirements-optional.txt --allowlist .security/audit-allowlist.yml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import yaml


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
        default=["pypi", "osv"],
        help="Vulnerability data sources (default: pypi osv)",
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
    today = date.today()

    vulns = allowlist.get("ignored_vulnerabilities", [])
    for vuln in vulns:
        vuln_id = vuln.get("id", "UNKNOWN")
        reevaluate_at_str = vuln.get("reevaluate_at")

        if not reevaluate_at_str:
            print(f"WARNING: Vulnerability {vuln_id} has no reevaluate_at date")
            continue

        try:
            reevaluate_at = datetime.strptime(reevaluate_at_str, "%Y-%m-%d").date()
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
        cmd.extend(ignore_flags)

        print(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=False)
        except FileNotFoundError:
            print("ERROR: pip-audit not found. Install with: pip install pip-audit")
            sys.exit(1)
        except subprocess.SubprocessError as e:
            print(f"ERROR: Failed to run pip-audit: {e}")
            sys.exit(1)

        if result.returncode != 0:
            exit_code = result.returncode

    return exit_code


def main() -> None:
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
