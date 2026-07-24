"""fake_sidecar fixture — E2E/integration 测试用的 sidecar 替身 (P3-17).

提供 ``create_fake_sidecar(tmp_path)`` 写出可执行的 fake sidecar 脚本 +
跨平台包装器，供 ``EmbeddedPostgresService`` / ``EmbeddedPgMaintenanceService``
在测试中 Popen 真实子进程。

设计要点:
- 复用 ``tests/unit/test_embedded_postgres_service.py::_MOCK_SIDECAR_PY`` 的跨平台包装器模式
  (Windows ``.bat`` / Unix ``.sh``，包装器调用 ``python -I`` 执行 Python 脚本)
- 支持子命令分发: run/status/stop/doctor/dump/restore/maintenance-shell
- 环境变量控制失败注入: ``FAKE_SIDECAR_EXIT_CODE`` / ``FAKE_SIDECAR_READY_JSON``
  / ``FAKE_SIDECAR_TIMEZONE`` / ``FAKE_SIDECAR_RESTORE_FAIL``
- ``run`` 子命令模拟 sidecar 长驻进程 (输出 ready JSON 后等 stdin EOF 退出)
- ``run`` 子命令写 password 到 ``--password-file`` (与真实 sidecar 行为一致)
- ``restore`` 子命令输出 target_data_dir 到 stdout (对齐 sidecar §12.2 原子切换协议,
  Python ``EmbeddedPgMaintenanceService.restore`` 用 ``stdout.strip()`` 取此值)
"""

from __future__ import annotations

import os
from pathlib import Path

# fake_sidecar.py 脚本 (自包含，通过 ``python -I`` 调用，不可 import 项目内模块)。
# 跨平台，通过 .bat (Windows) / .sh (Unix) 包装器调用。
# 注意: 字符串内的 ``\\n`` 写到文件后变成字面量 ``\n`` (Python 解析为换行符)。
_FAKE_SIDECAR_PY = """import argparse, json, os, sys
from pathlib import Path


def _override_exit_code():
    override = os.environ.get("FAKE_SIDECAR_EXIT_CODE")
    if override is not None:
        try:
            sys.exit(int(override))
        except ValueError:
            sys.exit(2)


def _build_ready_json(args):
    return {
        "schema": "qtrading.embedded_postgres.run.ready.v1",
        "status": "running",
        "postgres_version": "17.2.0",
        "host": "127.0.0.1",
        "port": 55432,
        "database": "qtrading",
        "username": "qtrading",
        "password_source": "password_file",
        "url": "postgresql://qtrading:***@127.0.0.1:55432/qtrading",
        "data_dir": args.data_dir,
        "sidecar_pid": 12340,
        "pid": 12345,
    }


def _build_doctor_json(args):
    return {
        "schema": "qtrading.embedded_postgres.doctor.v1",
        "data_dir": args.data_dir,
        "initialized": True,
        "pg_version": 170002,
        "bundled_pg_major": 17,
        "version_match": True,
        "critical_files_missing": [],
        "install_dir_complete": True,
        "missing_tools": [],
        "lock_held": False,
        "postgres_alive": True,
        "state_file": "running",
        "runtime_status": "running",
        "last_start_error": None,
        "issues": [],
        "timezone": os.environ.get("FAKE_SIDECAR_TIMEZONE", "Asia/Shanghai"),
    }


def cmd_run(args):
    if args.password_file:
        path = Path(args.password_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake_pg_password_55432", encoding="utf-8")

    ready_json_str = os.environ.get("FAKE_SIDECAR_READY_JSON")
    if ready_json_str:
        ready = json.loads(ready_json_str)
    else:
        ready = _build_ready_json(args)

    sys.stdout.write(json.dumps(ready) + "\\n")
    sys.stdout.flush()

    try:
        sys.stdin.read()
    except Exception:
        pass
    sys.exit(0)


def cmd_status(args):
    status = {"running": True, "pid": 12345}
    sys.stdout.write(json.dumps(status) + "\\n")
    sys.stdout.flush()
    sys.exit(0)


def cmd_stop(args):
    sys.exit(0)


def cmd_doctor(args):
    doctor = _build_doctor_json(args)
    sys.stdout.write(json.dumps(doctor) + "\\n")
    sys.stdout.flush()
    sys.exit(0)


def cmd_dump(args):
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"FAKE_PG_DUMP_BYTES")
    sys.exit(0)


def cmd_restore(args):
    if os.environ.get("FAKE_SIDECAR_RESTORE_FAIL") == "1":
        sys.exit(11)
    # 对齐 sidecar §12.2: 输出 target_data_dir 到 stdout
    # (Python EmbeddedPgMaintenanceService.restore 用 stdout.strip() 取此值)
    # 显式 --target-data-dir 透传; 缺省时模拟 sidecar 原子切换生成的 restore 目录
    if args.target_data_dir:
        target = args.target_data_dir
    else:
        data_dir = Path(args.data_dir)
        target = str(data_dir.parent / f"{data_dir.name}.restore-fake")
    sys.stdout.write(target + "\\n")
    sys.stdout.flush()
    sys.exit(0)


def cmd_maintenance_shell(args):
    info = {
        "psql_path": "/fake/psql",
        "connection_string_redacted": "postgresql://qtrading:***@127.0.0.1:55432/qtrading",
    }
    sys.stdout.write(json.dumps(info) + "\\n")
    sys.stdout.flush()
    sys.exit(0)


_DISPATCH = {
    "run": cmd_run,
    "status": cmd_status,
    "stop": cmd_stop,
    "doctor": cmd_doctor,
    "dump": cmd_dump,
    "restore": cmd_restore,
    "maintenance-shell": cmd_maintenance_shell,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=list(_DISPATCH.keys()))
    parser.add_argument("--data-dir", default="/fake/data")
    parser.add_argument("--install-dir")
    parser.add_argument("--password-file")
    parser.add_argument("--database", default="qtrading")
    parser.add_argument("--username", default="qtrading")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log-file")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--output")
    parser.add_argument("--input")
    parser.add_argument("--target-data-dir")
    args = parser.parse_args()

    _override_exit_code()

    _DISPATCH[args.command](args)


if __name__ == "__main__":
    main()
"""


def create_fake_sidecar(tmp_path: Path) -> Path:
    """写出 fake sidecar 脚本 + 跨平台包装器，返回包装器路径。

    Args:
        tmp_path: 临时目录 (通常由 ``tmp_path_factory.mktemp("fake_sidecar")`` 创建)

    Returns:
        Path: 包装器路径 (Windows 为 ``qtrading-pg-sidecar.bat``，Unix 为
              ``qtrading-pg-sidecar.sh``)，调用方将其作为 ``embedded_pg_sidecar_path``
              注入 AppConfig / 环境变量
    """
    fake_sidecar_py = tmp_path / "fake_sidecar.py"
    fake_sidecar_py.write_text(_FAKE_SIDECAR_PY, encoding="utf-8")

    if os.name == "nt":
        wrapper = tmp_path / "qtrading-pg-sidecar.bat"
        # %~dp0 是 .bat 所在目录 (含尾部 \\)；%* 透传所有参数
        wrapper.write_text(
            f'@python -I "{fake_sidecar_py}" %*\n',
            encoding="utf-8",
        )
    else:
        wrapper = tmp_path / "qtrading-pg-sidecar.sh"
        wrapper.write_text(
            f'#!/bin/sh\nexec python3 -I "{fake_sidecar_py}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    return wrapper
