import asyncio
import os
import subprocess
import sys
import pytest


pytestmark = [
    pytest.mark.unit,
    # R18 一致性：subprocess 测试在 Windows + xdist 并行环境下会触发 worker crash
    # （4 个 worker 同时启动 subprocess，Windows 进程创建开销大致 runner 资源耗尽）。
    # 用 xdist_group 将这些测试锁定到同一 worker 串行运行，避免资源竞争。
    # 参见 .github/workflows/ci_cd.yml E2E 测试用 -n 1 的同类问题处理。
    pytest.mark.xdist_group("conftest_safety"),
]

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DB_ENV_KEYS = (
    "TEST_DB_NAME",
    "TEST_DB_RUN_ID",
    "TEST_DB_ALLOW_REBUILD",
    "PYTEST_XDIST_WORKER",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_JOB",
)


def _run_db_config_probe(overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """在干净 DB 环境下导入 ``_db_config`` 并输出命名/所有权结果。"""
    env = {**os.environ, "CI_PG_PASSWORD": "testpw"}
    env.pop("TEST_DB_PASSWORD", None)
    for key in _DB_ENV_KEYS:
        env.pop(key, None)
    env.update(overrides)
    code = (
        "import os\n"
        "import tests.integration._db_config as m\n"
        "print('NAME=' + m.TEST_DB_NAME)\n"
        "print('RUN=' + m.TEST_DB_RUN_ID)\n"
        "print('OWNED=' + str(m.TEST_DB_RUN_OWNED))\n"
        "print('EXPLICIT=' + str(m.TEST_DB_NAME_EXPLICIT))\n"
        "print('ENV_RUN=' + os.environ.get('TEST_DB_RUN_ID', ''))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=_PROJECT_ROOT,
        env=env,
    )


def _probe_values(result: subprocess.CompletedProcess) -> dict[str, str]:
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


class _FakeDbConnection:
    """伪 asyncpg 连接：捕获 DDL 目标，模拟库存在性判断。"""

    def __init__(self, *, exists: bool):
        self.exists = exists
        self.statements: list[str] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        if query.startswith("SELECT format("):
            template, db_name = args
            return template.replace("%I", f'"{db_name}"')
        if "FROM pg_database" in query:
            return 1 if self.exists else None
        raise AssertionError(f"unexpected query: {query}")

    async def execute(self, ddl):
        self.statements.append(ddl)
        if ddl.startswith("DROP DATABASE"):
            self.exists = False
        elif ddl.startswith("CREATE DATABASE"):
            self.exists = True


@pytest.fixture
def db_config(monkeypatch):
    """导入轻量 ``_db_config``（仅 stdlib），并固定 run 环境避免污染。"""
    monkeypatch.setenv("CI_PG_PASSWORD", "testpw")
    monkeypatch.setenv("TEST_DB_RUN_ID", "testrun1234")
    import tests.integration._db_config as m

    return m


class TestConftestSafety:
    def test_test_db_name_must_start_with_test_prefix(self):
        code = (
            "import os; os.environ['TEST_DB_NAME']='prod_db'; "
            "import importlib; import tests.integration._db_config as m; importlib.reload(m)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "TEST_DB_NAME": "prod_db", "CI_PG_PASSWORD": "testpw"},
        )
        assert result.returncode != 0, "conftest should reject non-test_ prefixed DB name"
        assert "test_" in result.stderr or "test_" in result.stdout

    def test_test_db_name_with_test_prefix_passes(self):
        code = (
            "import os; os.environ.setdefault('CI_PG_PASSWORD','testpw'); "
            "os.environ.setdefault('TEST_DB_NAME','test_mysafe'); "
            "import tests.integration._db_config as m; print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={
                **os.environ,
                "TEST_DB_NAME": "test_mysafe",
                "CI_PG_PASSWORD": "testpw",
            },
        )
        assert "OK" in result.stdout or result.returncode == 0

    def test_reject_remote_db_host(self):
        code = "import os; os.environ['TEST_DB_HOST']='db.prod.example.com'; import tests.integration._db_config as m"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={
                **os.environ,
                "TEST_DB_HOST": "db.prod.example.com",
                "CI_PG_PASSWORD": "testpw",
            },
        )
        assert result.returncode != 0, "conftest should reject remote DB host"
        assert "TEST_DB_HOST" in result.stderr or "TEST_DB_HOST" in result.stdout


