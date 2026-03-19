# AStockScreener 全身代码检视方案

> 基于 `code_review_guidelines.md` 8 大防区、70 条检查项制定的系统性代码审查计划

---

## 一、检视概述

### 1.1 检视目标

- **发现潜在缺陷**：识别可能导致运行时错误、数据损坏或安全漏洞的代码
- **提升代码质量**：消除技术债务，统一代码风格
- **确保架构一致性**：验证各层职责清晰，契约完整
- **建立质量基线**：为后续迭代提供可追溯的质量标准

### 1.2 检视范围

| 模块 | 文件数 | 核心文件 |
|------|--------|----------|
| 策略引擎 | 12 | `strategies/*.py`, `viewmodels/screener_view_model.py` |
| 数据存储 | 18 | `data/daos/*.py`, `data/cache_manager.py`, `data/models.py` |
| 前端工程 | 15 | `ui/views/*.py`, `ui/components/*.py` |
| AI 服务 | 5 | `services/ai_service.py`, `strategies/ai_*.py` |
| 系统韧性 | 12 | `main.py`, `utils/*.py`, `data/tushare_client.py` |
| 测试用例 | 22 | `tests/*.py` |

### 1.3 检视周期

| 阶段 | 内容 | 预计时间 |
|------|------|----------|
| 第一阶段 | 静态代码扫描 (防区八) | 0.5 天 |
| 第二阶段 | 核心模块深度审查 (防区一~五) | 2 天 |
| 第三阶段 | 代码质量与类型一致性 (防区六~七) | 1 天 |
| 第四阶段 | 问题修复与验证 | 1 天 |
| **总计** | | **4.5 天** |

---

## 二、防区一：策略引擎与数据流转换

### 2.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 1 | 数据质量门控遗漏 | Grep 搜索 `@require_quality` 装饰器覆盖情况 | P0 |
| 2 | ViewModel ↔ Strategy 契约断裂 | 对比 `context.get()` 调用与 `run_strategy()` 传参 | P0 |
| 3 | 策略注册断链 | 检查 `all_strategies.py` 导入完整性 | P0 |
| 4 | 未来函数穿越 | 搜索财报查询中的 `ann_date` vs `end_date` | P0 |
| 5 | Join 笛卡尔爆破 | 审查 Polars join 操作的连接键 | P1 |
| 6 | NaN/Null 隐式穿透 | 检查 filter 操作后的 null 处理 | P1 |
| 7 | 价格复权对齐 | 确认策略使用 `adj_close` 而非 `close` | P1 |

### 2.2 扫描命令

```bash
# 检查策略类是否都有 @require_quality 装饰器
grep -rn "class.*Strategy" strategies/ --include="*.py" > strategy_classes.txt
grep -rn "@require_quality" strategies/ --include="*.py" > quality_decorators.txt

# 检查未来函数风险 (财报日期字段使用)
grep -rn "end_date" strategies/ data/ --include="*.py" | grep -v "ann_date"

# 检查 Join 操作
grep -rn "\.join(" strategies/ data/ --include="*.py"
```

### 2.3 重点文件

```
strategies/
├── fundamental.py      # 基本面策略，需检查财报日期字段
├── market.py           # 行情策略，需检查复权价格
├── oversold_strategy.py # 超跌策略
├── ai_strategy.py      # AI 策略
├── all_strategies.py   # 策略注册入口
└── base_strategy.py    # 策略基类

ui/viewmodels/
└── screener_view_model.py  # ViewModel，需检查 context 传递
```

### 2.4 预期产出

- [ ] 策略装饰器覆盖报告
- [ ] ViewModel-Strategy 契约矩阵
- [ ] 未来函数风险清单
- [ ] Join 操作安全性评估

---

## 三、防区二：本地存储与字典抽象

