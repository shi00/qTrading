"""实现指纹探针：证明「研究的工具实际实现」与只读审核时一致（§10.4）。

- stdio：对绝对可执行文件内容、``args`` 中实际存在的脚本文件内容、规范工作目录、
  参数与显式环境键名生成统一摘要；
- remote（streamable_http / sse）：恒返回 ``None``。远端实现状态与部署版本本机
  不可观测，按设计明确为第三方信任限制，不做准入（远端仍可保存配置与连接测试）。

残留（不得宣称已验证）：经 ``-m 包名``/目录入口启动时，包内文件不递归展开，
故包内容变化仅在可执行入口或显式脚本变化时才被观测到；该上限记录于审核记录的
失效条件中，由调用方如实标注。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from core.agent_types import MCPTransportKind, sha256_hex
from services.agent_service.mcp import MCPServerConfig

logger = logging.getLogger(__name__)

__all__ = ["StdioImplementationProbe"]

_JSON_SEPARATORS = (",", ":")
_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class _FileDigest:
    path: str
    size: int
    sha256: str


def _hash_file(path: Path) -> _FileDigest | None:
    """读取文件内容做摘要；不可读/非文件返回 ``None``（不猜测、不降级）。"""

    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
        return _FileDigest(path=str(path.resolve()), size=size, sha256=digest.hexdigest())
    except OSError as exc:
        logger.debug("[MCPProbe] file digest unavailable: %s", type(exc).__name__)
        return None


class StdioImplementationProbe:
    """``MCPImplementationProbe`` 的本机实现（stdio 可观测，远端不可观测）。"""

    async def probe(self, config: MCPServerConfig) -> str | None:
        if config.transport.kind is not MCPTransportKind.STDIO:
            return None
        return await asyncio.to_thread(self._fingerprint, config)

    def _fingerprint(self, config: MCPServerConfig) -> str | None:
        transport = config.transport
        if transport.command is None or transport.cwd is None:
            return None
        executable = _hash_file(Path(transport.command))
        if executable is None:
            return None
        cwd = Path(transport.cwd)
        scripts: list[_FileDigest] = []
        for arg in transport.args:
            candidate = Path(arg) if Path(arg).is_absolute() else cwd / arg
            digest = _hash_file(candidate)
            if digest is not None:
                scripts.append(digest)
        payload = {
            "executable": {"path": executable.path, "size": executable.size, "sha256": executable.sha256},
            "scripts": [{"path": item.path, "size": item.size, "sha256": item.sha256} for item in scripts],
            "cwd": str(cwd.resolve()),
            "args": list(transport.args),
            # 只纳入键名（不含值）：普通字面量与敏感引用分属两个字段，任一名变化都
            # 应改变实现指纹。
            "env_keys": sorted(name for name, _ in (*transport.env, *transport.secret_env)),
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True)
        return sha256_hex(canonical.encode("utf-8"))
