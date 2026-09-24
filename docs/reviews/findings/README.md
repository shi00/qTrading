# 检视结论索引（Findings Index）

> **目的**（GOV-04）：检视**报告正文**（含长证据）为本地 gitignored 产物（落根目录 `reviews/`，不入版本控制），
> 为避免结论系统性丢失，把每条发现的**结论性信息**（id / ruleId / severity / location / summary / disposition / 判据）
> 按轮次结构化登记于此，新会话可一跳溯源、可跨机器追溯，且不携带代码片段与长证据（不违反 ADR-0002「正文不入库」的初衷）。
>
> **登记规则**：
> 1. 每次检视轮次（review01 ~ reviewNN / 专项轮次）产出的发现，须在 `docs/reviews/findings/` 下新增 `<轮次slug>.json`（符合
>    [review-result.schema.json](../review-result.schema.json)）或 `<轮次slug>.md` 精简登记（字段：`id` / `ruleId` / `severity` /
>    `location` / `summary` / `disposition` / `可验证判据` 七要素）；轮次的「未关闭发现」同步在 [../README.md](../README.md) 未关闭摘要维护（DS-04）。
> 2. 发现被关闭时，更新对应条目 `disposition`（open → resolved / waived），不回溯改写已归档报告正文。
> 3. 本文件为 findings/ 目录索引；新增 findings 条目必须在下方登记链接，否则被 `check_reviews_findings_index` 门禁拦截。
> 4. 现有轮次的旧结论（如 M3~M12「报告已丢失」）因无原始证据无法补录，保持缺失并在 [../README.md](../README.md) 轮次表标注。

## 已登记结论

| 轮次 | 结论文件 | 状态 |
|------|---------|------|
| review08 | [review08.md](./review08.md) | 进行中（C1 open；A2/A3 waived） |