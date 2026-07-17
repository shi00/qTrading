# Flet API 核验记录模板

> 用途：每次 Flet 版本升级或新增/变更 API 使用时，按本模板沉淀核验记录，作为升级证据。配合 [upgrade-checklist.md](./upgrade-checklist.md) 使用。

> Owner: UI 维护者
> 复核触发器: Flet 版本升级、新增高风险 API 使用、API 行为异常排查

## 模板字段

每次核验按以下字段记录：

| 字段 | 说明 |
|------|------|
| **API** | 受核验的 Flet API（如 `ft.use_dialog`、`ft.Dropdown.on_select`、`use_viewmodel(factory=)`） |
| **锁定版本** | 核验时的 Flet 锁定版本（从 [`pyproject.toml`](../../pyproject.toml) 读取，不写补丁号漂移；写小版本+日期） |
| **核验来源** | 官方文档链接 / Flet issue / 项目运行期验证 / 项目单元测试 |
| **项目结论** | 在项目中的契约判定（继续使用 / 调整契约 / 暂禁用 / 待评估） |
| **需更新文件** | 核验结果波及的文件清单（`v1-api-constraints.md` / `project-differences.md` / `upgrade-checklist.md` / `ui/hooks.py` / `tests/unit/ui/*_contract.py` 等） |
| **核验日期** | YYYY-MM-DD |
| **核验人** | 核验执行者（GitHub 用户名或团队角色） |

## 核验记录

> 按「最新在上」顺序追加记录。每条记录使用以下子模板：

### 核验记录模板

```markdown
### <API 名> @ <锁定版本小版本> (<核验日期>)

- **API**: <API 签名/行为>
- **锁定版本**: Flet <major.minor>（pyproject.toml 实际锁定见文件）
- **核验来源**:
  - 官方文档: <URL>
  - Flet issue / PR: <URL 或 N/A>
  - 项目运行期验证: <验证步骤或测试名>
  - 项目单元测试: <测试文件::测试方法>
- **项目结论**: <继续使用 / 调整契约 / 暂禁用 / 待评估>
  - 理由: <为何得出此结论>
- **需更新文件**:
  - [ ] <文件路径>
  - [ ] <文件路径>
- **核验人**: <GitHub 用户名 / 团队角色>
```

## 历史核验记录

> 当前无历史记录。首次升级 Flet 版本或新增高风险 API 使用时，按上述模板在此追加。

## 引用关系

- [v1-api-constraints.md](./v1-api-constraints.md): 声明式组件内 API 契约与 V0→V1 迁移表（契约定义源）
- [project-differences.md](./project-differences.md): 项目验证过的高风险 API（历史验证结果沉淀）
- [upgrade-checklist.md](./upgrade-checklist.md): 升级时的验证步骤（本模板的触发场景）
- [CLAUDE.md §3.1 R16](../../CLAUDE.md#31--绝对禁止): UI 阻塞红线（涉及 async API 时必查）
