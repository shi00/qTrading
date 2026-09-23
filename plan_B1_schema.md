# OSS 检视报告 B1 — use_memo / use_callback 补位方案（v3，代码检视修订版）

> 来源：`reviews/09-22/开源组件检视报告.md` §2 B1（P2，"建议先测量再优化"）
> 分支：`fix/oss-b1-hooks`（worktree 隔离，R18）
> 日期：2026-09-23
> 版本：v3 —— v2 落地后经两轮代码检视（宪法规范 + 对抗性）发现并修复 screener locale 陈旧缺陷后定稿

## 1. 问题现状（前置核实）

- `ui/` 全目录 `use_memo` / `use_callback` 零使用（Grep 实测，233 use_state / 48 use_effect / 21 use_ref / 0+0）。
- 最热组件：`screener_view.py`（2201 行）、`data_view.py`（1196 行）。每次任意 state 变更整棵子树重建、内联 lambda 重新分配。
- 已存在的手工优化：`screener_view.py` `table_memo_ref` + `_resolve_table_data()`（基于行引用 + locale 的 memo，L1483-1492）；`virtual_table.py` `TableRow` hover 局部化（G5）。
- **滚动位置依赖**：`virtual_table.py` 用 `VScroll key=f"vt_{id(rows)}"` 重建列表重置滚动位置 —— memo 命中时复用原 list 对象则滚动位置保留为预期行为。

## 2. 方案检视修订历程

| 轮次 | 结论 |
|------|------|
| v1 方案两轮检视 | 对抗检视 CRITICAL：P1（virtual_table handler 稳化）hook 在列循环内调用违规 → **删除 P1**；columns_spec 不 memo（MetaDataManager 单例陈旧 M2）；rows_data/section 去 locale（当时误判行格式化不依赖 locale） |
| v2 落地后代码两轮检视 | **MAJOR-1（两轮一致）**：screener `_format_cell_value`（L208-235）依赖 `I18n.get`（prediction_win/loss、unit_yi/unit_wan），**v2 去 locale 是错误论证** → locale 切换后三分区与主表跨 locale 不一致 → **修复：section_formatted deps 补回 `get_observable_state().locale`** |
| v3 修复后两轮复核 | locale 类型为 str（ui/i18n.py L48），== 值比较命中/失效语义正确；与主表 `_resolve_table_data` locale 纬度一致；无 CRITICAL/MAJOR 残留 → **可合入** |

## 3. 最终落点（v3，仅两处）

### 落点 A：`ui/views/data_view.py` — TableViewerTab rows_data use_memo（L776-785）

```python
rows_data = ft.use_memo(
    lambda: _table_rows_to_paginated_rows(state.table_rows, state.table_columns),
    dependencies=[state.table_rows, state.table_columns],
)
```
- data_view 的 `_format_cell_value`（L176-185）为纯值/日期格式化（固定 `%Y-%m-%d`），**不依赖 locale** → deps 不含 locale 正确。
- columns_spec/table_options/filter_col_options 不 memo（避免单例陈旧）；SQL 结果路径不纳入（B1 聚焦表浏览器，SQL ≤100 行无感知差异）。

### 落点 B：`ui/views/screener_view.py` — 三分区 rows 格式化 use_memo（L2302-2316）

```python
section_formatted = ft.use_memo(
    lambda: {
        "recommended": _format_rows(state.ai_recommended_rows, _visible_cols),
        "excluded": _format_rows(state.ai_excluded_rows, _visible_cols),
        "failed": _format_rows(state.ai_failed_rows, _visible_cols),
    },
    dependencies=[
        state.ai_recommended_rows,
        state.ai_excluded_rows,
        state.ai_failed_rows,
        _visible_cols,
        get_observable_state().locale,
    ],
)
```
- **deps 含 locale（v3 修复）**：`_format_cell_value`（L208-235）经 `I18n.get` 渲染 prediction WIN/LOSS 文案与成交量单位（unit_yi/unit_wan），locale 切换须失效重建；与主表 `_resolve_table_data`（L1488）locale 比较键完全一致。

## 4. 不做的事（YAGNI / 微创）

- 不批量改写全 ui/ 的 use_state / 内联 lambda。
- virtual_table 不引入 hooks（P1 已删）、不重构窗口化（B3 受管债务）。
- columns_spec 不 memo（单例外部态陈旧风险）。
- 不做埋点式"测量"（引用稳定性推理 + 行为不回归验证代替）。

## 5. DoD

- [x] use_memo 使用数 0 → 2；Grep 可查
- [ ] ruff check / format / pyright / pre-commit 通过
- [ ] 相关单测与全量回归绿（tests/unit/ui contract、viewmodel 等）
- [ ] 行为不回归：rows_data/section 输出与重构前一致（纯 memo 包装）

## 6. 检视记录

- 方案两轮检视（v1→v2）：无真实缺陷，修订 3 项（P1 删、columns_spec 不 memo、locale 论证）
- 代码两轮检视（v2）：MAJOR-1 screener locale 陈旧 → 修复
- 修复后两轮复核（v3）：无真实问题，可合入