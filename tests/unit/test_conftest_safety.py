import os
import subprocess
import sys


class TestConftestSafety:
    def test_test_db_name_must_start_with_test_prefix(self):
        code = (
            "import os; os.environ['TEST_DB_NAME']='prod_db'; "
            "import importlib; import tests.conftest as m; importlib.reload(m)"
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
            "import tests.conftest as m; print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env={**os.environ, "TEST_DB_NAME": "test_mysafe", "CI_PG_PASSWORD": "testpw"},
        )
        assert "OK" in result.stdout or result.returncode == 0

    def test_reject_remote_db_host(self):
        code = "import os; os.environ['TEST_DB_HOST']='db.prod.example.com'; import tests.conftest as m"
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

    def test_default_password_is_not_weak(self):
        import re

        conftest_path = os.path.join(os.path.dirname(__file__), "..", "conftest.py")
        with open(conftest_path, encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'TEST_DB_PASSWORD\s*=\s*["\'](\S+)["\']\s*$', content, re.MULTILINE)
        if match:
            hardcoded_default = match.group(1)
            assert hardcoded_default != "123456", "hardcoded default password must not be '123456'"
        else:
            pass
