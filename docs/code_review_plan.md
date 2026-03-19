# AStockScreener 代码检视计划

> 基于 `code_review_guidelines.md` 制定的系统性代码检视方案
> 
> 最后更新：2026-03-19

---

## 一、检视概述

### 1.1 检视目标

对 AStockScreener 项目进行全方位代码质量审查，确保：
- 架构设计符合 `architecture_principles.md` 规范
- 代码质量满足 `code_review_guidelines.md` 的 8 大防区、59 条检查项
- 消除潜在的技术债务与安全隐患

### 1.2 检视范围

| 模块 | 目录 | 优先级 |
|------|------|--------|
| 策略引擎 | `strategies/` | P0 |
| 数据访问层 | `data/daos/`, `data/cache_manager.py` | P0 |
| 数据源层 | `data/tushare_client.py` | P0 |
| 服务层 | `services/` | P1 |
| UI 层 | `ui/views/`, `ui/components/` | P1 |
| 工具层 | `utils/` | P2 |

---

## 二、检视阶段规划

### 阶段一：静态代码扫描 (Day 1)

**目标**：使用自动化工具快速定位问题

| 检查项 | 工具 | 命令 |
|--------|------|------|
| 代码风格 | Ruff | `ruff check .` |
| 类型检查 | MyPy | `mypy data/ services/ utils/` |
| 安全扫描 | Ruff (S规则) | `ruff check . --select S` |
| 导入清理 | Ruff | `ruff check . --select F401,F841` |

**预期产出**：
- 静态扫描报告
- 可自动修复的问题清单
- 需人工审查的问题清单

---

### 阶段二：防区一检视 - 策略引擎与数据流 (Day 2-3)

**重点文件**：`strategies/`, `viewmodels/screener_view_model.py`, `quality_gate.py`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 1 | 数据质量门控遗漏 | 关键 | 搜索 `@require_quality` 装饰器覆盖情况 |
| 2 | ViewModel ↔ Strategy 契约断裂 | 关键 | 检查 `context.get()` 与上游数据装载 |
| 3 | 策略注册与装载断链 | 关键 | 验证 `all_strategies.py` 引用完整性 |
| 4 | 未来函数穿越 | 关键 | 检查 SQL 连表使用 `ann_date` vs `end_date` |
| 5 | Join 操作笛卡尔爆破 | 高 | 检查连接键是否覆盖联合主键 |
| 6 | NaN/Null 隐式穿透 | 高 | 检查 `.filter()` 后是否 `.drop_nulls()` |
| 7 | 价格复权对齐 | 高 | 检查历史 K 线使用复权价格列 |

**检查命令**：
```bash
# 搜索缺失质量门控的策略
grep -r "def filter\|def _filter_logic" strategies/ --include="*.py" -A 1 | grep -v "@require_quality"

# 检查未来函数风险
grep -r "end_date" strategies/ data/ --include="*.py" | grep -v "ann_date"
```

---

### 阶段三：防区二检视 - 本地存储与字典抽象 (Day 4-5)

**重点文件**：`daos/`, `cache_manager.py`, `data_dictionary.py`, `models.py`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 8 | 无脑全量更新 | 高 | 检查是否使用 `_save_upsert` |
| 9 | 异步并发 DB 锁死 | 高 | 检查 `async with engine.begin()` 包裹 |
| 10 | 维护锁屏蔽 | 高 | 检查 `clear_cache` 是否触发 `_maintenance_event` |
| 11 | 表结构升级静默失败 | 关键 | 检查 `_check_and_update_schema` 补丁 |
| 12 | 数据字典缺位 | 中 | 检查 `data_dictionary.py` 字段注册 |
| 13 | 量纲单位换算 | 高 | 检查市值单位转换 `/ 10000` |
| 14 | 索引缺位 | 关键 | 使用 `EXPLAIN ANALYZE` 验证查询计划 |
| 14a | ORM 列类型对齐 | 高 | 检查 `Column(Date)` vs `Column(DateTime)` |
| 14b | DAO 方法返回类型一致性 | 中 | 检查同类方法返回类型 |
| 14c | 原始 SQL 参数类型安全 | 高 | 检查 `_read_db`/`_write_db` 参数类型 |

**检查命令**：
```bash
# 搜索 DELETE + INSERT 模式
grep -r "DELETE FROM\|DROP TABLE" data/ --include="*.py" -B 2 -A 2

# 检查索引定义
grep -r "Index\|INDEX" data/models.py data/schema.sql
```

---

### 阶段四：防区三检视 - Flet 大前端工程 (Day 6-7)

