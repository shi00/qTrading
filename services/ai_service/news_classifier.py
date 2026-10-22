"""AIService 新闻分类子模块（review01-A5b-2）。

自 ``services/ai_service.py`` 移出的 ``classify_news`` / ``_parse_news_result``
完整方法体及其专属常量（``NEWS_TEXT_MAX_LEN``）。

设计约定（测试兼容）：
- ``NewsClassifier`` 经构造参数持有 ``AIService`` 实例，经 ``self._service``
  访问共享状态（``_chat_completion``），本模块不 import ``services.ai_service``
  顶层符号，避免循环依赖。
- 跨子模块方法一律经组合根（``self._service``）调用，保证测试对 ``AIService``
  实例属性（如 ``svc._chat_completion = AsyncMock(...)``）的 monkeypatch 生效。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any, Final, cast

from core.model_management_types import (
    AdapterResponse,
    InferenceRequest,
    LegacyDispatchBlockedError,
    ModelOutputValidationError,
    ScenarioExecutionResult,
    ValidatedBusinessOutput,
)
from services.ai_service.scenario_executor import ScenarioOutput
from utils.config_models import NEWS_CATEGORY_MAP
from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation

if TYPE_CHECKING:
    from services.ai_service import AIService
    from services.ai_service.scenario_dispatch import ScenarioDispatch

logger = logging.getLogger(__name__)

# classify_news 中外部新闻原文长度截断
NEWS_TEXT_MAX_LEN = 500

# S4（§8.1）：情绪取值契约与存量入库 code 一致（news_subscription_service
# _LLM_SENTIMENT_CODES / MarketNews.sentiment 列），严格大小写；非法/缺失归 None（R21）。
_SENTIMENT_ENUM: Final[tuple[str, ...]] = ("Positive", "Neutral", "Negative")

# F24 断裂 3：GGUF 专用情绪链 label → 入库 code 映射。与 news_subscription_service
# _SENTIMENT_LABEL_TO_CODE 同契约（scorer head.npz 标签序 _LABELS_REQUIRED 为唯一事实源）；
# 不跨模块 import（news_subscription_service 顶层 import AIService，反向引入会成环）。
_GGUF_LABEL_TO_CODE: Final[dict[str, str]] = {"利好": "Positive", "中性": "Neutral", "利空": "Negative"}

# bundled 联合 prompt 的情绪输出要求（附加在主题 prompt 之后，一次请求同时产出主题+情绪）。
_BUNDLED_SENTIMENT_INSTRUCTION: Final[str] = (
    '同时判断该新闻的市场情绪倾向。必须在 JSON 中输出 "sentiment" 字段，'
    '取值严格为 "Positive" / "Neutral" / "Negative" 之一；无法判断时省略该字段，禁止编造。'
)

# dedicated 情绪子任务 system prompt（task_id="sentiment" 独立请求）。
_SENTIMENT_DEDICATED_SYSTEM: Final[str] = (
    "你是 A 股新闻情绪判定助手。对给定新闻判断市场情绪倾向，"
    '只输出 JSON：{"sentiment": "Positive" | "Neutral" | "Negative"}；无法判断时输出 {}。'
)


def _validate_bundled_output(response: AdapterResponse) -> ValidatedBusinessOutput:
    """bundled 联合输出分别校验（§8.1，s4_plan §2.1）。

    - 主题维度：JSON 可解析 + ``category_L1`` 在白名单，否则抛
      :class:`ModelOutputValidationError`（Executor 按失败表格切换候选）；
    - 情绪维度：缺失/非法归 ``None``（合法中性是校验成功的结果；非法值**不做**
      大小写转换成合法 Neutral——存量失败标记语义不伪装，R21），**不抛**；
    - 程序元数据（actual_model / 连接身份）经 metadata 携带，模型输出中同名字段
      不进入 output。
    """
    content = response.content
    if not content:
        raise ModelOutputValidationError("bundled output: empty content")
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        raise ModelOutputValidationError(f"bundled output: invalid json ({type(e).__name__})") from e
    if not isinstance(raw, dict):
        raise ModelOutputValidationError("bundled output: non-dict payload")

    l1 = (raw.get("category_L1") or "").strip().lower()
    if l1 not in NEWS_CATEGORY_MAP:
        raise ModelOutputValidationError(f"bundled output: L1 not in whitelist ({l1!r})")

    sentiment = raw.get("sentiment")
    if sentiment not in _SENTIMENT_ENUM:
        sentiment = None

    output = {
        "category_L1": l1,
        "category_L2": (raw.get("category_L2") or "").strip().lower(),
        "sentiment": sentiment,
    }
    metadata = {"actual_model": response.actual_model, **dict(response.metadata)}
    return ValidatedBusinessOutput(output=output, metadata=metadata)


def _finite_float(value: object) -> float | None:
    """数值列协议归一（F24）：有限 int/float → float；缺失/坏值/NaN/Inf → None（R21）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _validate_gguf_sentiment_output(response: AdapterResponse) -> ValidatedBusinessOutput:
    """GGUF 专用情绪协议校验（F24 断裂 3）：adapter 的 status/results JSON → 业务输出。

    - 仅接受 ``status ∈ {"success", "partial"}``（其余终态 adapter 已转译为异常，
      到达即契约破坏，抛错切换候选）；
    - 按 ``text_id="0"`` 取本请求（单文本）条目；条目缺失 / label 缺失或未知 →
      :class:`ModelOutputValidationError`（Executor 切换候选；全候选耗尽 →
      unavailable → 情绪缺失，R21 不伪装）；
    - score / confidence 经 :func:`_finite_float` 归一（坏值归 None，不因数值列
      坏而丢弃有效 label；合法中性/零分不误报缺失）；
    - ``sentiment_model`` 取 adapter 产出身份（actual_model = 模型文件 stem tag，
      与 sentiment 数值同源，产物溯源不丢）。
    """
    content = response.content
    if not content:
        raise ModelOutputValidationError("sentiment output: empty content")
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        raise ModelOutputValidationError(f"sentiment output: invalid json ({type(e).__name__})") from e
    if not isinstance(raw, dict):
        raise ModelOutputValidationError("sentiment output: non-dict payload")

    status = raw.get("status")
    if status not in ("success", "partial"):
        raise ModelOutputValidationError(f"sentiment output: invalid status ({status!r})")

    results = raw.get("results")
    if not isinstance(results, list) or not results:
        raise ModelOutputValidationError("sentiment output: results missing or empty")

    entry = next((r for r in results if isinstance(r, dict) and r.get("text_id") == "0"), results[0])
    if not isinstance(entry, dict):
        raise ModelOutputValidationError("sentiment output: result entry is not an object")

    raw_label = entry.get("label")
    code = _GGUF_LABEL_TO_CODE.get(raw_label) if isinstance(raw_label, str) else None
    if code is None:
        raise ModelOutputValidationError(f"sentiment output: invalid label ({raw_label!r})")

    metadata = {"actual_model": response.actual_model, **dict(response.metadata)}
    return ValidatedBusinessOutput(
        output={
            "sentiment": code,
            "sentiment_score": _finite_float(entry.get("score")),
            # confidence 为 adapter 协议字段（max(probabilities) 口径，与 scorer
            # apply_head_and_score 一致），校验器消费产物、不重复推导。
            "sentiment_conf": _finite_float(entry.get("confidence")),
            "sentiment_model": response.actual_model,
            "sentiment_status": status,
        },
        metadata=metadata,
    )