### 3.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 8 | Upsert 原则遵守 | 搜索 `DELETE` + `INSERT` 组合模式 | P0 |
| 9 | 异步并发 DB 锁死 | 检查 `engine.begin()` 包裹情况 | P0 |
| 10 | 维护锁屏蔽 | 搜索 `_maintenance_event` 使用 | P1 |
| 11 | Schema Migration 完整性 | 对比 models.py 与 Alembic 迁移脚本 | P0 |
| 12 | 数据字典缺位 | 对比 SQL 字段与 `data_dictionary.py` | P1 |
| 13 | 单位换算陷阱 | 搜索市值相关计算中的 `/ 10000` | P1 |
| 14-14h | 索引与查询优化 | EXPLAIN 分析高频查询 | P0 |
| 14a-c | ORM 类型安全 | 检查 Column 定义与参数类型 | P0 |

### 3.2 扫描命令

```bash
# 检查危险的全量删除模式
grep -rn "DELETE FROM" data/ --include="*.py"
grep -rn "DROP TABLE" data/ --include="*.py"

# 检查事务包裹
grep -rn "engine.begin()" data/ --include="*.py"
grep -rn "async with.*engine" data/ --include="*.py"

# 检查索引定义
grep -rn "Index\|INDEX" data/models.py data/daos/ --include="*.py"

# 检查原始 SQL 参数化
grep -rn "_read_db\|_write_db" data/ --include="*.py"
```

### 3.3 重点文件

```
data/
├── daos/
│   ├── base_dao.py       # DAO 基类，Upsert 逻辑
│   ├── quote_dao.py      # 行情 DAO，高频查询
│   ├── stock_dao.py      # 股票 DAO
│   └── financial_dao.py  # 财务 DAO
├── models.py             # ORM 模型定义
├── cache_manager.py      # 缓存管理
├── data_dictionary.py    # 数据字典
└── database_manager.py   # 数据库管理

alembic/versions/         # 迁移脚本
```

### 3.4 预期产出

- [ ] DAO 层事务安全报告
- [ ] 索引覆盖率分析
- [ ] 数据字典完整性检查
- [ ] EXPLAIN 执行计划报告

---

## 四、防区三：Flet 大前端工程

### 4.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 15 | PubSub 订阅泄漏 | 搜索 `subscribe` 与 `unsubscribe` 配对 | P0 |
| 16 | 异步重绘安全 | 检查 `if self.page:` 保护 | P0 |
| 17 | 路由栈管理 | 检查 `page.views.clear()` 使用 | P1 |
| 18 | `__init__` 滥用 | 检查 `__init__` 中的 `self.page` 访问 | P1 |
| 19 | UI 线程阻塞 | 搜索点击回调中的耗时操作 | P0 |
| 20 | 高频重绘优化 | 检查循环中的 `.update()` 调用 | P1 |
| 21 | DOM 渲染爆炸 | 检查 ListView/Column 中的大列表 | P1 |
| 22 | 硬编码中文 | 搜索 `ft.Text("` 中的中文字符 | P2 |
| 23 | 双语词典对齐 | 对比 i18n.py 中 en/zh 字典 | P1 |
| 24 | i18n 冗余 | 检查重复翻译值 | P2 |

### 4.2 扫描命令

```bash
# 检查 PubSub 订阅配对
grep -rn "pubsub.subscribe\|subscribe_topic" ui/ --include="*.py" > subscribe.txt
grep -rn "unsubscribe\|unsubscribe_topic" ui/ --include="*.py" > unsubscribe.txt

# 检查异步重绘安全
grep -rn "\.update()" ui/ --include="*.py" -A 2 -B 2

# 检查硬编码中文
grep -rn 'ft.Text("' ui/ --include="*.py" | grep -P '[\x{4e00}-\x{9fff}]'

# 检查 UI 线程阻塞风险
grep -rn "def on_click\|async def on_" ui/ --include="*.py" -A 10
```

### 4.3 重点文件

