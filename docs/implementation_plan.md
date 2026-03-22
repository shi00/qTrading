# 核心类型安全收严整改计划

## 现状分析
在我们收严了 [pyrightconfig.json](file:///d:/workspace/Quantitative%20Trading/astock_screener/pyrightconfig.json) 的规则（去除了对未注参、未知来源的宽松屏蔽，启用强检验）后，全量扫描瞬间截获了 **7000+** 行的输出（包含 Error 与 Warning）。

这些不合规的爆发点绝大多数并不能说明代码逻辑本身有 bug，而是由于 Python 生态的固有局限性引起的推断断层：
1. **数据科学生态的黑盒 (Unknown Types)**: 比如 Pandas 的 `DataFrame`，以及各种 Numpy 的数组切片、`itertuples()`。它们在编译期根本无法推断出里面有什么列，这导致了漫山遍野的 `Unknown Member` 警告。
2. **三方库缺乏存根文件 (Missing Stubs)**: Tushare API 的装配、Flet 的 UI 组件内部事件委托（如 `e.control.page`），它们的官方并无完美的 `.pyi` 类型描述。
3. **入参缺失显式注解 (Missing Parameter Type)**: 特别是在早期业务代码以及 `tests/` 测试桩里，`def do_something(data, code):` 没有补充 `: str`, `: int`，在严控下会直接以 Error 报出。

如果一天之内在全库强加成千上万个 `typing.cast` 或者 `# type: ignore`，这会极其严重地污染业务代码的阅读体验，这绝对不是您期望的“提升代码质量”的做法。

---

## Proposed Changes (渐进式护栏整改方案)

我建议分两步走，本期只做**核心投资回报率最高**的操作（高价值区域收严），对纯粹工具链的警告予以合理的分级隔离。

### 阶段一：扫清真实代码漏洞并重筑接口墙（本期建议立刻执行）
1. **消除代码库中的真 Error (Missing Parameter Type / Missing Type Argument)**
   - 我们将遍历 [data/](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/tushare_client.py#797-806), `services/`, `strategies/` 目录，对于那些在定义方法时丢失类型注解的参数，补充标准的 Python 类型注解（这极大提升了接口文档属性和接手该项目时的提示词顺滑度）。
2. **对测试文件豁免严苛审计 (Tests Exemption)**
   - 我们将在 [pyrightconfig.json](file:///d:/workspace/Quantitative%20Trading/astock_screener/pyrightconfig.json) 内部开辟对 `tests/` 目录的单独覆盖规则或利用子域配置，仅对核心源码强力发难，不对 Mock 和 monkeypatch 等不可导出的脏逻辑指手画脚。

### 阶段二：底层生态数据沙箱 (后续视精力迭代)
1. **DataFrame 边界防线化**
   - 可以在核心的 [BaseDao](file:///d:/workspace/Quantitative%20Trading/astock_screener/tests/test_dao_base.py#71-88) 处为 `DataFrame` 返回明确书写接口签名注释，将未知传递拦截在边界。
2. **UI 隔离**
   - Flet UI 并非安全重灾区，可单独放宽相关目录的推断阻断。

---

## User Review Required

> [!WARNING]
> 目前强管控配置下抛出了极大的异常，为了不让我们的精力浪费在为 Flet 和 Pandas 无意义地加上 [cast(Any, ...)](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/tushare_client.py#658-677) 转型上，您是否同意**我们仅着力修复 Parameter Type 缺失错误和泛型缺失错误，同时针对框架动态属性允许保留 Warning 级别但在 PR 时不卡点**？

## Verification Plan

### Automated Tests
1. 在修补完核心模块的类型签名后，执行一次全量自动化测试 `.venv\Scripts\python.exe -m pytest tests/`，确认 658 个用例依旧 100% 畅通，未破坏鸭子类型逻辑。
2. 执行局部核心业务的 Pyright 防护核验 `npx.cmd pyright data/ services/ strategies/`，确认核心链路不存在 Error 级别的严重崩离。
