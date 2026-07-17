# ADR-0004: redlines.yml 格式选择 YAML

> Status: Accepted
> Date: 2026-07-17
> Owner: 架构维护者

## Context

ADR-0003 决定落地红线 R1~R18 机器可读映射，需要选择 `docs/governance/redlines.yml` 的文件格式。候选格式：

- **YAML**：人类可读性强，支持注释，广泛用于配置（GitHub Actions / pre-commit / k8x 等）
- **JSON**：机器可读性强，但人类可读性差（无注释、引号多），Python stdlib 原生 `json` 支持
- **TOML**：人类可读性强，但表结构表达力弱于 YAML（嵌套数组表格语法繁琐），Python 3.11+ stdlib `tomllib` 支持（仅读）

项目现状：
- `pyproject.toml` 已用 TOML（`tomllib` 已是依赖）
- 无 JSON schema 工具链（无 `jsonschema` / `pydantic` 校验 yml 的现有路径）
- 无 YAML 工具链（项目依赖未含 `pyyaml`）

## Decision

选择 **YAML** 作为 redlines.yml 格式。

理由：
1. **人类可读性优先**：redlines.yml 需要工程师手动维护（新增 R19 等场景），YAML 的注释 + 块状结构最易读
2. **配置文件生态对齐**：GitHub Actions / pre-commit / dependabot 等项目内已有的配置文件均为 YAML，风格一致
3. **字段简单无需 schema 工具链**：redlines.yml 字段固定（5 个），用 stdlib + 简单校验即可，无需 `pyyaml` 的 schema 校验能力
4. **新增 `pyyaml` 依赖成本可控**：`pyyaml` 是 Python 生态最稳定库之一，无 transitive deps，安装成本极低

**不选 JSON 的理由**：无注释导致维护者无法在文件内解释字段含义；引号噪音降低可读性。

**不选 TOML 的理由**：嵌套数组表格语法（`[[redlines]]`）对 5 字段简单结构过度繁琐；TOML 表达力优势在多层级配置，单层列表场景无优势。

## Consequences

- **正向**：redlines.yml 可读性最佳；与项目内其他配置文件风格一致；新增 `pyyaml` 依赖但成本可控。
- **负向**：新增 `pyyaml` 依赖；`tomllib` 已有但无法复用（TOML 不选）；YAML 缩进敏感，维护者需注意格式。
- **缓解**：`pyyaml` 是 Python 生态稳定库，CI 已通过 `pip-audit` 守护安全；`check_redlines_yaml_consistency()` 在 parse 失败时提供精确报错。

## Alternatives

- **JSON**：拒绝。无注释 + 引号噪音降低可读性；维护成本高于 YAML。
- **TOML**：拒绝。`[[redlines]]` 数组表格语法对单层 5 字段结构过度繁琐；TOML 表达力优势在多层级配置，本场景无优势。
- **Python 文件（`redlines.py` 定义常量列表）**：拒绝。机器可读但混入代码层；治理文件应在 docs/governance/ 而非代码层。
