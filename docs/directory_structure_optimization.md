# 目录结构优化方案

## 一、当前结构分析

### 1.1 现有目录结构

```
astock_screener/
├── .github/                 # GitHub 配置
├── .idea/                   # IDE 配置
├── .pytest_cache/           # Pytest 缓存
├── .ruff_cache/             # Ruff 缓存
├── .venv/                   # 虚拟环境
├── .vscode/                 # VSCode 配置
├── __pycache__/             # Python 缓存
├── alembic/                 # 数据库迁移
├── assets/                  # 静态资源
├── data/                    # 数据层（职责过重）
│   ├── daos/               # DAO 层
│   ├── mixins/             # Mixin 类
│   ├── services/           # 数据服务 ← 与顶层 services/ 混淆
│   ├── sync_strategies/    # 同步策略 ← 与顶层 strategies/ 混淆
│   └── (10+ 顶层文件)       # 文件过多
├── docs/                    # 文档
├── logs/                    # 日志
├── models/                  # ⚠️ 定位不清（AI模型？数据模型？）
├── scripts/                 # 脚本
├── services/                # 业务服务 ← 与 data/services/ 混淆
├── strategies/              # 选股策略 ← 与 data/sync_strategies/ 混淆
├── tests/                   # 测试
├── ui/                      # UI 层（结构良好）
│   ├── components/         # UI 组件
│   ├── viewmodels/         # 视图模型
│   └── views/              # 视图
└── utils/                   # 工具函数
```

### 1.2 问题清单

| 问题 | 严重程度 | 说明 |
|------|---------|------|
| `data/` 职责过重 | 🔴 高 | 包含 DAO、Services、Strategies、Mixins 等，违反单一职责原则 |
| `services/` 命名冲突 | 🟠 中 | `data/services/` 与顶层 `services/` 容易混淆，导入时容易出错 |
| `strategies/` 命名冲突 | 🟠 中 | `data/sync_strategies/` 与顶层 `strategies/` 性质不同但命名相似 |
| `models/` 定位不清 | 🟡 低 | 不清楚是 AI 模型文件还是数据模型代码 |
| 缺少领域层 | 🟠 中 | 业务逻辑分散在多处，缺少统一的领域模型层 |
| `data/` 文件过多 | 🟡 低 | 顶层有 10+ 个文件，难以快速定位 |

---

## 二、推荐的最佳实践结构

### 2.1 分层架构（DDD 风格）

```
astock_screener/
├── src/                           # 可选：使用 src 布局避免导入问题
│   │
│   ├── domain/                    # 领域层（核心业务逻辑）
│   │   ├── models/               # 领域模型（纯 Python 类，无 ORM）
│   │   ├── services/             # 领域服务（交易日历、选股逻辑）
│   │   ├── strategies/           # 选股策略
│   │   └── events/               # 领域事件
│   │
│   ├── infrastructure/            # 基础设施层
│   │   ├── database/             # 数据库相关
│   │   │   ├── daos/            # DAO 实现
│   │   │   ├── models.py        # ORM 模型（原 data/models.py）
│   │   │   └── migrations/      # Alembic 迁移
│   │   ├── external/             # 外部服务
│   │   │   ├── tushare/         # Tushare 客户端
│   │   │   └── ai/              # AI 服务客户端
│   │   ├── cache/                # 缓存实现
│   │   └── persistence/          # 持久化相关
│   │
│   ├── application/               # 应用层
│   │   ├── services/             # 应用服务（任务管理、同步调度）
│   │   ├── sync/                 # 数据同步策略
│   │   └── dtos/                 # 数据传输对象
│   │
│   └── presentation/              # 表示层
│       └── ui/                   # UI 组件
│           ├── components/       # 可复用组件
│           ├── viewmodels/       # 视图模型
│           └── views/            # 视图页面
│
├── tests/                         # 测试（镜像 src 结构）
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── docs/                          # 文档
├── scripts/                       # 运维脚本
├── ai_models/                     # AI 模型文件（非代码，原 models/）
├── logs/                          # 日志文件
├── assets/                        # 静态资源
└── config/                        # 配置文件
```

### 2.2 各层职责说明