class TestConftestNoHardcodedPassword:
    def test_no_hardcoded_password_literal(self):
        integration_dir = os.path.join(os.path.dirname(__file__), "..", "integration")
        # 密码派生逻辑已迁移到 _db_config.py，需同时检查 conftest.py 和 _db_config.py
        for file_name in ("conftest.py", "_db_config.py"):
            file_path = os.path.join(integration_dir, file_name)
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            assert "astock_test_local_2024" not in content, (
                f"Hardcoded default password 'astock_test_local_2024' should be removed in {file_name}"
            )
            assert "test_ci_password_2024" not in content, (
                f"Hardcoded CI password 'test_ci_password_2024' should be removed in {file_name}"
            )

    def test_password_derived_from_github_run_id(self):
        code = (
            "import os; os.environ.pop('TEST_DB_PASSWORD', None); "
            "os.environ.pop('CI_PG_PASSWORD', None); "
            "os.environ['GITHUB_RUN_ID']='12345'; "
            "import sys; sys.modules['dotenv'] = type(sys)('dotenv'); sys.modules['dotenv'].load_dotenv = lambda *a, **k: None; "
            "import tests.integration._db_config as m; print(m.TEST_DB_PASSWORD)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "GITHUB_RUN_ID": "12345"},
        )
        assert result.returncode == 0
        password = result.stdout.strip()
        assert len(password) >= 16, f"Derived password should be long enough, got: {password}"
        assert password != "12345", "Password should not be the raw run ID"

    def test_password_derived_locally_without_ci(self):
        code = (
            "import os; os.environ.pop('TEST_DB_PASSWORD', None); "
            "os.environ.pop('CI_PG_PASSWORD', None); "
            "os.environ.pop('GITHUB_RUN_ID', None); "
            "import sys; sys.modules['dotenv'] = type(sys)('dotenv'); sys.modules['dotenv'].load_dotenv = lambda *a, **k: None; "
            "import tests.integration._db_config as m; print(m.TEST_DB_PASSWORD)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={
                **os.environ,
                "TEST_DB_PASSWORD": "",
                "CI_PG_PASSWORD": "",
                "GITHUB_RUN_ID": "",
            },
        )
        assert result.returncode == 0
        password = result.stdout.strip().split("\n")[-1]
        assert len(password) >= 16, f"Derived password should be long enough, got: {password}"

    def test_explicit_password_takes_precedence(self):
        code = (
            "import os; os.environ['TEST_DB_PASSWORD']='my_explicit_pw'; "
            "import tests.integration._db_config as m; print(m.TEST_DB_PASSWORD)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "TEST_DB_PASSWORD": "my_explicit_pw"},
        )
        assert "my_explicit_pw" in result.stdout


