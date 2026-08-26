from __future__ import annotations

import asyncio
import atexit
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from data.persistence.db_migrator import DatabaseMigrationNeeded
from data.persistence.metadata_manager import MetaDataManager
from services.task_manager import TaskManager
from utils.error_classifier import log_classified
from utils.sanitizers import DataSanitizer
from utils.scheduler_service import SchedulerService
from core.i18n import I18n

# review01-A3: EmbeddedPgStartupScenario 下沉到 core.startup_types（纯类型），此处 re-export 兼容旧引用
from core.startup_types import EmbeddedPgStartupScenario

if TYPE_CHECKING:
    from utils.config_models import AppConfig

logger = logging.getLogger(__name__)

# Phase 2A.1 §3.2.10：距上次 probe 超过此阈值时启动期自动触发 probe
_AUTO_PROBE_INTERVAL = timedelta(days=7)

# review01-A9: 原模块级 _services_initialized flag 在多 session 下不安全——
# Flet Web 多 session 共享进程时，第二个 session 会重新构造 CacheManager 但
# 模块级 flag 使 initialize_services 跳过，导致"引擎就绪、服务未启动"的不一致状态。
# 已改为 per-StartupController 实例状态（由调用方传入 services_initialized 参数），
# 模块级 flag 与 reset_services_initialized() 一并移除。

# Skeptic-MAJOR-4 修复：atexit handler 线性泄漏。Flet Web 模式下每个 main(page) 调用
# prepare_database_runtime，若 QTRADING_EMBEDDED_PG_URL_FILE 设置会无条件 atexit.register，
# 导致 N+1 个 handler 指向同一 url_file。模块级 flag 保证只注册一次。
_embedded_pg_url_file_atexit_registered = False


class InitResult(TypedDict):
    success: bool
    error: str | None
    detail: str | None
    current_rev: str | None
    head_rev: str | None
    # Phase 2A.1 §3.2.9：启动期自动 probe 任务（fire-and-forget），
    # 由 main.py 注册到 ShutdownCoordinator 以便关机时取消
    auto_probe_task: asyncio.Task | None