**重点文件**：`ui/views/`, `ui/components/`, `ui/i18n.py`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 15 | PubSub 订阅泄漏 | 关键 | 检查 `_on_mount` 与 `_on_unmount` 配对 |
| 16 | 异步重绘 AssertionError | 高 | 检查 `if self.page:` 安全校验 |
| 17 | 路由栈迷失 | 高 | 检查 `page.views.clear()` 使用 |
| 18 | __init__ 滥用 | 高 | 检查 `__init__` 中的 `self.page` 访问 |
| 19 | UI 主线程堵塞 | 关键 | 检查 `page.run_task()` 委托 |
| 20 | 高频重绘 | 中 | 检查循环中的 `.update()` 调用 |
| 21 | 巨量 DOM 渲染 | 高 | 检查是否使用 `VirtualTable` |
| 22 | 硬编码中文 | 中 | 搜索 `ft.Text("` 中的中文 |
| 23 | 双语词典对齐 | 高 | 检查 `en`/`zh` 字典键一致性 |
| 24 | 多语言字典冗余 | 低 | 检查重复翻译文本 |

**检查命令**：
```bash
# 搜索 PubSub 订阅泄漏
grep -r "pubsub.subscribe\|subscribe_topic" ui/ --include="*.py" -A 10 | grep -v "unsubscribe"

# 搜索硬编码中文
grep -r "ft.Text(\"[^\"]*[\u4e00-\u9fa5]" ui/ --include="*.py"
```

---

### 阶段五：防区四检视 - AI 混合调用边界 (Day 8)

**重点文件**：`services/ai_service.py`, `services/local_model_manager.py`, `strategies/ai_strategy.py`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 25 | 漏斗筛选口径过大 | 关键 | 检查 AI 调用前的 `head(50)` 限制 |
| 26 | 幻觉结构化容错 | 高 | 检查 `json.loads` 的 try-except 包裹 |
| 27 | Prompt 注入防范 | 高 | 检查 `<news>` 等分隔符使用 |

**检查命令**：
```bash
# 搜索 AI 调用前的限制
grep -r "openai\|llm\|ai_service" strategies/ services/ --include="*.py" -B 5 -A 5 | grep -E "head|limit|[:50]"
```

---

### 阶段六：防区五检视 - 全局系统韧性 (Day 9-10)

**重点文件**：`main.py`, `data/tushare_client.py`, `utils/`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 28 | 单例生命周期 | 关键 | 检查 `if self._initialized: return` |
| 29 | 优雅关闭 | 关键 | 检查 `cleanup_resources` 注册 |
| 30 | 配置文件原子写入 | 关键 | 检查 `_save_json_atomically` 使用 |
| 31 | 配置结构断层 | 关键 | 检查 `DEFAULT_CONFIG` 与 `user_settings.json` 对齐 |
| 32 | 网络封禁自愈 | 高 | 检查重试次数与请求间隔配置 |
| 33 | 安全秘钥泄露 | 关键 | 检查日志中的 Token 打印 |
| 34 | 时区一致性 | 关键 | 检查 `get_now()` 替代 `datetime.now()` |
| 35 | 文件名安全 | 高 | 检查路径拼接的正则过滤 |
| 36 | 系统兜底 | 中 | 检查后台任务的 UI 反馈 |
| 37 | 日志规范 | 中 | 检查日志级别与上下文参数 |
| 38 | 并发安全 | 高 | 检查 `asyncio.Lock` 使用 |

**检查命令**：
```bash
# 搜索原生 datetime.now() 调用
grep -r "datetime.now()\|datetime.datetime.now()" --include="*.py" | grep -v "time_utils.py"

# 搜索硬编码密码
grep -r "password\s*=\s*\"" --include="*.py"
```

---

### 阶段七：防区六检视 - 代码坏味道 (Day 11-12)

**重点文件**：`strategies/*.py`, `data/daos/`, `ui/views/`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 39 | 魔术数字硬编码 | 中 | 搜索 `.filter()` 中的裸数字 |
| 40 | 神级函数肥胖症 | 高 | 检查超过 80 行的函数 |
| 41 | 业务逻辑泄漏 | 高 | 检查 UI 回调中的 DataFrame 操作 |
| 42 | 安静吞咽异常 | 关键 | 检查 `except: pass` 模式 |
| 43 | 重复代码 | 中 | 使用 Ruff 检测或人工比对 |
| 44 | 僵尸代码 | 低 | 检查未使用的导入和函数 |

**检查命令**：
```bash
# 搜索安静吞咽异常
grep -r "except.*:\s*pass" --include="*.py"

# 搜索大函数 (超过 80 行)
find . -name "*.py" -exec awk 'BEGIN{c=0;f=""} /^def |^async def /{if(c>80)print f": "c;c=0;f=$0} {c++} END{if(c>80)print f": "c}' {} \;
```

---

### 阶段八：防区七检视 - 日期时间类型一致性 (Day 13-14)

