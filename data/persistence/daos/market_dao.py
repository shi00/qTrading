import datetime
import hashlib
import json
import logging
import typing

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

# news_risk_brief 成功态值域（S4 MM-06 条件 upsert 用）。
# R1（data 不得 import services）：与 services.news_insight_models.SUCCESS_STATUS
# 同值的字面量复制，由 test_market_dao 的 parity 测试守护两者一致。
_RISK_SUCCESS_STATUS: typing.Final[tuple[str, ...]] = ("analyzed_with_events", "analyzed_no_event")

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
        # 反查 id：平铺 $N 占位符 + 全部参数绑定（R4：占位符编号为代码自增整数，
        # 经普通字符串拼接写入 SQL 文本，禁用 f-string / .format 拼 SQL；值一律走 $N 参数绑定）。
        pairs = [(r["content_hash"], r["publish_time"]) for r in rows]
        placeholders = ",".join("($" + str(2 * i + 1) + ",$" + str(2 * i + 2) + ")" for i in range(len(pairs)))
        flat_params: list = [v for pair in pairs for v in pair]
        sql = "SELECT id, content_hash, publish_time FROM market_news "
        sql += "WHERE (content_hash, publish_time) IN (" + placeholders + ")"
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

        N3-5：sentiment 冲突时取 ``COALESCE(excluded, 旧值)``——处理循环 UPSERT
        补写打分结果；重试/重复打分失败（None）不清空已有值（与 tags 同范式）。
        N5-2：sentiment_score / sentiment_conf / sentiment_model 三打分列随载荷落库，
        冲突时同 COALESCE 范式（打分失败 None 不清空既有值）。

        S4 MM-06 情绪字段组三态分支（s4_plan §2.3）：
        - 载荷无 ``analysis_metadata``（或非 dict）→ 旧 SQL 逐字节保留
          （pending 期间旧链生产语义不变，S6 退役）；
        - ``analysis_metadata.sentiment_applied is True``（本次 LLM/GGUF 校验成功）→
          情绪四列 excluded 直写（整组原子写入；LLM 成功载荷 score/conf/model=None
          即清空旧 GGUF 数值组，不逐列继承），来源元数据写 ``analysis_metadata``，
          category_l1/l2 经 COALESCE 透传（缺失不清空既有分类）；
        - 非 True（失败/情绪缺失）→ 仅写 ``analysis_metadata``（最近尝试状态通道）+
          category 透传，旧成功情绪组整组保留（不逐列混写，R21 不伪装）。
        """
        content = news_item.get("content", "") or ""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        am = news_item.get("analysis_metadata")
        if not isinstance(am, dict) or not am:
            sql = """
                  INSERT INTO market_news ("content","content_hash","tags","publish_time","source","sentiment","sentiment_score","sentiment_conf","sentiment_model","created_at")
                  VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, CURRENT_TIMESTAMP)
                  ON CONFLICT("content_hash","publish_time") DO
                  UPDATE SET "tags" = COALESCE(excluded."tags", market_news."tags"),
                             "content" = excluded."content",
                             "source" = excluded."source",
                             "sentiment" = COALESCE(excluded."sentiment", market_news."sentiment"),
                             "sentiment_score" = COALESCE(excluded."sentiment_score", market_news."sentiment_score"),
                             "sentiment_conf" = COALESCE(excluded."sentiment_conf", market_news."sentiment_conf"),
                             "sentiment_model" = COALESCE(excluded."sentiment_model", market_news."sentiment_model")
                  """
            params = (
                content,
                content_hash,
                news_item.get("tags"),
                news_item.get("publish_time"),
                news_item.get("source", "Sina"),
                news_item.get("sentiment"),
                news_item.get("sentiment_score"),
                news_item.get("sentiment_conf"),
                news_item.get("sentiment_model"),
            )
            return await self._write_db(sql, params)

        am_json = json.dumps(am, ensure_ascii=False, default=str)
        if am.get("sentiment_applied") is True:
            sql = """
                  INSERT INTO market_news ("content","content_hash","tags","publish_time","source","category_l1","category_l2","sentiment","sentiment_score","sentiment_conf","sentiment_model","analysis_metadata","created_at")
                  VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, CURRENT_TIMESTAMP)
                  ON CONFLICT("content_hash","publish_time") DO
                  UPDATE SET "tags" = COALESCE(excluded."tags", market_news."tags"),
                             "content" = excluded."content",
                             "source" = excluded."source",
                             "category_l1" = COALESCE(excluded."category_l1", market_news."category_l1"),
                             "category_l2" = COALESCE(excluded."category_l2", market_news."category_l2"),
                             "sentiment" = excluded."sentiment",
                             "sentiment_score" = excluded."sentiment_score",
                             "sentiment_conf" = excluded."sentiment_conf",
                             "sentiment_model" = excluded."sentiment_model",
                             "analysis_metadata" = excluded."analysis_metadata"
                  """
            params = (
                content,
                content_hash,
                news_item.get("tags"),
                news_item.get("publish_time"),
                news_item.get("source", "Sina"),
                news_item.get("category_l1"),
                news_item.get("category_l2"),
                news_item.get("sentiment"),
                news_item.get("sentiment_score"),
                news_item.get("sentiment_conf"),
                news_item.get("sentiment_model"),
                am_json,
            )
        else:
            sql = """
                  INSERT INTO market_news ("content","content_hash","tags","publish_time","source","category_l1","category_l2","analysis_metadata","created_at")
                  VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, CURRENT_TIMESTAMP)
                  ON CONFLICT("content_hash","publish_time") DO
                  UPDATE SET "tags" = COALESCE(excluded."tags", market_news."tags"),
                             "content" = excluded."content",
                             "source" = excluded."source",
                             "category_l1" = COALESCE(excluded."category_l1", market_news."category_l1"),
                             "category_l2" = COALESCE(excluded."category_l2", market_news."category_l2"),
                             "analysis_metadata" = excluded."analysis_metadata"
                  """
            params = (
                content,
                content_hash,
                news_item.get("tags"),
                news_item.get("publish_time"),
                news_item.get("source", "Sina"),
                news_item.get("category_l1"),
                news_item.get("category_l2"),
                am_json,
            )
        return await self._write_db(sql, params)

    async def update_market_news_analysis(
        self,
        news_id: int,
        *,
        sentiment: str | None = None,
        sentiment_score: float | None = None,
        sentiment_conf: float | None = None,
        sentiment_model: str | None = None,
        category_l1: str | None = None,
        category_l2: str | None = None,
        analysis_metadata: typing.Mapping[str, typing.Any] | None = None,
    ) -> bool:
        """按 market_news.id 更新分析结果（S4 dedicated 二段写入 / 补写路径）。

        字段组语义与 :meth:`save_market_news` 三态一致：
        - ``analysis_metadata.sentiment_applied is True`` → 情绪四列直写
          （score/conf/model=None 即清空旧 GGUF 数值组）+ ``analysis_metadata``；
        - 非 True → 仅写 ``analysis_metadata``，旧成功情绪组整组保留；
        - category_l1/l2 经 COALESCE 透传（None 不清空既有分类）。

        Returns:
            bool: 是否命中行（rowcount > 0）。

        Raises:
            ValueError: ``analysis_metadata`` 非 dict / 空——调用契约破坏，显式失败不伪装。
        """
        if not isinstance(analysis_metadata, dict) or not analysis_metadata:
            raise ValueError("update_market_news_analysis requires a non-empty analysis_metadata mapping")
        am_json = json.dumps(analysis_metadata, ensure_ascii=False, default=str)
        if analysis_metadata.get("sentiment_applied") is True:
            sql = (
                'UPDATE market_news SET "sentiment" = $2, "sentiment_score" = $3, "sentiment_conf" = $4, '
                '"sentiment_model" = $5, "analysis_metadata" = $6::jsonb, '
                '"category_l1" = COALESCE($7::varchar, "category_l1"), '
                '"category_l2" = COALESCE($8::varchar, "category_l2") '
                'WHERE "id" = $1'
            )
            params = (
                news_id,
                sentiment,
                sentiment_score,
                sentiment_conf,
                sentiment_model,
                am_json,
                category_l1,
                category_l2,
            )
        else:
            sql = (
                'UPDATE market_news SET "analysis_metadata" = $2::jsonb, '
                '"category_l1" = COALESCE($3::varchar, "category_l1"), '
                '"category_l2" = COALESCE($4::varchar, "category_l2") '
                'WHERE "id" = $1'
            )
            params = (news_id, am_json, category_l1, category_l2)
        async with self._guarded_begin() as conn:
            result = await conn.exec_driver_sql(sql, params)
            return bool(getattr(result, "rowcount", 0))

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
            sql += " AND publish_time >= $" + str(idx)
            params.append(start_time)
            idx += 1
        if end_time is not None:
            sql += " AND publish_time <= $" + str(idx)
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
                clauses.append("(title ILIKE $" + str(idx) + " OR content ILIKE $" + str(idx + 1) + ")")
                params.extend([like, like])
                idx += 2
            sql += " AND (" + " OR ".join(clauses) + ")"
        sql += " ORDER BY publish_time DESC LIMIT $" + str(idx)
        params.append(limit)
        # 证据读取不得吞 DB 故障（§10.3）：_read_db 默认 suppress_errors=True 会把
        # 基础设施故障伪装成"空结果"，导致服务层误判 no_evidence（对抗性检视 Major①）。
        return await self._read_db(sql, params, suppress_errors=False)

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
            sql += " AND publish_time >= $" + str(idx)
            params.append(start_time)
            idx += 1
        if end_time is not None:
            sql += " AND publish_time <= $" + str(idx)
            params.append(end_time)
            idx += 1
        sql += " ORDER BY publish_time DESC"
        # 证据读取不得吞 DB 故障（§10.3）：与 get_telegraph_news_for_stocks 同理由，
        # suppress_errors=False 让 DB 故障显式抛异常，避免伪装成 no_evidence。
        return await self._read_db(sql, params, suppress_errors=False)

    async def get_scored_stock_news(
        self,
        ts_code: str,
        start_time: typing.Any = None,
        end_time: typing.Any = None,
    ):
        """按 ts_code 读取个股公告/个股新闻的打分行（N5-2 情绪指数聚合用）。

        限定 ``source_kind IN ('announcement','news')``（ts_code 直达口径，与
        ``get_market_news_documents`` 一致）；显式列含
        sentiment_score / sentiment_conf / sentiment_model 三打分列。
        telegraph 快讯不在此列（ts_code 恒为 NULL，由服务层经
        ``get_telegraph_news_for_stocks`` + ``match_news_to_stock`` 精确关联另行并入）。
        """
        if not ts_code:
            return pd.DataFrame()
        sql = (
            "SELECT id, content, title, publish_time, source, source_kind, "
            "sentiment, sentiment_score, sentiment_conf, sentiment_model FROM market_news"
        )
        sql += " WHERE ts_code = $1 AND source_kind IN ($2, $3)"
        params: list = [ts_code, "announcement", "news"]
        idx = 4
        if start_time is not None:
            sql += " AND publish_time >= $" + str(idx)
            params.append(start_time)
            idx += 1
        if end_time is not None:
            sql += " AND publish_time <= $" + str(idx)
            params.append(end_time)
        sql += " ORDER BY publish_time DESC"
        # 聚合读取不得吞 DB 故障（§10.3 同理由）：suppress_errors=False。
        return await self._read_db(sql, params, suppress_errors=False)

    async def get_telegraph_news_in_window(
        self,
        start_time: typing.Any = None,
        end_time: typing.Any = None,
        limit: int = 1000,
    ):
        """读取时间窗内全部 telegraph/存量快讯（N5-2 市场级情绪聚合用，无个股预筛）。

        口径与 ``get_telegraph_news_for_stocks`` 一致（``source_kind = 'telegraph'
        OR NULL``）；``【市场】`` 前缀过滤属业务语义，由服务层承担（DAO 保持通用）。
        """
        sql = "SELECT * FROM market_news WHERE (source_kind = $1 OR source_kind IS NULL)"
        params: list = ["telegraph"]
        idx = 2
        if start_time is not None:
            sql += " AND publish_time >= $" + str(idx)
            params.append(start_time)
            idx += 1
        if end_time is not None:
            sql += " AND publish_time <= $" + str(idx)
            params.append(end_time)
            idx += 1
        sql += " ORDER BY publish_time DESC LIMIT $" + str(idx)
        params.append(limit)
        # 聚合读取不得吞 DB 故障（§10.3 同理由）：suppress_errors=False。
        return await self._read_db(sql, params, suppress_errors=False)

    async def save_news_risk_brief(self, brief: dict) -> dict:
        """持久化 NewsRiskBrief 快照（S4 MM-06：Core 条件 upsert，s4_plan §4.3）。

        - 复合主键 ``(ts_code, input_hash)`` 冲突（Core ``on_conflict_do_update``，
          参数化 R4）；
        - 成功态（``_RISK_SUCCESS_STATUS``）无条件 upsert（允许刷新成功快照）；
        - 失败态 DO UPDATE 带 ``analysis_status NOT IN (成功态)`` 条件——既有成功快照
          保留（rowcount=0 → ``kept_success``），失败不得覆盖成功（§7.2）；
        - null_protected 列 ``coalesce(EXCLUDED, table)`` 防 None 覆盖既有非空结果；
        - created_at 不参与写入（server_default）；updated_at 冲突命中时刷新 now()；
        - ``_guarded_begin`` 事务内执行（R5 守卫 + EngineDisposedError 传播）。

        Returns:
            dict: ``{"written": bool, "kept_success": bool}``——written 表示本语句实际
            写入（insert 或 update）；kept_success 仅失败写入被条件挡下时为 True。
        """
        if not brief:
            return {"written": False, "kept_success": False}
        self._check_engine(context="upsert")
        table = NewsRiskBrief.__table__
        value_cols = [c for c in get_model_columns(NewsRiskBrief) if c in brief]
        values = {c: brief.get(c) for c in value_cols}
        ins = pg_insert(table).values(**values)
        null_protected = {c.name for c in table.columns if c.info.get("null_protected", False)}
        set_: dict[str, typing.Any] = {
            c: (sa.func.coalesce(ins.excluded[c], table.c[c]) if c in null_protected else ins.excluded[c])
            for c in values
        }
        set_["updated_at"] = sa.func.now()
        is_success = brief.get("analysis_status") in _RISK_SUCCESS_STATUS
        where = None if is_success else table.c.analysis_status.notin_(_RISK_SUCCESS_STATUS)
        stmt = ins.on_conflict_do_update(index_elements=["ts_code", "input_hash"], set_=set_, where=where)
        async with self._guarded_begin() as conn:
            result = await conn.execute(stmt)
            rowcount = int(result.rowcount or 0)
        return {"written": rowcount > 0, "kept_success": (not is_success) and rowcount == 0}

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
            sql += " AND ts_code = $" + str(idx)
            params.append(ts_code)
            idx += 1
        sd = self._to_db_date(start_date) if start_date else None
        if sd:
            sql += " AND trade_date >= $" + str(idx)
            params.append(sd)
            idx += 1
        ed = self._to_db_date(end_date) if end_date else None
        if ed:
            sql += " AND trade_date <= $" + str(idx)
            params.append(ed)
            idx += 1

        sql += " ORDER BY trade_date DESC"
        if limit:
            sql += " LIMIT $" + str(idx)
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
            sql += " AND trade_date >= $" + str(idx)
            params.append(sd)
            idx += 1
        ed = self._to_db_date(end_date) if end_date else None
        if ed:
            sql += " AND trade_date <= $" + str(idx)
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

    async def get_latest_indicators_bulk(self, ts_codes: list[str]) -> pd.DataFrame:
        """批量取多只股票各自最新交易日的估值/换手指标（DISTINCT ON，UX-09 MAJOR-04）。

        与 ``get_daily_indicators_bulk`` 的区别：本方法返回「每码最新一行」而非区间
        全量行，并补出 ps_ttm / dv_ttm（详情框估值区需要）。无记录的代码不出现。
        """
        if not ts_codes:
            return pd.DataFrame()
        sql_template = (
            "SELECT DISTINCT ON (ts_code) ts_code, trade_date, turnover_rate, pe, pe_ttm, pb, ps, ps_ttm, "
            "dv_ratio, dv_ttm, total_mv, circ_mv "
            "FROM daily_indicators WHERE ts_code IN ({placeholders}) "
            "ORDER BY ts_code, trade_date DESC"
        )
        return await self.chunked_in_query(self._read_db, sql_template, list(ts_codes))

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
            sql += " AND trade_date = $" + str(idx)
            params.append(td)
            idx += 1

        sql += " ORDER BY trade_date DESC"
        if limit:
            sql += " LIMIT $" + str(idx)
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