```
ui/
├── views/
│   ├── home_view.py          # 首页
│   ├── screener_view.py      # 选股页
│   ├── data_view.py          # 数据页
│   ├── settings_view.py      # 设置页
│   └── task_center_view.py   # 任务中心
├── components/
│   ├── virtual_table.py      # 虚拟表格
│   ├── market_dashboard.py   # 行情面板
│   └── news_feed.py          # 新闻订阅
├── viewmodels/
│   └── screener_view_model.py
└── i18n.py                   # 国际化
```

### 4.4 预期产出

- [ ] PubSub 订阅泄漏报告
- [ ] UI 线程阻塞风险清单
- [ ] 硬编码字符串修复建议
- [ ] i18n 完整性报告

---

## 五、防区四：AI 混合调用边界

### 5.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 25 | 漏斗筛选口径 | 检查 AI 调用前的 `head()` 限制 | P0 |
| 26 | JSON 幻觉容错 | 检查 `json.loads` 的 try-except 包裹 | P0 |
| 27 | Prompt 注入防范 | 检查分隔符使用 | P1 |

### 5.2 扫描命令

```bash
# 检查 AI 调用前的数量限制
grep -rn "ai_service\|openai\|llm" strategies/ services/ --include="*.py" -B 5 -A 5

# 检查 JSON 解析安全
grep -rn "json.loads" strategies/ services/ --include="*.py" -B 2 -A 2

# 检查 Prompt 分隔符
grep -rn "prompt\|system_message" strategies/ services/ --include="*.py"
```

### 5.3 重点文件

```
services/
├── ai_service.py           # AI 服务核心
└── local_model_manager.py  # 本地模型管理

strategies/
├── ai_strategy.py          # AI 策略
├── ai_mixin.py             # AI 混入
└── strategy_prompts.py     # Prompt 模板
```

### 5.4 预期产出

- [ ] AI 调用安全评估
- [ ] Prompt 注入风险报告
- [ ] JSON 解析容错检查

---

## 六、防区五：全局系统韧性与容灾

### 6.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 28 | 单例生命周期 | 检查 `_initialized` 防重入 | P0 |
| 29 | 优雅关闭 | 检查 `atexit` 和 `shutdown` 注册 | P0 |
| 30 | 配置原子写入 | 检查 `_save_json_atomically` 使用 | P0 |
| 31 | 配置结构对齐 | 对比 DEFAULT_CONFIG 与实际使用 | P0 |
| 32 | 断点续传 | 检查重试机制和切片逻辑 | P1 |
| 33 | 凭证泄露 | 搜索日志中的 token/key 打印 | P0 |
| 34 | 时区一致性 | 搜索 `datetime.now()` 使用 | P0 |
| 35 | 路径穿越 | 检查文件路径拼接安全性 | P0 |
| 36 | 异常兜底 | 检查后台任务的异常处理 | P1 |
| 37 | 日志规范 | 检查日志级别和上下文 | P2 |
| 38-38c | 并发安全 | 检查锁和 `return_exceptions` | P0 |

### 6.2 扫描命令

```bash
# 检查单例模式
grep -rn "_initialized\|_instance" data/ services/ utils/ --include="*.py"

# 检查原生 datetime.now() 使用
grep -rn "datetime\.now()\|datetime\.datetime\.now()" . --include="*.py" | grep -v "get_now()"

# 检查凭证泄露风险
grep -rn "token\|password\|secret\|api_key" . --include="*.py" | grep -i "logger\|print\|log"

# 检查并发安全
grep -rn "asyncio.gather" . --include="*.py" -A 1
grep -rn "Lock\|threading.Lock\|asyncio.Lock" . --include="*.py"

# 检查路径拼接
grep -rn "os.path.join\|f\".*{.*}\" " . --include="*.py" | grep -v "sanitize"
```

### 6.3 重点文件

```
main.py                     # 应用入口
utils/
├── config_handler.py       # 配置管理
├── scheduler_service.py    # 调度服务
├── security_utils.py       # 安全工具
└── time_utils.py           # 时间工具

data/
├── tushare_client.py       # Tushare 客户端
└── database_manager.py     # 数据库管理
```

