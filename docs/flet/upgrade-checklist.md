# Flet V1 升级检查清单

> 来源：从 man/flet-best-practices.md 迁移

> Owner: UI 维护者
> 复核触发器: Flet 依赖版本变化（pyproject.toml）、关键 API 变化、架构红线/边界变化或 ADR 决策（见 [../adr/](../adr/)）

Flet 版本升级时，按以下清单逐项验证。每项验证结果建议记录到 [api-verification-template.md](./api-verification-template.md) 模板中，作为升级证据沉淀。

## 1. API 兼容性检查

- [ ] `ft.use_dialog()` 签名与行为（声明式 Dialog 唯一契约，见 [project-differences.md §4.1](./project-differences.md#41-ftuse_dialog声明式组件内唯一-dialog-契约)）
- [ ] `ft.Dropdown.on_select` 事件名（项目统一用 `on_select`，非 `on_change`，见 [project-differences.md §4.2](./project-differences.md#42-dropdown-on_select非-on_change)）
- [ ] `use_effect` 的 `cleanup=` 关键字参数（非 setup 返回值，见 [project-differences.md §4.3](./project-differences.md#43-use_effect-cleanup-显式参数)）
- [ ] `use_viewmodel(factory=)` / `use_viewmodel(vm=)` 双模式互斥（见 [`ui/hooks.py`](../../ui/hooks.py)）
- [ ] `ft.Router` / `ft.Route` 声明式路由
- [ ] `DialogControl` 子类清单（`AlertDialog`/`DatePicker`/`TimePicker`/`SnackBar`/`Banner`/`BottomSheet`）
- [ ] V0→V1 迁移 API 表全部 21 项（见 [v1-api-constraints.md §V0→V1 迁移 API 表](./v1-api-constraints.md#v0v1-迁移-api-表)）

## 2. 兼容性测试与文档检查

```bash
# Flet 兼容性测试（从项目根目录运行）
python -m pytest tests/unit/ui/test_flet_0_86_*.py -v

# 文档一致性检查
python scripts/check_docs_consistency.py
```

## 3. 项目验证步骤

- [ ] 运行 `ruff check .` → `ruff format --check .` → `pyright`
- [ ] 运行 `python -m pytest tests/unit/ -v --tb=short`
- [ ] 启动应用，验证 Dialog / Dropdown / use_effect / use_viewmodel 关键路径
- [ ] 更新 [project-differences.md](./project-differences.md) 「最后验证日期」
- [ ] 在 [api-verification-template.md](./api-verification-template.md) 中登记本次升级的核验记录

## 4. 文档同步

- [ ] 检查 [CLAUDE.md](../../CLAUDE.md) 中 Flet 版本引用
- [ ] 检查 [CONTRIBUTING.md](../../CONTRIBUTING.md) Flet V1 章节入口索引
- [ ] 检查 [v1-api-constraints.md](./v1-api-constraints.md) 中 API 约束
- [ ] 检查 [project-differences.md](./project-differences.md) 中项目分叉与高风险 API
- [ ] 更新 [project-differences.md](./project-differences.md) 「最后验证日期」

## 5. 官方文档链接

- Flet 官方文档：<https://docs.flet.dev/>
- Flet Changelog：<https://github.com/flet-dev/flet/blob/main/CHANGELOG.md>
- Flet GitHub 仓库：<https://github.com/flet-dev/flet>

> 通用 Flet v1 教程（路由、Services、存储、构建打包、移动/Web 适配、响应式布局、控件清单等）请直接查阅官方文档，本文件不再复制，避免与上游漂移。
