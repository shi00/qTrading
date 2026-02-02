import logging
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

class I18n:
    """
    Internationalization support.
    Manages locale state and provides translated strings.
    """
    _locale = "zh"  # Enforce Chinese only
    _listeners = []
    
    STRINGS = {
        "zh": {

            "app_title": "A股智能选股助手 (Pro)",
            "nav_market": "市场概览",
            "nav_screener": "智能选股",
            "nav_analysis": "AI 分析",
            "nav_data": "数据中心",
            "nav_settings": "系统设置",
            
            "home_welcome": "欢迎使用 A股智能选股助手",
            "home_market_status": "市场状态",
            "home_indices": "主要指数",
            "home_live_news": "实时市场快讯",
            "news_load_more": "加载更多",
            
            "screener_title": "智能选股",
            "screener_strategies": "选股策略",
            "screener_run": "执行选股",
            "screener_results": "选股结果",
            "screener_export": "导出 CSV",
            
            "settings_title": "设置",
            "settings_general": "基本设置",
            "settings_language": "语言 / Language",
            "settings_theme": "主题",
            "settings_api": "API 设置",
            "settings_token": "Tushare Token",
            "settings_save": "保存设置",
            
            "status_ready": "就绪",
            "status_running": "运行中...",
            "status_running": "运行中...",
            "status_error": "错误",
            
            # Screener
            "screener_select_strategy": "选择策略",
            "screener_reload_data": "重载数据",
            "screener_auto_save": "自动保存复盘记录",
            "screener_ai_log_title": "AI 思考过程日志 (Real-time Reasoning)",
            "screener_ai_log_subtitle": "点击展开查看 AI 详细推理链",
            "screener_page_prev": "上一页",
            "screener_page_next": "下一页",
            "screener_page_info": "第 {current} 页 / 共 {total} 页",
            
            # Columns
            "col_ts_code": "代码",
            "col_name": "名称",
            "col_ai_score": "AI 评分",
            "col_price": "现价",
            "col_chg": "涨跌幅",
            "col_pe": "PE",
            "col_turnover": "换手率",
            "col_details": "详情",
            
            # Screener Status Messages
            "screener_no_strategy_hint": "请选择策略以查看逻辑说明",
            "screener_loading_data": "正在初始化/重载数据库...",
            "screener_data_loaded": "数据加载完成",
            "screener_load_failed": "加载失败: {error}",
            "screener_please_select": "请先选择策略！",
            "screener_strategy_not_found": "未找到该策略",
            "screener_syncing": "正在同步数据并执行 {name}...",
            "screener_first_run": "历史数据首次运行，自动同步...",
            "screener_data_ready": "数据准备完毕，请重新同步",
            "screener_ai_analyzing": "AI 分析中: {done}/{total} - {msg}",
            "screener_filter_error": "策略执行中出错: {error}",
            "screener_no_results": "未找到符合条件的股票",
            "screener_done": "筛选完成，共 {count} 只股票",
            "screener_saved": "(已保存)",
            "screener_exec_error": "执行中出错: {error}",
            "screener_view_details": "查看详情",
            
            # Strategy Names & Descriptions
            "strategy_ai_active_name": "AI 深度精选 (Beta)",
            "strategy_ai_active_desc": "AI 全自动选股：先用量化筛选活跃股，再由 DeepSeek/AI 深度阅读新闻和财报进行最终评分。",
            "strategy_value_name": "价值投资",
            "strategy_value_desc": "标准：市盈率 5-20倍 | 市净率 0.5-3倍 | 股息率 > 2% (寻找低估值蓝筹)",
            "strategy_growth_name": "高成长策略",
            "strategy_growth_desc": "标准：营收增长 > 20% | 净利增长 > 25% | ROE > 15%",
            "strategy_dividend_name": "高股息策略",
            "strategy_dividend_desc": "标准：股息率(TTM) > 4% (防御性现金牛资产)",
            "strategy_tech_breakout_name": "技术突破",
            "strategy_tech_breakout_desc": "标准：当日涨幅 2-7% | 换手率 3-15% (放量活跃)",
            "strategy_northbound_name": "北向资金",
            "strategy_northbound_desc": "标准：北向资金持股比例 > 5% (外资重仓股)",
            "strategy_oversold_name": "超跌反弹",
            "strategy_oversold_desc": "标准：当日跌幅 > 3% | 市盈率 < 30 (短期错杀)",
            "strategy_institutional_name": "龙虎榜机构",
            "strategy_institutional_desc": "标准：龙虎榜机构净买入 > 3000万 (主力资金抢筹)",
            "strategy_chips_name": "筹码集中 (暂不可用)",
            "strategy_chips_desc": "股东户数大幅减少",
            "strategy_block_trade_name": "大宗交易",
            "strategy_block_trade_desc": "标准：大宗成交额 > 1000万 (关注主力吸筹)",
            "strategy_cashflow_name": "现金流优质",
            "strategy_cashflow_desc": "标准：资产负债率 < 50% | ROE > 10% (稳健经营)",
            "strategy_large_pe_name": "大盘低估",
            "strategy_large_pe_desc": "标准：总市值 > 500亿 | 市盈率 < 15倍 (核心资产)",
            
            # Home View
            "home_title": "市场概览",
            "home_data_date": "数据日期: {date}",
            "home_refresh": "刷新数据",
            "home_index_sh": "上证指数",
            "home_index_sz": "深证成指",
            "home_index_cyb": "创业板指",
            "home_northbound": "北向资金",
            "home_hot_concepts": "热门概念",
            "home_inflow": "流入",
            "home_outflow": "流出",
            "home_hot_strategies": "热门策略推荐",
            "home_strategy_value": "价值投资",
            "home_strategy_value_desc": "寻找低估值优质蓝筹",
            "home_strategy_growth": "高成长",
            "home_strategy_growth_desc": "捕捉业绩爆发股",
            "home_strategy_institutional": "机构龙虎榜",
            "home_strategy_institutional_desc": "跟踪主力资金动向",
            "home_strategy_dividend": "高股息",
            "home_strategy_dividend_desc": "稳健收息精选",
            "home_run": "运行",

            # Data Explorer
            "data_select_table": "选择数据表",
            "data_filter_col": "过滤列",
            "data_filter_op": "操作符",
            "data_filter_val": "值",
            "data_export_current": "导出当前页",
            "data_export_all": "导出全部 (CSV)",
            "data_tab_viewer": "表格查看器",
            "data_tab_sql": "SQL 控制台",
            "data_page_num": "第 {current} 页 / 共 {total} 页",
            "data_total_rows": "共 {count} 条记录",
            
            "data_sql_label": "SQL 查询语句",
            "data_sql_hint": "输入 SELECT 查询 (例如: SELECT * FROM stock_basic LIMIT 10)",
            "data_sql_execute": "执行 (Ctrl+Enter)",
            "data_sql_result": "查询结果",
            "data_sql_ready": "就绪",
            "data_sql_success": "查询成功 (耗时 {time:.3f}s). 共返回 {rows} 行.",
            "data_sql_success_truncated": "查询成功 (耗时 {time:.3f}s). 显示前 {limit} 行 (共 {rows} 行).",
            "data_sql_error": "执行错误: {error}",
            "data_btn_count": "统计行数",

            # Settings - Tabs
            "settings_tab_general": "基本配置",
            "settings_tab_data": "数据管理",
            "settings_tab_ai": "AI 大脑",
            "settings_tab_tasks": "定时任务",
            "settings_tab_notify": "消息提醒",
            "settings_tab_system": "系统优化",

            # Settings - Sections
            "settings_sec_api": "API 连接配置",
            "settings_sec_ai": "AI 模型配置",
            "settings_ai_api_key_label": "AI 接口密钥 (API Key)",
            "settings_ai_base_url_label": "API 服务地址 (Base URL)",
            "settings_sec_tuning": "AI 性能调优",
            "settings_sec_history": "历史数据管理",
            "settings_sec_tasks": "定时任务配置",
            "settings_notify_title": "消息推送设置",
            "settings_notify_desc": "开启后，当有重大利好消息或AI选出牛股时，会通过系统通知提醒您。",
            "settings_sec_health": "健康检查",
            
            # Settings - Labels & Descriptions
            "settings_token_desc": "请输入您的 Tushare Pro Token 以获取数据权限。",
            "settings_save_token": "保存 Tushare Token",
            "settings_ai_desc": "配置大模型 API 以启用智能选股分析功能。",
            "settings_ai_model": "模型名称",
            "settings_max_candidates": "AI 初选数量",
            "settings_min_turnover": "最低换手率阈值 (%)",
            "settings_ai_concurrency": "并发分析线程数",
            "settings_init_data": "初始化3年数据",
            "settings_cancel_sync": "取消同步",
            "settings_msg_sync_cancelled": "同步已由用户取消",
            "settings_init_desc": "同步3年历史行情和财务数据，约需10-15分钟",
            "settings_auto_update": "启用每日自动更新",
            "settings_news_alerts": "启用实时财经消息推送",
            "settings_update_time": "更新时间",
            "settings_log_level": "日志级别", 
            "settings_sync_concurrency": "数据同步并发数",
            "settings_sync_desc": "根据网络和CPU性能调整，默认5。过高可能导致被封锁。",
            "settings_db_buffer": "数据库写入缓冲",
            "settings_buffer_desc": "调整批量写入的缓冲大小 (需重启生效)",
            "settings_save_config": "保存配置",
            "settings_check_health": "开始检查",
            "settings_verify_success": "验证成功",
            "settings_verify_failed": "未验证",
            "settings_manual_update": "手动数据更新",
            "settings_update_today": "更新今日行情",
            "settings_full_sync": "完整日更新",
            "settings_cache_manage": "缓存管理",
            "settings_clear_cache": "清除所有缓存",
            "settings_cache_desc": "当数据出现异常时，可尝试清除缓存重新同步。",
            "settings_auto_desc": "设置每日自动同步数据，无需手动操作。",
            "settings_save_ai": "保存 AI 配置",
            
            # Settings - Options & Hints
            "settings_opt_1530": "15:30 (收盘后)",
            "settings_opt_2000": "20:00 (晚间)",
            "settings_lang_zh": "简体中文",
            "settings_lang_en": "English",
            "settings_hint_ai_cost": "进入AI深度分析的候选股票数量 (影响成本)",
            "settings_hint_turnover": "过滤不活跃股票，低于此换手率将被剔除",
            "settings_hint_ai_model": "控制同时分析几只股票。过高可能触语速限制。",
            "settings_hint_sync_full": "同步行情、估值、资金流、北向持股",
            "settings_hint_first_run": "首次使用需拉取历史数据",
            
            # AI Settings Dialog
            "ai_system_prompt": "系统提示词",
            "ai_settings_title": "AI 助手配置",
            "ai_settings_desc": "配置 AI API (默认推荐 DeepSeek)",
            "ai_prompt_label": "系统提示词配置 (A股专家模式)",
            "ai_reset_default": "恢复默认",
            "ai_key_required": "API Key 不能为空",
            "ai_key_required": "API Key 不能为空",
            "ai_settings_saved": "AI 配置已更新!",
            "settings_ai_prompt": "分析提示词 (Prompt)",
            "settings_ai_prompt_hint": "自定义大模型分析股票时的角色和规则",
            "settings_reset_prompt": "恢复默认提示词",
            "settings_snack_prompt_reset": "提示词已恢复默认",
            "common_cancel": "取消",
            "common_cancel": "取消",
            "common_save": "保存",
            "common_close": "关闭",
            "common_query": "查询",
            "common_refresh": "刷新",
            "common_download": "导出数据",

            # Stock Details
            "detail_loading_chart": "正在加载 K 线图...",
            "detail_sec_price": "行情数据",
            "detail_price": "现价",
            "detail_pct_chg": "涨跌幅",
            "detail_turnover": "换手率",
            "detail_vol": "成交量",
            "detail_amount": "成交额",
            "detail_sec_valuation": "估值指标",
            "detail_pe": "PE(TTM)",
            "detail_pb": "PB",
            "detail_ps": "PS(TTM)",
            "detail_dividend": "股息率",
            "detail_total_mv": "总市值",
            "detail_circ_mv": "流通市值",
            "detail_sec_financial": "财务指标",
            "detail_roe": "ROE",
            "detail_gpm": "毛利率",
            "detail_debt_ratio": "资产负债率",
            "detail_rev_yoy": "营收同比",
            "detail_profit_yoy": "净利润同比",
            "detail_sec_basic": "基本信息",
            "detail_industry": "行业",
            "detail_list_date": "上市日期",
            "detail_ai_analysis": "AI 智能分析",
            "detail_ai_score_prefix": "评分: ",
            "detail_ai_no_analysis": "暂无详细分析",
            "detail_ai_thinking": "查看 AI 思考 (Thinking Process)",
            "detail_ai_no_thinking": "暂无思考过程记录",
            "detail_err_no_processor": "数据处理器未连接",
            "detail_loading_history": "正在加载历史K线数据...",
            "detail_no_history": "暂无历史数据",
            "detail_chart_generated": "交互式K线图已生成",
            "detail_chart_browser_hint": "由于平台限制，请在浏览器中查看完整图表",
            "detail_open_browser": "打开图表 (浏览器)",
            "detail_err_load_chart": "加载图表失败: {error}",

            "chart_kline": "K线",

            # Onboarding Wizard
            "wizard_welcome_title": "欢迎使用 A股智能选股助手",
            "wizard_welcome_desc": "首次使用需要完成以下配置",
            "wizard_step_prefix": "步骤{index}",
            "wizard_step_label_token": "Token配置",
            "wizard_step_label_ai": "AI配置",
            "wizard_step_label_sync": "数据同步",
            "wizard_step_label_schedule": "定时任务",
            "wizard_step_label_done": "完成",
            
            "wizard_token_label": "请输入您的 Tushare Pro Token",
            "wizard_token_hint": "可在 tushare.pro 个人中心获取",
            "wizard_step1_title": "步骤 1: 配置 Tushare Token",
            "wizard_step1_desc": "Tushare Pro 是专业的金融数据服务平台，需要注册并获取Token。\n注册地址：https://tushare.pro/register\n获取Token后粘贴到下方输入框。",
            "wizard_btn_verify_next": "验证并继续",
            "wizard_verifying": "验证中...",
            "wizard_err_token_empty": "❌ 请输入Token",
            "wizard_msg_token_success": "✅ Token验证成功",
            "wizard_err_verify_failed": "❌ 验证失败: {error}",
            
            "wizard_ai_key_label": "AI API Key (DeepSeek/OpenAI)",
            "wizard_ai_model_label": "模型名称",
            "wizard_ai_prompt_label": "系统提示词 (专家设定)",
            "wizard_ai_prompt_hint": "设定 AI 分析时的角色和规则...",
            "wizard_step2_title": "步骤 2: 配置 AI 助手 (必选)",
            "wizard_step2_desc": "配置大模型 API 以启用智能选股分析功能。\n支持 DeepSeek 等 OpenAI 兼容接口",
            "wizard_ai_advanced": "高级设置: 系统提示词",
            "wizard_ai_advanced_subtitle": "点击展开编辑 A股分析逻辑",
            "wizard_btn_skip": "跳过配置",
            "wizard_err_ai_key": "❌ 请输入 API Key",
            "wizard_ai_connecting": "验证连接中...",
            "wizard_ai_success": "✅ 验证成功",
            "wizard_ai_failed": "❌ 验证失败: 连接被拒绝",
            "wizard_ai_error": "❌ 验证出错: {error}",
            
            "wizard_sync_quick": "仅同步今日 (快)",
            "wizard_sync_full": "完整同步 (3年)",
            "wizard_btn_cancel": "取消",
            "wizard_step3_title": "步骤 3: 同步历史数据",
            "wizard_step3_desc": "选股策略需要历史数据支持。\n完整同步约需3-5分钟 (5倍并发)，也可选择仅同步今日数据。",
            "wizard_btn_skip_step": "跳过此步骤",
            "wizard_status_ready": "准备就绪",
            "wizard_status_init": "正在初始化...",
            "wizard_status_today": "同步今日数据...",
            "wizard_msg_today_done": "✅ 今日数据同步完成",
            "wizard_status_stock_list": "同步股票列表...",
            "wizard_status_history": "同步历史行情 (耗时较长)...",
            "wizard_status_cancelling": "正在取消...",
            "wizard_msg_sync_cancelled": "❌ 同步已取消",
            "wizard_msg_history_done": "✅ 历史数据同步完成",
            "wizard_msg_sync_failed": "❌ 同步失败: {error}",
            
            "wizard_schedule_label": "启用每日自动更新 (16:30)",
            "wizard_step4_title": "步骤 4: 设置自动更新",
            "wizard_step4_desc": "建议在每个交易日收盘后自动更新数据。\n程序将在后台静默更新，不会打扰您的使用。",
            "wizard_schedule_note": "* 需要程序在后台运行",
            "wizard_btn_finish": "完成配置",
            
            "wizard_step5_title": "🎉 配置完成！",
            "wizard_step5_desc": "您已完成所有初始配置。\n现在可以开始使用智能选股功能了！",
            "wizard_btn_start": "开始使用",

            "settings_hint_bg_run": "* 需要程序在后台持续运行",
            "settings_hint_cpu": "根据网络和CPU性能调整，默认5。过高可能导致被封锁。",
            
            # Settings - Status & Messages
            "settings_status_auto_on": "✅ 自动更新已启用，将在每个交易日指定时间自动同步数据",
            "settings_status_auto_off": "自动更新已关闭",
            "settings_snack_auto_on": "自动更新已启用",
            "settings_snack_auto_off": "自动更新已关闭",
            "settings_snack_news_on": "实时消息订阅已启用",
            "settings_snack_news_off": "实时消息订阅已关闭",
            "settings_snack_time_set": "更新时间已设置为 {time}",
            "settings_snack_saved_fail": "设置保存失败",
            "settings_status_verifying": "验证中...",
            "settings_status_no_key": "未配置 Key",
            "settings_status_verify_ok": "验证成功 ✓",
            "settings_status_verify_err": "验证失败: {error}",
            "settings_snack_ai_saved": "AI 配置已保存并在验证中...",
            "settings_snack_ai_error": "保存出错: {error}",
            "settings_snack_token_empty": "Token 为空，Tushare 功能将不可用",
            "settings_snack_token_verified": "验证成功 ✓",
            "settings_snack_token_fail": "验证失败: {error}",
            "settings_trading_days": "(交易日)",
            "settings_snack_concurrency_set": "并发数已设置为 {val} (下次同步生效)",
            "settings_snack_log_level": "日志级别已切换为 {level}",
            
            # Dialogs & Snacks
            "dialog_confirm_clear_title": "确认清理缓存",
            "dialog_confirm_clear_content": "这将删除所有已缓存的历史数据。\n清理后需要重新同步数据。\n\n确定要继续吗？",
            "btn_confirm_clear": "确认清理",
            "snack_queue_empty": "队列大小不能为空",
            "snack_queue_min": "队列大小太小 (至少 10)",
            "snack_queue_saved": "配置已保存，请重启程序生效",
            "snack_save_fail": "保存失败",
            "snack_daily_sync_start": "正在同步今日行情数据...",
            "snack_daily_sync_done": "行情更新完成！共 {count} 只股票",
            "snack_daily_sync_nodata": "未获取到数据，请检查Token或网络",
            "snack_full_sync_start": "正在执行完整日更新...",
            "snack_full_sync_done": "完整日更新完成！共同步 {total} 条记录",
            "progress_sync_prepare": "准备同步...",
            "snack_sync_not_completed": "同步未完成，请重试",
            "progress_sync_market": "正在同步行情: {date}",
            "progress_sync_finance": "正在同步财务: {date}",
            "progress_sync_moneyflow": "正在同步资金流: {date}",
            "progress_sync_done": "同步完成",
            
            # System Initialization (Step 1-5)
            "init_stock_list": "正在同步股票列表...",
            "init_stock_list_done": "股票列表同步完成",
            "init_calendar": "正在同步交易日历...",
            "init_calendar_done": "交易日历同步完成",
            "init_quotes": "正在同步历史行情 (3年)...",
            "init_fundamentals": "正在同步深度基本面数据...",
            "init_health_check": "正在执行系统健康检查...",
            "init_complete": "初始化完成! 健康度: {status} (财报覆盖: {coverage})",
            
            # --- Settings: System Tab ---
            "sys_core_config": "核心配置",
            "sys_log_label": "控制系统日志详细程度 (Info/Debug)",
            "sys_concurrency": "同步并发数",
            "sys_concurrency_hint": "多线程请求数量，建议 3-5",
            "sys_tushare_limit": "Tushare流控",
            "sys_tushare_limit_desc": "Tushare API 每分钟最大请求次数限制 (留空则不限速)",
            "sys_data_maint": "数据维护",
            "sys_data_maint_desc": "系统初始化、数据修复与清理",
            "sys_btn_init": "系统初始化",
            "sys_btn_health": "健康检查",
            "sys_init_confirm_title": "确认初始化",
            "sys_init_confirm_content": "此操作将执行完整的系统初始化（约需 30-90 分钟）：\n1. 同步股票列表\n2. 同步3年交易日历\n3. 同步3年日线行情\n4. 同步3年全量财务数据 (财报, 指标, 质押)\n\n建议在空闲时间执行。是否继续？",
            "sys_init_progress_title": "系统初始化中",
            "sys_init_wait": "请不要关闭程序...",
            "sys_init_cancel_wait": "正在取消... (请稍候)",
            "sys_init_success": "初始化完成！",
            "sys_init_failed": "初始化失败: {error}",
            "sys_snack_limit_off": "Tushare API 速率限制已解除 (不限速)",
            "sys_snack_limit_min": "若要限制，建议至少 10 次/分钟",
            "sys_snack_limit_set": "Tushare API 速率限制已更新为 {limit} 次/分钟",
            # Health Report
            "health_report_title": "全量数据体检报告",
            "health_checking": "正在执行全量数据体检...",
            "health_status_label": "健康状态: {status}",
            "health_checked_count": "检查了 {count} 个核心数据表",
            "health_market_ts": "市场行情 (Time-Series)",
            "health_sync_latest": "最新同步",
            "health_sync_official": "官方最新",
            "health_lag_days": "滞后天数",
            "health_qa": "深度质检 (Quality Assurance)",
            "health_gap_count": "行情断点",
            "health_sanity_err": "脏数据熔断",
            "health_coverage": "深度覆盖 (Coverage & Freshness)",
            
            # --- Settings: Data Source Tab ---
            "ds_last_update": "最后更新",
            "ds_data_coverage": "数据覆盖",
            "ds_sys_health": "系统健康",
            "ds_storage_usage": "存储占用",
            "ds_status_checking": "检测中...",
            "ds_status_calc": "计算中...",
            "ds_btn_repair": "一键修复缺失数据",
            "ds_btn_repair_fmt": "修复 {missing} 只缺失 + {stale} 只过期数据的股票",
            "ds_shortcut_console": "快捷指令台",
            "ds_action_today": "快速同步今日行情数据",
            "ds_action_full": "完整遍历修复缺失数据",
            "ds_action_clear": "清除缓存并重新校验",
            "ds_health_lag": "数据滞后",
            "ds_health_error": "数据异常/缺失",
            "ds_health_ok": "数据健康",
            "ds_never_sync": "从未同步",
            "ds_repair_start": "开始针对性修复...",
            "ds_repair_progress": "正在修复... 请勿关闭",
            "ds_repair_done": "✅ 修复完成！已补充 {count} 条记录",
            "ds_repair_fail": "修复失败: {error}",
            
            # --- Settings: AI Tab ---
            "ai_status_disconnected": "未连接",
            "ai_status_connected": "已连接",
            "ai_btn_test": "测试连接",
            "ai_btn_testing": "测试中...",
            "ai_tuning_desc": "调整AI分析的深度与广度，平衡成本与性能",
            "ai_hint_default": "默认: {val}",
            
            # --- Missed Keys (Round 2) ---
            "time_today": "今日",
            "common_items": "条",
            "common_times_min": "次/分",
            "common_suffix_day": "天",
            "common_suffix_place": "处",
            "common_start_exec": "开始执行",
            "common_preparing": "正在准备...",
            "common_check_fail": "检查失败: {error}",
            "common_op_fail": "操作失败: {error}",
            "common_reason": "异常原因:",
            "common_normal": "正常",
            
            # System Tab Dropdowns
            "sys_opt_debug": "调试 (DEBUG)",
            "sys_opt_info": "信息 (INFO)",
            "sys_opt_warn": "警告 (WARN)",
            "sys_opt_error": "错误 (ERROR)",
            
            # Health Report Inner
            "health_missing_sample": "未覆盖股票示例",
            
            # Data Source Tab
            "ds_val_placeholder_count": "5000+ 股票",
            "ds_text_cov_detail": "覆盖率: {cov} | 财报覆盖: {fin_cov} (近期: {recent}) | 滞后: {lag}天",
            "ds_repair_start_snack": "开始针对性修复...",
            "ds_cache_cleared": "缓存已清理完成！",
            "ds_clean_fail": "清理失败: {error}",
            "ds_verify_fail_fmt": "验证失败: {error}",
            "ds_init_fail_fmt": "初始化失败: {error}",
            "ai_hint_cap": "上限",
            "ai_hint_turnover_min": "换手率下限",
            "ai_sec_persona": "系统人设 (System Prompt)",
            "ai_snack_key_err": "请输入API Key",
            "ai_snack_conn_ok": "连接测试成功！",
            "ai_snack_conn_fail": "连接测试失败",
            "ai_snack_param_err": "参数错误：数量必须为整数，换手率必须为数字",
        }
    }

    @classmethod
    def initialize(cls):
        cls._locale = ConfigHandler.get_locale()
        logger.info(f"[I18n] Initialized with locale: {cls._locale}")

    @classmethod
    def get(cls, key):
        """Get translated string by key"""
        return cls.STRINGS.get(cls._locale, {}).get(key, key)
    
    @classmethod
    def set_locale(cls, locale):
        """Change locale and notify listeners"""
        if locale in cls.STRINGS:
            cls._locale = locale
            ConfigHandler.set_locale(locale)
            logger.info(f"[I18n] Locale changed to: {locale}")
            
            # Notify all subscribed components (usually Views)
            for listener in cls._listeners:
                try:
                    listener()
                except Exception as e:
                    logger.error(f"[I18n] Listener error: {e}")

    @classmethod
    def subscribe(cls, callback):
        """Subscribe to locale changes"""
        if callback not in cls._listeners:
            cls._listeners.append(callback)
            
    @classmethod
    def current_locale(cls):
        return cls._locale
