# Contributing to AStockScreener

感谢你考虑为 AStockScreener 做贡献！

## 目录

- [行为准则](#行为准则)
- [如何贡献](#如何贡献)
- [开发环境设置](#开发环境设置)
- [代码风格](#代码风格)
- [提交信息规范](#提交信息规范)
- [CI 门禁要求](#ci-门禁要求)
- [Pull Request 流程](#pull-request-流程)

## 行为准则

本项目采用贡献者公约作为行为准则。参与此项目即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

如果你发现了 bug，请通过 [GitHub Issues](https://github.com/louis2sin/AStockScreener/issues) 提交。提交前请：

1. 搜索现有 issues，确认没有被报告过
2. 使用 issue 模板，提供以下信息：
   - 问题描述
   - 复现步骤
   - 期望行为
   - 实际行为
   - 环境信息（操作系统、Python 版本等）

### 提出新功能

欢迎提出新功能建议！请在 Issue 中详细描述：

- 功能描述
- 使用场景
- 可能的实现方案

### 提交代码

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 开发环境设置

### 前置要求

- Python 3.13+
- PostgreSQL 16+
- Git

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/louis2sin/AStockScreener.git
cd AStockScreener

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或 .venv\Scripts\activate  # Windows

# 安装开发依赖
pip install -r requirements.txt
pip install -r requirements-optional.txt
pip install -r requirements-dev.txt

# 安装 pre-commit hooks
pre-commit install

# 运行测试验证环境
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

### 数据库设置

```bash
# 创建数据库
createdb astock_screener

# 运行迁移
python -m alembic upgrade head
```

## 代码风格

### Python 代码规范

- 行宽：120 字符
- 缩进：4 空格
- 引号：双引号
- 使用 Python 3.13+ 语法（`X | None` 而非 `Optional[X]`）

### 工具

我们使用以下工具确保代码质量：

- **Ruff**: Lint 和格式化
- **Pyright**: 静态类型检查
- **pytest**: 测试框架

### 运行检查

```bash
# Lint 检查
ruff check .

# 格式化
ruff format .

# 类型检查
pyright

# 运行测试
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

### 类型注解

- 所有公共函数必须有类型注解
- 使用 `# type: ignore[错误码]  # 原因` 格式抑制类型错误
- 禁止裸 `# type: ignore`（pre-commit 会拦截）

## 提交信息规范

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### 类型

| 类型 | 描述 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具相关 |
| `ci` | CI 配置相关 |

### 示例

```
feat(strategy): add MACD crossover strategy

- Add MACD calculation using Polars
- Implement signal generation logic
- Add unit tests for edge cases

Closes #123
```

## CI 门禁要求

所有 Pull Request 必须通过以下检查：

### 必须通过的检查

| 检查项 | 说明 |
|--------|------|
| **lint-fast** | Ruff lint + format 检查（~60s） |
| **ci-checks (Linux)** | 完整测试套件（Linux 环境） |
| **ci-checks-windows** | 完整测试套件（Windows 环境） |
| **CodeQL** | 安全漏洞扫描 |
| **Gitleaks** | 密钥泄露扫描 |

### 覆盖率要求

- **整体覆盖率**: ≥ 85%（硬性门禁）
- **单文件覆盖率**: ≥ 80%（每个文件必须达标）

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链

## Pull Request 流程

### 提交前

1. 确保所有测试通过
2. 确保代码覆盖率达标
3. 运行 `pre-commit run --all-files`
4. 更新相关文档

### PR 描述模板

```markdown
## 变更类型
- [ ] Bug 修复
- [ ] 新功能
- [ ] 重构
- [ ] 文档更新
- [ ] 其他

## 变更描述
<!-- 描述你的变更 -->

## 相关 Issue
<!-- 关联的 Issue 编号 -->

## 测试
<!-- 描述如何测试这些变更 -->

## 截图（如适用）
<!-- UI 相关变更的截图 -->
```

### 代码审查

- 所有 PR 需要至少一位 reviewer 批准
- 某些关键路径（如 `data/persistence/`、`strategies/`）需要 CODEOWNERS 批准
- 解决所有 review 意见后才能合并

### 合并策略

我们使用 **Merge Queue** 确保合并安全：

1. PR 获得批准后，点击 "Ready for review" → "Merge when ready"
2. 系统会自动将 PR 加入合并队列
3. 在队列中会与 main 最新代码组合后重新运行 CI
4. 通过后自动合并

## 获取帮助

- **GitHub Issues**: 提问或报告问题
- **Email**: louis2sin@gmail.com

---

再次感谢你的贡献！