class TestConftestXdistIsolation:
    def test_xdist_worker_gets_unique_db_name(self):
        code = (
            "import os; os.environ['PYTEST_XDIST_WORKER']='gw1'; "
            "os.environ.pop('TEST_DB_NAME', None); "
            "import sys; sys.modules['dotenv'] = type(sys)('dotenv'); sys.modules['dotenv'].load_dotenv = lambda *a, **k: None; "
            "import tests.integration._db_config as m; print(m.TEST_DB_NAME)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "PYTEST_XDIST_WORKER": "gw1"},
        )
        assert result.returncode == 0
        db_name = result.stdout.strip().split("\n")[-1]
        assert db_name.startswith("test_"), f"DB name must start with test_, got: {db_name}"
        assert "gw1" in db_name, f"DB name should contain worker id 'gw1', got: {db_name}"

    def test_no_xdist_worker_uses_run_scoped_default_name(self):
        code = (
            "import os; os.environ.pop('PYTEST_XDIST_WORKER', None); "
            "os.environ.pop('TEST_DB_NAME', None); "
            "os.environ.pop('TEST_DB_RUN_ID', None); "
            "os.environ.pop('GITHUB_RUN_ID', None); "
            "import tests.integration._db_config as m; print(m.TEST_DB_NAME); print(m.TEST_DB_RUN_ID)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=_PROJECT_ROOT,
            env={
                **os.environ,
                "CI_PG_PASSWORD": "testpw",
                "PYTEST_XDIST_WORKER": "",
                "TEST_DB_NAME": "",
                "TEST_DB_RUN_ID": "",
                "GITHUB_RUN_ID": "",
            },
        )
        assert result.returncode == 0, result.stderr
        db_name, run_id = result.stdout.strip().splitlines()[-2:]
        assert db_name.startswith("test_astock_"), f"Default DB name must be run-scoped, got: {db_name}"
        assert db_name == f"test_astock_{run_id}", f"Default DB name must contain run-id, got: {db_name}"
        assert len(run_id) == 12, f"Generated run-id should be 12 chars, got: {run_id!r}"

    def test_different_workers_get_different_names(self):
        names = []
        for worker in ["gw0", "gw1", "gw2"]:
            code = (
                f"import os; os.environ['PYTEST_XDIST_WORKER']='{worker}'; "
                "os.environ.pop('TEST_DB_NAME', None); "
                "import sys; sys.modules['dotenv'] = type(sys)('dotenv'); sys.modules['dotenv'].load_dotenv = lambda *a, **k: None; "
                "import tests.integration._db_config as m; print(m.TEST_DB_NAME)"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
                env={**os.environ, "PYTEST_XDIST_WORKER": worker},
            )
            assert result.returncode == 0
            db_name = result.stdout.strip().split("\n")[-1]
            names.append(db_name)

        assert len(set(names)) == 3, f"Each worker should get a unique DB name, got: {names}"


class TestConftestRunIsolation:
    """D21-OTHER-002：不同 run 的同名 worker 必须得到不同目标库。"""

    def test_two_runs_same_worker_get_distinct_db_names(self):
        first = _probe_values(_run_db_config_probe({"PYTEST_XDIST_WORKER": "gw0"}))
        second = _probe_values(_run_db_config_probe({"PYTEST_XDIST_WORKER": "gw0"}))
        assert first["NAME"] != second["NAME"], f"Two runs must not share a target DB, got: {first['NAME']}"
        for values in (first, second):
            assert values["NAME"].startswith("test_astock_")
            assert values["NAME"].endswith("_gw0")
            assert values["OWNED"] == "True"

    def test_controller_run_id_is_distributed_to_worker(self):
        controller = _probe_values(_run_db_config_probe({}))
        assert controller["ENV_RUN"] == controller["RUN"], "controller must export TEST_DB_RUN_ID for workers"
        worker = _probe_values(
            _run_db_config_probe({"PYTEST_XDIST_WORKER": "gw3", "TEST_DB_RUN_ID": controller["RUN"]})
        )
        assert worker["RUN"] == controller["RUN"]
        assert worker["NAME"] == f"test_astock_{controller['RUN']}_gw3"

    def test_two_sessions_in_same_ci_job_get_distinct_names(self):
        """同一 CI job 的并行分片/双会话也必须拿到不同库。"""
        base = {
            "GITHUB_RUN_ID": "1001",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "integration",
            "PYTEST_XDIST_WORKER": "gw0",
        }
        first = _probe_values(_run_db_config_probe(base))
        second = _probe_values(_run_db_config_probe(base))
        assert first["NAME"] != second["NAME"], "each controller session must generate a unique run-id"

    def test_explicit_name_is_not_owned_without_rebuild_opt_in(self):
        values = _probe_values(_run_db_config_probe({"TEST_DB_NAME": "test_shared", "PYTEST_XDIST_WORKER": "gw0"}))
        assert values["NAME"] == "test_shared_gw0"
        assert values["EXPLICIT"] == "True"
        assert values["OWNED"] == "False", "explicit TEST_DB_NAME must not be treated as run-owned"

    def test_explicit_name_owned_only_when_rebuild_opted_in(self):
        values = _probe_values(_run_db_config_probe({"TEST_DB_NAME": "test_shared", "TEST_DB_ALLOW_REBUILD": "1"}))
        assert values["OWNED"] == "True"

    def test_invalid_run_id_rejected(self):
        result = _run_db_config_probe({"TEST_DB_RUN_ID": "bad run id"})
        assert result.returncode != 0, "invalid TEST_DB_RUN_ID must be rejected"
        assert "TEST_DB_RUN_ID" in (result.stderr + result.stdout)

    def test_invalid_rebuild_flag_rejected(self):
        result = _run_db_config_probe({"TEST_DB_ALLOW_REBUILD": "maybe"})
        assert result.returncode != 0, "invalid TEST_DB_ALLOW_REBUILD must be rejected"
        assert "TEST_DB_ALLOW_REBUILD" in (result.stderr + result.stdout)


