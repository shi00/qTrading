"""ScreenerViewModel 分页与排序 mixin（C3-3 拆分）。

从 ``screener_view_model.py`` 按职责拆出：分页唯一 owner ``_update_pagination``、
当前页切片 ``_build_current_page_rows``、排序 ``sort_data``/``_sort_helper``、
翻页/改页大小/过滤 ``change_page``/``change_page_size``/``set_stock_filter``。

本 mixin 无独立状态，仅依赖宿主 VM（组合后的 ``ScreenerViewModel``）提供的
``_state``/``_full_results``/``_set_state`` 等共享成员；cross-mixin 方法
（如 ``_update_pagination``）亦在组合实例上解析。``_state`` 以类级注解声明
以满足类型检查（沿用 observable_mixin.py 的 mixin 类型化惯例）。
"""

from __future__ import annotations

import json
import logging
import typing
from collections.abc import Callable
from types import MappingProxyType

import pandas as pd

from strategies.attribution import ATTRIBUTION_COLUMN
from ui.viewmodels import Message
from ui.viewmodels.screener_types import ScreenerRow, ScreenerState
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

# MAJOR-06: AI 三分区顺序 (VM 侧唯一正本, 同时作为页签取值)。顺序即页签展示顺序,
# 也是「请求分组为空时回落到首个非空分组」的优先级 (默认「推荐」)。
AI_SECTION_KEYS: tuple[str, str, str] = ("recommended", "excluded", "failed")


def _decode_cell(col: str, value) -> object:
    """UX-04: 把结果行单元格转为 row.values 中的可消费值。

    仅对归因列把 JSON 字符串反序列化为 dict (二次检视 m4)；其余列原样返回。
    非法/缺失 JSON 安全降级为 None，不 raise，避免 HISTORY 等无归因列模式击穿。
    """
    if col != ATTRIBUTION_COLUMN or value is None:
        return value
    if isinstance(value, dict):  # 防御：已反序列化 (如某些路径)
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


