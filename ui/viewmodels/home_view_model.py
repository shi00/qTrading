import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import pandas as pd

from data.data_processor import DataProcessor
from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.loop_local import get_loop_local
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsRow:
    """新闻行数据 frozen dataclass (L771 合规: 行数据用 frozen dataclass).

    替代 dual-track 的 DataFrame 持有模式, 直接放入 state.
    """

    content: str = ""
    tags: str = ""
    source: str = ""
    publish_time: str = ""
    is_ai_tagged: bool = False
    # 入库 market_news.sentiment 原值（如 "Positive"/"Neutral"/"Negative"）；
    # 缺失/不可解析时为 None，由 View 渲染为中性样式（R21：不靠本地关键词猜测填充）。
    sentiment: str | None = None
    # UIR-F04-004: 稳定身份 market_news.id。实时推送项尚未回读 DB 时为 None，
    # 此时以 (publish_time, content) 复合键参与幂等合并（不同时间同正文是合法新闻）。
    news_id: int | None = None


@dataclass(frozen=True)
class MarketIndexRow:
    """大盘指数行数据 (SH/SZ/CYB)."""

    name: str = ""
    value: str = "--"
    change: str = "--"
    color: str = ""
    # UIR-F03-002: 该行数据所属交易日 (as_of)。None = 无数据或日期未知 (R21: 不伪称)。
    as_of: str | None = None


@dataclass(frozen=True)
class HsgtRow:
    """北向资金行数据."""

    value: str = "--"
    color: str = ""
    sub: str = "--"
    # UIR-F03-002: 该行数据所属交易日 (as_of)。None = 无数据或日期未知 (R21: 不伪称)。
    as_of: str | None = None


@dataclass(frozen=True)
class HotConceptRow:
    """热门概念行数据."""

    name: str = "--"
    change: str = "0.00%"
    color: str = ""


@dataclass(frozen=True)
class HomeState:
    """HomeViewModel 的不可变状态快照 (L770/L771 合规).

    所有业务数据直接放入 state, 用 tuple[Row, ...] 替代 DataFrame/dict.
    View = f(state), 无 dual-track.
    """

    news_page: int = 0
    has_more_news: bool = False
    is_loading_more: bool = False
    # 业务数据 (tuple[Row, ...], 符合 L771)
    news_rows: tuple[NewsRow, ...] = ()
    # 有效股票代码集合 (6 位 symbol, 源自 stock_basic), 供新闻「查看个股」链接
    # 校验 (UX-09 MINOR-03); 为空表示尚未加载或加载失败, 此时不生成任何链接。
    valid_stock_codes: frozenset[str] = frozenset()
    market_indices: tuple[MarketIndexRow, ...] = ()
    market_hsgt: HsgtRow = field(default_factory=HsgtRow)
    market_hot_concepts: tuple[HotConceptRow, ...] = ()
    market_date: str = "--"
    market_stale: bool = False
    # UIR-F03-002: 逐分量 as_of 交易日。None = 该分量无有效数据或日期未知
    # (旧缓存无该键同样归 None —— 不默认复制页头日期为已证实日期, R21)。
    market_indices_date: str | None = None
    market_hsgt_date: str | None = None
    # UIR-F03-002: 页头 i18n key (VM 派生, View 按 locale 渲染)。
    # 混合日期 → "market_data_mixed_dates"; stale 单一日期 → "market_data_cached_at";
    # 新数据 → "home_data_date"。
    market_header_key: str = "home_data_date"
    # P1-3 批次 2: 三态字段 (EmptyState/ErrorState/加载态)
    is_loading: bool = False
    has_market_data: bool = False
    has_news_data: bool = False
    load_error: Message | None = None


