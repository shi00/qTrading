# Phase 5 覆盖率报告

## 报告口径

Phase 5 覆盖率归档包含三份报告，对应 pg_plan.md §17.0 覆盖率门禁：

| 报告 | 工具 | 门禁 | 生成位置 | 归档路径 |
|------|------|------|---------|---------|
| Python unit | pytest-cov | services/strategies/data ≥ 90%, ui ≥ 85%, 整体 ≥ 85% | CI ci_cd.yml `Run Unit Tests` step | `docs/coverage/embedded-pg-phase5-python/` |
| Rust unit | cargo-llvm-cov | 行 ≥ 85% | CI sidecar.yml `cargo llvm-cov` step (Linux) | `docs/coverage/embedded-pg-phase5-rust/` |
| Python integration | pytest-cov | 关键路径 100% (roundtrip 全通过) | CI ci_cd.yml `Run Integration Tests` step | `docs/coverage/embedded-pg-phase5-integration/` |

## 本地环境限制

本地环境（Windows + Python 3.10.11）不满足项目 Python 3.13+ 要求，且无 PostgreSQL 测试数据库。三份报告由 CI 生成，PR 合入后从 CI artifact 下载归档。

## CI Artifact 来源

### Python unit + integration 覆盖率

- CI workflow: `.github/workflows/ci_cd.yml`
- Artifact 名称: `test-reports-Linux-3.13`
- 包含文件: `coverage.xml`, `coverage.json`, `junit-*.xml`
- Codecov 上传: CI 自动上传到 Codecov（`codecov-action`）

### Rust unit 覆盖率

- CI workflow: `.github/workflows/sidecar.yml`
- Artifact 名称: `rust-coverage-lcov`
- 包含文件: `lcov.info`
- 仅 Linux 生成（`matrix.os == 'linux'`）

## 归档步骤（PR 合入后手动执行）

```bash
# 1. 下载 Python unit + integration 覆盖率
gh run download <run-id> -n test-reports-Linux-3.13 -D /tmp/phase5-python-cov
# 生成 HTML 报告
coverage html -d docs/coverage/embedded-pg-phase5-python /tmp/phase5-python-cov/coverage.json

# 2. 下载 Rust 覆盖率
gh run download <run-id> -n rust-coverage-lcov -D /tmp/phase5-rust-cov
# 转换 lcov → HTML（需安装 genhtml: choco install lcov 或 apt install lcov）
genhtml /tmp/phase5-rust-cov/lcov.info -o docs/coverage/embedded-pg-phase5-rust

# 3. Integration 覆盖率（从同一 CI run 的 integration 覆盖率数据）
# CI 已合并 unit + integration 覆盖率到 coverage.xml/json
# 如需单独 integration 报告，需修改 CI 分别生成
```

## §17.0 门禁

- Python unit: services/strategies/data ≥ 90%, ui ≥ 85%, 整体 `fail_under = 85`（pyproject.toml）
- Rust unit: 行 ≥ 85%（sidecar.yml `--fail-under-lines 85`）
- Integration: 关键路径 100%（roundtrip 全通过）
- E2E: 全通过，禁止 xFail（user_profile 强制约束）

## 重新生成完整报告

如需本地生成完整覆盖率报告（含 integration），在无沙箱限制的环境执行：

```bash
# Python unit + integration
pytest tests/unit/ tests/integration/ \
  --cov=services --cov=strategies --cov=data --cov=ui --cov=core --cov=utils --cov=app \
  --cov-report=html:docs/coverage/embedded-pg-phase5-python \
  --cov-report=json:docs/coverage/embedded-pg-phase5-python.json \
  --cov-report=term-missing

# Rust unit（需 Linux + cargo-llvm-cov）
cd sidecars/qtrading-pg-sidecar
cargo llvm-cov --bins --lcov --output-path lcov.info --fail-under-lines 85
genhtml lcov.info -o ../docs/coverage/embedded-pg-phase5-rust
```

## 相关文档

- 主计划: `reviews/pg_plan.md` §17.0
- Phase 3 报告: `docs/coverage/embedded-pg-phase3.json` + `docs/coverage/README.md`
- CI workflow: `.github/workflows/ci_cd.yml`, `.github/workflows/sidecar.yml`