class PaginationSortingMixin:
    """分页与排序职责（C3-3）。组合进 ``ScreenerViewModel``。"""

    _state: ScreenerState
    _full_results: pd.DataFrame | None
    _set_state: Callable[..., None]

    def _update_pagination(self, page_size: int | None = None, page_no: int | None = None, **changes) -> None:
        """Recompute pagination fields in state, then notify via _set_state.

        C2b H1: 唯一切片 owner。所有导致 ``_full_results`` 或过滤/排序/分页变化的数据写点
        必须汇入本方法，禁止在写点直接拼 ``_set_state(page_no/total_*/current_page_rows)``。
        本方法**单帧原子**地产出 ``page_no/total_items/total_pages/current_page_rows``，
        并可将写点的非分页字段经 ``**changes`` 一并原子落入同一帧（第 2 轮对抗检视 M-1/H-1：
        消除「新分页元数据 + 旧切片内容」或「mode 已切换 + 旧内容行」的陈旧中间帧）。
        current_page_rows 存 locale-neutral 原始行 (VM 不调 I18n)，View 渲染期按 locale 格式化。
        内容未变时 (如流式页满后仅 total_* 增长) 复用上一帧引用，使 View 侧格式化 memo 命中。

        不再由 caller 手动 _notify()：走 Mixin._set_state 统一路径（disposed guard +
        跨线程封送 + subscribers snapshot）。
        """
        ps = page_size if page_size is not None else self._state.page_size
        # 单帧原子: stock_filter 随 changes 在帧末才落入 state, 计算过滤须显式取本次值
        # (否则读旧 state.stock_filter, 过滤不生效 — M-1 单帧化时序)
        filtered = self._get_filtered_results(stock_filter=changes.get("stock_filter"))
        # UX-02: 本次结果是否含 AI 三态分区能力 — ai_status 列存在即 AI 管线介入过
        # (非AI策略 enable_ai_analysis=False / AI 未执行返回无该列的候选表)。
        # False 时 View 渲染单表而非三分区, 避免成功的数学筛选被误标「分析失败」
        # (05-explainability-ux UX-02; D7-3 三分区的非AI策略回归)。
        show_ai_sections = filtered is not None and not filtered.empty and "ai_status" in filtered.columns
        # MAJOR-06 (review 09-24): 分组先于分页 —— 页签请求的分组随 changes 在帧末才落入
        # state, 故与 stock_filter 同理须显式取本次值 (M-1 单帧化时序); 解析出的有效分组
        # (请求分组为空时回落首个非空分组) 回写同一帧, 保证页签 selected 与切片内容一致。
        requested_section = changes.pop("ai_active_section", self._state.ai_active_section)
        section_counts: tuple[int, int, int] = (0, 0, 0)
        active_section = self._state.ai_active_section
        page_source: pd.DataFrame | None = filtered
        if show_ai_sections:
            section_counts, active_section, page_source = self._ai_section_view(
                typing.cast("pd.DataFrame", filtered), requested_section
            )
        if page_source is not None:
            total_items = len(page_source)
            total_pages = (total_items + ps - 1) // ps
        else:
            total_items = 0
            total_pages = 0
        pn = page_no if page_no is not None else self._state.page_no
        # UX-04: 页码 clamp — 过滤/模式切换缩小 total_pages 后, 恢复的历史 page_no
        # 可能越界 (HISTORY 中修改过滤后 switch_to_realtime 恢复快照页码 → 空表格)
        pn = max(1, min(pn, total_pages)) if total_pages else 1
        rows = self._build_current_page_rows(page_source, pn, ps)
        # 内容未变时复用引用 (NaN 会导致 value 比较误判重建, 属安全侧: 额外重格式化而非陈旧命中)
        if rows == self._state.current_page_rows:
            rows = self._state.current_page_rows
        # D7-3: 当前页切片按 ai_status 拆三区 (与 current_page_rows 同帧原子, 保证分区与页/排序/过滤一致)
        # MAJOR-06: 分组先于分页后, 当前页切片已只含活动分组行, 故本拆分退化为「活动分组
        # 独占全页、其余桶为空」; 三分桶之和仍恒等于 current_page_rows (零丢失契约不变)。
        recommended, excluded, failed = self._split_page_rows_by_ai_status(rows)
        self._set_state(
            page_size=ps,
            page_no=pn,
            total_items=total_items,
            total_pages=total_pages,
            current_page_rows=rows,
            ai_recommended_rows=recommended,
            ai_excluded_rows=excluded,
            ai_failed_rows=failed,
            show_ai_sections=show_ai_sections,
            ai_section_counts=section_counts,
            ai_active_section=active_section,
            **changes,
        )

    @staticmethod
    def _ai_section_view(
        filtered: pd.DataFrame, requested_section: str
    ) -> tuple[tuple[int, int, int], str, pd.DataFrame | None]:
        """MAJOR-06: 对过滤后**全量**结果集「先分组」, 并解析活动分组 (供组内分页)。

        - 计数口径为**全量**分组条数 (非当前页切片长度), 供页签标签展示「全量 N 只」;
        - 请求分组为空 (或非法值) 时回落到首个非空分组, 默认「推荐」;
        - 全走向量化布尔掩码 (单次 O(n) 扫描), 不逐行查询, 不引入 N+1。

        Returns:
            (section_counts, active_section, page_source): ``section_counts`` 为
            (recommended, excluded, failed) 全量计数; ``page_source`` 为活动分组
            对应行集合, 交给 ``_build_current_page_rows`` 在**组内**分页。
        """
        status = filtered["ai_status"]
        rec_mask = status == "analyzed"
        exc_mask = status == "rejected"
        rec = int(rec_mask.sum())
        exc = int(exc_mask.sum())
        # 其余 (failed/skipped/ai_unavailable/policy_not_acknowledged/缺失) 全归 failed
        # —— 与 _split_page_rows_by_ai_status 同口径, 保证三分组计数之和 = 全量条数 (零丢失)
        counts: tuple[int, int, int] = (rec, exc, len(filtered) - rec - exc)
        index = AI_SECTION_KEYS.index(requested_section) if requested_section in AI_SECTION_KEYS else 0
        if counts[index] == 0:
            index = next((i for i, count in enumerate(counts) if count > 0), index)
        active = AI_SECTION_KEYS[index]
        if active == "recommended":
            page_source = filtered[rec_mask]
        elif active == "excluded":
            page_source = filtered[exc_mask]
        else:
            page_source = filtered[~(rec_mask | exc_mask)]
        return counts, active, typing.cast("pd.DataFrame", page_source)

    @staticmethod
    def _split_page_rows_by_ai_status(
        rows: tuple[ScreenerRow, ...],
    ) -> tuple[tuple[ScreenerRow, ...], tuple[ScreenerRow, ...], tuple[ScreenerRow, ...]]:
        """将当前页行按 ai_status 拆为 (recommended, excluded, failed) 三区 (D7-3).

        - ai_status == "analyzed"   → recommended (AI 已分析, 评分 > 0)
        - ai_status == "rejected"   → excluded (AI 已分析, 评分 = 0)
        - 其余所有值 (failed/skipped/ai_unavailable/policy_not_acknowledged/缺失)
          一律归入 failed, 保证 current_page_rows 行零丢失 (D7-3 对抗检视 ROE-1).
        """
        recommended: list[ScreenerRow] = []
        excluded: list[ScreenerRow] = []
        failed: list[ScreenerRow] = []
        for row in rows:
            status = row.values.get("ai_status")
            if status == "analyzed":
                recommended.append(row)
            elif status == "rejected":
                excluded.append(row)
            else:
                failed.append(row)
        return tuple(recommended), tuple(excluded), tuple(failed)

    @staticmethod
    def _build_current_page_rows(
        filtered: pd.DataFrame | None, page_no: int, page_size: int
    ) -> tuple[ScreenerRow, ...]:
        """从过滤结果切出当前页原始行 (locale-neutral, VM 不调 I18n, C2b 唯一切片 owner).

        Returns:
            tuple[ScreenerRow, ...]: 每行 ``values`` 为 MappingProxyType 只读映射;
            空数据返回空元组。
        """
        if filtered is None or filtered.empty:
            return ()
        start = (page_no - 1) * page_size
        end = start + page_size
        page_slice = filtered.iloc[start:end]
        return tuple(
            ScreenerRow(values=MappingProxyType({str(k): _decode_cell(str(k), v) for k, v in record.items()}))
            for record in page_slice.to_dict("records")  # type: ignore[call-overload]
        )

    async def sort_data(self, column_key: str, ascending: bool | None = None):
        """Sort data using ThreadPool to avoid blocking UI"""
        if self._full_results is None or self._full_results.empty:
            return

        if ascending is not None:
            sort_column = column_key
            sort_ascending = ascending
        elif self._state.sort_column == column_key:
            sort_ascending = not self._state.sort_ascending
            sort_column = column_key
        else:
            sort_column = column_key
            sort_ascending = True

        self._set_state(loading=True)

        try:
            # Offload sorting to thread
            sorted_df = await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._sort_helper,
                self._full_results,
                column_key,
                sort_ascending,
            )

            self._full_results = sorted_df
            # C2b H1: 排序变更后经唯一 owner 单帧原子重算分页与当前页切片
            # (第 2 轮对抗检视 M-1: 消除「loading=False + 旧未排序切片」陈旧中间帧)
            self._update_pagination(
                page_no=1,
                sort_column=sort_column,
                sort_ascending=sort_ascending,
                loading=False,
            )

        except Exception as e:
            logger.error("Sort failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            self._set_state(
                loading=False,
                status_message=Message("screener_sort_failed"),
                status_color="error",
            )

    @staticmethod
    def _sort_helper(df, col, ascending):
        """Static helper for pickling/thread safety"""
        try:
            return df.sort_values(by=col, ascending=ascending, na_position="last")
        except KeyError:
            return df

    def change_page(self, delta: int):
        new_page = self._state.page_no + delta
        if 1 <= new_page <= self._state.total_pages:
            # C2b H1: 翻页须重排当前页切片, 经唯一 owner 而非直改 page_no
            self._update_pagination(page_no=new_page)

    def change_page_size(self, new_size: int):
        """Update pagination size and jump back to page 1."""
        if new_size > 0 and new_size != self._state.page_size:
            self._update_pagination(page_size=new_size, page_no=1)

    def select_ai_section(self, section: str) -> None:
        """MAJOR-06: 切换 AI 分组页签 (分组先于分页) —— 回到该分组第 1 页并重算切片。

        非法取值或选择当前分组时幂等 no-op (Flet 分段控件可能触发取消选择/重复回调)。
        请求分组为空时由 ``_ai_section_view`` 回落到首个非空分组, 不回退为「全表」。
        """
        if section not in AI_SECTION_KEYS or section == self._state.ai_active_section:
            return
        self._update_pagination(page_no=1, ai_active_section=section)

    def _get_filtered_results(self, stock_filter: str | None = None) -> pd.DataFrame | None:
        """UX-04: 应用股票代码过滤 — ts_code 子串匹配 (case-insensitive, 字面量).

        Args:
            stock_filter: 显式过滤值 (None 时读 ``state.stock_filter``)。
                匹配前 strip; 空串/列缺失时跳过过滤返回全量。
        """
        if self._full_results is None or self._full_results.empty:
            return self._full_results
        code = (self._state.stock_filter if stock_filter is None else stock_filter).strip()
        if not code or "ts_code" not in self._full_results.columns:
            return self._full_results
        # regex=False: ts_code 含 "." (如 000001.SZ), 字面量匹配防通配误命中
        mask = self._full_results["ts_code"].astype(str).str.contains(code, case=False, na=False, regex=False)
        # bool Series 布尔索引返回 DataFrame; cast 收窄 pyright 对 pd 布尔掩码的联合推断
        return typing.cast("pd.DataFrame | None", self._full_results[mask])

    def set_stock_filter(self, value: str) -> None:
        """UX-04: 设置股票代码过滤 (深链/手动输入), 回到第 1 页并重算分页.

        原值存储不 strip (受控 TextField 光标保护: state 与输入框内容一致,
        防重渲染重置 value 导致光标跳动); 匹配时 ``_get_filtered_results``
        内部 strip。
        """
        if value == self._state.stock_filter:
            return  # 幂等: 相同值不触发重渲染
        # C2b H1: 过滤值变化后经唯一 owner 单帧原子重算分页与当前页切片
        # (M-1: page_no 不直写 _set_state, 避免「新 page_no + 旧过滤切片」陈旧帧)
        self._update_pagination(page_no=1, stock_filter=value)