async def initialize_services(
    cache_manager,
    show_toast_fn=None,
    services_initialized: bool = False,
) -> InitResult:
    from utils.correlation import ensure_correlation_id

    ensure_correlation_id()

    # review03-C15: 生产构建（非 E2E、非 DEBUG）下禁止关闭质量门控严格模式
    _validate_quality_gate_strictness()

    # review01-A9: 由调用方（StartupController per-session 实例）传入 services_initialized。
    # 原模块级 flag 在多 session 下不安全——Flet Web 多 session 共享进程时，第二个 session
    # 重新构造 CacheManager 但模块级 flag 跳过初始化，导致"引擎就绪、服务未启动"的不一致状态。
    # 单例服务（SchedulerService/NewsSubscriptionService/MarketDataService）自身有幂等 guard，
    # 但 TaskManager.init_db() 不幂等（每次 UPDATE task_history）、auto_probe_task 每次创建新 task。
    # 同一 session 内重复调用时直接返回成功结果，避免重复副作用。
    if services_initialized:
        logger.debug("[Bootstrap] services already initialized, skipping duplicate initialize_services call")
        return {
            "success": True,
            "error": None,
            "detail": None,
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    try:
        logger.info("[Bootstrap] Calling cache_manager.init_db()...")
        await cache_manager.init_db()
        logger.info("[Bootstrap] cache_manager.init_db() completed.")
    except DatabaseMigrationNeeded as e:
        logger.warning("[Bootstrap] Database needs migration: %s", e)
        return {
            "success": False,
            "error": "db_upgrade_needed",
            "detail": DataSanitizer.sanitize_error(e),
            "current_rev": e.current_rev,
            "head_rev": e.head_rev,
            "auto_probe_task": None,
        }
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] Database initialization failed (%s): %s",
            exc_info=True,
        )
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_init_failed"), "error")
        return {
            "success": False,
            "error": "db_init_failed",
            "detail": DataSanitizer.sanitize_error(e),
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    MetaDataManager.preload_aliases()

    if cache_manager.engine is None:
        logger.error("[Bootstrap] Database engine not created after init_db().")
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_engine_missing"), "error")
        return {
            "success": False,
            "error": "db_engine_missing",
            "detail": None,
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    try:
        logger.info("[Bootstrap] Calling TaskManager.init_db()...")
        await TaskManager().init_db()
        logger.info("[Bootstrap] TaskManager.init_db() completed.")
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] TaskManager init failed (%s): %s",
            exc_info=True,
        )
        if show_toast_fn:
            show_toast_fn(I18n.get("error_task_manager_init_failed"), "error")
        return {
            "success": False,
            "error": "task_manager_init_failed",
            "detail": DataSanitizer.sanitize_error(e),
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    import os

    from utils.app_env import is_e2e_mode  # review03-C16: E2E 判定统一收口

    logger.info("[Bootstrap] After TaskManager init, checking E2E_TESTING env (=%s)...", os.environ.get("E2E_TESTING"))
    if is_e2e_mode():
        # review03-C16: 非 DEBUG 环境下 E2E 模式激活应被审计（后门误触发显式化）
        is_debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
        if not is_debug:
            logger.critical(
                "[Bootstrap] E2E_TESTING=true 在非 DEBUG 环境激活：确认这是显式测试运行，"
                "否则该环境变量残留会使质量门控/日历服务后门静默生效。"
            )
        logger.info("[Bootstrap] E2E testing mode detected, skipping background scheduler and data polling services.")
        # E2E 预热: 在服务初始化阶段预加载 AIService (触发 litellm import，约 18s+)，
        # 避免 UI 渲染时第一次导入阻塞 MainThread 导致 Flet patch 下发延迟、
        # E2E 浏览器等待元素超时而失败。initialize_services 在 async 上下文中执行，
        # 此时 UI 尚未挂载，预加载不影响用户感知。
        try:
            from services.ai_service import AIService

            AIService()
            logger.info("[Bootstrap] E2E warmup: AIService pre-initialized.")
        except Exception as exc:
            logger.warning("[Bootstrap] E2E warmup: AIService pre-init failed: %s", exc, exc_info=True)
        auto_probe_task = None
    else:
        started: list[str] = []
        try:
            # review01-A2-1: 先注册业务 job（夜间预测等）再启动调度器。
            # SchedulerService 不再感知具体业务类，由 app 层完成依赖注入装配。
            _register_scheduler_jobs()
            SchedulerService().start()
            started.append("scheduler")
            await NewsSubscriptionService().start()
            started.append("news")
            await MarketDataService().start()
            started.append("market_data")
        except BaseException:
            # TD-P2：启动阶段部分服务成功即后续失败 → 逆序停止已启动服务防资源泄漏。
            # BaseException 覆盖 CancelledError（R2）与系统级异常；清理后 re-raise 原始异常。
            await _stop_started_services(started)
            raise

        await _warmup_tushare_capabilities()

        _validate_failover_credentials()

        # Phase 2A.1 Task 2A.1.10：启动期校验策略档位覆盖（warning 不 raise）
        _validate_strategy_tier_coverage()

        # Phase 2A.1 Task 2A.1.8：启动期自动 probe（fire-and-forget）
        auto_probe_task = asyncio.create_task(_maybe_auto_probe_on_startup())

    # review01-A9: 不再由本模块写模块级 flag；调用方（StartupController per-session 实例）
    # 根据 result["success"] 自行置位 _services_initialized。
    return {
        "success": True,
        "error": None,
        "detail": None,
        "current_rev": None,
        "head_rev": None,
        "auto_probe_task": auto_probe_task,
    }


def _register_scheduler_jobs() -> None:
    """review01-A2-1: 注册定时业务 job（SchedulerService 依赖注入装配）。

    夜间预测等业务编排已下沉到 services/scheduled_jobs/。因 services 禁入 strategies
    （契约 3 / R1），AI 策略执行器（AISelectionRunner）由本 app 层构造并注入——
    app 层作为编排层可合法 import strategies + services，实现依赖倒置。
    """
    from strategies.ai_strategy import AISelectionStrategy
    from services.scheduled_jobs.nightly_prediction import build_nightly_prediction_job

    async def _ai_runner(context: dict):
        # 每次执行新建策略实例（与原 SchedulerService._prediction_logic 行为一致）
        strategy = AISelectionStrategy()
        return await strategy.filter(context)

    SchedulerService().register_job("nightly_prediction", build_nightly_prediction_job(_ai_runner))


async def _stop_started_services(started: list[str]) -> None:
    """TD-P2：启动阶段部分服务已启动即后续失败时，逆序停止已启动服务防资源泄漏。

    started 保存按启动顺序登记的已成功启动服务名；逆序遍历停止，保证无论哪个
    位置失败都能清理其前的所有服务。单个服务停止失败仅记 warning 不中断清理，
    以免一个清理异常阻断后续服务释放；清理阶段的 CancelledError 同样记为 warning
    不再传播（原始启动失败异常才是需要传播的对象）。
    """
    for name in reversed(started):
        try:
            if name == "scheduler":
                SchedulerService().stop()
            elif name == "news":
                await NewsSubscriptionService().stop_async()
            elif name == "market_data":
                await MarketDataService().stop_async()
        except BaseException as e:  # noqa: BLE001  # [reason: 清理阶段防御性捕获全部异常（含取消）以保证后续服务仍被释放]
            logger.warning(
                "[Bootstrap] failed to stop startup service %s after startup failure: %s",
                name,
                DataSanitizer.sanitize_error(e),
            )


async def _warmup_tushare_capabilities() -> None:
    """
    Warm up Tushare capability cache from AppState on startup.

    This ensures that API availability status persists across restarts,
    avoiding repeated probe calls that waste API quota.
    """
    from data.external.tushare_client import TushareClient

    client = TushareClient()
    if not client.token:
        logger.debug("[Bootstrap] No Tushare token configured, skipping capability warmup")
        return

    try:
        await client.load_capabilities_from_app_state()

        cache = client.get_capability_cache()
        if cache:
            logger.info("[Bootstrap] Loaded %s Tushare capabilities from AppState", len(cache))
        else:
            logger.debug("[Bootstrap] Tushare capability cache empty after load (first startup or token changed)")
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] Tushare capability warmup failed (non-critical) (%s): %s",
            exc_info=True,
        )


