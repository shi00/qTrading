# 依赖管理与 PyInstaller 打包

> 来源：从 CONTRIBUTING.md 迁移

### 依赖管理

- `flet` / `flet-desktop` / `flet-charts` 三个独立包，版本以 `==` 精确锁定（锁定值见 [`pyproject.toml`](./pyproject.toml) 的 `dependencies` 中 `flet` / `flet-desktop` / `flet-charts` 三项）
- `flet-charts` 是 V1 拆分出的图表控件独立包，新增图表控件必须 `import flet_charts as fch`
- 版本锁定策略：`==` 精确锁定，避免 minor 版本间的 API 漂移（V1 处于 alpha/beta 阶段）
- 升级 Flet 版本时，三个包必须同步升级

### PyInstaller 打包

[`AStockScreener.spec`](./AStockScreener.spec) 的 `hiddenimports` 列表必须含 `flet` / `flet_desktop` / `flet_charts` 三项：

- `flet_charts` 是 V1 新增的独立模块，遗漏会导致打包产物 `import flet_charts` 报 ImportError
- `flet_core` / `flet_desktop` 在 V1 已合并入 `flet`，但保守保留 `flet_desktop` 以兼容桌面打包路径
- 新增 flet 相关 import 时，同步检查 spec 文件的 `hiddenimports` 是否覆盖
