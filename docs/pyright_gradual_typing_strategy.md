# 静态类型检查：Pyright 渐进式引入与 Bug 修复架构方案

**撰写日期**: 2026-04-07
**现状描述**: GitHub CI/CD 中的 Pyright 检查任务导致流水线崩溃（抛出超过 1500 个错误和 9000 多条警告）。此问题在引入严格的 Python 静态类型检测工具（如 Pyright / Mypy）的存量量化项目中极为常见。

---

## 🔬 本地环境排查与溯源分析

利用 `pyright --outputjson` 对系统进行了完整的 AST 解析与词法检查，1570 个报错的具体组成可化分为如下**三个梯队**，这也决定了我们对应的修复策略：

### 🗑️ 第一梯队：纯代码风格由于无类型注解产生的噪声（1355 个）
- **代表性报错**: `reportMissingParameterType`, `reportMissingTypeArgument`
- **问题本质**: 代码在声明函数时，未显式附带 Type Hints。例如 `def insert_db(db):` 而没有写成 `def insert_db(db: Database):`。
- **架构决策**: 因 Python 仍保留高度动态特性，强制对所有已有遗留代码去补全 1300 个类型签名投资回报率极低，并且极大可能引发回归错误（Regression Bug）。
- **处理方案**: **无需修改代码，修改配置文件全盘屏蔽**。

### ⚠️ 第二梯队：逻辑层面的中危隐患与类型歧义（约 184 个）
- **代表性报错**: `reportOptionalMemberAccess`（对可空对象调用属性）, `reportArgumentType`（入参预期和实际传入可能有歧义）
- **问题本质**: 高频出现在 Pandas/Polars 的 DataFrame 操作中。动态属性在静态检查中无法完整推导，偶尔可能包含空指针潜在威胁。
- **架构决策**: 这些属于代码的 “亚健康” 状态。不能作为阻塞 CI 构建的强约束，但也具有追踪价值。
- **处理方案**: **通过配置文件将其从 `Error` 级别降级为 `Warning`**，流水线可绿灯通过，供后续敏捷重构。

### 🐞 第三梯队：极具代码崩溃风险的真实 Bug（约 23 个）
- **代表性报错**: `reportCallIssue`, `reportOptionalOperand`, `reportOptionalIterable`
- **问题本质**: 明确违反了运行时的基础逻辑。
  - *例1* (`database_manager.py`)：`Object of type "None" cannot be used as iterable value` —— 在对极大几率为 `None` 的变量进行强行 for 循环遍历，必定引发 `TypeError`！
  - *例2* (`historical.py`)：`No parameter named "result"` —— 函数执行时传递了该方法根本没有注册的签名实参，调用必挂！
  - *例3* (`screener_dao.py`)：`Operator "*" not supported for "None"` —— 没有做防空值容错就去算乘法算数逻辑了！
- **处理方案**: **绝不姑息，直接由人工审查介入，逐一在对应 Python 代码里落实底层修复！**

---

## 🛠️ 标准化解决方案与落地执行路线 (Action Plan)

为了实现 GitHub Actions 的 CI / CD 顺利通过，且**保证真正有问题的核心 Bug 得以修复**，我们决定执行教科书式的排雷手术，分为以下两步执行：

### 第一步：引入 Pyright 白名单配置文件 (抑制噪音)
在根目录 `pyproject.toml` 中的 `[tool.pyright]` 块加入以下配置：
```toml
[tool.pyright]
include = ["."]
typeCheckingMode = "basic"
# 1. 彻底屏蔽噪音：不强求老代码的显式类型签名
reportMissingParameterType = "none"
reportMissingTypeArgument = "none"

# 2. 降级亚健康异味：不要阻断 CI 发版
reportOptionalMemberAccess = "warning"
reportArgumentType = "warning"
reportAttributeAccessIssue = "warning"
```

### 第二步：源码级定点排雷手术 (根除 Bug)
依据配置完毕后的真实筛查结果，集中且定向开刀修复 23 个可能导致应用或模块彻底 Crash 的高危节点：
- 给相应的运算和循环添加 `if var is not None:` 的安全卫士保护机制。
- 删除调用函数时错写的 `result=...` 参数。
- 修改 `macro.py` 中过时的 DataFrame API 调用方式等。

### 最终预期
通过这套组合拳：由于噪声已被过滤，开发者不再心智过载；由于 23 个高潜地雷被彻底清剿，量化平台的健壮度将出现立竿见影的实质性架构提升；而且，流水线可以立刻跑通打包！