### 6.4 预期产出

- [ ] 单例模式安全报告
- [ ] 凭证泄露风险评估
- [ ] 并发安全审计
- [ ] 时区使用一致性报告

---

## 七、防区六：量化系统代码坏味道

### 7.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 39 | 魔术数字 | 搜索 filter 中的硬编码数值 | P1 |
| 40 | 神级函数 | 统计函数行数 > 80 行 | P1 |
| 41 | UI 计算泄漏 | 检查 View 中的 DataFrame 操作 | P1 |
| 42 | 异常吞咽 | 搜索 `except.*pass` 模式 | P0 |
| 43 | 重复代码 | 代码相似度检测 | P2 |

### 7.2 扫描命令

```bash
# 检查魔术数字
grep -rn "\.filter.*>" strategies/ --include="*.py" | grep -v "context\|params\|constant"

# 检查异常吞咽
grep -rn "except.*:" . --include="*.py" -A 1 | grep "pass"

# 检查长函数 (需配合脚本)
python -c "
import ast
import os
for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as file:
                    tree = ast.parse(file.read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        lines = node.end_lineno - node.lineno + 1
                        if lines > 80:
                            print(f'{path}:{node.lineno} {node.name} ({lines} lines)')
            except: pass
"
```

### 7.3 预期产出

- [ ] 魔术数字清单
- [ ] 长函数重构建议
- [ ] 异常吞咽修复清单

---

## 八、防区七：日期时间类型一致性

### 8.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 45 | strptime 非字符串 | 搜索 `strptime` 调用 | P0 |
| 46 | date vs str 混合比较 | 检查日期比较操作 | P0 |
| 47 | 字符串写入 Date 列 | 检查 DAO 参数类型 | P0 |
| 48 | parse_date 规范 | 检查 `parse_date` 使用 | P1 |
| 49 | 时区感知 datetime | 检查 `.replace(tzinfo=None)` | P0 |
| 50 | ORM 类型对齐 | 检查 Column(Date/DateTime) | P0 |
| 51 | set 差集类型 | 检查集合运算中的类型一致性 | P1 |

### 8.2 扫描命令

```bash
# 检查 strftime/isoformat 使用 (已在之前修复中完成)
grep -rn "\.strftime\|\.isoformat" . --include="*.py" | grep -v "log_decorators\|test_"

# 检查 strptime 调用
grep -rn "strptime" . --include="*.py"

# 检查日期比较
grep -rn "!= \|== " . --include="*.py" | grep -E "date|time"

# 检查时区处理
grep -rn "tzinfo\|timezone" . --include="*.py"
```

### 8.3 预期产出

- [ ] 日期类型一致性报告
- [ ] strftime/isoformat 残留检查
- [ ] 时区处理规范性评估

---

## 九、防区八：静态代码检查工具

### 9.1 检查清单

| 编号 | 检查项 | 检查方法 | 优先级 |
|------|--------|----------|--------|
| 52 | Ruff 代码风格 | `ruff check .` | P0 |
| 53 | MyPy 类型检查 | `mypy data/ services/ strategies/` | P0 |
| 54 | Pre-commit 配置 | 检查 `.pre-commit-config.yaml` | P1 |
| 55 | CI/CD 门禁 | 检查 GitHub Actions 配置 | P1 |
| 56 | noqa 规范 | 检查 `# noqa` 使用原因 | P2 |
| 57 | 圈复杂度 | Ruff C901 规则 | P1 |
| 58 | Docstring 规范 | Ruff D 系列规则 | P2 |
| 59 | 安全漏洞 | Ruff S 系列规则 | P0 |

### 9.2 执行命令

