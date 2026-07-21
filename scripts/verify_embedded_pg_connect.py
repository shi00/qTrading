"""Phase 0 Spike：端到端验证 embedded PostgreSQL sidecar 可用性（一次性脚本，计划 D-P3）。

对应 pg_plan §19 Phase 0 DoD 第 3/4/5 条：
1. 四种客户端连接（asyncpg / psycopg2 / SQLAlchemy sync / SQLAlchemy async）SELECT 1
2. Alembic upgrade head 落在空 embedded cluster
3. pg_dump/pg_restore roundtrip（sidecar 安装目录内 bundled 工具）

约束：不 import data/cache（project_memory scripts/ 红线）；仅用 stdlib + DB 驱动 + alembic。
用法：python scripts/verify_embedded_pg_connect.py --sidecar <sidecar.exe> --work-dir <tmp_dir>
退出码：0 全部通过；1 任一步骤失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

READY_TIMEOUT_S = 180  # 首次 setup 需解压 bundled PostgreSQL 归档 + initdb，给足余量
STOP_TIMEOUT_S = 60


def _readline_with_timeout(stream, timeout: float) -> str:
    q: queue.Queue[str] = queue.Queue(maxsize=1)

    def _reader() -> None:
        q.put(stream.readline())

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"sidecar ready line not received within {timeout}s") from exc


def step_select1_psycopg2(url: str) -> None:
    import psycopg2

    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        assert row is not None and row[0] == 1


def step_select1_sqlalchemy_sync(url: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()


async def _select1_asyncpg(url: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        assert await conn.fetchval("SELECT 1") == 1
    finally:
        await conn.close()


async def _select1_sqlalchemy_async(async_url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(async_url)
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1
    await engine.dispose()


def step_alembic_upgrade_head(repo_root: Path, url: str) -> str:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    # attributes 注入为 env.py get_database_url() 最高优先级，避免触碰用户真实配置
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()
    assert head, "alembic_version empty after upgrade head"
    return str(head)


def _find_tool(install_dir: Path, name: str) -> Path:
    matches = list(install_dir.rglob(name))
    assert matches, f"{name} not found under {install_dir}"
    return matches[0]


def step_dump_restore(install_dir: Path, url: str, work_dir: Path) -> None:
    from sqlalchemy import create_engine, text

    pg_dump = _find_tool(install_dir, "pg_dump.exe" if sys.platform == "win32" else "pg_dump")
    pg_restore = _find_tool(install_dir, "pg_restore.exe" if sys.platform == "win32" else "pg_restore")

    dump_file = work_dir / "spike.dump"
    base_url, _, _ = url.rpartition("/qtrading")
    rt_url = f"{base_url}/qtrading_rt"

    subprocess.run([str(pg_dump), "-Fc", "-f", str(dump_file), url], check=True, timeout=180)
    assert dump_file.exists() and dump_file.stat().st_size > 0, "pg_dump produced empty file"
    # createdb 不接受 conninfo URL（第二位置参数是 DESCRIPTION，会误连默认 5432 并挂起密码提示），
    # 改用 SQLAlchemy AUTOCOMMIT 建库
    engine = create_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("CREATE DATABASE qtrading_rt"))
    engine.dispose()
    subprocess.run([str(pg_restore), "-d", rt_url, str(dump_file)], check=True, timeout=180)

    engine = create_engine(rt_url)
    with engine.connect() as conn:
        table_count = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar()
    engine.dispose()
    assert table_count and table_count > 10, f"restore verification failed: only {table_count} tables"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    work = args.work_dir.resolve()
    data_dir = work / "17" / "data"
    install_dir = work / "17" / "install"
    password_file = work / "qtrading.pgpass"
    stderr_log = work / "sidecar.stderr.log"
    data_dir.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, bool, str]] = []
    proc: subprocess.Popen[str] | None = None
    try:
        with stderr_log.open("wb") as stderr_f:
            proc = subprocess.Popen(
                [
                    str(args.sidecar),
                    "run",
                    "--data-dir",
                    str(data_dir),
                    "--install-dir",
                    str(install_dir),
                    "--password-file",
                    str(password_file),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_f,
                text=True,
            )
            assert proc.stdout is not None
            t0 = time.monotonic()
            line = _readline_with_timeout(proc.stdout, READY_TIMEOUT_S)
            elapsed = time.monotonic() - t0
            assert line, "sidecar exited before ready line"
            ready = json.loads(line)
            results.append((f"sidecar ready ({elapsed:.1f}s)", True, ready["url"]))

        port = int(ready["port"])
        password = password_file.read_text(encoding="utf-8").strip()
        url = f"postgresql://postgres:{password}@127.0.0.1:{port}/qtrading"
        async_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        steps = [
            ("asyncpg SELECT 1", lambda: asyncio.run(_select1_asyncpg(url))),
            ("psycopg2 SELECT 1", lambda: step_select1_psycopg2(url)),
            ("sqlalchemy sync SELECT 1", lambda: step_select1_sqlalchemy_sync(url)),
            ("sqlalchemy async SELECT 1", lambda: asyncio.run(_select1_sqlalchemy_async(async_url))),
        ]
        for name, fn in steps:
            t0 = time.monotonic()
            try:
                fn()
                results.append((name, True, f"{time.monotonic() - t0:.2f}s"))
            except Exception as e:  # noqa: BLE001 — spike 脚本逐步骤收集失败
                results.append((name, False, repr(e)))

        try:
            t0 = time.monotonic()
            head = step_alembic_upgrade_head(repo_root, url)
            results.append(("alembic upgrade head", True, f"head={head} ({time.monotonic() - t0:.1f}s)"))
        except Exception as e:  # noqa: BLE001
            results.append(("alembic upgrade head", False, repr(e)))

        try:
            t0 = time.monotonic()
            step_dump_restore(install_dir, url, work)
            results.append(("pg_dump/pg_restore roundtrip", True, f"{time.monotonic() - t0:.1f}s"))
        except Exception as e:  # noqa: BLE001
            results.append(("pg_dump/pg_restore roundtrip", False, repr(e)))
    except Exception as e:  # noqa: BLE001
        results.append(("sidecar startup", False, repr(e)))
    finally:
        if proc is not None and proc.poll() is None and proc.stdin is not None:
            proc.stdin.close()  # 触发 EOF → sidecar graceful stop
            try:
                rc = proc.wait(timeout=STOP_TIMEOUT_S)
                results.append(("graceful stop via stdin EOF", rc == 0, f"exit={rc}"))
            except subprocess.TimeoutExpired:
                proc.kill()
                results.append(("graceful stop via stdin EOF", False, "timeout, killed"))

    print("\n=== Phase 0 Spike Results ===")
    ok_all = True
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        ok_all = ok_all and ok
    print(f"stderr log: {stderr_log}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
