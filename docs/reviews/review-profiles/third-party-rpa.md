# 第三方集成与浏览器自动化专项 Profile

> 加载方式：由 [ai-review.md §6](../ai-review.md#6-风险信号--专项-profile-触发表) 触发。只补充通用维度的增量风险。

## 检视要点

- 是否优先使用稳定契约或正式 API；
- 页面定位是否稳健，失败时是否安全停止；
- 身份、会话过期、权限变化和交互式挑战；
- 重复提交、假成功和响应丢失；
- 幂等标识、操作记录和执行状态核对；
- 截图、日志和制品是否包含敏感数据；
- 补偿、回滚和人工接管路径。

## 项目特定（AStockScreener）

- E2E 测试须本地化外部资源（字体/CanvasKit）到 mock_assets/，避免网络依赖
- E2E 测试 intercept_external route 须满足缓存资源并 abort 其他
- E2E 测试 Dropdown 选择须用 JavaScript strategy 检查 closest('group') aria-label 精度，避免 CSS 选择器误匹配
