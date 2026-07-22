"""Generate release manifest for qtrading-pg-sidecar binary (pg_plan §15.3 / §16.1).

产物用于发布流程的 sha256 校验与版本审计（§15.7）。CI build-artifacts job
构建 sidecar 后调用本脚本生成 manifest JSON，与 binary 一同上传为 release artifact。

用法:
    python scripts/generate_sidecar_manifest.py \
        --sidecar path/to/qtrading-pg-sidecar.exe \
        --target x86_64-pc-windows-msvc \
        [--output manifest.json]

退出码: 0 成功; 1 参数/文件错误; 2 sidecar version --json 调用失败。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

MANIFEST_SCHEMA = "qtrading.sidecar.manifest.v1"
VERSION_TIMEOUT_S = 10


def compute_sha256(path: Path) -> str:
    """计算文件 sha256 十六进制摘要。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def query_sidecar_version(sidecar: Path) -> dict[str, object]:
    """调用 ``sidecar version --json`` 获取构建元数据。

    返回 sidecar 输出的 JSON dict（schema qtrading.embedded_postgres.version.v1）。
    sidecar 不存在 / 调用超时 / 非 JSON 输出 / exit code != 0 时抛 RuntimeError。
    """
    if not sidecar.exists():
        raise FileNotFoundError(f"sidecar binary not found: {sidecar}")
    try:
        proc = subprocess.run(
            [str(sidecar), "version", "--json"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"sidecar version --json timed out after {VERSION_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"sidecar version --json exited {proc.returncode}; stderr={proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"sidecar version --json produced non-JSON stdout: {proc.stdout!r}") from exc


def build_manifest(
    sidecar: Path,
    target: str,
    version_json: dict[str, object],
    sha256: str,
    generated_at: _dt.datetime | None = None,
) -> dict[str, object]:
    """组装 manifest dict。

    version_json 来自 sidecar version --json（已校验 schema）。
    generated_at 默认 UTC now，可注入便于测试。
    """
    if generated_at is None:
        generated_at = _dt.datetime.now(_dt.UTC)
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at_utc": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_unix": int(generated_at.timestamp()),
        "target": target,
        "binary_name": sidecar.name,
        "sha256": sha256,
        "sidecar_version": version_json.get("sidecar_version", ""),
        "protocol_version": version_json.get("protocol_version", ""),
        "postgres_version": version_json.get("postgres_version", ""),
        "postgres_binary_source": version_json.get("postgres_binary_source", ""),
        "postgresql_embedded_version": version_json.get("postgresql_embedded_version", ""),
        "rustc_version": version_json.get("rustc_version", ""),
        "git_sha": version_json.get("git_sha", ""),
        "build_time_utc": version_json.get("build_time_utc", ""),
        "build_time_unix": version_json.get("build_time_unix", 0),
        "sidecar_self_sha256": version_json.get("self_sha256"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate release manifest for qtrading-pg-sidecar binary.")
    parser.add_argument("--sidecar", required=True, type=Path, help="sidecar binary path")
    parser.add_argument("--target", required=True, help="target triple (e.g. x86_64-pc-windows-msvc)")
    parser.add_argument("--output", type=Path, default=None, help="output manifest path (default: stdout)")
    args = parser.parse_args()

    sidecar: Path = args.sidecar.resolve()
    if not sidecar.exists():
        print(f"ERROR: sidecar binary not found: {sidecar}", file=sys.stderr)
        return 1

    try:
        version_json = query_sidecar_version(sidecar)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    sha256 = compute_sha256(sidecar)
    manifest = build_manifest(sidecar, args.target, version_json, sha256)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    if args.output is not None:
        args.output.write_text(manifest_text, encoding="utf-8")
        print(f"manifest written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(manifest_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