def _validate_sentiment_output(response: AdapterResponse) -> ValidatedBusinessOutput:
    """dedicated 情绪子任务输出校验（task_id="sentiment"）：无效输出抛错切换候选。

    F24 双协议分流（同一 sentiment 子路由候选可混排 litellm_chat / gguf_sentiment，
    校验器按 AdapterResponse.content 结构区分）：
    - GGUF 专用协议（含 ``results`` 键）→ :func:`_validate_gguf_sentiment_output`；
    - 聊天 LLM 协议（顶层 ``sentiment`` 白名单值）→ 原逻辑不变（缺失即抛，
      LLM 无校准数值，score/conf/model 三列不产出）。
    """
    content = response.content
    if not content:
        raise ModelOutputValidationError("sentiment output: empty content")
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        raise ModelOutputValidationError(f"sentiment output: invalid json ({type(e).__name__})") from e
    if not isinstance(raw, dict):
        raise ModelOutputValidationError("sentiment output: non-dict payload")

    if "results" in raw:
        return _validate_gguf_sentiment_output(response)

    sentiment = raw.get("sentiment")
    if sentiment not in _SENTIMENT_ENUM:
        raise ModelOutputValidationError(f"sentiment output: invalid value ({sentiment!r})")

    metadata = {"actual_model": response.actual_model, **dict(response.metadata)}
    return ValidatedBusinessOutput(output={"sentiment": sentiment}, metadata=metadata)


