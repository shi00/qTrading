import datetime
import hashlib
import logging
import typing

import pandas as pd

from data.persistence.models import (
    DailyIndicators,
    IndexWeight,
    MoneyflowHsgt,
    NewsRiskBrief,
    get_model_columns,
    get_model_pk_columns,
)

from data.constants import attach_hsgt_column_units
from data.news_match import _cn_len
from .base_dao import BaseDao

logger = logging.getLogger(__name__)

# B2: market_news 批量落库（_save_upsert）显式插入列；不含 id（自增主键）与 created_at（server_default）。
_MARKET_NEWS_BATCH_COLUMNS = [
    "content",
    "content_hash",
    "tags",
    "publish_time",
    "source",
    "ts_code",
    "title",
    "url",
    "source_kind",
    "category_l1",
    "category_l2",
    "sentiment",
]


def _row_to_dict(row: dict) -> dict:
    """清洗单行 dict：JSONB(id)/None 原样，float NaN→None，numpy 标量转原生类型。

    供 brief 读取方法（get_news_risk_brief / get_latest_success_brief）整合返回，避免
    把 ``events`` / ``evidence_news_ids`` / ``coverage`` JSONB 或数值列暴露 numpy/pandas 类型。
    """
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, (dict, list)) or v is None:
            out[k] = v
        elif isinstance(v, float) and pd.isna(v):
            out[k] = None
        elif hasattr(v, "item"):
            try:
                out[k] = v.item()  # type: ignore[attr-defined]  # [reason: 运行时 hasattr 守卫已确认 v 为 numpy 标量（numpy2 不再子类 float），pyright 无法静态识别 numpy 基元 .item()]
            except (ValueError, TypeError):
                out[k] = v
        else:
            out[k] = v
    return out


