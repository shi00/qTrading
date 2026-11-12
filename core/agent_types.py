"""Agent 公共数据合同——基础类型层（设计正本 §3.1/§3.2、V04 数值编码，B-01）。

仅使用标准库（``dataclass(frozen=True)`` / ``Enum`` / ``tuple`` / ``Decimal``），
不导入 Flet / PydanticAI / DBOS，不导入任何业务层模块（core 层隔离，CLAUDE.md §4.2）。

本模块承载：
- :class:`AgentValidationError`：纯校验失败（code + field_path），服务边界负责转译；
- :class:`CanonicalJson` / :func:`json_doc` / :func:`decode_json_doc`：规范 JSON
  编解码（UTF-8 语义、键排序、无空白、禁 NaN/Infinity、拒重复键、精确数值）；
- :class:`ExactNumber` / :func:`encode_exact_number` / :func:`decode_exact_number` /
  :func:`parse_preserving_numbers`：agent-number-v1 精确数值编码（V04）——JSON number
  的原词法与无舍入十进制值在转 float 之前保留，``1e-1`` 与 ``0.1`` 词法不同而数值相等；
- :func:`stable_id`：稳定身份（§3.2 ``kind + "_" + sha256(canonical_json([1, kind, *parts]))``）；
- 校验基元：Digest / Identity / Revision / Sequence；
- 状态枚举：ReadState / VerificationState / SourceKind / CommitState / EffectState /
  Mode / Purpose（唯一 wire 拼写）。

正文原样保留：不 trim、不改 Unicode、不把合法代码前导零转数字。摘要不得携带凭据、
控件、Engine、锁或客户端引用。None 与空字符串、0、False、空数组保不同含义。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Final, NoReturn

# ---------------------------------------------------------------------------
# 类型别名与异常
# ---------------------------------------------------------------------------

#: CanonicalJson（§3.1）：UTF-8 JSON 字符串；结构版本 1；数值须先经 agent-number-v1
#: 精确编码，禁止把 Decimal/原数值直接交给默认 float 转换。
CanonicalJson = str

#: 兼容别名：规范 JSON 文档（同一类型，同一约束）。
JsonDoc = CanonicalJson


class AgentValidationError(ValueError):
    """纯校验失败（§3.1）：``code`` 为稳定错误码，``field_path`` 为 JSON 路径。

    服务边界负责将该异常转译为对应 Problem/回执；core 不做日志与脱敏。
    """

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


# ---------------------------------------------------------------------------
# 状态枚举（§3.1，唯一 wire 拼写）
# ---------------------------------------------------------------------------


class ReadState(Enum):
    """读取状态：读取成功和事实核验成功是不同字段。"""

    UNREAD = "unread"
    READY = "ready"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class VerificationState(Enum):
    """核验状态：程序核验与人工核验分列，unknown 不等于失败或成功。"""

    PROGRAM_VERIFIED = "program_verified"
    HUMAN_VERIFIED = "human_verified"
    UNVERIFIED = "unverified"
    UNKNOWN = "unknown"
    SOURCE_UNAVAILABLE = "source_unavailable"


class SourceKind(Enum):
    """来源类别（唯一 wire 拼写，无 message/direct_message 别名）。

    与草稿转移的 ``session_draft/business_preparation`` 枚举不混用（§3.3.2）。
    """

    BUSINESS_SNAPSHOT = "business_snapshot"
    ATTACHMENT = "attachment"
    USER_MESSAGE = "user_message"
    MCP_RETURN = "mcp_return"
    RESULT = "result"
    SUMMARY = "summary"


class CommitState(Enum):
    """提交三态：保存成功不等于投递或消费（INV-06）。"""

    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    COMMIT_UNKNOWN = "commit_unknown"


class EffectState(Enum):
    """效果状态：与结束状态分离（INV-07，unknown 不等于失败或成功）。"""

    NOT_DISPATCHED = "not_dispatched"
    DISPATCHED = "dispatched"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class Mode(Enum):
    """研究模式：仅用户选择或明确要求决定（F21）。"""

    DISCUSS = "discuss"
    PLAN = "plan"
    EXECUTE = "execute"


class Purpose(Enum):
    """研究用途（受理时冻结，A-01 授权 purpose）。"""

    CONCEPT = "concept"
    END_OF_DAY_RESEARCH = "end_of_day_research"
    HISTORICAL_DECISION = "historical_decision"
    POST_HOC_EXPLANATION = "post_hoc_explanation"


# ---------------------------------------------------------------------------
# 校验基元（§3.1/§3.2）
# ---------------------------------------------------------------------------

_DIGEST_RE: Final = re.compile(r"[0-9a-f]{64}")
#: JSON number 严格词法（RFC 8259）：禁前导零/孤立小数点/残留符号/空白。
_JSON_NUMBER_RE: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")

AGENT_NUMBER_ENCODING: Final = "agent-number-v1"
INTEGER_RAW_TYPE: Final = "json_integer"
DECIMAL_RAW_TYPE: Final = "json_decimal"


def _fail(code: str, field_path: str) -> NoReturn:
    raise AgentValidationError(code, field_path)


def sha256_hex(data: str | bytes) -> str:
    """小写 64 位十六进制 SHA-256 摘要；str 按 UTF-8 编码。"""

    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def is_digest(value: object) -> bool:
    """值是否为小写 64 位十六进制 SHA-256 摘要。"""

    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def require_identity(value: object, field_path: str) -> str:
    """非空 str 身份（S 类型）：不 trim、不改 Unicode；空串/非 str 拒绝。"""

    if not isinstance(value, str) or not value:
        _fail("invalid_identity", field_path)
    return value


def require_revision(value: object, field_path: str, *, allow_zero: bool = False) -> int:
    """Revision：非 bool int；起点 1；预期 revision / 初始水位允许 0。"""

    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_type", field_path)
    minimum = 0 if allow_zero else 1
    if value < minimum:
        _fail("invalid_range", field_path)
    return value


def require_sequence(value: object, field_path: str) -> int:
    """Sequence：非 bool int，≥0（观测水位 after_seq 允许 0）。"""

    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_type", field_path)
    if value < 0:
        _fail("invalid_range", field_path)
    return value


# ---------------------------------------------------------------------------
# CanonicalJson（§3.1）
# ---------------------------------------------------------------------------


def _fixed_point(value: Decimal) -> str:
    """Decimal 的确定性定点字符串（无指数形式）：``1E+2`` → ``100``。"""

    return format(value, "f")


def _to_jsonable(value: object, field_path: str) -> Any:
    """递归转为 JSON 可承载结构；不支持的类型显式拒绝，不走默认 float 转换。"""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _fixed_point(value)
    if isinstance(value, float):
        # 禁止把原数值直接交给默认 float 转换（§3.1）；须先经 encode_exact_number。
        _fail("invalid_type", field_path)
        return None  # pragma: no cover - _fail 总是抛出
    if isinstance(value, Enum):
        return _to_jsonable(value.value, field_path)
    if isinstance(value, ExactNumber):
        return {
            "numeric_encoding": AGENT_NUMBER_ENCODING,
            "raw_type": value.raw_type,
            "lexeme": value.lexeme,
            "exact_decimal": value.exact_decimal,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            name: _to_jsonable(getattr(value, name), f"{field_path}.{name}")
            for name in value.__dataclass_fields__  # pyright: ignore[reportAttributeAccessIssue]  # 运行期为 dataclass 实例，字段名由标准库提供
        }
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid_json", field_path)
            result[key] = _to_jsonable(item, f"{field_path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item, f"{field_path}[{index}]") for index, item in enumerate(value)]
    _fail("invalid_json", field_path)
    return None  # pragma: no cover - _fail 总是抛出


def json_doc(value: object) -> CanonicalJson:
    """规范 JSON：UTF-8 语义、键排序、无空白、禁 NaN/Infinity（§3.1）。"""

    encoded = _to_jsonable(value, "$")
    return json.dumps(encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_json_doc(doc: CanonicalJson) -> object:
    """严格解析：拒绝重复键与非有限数；浮点按 Decimal 精确承载。"""

    if not isinstance(doc, str):
        _fail("invalid_json", "$")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                _fail("duplicate_key", f"$.{key}")
            seen[key] = value
        return seen

    def _constant(name: str) -> object:
        _fail("non_finite_number", f"$.{name}")

    try:
        return json.loads(
            doc,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=Decimal,
        )
    except AgentValidationError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError):
        _fail("invalid_json", "$")
    return None  # pragma: no cover - _fail 总是抛出


# ---------------------------------------------------------------------------
# V04 agent-number-v1 精确数值编码（§3.3）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExactNumber:
    """agent-number-v1 记录：``raw_type=json_integer/json_decimal`` + 原词法 + 精确值。

    普通字符串即使形似数字也仍是 string；外层 ``raw_type`` 先区分
    json_integer/json_decimal 与 string/bool/null，内外类型一致才解码。
    """

    raw_type: str
    lexeme: str
    exact_decimal: str


def encode_exact_number(lexeme: str) -> ExactNumber:
    """把 JSON number 原 lexeme 编码为 agent-number-v1 记录（V04）。

    词法须符合严格 JSON number 语法；``exact_decimal`` 为无舍入定点十进制字符串
    （``1e-1`` → ``"0.1"``，``0.100000000000000005`` 原样保留）。
    """

    if not isinstance(lexeme, str) or _JSON_NUMBER_RE.fullmatch(lexeme) is None:
        _fail("invalid_number", "$")
    has_fraction = "." in lexeme
    has_exponent = "e" in lexeme or "E" in lexeme
    raw_type = DECIMAL_RAW_TYPE if (has_fraction or has_exponent) else INTEGER_RAW_TYPE
    exact = _fixed_point(Decimal(lexeme))
    return ExactNumber(raw_type=raw_type, lexeme=lexeme, exact_decimal=exact)


def decode_exact_number(value: object, field_path: str) -> Decimal:
    """解码 agent-number-v1 记录为精确 :class:`Decimal`（§3.3）。

    接受内存 :class:`ExactNumber` 或其 wire dict（全部键存在、禁额外键、
    编码标签/raw_type/词法一致性）；词法与 exact_decimal 不一致（含经 float
    丢精度的往返污染）一律拒绝。
    """

    path = f"{field_path}" if field_path else "$"
    if isinstance(value, ExactNumber):
        recomputed = encode_exact_number(value.lexeme)
        if recomputed.raw_type != value.raw_type or recomputed.exact_decimal != value.exact_decimal:
            _fail("invalid_number", path)
        return Decimal(value.lexeme)
    if not isinstance(value, dict):
        _fail("invalid_number", path)
    required = {"numeric_encoding", "raw_type", "lexeme", "exact_decimal"}
    missing = sorted(required - value.keys())
    if missing:
        _fail("invalid_number", f"{path}.{missing[0]}")
    extra = sorted(value.keys() - required)
    if extra:
        _fail("invalid_number", f"{path}.{extra[0]}")
    encoding = value["numeric_encoding"]
    raw_type = value["raw_type"]
    lexeme = value["lexeme"]
    exact = value["exact_decimal"]
    if encoding != AGENT_NUMBER_ENCODING:
        _fail("invalid_number", f"{path}.numeric_encoding")
    if raw_type not in (INTEGER_RAW_TYPE, DECIMAL_RAW_TYPE):
        _fail("invalid_number", f"{path}.raw_type")
    if not isinstance(lexeme, str) or not isinstance(exact, str) or not exact:
        _fail("invalid_number", path)
    recomputed = encode_exact_number(lexeme)
    if recomputed.raw_type != raw_type or recomputed.exact_decimal != exact:
        _fail("invalid_number", path)
    return Decimal(lexeme)


def parse_preserving_numbers(doc: str) -> object:
    """受控 JSON 解析：在转 float 之前保留 number 原 lexeme（V04）。

    数字解析为 :class:`ExactNumber`；普通字符串即使形似数字也仍是 string；
    拒绝重复键与 NaN/Infinity。
    """

    if not isinstance(doc, str):
        _fail("invalid_json", "$")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                _fail("duplicate_key", f"$.{key}")
            seen[key] = value
        return seen

    def _int_hook(lexeme: str) -> ExactNumber:
        return encode_exact_number(lexeme)

    def _float_hook(lexeme: str) -> ExactNumber:
        return encode_exact_number(lexeme)

    def _constant(name: str) -> object:
        _fail("non_finite_number", f"$.{name}")

    try:
        return json.loads(
            doc,
            object_pairs_hook=_pairs,
            parse_int=_int_hook,
            parse_float=_float_hook,
            parse_constant=_constant,
        )
    except AgentValidationError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError):
        _fail("invalid_json", "$")
    return None  # pragma: no cover - _fail 总是抛出


# ---------------------------------------------------------------------------
# 稳定身份（§3.2）
# ---------------------------------------------------------------------------


def stable_id(kind: str, parts: Sequence[str]) -> str:
    """稳定身份：``kind + "_" + sha256(canonical_json([1, kind, *parts]))``（§3.2）。

    parts 顺序即身份（非集合）；输入 digest 不进入 intent 身份（意图异输入
    报冲突而非新建研究，§3.2）。
    """

    kind_text = require_identity(kind, "$.kind")
    normalized: list[str] = []
    for index, part in enumerate(parts):
        normalized.append(require_identity(part, f"$.parts[{index}]"))
    payload = json_doc([1, kind_text, *normalized])
    return f"{kind_text}_{sha256_hex(payload)}"
