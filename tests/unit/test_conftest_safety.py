import os
import subprocess
import sys
import pytest


pytestmark = pytest.mark.unit


class TestConftestSafety:
    def test_test_db_name_must_start_with_test_prefix(self):
        code = (
            "import os; os.environ['TEST_DB_NAME']='prod_db'; "
            "import importlib; import tests.integration.conftest as m; importlib.reload(m)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "TEST_DB_NAME": "prod_db", "CI_PG_PASSWORD": "testpw"},
        )
        assert result.returncode != 0, "conftest should reject non-test_ prefixed DB name"
        assert "test_" in result.stderr or "test_" in result.stdout

    def test_test_db_name_with_test_prefix_passes(self):
        code = (
            "import os; os.environ.setdefault('CI_PG_PASSWORD','testpw'); "
            "os.environ.setdefault('TEST_DB_NAME','test_mysafe'); "
            "import tests.integration.conftest as m; print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={
                **os.environ,
                "TEST_DB_NAME": "test_mysafe",
                "CI_PG_PASSWORD": "testpw",
            },
        )
        assert "OK" in result.stdout or result.returncode == 0

    def test_reject_remote_db_host(self):
        code = "import os; os.environ['TEST_DB_HOST']='db.prod.example.com'; import tests.integration.conftest as m"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
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
        conftest_path = os.path.join(os.path.dirname(__file__), "..", "integration", "conftest.py")
        with open(conftest_path, encoding="utf-8") as f:
            content = f.read()
        assert "astock_test_local_2024" not in content, (
            "Hardcoded default password 'astock_test_local_2024' should be removed"
        )
        assert "test_ci_password_2024" not in content, "Hardcoded CI password 'test_ci_password_2024' should be removed"

    def test_password_derived_from_github_run_id(self):
        code = (
            "import os; os.environ.pop('TEST_DB_PASSWORD', None); "
            "os.environ.pop('CI_PG_PASSWORD', None); "
            "os.environ['GITHUB_RUN_ID']='12345'; "
            "import sys; sys.modules['dotenv'] = type(sys)('dotenv'); sys.modules['dotenv'].load_dotenv = lambda *a, **k: None; "
            "import tests.integration.conftest as m; print(m.TEST_DB_PASSWORD)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
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
            "import tests.integration.conftest as m; print(m.TEST_DB_PASSWORD)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
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
            "import tests.integration.conftest as m; print(m.TEST_DB_PASSWORD)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
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
            "import tests.integration.conftest as m; print(m.TEST_DB_NAME)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "PYTEST_XDIST_WORKER": "gw1"},
        )
        assert result.returncode == 0
        db_name = result.stdout.strip().split("\n")[-1]
        assert db_name.startswith("test_"), f"DB name must start with test_, got: {db_name}"
        assert "gw1" in db_name, f"DB name should contain worker id 'gw1', got: {db_name}"

    def test_no_xdist_worker_uses_default_name(self):
        code = (
            "import os; os.environ.pop('PYTEST_XDIST_WORKER', None); "
            "os.environ.pop('TEST_DB_NAME', None); "
            "import tests.integration.conftest as m; print(m.TEST_DB_NAME)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "PYTEST_XDIST_WORKER": ""},
        )
        assert result.returncode == 0
        db_name = result.stdout.strip().split("\n")[-1]
        assert db_name == "test_astock", f"Default DB name should be 'test_astock', got: {db_name}"

    def test_different_workers_get_different_names(self):
        names = []
        for worker in ["gw0", "gw1", "gw2"]:
            code = (
                f"import os; os.environ['PYTEST_XDIST_WORKER']='{worker}'; "
                "os.environ.pop('TEST_DB_NAME', None); "
                "import sys; sys.modules['dotenv'] = type(sys)('dotenv'); sys.modules['dotenv'].load_dotenv = lambda *a, **k: None; "
                "import tests.integration.conftest as m; print(m.TEST_DB_NAME)"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
                env={**os.environ, "PYTEST_XDIST_WORKER": worker},
            )
            assert result.returncode == 0
            db_name = result.stdout.strip().split("\n")[-1]
            names.append(db_name)

        assert len(set(names)) == 3, f"Each worker should get a unique DB name, got: {names}"