class MarketDao(BaseDao):
    """DAO for Market News, Adjustment Factors, Index Weights, and HSGT Money Flow."""

    # --- Market News ---
    @staticmethod
    def build_content_hash(
        source_kind: str | None,
        content: str | None,
        title: str | None,
        ts_code: str | None,
    ) -> str | None:
        """按来源口径计算 market_news.content_hash（统一 sha256 hexdigest，64 字符）。

        口径（B1 已闭合，与 save_market_news 现状/Phase A 约定一致）：
          - telegraph / None：输入 = 入库前原始 content 全文（与 save_market_news 逐字节一致）。
          - announcement / news：输入 = `ts_code + source_kind + canonical_text`，
            canonical_text = content.strip() or title.strip()；两者皆空时拒绝（返回 None）。
        """
        if source_kind in ("announcement", "news"):
            canonical_text = (content or "").strip() or (title or "").strip()
            if not canonical_text:
                return None
            plain = f"{ts_code}{source_kind}{canonical_text}"
        else:
            plain = content or ""
        return hashlib.sha256(plain.encode("utf-8")).hexdigest()

    async def save_market_news_batch(self, docs: list[dict]) -> list[int]:
        """批量落库 news 文档并返回成功写入/命中的 market_news.id 列表（R8 合规）。

        - content_hash 由 build_content_hash 按来源分口径计算（R8 批量写入必须 _save_upsert）。
        - 冲突键 (content_hash, publish_time)，写入后按 `(content_hash, publish_time) IN (($1,$2),...)`
          一次反查 id，返回与输入 docs 同序的 id（未命中入库的行略过）。
        - publish_time 须传 naive datetime（DB 存 UTC naive），否则反查匹配不到。
        """
        if not docs:
            return []
        rows: list[dict] = []
        for doc in docs:
            content = doc.get("content") or ""
            title = doc.get("title")
            ts_code = doc.get("ts_code")
            source_kind = doc.get("source_kind")
            content_hash = self.build_content_hash(source_kind, content, title, ts_code)
            publish_time = doc.get("publish_time")
            if not content_hash or publish_time is None:
                logger.warning(
                    "[MarketDao] skip news doc without hash material or publish_time: source_kind=%s",
                    source_kind,
                )
                continue
            rows.append(
                {
                    "content": content,
                    "content_hash": content_hash,
                    "tags": doc.get("tags"),
                    "publish_time": publish_time,
                    "source": doc.get("source"),
                    "ts_code": ts_code,
                    "title": title,
                    "url": doc.get("url"),
                    "source_kind": source_kind,
                    "category_l1": doc.get("category_l1"),
                    "category_l2": doc.get("category_l2"),
                    "sentiment": doc.get("sentiment"),
                }
            )
        if not rows:
            return []
        df = pd.DataFrame(rows)
        await self._save_upsert(
            df,
            "market_news",
            columns=_MARKET_NEWS_BATCH_COLUMNS,
            pk_columns=["content_hash", "publish_time"],
            conflict_columns=["content_hash", "publish_time"],
        )
        # 反查 id：平铺 $N 占位符 + 全部参数绑定（R4：无字符串拼接用户输入）。
        pairs = [(r["content_hash"], r["publish_time"]) for r in rows]
        placeholders = ",".join(f"(${2 * i + 1},${2 * i + 2})" for i in range(len(pairs)))
        flat_params: list = [v for pair in pairs for v in pair]
        sql = (
            "SELECT id, content_hash, publish_time FROM market_news "
            f"WHERE (content_hash, publish_time) IN ({placeholders})"
        )
        df_ids = await self._read_db(sql, flat_params)
        lookup: dict = {}
        if df_ids is not None and not df_ids.empty:
            for _, r in df_ids.iterrows():
                lookup[(r["content_hash"], r["publish_time"])] = r["id"]
        return [lookup[p] for p in pairs if p in lookup]

    async def save_market_news(self, news_item: dict, wait: bool = False):
        """Save a single market news item.

        Uses raw SQL because MarketNews has an auto-increment PK (id),
        but the UPSERT conflict key is (content_hash, publish_time).
        _save_upsert only supports PK-based conflict resolution.
        """
        content = news_item.get("content", "") or ""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        sql = """
              INSERT INTO market_news ("content","content_hash","tags","publish_time","source","created_at")
              VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
              ON CONFLICT("content_hash","publish_time") DO
              UPDATE SET "tags" = COALESCE(excluded."tags", market_news."tags"),
                         "content" = excluded."content",
                         "source" = excluded."source"
              """
        params = (
            content,
            content_hash,
            news_item.get("tags"),
            news_item.get("publish_time"),
            news_item.get("source", "Sina"),
        )
        return await self._write_db(sql, params)

    async def get_market_news(
        self,
        limit: int | None = 50,
        offset: int = 0,
        min_publish_time: typing.Any = None,
        home_filter: bool = True,
    ):
        sql = "SELECT * FROM market_news WHERE 1=1"
        params = []
        idx = 1
        if min_publish_time:
            # review03-C7: 占位符编号为代码自增整数（非用户输入），普通拼接避免 f-string 拼 SQL
            sql += " AND publish_time >= $" + str(idx)
            params.append(min_publish_time)
            idx += 1
        # 首页来源过滤（A2）：只返回快讯/存量（source_kind IS NULL）行，剔除 announcement/news 文档。
        if home_filter:
            sql += " AND (source_kind = $" + str(idx) + " OR source_kind IS NULL)"
            params.append("telegraph")
            idx += 1
        sql += " ORDER BY publish_time DESC LIMIT $" + str(idx) + " OFFSET $" + str(idx + 1)
        params.extend([limit, offset])
        return await self._read_db(sql, params)

    async def get_telegraph_news_for_stocks(
        self,
        ts_codes: list[str],
        names: list[str],
        start_time: typing.Any = None,
        end_time: typing.Any = None,
        limit: int = 200,
    ):
        """按候选股池（ts_code / 已确认名称）参数化预筛首页快讯候选（B4）。

        限定 ``source_kind = 'telegraph' OR source_kind IS NULL``；DB 侧 LIKE 预筛，
        避免"先取全市场快讯再内存匹配"（§8.4）。最终精确关联由 data/news_match 纯函数承担。

        构造规则：对每个候选 suffix6 代码与唯一名称（>=3 汉字）生成一个
        ``(title ILIKE $N OR content ILIKE $N+1)`` 条件并用 OR 连接，全部参数绑定（R4）。
        """
        if not ts_codes and not names:
            return pd.DataFrame()
        sql = "SELECT * FROM market_news WHERE (source_kind = $1 OR source_kind IS NULL)"
        params: list = ["telegraph"]
        idx = 2
        if start_time is not None:
            sql += f" AND publish_time >= ${idx}"
            params.append(start_time)
            idx += 1
        if end_time is not None:
            sql += f" AND publish_time <= ${idx}"
            params.append(end_time)
            idx += 1

        patterns: list[str] = []
        seen: set[str] = set()
        for code in ts_codes or []:
            suffix = code.split(".")[0]
            if suffix and suffix not in seen:
                seen.add(suffix)
                patterns.append(suffix)
        for name in names or []:
            name = name.strip()
            if name and _cn_len(name) >= 3 and name not in seen:
                seen.add(name)
                patterns.append(name)

        if patterns:
            clauses = []
            for pat in patterns:
                like = f"%{pat}%"
                clauses.append(f"(title ILIKE ${idx} OR content ILIKE ${idx + 1})")
                params.extend([like, like])
                idx += 2
            sql += " AND (" + " OR ".join(clauses) + ")"
        sql += f" ORDER BY publish_time DESC LIMIT ${idx}"
        params.append(limit)
        return await self._read_db(sql, params)

    async def get_market_news_documents(
        self,
        ts_code: str,
        start_time: typing.Any = None,
        end_time: typing.Any = None,
    ):
        """按 ts_code 读取个股证据文档（source_kind in announcement/news），返回 evidence 候选。

        编排：服务层将 NewsFetcher 实时抓取的公告/新闻先经 ``save_market_news_batch`` 落库取
        id，再统一经本查询按 ts_code 读取落库后的公告/新闻作为证据基集（同时覆盖历史已入库行）。
        ``telegraph`` 快讯不在此列（其 ts_code 恒为 NULL，打字由服务层 ``match_news_to_stock`` 判定）。
        """
        if not ts_code:
            return pd.DataFrame()
        sql = "SELECT id, content, content_hash, title, url, source_kind, source, sentiment, publish_time FROM market_news"
        sql += " WHERE ts_code = $1"
        params: list = [ts_code]
        idx = 2
        if start_time is not None:
            sql += f" AND publish_time >= ${idx}"
            params.append(start_time)
            idx += 1
        if end_time is not None:
            sql += f" AND publish_time <= ${idx}"
            params.append(end_time)
            idx += 1
        sql += " ORDER BY publish_time DESC"
        return await self._read_db(sql, params)

    async def save_news_risk_brief(self, brief: dict) -> None:
        """持久化 NewsRiskBrief 快照（R8：走 _save_upsert 主键冲突路径）。

        - 复合主键 ``(ts_code, input_hash)`` 走主键冲突；结果列已在模型声明
          ``null_protected``，避免新写入 NULL 覆盖既有成功结果（§7.2 失败不覆盖成功）。
        - ``updated_at`` 由 _save_upsert 在冲突命中时自动刷新。
        """
        if not brief:
            return
        df = pd.DataFrame([brief])
        await self._save_upsert(
            df,
            "news_risk_brief",
            columns=get_model_columns(NewsRiskBrief),
            pk_columns=["ts_code", "input_hash"],
        )

    async def get_news_risk_brief(
        self,
        ts_code: str,
        input_hash: str,
    ) -> dict | None:
        """按 (ts_code, input_hash) 读取某次输入的快照（缓存命中）。无则 None。"""
        sql = "SELECT * FROM news_risk_brief WHERE ts_code = $1 AND input_hash = $2 LIMIT 1"
        df = await self._read_db(sql, [ts_code, input_hash])
        if df is None or df.empty:
            return None
        row = df.iloc[0].to_dict()
        return _row_to_dict(row)

    async def get_latest_success_brief(
        self,
        ts_code: str,
        window_start: typing.Any,
        window_end: typing.Any,
    ) -> dict | None:
        """§11 子集例外：查 (ts_code, window) 下最近一次成功快照（created_at 最新）。

        成功状态 = ``analyzed_with_events`` / ``analyzed_no_event``。用于判断本次证据集是否
        为既有成功快照证据集的真子集，以复用更完整结果、避免部分来源失败造成重复付费分析。
        """
        sql = (
            "SELECT * FROM news_risk_brief "
            "WHERE ts_code = $1 AND window_start = $2 AND window_end = $3 "
            "AND analysis_status IN ($4, $5) ORDER BY created_at DESC LIMIT 1"
        )
        df = await self._read_db(
            sql,
            [ts_code, window_start, window_end, "analyzed_with_events", "analyzed_no_event"],
        )
        if df is None or df.empty:
            return None
        return _row_to_dict(df.iloc[0].to_dict())

    # --- Daily Indicators ---
    async def save_daily_indicators(self, df: pd.DataFrame, suppress_errors: bool = False):
        """
        Save Daily Indicators (PE, PB, etc.). Table: daily_indicators
        :param suppress_errors: If True (default), log errors but returns 0. If False, raises Exception.
        """
        if df is None or df.empty:
            return 0
        columns = get_model_columns(DailyIndicators)
        pk_columns = get_model_pk_columns(DailyIndicators)

        return await self._save_upsert(
            df,
            "daily_indicators",
            columns,
            pk_columns=pk_columns,
            suppress_errors=suppress_errors,
        )

    async def get_daily_indicators(
        self,
        ts_code: str | None = None,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        limit: int | None = None,
    ):
        """Get Daily Indicators."""
        sql = "SELECT * FROM daily_indicators WHERE 1=1"
        params = []
        idx = 1
        if ts_code:
            sql += f" AND ts_code = ${idx}"
            params.append(ts_code)
            idx += 1
        sd = self._to_db_date(start_date) if start_date else None
        if sd:
            sql += f" AND trade_date >= ${idx}"
            params.append(sd)
            idx += 1
        ed = self._to_db_date(end_date) if end_date else None
        if ed:
            sql += f" AND trade_date <= ${idx}"
            params.append(ed)
            idx += 1

        sql += " ORDER BY trade_date DESC"
        if limit:
            sql += f" LIMIT ${idx}"
            params.append(limit)

        return await self._read_db(sql, params)

    async def get_daily_indicators_bulk(
        self,
        ts_code_list: list,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
    ):
        """
        批量获取多只股票的 daily_indicators 数据。
        解决 N+1 查询问题，一次查询获取所有候选股票的指标数据。

        Args:
            ts_code_list: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame 包含所有指定股票的指标数据
        """
        if not ts_code_list:
            return await self._read_db("SELECT * FROM daily_indicators WHERE 1=0", [])

        sql = "SELECT ts_code, trade_date, turnover_rate, turnover_rate_f, volume_ratio, pe, pe_ttm, pb, total_mv, circ_mv FROM daily_indicators WHERE 1=1"
        params = []
        idx = 1

        sd = self._to_db_date(start_date) if start_date else None
        if sd:
            sql += f" AND trade_date >= ${idx}"
            params.append(sd)
            idx += 1
        ed = self._to_db_date(end_date) if end_date else None
        if ed:
            sql += f" AND trade_date <= ${idx}"
            params.append(ed)
            idx += 1

        if ts_code_list:
            sql_template = sql + " AND ts_code IN ({placeholders})"
            df = await self.chunked_in_query(
                self._read_db,
                sql_template,
                ts_code_list,
                extra_params=params,
            )
            if not df.empty:
                sort_cols = [c for c in ["ts_code", "trade_date"] if c in df.columns]
                if sort_cols:
                    df = df.sort_values(sort_cols, ignore_index=True)
            return df

        sql += " ORDER BY ts_code, trade_date"
        return await self._read_db(sql, params)

    # --- Index Weights ---
    async def save_index_weights(self, df: pd.DataFrame):
        """Save Index Component Weights. Table: index_weight"""
        if df is None or df.empty:
            return 0
        columns = get_model_columns(IndexWeight)
        pk_columns = get_model_pk_columns(IndexWeight)
        return await self._save_upsert(
            df,
            "index_weight",
            columns,
            pk_columns=pk_columns,
        )

    async def get_index_weights(self, index_code: str | None, trade_date: str | None):
        sql = "SELECT * FROM index_weight WHERE index_code = $1 AND trade_date = $2"
        return await self._read_db(sql, (index_code, self._to_db_date(trade_date)))

    async def get_latest_index_weight_date(self):
        """Get latest trade_date in index_weight."""
        df = await self._read_db("SELECT MAX(trade_date) as max_date FROM index_weight")
        if df is not None and not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    # --- Northbound Moneyflow ---
    async def save_moneyflow_hsgt(self, df: pd.DataFrame):
        """Save Northbound (HSGT) Moneyflow. Table: moneyflow_hsgt"""
        if df is None or df.empty:
            return 0
        columns = get_model_columns(MoneyflowHsgt)
        pk_columns = get_model_pk_columns(MoneyflowHsgt)

        # Tushare returns moneyflow_hsgt numeric fields as strings!
        # Postgres asyncpg is strictly typed (FLOAT). We must forcibly coerce them.
        import pandas as pd

        df = df.copy()
        for col in columns[1:]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return await self._save_upsert(
            df,
            "moneyflow_hsgt",
            columns,
            pk_columns=pk_columns,
        )

    async def get_moneyflow_hsgt(self, trade_date: datetime.date | str | None = None, limit: int | None = None):
        """Get Northbound Money Flow."""
        sql = "SELECT * FROM moneyflow_hsgt WHERE 1=1"
        params = []
        idx = 1
        td = self._to_db_date(trade_date) if trade_date else None
        if td:
            sql += f" AND trade_date = ${idx}"
            params.append(td)
            idx += 1

        sql += " ORDER BY trade_date DESC"
        if limit:
            sql += f" LIMIT ${idx}"
            params.append(limit)

        df = await self._read_db(sql, params)
        if df is not None and not df.empty:
            df = attach_hsgt_column_units(df)
        return df

    async def get_moneyflow_hsgt_range(self, start_date: str, end_date: str):
        sql = "SELECT * FROM moneyflow_hsgt WHERE trade_date >= $1 AND trade_date <= $2 ORDER BY trade_date DESC"
        df = await self._read_db(sql, [self._to_db_date(start_date), self._to_db_date(end_date)])
        if df is not None and not df.empty:
            df = attach_hsgt_column_units(df)
        return df