class HomeViewModel(ObservableViewModelMixin[HomeState]):
    """
    ViewModel for HomeView.
    Handles data fetching, state management, and service subscriptions.
    Follows "Supervising Controller" pattern.

    L771 合规: state 字段全部用 tuple[Row, ...] / frozen dataclass,
    VM 内部不持有 DataFrame/dict 作为业务状态 (移除 dual-track).
    """

    PAGE_SIZE = 20  # 常量,不放入 state

    def __init__(self):
        # 懒构造 DataProcessor：DataProcessor.__init__ 同步初始化 TushareClient
        # （为 40+ API 创建 rate_limiter，耗时 34s+），若在 __init__ 同步构造会阻塞
        # Flet 主线程（R16）。改为 _ensure_processor() 首次真正使用时经 IO 线程池异步
        # 构造。E2E 下 home_view._init_and_load 顶部早返（home_view.py L165-167）在
        # init_data 之前，不会触发懒构造；原 E2E_TESTING 特判已删除（懒构造天然兼容）。
        self.processor: DataProcessor | None = None

        # Internal state (frozen snapshot)
        self._state = HomeState()

        # 有效股票代码集合缓存 (UX-09 MINOR-03): None=未加载; 加载成功后缓存
        # (stock_basic 每日同步, 变更频率低), 避免每次刷新重复读库。
        self._valid_stock_codes: frozenset[str] | None = None

        # Concurrency Control (UIR-F04-003)
        # 两个独立代次: 新闻写回 (refresh/pagination) 与全量加载所有权
        # (is_loading/load_error)。后台 INITIAL 刷新会推进新闻代次但不持有
        # loading, 若共用一个代次会让进行中的 _load_data 收尾失效 (loading 卡 True)。
        self._load_generation = 0  # 新闻写回代次
        self._view_load_generation = 0  # 全量加载 (is_loading/load_error) 所有权代次
        # UIR-F04-004: 向后 keyset 游标 (publish_time, id)，由已成功写入的最旧 DB 行推进；
        # 失败/取消/代次失效均不推进，跨刷新由 refresh_news 重置。
        self._news_cursor: tuple[datetime, int] | None = None

        # Mixin 字段初始化（跨线程修复）
        self._init_mixin_fields()

    async def _ensure_processor(self) -> DataProcessor:
        """懒构造 DataProcessor（IO 线程池 offload），避免阻塞 UI 主线程 (R16)。

        双检 + loop-local 锁防并发双检竞态 (R11)：refresh_news 与 load_next_page 等
        异步方法可能交错触发构造。key 带 VM 前缀避免跨实例共享同一锁。
        """
        if self.processor is None:
            lock = get_loop_local("home_vm_processor_lock", asyncio.Lock)
            async with lock:
                if self.processor is None:
                    self.processor = await ThreadPoolManager().run_async(TaskType.IO, DataProcessor)
        return self.processor

    async def _ensure_valid_stock_codes(self) -> frozenset[str]:
        """加载有效股票代码集合 (6 位 symbol), 供新闻「查看个股」链接校验 (UX-09 MINOR-03).

        数据源复用 ``stock_dao.get_ts_code_map()`` (源自 ``stock_basic`` 落库的
        symbol→ts_code 映射)。惰性加载 + 实例级缓存 (stock_basic 每日同步, 集合变更
        频率低); async 原生 DB 访问, 不阻塞 UI 主线程 (R16)。

        DB 读取失败时不缓存失败结果 (下次刷新重试), 并返回空集合——链接降级为不展示,
        宁可少展示也不生成未经校验的错误链接 (R21 精神: 不伪造业务合法值)。
        """
        if self._valid_stock_codes is not None:
            return self._valid_stock_codes
        # NOTE(lazy): 代码集合按 VM 实例生命周期缓存, 不在 stock_basic 同步后主动失效.
        # ceiling: 应用运行期间新上市/退市股票可能带来链接缺失或多余 (代码集合未刷新).
        # upgrade: 当 stock_basic 同步具备完成事件/失效通知, 或用户报告链接缺失时, 改为监听同步完成刷新缓存.
        try:
            processor = await self._ensure_processor()
            code_map = await processor.cache.stock_dao.get_ts_code_map()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[HomeVM] Error loading valid stock codes: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[HomeVM] Error loading valid stock codes traceback", exc_info=True)
            return frozenset()
        self._valid_stock_codes = frozenset(code_map.keys()) if code_map else frozenset()
        self._set_state(valid_stock_codes=self._valid_stock_codes)
        return self._valid_stock_codes

    def _invoke_single_subscriber(self, cb: Callable[[HomeState], None], snap: HomeState) -> None:
        """覆盖 per-cb 调用策略：HomeVM-specific try/except + warning logging。

        不再 override 终态骨架 _notify()（避免跨线程修复被绕过，架构 P0-1 修复）。
        """
        try:
            cb(snap)
        except Exception as e:
            logger.warning(
                "[HomeVM] Subscriber error: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    def init(self) -> None:
        """Initialize subscriptions (无回调参数,View 通过 subscribe 订阅 state)。"""
        NewsSubscriptionService().add_listener(self._on_news_service_update)
        MarketDataService().add_listener(self._on_market_service_update)

    def stop(self) -> None:
        """Stop subscriptions (视图失活时经 use_effect cleanup 调用, 与 init() 配对).

        仅移除 News/MarketData service listener (保留 state/subscribers, 便于切回
        active 时经 init() 重新注册恢复)。幂等: News/MarketData 的 remove_listener
        均基于 set.remove + try/except KeyError, 重复调用安全 (无需额外订阅状态字段)。

        不替代 dispose(): dispose() 为组件真正卸载时调用, 额外清订阅者; stop() 仅退订
        service listener。HomeView 常驻 app_layout 的 ft.Stack 永不卸载, 失活时必须
        经本方法退订, 否则被新闻/行情 push 反复重渲染 (详见 Plans.md 根因)。

        UIR-F04-003: 失活后旧刷新的新闻结果永久无效, 故推进新闻代次; 不推进
        loading 所有权代次——在途 _load_data 仍需正常收尾 is_loading, 避免失活
        期间残留 loading=True (重激活时 _init_and_load 会重新 begin_load)。
        """
        self._load_generation += 1
        try:
            NewsSubscriptionService().remove_listener(self._on_news_service_update)
            MarketDataService().remove_listener(self._on_market_service_update)
        except Exception as e:
            logger.warning(
                "[HomeVM] Stop error: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    def dispose(self) -> None:
        """覆盖 dispose: 移除 service listener, 再清订阅者.

        UIR-F04-003: 推进两个代次, 使 dispose 后在途请求的新闻写回与
        loading/error 收尾全部失效 (notifier 已 disposed 时 _set_state 亦为 no-op)。
        """
        self._load_generation += 1
        self._view_load_generation += 1
        try:
            NewsSubscriptionService().remove_listener(self._on_news_service_update)
            MarketDataService().remove_listener(self._on_market_service_update)
        except Exception as e:
            logger.warning(
                "[HomeVM] Dispose error: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
        super().dispose()

    @staticmethod
    def register_news_alert_listener(callback: Callable[[str], Awaitable[None]]) -> None:
        """注册新闻告警监听 (View 通过 VM 命令转发, 不直调 NewsSubscriptionService).

        参照 ``init`` 范例 (CLAUDE.md §3.2 MVVM): View 持有需要 page 访问的 toast 回调,
        通过本命令转发注册到 NewsSubscriptionService 的 alert listener 集合。

        P2-2: 改为 staticmethod 避免调用方临时实例化 HomeViewModel (startup_views.py
        启动期无业务状态需求, 仅需转发到 NewsSubscriptionService 单例)。
        callback 必须为 async, 服务端在事件循环线程 await 执行 UI 变更 (R16 跨线程修复)。
        """
        NewsSubscriptionService().add_listener(callback, is_alert=True)

    @staticmethod
    def unregister_news_alert_listener(callback: Callable[[str], Awaitable[None]]) -> None:
        """退订新闻告警监听 (与 register_news_alert_listener 配对)。"""
        NewsSubscriptionService().remove_listener(callback, is_alert=True)

    # --- Service Event Handlers ---

    async def _on_news_service_update(self, update_type=None, data=None):
        """NewsSubscriptionService listener (async, 在事件循环线程执行).

        NewsSubscriptionService._notify_listeners 支持 async listener
        (inspect.iscoroutinefunction 检测), 在事件循环线程 await 调用,
        可以直接调 await refresh_news() 处理 INITIAL 类型.

        竞态安全: tuple 不可变, _set_state 创建新 HomeState 实例.
        load_next_page 在 await 后重新读取 self._state.news_rows,
        获取最新快照, 不会丢失 NEW_ITEM 追加.

        UIR-F04-004: NEW_ITEM 按稳定身份幂等前插 (id 优先, 推送项无 id 时用
        (publish_time, content) 复合键), 重复推送不产生重复行.
        """
        from services.news_subscription_service import NewsUpdateType

        if update_type == NewsUpdateType.NEW_ITEM and data:
            new_rows = tuple(_news_item_to_row(item) for item in data)
            if new_rows:
                current_rows = self._state.news_rows
                fresh_rows = _merge_news_rows(current_rows, new_rows)
                if fresh_rows:
                    # tuple 拼接 (同步, O(n), n 是当前新闻数量)
                    self._set_state(news_rows=fresh_rows + current_rows)
        elif update_type == NewsUpdateType.TAG_UPDATE and data:
            content = str(data.get("content", "") or "")
            tags = str(data.get("tags", "") or "")
            if content:
                # 遍历 tuple 更新匹配项 (同步, O(n))
                updated = tuple(
                    replace(row, tags=tags) if row.content == content else row for row in self._state.news_rows
                )
                if updated != self._state.news_rows:
                    self._set_state(news_rows=updated)
        elif update_type == NewsUpdateType.INITIAL:
            # INITIAL: 全量刷新 (async, 在事件循环线程执行)
            await self.refresh_news()

    def _on_market_service_update(self):
        """MarketDataService listener (sync, 在 IO 线程池执行).

        MarketDataService._notify_listeners 总是通过
        ThreadPoolManager().run_async(TaskType.IO, listener) 提交,
        不支持 async listener. 保持 sync 签名.

        竞态安全: get_cached_data 返回引用, _set_state 创建新 HomeState.
        与 load_market_data 并发时 last-write-wins (相同数据, 无不一致).
        """
        data = MarketDataService().get_cached_data()
        if data:
            self._set_state(**_market_data_to_state_fields(data), has_market_data=True)

    # --- Data Actions ---

    async def init_data(self):
        """Initialize data processor"""
        processor = await self._ensure_processor()
        await processor.init_data()

    async def load_market_data(self):
        """
        Fetch latest market data with retry logic.
        Updates state directly (无 dual-track).
        Returns: dict or None (保留返回值兼容测试, 但 View 不依赖返回值)
        """
        data = None
        for _ in range(5):
            data = MarketDataService().get_cached_data()
            if data:
                break
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise

        if data:
            self._set_state(**_market_data_to_state_fields(data), has_market_data=True)

        return data

    async def get_cached_market_data(self):
        """Get data immediately from service cache, update state."""
        data = MarketDataService().get_cached_data()
        if data:
            self._set_state(**_market_data_to_state_fields(data), has_market_data=True)
        return data

    def begin_load(self) -> tuple[int, int]:
        """开始一次全量加载, 在命令入口分配代次 (UIR-F04-003)。

        代次必须在「用户点击/挂载触发」时分配, 而不是在 ``refresh_news`` 的
        await 之后——否则 ``load_market_data`` 耗时差异会让后发起的请求拿到
        更旧代次, 导致旧数据覆盖新数据 (DAO 返回顺序 ≠ 用户意图顺序)。

        Returns:
            (view_generation, news_generation): View 在 finally 中把
            view_generation 传给 :meth:`end_load` 收尾 loading/error;
            news_generation 透传给 :meth:`refresh_news`, 由新闻写回校验。
        """
        self._view_load_generation += 1
        self._load_generation += 1
        # 新加载取代在途分页: 旧分页代次失效, 其 finally 不再清 is_loading_more
        self._set_state(is_loading=True, load_error=None, is_loading_more=False)
        return self._view_load_generation, self._load_generation

    def end_load(self, view_generation: int, error: Message | None = None) -> None:
        """结束全量加载: 仅当前有效加载可写回 is_loading/load_error (UIR-F04-003)。

        旧请求 (被新加载/clear_state/dispose 推进代次) 的收尾为 no-op,
        既不清除新请求的 loading, 也不写入过期的错误。
        """
        if view_generation != self._view_load_generation:
            return
        self._set_state(is_loading=False, load_error=error)

    async def refresh_news(self, generation: int | None = None):
        """
        Full refresh of news (Page 0).
        Updates state.news_rows directly (无 dual-track).
        Returns: (DataFrame, has_more) — 保留返回值兼容测试

        Args:
            generation: ``begin_load`` 分配的新闻代次。为 None 时 (后台 INITIAL
                刷新/独立调用) 在入口自增并分配。所有 await 之后与写回之前
                校验代次, 晚到的旧结果直接丢弃 (UIR-F04-003)。
        """
        if generation is None:
            self._load_generation += 1  # Invalidate pending loads
            generation = self._load_generation
            # 旧分页代次已失效且不会再清 loading, 由本次刷新重置 (防卡死)
            self._set_state(is_loading_more=False)
        elif generation != self._load_generation:
            return None, False

        # UX-09 MINOR-03: 加载有效代码集合, 供新闻「查看个股」链接校验 (失败降级为空, 不阻断刷新)
        await self._ensure_valid_stock_codes()
        if generation != self._load_generation:
            return None, False
        batch = await self._fetch_news_batch(0)
        if generation != self._load_generation:
            return None, False

        has_more = self._state.has_more_news  # batch 为 None 时保持不变
        if batch is not None:
            if batch.empty:
                # 空页 = 无新闻, 游标同步清空 (不残留指向旧窗口的边界)
                self._news_cursor = None
                self._set_state(news_rows=(), news_page=0, has_more_news=False, has_news_data=False)
                has_more = False
            else:
                new_rows = _df_to_news_rows(batch)
                has_more = len(batch) >= self.PAGE_SIZE
                # UIR-F04-004: 跨刷新重置游标到新首页最旧行, 旧游标不得沿用
                self._news_cursor = _news_cursor_from_batch(batch)
                self._set_state(
                    news_rows=new_rows,
                    news_page=0,
                    has_more_news=has_more,
                    has_news_data=True,
                )

        return batch, has_more

    async def load_next_page(self):
        """
        Load next page of news.
        Updates state.news_rows directly (无 dual-track).

        竞态安全: await 后重新读取 self._state.news_rows 获取最新快照,
        包含 await 期间 _on_news_service_update 可能追加的 NEW_ITEM.

        UIR-F04-004: 使用 (publish_time, id) 向后 keyset 游标而非 OFFSET，
        新头部插入不会使窗口位移；合并按稳定身份幂等去重。游标仅在成功写回
        后推进，失败/取消/代次失效均保持原游标，重试不跳项。

        Returns: (new_batch_df, has_more) or (None, 当前 has_more_news)。
        取数失败 (batch 为 None) 时保持 has_more_news 原值 (与 refresh_news 的
        None 语义一致): 单次 DB 抖动不永久关闭分页, 用户可重试加载更多。
        """
        if self._state.is_loading_more or not self._state.has_more_news:
            return None, self._state.has_more_news

        self._set_state(is_loading_more=True)
        current_gen = self._load_generation
        cursor = self._news_cursor

        try:
            next_page = self._state.news_page + 1
            new_batch = await self._fetch_news_batch(next_page, cursor)

            # Check if generation changed (e.g. Refresh clicked while loading)
            if current_gen != self._load_generation:
                logger.info("[HomeVM] Load next page aborted due to generation change")
                return None, False

            if new_batch is None:
                # 取数失败: 保持 has_more_news 原值, 分页不因单次故障被永久关闭
                # (与 refresh_news 中 "batch 为 None 时保持不变" 的语义一致)。
                return None, self._state.has_more_news

            if new_batch.empty:
                self._set_state(has_more_news=False)
                return pd.DataFrame(), False

            new_rows = _df_to_news_rows(new_batch)
            # 关键: await 后重新读取 self._state.news_rows (最新快照)
            # 可能被 _on_news_service_update(NEW_ITEM) 追加过
            current_rows = self._state.news_rows
            batch_cursor = _news_cursor_from_batch(new_batch)
            if batch_cursor is not None:
                # 有稳定身份 (id) 才按身份幂等合并; 推送项若也落在本页窗口内不重复展示
                fresh_rows = _merge_news_rows(current_rows, new_rows)
                # 成功后推进游标到本页最旧 DB 行 (与是否有重复无关, 保证窗口向后)
                self._news_cursor = batch_cursor
            else:
                # 无 id 的旧数据/测试替身无法建立不变量, 保持原追加语义
                fresh_rows = new_rows
            self._set_state(
                news_rows=current_rows + fresh_rows,
                news_page=next_page,
                has_more_news=len(new_batch) >= self.PAGE_SIZE,
            )
            return new_batch, len(new_batch) >= self.PAGE_SIZE

        finally:
            # UIR-F04-003: 只有所属请求可清 loading; 代次已失效的旧分页不得
            # 清除后继 (新刷新/新分页) 的所有权 loading。
            if current_gen == self._load_generation:
                self._set_state(is_loading_more=False)

    async def _fetch_news_batch(self, page, cursor: tuple[datetime, int] | None = None):
        """取一页快讯。

        UIR-F04-004: 传入 ``cursor=(publish_time, id)`` 时走向后 keyset 查询
        (offset=0)；无游标 (首页/旧数据无 id 列) 时保留 OFFSET 调用兼容。
        """
        try:
            processor = await self._ensure_processor()
            if cursor is not None:
                cursor_time, cursor_id = cursor
                return await processor.cache.market_dao.get_market_news(
                    limit=self.PAGE_SIZE,
                    offset=0,
                    cursor_publish_time=cursor_time,
                    cursor_id=cursor_id,
                )
            offset = page * self.PAGE_SIZE
            return await processor.cache.market_dao.get_market_news(
                limit=self.PAGE_SIZE,
                offset=offset,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[HomeVM] Error fetching news: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[HomeVM] Error fetching news traceback", exc_info=True)
            return None

    def clear_state(self):
        """Reset state (e.g. on cache clear).

        P1-3 批次 2: 扩展重置 is_loading_more (修复 bug: 原 clear_state 不重置导致卡在 True)
        + 新增的 is_loading/has_market_data/has_news_data/load_error 字段。

        UIR-F04-003: 清空后旧刷新/分页的结果与收尾永久失效, 故先推进两个代次。
        """
        self._load_generation += 1
        self._view_load_generation += 1
        # UIR-F04-004: 游标同步清空, 避免旧分页边界残留
        self._news_cursor = None
        self._set_state(
            news_rows=(),
            news_page=0,
            has_more_news=False,
            is_loading_more=False,  # P1-3: 修复 bug (原 clear_state 不重置)
            market_indices=(),
            market_hsgt=HsgtRow(),
            market_hot_concepts=(),
            market_date="--",
            market_stale=False,
            market_indices_date=None,  # UIR-F03-002: 分量日期同步重置
            market_hsgt_date=None,
            market_header_key="home_data_date",
            # P1-3 批次 2: 新增 4 字段
            is_loading=False,
            has_market_data=False,
            has_news_data=False,
            load_error=None,
        )

    def set_load_error(self, message: Message | None) -> None:
        """设置/清除加载错误状态 (P1-3 批次 2)."""
        self._set_state(load_error=message)

    def set_loading(self, is_loading: bool) -> None:
        """设置加载状态 (P1-3 批次 2)."""
        self._set_state(is_loading=is_loading)


# ============================================================
# 纯转换函数 (DataFrame/dict → tuple[Row, ...])
# 模块级, 无副作用, 可独立测试
# ============================================================


def _detect_ai_tagged(tags: str, source: str) -> bool:
    """检测新闻是否被 AI 打标 (tags 含 AI_ 前缀或 source 标识 AI 来源).

    新闻经 LLM 分析后, tags 中可能出现 'AI_' 前缀标签; source 为 'AI' 时亦视为 AI 生成.
    """
    if not tags and not source:
        return False
    # 精确匹配: 仅 bare "AI" 标签或 "AI_" 前缀标签视为 AI 打标,
    # 避免 "AIRLINE"/"AIG" 等恰好以 AI 开头的普通标签误判.
    for t in (tags or "").split(","):
        t = t.strip().upper()
        if t == "AI" or t.startswith("AI_"):
            return True
    return (source or "").upper() == "AI"


def _coerce_sentiment(value: Any) -> str | None:
    """将来源 sentiment 规整为 ``str | None``（R21：不伪装缺失）.

    DB NULL 经 DataFrame 常表现为 ``NaN``（float），实时 item 可能无该键或为空串；
    非字符串/空白一律归一为 ``None``，交由 View 渲染中性样式。
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _news_item_to_row(item: Any) -> NewsRow:
    """单个 news item (dict) → NewsRow.

    复用 CacheManager.normalize_news_item 保证字段一致
    (N3-5 起 normalize 透传 sentiment；此处仍从原始 item 读取，幂等).
    """
    from data.cache.cache_manager import CacheManager

    normalized = CacheManager.normalize_news_item(item, default_source="CLS")
    tags = str(normalized.get("tags", "") or "")
    source = str(normalized.get("source", "") or "")
    return NewsRow(
        content=str(normalized.get("content", "") or ""),
        tags=tags,
        source=source,
        publish_time=str(normalized.get("publish_time", "") or ""),
        is_ai_tagged=_detect_ai_tagged(tags, source),
        sentiment=_coerce_sentiment(item.get("sentiment")),
    )


def _coerce_news_id(value: Any) -> int | None:
    """规整 market_news.id 为 ``int | None``（缺失/NaN/不可解析 → None）。

    稳定身份缺失不得用 0 等业务合法值顶替（R21），由调用方退化到
    (publish_time, content) 复合键。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _df_to_news_rows(df: pd.DataFrame | None) -> tuple[NewsRow, ...]:
    """DataFrame → tuple[NewsRow, ...] (L771 合规)."""
    if df is None or df.empty:
        return ()
    return tuple(
        NewsRow(
            content=str(row.get("content", "") or ""),
            tags=str(row.get("tags", "") or ""),
            source=str(row.get("source", "") or ""),
            publish_time=str(row.get("publish_time", "") or ""),
            is_ai_tagged=_detect_ai_tagged(str(row.get("tags", "") or ""), str(row.get("source", "") or "")),
            sentiment=_coerce_sentiment(row.get("sentiment")),
            news_id=_coerce_news_id(row.get("id")),
        )
        for row in df.to_dict("records")
    )


def _news_text_key(row: NewsRow) -> tuple[str, str] | None:
    """无 id 行的退化身份键；正文为空时无法建立身份，返回 None（不去重）。"""
    if not row.content:
        return None
    return (row.publish_time, row.content)


def _merge_news_rows(existing: tuple[NewsRow, ...], incoming: tuple[NewsRow, ...]) -> tuple[NewsRow, ...]:
    """按稳定身份返回 ``existing`` 中不存在的新行（UIR-F04-004 幂等合并）。

    身份优先 ``market_news.id``；实时推送项尚未回读 DB (id=None) 时退化为
    ``(publish_time, content)`` 复合键——不同时间同正文是合法新闻，故禁止
    仅按 content 去重。空正文行无法建立身份，按原样保留。
    """
    seen_ids = {row.news_id for row in existing if row.news_id is not None}
    seen_text = {key for row in existing if (key := _news_text_key(row)) is not None}
    fresh: list[NewsRow] = []
    for row in incoming:
        if row.news_id is not None and row.news_id in seen_ids:
            continue
        text_key = _news_text_key(row)
        if text_key is not None and text_key in seen_text:
            continue
        fresh.append(row)
        if row.news_id is not None:
            seen_ids.add(row.news_id)
        if text_key is not None:
            seen_text.add(text_key)
    return tuple(fresh)


def _news_cursor_from_batch(df: pd.DataFrame | None) -> tuple[datetime, int] | None:
    """取批次最后一行（窗口最旧边界）的 ``(publish_time, id)`` 作为向后游标。

    批次按 ``publish_time DESC, id DESC`` 排序，最后一行即当前窗口最旧行。
    无 id 列（旧数据/测试替身）或时间不可解析时返回 None，调用方回退 OFFSET。
    """
    if df is None or df.empty:
        return None
    last = df.to_dict("records")[-1]
    news_id = _coerce_news_id(last.get("id"))
    if news_id is None:
        return None
    raw_time = last.get("publish_time")
    if raw_time is None or raw_time == "":
        return None
    try:
        parsed = pd.to_datetime(raw_time)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime(), news_id


def _coerce_as_of(value: Any) -> str | None:
    """UIR-F03-002: 规整逐分量 as_of 日期。

    服务端契约: str (交易日) 或 None (无有效数据/未知)。非 str 脏值 (旧缓存
    或异常来源) 一律归 None —— 不猜日期、不复制页头日期冒充分量归属 (R21)。
    """
    return value if isinstance(value, str) and value else None


def _market_data_to_state_fields(data: dict) -> dict[str, Any]:
    """dict → HomeState 字段 dict (供 _set_state 使用).

    将 market_data dict 转换为 tuple[MarketIndexRow, ...] 等不可变字段.
    """
    indices = data.get("indices") or []
    indices_as_of = _coerce_as_of(data.get("indices_date"))
    market_indices = tuple(
        MarketIndexRow(
            name=str(idx.get("name", "")),
            value=str(idx.get("value", "--")),
            change=str(idx.get("change", "--")),
            color=str(idx.get("color", "")),
            as_of=indices_as_of,
        )
        for idx in indices
        if isinstance(idx, dict)
    )

    hsgt = data.get("hsgt") or {}
    hsgt_as_of = _coerce_as_of(data.get("hsgt_date"))
    market_hsgt = HsgtRow(
        value=str(hsgt.get("value", "--")),
        color=str(hsgt.get("color", "")),
        sub=str(hsgt.get("sub", "--")),
        as_of=hsgt_as_of,
    )

    hot_concepts = data.get("hot_concepts") or []
    market_hot_concepts = tuple(
        HotConceptRow(
            name=str(item.get("name", "--")),
            change=str(item.get("change", "0.00%")),
            color=str(item.get("color", "")),
        )
        for item in hot_concepts
        if isinstance(item, dict)
    )

    market_stale = bool(data.get("stale", False))
    # UIR-F03-002: 页头 key 由 VM 派生。混合日期判定要求两分量 as_of 均已知
    # (str) 且不等 —— 任一分量未知 (None) 不判混合, 回退到 stale 单一日期文案,
    # 避免对缺失分量伪称"更早交易日" (R21)。
    if market_stale and indices_as_of and hsgt_as_of and indices_as_of != hsgt_as_of:
        header_key = "market_data_mixed_dates"
    elif market_stale:
        header_key = "market_data_cached_at"
    else:
        header_key = "home_data_date"

    return {
        "market_indices": market_indices,
        "market_hsgt": market_hsgt,
        "market_hot_concepts": market_hot_concepts,
        "market_date": str(data.get("date", "--")),
        "market_stale": market_stale,
        "market_indices_date": indices_as_of,
        "market_hsgt_date": hsgt_as_of,
        "market_header_key": header_key,
    }