```bash
# 安装依赖
pip install ruff mypy

# Ruff 检查
ruff check . --output-format=github > ruff_report.txt

# Ruff 自动修复
ruff check . --fix

# MyPy 类型检查
mypy data/ services/ strategies/ --ignore-missing-imports > mypy_report.txt

# 安全规则专项
ruff check . --select=S > security_report.txt
```

### 9.3 预期产出

- [ ] Ruff 检查报告
- [ ] MyPy 类型错误清单
- [ ] 安全漏洞扫描结果
- [ ] Pre-commit 配置建议

---

## 十、执行计划

### 10.1 第一阶段：静态扫描 (Day 1 上午)

```
09:00 - 10:00  安装 ruff/mypy，执行静态扫描
10:00 - 11:00  分析扫描结果，分类问题
11:00 - 12:00  生成初步报告
```

### 10.2 第二阶段：核心模块审查 (Day 1 下午 - Day 2)

```
Day 1 下午: 防区一 (策略引擎) + 防区二 (存储层)
Day 2 上午: 防区三 (前端) + 防区四 (AI)
Day 2 下午: 防区五 (系统韧性)
```

### 10.3 第三阶段：代码质量 (Day 3)

```
上午: 防区六 (代码坏味道) + 防区七 (日期类型)
下午: 汇总问题，生成修复优先级清单
```

### 10.4 第四阶段：修复验证 (Day 4)

```
上午: 修复 P0 级别问题
下午: 验证修复效果，更新文档
```

---

## 十一、问题分级标准

| 级别 | 定义 | 处理时限 |
|------|------|----------|
| **P0 - 致命** | 可能导致数据损坏、安全漏洞、运行时崩溃 | 立即修复 |
| **P1 - 严重** | 影响功能正确性、性能或可维护性 | 本周修复 |
| **P2 - 一般** | 代码风格、文档缺失等非功能性问题 | 下版本修复 |

---

## 十二、交付物清单

| 序号 | 交付物 | 格式 |
|------|--------|------|
| 1 | 静态扫描报告 | Markdown |
| 2 | 各防区问题清单 | Markdown |
| 3 | 修复优先级矩阵 | Excel/Markdown |
| 4 | 修复后验证报告 | Markdown |
| 5 | 代码质量改进总结 | Markdown |

---

## 附录：快速检查脚本

```bash
#!/bin/bash
# quick_check.sh - 快速代码检查脚本

echo "=== AStockScreener 代码快速检查 ==="

echo "\n[1/8] Ruff 代码风格检查..."
ruff check . --statistics

echo "\n[2/8] 安全漏洞扫描..."
ruff check . --select=S

echo "\n[3/8] 检查 datetime.now() 使用..."
grep -rn "datetime\.now()\|datetime\.datetime\.now()" . --include="*.py" | grep -v "get_now()" | grep -v ".venv"

echo "\n[4/8] 检查异常吞咽..."
grep -rn "except.*:" . --include="*.py" -A 1 | grep "pass" | head -20

echo "\n[5/8] 检查 strftime 残留..."
grep -rn "\.strftime\|\.isoformat" . --include="*.py" | grep -v "log_decorators\|test_"

echo "\n[6/8] 检查凭证泄露风险..."
grep -rn "token\|password\|secret" . --include="*.py" | grep -i "logger\|print" | head -10

echo "\n[7/8] 检查 PubSub 订阅配对..."
echo "订阅数: $(grep -r "subscribe" ui/ --include="*.py" | wc -l)"
echo "取消订阅数: $(grep -r "unsubscribe" ui/ --include="*.py" | wc -l)"

echo "\n[8/8] 检查长函数 (>80行)..."
python -c "
import ast, os
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ['.venv', '__pycache__', 'node_modules']]
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as file:
                    tree = ast.parse(file.read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        lines = node.end_lineno - node.lineno + 1
                        if lines > 80:
                            print(f'{path}:{node.lineno} {node.name} ({lines} lines)')
            except: pass
" | head -20

echo "\n=== 检查完成 ==="
```