class NewsClassifier:
    """AIService 新闻分类子模块（review01-A5b-2）。

    承载 ``classify_news`` / ``_parse_news_result`` 完整方法体。持有 ``AIService``
    实例经 ``self._service`` 访问共享状态（``_chat_completion``），本模块不 import
    ``services.ai_service`` 顶层符号以避免循环依赖。
    """

    def __init__(self, service: AIService) -> None:
        self._service = service

    def _parse_news_result(self, raw_result: dict) -> dict:
        """
        Helper to normalize news classification result.
        Handles the L1/L2 category logic to provide a clean 'category' string for UI.
        L1/L2 codes are English enum values returned by the AI prompt,
        translated to locale-specific display names via I18n.

        防御性策略 (不信任 AI 响应):
        1. 输入归一化: strip + lower，应对 AI 大小写/空白波动
        2. 词典校验: L1 必须在 NEWS_CATEGORY_MAP，L2 必须在反向映射中
        3. 错位纠正: AI 将 L2 放到 L1 位置时，通过反向映射推导正确 L1
        4. L2 推导 L1: L1 无效但 L2 有效时，通过 L2 反推 L1
        5. 安全兜底: 任何无效层级不暴露英文编码，降级为本地化"资讯"
        """
        from core.i18n import I18n

        # 1. 输入归一化 (None 值经 `or ""` 转为空串，避免 str(None)="none" 被当作无效编码处理)
        l1_code = (raw_result.get("category_L1") or "").strip().lower()
        l2_code = (raw_result.get("category_L2") or "").strip().lower()

        # 2. 构建反向映射 (L2 -> L1)
        l2_to_l1_map: dict[str, str] = {}
        for l1, l2_list in NEWS_CATEGORY_MAP.items():
            for l2 in l2_list:
                l2_to_l1_map[l2] = l1

        is_valid_l1 = l1_code in NEWS_CATEGORY_MAP
        is_valid_l2 = l2_code in l2_to_l1_map

        # 3. 错位纠正: AI 错把 L2 作为 L1 输出 (例如 category_L1="macro_policy")
        if l1_code and l1_code in l2_to_l1_map and not is_valid_l1:
            if not l2_code:
                l2_code = l1_code
            l1_code = l2_to_l1_map[l1_code]
            is_valid_l1 = True
            is_valid_l2 = l2_code in l2_to_l1_map

        # 4. L2 推导 L1: L1 彻底错乱但 L2 合法
        if is_valid_l2 and not is_valid_l1:
            l1_code = l2_to_l1_map[l2_code]
            is_valid_l1 = True

        # 5. 翻译为本地化展示名
        l1_display = I18n.get(f"news_l1_{l1_code}", l1_code) if l1_code else ""
        l2_display = I18n.get(f"news_l2_{l2_code}", l2_code) if l2_code else ""

        # 6. 安全兜底隔离: L1 非法或缺少语言包退回原始英文 → 降级为"资讯"
        if not is_valid_l1 or (l1_code and l1_display == l1_code):
            l1_display = I18n.get("news_fallback_category", "Other")

        # 7. 安全兜底隔离: L2 非法或缺少语言包退回原始英文 → 完全剔除
        if not is_valid_l2 or (l2_code and l2_display == l2_code):
            l2_display = ""

        # 8. 拼接返回
        if l2_display and l1_display:
            final_category = f"{l1_display}-{l2_display}"
        elif l1_display:
            final_category = l1_display
        else:
            final_category = I18n.get("news_fallback_category", "Other")

        raw_result["category"] = final_category
        if "emoji" not in raw_result:
            raw_result["emoji"] = "📰"
        if "sentiment" not in raw_result:
            raw_result["sentiment"] = "Neutral"

        return raw_result

    @log_async_operation(
        operation_name="classify_news",
        threshold_ms=PerfThreshold.AI_INFERENCE,
    )
    async def classify_news(self, text: str) -> dict:
        """
        Classify news text using the unified scenario chain (S4) or Local/Cloud legacy chain.

        S4 请求级双轨分派（模型统一.md §12.2）：分派条件全部满足时走统一场景链
        （ScenarioExecutor，bundled/dedicated 见 :meth:`_classify_via_scenario`），
        否则保留旧链 local/cloud failover（pending 期间生产语义逐字节不变，S6 退役）。

        P3-6 设计取舍：外部新闻原文 ``text`` 仅做长度截断（``NEWS_TEXT_MAX_LEN``），
        未走 ``neutralize_external_text`` 中性化（与 ``analyze_stock`` 不一致）。
        残余展示层污染属已知简化，靠三层防御兜底：
        1. JSON mode：LLM 输出强制 JSON 结构，限制自由文本污染面；
        2. 白名单校验：``_parse_news_result`` 仅接受 ``NEWS_CATEGORY_MAP`` 内的
           L1/L2 枚举值，无效编码降级为本地化"资讯"；
        3. 兜底降级：解析失败回退默认 Neutral 语义，避免污染下游消费方。

        升级触发条件：若新增展示层字段直接 echo LLM 原文（如 reason 段落），
        必须补 ``neutralize_external_text`` 调用对齐 ``analyze_stock`` 范式。
        """
        # 经组合根模块属性访问 ConfigHandler/DataSanitizer：保证测试
        # patch("services.ai_service.ConfigHandler"/"DataSanitizer") 生效。
        import services.ai_service as _ai

        text = text[:NEWS_TEXT_MAX_LEN]

        # S4 分派点：一次请求只走一条链（§14.2-6 同请求单轨）；分派过程异常
        # （如配置损坏）降级旧链，不伪装成功（R21）。
        dispatch = await _resolve_news_annotation_dispatch()
        if dispatch is not None:
            return await self._classify_via_scenario(text, dispatch)

        system_instruction = _ai.ConfigHandler.get_ai_news_prompt()
        # NOTE(lazy): classify_news 未走 neutralize_external_text, 仅长度截断. ceiling: 三层防御(JSON mode+白名单+兜底降级). upgrade: 新增展示层字段 echo LLM 原文时补 neutralize_external_text.
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": text},
        ]

        # 1. Try Local Model
        try:
            raw_result = await self._service._chat_completion(
                messages,
                provider="local",
                json_mode=True,
            )
            result = self._parse_news_result(raw_result)
            logger.debug(
                "[AIService] Classify | Local ✅ %s / %s",
                result.get("category"),
                result.get("sentiment"),
            )
            return result
        except Exception as local_e:
            # Local failed (not configured, crash, etc.)
            # Log only if it wasn't just "not configured" (which is common)
            if "not installed" not in str(local_e) and "not configured" not in str(
                local_e,
            ):
                log_classified(
                    logger,
                    local_e,
                    "llm",
                    "[AIService] Classify | Local failed, falling back to cloud (%s): %s",
                    exc_info=True,
                )
            else:
                log_classified(
                    logger,
                    local_e,
                    "llm",
                    "[AIService] Classify | Local model unavailable, falling back to cloud (%s): %s",
                    exc_info=True,
                )

        # 2. Fallback to Cloud
        try:
            # Enforce global 5s timeout? The original code had per-call timeout.
            # _chat_completion has default 30s. classify used to wrap in wait_for 30s.
            # Inner cloud call had 30s timeout on client.
            # We will use 30s default.
            raw_result = await self._service._chat_completion(
                messages,
                provider="cloud",
                json_mode=True,
                purpose="news",
            )
            result = self._parse_news_result(raw_result)
            logger.debug(
                "[AIService] Classify | Cloud OK: %s / %s",
                result.get("category"),
                result.get("sentiment"),
            )
            return result
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIService] Classify | ❌ All providers failed (%s): %s",
                exc_info=True,
            )
            logger.debug("[AIService] Classify | All providers failed traceback:", exc_info=True)
            return {"category": "unknown", "sentiment": "neutral", "error": _ai.DataSanitizer.sanitize_error(e)}

    # ------------------------------------------------------------------
    # S4 统一场景链（§8.1）
    # ------------------------------------------------------------------

    async def _classify_via_scenario(self, text: str, dispatch: ScenarioDispatch) -> dict:
        """统一场景链分类（§8.1）：bundled 一次联合请求 / dedicated 主题+情绪独立请求。

        返回结构向后兼容（category / error 语义不变），新增 keys：
        sentiment（校验成功才非 None，R21）/ sentiment_status / sentiment_source /
        execution_metadata。
        """
        loop = asyncio.get_running_loop()
        invocation_id = uuid.uuid4().hex
        deadline = loop.time() + dispatch.total_timeout_seconds
        # 模式选择（s4_plan §2.1）：bundled iff task_routes.sentiment.mode == "bundled"；
        # 否则（mode == "dedicated" 或无 sentiment 键）→ dedicated（方案分派语义，
        # 无 sentiment 键即「无专用情绪子路由」：主题照常、情绪保持缺失，R21）。
        sentiment_route = dict(dispatch.task_routes.get("sentiment") or {})
        if sentiment_route.get("mode") == "bundled":
            return await self._classify_bundled(text, dispatch, invocation_id, deadline)
        return await self._classify_dedicated(text, dispatch, invocation_id, deadline, sentiment_route)

    async def _classify_bundled(
        self,
        text: str,
        dispatch: ScenarioDispatch,
        invocation_id: str,
        deadline: float,
    ) -> dict:
        """bundled：主题 + 情绪一次联合请求（§8.1 L62-67）。

        - validate_output 分别校验：主题无效切换候选；情绪缺失/无效归 None 不抛
          （保留主题，字段状态 unavailable，R21）；
        - 全候选失败 / 执行异常 → category=unknown + sentiment=None + unavailable
          兼容字段（存量失败 neutral 不转换成合法 Neutral）。
        """
        import services.ai_service as _ai

        request = InferenceRequest(
            scenario_id="news_annotation",
            task_id="topic",
            invocation_id=invocation_id,
            payload={
                "system_prompt": (_ai.ConfigHandler.get_ai_news_prompt() + "\n" + _BUNDLED_SENTIMENT_INSTRUCTION),
                "prompt": text,
                "json_mode": True,
            },
            deadline=deadline,
        )
        try:
            exec_result = await dispatch.executor.execute(request, dispatch.snapshot, _validate_bundled_output)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIService] Classify | scenario chain error (%s): %s",
                exc_info=True,
            )
            return self._scenario_failure_result("bundled", invocation_id, dispatch, e)

        if exec_result.status != "succeeded" or not exec_result.outputs:
            return self._scenario_failure_result("bundled", invocation_id, dispatch, None, exec_result)

        # F03：经公共输出类型 ScenarioOutput 读取（Executor 成功出口唯一生产形态）
        first = cast(ScenarioOutput, exec_result.outputs[0])
        output = dict(first.output)
        meta = first.metadata
        result = self._parse_news_result(dict(output))
        # 情绪以校验规范化结果为准（_parse_news_result 的默认 Neutral 仅补缺失键，
        # 不会覆盖显式 None；此处显式覆盖确保 None 语义不被回填，R21）
        result["sentiment"] = output.get("sentiment")
        result["sentiment_status"] = "success" if output.get("sentiment") else "unavailable"
        result["sentiment_source"] = "bundled"
        result["execution_metadata"] = self._scenario_execution_metadata(exec_result, invocation_id, dispatch, meta)
        logger.debug(
            "[AIService] Classify | Scenario bundled ✅ %s / %s",
            result.get("category"),
            result.get("sentiment"),
        )
        return result

    async def _classify_dedicated(
        self,
        text: str,
        dispatch: ScenarioDispatch,
        invocation_id: str,
        deadline: float,
        sentiment_route: dict,
    ) -> dict:
        """dedicated：主题与情绪独立请求（§8.1 L68-72）。

        - 主题走主候选（task_id="topic"，纯主题 prompt）；情绪走 sentiment 子路由
          候选（task_id="sentiment"，独立排序）；主题失败不丢已成功情绪，情绪失败
          不重跑主题；
        - sentiment 子列表独立排序（正本 §8.1 L249）：情绪请求经 ``task_routes[
          "sentiment"]["candidates"]`` 构造独立 ExecutionSnapshot（dataclasses.replace
          子路由快照），Executor 按 scenario_id 命中该子路由候选，与主题候选互不干扰；
        - reuse_bundled_sentiment 显式降级（正本 L249/L250，方案 §2.1）：主题响应若
          携带合法联合情绪（validator 捕获），仅当「专用链整体不可用」（非 invalid_output
          耗尽）时复用（source="reuse_bundled"）；invalid_output 耗尽 → 保持缺失不回填
          （R21）。dedicated 主题请求为纯主题 prompt，联合情绪仅在模型自愿输出时产生。
        """
        import services.ai_service as _ai

        topic_request = InferenceRequest(
            scenario_id="news_annotation",
            task_id="topic",
            invocation_id=invocation_id,
            payload={
                "system_prompt": _ai.ConfigHandler.get_ai_news_prompt(),
                "prompt": text,
                "json_mode": True,
            },
            deadline=deadline,
        )
        topic_result, topic_error = await self._execute_scenario_task(topic_request, dispatch, _validate_bundled_output)

        loop = asyncio.get_running_loop()
        # D1（检视修复）：sentiment 子列表独立排序——用 task_routes.sentiment.candidates
        # 构造子路由快照（Executor 按 scenario_id 匹配路由），不经场景级候选。
        sentiment_candidates = [str(cid) for cid in (sentiment_route.get("candidates") or [])]
        sentiment_snapshot = dataclass_replace(
            dispatch.snapshot,
            routes=(
                {
                    "scenario_id": "news_annotation",
                    "route_revision": dispatch.route_revision,
                    "candidates": tuple(
                        {
                            "candidate_index": index,
                            "task_id": "sentiment",
                            "connection_id": conn_id,
                            "reservation_id": None,
                            "owner_session_id": "",
                            "owner_epoch": 0,
                        }
                        for index, conn_id in enumerate(sentiment_candidates)
                    ),
                },
            ),
        )
        sentiment_request = InferenceRequest(
            scenario_id="news_annotation",
            task_id="sentiment",
            invocation_id=uuid.uuid4().hex,
            payload={
                "system_prompt": _SENTIMENT_DEDICATED_SYSTEM,
                "prompt": text,
                # F24 断裂 2：gguf_sentiment 候选要求 texts 协议（专用 adapter 校验），
                # litellm_chat 候选忽略该键；单请求单文本，text_id="0"。
                "texts": [text],
                "json_mode": True,
            },
            deadline=loop.time() + dispatch.total_timeout_seconds,
        )
        sentiment_result, sentiment_error = await self._execute_scenario_task(
            sentiment_request, dispatch, _validate_sentiment_output, snapshot=sentiment_snapshot
        )

        base_meta = {
            "invocation_id": invocation_id,
            "route_revision": dispatch.route_revision,
            "connection_id": None,
            "connection_revision": None,
            "actual_model": None,
            "usage_status": "unavailable",
        }
        bundled_sentiment: str | None = None
        if topic_result is not None:
            # F03：经公共输出类型 ScenarioOutput 读取（Executor 成功出口唯一生产形态）
            base_meta = self._scenario_execution_metadata(
                topic_result, invocation_id, dispatch, cast(ScenarioOutput, topic_result.outputs[0]).metadata
            )

        # 组装：主题结果（可能失败）+ 情绪结果（可能失败）
        if topic_result is not None:
            output = dict(cast(ScenarioOutput, topic_result.outputs[0]).output)
            result = self._parse_news_result(dict(output))
            # 主题响应若携带合法联合情绪（validator 捕获），暂存供显式降级复用（D3）。
            bundled_sentiment = output.get("sentiment")
            result["sentiment"] = None
        elif isinstance(topic_error, ScenarioExecutionResult):
            result = {
                "category": "unknown",
                "error": "; ".join(f"{d.reason}" for d in topic_error.candidate_decisions) or topic_error.status,
            }
        else:
            result = {
                "category": "unknown",
                "error": _ai.DataSanitizer.sanitize_error(topic_error) if topic_error else "scenario task failed",
            }

        if sentiment_result is not None:
            # F24 断裂 4：GGUF 专用链 output 额外携带数值三列与部分失败状态
            # （校验器产出）；LLM 链 output 仅 sentiment → 数值列缺失归 None
            # （LLM 无校准数值，R21 不填 0）。partial 状态透传（部分失败不伪装成功）。
            sentiment_output = dict(cast(ScenarioOutput, sentiment_result.outputs[0]).output)
            result["sentiment"] = sentiment_output.get("sentiment")
            result["sentiment_score"] = sentiment_output.get("sentiment_score")
            result["sentiment_conf"] = sentiment_output.get("sentiment_conf")
            result["sentiment_model"] = sentiment_output.get("sentiment_model")
            result["sentiment_status"] = sentiment_output.get("sentiment_status") or "success"
            adapter_kind = self._sentiment_adapter_kind(dispatch, sentiment_result)
            result["sentiment_source"] = "dedicated:gguf" if adapter_kind else "dedicated:llm"
            # 主 execution_metadata 以情绪任务身份补充连接信息（主题失败时为情绪身份）
            if topic_result is None:
                base_meta = self._scenario_execution_metadata(
                    sentiment_result,
                    base_meta["invocation_id"],
                    dispatch,
                    cast(ScenarioOutput, sentiment_result.outputs[0]).metadata,
                )
        elif self._sentiment_failure_kind(sentiment_error) == "unavailable" and bundled_sentiment in _SENTIMENT_ENUM:
            # D3（检视修复）：专用链整体不可用 + 已有合法联合情绪 → 显式降级复用
            # （正本 L249「可保留其合法情绪用于显式降级」；方案 reuse_bundled_sentiment）。
            result["sentiment"] = bundled_sentiment
            result["sentiment_status"] = "success"
            result["sentiment_source"] = "reuse_bundled"
        else:
            # invalid_output 耗尽 / 无合法联合情绪 → 保持缺失，不回填（R21）。
            result["sentiment"] = None
            result["sentiment_status"] = "unavailable"
            result["sentiment_source"] = "dedicated:llm"
        if sentiment_result is None and sentiment_error is not None:
            logger.warning(
                "[AIService] Classify | dedicated sentiment unavailable (%s)",
                _ai.DataSanitizer.sanitize_error(sentiment_error),
            )

        result["execution_metadata"] = base_meta
        return result

    @staticmethod
    def _sentiment_failure_kind(sentiment_error: object) -> str:
        """dedicated 情绪链失败分类（方案 §2.1 reuse 判定）。

        - ``"invalid_output"``：有响应但输出校验失败耗尽（candidate reason 以
          "output validation failed" 起始，与 Executor 失败表格对齐）→ 保持缺失不回填；
        - ``"unavailable"``：整体不可用（deadline / cooldown / connection / 无候选 /
          执行异常）→ 允许合法联合情绪显式降级复用。
        """
        if isinstance(sentiment_error, ScenarioExecutionResult):
            if any(str(d.reason).startswith("output validation failed") for d in sentiment_error.candidate_decisions):
                return "invalid_output"
        return "unavailable"

    async def _execute_scenario_task(
        self,
        request: InferenceRequest,
        dispatch: ScenarioDispatch,
        validator,
        snapshot: Any | None = None,
    ):
        """执行单任务场景请求：返回 (ScenarioExecutionResult | None, 错误 | None)。

        ``snapshot`` 缺省用分派快照；dedicated 情绪任务传子路由快照（D1）。
        CancelledError 始终传播（R2）；执行异常 / 全候选失败返回 (None, 错误)。
        """
        try:
            exec_result = await dispatch.executor.execute(request, snapshot or dispatch.snapshot, validator)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIService] Classify | scenario task error (task=%s) (%s): %s",
                request.task_id,
                exc_info=True,
            )
            return None, e
        if exec_result.status != "succeeded" or not exec_result.outputs:
            return None, exec_result
        return exec_result, None

    @staticmethod
    def _sentiment_adapter_kind(dispatch: ScenarioDispatch, exec_result: ScenarioExecutionResult) -> str | None:
        """从快照连接定义解析情绪任务的 adapter_kind（gguf 适配器判定）。"""
        connection_id = next(
            (d.connection_id for d in exec_result.candidate_decisions if d.decision == "succeeded"),
            None,
        )
        if connection_id is None:
            return None
        for conn in dispatch.snapshot.connections:
            if isinstance(conn, dict) and conn.get("connection_id") == connection_id:
                kind = conn.get("adapter_kind")
                return str(kind) if kind and str(kind).startswith("gguf") else None
        return None

    @staticmethod
    def _scenario_execution_metadata(
        exec_result: ScenarioExecutionResult,
        invocation_id: str,
        dispatch: ScenarioDispatch,
        meta: dict | None,
    ) -> dict:
        """程序状态 execution_metadata（§8.1 返回结构）：模型输出不可覆写。"""
        connection_id = next(
            (d.connection_id for d in exec_result.candidate_decisions if d.decision == "succeeded"),
            None,
        )
        meta = meta or {}
        return {
            "invocation_id": invocation_id,
            "route_revision": dispatch.route_revision,
            "connection_id": connection_id or meta.get("connection_id"),
            "connection_revision": meta.get("connection_revision"),
            "actual_model": meta.get("actual_model"),
            "usage_status": "reported" if exec_result.actual_usage else "unavailable",
        }

    @staticmethod
    def _scenario_failure_result(
        source: str,
        invocation_id: str,
        dispatch: ScenarioDispatch,
        error: Exception | None = None,
        exec_result: ScenarioExecutionResult | None = None,
    ) -> dict:
        """全链失败兼容返回：category=unknown + sentiment=None（R21 缺失不伪装）。"""
        import services.ai_service as _ai

        if error is not None:
            error_text = _ai.DataSanitizer.sanitize_error(error)
        elif exec_result is not None:
            error_text = "; ".join(f"{d.reason}" for d in exec_result.candidate_decisions) or exec_result.status
        else:
            error_text = "scenario chain failed"
        return {
            "category": "unknown",
            "sentiment": None,
            "sentiment_status": "unavailable",
            "sentiment_source": source,
            "error": error_text,
            "execution_metadata": {
                "invocation_id": invocation_id,
                "route_revision": dispatch.route_revision,
                "connection_id": None,
                "connection_revision": None,
                "actual_model": None,
                "usage_status": "unavailable",
            },
        }


