# 误报 #001：Flet 框架自动转义被误报 XSS

## 输入

```python
import flet as ft

def build_greeting(user_input: str):
    # user_input 来自不可信前端
    return ft.Text(user_input)
```

## 期望发现

- **类别**：无发现（不应报告为 XSS 缺陷）
- **规则 ID**：
  - [ROUND3-01](../../ai-review.md)（反证：检查类型、schema、框架、数据库及上游保护）
  - [ROUND3-04](../../ai-review.md)（尝试寻找能推翻问题的证据）

## 评分要点

- **误报率**：若 AI 报告"XSS 漏洞"则为误报，指标失败
- **规则遵从率**：AI 应执行 [ROUND3-01] 反证步骤，确认 Flet 的 `ft.Text` 默认对文本内容做 HTML 转义，user_input 不会作为 HTML 解析
- **证据完整性**：若 AI 报告 XSS，必须说明为何 Flet 转义不够；若不报告，应在"已检查"中说明已验证框架转义

## 备注

陷阱：AI 若只看"user_input 来自不可信源"就报告 XSS，则未完成第三轮反证，违反 [ROUND3-01]。

Flet 文档：`ft.Text` 默认以纯文本渲染，不解析 HTML；若使用 `ft.Markdown` 才需考虑注入。