| 层级 | 职责 | 依赖方向 |
|------|------|---------|
| Domain | 核心业务逻辑、领域模型、策略 | 无外部依赖 |
| Infrastructure | 数据库、外部 API、缓存 | 依赖 Domain 接口 |
| Application | 用例编排、任务调度、同步 | 依赖 Domain + Infrastructure |
| Presentation | UI 展示、用户交互 | 依赖 Application |

---

## 三、渐进式改进方案

考虑到改动成本和风险，建议分阶段实施：

### 3.1 阶段一：重命名消除混淆（低成本，1-2 天）

**目标**：消除命名冲突，不改变代码逻辑

**改动清单**：

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `data/services/` | `data/domain_services/` | 区分领域服务与应用服务 |
| `data/sync_strategies/` | `data/sync/` | 简化命名，避免与选股策略混淆 |
| `models/` | `ai_models/` | 明确是 AI 模型文件（非代码） |

**执行步骤**：
1. 创建新目录
2. 移动文件并更新导入
3. 全局搜索替换导入路径
4. 运行测试验证

### 3.2 阶段二：拆分 data/ 目录（中成本，3-5 天）

**目标**：按职责拆分 `data/` 目录

**改动后结构**：

```
data/
├── persistence/          # 持久化层
│   ├── daos/            # DAO 实现
│   ├── models.py        # ORM 模型
│   └── database_manager.py
│
├── external/             # 外部服务
│   ├── tushare_client.py
│   └── news_fetcher.py
│
├── cache/                # 缓存
│   └── cache_manager.py
│
├── sync/                 # 数据同步
│   ├── base.py
│   ├── financial.py
│   ├── historical.py
│   └── ...
│
├── domain_services/      # 领域服务
│   └── trade_calendar_service.py
│
└── constants.py          # 常量定义
```

### 3.3 阶段三：引入领域层（高成本，1-2 周，可选）

**目标**：实现真正的分层架构

**主要工作**：
1. 抽取领域模型（纯 Python 类，无 ORM 依赖）
2. 定义领域服务接口
3. 重构策略类为纯领域逻辑
4. 实现依赖注入

**收益**：
- 业务逻辑与技术实现解耦
- 提高可测试性
- 便于未来扩展（如更换数据库、API）

---

## 四、风险评估与缓解措施

### 4.1 风险清单

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 导入路径错误 | 高 | 中 | 使用 IDE 重构功能 + 全局搜索验证 |
| 测试失败 | 中 | 高 | 每次改动后运行全量测试 |
| 遗漏导入更新 | 中 | 中 | 使用 `grep` 搜索旧路径 |
| 团队成员困惑 | 低 | 低 | 更新文档 + 代码评审 |

### 4.2 验证清单

每个阶段完成后需验证：

- [ ] 所有导入路径正确
- [ ] 全量测试通过（`pytest tests/`）
- [ ] 类型检查通过（`pyright`）
- [ ] 代码检查通过（`ruff check`）
- [ ] 应用可正常启动
- [ ] 核心功能可正常使用

---

## 五、执行建议

### 5.1 推荐执行顺序

```
阶段一（必须）→ 阶段二（推荐）→ 阶段三（可选）
```

### 5.2 时间规划

| 阶段 | 工作量 | 建议时间 |
|------|--------|---------|
| 阶段一 | 低 | 1-2 天 |
| 阶段二 | 中 | 3-5 天 |
| 阶段三 | 高 | 1-2 周 |

### 5.3 注意事项

1. **每次只改一个模块**：避免大范围改动导致难以定位问题
2. **保持向后兼容**：可以使用 `__init__.py` 重导出旧路径
3. **及时更新文档**：改动后立即更新相关文档
4. **代码评审**：每个阶段完成后进行代码评审

---

## 六、参考资源

- [Python Application Layouts](https://realpython.com/python-application-layouts/)
- [Domain-Driven Design in Python](https://www.cosmicpython.com/)
- [Clean Architecture in Python](https://github.com/cosmic-python/code)

---

## 七、变更记录

| 日期 | 版本 | 变更内容 |
|------|------|---------|
| 2026-03-22 | v1.0 | 初始版本 |