def _validate_failover_credentials() -> None:
    """
    Validate failover provider credentials on startup.

    Logs a warning if any failover provider is missing API key.
    """
    from utils.config_handler import ConfigHandler

    try:
        missing = ConfigHandler.validate_failover_credentials()
        if missing:
            logger.warning(
                "[Bootstrap] Failover providers missing credentials: %s. Cross-provider fallback may fail.",
                ", ".join(missing),
            )
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] Failover credential validation skipped (%s): %s",
            exc_info=True,
        )


async def _maybe_auto_probe_on_startup() -> None:
    """Phase 2A.1 Task 2A.1.8：启动期自动 probe（fire-and-forget，不阻塞 UI）。

    决策逻辑：
    1. Token 未配置时短路跳过（不读 AppState，避免无谓 IO）
    2. 距上次 probe > 7 天（``_AUTO_PROBE_INTERVAL``）时触发 ``probe_api_capabilities``
    3. 失败降级 ``warning`` 日志（不 raise，不影响主流程）
    4. CancelledError 必须 raise（R2 红线，配合优雅停机）

    本函数返回的 Task 由 ``initialize_services`` 保存到 ``InitResult.auto_probe_task``，
    再由 main.py 注册到 ``ShutdownCoordinator`` 以便关机时取消。
    """
    from data.external.tushare_client import TushareClient
    from utils.time_utils import get_now

    try:
        client = TushareClient()
        if not client.token:
            logger.debug("[Bootstrap] No Tushare token configured, skipping auto probe")
            return

        last_probe = client.get_last_probe_time()
        if last_probe is not None and (get_now() - last_probe) < _AUTO_PROBE_INTERVAL:
            logger.debug(
                "[Bootstrap] Last probe %s within %s days, skipping auto probe",
                last_probe.isoformat(),
                _AUTO_PROBE_INTERVAL.days,
            )
            return

        logger.info(
            "[Bootstrap] Auto probe triggered (last_probe=%s)",
            last_probe.isoformat() if last_probe else "never",
        )
        await client.probe_api_capabilities()
    except asyncio.CancelledError:
        # R2 红线：CancelledError 必须 raise 以配合优雅停机
        raise
    except Exception as e:
        # 非取消异常降级 warning，不影响主流程
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] Auto probe failed (non-critical) (%s): %s",
            exc_info=True,
        )


