"""scripts/verify_embedded_pg_connect.py 双运行模式回归（技术债 P3-PR513-Script-SysPath-Inject）。

覆盖验收标准：
① 移除无条件 `sys.path.insert` 后 `python -m scripts.verify_embedded_pg_connect --help` 可运行；
② 现有使用方式 `python scripts/verify_embedded_pg_connect.py --help` 不回退。

另断言路径注入守卫本身：模块模式（`__package__ == "scripts"`）不注入仓库根；
直跑等价模式（`__package__` 为空）才注入仓库根；源码中不存在无条件注入。
两路均清除 `PYTHONPATH`，确保 import 可达性只来自各自运行模式的 `sys.path` 发现机制。
`--help` 由 argparse 在触达 sidecar 前退出，无副作用。
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = ROOT / "scripts" / "verify_embedded_pg_connect.py"
ROOT_STR = str(ROOT)

sys.path.insert(0, str(ROOT / "scripts"))

import verify_embedded_pg_connect as vp  # noqa: E402 - sys.path 注入后导入（与 tests/unit 同惯例）

from data.persistence.embedded_postgres import service as svc_module  # noqa: E402
from utils.sanitizers import DataSanitizer  # noqa: E402


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _run_help(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args, "--help"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_clean_env(),
        timeout=120,
    )


def _run_inline(code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_clean_env(),
        timeout=120,
    )


def test_module_mode_help_from_repo_root() -> None:
    """模块模式（仓库根 cwd）不依赖 sys.path 注入即可运行。"""
    proc = _run_help(["-m", "scripts.verify_embedded_pg_connect"], cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    # 仅校验脚本名：Python 3.14 起 `-m` 调用的默认 prog 为 `python -m <module>`，
    # 不再以脚本文件名开头（脚本已显式 prog，此处避免断言绑定解释器版本行为）
    assert "verify_embedded_pg_connect" in proc.stdout


def test_script_mode_help_from_outside_repo_root(tmp_path: Path) -> None:
    """直跑模式在仓库外 cwd 仍需注入仓库根，保证旧用法不回退。"""
    proc = _run_help([str(SCRIPT)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "usage: verify_embedded_pg_connect.py" in proc.stdout


def test_module_mode_does_not_inject_repo_root() -> None:
    """模块模式 `__package__ == "scripts"`，守卫必须跳过注入。"""
    code = f"import json, sys\nimport scripts.verify_embedded_pg_connect\nprint(json.dumps({ROOT_STR!r} in sys.path))\n"
    proc = _run_inline(code, ROOT)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) is False


def test_script_mode_injects_repo_root_outside_repo(tmp_path: Path) -> None:
    """直跑等价模式（`__package__` 为空）仍须注入仓库根，仓库外 cwd 下 import 才可达。"""
    code = f"import json, runpy, sys\nrunpy.run_path({str(SCRIPT)!r})\nprint(json.dumps({ROOT_STR!r} in sys.path))\n"
    proc = _run_inline(code, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) is True


def test_sys_path_insert_is_guarded_by_package_check() -> None:
    """源码中的 `sys.path.insert` 必须包在 `__package__` 判空守卫内（禁止无条件注入）。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def _is_sys_path_insert(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "insert"):
            return False
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "path"):
            return False
        return isinstance(owner.value, ast.Name) and owner.value.id == "sys"

    inserts = [node for node in ast.walk(tree) if _is_sys_path_insert(node)]
    assert inserts, "直跑兼容依赖路径注入，但源码中未找到 sys.path.insert"

    guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(isinstance(sub, ast.Name) and sub.id == "__package__" for sub in ast.walk(node.test))
    ]
    assert guards, "缺少 `__package__` 判空守卫"

    for insert in inserts:
        assert any(insert in ast.walk(guard) for guard in guards), (
            "存在未被 `__package__` 守卫包裹的 sys.path.insert（无条件注入）"
        )


# --------------------------------------------------------------------------- #
# R9：结果行脱敏（DSN 明文密码不得出现在 stdout）
# --------------------------------------------------------------------------- #
_MISSING_DSN_DETAIL = (
    "subprocess.CalledProcessError: Command '['/tmp/work/install/bin/pg_dump', '-Fc', '-f', "
    "'/tmp/work/spike.dump', 'postgresql://postgres:{password}@127.0.0.1:5432/qtrading']' "
    "returned non-zero exit status 1."
)


