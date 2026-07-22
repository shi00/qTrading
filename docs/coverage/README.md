# Phase 3 覆盖率报告

## 报告口径

本报告仅基于 **unit 测试** 生成，不含 integration 测试覆盖率。

**原因**: 本地 TRAE 沙箱限制 + C 盘磁盘空间不足，导致 integration 测试套件无法完整运行。

**CI 完整报告**: CI 环境会合并 unit + integration 覆盖率并上传 Codecov，作为最终门禁（§17.0 强制要求）。

## 文件
- `embedded-pg-phase3.json` — JSON 机器可读报告（unit-only，约 2.5 MB）

## §17.0 门禁
- services/strategies/data ≥ 90%
- ui ≥ 85%
- 整体 fail_under = 85（pyproject.toml）

本地 unit-only 报告可能低于上述门禁（因 integration 覆盖的代码路径未计入），以 CI 合并报告为准。

## 重新生成完整报告

如需本地生成完整覆盖率报告（含 integration），在无沙箱限制的环境执行：

```bash
pytest tests/unit/ tests/integration/ \
  --cov=services --cov=strategies --cov=data --cov=ui --cov=core --cov=utils --cov=app \
  --cov-report=html:docs/coverage/embedded-pg-phase3.html \
  --cov-report=json:docs/coverage/embedded-pg-phase3.json \
  --cov-report=term-missing
```