def _validate_quality_gate_strictness() -> None:
    """review03-C15: 生产构建（非 E2E、非 DEBUG）下 STRICT_QUALITY_GATE=false 时拒绝启动。

    质量门控是量化决策的安全机制——非严格模式下 processor 缺失会静默放行策略，
    这对生产是"不应该发生"的配置。E2E 模式（测试数据本就低质量）与 DEBUG=true
    （本地调试豁免）放行。
    """
    from data.persistence.quality_gate import is_strict_quality_gate_enabled
    from utils.app_env import is_e2e_mode  # review03-C16: E2E 判定统一收口

    is_e2e = is_e2e_mode()
    is_debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    if not is_e2e and not is_debug and not is_strict_quality_gate_enabled():
        logger.error("[Bootstrap] STRICT_QUALITY_GATE=false 且非开发模式：质量门控是安全机制，拒绝启动。")
        raise RuntimeError(
            "STRICT_QUALITY_GATE=false 禁止在非开发模式启动：数据质量门控是量化决策安全机制。"
            "请删除该环境变量，或设置 DEBUG=true 豁免（仅限本地调试）。"
        )


def _validate_strategy_tier_coverage() -> None:
    """Phase 2A.1 Task 2A.1.10：启动期校验已注册策略是否都在 _STRATEGY_MIN_TIER 中登记。

    R1 红线：services/ 不可导入 strategies/（反向依赖禁止），因此由 app/ 层（可同时
    引用 services/ 和 strategies/）查询 ``StrategyManager().strategies.keys()`` 后
    注入 ``services.ai_service.validate_strategy_tier_coverage``。warning 不 raise，
    避免阻断启动。
    """
    try:
        from services.ai_service import validate_strategy_tier_coverage
        from strategies.all_strategies import StrategyManager

        registered_keys = set(StrategyManager().strategies.keys())
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] validate_strategy_tier_coverage skipped (%s): %s",
            exc_info=True,
        )
        return

    try:
        validate_strategy_tier_coverage(registered_keys)
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Bootstrap] validate_strategy_tier_coverage failed (%s): %s",
            exc_info=True,
        )


def check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
    # FR-UX-001: 云端 AI 可选化，llm_api_key 不再是 onboarding 硬门槛
    # （参数保留以兼容 startup_controller.start 调用签名；未配置云端 AI 时
    # 主界面 AI 入口走 ai_not_configured 降级路径）
    return not db_url or not token or not onboarding_complete


def mask_sensitive(value):
    """R9 一致性：复用 DataSanitizer.sanitize_token 替换私有前缀脱敏实现。

    旧实现固定泄露 token 前 4 字符，对短 token 仍泄露显著片段；改用 sanitize_token
    后短 token（< 32）全部隐藏为 ***，长 token 部分脱敏（前 3 + *** + 后 4）。
    """
    return DataSanitizer.sanitize_token(value)