class TestTestDbOwnershipDdl:
    """伪连接验证 DDL 目标与所有权门禁（不连接真实 PG）。"""

    def test_owned_clean_slate_drops_and_creates_only_own_db(self, db_config):
        conn = _FakeDbConnection(exists=True)
        asyncio.run(db_config.ensure_test_db_state(conn, clean_slate=True, db_name="test_astock_run1_gw0", owned=True))
        assert conn.statements == [
            'DROP DATABASE IF EXISTS "test_astock_run1_gw0" WITH (FORCE)',
            'CREATE DATABASE "test_astock_run1_gw0"',
        ]

    def test_non_owned_existing_db_is_never_dropped(self, db_config):
        conn = _FakeDbConnection(exists=True)
        asyncio.run(db_config.ensure_test_db_state(conn, clean_slate=True, db_name="test_shared_gw0", owned=False))
        assert conn.statements == [], "explicit non-owned DB must not be dropped or recreated"
        dropped = asyncio.run(db_config.drop_test_db_if_owned(conn, db_name="test_shared_gw0", owned=False))
        assert dropped is False
        assert conn.statements == []

    def test_non_owned_missing_db_only_creates(self, db_config):
        conn = _FakeDbConnection(exists=False)
        asyncio.run(db_config.ensure_test_db_state(conn, clean_slate=True, db_name="test_shared_gw1", owned=False))
        assert conn.statements == ['CREATE DATABASE "test_shared_gw1"']

    def test_drop_only_targets_own_run_name(self, db_config):
        conn = _FakeDbConnection(exists=True)
        dropped = asyncio.run(db_config.drop_test_db_if_owned(conn, db_name="test_astock_runA_gw0", owned=True))
        assert dropped is True
        assert conn.statements == ['DROP DATABASE IF EXISTS "test_astock_runA_gw0" WITH (FORCE)']
        assert "runB" not in conn.statements[0]

    def test_unsafe_db_name_rejected_before_ddl(self, db_config):
        conn = _FakeDbConnection(exists=True)
        unsafe = 'test_ok"; DROP DATABASE qtrading; --'
        with pytest.raises(ValueError, match="must contain only letters, digits, and underscores"):
            asyncio.run(db_config.execute_quoted_ddl(conn, db_config.DDL_CREATE_DB, unsafe))
        assert conn.statements == [], "unsafe identifier must be rejected before any DDL"