def test_format_result_masks_password_in_argv_conninfo() -> None:
    """pg_dump 失败时 CalledProcessError 的 argv 含 conninfo URL：打印行不得含明文密码。"""
    password = "spike-password-123"
    line = vp._format_result(
        "pg_dump/pg_restore roundtrip", False, _MISSING_DSN_DETAIL.format(password=password), password
    )
    assert password not in line
    # 可诊断信息保留：错误语义 + host:port/db（路径已由 DataSanitizer 替换为 <PATH>）
    assert "postgresql://postgres:***@127.0.0.1:5432/qtrading" in line
    assert "returned non-zero exit status 1" in line


def test_format_result_masks_password_with_special_chars() -> None:
    """密码含 @ / : / / 等特殊字符（URL 正则难切分）时仍不得残留任何片段。"""
    password = "p@ss:w/rd"
    line = vp._format_result(
        "pg_dump/pg_restore roundtrip", False, _MISSING_DSN_DETAIL.format(password=password), password
    )
    assert password not in line
    assert "p@ss" not in line and "ss:w/rd" not in line
    assert "127.0.0.1:5432/qtrading" in line


def test_format_result_without_password_argument_still_sanitizes_dsn() -> None:
    """未提供密码（如 sidecar 未就绪、DSN 尚未构造）时，DataSanitizer 仍遮蔽常规 DSN 密码。"""
    line = vp._format_result(
        "pg_dump/pg_restore roundtrip", False, _MISSING_DSN_DETAIL.format(password="common-password"), None
    )
    assert "common-password" not in line
    assert "postgresql://postgres:***@" in line