async def prepare_database_runtime() -> str | None:
    """根据数据库模式准备运行时（Phase 2 §3.4）。

    - embedded: 启动 EmbeddedPostgresService → 返回 ``info.url``，由调用方永久设置
      ``config.DB_URL = url``（D15：不持久化到 config 文件，不设 DATABASE_URL 环境变量）
    - external: 返回 ``None``（沿用既有 DATABASE_URL/db_* 配置）

    必须在 ``CacheManager()`` 之前调用（CacheManager 构造时建引擎）。

    模式判定：``QTRADING_DATABASE_MODE`` 环境变量（embedded|external，默认 embedded）。
    spec.md §3 不变量 1：默认 embedded 与产品契约"无需任何配置"对齐，
    用户显式设 ``QTRADING_DATABASE_MODE=external`` 才进入 external 分支。

    Returns:
        embedded 模式且启动成功时返回 sidecar ``ConnectionInfo.url``；
        external 模式 / 未启用 / 跳过时返回 ``None``。

    Raises:
        EmbeddedPostgresStartError: sidecar 启动失败时透传（不吞没，R2 红线要求
            CancelledError 也透传）。
    """
    import os

    mode = os.environ.get("QTRADING_DATABASE_MODE", "embedded").lower()
    if mode != "embedded":
        # M5: mode=external 但 config.embedded_pg_enabled=True → 记 WARNING（用户可能误配置）
        from utils.config_handler import ConfigHandler
        from utils.config_models import AppConfig

        config = AppConfig.model_validate(ConfigHandler.load_config())
        if config.embedded_pg_enabled:
            logger.warning(
                "[Bootstrap] QTRADING_DATABASE_MODE=%s but embedded_pg_enabled=True; "
                "embedded PostgreSQL service will NOT start (external mode takes precedence)",
                mode,
            )
        return None

    from data.persistence.embedded_postgres.service import EmbeddedPostgresService
    from utils.config_handler import ConfigHandler
    from utils.config_models import AppConfig

    config = AppConfig.model_validate(ConfigHandler.load_config())
    if not config.embedded_pg_enabled:
        logger.warning("[Bootstrap] QTRADING_DATABASE_MODE=embedded but embedded_pg_enabled=False; skip")
        return None

    # R-Arch-2/Ske-1：embedded 模式下若 DATABASE_URL 环境变量被外部误设，
    # ConfigHandler.get_db_url() Priority 1 会返回该值覆盖 embedded URL，
    # 导致 CacheManager 连错 DB 而 embedded sidecar 空转。emit WARNING 让用户感知。
    # P3 根因修复后：main.py 启动时会自动 pop 此 env var，但建议用户清理 shell profile
    # 从源头消除冲突（spec.md §1.7 不变量：embedded URL 永不写入 DATABASE_URL）。
    external_db_url = os.environ.get("DATABASE_URL")
    if external_db_url:
        logger.warning(
            "[Bootstrap] QTRADING_DATABASE_MODE=embedded but DATABASE_URL env var is set. "
            "main.py 启动时会自动 pop 此 env var 以避免 ContextVar 在调度边界外失效，"
            "但建议清理 shell profile（如 .bashrc/.zshrc/系统环境变量）中的残留 DATABASE_URL，"
            "从源头消除冲突。"
        )

    # R16: from_config 内含 resolve_pg_major_version（同步子进程），在 async 层卸载到线程池，
    #      避免阻塞事件循环。单例构造用 RLock 保护，线程安全（与 service.start 的 to_thread 一致）。
    service = await asyncio.to_thread(EmbeddedPostgresService.from_config, config)
    # H3: start 失败时清理单例，避免后续 CacheManager 误用残留状态
    try:
        info = await service.start()
    except Exception:
        EmbeddedPostgresService._reset_singleton()
        raise
    logger.info(
        "[Bootstrap] embedded postgres ready on %s:%s",
        config.embedded_pg_listen,
        info.port,
    )
    # E2E 测试支持：当设置 QTRADING_EMBEDDED_PG_URL_FILE 环境变量时，
    # 把 sidecar URL 写入该文件，供 E2E 主进程读取后连接 sidecar DB 播种数据。
    # 生产环境不设置此环境变量，无副作用。
    # 安全：文件权限 0600（仅所有者可读写），URL 含密码；atexit 注册清理。
    url_file_path = os.environ.get("QTRADING_EMBEDDED_PG_URL_FILE")
    if url_file_path:
        try:
            url_file = Path(url_file_path)
            url_file.parent.mkdir(parents=True, exist_ok=True)
            url_file.write_text(info.url, encoding="utf-8")
            # Skeptic-MAJOR-4 修复：用模块级 flag 保证 atexit 只注册一次，
            # 避免 Flet Web 模式下多个 main(page) 调用导致 handler 线性泄漏。
            # MINOR-6 修复：先注册 atexit 再 chmod，避免 chmod 失败导致 atexit 被跳过。
            global _embedded_pg_url_file_atexit_registered
            if not _embedded_pg_url_file_atexit_registered:
                atexit.register(_cleanup_embedded_pg_url_file, url_file)
                _embedded_pg_url_file_atexit_registered = True
                logger.info("[Bootstrap] embedded PG URL file atexit handler registered")
            try:
                os.chmod(url_file, 0o600)
            except OSError as chmod_err:
                # chmod 失败不阻塞：URL 文件已写入，atexit 已注册
                logger.warning("[Bootstrap] os.chmod 0600 failed for URL file: %s", chmod_err)
            logger.info("[Bootstrap] embedded postgres URL written to %s", url_file)
        except OSError as e:
            # URL 文件写入失败不阻塞启动，E2E 主进程会超时失败
            logger.warning("[Bootstrap] failed to write embedded PG URL file: %s", e)
    # D15（pg-plan §22）：返回 URL 供调用方永久设置 config.DB_URL，
    # 不再调 ConfigHandler.save_db_config 持久化（embedded URL 不应写 config 文件）。
    return info.url