**重点文件**：`utils/time_utils.py`, `data/daos/*.py`, `data/sync_strategies/*.py`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 45 | strptime 接收非字符串 | 关键 | 检查 `strptime` 调用前的类型检查 |
| 46 | date vs str 混合比较 | 关键 | 搜索 `!= "` 和 `== "` 日期比较 |
| 47 | YYYYMMDD 字符串写入 Date 列 | 关键 | 检查 DAO 写入参数类型 |
| 48 | parse_date 使用规范 | 中 | 检查是否统一使用 `parse_date()` |
| 49 | 时区感知 datetime 写入 | 高 | 检查 `.replace(tzinfo=None)` |
| 50 | ORM 列类型对齐 | 高 | 检查 `Column(Date)` vs `Column(DateTime)` |
| 51 | set[date] 与 set[str] 差集失效 | 高 | 检查集合运算的类型一致性 |

**检查命令**：
```bash
# 搜索原生 strptime 调用
grep -r "strptime" --include="*.py" | grep -v "time_utils.py" | grep -v "parse_date"

# 搜索日期字符串比较
grep -rE "!= \"[0-9]{8}\"|== \"[0-9]{8}\"" --include="*.py"
```

---

### 阶段九：防区八检视 - 静态代码检查工具 (Day 15)

**重点工具**：`ruff`, `mypy`, `pre-commit`

| 序号 | 检查项 | 严重级别 | 检查方法 |
|------|--------|----------|----------|
| 52 | Ruff 代码风格检查 | 关键 | `ruff check .` |
| 53 | MyPy 类型检查 | 关键 | `mypy data/ services/ utils/` |
| 54 | pre-commit 钩子配置 | 高 | 检查 `.pre-commit-config.yaml` |

**检查命令**：
```bash
# Ruff 完整检查
ruff check . --output-format=grouped

# MyPy 类型检查
mypy data/ services/ utils/ --ignore-missing-imports

# 安全规则检查
ruff check . --select S --output-format=grouped
```

---

## 三、检视产出物

### 3.1 检视报告模板

```markdown
# AStockScreener 代码检视报告

## 检视概况
- 检视日期：YYYY-MM-DD
- 检视范围：[模块列表]
- 检视人员：[姓名]

## 问题清单

### 关键问题 (P0)
| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|
| 1 | xxx.py | 123 | ... | ... |

### 高优先级问题 (P1)
| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|

### 中优先级问题 (P2)
| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|

## 统计汇总
- 关键问题：X 个
- 高优先级：X 个
- 中优先级：X 个
- 低优先级：X 个

## 改进建议
[整体改进建议]
```

### 3.2 问题跟踪

所有发现的问题需记录到 Issue 跟踪系统，标签分类：
- `bug`: 功能缺陷
- `security`: 安全漏洞
- `performance`: 性能问题
- `code-quality`: 代码质量
- `documentation`: 文档问题

---

## 四、检视检查清单

### 4.1 检视前准备

- [ ] 确认检视范围与优先级
- [ ] 准备检视环境（Python 环境、工具安装）
- [ ] 读取最新的 `architecture_principles.md` 和 `code_review_guidelines.md`
- [ ] 了解近期代码变更（`git log --oneline -20`）

### 4.2 检视中执行

- [ ] 按阶段顺序执行检视
- [ ] 记录所有发现的问题
- [ ] 对关键问题进行根因分析
- [ ] 提出具体的修复建议

### 4.3 检视后跟进

- [ ] 输出检视报告
- [ ] 创建 Issue 跟踪问题
- [ ] 与开发团队沟通修复方案
- [ ] 跟踪修复进度

---

## 五、附录

### 5.1 快速检查脚本

```bash
#!/bin/bash
# quick_check.sh - 快速代码质量检查

echo "=== Ruff 代码风格检查 ==="
ruff check . --output-format=grouped

echo ""
echo "=== MyPy 类型检查 ==="
mypy data/ services/ utils/ --ignore-missing-imports

echo ""
echo "=== 安全规则检查 ==="
ruff check . --select S --output-format=grouped

echo ""
echo "=== 未使用导入检查 ==="
ruff check . --select F401,F841 --output-format=grouped

echo ""
echo "=== 原生 datetime.now() 检查 ==="
grep -r "datetime.now()\|datetime.datetime.now()" --include="*.py" | grep -v "time_utils.py" | grep -v "__pycache__"

echo ""
echo "=== 安静吞咽异常检查 ==="
grep -r "except.*:\s*pass" --include="*.py" | grep -v "__pycache__"
```

### 5.2 参考文档

| 文档 | 说明 |
|------|------|
| [architecture_principles.md](architecture_principles.md) | 架构设计原则 |
| [code_review_guidelines.md](code_review_guidelines.md) | 代码检视指南 |
| [code_review_plan.md](code_review_plan.md) | 本文档 |
