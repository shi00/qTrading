"""LLM 云端外发元数据集中审计（SEC-03）。

满足 UN-07「掌控」——「能否知道今天向外发送过什么」。本次聚焦 LLM 云端口
（数据量最大的敏感出口），统一记录外发**元数据**（时间/目的地/类别/载荷字节/
条目数/状态），**不记录 prompt 内容本身**，避免二次泄露（检视报告 §1.3 明确要求）。

架构约定（对抗检视固化，防止双重计数 / 内存膨胀 / 并发写交错）：
- **JSONL 文件 = 今日/本月聚合的唯一事实源**。今日/本月聚合直接扫 JSONL 文件
  （经 ``ThreadPoolManager.run_async(IO)`` 归并，R16），**不叠加内存 deque**，
  从根本避免「record 内存+落盘文件被双计」。
- **会话计数为内存独立 monotonically-incrementing counter**（进程内、重启归零），
  供状态栏指示器；不受 deque maxlen 截断影响。
- **deque 设 maxlen 仅作 recent(N) UI 明细缓冲**，不参与聚合、不无限增长。
- **落盘用单例级写锁互斥**，避免 IO 线程池并发 open/write 同一 JSONL 造成
  Windows 文本模式行交错。
- 落盘失败仅 ``logger.debug`` 降级（审计绝不阻断 AI 主流程）；聚合相应低估，
  属已声明的可接受取舍。

设计约定（项目宪法 compliance）：
- 横切叶子层（utils），不引用任何业务层模块；纯 stdlib（dataclass/json/pathlib/
  time/threading/collections）+ ``utils`` 内部辅助。
- ``@register_singleton`` 注册 + ``_reset_singleton()`` 实现（R15/R7 单测隔离）。
- 落盘经 ``ThreadPoolManager.run_async(TaskType.IO)`` 归并（R16）。
- R2：``CancelledError`` 显式 raise，不被 ``except Exception`` 吞没（CLAUDE.md §3.1）。
- R11：跨线程共享计数器 / deque / 写锁用 ``threading.Lock`` 保护（落盘经 IO 线程池触达），
  避免在类属性上使用 ``asyncio`` 同步原语。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

import config
from utils.error_classifier import log_classified
from utils.singleton_registry import register_singleton

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# recent(N) 缓冲上限：仅 UI 明细展示，不影响聚合准确性与内存增长
_MAX_RECENT = 200


@dataclass(frozen=True)
class EgressRecord:
    """一次 LLM 云端外发的元数据（不含 prompt 内容本身）。"""

    timestamp: datetime
    destination: str  # "llm:<effective_model>"，如 "llm:deepseek/deepseek-chat" 或 "llm:qwen/qwen-max"
    category: str  # "analysis" / "news" / "web_search" / "verify"
    payload_size_bytes: int
    item_count: int
    status: str = "sent"  # "sent" / "failed"


@register_singleton
class EgressAudit:
    """LLM 云端外发元数据集中审计单例。

    - JSONL 文件（USER_DATA_ROOT/logs/egress_audit.jsonl）为今日/本月聚合唯一事实源。
    - 内存维护：本会话外发计数（monotonic counter）+ 最近 N 条缓冲（deque maxlen）。
    """

    _instance: EgressAudit | None = None
    _initialized = False
    _lock = threading.Lock()
    # 落盘路径写锁（单例级）：防 IO 线程池并发追加交错
    _write_lock = threading.Lock()
    # 单测注入的落盘路径；None 时用默认 USER_DATA_ROOT/logs/egress_audit.jsonl
    _override_path: str | None = None

    def __new__(cls) -> EgressAudit:
        """真单例：恒返回同一实例（会话计数/监听器/recent 缓冲跨调用点共享）。

        无 ``__new__`` 时 ``EgressAudit()`` 每次创建全新实例，__init__ 依据实例级
        ``_initialized`` 判定导致每个实例都完整初始化，app_layout 注册的 listener 与
        litellm 审计点的 record 落在不同实例上，会话计数/状态栏通知全部失效。
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    @classmethod
    def _reset_singleton(cls) -> None:
        """Testing only. NEVER call in production.

        重置会话计数与 recent 缓冲；保留落盘路径 override（单测在 reset 前后设置）。
        """
        with cls._lock:
            cls._instance = None
            cls._initialized = False
        # 保留 _override_path：单测在 reset 前设置，跨 reset 保持指向测试临时目录

    @classmethod
    def _configure_for_tests(cls, path: str) -> None:
        """单测专用：注入落盘路径（须在 reset 后、首次 record 前调用）。"""
        with cls._lock:
            cls._override_path = path

    def __init__(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._session_egress_count = 0
            self._recent_records: deque[EgressRecord] = deque(maxlen=_MAX_RECENT)
            self._listeners: list[Callable[[int], None]] = []
            self._state_lock = threading.Lock()  # 保护 session counter + recent deque + listeners
            self._initialized = True

    # ------------------------------------------------------------------
    # 落盘路径
    # ------------------------------------------------------------------

    def _file_path(self) -> str:
        if self._override_path:
            return self._override_path
        return os.path.join(config.USER_DATA_ROOT, "logs", "egress_audit.jsonl")

    def _append_jsonl(self, rec: EgressRecord) -> None:
        """同步单行追加 JSONL（在 IO 线程池执行；单例写锁防交错）。"""
        path = self._file_path()
        line = json.dumps(
            {
                "timestamp": rec.timestamp.isoformat(timespec="seconds"),
                "destination": rec.destination,
                "category": rec.category,
                "payload_size_bytes": rec.payload_size_bytes,
                "item_count": rec.item_count,
                "status": rec.status,
            },
            ensure_ascii=False,
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 单系统调用 + O_APPEND：保证单行写原子（避免 Windows 文本模式多字节转换交错）
        data = (line + "\n").encode("utf-8")
        with self._write_lock:
            with open(path, "ab") as f:
                f.write(data)

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    async def record(
        self,
        *,
        destination: str,
        category: str,
        item_count: int,
        payload_size_bytes: int,
        status: str = "sent",
    ) -> None:
        """记录一次 LLM 云端外发元数据。审计失败不阻断 AI 主流程。"""
        rec = EgressRecord(
            timestamp=datetime.now(),
            destination=destination,
            category=category,
            payload_size_bytes=int(payload_size_bytes),
            item_count=int(item_count),
            status=status,
        )
        # 会话计数 + recent 缓冲（内存操作）
        notified_count: int = 0
        listeners_snapshot: list[Callable[[int], None]] | None = None
        with self._state_lock:
            self._session_egress_count += 1
            notified_count = self._session_egress_count
            self._recent_records.append(rec)
            listeners_snapshot = list(self._listeners) if self._listeners else None

        # 通知会话计数监听（状态栏指示器等），同步轻量回调，异常隔离不破坏审计主流程
        if listeners_snapshot:
            for cb in listeners_snapshot:
                try:
                    cb(notified_count)
                except Exception:  # noqa: BLE001 -- 监听回调隔离, 不阻断审计/落盘
                    logger.debug("[EgressAudit] listener callback failed", exc_info=True)

        from utils.thread_pool import TaskType, ThreadPoolManager

        try:
            # 落盘到唯一事实源（IO 线程池，不阻塞事件循环线程）
            await ThreadPoolManager().run_async(TaskType.IO, self._append_jsonl, rec)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:  # noqa: BLE001 -- 审计降级不阻断 AI 主流程
            log_classified(
                logger,
                exc,
                "general",
                "[EgressAudit] JSONL append failed (%s): %s",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # 会话计数（状态栏指示器）
    # ------------------------------------------------------------------

    def get_session_egress_count(self) -> int:
        """本次进程启动以来累计外发次数（内存单调计数，重启归零，不持久）。"""
        with self._state_lock:
            return self._session_egress_count

    def add_listener(self, callback: Callable[[int], None]) -> Callable[[], None]:
        """订阅会话计数变化，返回退订函数（供状态栏指示器实时刷新）。

        回调在 ``record()`` 调用线程同步触发（UI 事件循环线程），须为轻量操作（如
        递增 Flet Observable 序号），禁止在回调内做同步 IO/CPU 密集操作（R16）。
        回调异常被隔离记录，不影响审计/落盘主流程。
        """
        with self._state_lock:
            self._listeners.append(callback)

        def _unsubscribe() -> None:
            with self._state_lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return _unsubscribe

    # ------------------------------------------------------------------
    # 今日/本月聚合（唯一事实源 = JSONL 文件）
    # ------------------------------------------------------------------

    async def get_aggregates(self) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[int, int]]]:
        """按目的地聚合今日/本月，返回 (today_map, month_map)。

        today_map/month_map: ``{destination: (count, bytes)}``。经 IO 线程池扫 JSONL 文件
        （唯一事实源），不叠加内存，避免双重计数。R16：不在事件循环线程同步扫盘。
        """
        from utils.thread_pool import TaskType, ThreadPoolManager

        today_map, month_map = await ThreadPoolManager().run_async(
            TaskType.IO,
            self._scan_aggregates,
        )
        return today_map, month_map

    def _scan_aggregates(self) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[int, int]]]:
        """同步扫描 JSONL 文件聚合今日/本月（在 IO 线程池执行）。"""
        today_prefix = date.today().isoformat()  # "2026-09-14"
        month_prefix = date.today().strftime("%Y-%m")
        today_map: dict[str, tuple[int, int]] = {}
        month_map: dict[str, tuple[int, int]] = {}
        path = self._file_path()
        if not os.path.isfile(path):
            return today_map, month_map
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        ts_str = obj.get("timestamp", "")
                        dest = obj.get("destination", "")
                        if not ts_str or not dest:
                            continue
                        rec_date = ts_str[:10]  # ISO 日期前缀
                        rec_month = ts_str[:7]
                        payload = int(obj.get("payload_size_bytes", 0))
                        if rec_date == today_prefix:
                            _agg_add(today_map, dest, payload)
                        if rec_month == month_prefix:
                            _agg_add(month_map, dest, payload)
                    except (ValueError, KeyError, TypeError):
                        continue  # 容忍损坏行
        except OSError:
            return today_map, month_map
        return today_map, month_map

    def recent(self, limit: int = 20) -> list[dict]:
        """最近 N 条外发记录（内存缓冲，非持久，不回溯历史文件）。"""
        with self._state_lock:
            items = list(self._recent_records)[-limit:]
        return [asdict(r) for r in items]


def _agg_add(acc: dict[str, tuple[int, int]], dest: str, payload: int) -> None:
    """累积器：count+1 / bytes+payload。"""
    count, total = acc.get(dest, (0, 0))
    acc[dest] = (count + 1, total + payload)