def _cleanup_embedded_pg_url_file(url_file: Path) -> None:
    """atexit 回调：清理 sidecar URL 文件（E2E 测试支持）。"""
    try:
        url_file.unlink(missing_ok=True)
    except OSError as e:
        logger.debug("[Bootstrap] failed to cleanup embedded PG URL file: %s", e)


def detect_embedded_pg_startup_scenario(config: AppConfig) -> EmbeddedPgStartupScenario | None:
    """检测 embedded PostgreSQL 启动场景，供 LoadingView 显示差异化文案。

    判定逻辑（spec §「Requirement: 启动场景检测」）：
    - ``QTRADING_DATABASE_MODE != "embedded"`` → 返回 ``None``（external 模式不检测）
    - ``config.embedded_pg_enabled == False`` → 返回 ``None``
    - 否则检查 ``<install_dir>/.setup-complete`` 与 ``<data_dir>/PG_VERSION`` 存在性：
      * 两者均不存在 → ``FIRST_RUN``
      * 两者均存在 → ``NORMAL``
      * 不一致 → ``UNKNOWN`` + WARNING 日志（不阻塞启动）

    路径解析复用 ``EmbeddedPostgresService.from_config`` 的逻辑：构造单例后读取
    ``_data_dir`` / ``_install_dir`` 私有属性。单例 idempotent，后续
    ``prepare_database_runtime`` 再次调用 ``from_config`` 会返回同一实例。

    模式判定：``QTRADING_DATABASE_MODE`` 环境变量（默认 ``embedded``，spec.md §3 不变量 1）。

    Args:
        config: ``AppConfig`` 实例

    Returns:
        ``EmbeddedPgStartupScenario`` 枚举值；external 模式或未启用时返回 ``None``
    """
    mode = os.environ.get("QTRADING_DATABASE_MODE", "embedded").lower()
    if mode != "embedded":
        logger.debug("[Bootstrap] detect skipped: QTRADING_DATABASE_MODE=%s (not embedded)", mode)
        return None
    if not config.embedded_pg_enabled:
        logger.debug("[Bootstrap] detect skipped: embedded_pg_enabled=False")
        return None

    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    # 复用 from_config 路径解析；service 为单例，已初始化时 from_config 直接返回。
    service = EmbeddedPostgresService.from_config(config)
    install_marker = Path(service._install_dir) / ".setup-complete"  # type: ignore[attr-defined]  # [reason: EmbeddedPostgresService 未暴露公开 install_dir 属性，复用 from_config 路径解析需访问私有属性；后续可暴露公开属性重构]
    pg_version = Path(service._data_dir) / "PG_VERSION"  # type: ignore[attr-defined]  # [reason: 同上，复用 from_config 路径解析访问私有 data_dir]

    marker_exists = install_marker.exists()
    pg_version_exists = pg_version.exists()

    if not marker_exists and not pg_version_exists:
        return EmbeddedPgStartupScenario.FIRST_RUN
    if marker_exists and pg_version_exists:
        return EmbeddedPgStartupScenario.NORMAL
    logger.warning(
        "[Bootstrap] embedded PG startup scenario UNKNOWN: "
        "install_marker=%s (%s), pg_version=%s (%s); treating as NORMAL for UX",
        marker_exists,
        install_marker,
        pg_version_exists,
        pg_version,
    )
    return EmbeddedPgStartupScenario.UNKNOWN