async def _resolve_news_annotation_dispatch() -> ScenarioDispatch | None:
    """news_annotation 场景分派：任一条件不满足 → None 走旧链（与三入口统一形态）。

    S6 D6 旧链准入：分派未命中时经 ``evaluate_legacy_admission`` 判定迁移期放行——
    draining / sealed / active raise :class:`LegacyDispatchBlockedError`（在 try 外
    raise，不落入分派异常的降级旧链分支，R21 不伪装成功）。F05 fail-closed：分派 /
    准入读取过程异常（如配置损坏、CAS 锁失败、IO 超时）以 ``admission="read_failed"``
    阻断旧链并传出——读取失败等于授权状态未知，降级旧链即把「未知」伪装成「允许」，
    在 active / sealed 期间放行旧外发（R21 缺失值伪装的传输面变体）。
    """
    from services.ai_service.scenario_dispatch import evaluate_legacy_admission, resolve_executor

    try:
        dispatch = await resolve_executor("news_annotation")
        if dispatch is not None:
            return dispatch
        admission = await evaluate_legacy_admission()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # F05 fail-closed：读取失败不得降级旧链授权语义（原 fail-open 行为已移除）。
        log_classified(
            logger,
            e,
            "llm",
            "[AIService] Classify | scenario dispatch read failed, blocking legacy (%s): %s",
            exc_info=True,
        )
        raise LegacyDispatchBlockedError("read_failed") from e
    # S6 D6：准入停接在 try 外 raise——「AI 功能明确不可用」必须传出，不得降级旧链。
    if admission != "allowed":
        raise LegacyDispatchBlockedError(admission)
    return None