def test_main_registers_password_secret_for_sanitizer_fallback() -> None:
    """main 读取密码后须向 DataSanitizer 注册（无括号 URL 形态的兜底精确替换）。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_secret"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "DataSanitizer"
    ]
    assert calls, "main 未调用 DataSanitizer.register_secret(password)"
    assert any(isinstance(arg, ast.Name) and arg.id == "password" for call in calls for arg in call.args), (
        "register_secret 的参数应为实际读取的 password"
    )


# --------------------------------------------------------------------------- #
# D16-F09：DPAPI（PGPW2）密码文件消费 / 非 Windows 预检 / UTF-8 机器协议
# --------------------------------------------------------------------------- #
_SENTINEL_PASSWORD = "SyntheticDpapiPwd-9_2~"


class _WindowsSysProxy:
    """仅替换 ``service`` 模块视角下的 ``sys``，模拟 Windows 平台。

    不能全局 patch ``sys.platform``：Linux 上的 ``asyncio.run`` 会因此误选 Windows
    事件循环实现（``ModuleNotFoundError: _overlapped``）。其余属性仍委托真实 ``sys``。
    """

    platform = "win32"

    def __getattr__(self, name: str) -> object:
        return getattr(sys, name)


class _FakeSidecarProcess:
    """sidecar `run` 子进程的最小替身（ready 行由 ``_readline_with_timeout`` 直接交付）。"""

    def __init__(self) -> None:
        self.stdin = MagicMock()
        self.stdout = io.StringIO("")
        self.killed = False
        self._running = True

    def poll(self) -> int | None:
        return None if self._running else 0

    def wait(self, timeout: float | None = None) -> int:
        self._running = False
        return 0

    def kill(self) -> None:
        self.killed = True
        self._running = False


def _ready_line(password: str, port: int = 55432) -> str:
    return json.dumps(
        {
            "schema": "qtrading.embedded_postgres.run.ready.v1",
            "status": "running",
            "port": port,
            "url": f"postgresql://postgres:{password}@127.0.0.1:{port}/qtrading",
        }
    )


def _run_main_with_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ready_line: str
) -> tuple[int, list[str], list[tuple[list[str], dict[str, object]]], _FakeSidecarProcess]:
    """以 fake sidecar + 记录型客户端步骤运行 ``main()``，返回退出码与实际消费的 URL。"""
    work = tmp_path / "work"
    sidecar = tmp_path / "sidecar"
    urls: list[str] = []

    def _sync_step(url: str) -> None:
        urls.append(url)

    async def _async_step(url: str) -> None:
        urls.append(url)

    def _alembic_step(_repo_root: Path, url: str) -> str:
        urls.append(url)
        return "head-1"

    def _dump_step(_install_dir: Path, url: str, _work_dir: Path) -> None:
        urls.append(url)

    monkeypatch.setattr(vp, "_select1_asyncpg", _async_step)
    monkeypatch.setattr(vp, "_select1_sqlalchemy_async", _async_step)
    monkeypatch.setattr(vp, "step_select1_psycopg2", _sync_step)
    monkeypatch.setattr(vp, "step_select1_sqlalchemy_sync", _sync_step)
    monkeypatch.setattr(vp, "step_alembic_upgrade_head", _alembic_step)
    monkeypatch.setattr(vp, "step_dump_restore", _dump_step)
    monkeypatch.setattr(vp, "resolve_pg_major_version", lambda _sidecar: "16")
    monkeypatch.setattr(vp, "_readline_with_timeout", lambda _stream, _timeout: ready_line)

    fake_proc = _FakeSidecarProcess()
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_popen(argv: list[str], **kwargs: object) -> _FakeSidecarProcess:
        popen_calls.append((list(argv), kwargs))
        return fake_proc

    monkeypatch.setattr(vp.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_embedded_pg_connect.py", "--sidecar", str(sidecar), "--work-dir", str(work)],
    )
    return vp.main(), urls, popen_calls, fake_proc


class TestDpapiPasswordAndProtocol:
    """D16-F09 回归：脚本必须消费 Windows 密码格式，不能把密文当明文密码。"""

    @pytest.fixture(autouse=True)
    def _reset_known_secrets(self) -> Iterator[None]:
        DataSanitizer._reset_known_secrets()
        yield
        DataSanitizer._reset_known_secrets()

    def test_main_decrypts_dpapi_password_for_all_clients(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """mock DPAPI 解密交付合成哨兵密码：六个消费点都用解密结果，输出不含哨兵。"""
        monkeypatch.setattr(svc_module, "sys", _WindowsSysProxy())
        monkeypatch.setattr(svc_module, "_unprotect_win32_blob", lambda _blob: _SENTINEL_PASSWORD.encode("utf-8"))
        work = tmp_path / "work"
        work.mkdir()
        (work / "qtrading.pgpass").write_text("PGPW2:00ff10", encoding="utf-8")

        rc, urls, popen_calls, fake_proc = _run_main_with_fakes(monkeypatch, tmp_path, _ready_line(_SENTINEL_PASSWORD))

        assert rc == 0
        # 4 个驱动 SELECT + Alembic + dump/restore 都必须消费解密后的密码
        assert len(urls) == 6
        assert all(_SENTINEL_PASSWORD in url for url in urls)
        captured = capsys.readouterr()
        assert _SENTINEL_PASSWORD not in captured.out + captured.err
        _argv, kwargs = popen_calls[0]
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert fake_proc.killed is False

    def test_main_plaintext_password_on_unix_still_works(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unix 明文密码文件保持原行为（不需要 DPAPI 解密）。"""
        monkeypatch.setattr(sys, "platform", "linux")
        password = "PlainPwd-1_2.3~"
        work = tmp_path / "work"
        work.mkdir()
        (work / "qtrading.pgpass").write_text(password, encoding="utf-8")

        rc, urls, _popen_calls, _fake_proc = _run_main_with_fakes(monkeypatch, tmp_path, _ready_line(password))

        assert rc == 0
        assert len(urls) == 6
        assert all(password in url for url in urls)
        # 结果行经 _format_result 精确替换，明文密码不会进入 stdout/stderr
        captured = capsys.readouterr()
        assert password not in captured.out + captured.err

    def test_ciphertext_on_non_windows_fails_before_sidecar_and_preserves_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 Windows 平台遇到 PGPW2 密文：启动 sidecar 前失败，且密码文件字节不变。"""
        monkeypatch.setattr(sys, "platform", "linux")
        ciphertext = "PGPW2:00ff10"
        work = tmp_path / "work"
        work.mkdir()
        password_file = work / "qtrading.pgpass"
        password_file.write_text(ciphertext, encoding="utf-8")

        rc, urls, popen_calls, _fake_proc = _run_main_with_fakes(
            monkeypatch, tmp_path, _ready_line("irrelevant-password")
        )

        assert rc == 1
        assert popen_calls == []
        assert urls == []
        assert password_file.read_text(encoding="utf-8") == ciphertext
        captured = capsys.readouterr()
        assert "non-Windows platform" in captured.out
        assert "[FAIL] sidecar startup" in captured.out

    def test_corrupted_ready_line_is_rejected_before_connecting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """机器协议（ready JSON）含 U+FFFD 时显式失败，不尝试任何客户端连接。"""
        monkeypatch.setattr(sys, "platform", "linux")
        corrupted = (
            '{"schema": "qtrading.embedded_postgres.run.ready.v1", "status": "running", '
            '"port": 55432, "url": "postgresql://postgres:pwd@127.0.0.1:55432/qtrading", '
            '"data_dir": "D:/x\ufffd"}'
        )

        rc, urls, popen_calls, _fake_proc = _run_main_with_fakes(monkeypatch, tmp_path, corrupted)

        assert rc == 1
        assert len(popen_calls) == 1
        assert urls == []
        captured = capsys.readouterr()
        assert "invalid UTF-8" in captured.out
