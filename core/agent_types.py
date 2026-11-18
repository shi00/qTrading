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
from collections.abc import Iterable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, is_dataclass
from dataclasses import field as dataclass_field
from datetime import datetime as dt
from datetime import UTC
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import PureWindowsPath
from typing import Any, Final, NoReturn, cast

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
    """研究模式：仅用户选择或明确要求决定（F21）。

    ``ANSWER`` 为直答模式：不调用研究工具、不委派、不启动研究操作，直接给出
    回答（R2-I08）；``DISCUSS`` 只理解获准资料，``PLAN`` 只交付方法，
    ``EXECUTE`` 才启动研究操作。
    """

    DISCUSS = "discuss"
    PLAN = "plan"
    EXECUTE = "execute"
    ANSWER = "answer"


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

#: 定点十进制字符串的字符上限（等于附件单文件字符预算）：短词法如 ``1E+10000000``
#: 若先 ``format`` 再校验长度，会在检查前分配巨串；故在展开前按位数/指数推导长度拒绝。
MAX_EXACT_DECIMAL_CHARS: Final = 100_000


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


def _fixed_point(value: Decimal, field_path: str = "$") -> str:
    """Decimal 的确定性定点字符串（无指数形式）：``1E+2`` → ``100``。

    先按 ``as_tuple()`` 的位数与指数推导输出长度（O(1)，不实际展开），超过
    :data:`MAX_EXACT_DECIMAL_CHARS` 直接拒绝，避免短词法在 ``format`` 时才展开成巨串；
    非有限值（NaN/Infinity）同样拒绝，不以字符串形态冒充成功。
    """

    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        _fail("non_finite_number", field_path)
    if any(digits):
        integral = max(len(digits) + exponent, 1)
        length = integral if exponent >= 0 else integral + 1 - exponent
    else:
        # 零：正指数不展开（``0E+5`` → ``0``），负指数补齐小数位（``0E-5`` → ``0.00000``）。
        length = 1 if exponent >= 0 else 2 - exponent
    if sign:
        length += 1
    if length > MAX_EXACT_DECIMAL_CHARS:
        _fail("exact_decimal_length_exceeded", field_path)
    return format(value, "f")


def _to_jsonable(value: object, field_path: str) -> Any:
    """递归转为 JSON 可承载结构；不支持的类型显式拒绝，不走默认 float 转换。"""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _fixed_point(value, field_path)
    if isinstance(value, float):
        # 禁止把原数值直接交给默认 float 转换（§3.1）；须先经 encode_exact_number。
        _fail("invalid_type", field_path)
        return None  # pragma: no cover - _fail 总是抛出
    if isinstance(value, Enum):
        return _to_jsonable(value.value, field_path)
    if isinstance(value, Selection):
        # wire 合同：all 没有 ranges 键（§3.3.1）
        wire: dict[str, Any] = {"kind": value.kind}
        if value.kind == "limited":
            wire["ranges"] = _to_jsonable(list(value.ranges), f"{field_path}.ranges")
        return wire
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
    try:
        decimal_value = Decimal(lexeme)
    except InvalidOperation:
        # 指数超出 Decimal 可承载范围：词法合法但值无法精确表示，拒绝。
        _fail("invalid_number", "$")
    exact = _fixed_point(decimal_value)
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


# ---------------------------------------------------------------------------
# §3.3.1 资料许可v1嵌套wire合同（唯一类型定义）
# ---------------------------------------------------------------------------

#: wire 端点类型：S=非空字符串、D=小写64位hex摘要、N=≥0整数、P=≥1整数，
#: 均拒 bool；T=UTC 微秒文本（由消费方核验）。除明确列出的 null 外不可空。
_ALL_KEYS_ERR_MISSING: Final = "missing_field"
_ALL_KEYS_ERR_UNKNOWN: Final = "unknown_field"


@dataclass(frozen=True, slots=True)
class SourceVersionRef:
    """受管源版引用：四值源身份，用户域继承许可主体，不接路径/URL/显示名（§3.3.1）。"""

    source_kind: SourceKind
    source_id: str
    source_version: str
    source_digest: str


@dataclass(frozen=True, slots=True)
class AllAtom:
    """ReadAtom 的 all 分型：仅可在 selection.kind=all 时独占对应数组（§3.3.1）。"""

    kind: str = "all"


@dataclass(frozen=True, slots=True)
class PagesRange:
    """PDF 页号范围，两端包含。"""

    kind: str = "pages"
    start: int = 1
    end: int = 1


@dataclass(frozen=True, slots=True)
class SheetsRange:
    """准确表名的非空无重复有序列表。"""

    kind: str = "sheets"
    names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RowsRange:
    """解析记录/物理表行号范围，两端包含；XLSX 必须准确表名，CSV 为 null。"""

    kind: str = "rows"
    sheet: str | None = None
    start: int = 1
    end: int = 1


@dataclass(frozen=True, slots=True)
class FieldsRange:
    """字段键范围；两行端点同时 null 表示所选源中该字段的全部行，否则同时非空。"""

    kind: str = "fields"
    sheet: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParagraphsRange:
    """TXT/MD 解析器段号范围，两端包含，不以显示换行重新编号。"""

    kind: str = "paragraphs"
    start: int = 1
    end: int = 1


@dataclass(frozen=True, slots=True)
class CodepointsRange:
    """准确文本表示内 Unicode 码点号范围，两端包含，不按 UTF-16 单元或字节计号。"""

    kind: str = "codepoints"
    start: int = 1
    end: int = 1


@dataclass(frozen=True, slots=True)
class JsonPointerRange:
    """RFC6901 JSON Pointer：必须以 / 开头，只允许 ~0/~1 转义，禁前导零与 -。"""

    kind: str = "json_pointer"
    pointer: str = "/"


#: RangeAtom 封闭联合：标签决定全部键，不接受其它分型的键（§3.3.1）。
RangeAtom = PagesRange | SheetsRange | RowsRange | FieldsRange | ParagraphsRange | CodepointsRange | JsonPointerRange
#: ReadAtom = RangeAtom 或 all。
ReadAtom = RangeAtom | AllAtom


@dataclass(frozen=True, slots=True)
class Selection:
    """选择：all 或 limited（limited 数组非空，禁止在其中使用 all）（§3.3.1）。"""

    kind: str
    ranges: tuple[RangeAtom, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """封闭定位对象：定位准确受管源版及范围，不是源字符串（§3.3.1）。"""

    locator_kind: str
    origin_ref: SourceVersionRef
    selection: Selection


@dataclass(frozen=True, slots=True)
class RangeStats:
    """范围统计：未知/不适用为 null，0 只指已知零，绝不据 null 放开上限（§3.3.1）。"""

    source_bytes: int | None = None
    pages: int | None = None
    sheets: int | None = None
    rows: int | None = None
    chars: int | None = None


@dataclass(frozen=True, slots=True)
class RangeLimitation:
    """限制记录：code 为稳定标识，message 为已脱敏说明；顺序为呈现顺序（§3.3.1）。"""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SelectedRange:
    """选定范围：读取/未读、统计、限制及准确接受记录（§3.3.1）。

    ``accepted_limited_version`` 接受受限时等于本项 source_version；未接受为
    null，不能由读取成功推导接受、许可或放宽每文件累计上限。
    """

    selection: Selection
    read_ranges: tuple[ReadAtom, ...] = ()
    unread_ranges: tuple[ReadAtom, ...] = ()
    stats: RangeStats = RangeStats()
    limitations: tuple[RangeLimitation, ...] = ()
    accepted_limited_version: str | None = None


@dataclass(frozen=True, slots=True)
class ProvenanceRef:
    """谱系边：source 必须等于 locator.origin_ref（§3.3.1）。"""

    source: SourceVersionRef
    locator: SourceLocator
    selected_range_digest: str
    representation_version: str
    representation_digest: str


# ---------------------------------------------------------------------------
# 研究成果合同（DD §3.5：ResearchResult 是已可靠保存的成果版本）
# ---------------------------------------------------------------------------


def _require_result_text(value: object, path: str) -> None:
    if not isinstance(value, str) or not value:
        _fail("invalid_value", path)


@dataclass(frozen=True, slots=True)
class ResearchObjectGap:
    """对象级缺口（F04：缺对象/缺期间/冲突/未核验论据逐对象保留，不汇总成「无问题」）。

    ``object_ref`` 为被研究对象的稳定身份（股票/期间/叶节点等，随调用方上下文）；
    ``detail`` 记录证据或原因（如缺失指标、冲突来源、未核验断言），不得为空——
    空原因等于把「已知缺口」伪装成「无缺口」（R21）。
    """

    object_ref: str
    gap_kind: str
    detail: str

    def __post_init__(self) -> None:
        for name in ("object_ref", "detail"):
            _require_result_text(getattr(self, name), f"$.{name}")
        if self.gap_kind not in RESEARCH_GAP_KINDS:
            _fail("invalid_gap_kind", "$.gap_kind")

    def to_wire(self) -> dict[str, str]:
        return {"object_ref": self.object_ref, "gap_kind": self.gap_kind, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ResearchCompletionAssessment:
    """研究完成核定（F04）：执行结束、成果保存、目标覆盖三项分别核定后再给结论。

    ``execution_ended`` 为 ``None`` 表示执行状态本身未核定（不推断结束）；``False``
    表示仍在进行；``result_saved`` 是保存事实而非文本存在；``goal_coverage`` 是核定后
    的有效覆盖（声明 ``full`` 但存在缺口/未收敛时降级 ``partial``），与声明的
    ``ResearchResultDraft.goal_coverage`` 同时保留。``outcome`` 为组级结论：
    任一事实未核定即 ``unknown``（未知优先于失败），不按成功叶数量推断完整。
    """

    execution_ended: bool | None
    result_saved: bool
    goal_coverage: str
    outcome: str
    object_gaps: tuple[ResearchObjectGap, ...]

    def __post_init__(self) -> None:
        if self.execution_ended is not None and not isinstance(self.execution_ended, bool):
            _fail("invalid_type", "$.execution_ended")
        if not isinstance(self.result_saved, bool):
            _fail("invalid_type", "$.result_saved")
        if self.goal_coverage not in GOAL_COVERAGES:
            _fail("invalid_goal_coverage", "$.goal_coverage")
        if self.outcome not in RESEARCH_OUTCOMES:
            _fail("invalid_research_outcome", "$.outcome")
        if not isinstance(self.object_gaps, tuple) or any(
            not isinstance(gap, ResearchObjectGap) for gap in self.object_gaps
        ):
            _fail("invalid_type", "$.object_gaps")


def assess_research_completion(
    *,
    execution_state: str,
    final_output_saved: bool,
    goal_coverage: str,
    convergence_confirmed: bool,
    object_gaps: tuple[ResearchObjectGap, ...] = (),
    missing_fields: tuple[str, ...] = (),
    data_warnings: tuple[str, ...] = (),
) -> ResearchCompletionAssessment:
    """按三项独立事实核定研究完成结论（F04；不由「任务跑完」推断目标达成）。

    判定规则：

    - **执行结束**：终态（``NON_RECOVERABLE_DISPLAY_STATES``）为已结束；``unknown``
      状态为未核定（``None``）；其余（排队/运行/等待/暂停）为未结束（``False``）。
    - **成果已保存**：只采用保存事实（``final_output_saved``），不以正文非空代替。
    - **目标覆盖**：声明 ``full`` 但存在对象级缺口、声明缺失字段/数据告警或未确认
      收敛时降级 ``partial``（缺证不得判完整）；声明 ``unknown`` 保 ``unknown``。
    - **组级结论**：执行未结束/未核定、执行 ``closed_unknown`` 或目标覆盖 ``unknown``
      一律 ``unknown``（未知优先于失败）；已结束且无可靠成果为 ``failed``；有效覆盖
      ``full`` 为 ``complete``；其余（有可独立使用成果但目标未完全覆盖）为 ``partial``。
      可靠确认的零匹配由调用方以 ``goal_coverage=full`` 且无缺口表达；读取失败或未
      确认须以缺口/``unknown`` 表达，不得冒充零匹配。
    """

    if execution_state == "unknown":
        execution_ended: bool | None = None
    else:
        execution_ended = execution_state in NON_RECOVERABLE_DISPLAY_STATES
    effective_coverage = goal_coverage
    if goal_coverage == "full" and (object_gaps or missing_fields or data_warnings or not convergence_confirmed):
        effective_coverage = "partial"
    if execution_state in {"unknown", "closed_unknown"} or execution_ended is not True or goal_coverage == "unknown":
        outcome = "unknown"
    elif not final_output_saved:
        outcome = "failed"
    elif effective_coverage == "full":
        outcome = "complete"
    else:
        outcome = "partial"
    return ResearchCompletionAssessment(
        execution_ended=execution_ended,
        result_saved=final_output_saved,
        goal_coverage=effective_coverage,
        outcome=outcome,
        object_gaps=object_gaps,
    )


def _require_text_tuple(value: object, path: str) -> None:
    if not isinstance(value, tuple):
        _fail("invalid_type", path)
    for index, item in enumerate(value):
        _require_result_text(item, f"{path}[{index}]")


def _require_json_object(value: object, path: str) -> None:
    if not isinstance(value, str):
        _fail("invalid_type", path)
    if not isinstance(decode_json_doc(value), dict):
        _fail("invalid_type", path)


@dataclass(frozen=True, slots=True)
class ResearchResultDraft:
    """待保存的成果内容（DD §3.5；保存成功后才是 :class:`ResearchResult`）。

    数据质量字段缺失一律用 ``None`` / 空元组 / 显式 unknown 表示，禁止填充
    业务上合法的具体值（R21）。``body_text`` 与结构化输出至少一项非空：
    空成果不得伪装成已保存成果。
    """

    user_scope: str
    session_id: str
    execution_id: str
    round_id: str
    node_id: str
    input_revision: int
    actual_input_digest: str
    actual_model_ref: str

    result_kind: str
    summary: str | None
    key_points: tuple[str, ...]
    body_text: str | None
    structured_fields: CanonicalJson | None
    object_rows: tuple[CanonicalJson, ...]

    evidence_links: tuple[str, ...]
    source_available_state: str
    coverage: str | None
    missing_fields: tuple[str, ...]
    failed_signal_dates: tuple[str, ...]
    data_warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    verification_state: str

    execution_state: str
    goal_coverage: str
    convergence_confirmed: bool
    unknown_operation_refs: tuple[str, ...]
    ledger_refs: tuple[str, ...]
    plan_version: int | None
    parent_artifact_refs: tuple[str, ...]
    source_refs: tuple[SourceVersionRef, ...]
    #: 对象级缺口（可选，关键字传入；不占位置参数序，保存子类仍可追加身份字段）。
    object_gaps: tuple[ResearchObjectGap, ...] = dataclass_field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        for name in ("user_scope", "session_id", "execution_id", "round_id", "node_id", "actual_model_ref"):
            _require_result_text(getattr(self, name), f"$.{name}")
        if isinstance(self.input_revision, bool) or not isinstance(self.input_revision, int) or self.input_revision < 1:
            _fail("invalid_revision", "$.input_revision")
        if not is_digest(self.actual_input_digest):
            _fail("invalid_digest", "$.actual_input_digest")
        if self.result_kind not in ARTIFACT_KINDS:
            _fail("invalid_artifact_kind", "$.result_kind")

        if self.summary is not None:
            _require_result_text(self.summary, "$.summary")
        _require_text_tuple(self.key_points, "$.key_points")
        if self.body_text is not None:
            _require_result_text(self.body_text, "$.body_text")
        if self.structured_fields is not None:
            _require_json_object(self.structured_fields, "$.structured_fields")
        if not isinstance(self.object_rows, tuple):
            _fail("invalid_type", "$.object_rows")
        for index, row in enumerate(self.object_rows):
            _require_json_object(row, f"$.object_rows[{index}]")

        _require_text_tuple(self.evidence_links, "$.evidence_links")
        _require_result_text(self.source_available_state, "$.source_available_state")
        if self.coverage is not None:
            _require_result_text(self.coverage, "$.coverage")
        for name in (
            "missing_fields",
            "failed_signal_dates",
            "data_warnings",
            "limitations",
            "unknown_operation_refs",
            "ledger_refs",
            "parent_artifact_refs",
        ):
            _require_text_tuple(getattr(self, name), f"$.{name}")
        if self.verification_state not in {item.value for item in VerificationState}:
            _fail("invalid_verification_state", "$.verification_state")
        if self.execution_state not in DISPLAY_STATES:
            _fail("invalid_display_state", "$.execution_state")
        if self.goal_coverage not in GOAL_COVERAGES:
            _fail("invalid_goal_coverage", "$.goal_coverage")
        if not isinstance(self.convergence_confirmed, bool):
            _fail("invalid_type", "$.convergence_confirmed")
        if self.plan_version is not None and (
            isinstance(self.plan_version, bool) or not isinstance(self.plan_version, int) or self.plan_version < 1
        ):
            _fail("invalid_revision", "$.plan_version")
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(ref, SourceVersionRef) for ref in self.source_refs
        ):
            _fail("invalid_type", "$.source_refs")
        if not isinstance(self.object_gaps, tuple) or any(
            not isinstance(gap, ResearchObjectGap) for gap in self.object_gaps
        ):
            _fail("invalid_type", "$.object_gaps")
        seen_gaps: set[tuple[str, str]] = set()
        for index, gap in enumerate(self.object_gaps):
            key = (gap.object_ref, gap.gap_kind)
            if key in seen_gaps:
                _fail("duplicate_object_gap", f"$.object_gaps[{index}]")
            seen_gaps.add(key)
        if not (self.summary or self.key_points or self.body_text or self.structured_fields or self.object_rows):
            _fail("empty_result", "$")

    def assess_completion(self, *, final_output_saved: bool) -> ResearchCompletionAssessment:
        """按本成果已记录的声明与缺口核定完成事实（保存事实由调用方传入）。"""

        return assess_research_completion(
            execution_state=self.execution_state,
            final_output_saved=final_output_saved,
            goal_coverage=self.goal_coverage,
            convergence_confirmed=self.convergence_confirmed,
            object_gaps=self.object_gaps,
            missing_fields=self.missing_fields,
            data_warnings=self.data_warnings,
        )


@dataclass(frozen=True, slots=True)
class ResearchResult(ResearchResultDraft):
    """已可靠保存的成果版本（DD §3.5）：带成果身份与保存事实，不是模型最终文本块。"""

    artifact_id: str
    artifact_version: int
    final_output_saved: bool

    @classmethod
    def from_draft(cls, draft: ResearchResultDraft, *, artifact_id: str, artifact_version: int) -> ResearchResult:
        """已保存成果：内容字段原样取自 draft，保存事实由调用方核定。"""

        return cls(
            user_scope=draft.user_scope,
            session_id=draft.session_id,
            execution_id=draft.execution_id,
            round_id=draft.round_id,
            node_id=draft.node_id,
            input_revision=draft.input_revision,
            actual_input_digest=draft.actual_input_digest,
            actual_model_ref=draft.actual_model_ref,
            result_kind=draft.result_kind,
            summary=draft.summary,
            key_points=draft.key_points,
            body_text=draft.body_text,
            structured_fields=draft.structured_fields,
            object_rows=draft.object_rows,
            evidence_links=draft.evidence_links,
            source_available_state=draft.source_available_state,
            coverage=draft.coverage,
            missing_fields=draft.missing_fields,
            failed_signal_dates=draft.failed_signal_dates,
            data_warnings=draft.data_warnings,
            limitations=draft.limitations,
            verification_state=draft.verification_state,
            execution_state=draft.execution_state,
            goal_coverage=draft.goal_coverage,
            convergence_confirmed=draft.convergence_confirmed,
            unknown_operation_refs=draft.unknown_operation_refs,
            ledger_refs=draft.ledger_refs,
            plan_version=draft.plan_version,
            parent_artifact_refs=draft.parent_artifact_refs,
            source_refs=draft.source_refs,
            object_gaps=draft.object_gaps,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            final_output_saved=True,
        )

    @property
    def completion(self) -> ResearchCompletionAssessment:
        """已保存成果的完成核定（三项事实分别核定 + 组级结论）。"""

        return self.assess_completion(final_output_saved=self.final_output_saved)

    def __post_init__(self) -> None:
        ResearchResultDraft.__post_init__(self)
        _require_result_text(self.artifact_id, "$.artifact_id")
        if (
            isinstance(self.artifact_version, bool)
            or not isinstance(self.artifact_version, int)
            or self.artifact_version < 1
        ):
            _fail("invalid_revision", "$.artifact_version")
        if self.final_output_saved is not True:
            _fail("result_not_saved", "$.final_output_saved")


def encode_research_result_payload(draft: ResearchResultDraft, *, final_output_saved: bool) -> CanonicalJson:
    """成果内容 → 保存载荷（DD §9.7：正文单独存 ``agent_artifact.body_text``）。

    载荷只承载结构/限制/证据与正文定位，不重复存正文；正文能否重建由
    ``body_saved`` 显式标记，不以摘要非空冒充完整成果（R21）。``completion`` 同时
    保存声明的目标覆盖与核定结论（执行结束/成果保存/有效覆盖/对象级缺口），
    读取方不必从「任务跑完」推断目标达成（F04）。
    """

    assessment = draft.assess_completion(final_output_saved=final_output_saved)
    return json_doc(
        {
            "schema_version": 2,
            "identity": {
                "user_scope": draft.user_scope,
                "session_id": draft.session_id,
                "execution_id": draft.execution_id,
                "round_id": draft.round_id,
                "node_id": draft.node_id,
                "input_revision": draft.input_revision,
                "actual_input_digest": draft.actual_input_digest,
                "actual_model_ref": draft.actual_model_ref,
            },
            "output": {
                "result_kind": draft.result_kind,
                "summary": draft.summary,
                "key_points": list(draft.key_points),
                "body_saved": draft.body_text is not None,
                "body_text_digest": (None if draft.body_text is None else sha256_hex(draft.body_text.encode("utf-8"))),
                "structured_fields": (
                    None if draft.structured_fields is None else decode_json_doc(draft.structured_fields)
                ),
                "object_rows": [decode_json_doc(row) for row in draft.object_rows],
            },
            "trust": {
                "evidence_links": list(draft.evidence_links),
                "source_available_state": draft.source_available_state,
                "coverage": draft.coverage,
                "missing_fields": list(draft.missing_fields),
                "failed_signal_dates": list(draft.failed_signal_dates),
                "data_warnings": list(draft.data_warnings),
                "limitations": list(draft.limitations),
                "verification_state": draft.verification_state,
            },
            "completion": {
                "execution_state": draft.execution_state,
                "execution_ended": assessment.execution_ended,
                "goal_coverage": draft.goal_coverage,
                "goal_coverage_effective": assessment.goal_coverage,
                "final_output_saved": final_output_saved,
                "result_saved": assessment.result_saved,
                "outcome": assessment.outcome,
                "convergence_confirmed": draft.convergence_confirmed,
                "unknown_operation_refs": list(draft.unknown_operation_refs),
                "object_gaps": [gap.to_wire() for gap in assessment.object_gaps],
            },
            "usage": {"ledger_refs": list(draft.ledger_refs)},
            "trace": {
                "plan_version": draft.plan_version,
                "parent_artifact_refs": list(draft.parent_artifact_refs),
                "source_refs": [encode_source_version_ref(ref) for ref in draft.source_refs],
            },
        }
    )


def research_result_digest(draft: ResearchResultDraft) -> str:
    """成果规范摘要（保存后写入 ``agent_artifact.result_digest``）。"""

    return sha256_hex(encode_research_result_payload(draft, final_output_saved=True))


def to_wire_dict(value: object) -> dict[str, Any]:
    """frozen dataclass/tuple → wire JSON 对象（CanonicalJson 语义，§3.3.1）。"""

    return cast(dict[str, Any], _to_jsonable(value, "$"))


def selected_range_digest(selected_range: SelectedRange) -> str:
    """``sha256(CanonicalJson(selected_range))``：覆盖读取/未读、统计、限制及接受记录。"""

    return sha256_hex(json_doc(selected_range))


# ---- §3.3.1 解码基元（全键必填、禁额外键、错误 tag/null/type/bool 拒绝） ----


def _require_keys(obj: dict[str, object], required: AbstractSet[str], path: str) -> None:
    missing = sorted(required - obj.keys())
    if missing:
        _fail(_ALL_KEYS_ERR_MISSING, f"{path}.{missing[0]}")
    extra = sorted(obj.keys() - required)
    if extra:
        _fail(_ALL_KEYS_ERR_UNKNOWN, f"{path}.{extra[0]}")


def _require_keys_with_optional(
    obj: dict[str, object], required: AbstractSet[str], optional: AbstractSet[str], path: str
) -> None:
    """必填键全在、额外键仅限 optional（strict 意义下的可选键分支）。"""

    missing = sorted(required - obj.keys())
    if missing:
        _fail(_ALL_KEYS_ERR_MISSING, f"{path}.{missing[0]}")
    extra = sorted(obj.keys() - required - optional)
    if extra:
        _fail(_ALL_KEYS_ERR_UNKNOWN, f"{path}.{extra[0]}")


def _require_str(value: object, path: str) -> str:
    return require_identity(value, path)


def _require_digest(value: object, path: str) -> str:
    if not is_digest(value):
        _fail("invalid_digest", path)
    return cast(str, value)


def _require_p(value: object, path: str) -> int:
    """P 端点：≥1 整数，拒 bool。"""

    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_type", path)
    if value < 1:
        _fail("invalid_range", path)
    return value


def _require_n(value: object, path: str) -> int | None:
    """N：≥0 整数或 null，拒 bool。"""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_type", path)
    if value < 0:
        _fail("invalid_range", path)
    return value


def _require_str_list(value: object, path: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail("invalid_type", path)
    if not value and not allow_empty:
        _fail("invalid_range", path)
    items: list[str] = []
    for index, item in enumerate(value):
        items.append(_require_str(item, f"{path}[{index}]"))
    if len(set(items)) != len(items):
        _fail("invalid_range", path)
    return tuple(items)


def _validate_json_pointer(pointer: object, path: str) -> str:
    """RFC6901 校验：以 / 开头、仅 ~0/~1 转义、数组索引无前导零、禁 -（§3.3.1）。"""

    if not isinstance(pointer, str) or not pointer.startswith("/"):
        _fail("invalid_pointer", path)
    for token in pointer.split("/")[1:]:
        if token == "-":
            _fail("invalid_pointer", path)
        if token.isdigit() and len(token) > 1 and token[0] == "0":
            _fail("invalid_pointer", path)
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    _fail("invalid_pointer", path)
                index += 2
            else:
                index += 1
    return pointer


def decode_source_version_ref(value: object, path: str) -> SourceVersionRef:
    """严格解码 SourceVersionRef wire 对象（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, {"source_kind", "source_id", "source_version", "source_digest"}, path)
    kind_value = value["source_kind"]
    if not isinstance(kind_value, str):
        _fail("invalid_tag", f"{path}.source_kind")
    try:
        kind = SourceKind(kind_value)
    except ValueError:
        _fail("invalid_tag", f"{path}.source_kind")
    return SourceVersionRef(
        source_kind=kind,
        source_id=_require_str(value["source_id"], f"{path}.source_id"),
        source_version=_require_str(value["source_version"], f"{path}.source_version"),
        source_digest=_require_digest(value["source_digest"], f"{path}.source_digest"),
    )


def encode_source_version_ref(ref: SourceVersionRef) -> dict[str, Any]:
    """SourceVersionRef → wire 四键（§3.3.1）；不含路径/URL/显示名。"""

    if not isinstance(ref.source_kind, SourceKind):
        _fail("invalid_tag", "$.source_kind")
    return {
        "source_kind": ref.source_kind.value,
        "source_id": _require_str(ref.source_id, "$.source_id"),
        "source_version": _require_str(ref.source_version, "$.source_version"),
        "source_digest": _require_digest(ref.source_digest, "$.source_digest"),
    }


def decode_range_atom(value: object, path: str, *, allow_all: bool = False) -> ReadAtom:
    """严格解码 RangeAtom/ReadAtom：标签决定全部键（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    kind = value.get("kind")
    if not isinstance(kind, str):
        _fail("invalid_tag", f"{path}.kind")

    if kind == "all":
        if not allow_all:
            _fail("invalid_tag", f"{path}.kind")
        _require_keys(value, {"kind"}, path)
        return AllAtom()

    if kind in ("pages", "paragraphs", "codepoints"):
        _require_keys(value, {"kind", "start", "end"}, path)
        start = _require_p(value["start"], f"{path}.start")
        end = _require_p(value["end"], f"{path}.end")
        if start > end:
            _fail("invalid_range", path)
        cls = {"pages": PagesRange, "paragraphs": ParagraphsRange, "codepoints": CodepointsRange}[kind]
        return cls(start=start, end=end)  # pyright: ignore[reportReturnType]  # 同构分型由 kind 分派闭合

    if kind == "sheets":
        _require_keys(value, {"kind", "names"}, path)
        return SheetsRange(names=_require_str_list(value["names"], f"{path}.names"))

    if kind == "rows":
        _require_keys(value, {"kind", "sheet", "start", "end"}, path)
        sheet = value["sheet"]
        if sheet is not None:
            sheet = _require_str(sheet, f"{path}.sheet")
        start = _require_p(value["start"], f"{path}.start")
        end = _require_p(value["end"], f"{path}.end")
        if start > end:
            _fail("invalid_range", path)
        return RowsRange(sheet=sheet, start=start, end=end)

    if kind == "fields":
        _require_keys(value, {"kind", "sheet", "row_start", "row_end", "keys"}, path)
        sheet = value["sheet"]
        if sheet is not None:
            sheet = _require_str(sheet, f"{path}.sheet")
        row_start = value["row_start"]
        row_end = value["row_end"]
        if (row_start is None) != (row_end is None):
            _fail("invalid_range", f"{path}.row_start")
        if row_start is not None:
            row_start = _require_p(row_start, f"{path}.row_start")
            row_end = _require_p(row_end, f"{path}.row_end")
            if row_start > row_end:
                _fail("invalid_range", path)
        keys = _require_str_list(value["keys"], f"{path}.keys")
        return FieldsRange(sheet=sheet, row_start=row_start, row_end=row_end, keys=keys)

    if kind == "json_pointer":
        _require_keys(value, {"kind", "pointer"}, path)
        return JsonPointerRange(pointer=_validate_json_pointer(value["pointer"], f"{path}.pointer"))

    _fail("invalid_tag", f"{path}.kind")


def decode_selection(value: object, path: str) -> Selection:
    """严格解码 Selection：all 或 limited（数组非空、单轴、禁 all 分型）（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    kind = value.get("kind")
    if not isinstance(kind, str):
        _fail("invalid_tag", f"{path}.kind")
    if kind == "all":
        _require_keys(value, {"kind"}, path)
        return Selection(kind="all", ranges=())
    if kind == "limited":
        _require_keys(value, {"kind", "ranges"}, path)
        raw_ranges = value["ranges"]
        if not isinstance(raw_ranges, list) or not raw_ranges:
            _fail("invalid_range", f"{path}.ranges")
        atoms = tuple(decode_range_atom(item, f"{path}.ranges[{index}]") for index, item in enumerate(raw_ranges))
        if len({type(atom) for atom in atoms}) > 1:
            _fail("kind_mismatch", f"{path}.ranges")
        return Selection(kind="limited", ranges=atoms)  # pyright: ignore[reportArgumentType]  # allow_all=False 排除 AllAtom
    _fail("invalid_tag", f"{path}.kind")


def _require_same_selection(expected: Selection, actual: Selection, path: str) -> None:
    """冗余承载的 selection 必须逐原子一致（§3.3.1）。

    ``all`` 与 ``limited`` 语义互斥，不得互相当作；同为 ``limited`` 但范围集合
    不同同样是分叉。
    """

    if expected.kind != actual.kind:
        _fail("kind_mismatch", path)
    if expected.ranges != actual.ranges:
        _fail("range_mismatch", path)


def decode_source_locator(value: object, path: str) -> SourceLocator:
    """严格解码 SourceLocator（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, {"locator_kind", "origin_ref", "selection"}, path)
    locator_kind = value["locator_kind"]
    if locator_kind != "source_range":
        _fail("invalid_tag", f"{path}.locator_kind")
    return SourceLocator(
        locator_kind=locator_kind,
        origin_ref=decode_source_version_ref(value["origin_ref"], f"{path}.origin_ref"),
        selection=decode_selection(value["selection"], f"{path}.selection"),
    )


def decode_range_stats(value: object, path: str) -> RangeStats:
    """严格解码 RangeStats（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, {"source_bytes", "pages", "sheets", "rows", "chars"}, path)
    return RangeStats(
        source_bytes=_require_n(value["source_bytes"], f"{path}.source_bytes"),
        pages=_require_n(value["pages"], f"{path}.pages"),
        sheets=_require_n(value["sheets"], f"{path}.sheets"),
        rows=_require_n(value["rows"], f"{path}.rows"),
        chars=_require_n(value["chars"], f"{path}.chars"),
    )


def decode_range_limitation(value: object, path: str) -> RangeLimitation:
    """严格解码 RangeLimitation（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, {"code", "message"}, path)
    return RangeLimitation(
        code=_require_str(value["code"], f"{path}.code"),
        message=_require_str(value["message"], f"{path}.message"),
    )


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    return tuple(pointer.split("/")[1:])


def _is_prefix(prefix: JsonPointerRange, other: JsonPointerRange) -> bool:
    a = _pointer_tokens(prefix.pointer)
    b = _pointer_tokens(other.pointer)
    return len(a) <= len(b) and b[: len(a)] == a


def _atoms_overlap(a: ReadAtom, b: ReadAtom) -> bool:
    """同轴两原子是否重叠（含 JSON 祖先/子树重叠与重复）（§3.3.1）。"""

    if isinstance(a, AllAtom) or isinstance(b, AllAtom):
        return True
    if type(a) is not type(b):
        return False
    if isinstance(a, (PagesRange, ParagraphsRange, CodepointsRange)):
        b_numeric = cast(PagesRange | ParagraphsRange | CodepointsRange, b)
        return a.start <= b_numeric.end and b_numeric.start <= a.end
    if isinstance(a, SheetsRange):
        b_sheets = cast(SheetsRange, b)
        return bool(set(a.names) & set(b_sheets.names))
    if isinstance(a, RowsRange):
        b_rows = cast(RowsRange, b)
        return a.sheet == b_rows.sheet and a.start <= b_rows.end and b_rows.start <= a.end
    if isinstance(a, FieldsRange):
        b_fields = cast(FieldsRange, b)
        if a.sheet != b_fields.sheet or not (set(a.keys) & set(b_fields.keys)):
            return False
        if a.row_start is None or b_fields.row_start is None:
            return True
        # 行端点由 decode 成对校验（同时 null 或同时非空）
        a_end = cast(int, a.row_end)
        b_end = cast(int, b_fields.row_end)
        return a.row_start <= b_end and b_fields.row_start <= a_end
    if isinstance(a, JsonPointerRange):
        return _is_prefix(a, cast(JsonPointerRange, b)) or _is_prefix(cast(JsonPointerRange, b), a)
    return False  # pragma: no cover - 封闭联合已穷尽


def _atom_in_selection(container: RangeAtom, item: ReadAtom) -> bool:
    """读取原子是否被选择原子包含（同轴）（§3.3.1「属于selection」）。"""

    if type(container) is not type(item):
        return False
    if isinstance(container, PagesRange) and isinstance(item, PagesRange):
        return container.start <= item.start and item.end <= container.end
    if isinstance(container, ParagraphsRange) and isinstance(item, ParagraphsRange):
        return container.start <= item.start and item.end <= container.end
    if isinstance(container, CodepointsRange) and isinstance(item, CodepointsRange):
        return container.start <= item.start and item.end <= container.end
    if isinstance(container, SheetsRange) and isinstance(item, SheetsRange):
        return set(item.names) <= set(container.names)
    if isinstance(container, RowsRange) and isinstance(item, RowsRange):
        return container.sheet == item.sheet and container.start <= item.start and item.end <= container.end
    if isinstance(container, FieldsRange) and isinstance(item, FieldsRange):
        if container.sheet != item.sheet or not (set(item.keys) <= set(container.keys)):
            return False
        if container.row_start is None:
            return True
        if item.row_start is None or item.row_end is None or container.row_end is None:
            return False
        return container.row_start <= item.row_start and item.row_end <= container.row_end
    if isinstance(container, JsonPointerRange) and isinstance(item, JsonPointerRange):
        return _is_prefix(container, item)
    return False  # pragma: no cover - 封闭联合已穷尽


def decode_selected_range(value: object, path: str) -> SelectedRange:
    """严格解码 SelectedRange：all/limited 归属、互斥、重叠校验（§3.3.1）。

    - selection=all：read/unread 各数组只能为空或恰一个 all 分型；
    - selection=limited：read/unread 只能列与选择同轴的准确 RangeAtom，
      且逐一被选择包含；read 与 unread 联合互斥（重复/重叠拒绝）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(
        value,
        {"selection", "read_ranges", "unread_ranges", "stats", "limitations", "accepted_limited_version"},
        path,
    )
    selection = decode_selection(value["selection"], f"{path}.selection")
    stats = decode_range_stats(value["stats"], f"{path}.stats")

    accepted = value["accepted_limited_version"]
    if accepted is not None:
        accepted = _require_str(accepted, f"{path}.accepted_limited_version")

    raw_limitations = value["limitations"]
    if not isinstance(raw_limitations, list):
        _fail("invalid_type", f"{path}.limitations")
    limitations = tuple(
        decode_range_limitation(item, f"{path}.limitations[{index}]") for index, item in enumerate(raw_limitations)
    )

    def _decode_atoms(key: str) -> tuple[ReadAtom, ...]:
        raw = value[key]
        if not isinstance(raw, list):
            _fail("invalid_type", f"{path}.{key}")
        if selection.kind == "all":
            # all 独占数组指整源：数组只能为空或恰一个 all 分型（§3.3.1）
            if len(raw) > 1:
                _fail("invalid_tag", f"{path}.{key}")
            if len(raw) == 1:
                atom = decode_range_atom(raw[0], f"{path}.{key}[0]", allow_all=True)
                if not isinstance(atom, AllAtom):
                    _fail("invalid_tag", f"{path}.{key}")
                return (atom,)
            return ()
        atoms = tuple(decode_range_atom(item, f"{path}.{key}[{index}]") for index, item in enumerate(raw))
        for atom in atoms:
            if isinstance(atom, AllAtom):
                _fail("invalid_tag", f"{path}.{key}")
            if not any(_atom_in_selection(sel, atom) for sel in selection.ranges):
                _fail("not_in_selection", f"{path}.{key}")
        return atoms

    read_ranges = _decode_atoms("read_ranges")
    unread_ranges = _decode_atoms("unread_ranges")

    if selection.kind == "limited" and (read_ranges or unread_ranges):
        selection_kind = type(selection.ranges[0])
        for atom in (*read_ranges, *unread_ranges):
            if not isinstance(atom, AllAtom) and not isinstance(atom, selection_kind):
                _fail("kind_mismatch", path)

    combined = (*read_ranges, *unread_ranges)
    for i in range(len(combined)):
        for j in range(i + 1, len(combined)):
            if _atoms_overlap(combined[i], combined[j]):
                _fail("overlap", f"{path}.read_ranges[{i}]")

    return SelectedRange(
        selection=selection,
        read_ranges=read_ranges,
        unread_ranges=unread_ranges,
        stats=stats,
        limitations=limitations,
        accepted_limited_version=accepted,
    )


def decode_provenance_ref(value: object, path: str) -> ProvenanceRef:
    """严格解码 ProvenanceRef：source 必须等于 locator.origin_ref（§3.3.1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(
        value,
        {"source", "locator", "selected_range_digest", "representation_version", "representation_digest"},
        path,
    )
    source = decode_source_version_ref(value["source"], f"{path}.source")
    locator = decode_source_locator(value["locator"], f"{path}.locator")
    if source != locator.origin_ref:
        _fail("origin_mismatch", f"{path}.source")
    return ProvenanceRef(
        source=source,
        locator=locator,
        selected_range_digest=_require_digest(value["selected_range_digest"], f"{path}.selected_range_digest"),
        representation_version=_require_str(value["representation_version"], f"{path}.representation_version"),
        representation_digest=_require_digest(value["representation_digest"], f"{path}.representation_digest"),
    )


def decode_provenance_refs(value: object, path: str) -> tuple[ProvenanceRef, ...]:
    """解码谱系边列表：保序；重复完全相同的边拒绝（§3.3.1）。"""

    if not isinstance(value, list):
        _fail("invalid_type", path)
    refs: list[ProvenanceRef] = []
    for index, item in enumerate(value):
        ref = decode_provenance_ref(item, f"{path}[{index}]")
        if ref in refs:
            _fail("duplicate_edge", f"{path}[{index}]")
        refs.append(ref)
    return tuple(refs)


# ---------------------------------------------------------------------------
# §9.8 R04 v1 编码：资料许可（DataConsent）合同
# ---------------------------------------------------------------------------


class DataConsentState(Enum):
    """资料许可状态（§9.8 唯一 wire 拼写）。"""

    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"
    EXPIRED = "expired"


_UTC_TS_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")

#: DataConsentBinding 全部键（§9.8「v1外层类型与可空矩阵」：无可省略键/额外键）。
_DATA_CONSENT_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "consent_id",
        "consent_revision",
        "state",
        "approver_ref",
        "user_scope",
        "execution_id",
        "round_id",
        "valid_from",
        "expires_at",
        "requires_egress_grant",
        "connection_authorization_ref",
        "connection_revision",
        "security_revision",
        "credential_version",
        "target_fingerprint",
        "purpose",
        "scope_version",
        "source_selections",
        "source_scope_digest",
    }
)

#: connection_authorization_ref 封闭对象键（§9.8 v1 编码局部工程选择）。
_CONNECTION_AUTH_REF_KEYS: Final[frozenset[str]] = frozenset(
    {"connection_id", "target_fingerprint", "purpose", "scope_version", "verified_connection_grant_id"}
)

#: 数据同意源项键（§9.8：不是含斜线的键）。
_DATA_CONSENT_SOURCE_ITEM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_kind",
        "source_id",
        "source_version",
        "source_digest",
        "selected_range",
        "locator",
        "selected_range_digest",
        "representation_version",
        "representation_digest",
        "provenance_refs",
    }
)


def _require_utc_timestamp(value: object, path: str) -> str:
    """非空时刻统一 UTC 微秒 ISO 文本（以 Z 结尾，§9.8）；拒绝其他时区/精度形态。"""

    if not isinstance(value, str) or _UTC_TS_RE.fullmatch(value) is None:
        _fail("invalid_timestamp", path)
    try:
        dt.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        _fail("invalid_timestamp", path)
    return value


def require_utc_timestamp(value: object, field_path: str) -> str:
    """公开入口：DataConsent 时刻字段校验（owner/服务层复用同一 codec 语义）。"""

    return _require_utc_timestamp(value, field_path)


@dataclass(frozen=True, slots=True)
class ConnectionAuthorizationRef:
    """连接授权引用（封闭对象，§9.8）：fingerprint/purpose/scope_version 必须与
    外层绑定一致；``verified_connection_grant_id`` 为原 owner 实际核到的连接
    grant ID 或显式 null，不引用 decision.grant_id，也不从 AttemptPermit 臆取。"""

    connection_id: str
    target_fingerprint: str
    purpose: str
    scope_version: int
    verified_connection_grant_id: str | None


@dataclass(frozen=True, slots=True)
class DataConsentSourceItem:
    """数据同意源项（§9.8）：身份/定位/范围/谱系严格沿 §3.3.1 类型。"""

    source_kind: SourceKind
    source_id: str
    source_version: str
    source_digest: str
    selected_range: SelectedRange
    locator: SourceLocator
    selected_range_digest: str
    representation_version: str
    representation_digest: str
    provenance_refs: tuple[ProvenanceRef, ...]


def decode_data_consent_source_item(value: object, path: str) -> DataConsentSourceItem:
    """严格解码源项（10 键全必填禁额外键，§9.8）。

    公共入口逐值闭合：顶层源身份四值与 ``locator.origin_ref`` 一致；
    ``selected_range`` 与 ``locator`` 冗余承载同一选择（all/limited 不得互当）；
    ``accepted_limited_version`` 只能等于本项 ``source_version``。最后重算
    ``selected_range_digest`` 并与载荷比对（保存/决定重读/每 attempt 核权均重算，
    不信调用方 digest）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DATA_CONSENT_SOURCE_ITEM_KEYS, path)

    locator = decode_source_locator(value["locator"], f"{path}.locator")
    origin = locator.origin_ref
    source_kind = _require_choice(value["source_kind"], SOURCE_KINDS, f"{path}.source_kind", "invalid_source_kind")
    source_id = _require_str(value["source_id"], f"{path}.source_id")
    source_version = _require_str(value["source_version"], f"{path}.source_version")
    source_digest = _require_digest(value["source_digest"], f"{path}.source_digest")
    # 源项顶层身份与 locator.origin_ref 一致（同一受管源版，冗余字段不得分叉）
    if (
        source_kind != origin.source_kind.value
        or source_id != origin.source_id
        or source_version != origin.source_version
        or source_digest != origin.source_digest
    ):
        _fail("identity_mismatch", path)

    selected_range = decode_selected_range(value["selected_range"], f"{path}.selected_range")
    _require_same_selection(selected_range.selection, locator.selection, f"{path}.selected_range.selection")
    accepted = selected_range.accepted_limited_version
    if accepted is not None and accepted != source_version:
        _fail("invalid_accepted_version", f"{path}.selected_range.accepted_limited_version")

    digest = _require_digest(value["selected_range_digest"], f"{path}.selected_range_digest")
    if digest != selected_range_digest(selected_range):
        _fail("digest_mismatch", f"{path}.selected_range_digest")

    return DataConsentSourceItem(
        source_kind=origin.source_kind,
        source_id=origin.source_id,
        source_version=origin.source_version,
        source_digest=origin.source_digest,
        selected_range=selected_range,
        locator=locator,
        selected_range_digest=digest,
        representation_version=_require_str(value["representation_version"], f"{path}.representation_version"),
        representation_digest=_require_digest(value["representation_digest"], f"{path}.representation_digest"),
        provenance_refs=decode_provenance_refs(value["provenance_refs"], f"{path}.provenance_refs"),
    )


def data_consent_source_scope_digest(items: Sequence[DataConsentSourceItem]) -> str:
    """``source_scope_digest``（§9.8）：源项先按各项 CanonicalJson 的 UTF-8 字节
    排序，重复项拒绝（[A,A] 必须拒绝，不先去重后成功），再对
    ``{"schema_version":1,"source_selections":规范源项数组}`` 取 SHA-256。"""

    canonicals = sorted(json_doc(to_wire_dict(item)) for item in items)
    if len(canonicals) != len(set(canonicals)):
        _fail("duplicate_source_item", "$.source_selections")
    payload = {"schema_version": 1, "source_selections": [json.loads(c) for c in canonicals]}
    return sha256_hex(json_doc(payload).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class DataConsentBinding:
    """资料许可绑定（§9.8）：全部 20 键，无可省略键/额外键。

    有效期仅准确轮/用途/目的地内且未过期/撤销，不因「稍后」延期、不跨轮迁移；
    ``expires_at=null`` 表示期限未核定，**不表示永久/不限期**。冻结许可不含
    正文、密钥或凭据。
    """

    schema_version: int
    consent_id: str
    consent_revision: int
    state: DataConsentState
    approver_ref: str | None
    user_scope: str
    execution_id: str
    round_id: str
    valid_from: str | None
    expires_at: str | None
    requires_egress_grant: bool
    connection_authorization_ref: ConnectionAuthorizationRef
    connection_revision: int
    security_revision: int
    credential_version: str | None
    target_fingerprint: str
    purpose: str
    scope_version: int
    source_selections: tuple[DataConsentSourceItem, ...]
    source_scope_digest: str


def _decode_connection_authorization_ref(value: object, path: str) -> ConnectionAuthorizationRef:
    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _CONNECTION_AUTH_REF_KEYS, path)
    grant_id = value["verified_connection_grant_id"]
    if grant_id is not None:
        if not isinstance(grant_id, str):
            _fail("invalid_type", f"{path}.verified_connection_grant_id")
        # 已核到的 grant ID 是非空身份：空串不等于「未核到」，不得当作合法值
        if not grant_id:
            _fail("invalid_identity", f"{path}.verified_connection_grant_id")
    return ConnectionAuthorizationRef(
        connection_id=_require_str(value["connection_id"], f"{path}.connection_id"),
        target_fingerprint=_require_str(value["target_fingerprint"], f"{path}.target_fingerprint"),
        purpose=_require_str(value["purpose"], f"{path}.purpose"),
        scope_version=_require_p(value["scope_version"], f"{path}.scope_version"),
        verified_connection_grant_id=grant_id,
    )


def decode_data_consent_binding(value: object, path: str) -> DataConsentBinding:
    """严格解码 DataConsentBinding（§9.8 v1 编码 + 可空矩阵 + 状态矩阵）。

    重算 ``source_scope_digest`` 并与载荷比对，不信调用方 digest；JSONB 重排键
    后重读保同身份（CanonicalJson 键序无关）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DATA_CONSENT_BINDING_KEYS, path)

    schema_version = value["schema_version"]
    # §9.8：schema_version 仅整数 1（拒 bool；1.0 因 == 1 成立，必须同时拒非 int）
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        _fail("invalid_schema_version", f"{path}.schema_version")

    if not isinstance(value["requires_egress_grant"], bool):
        _fail("invalid_type", f"{path}.requires_egress_grant")

    state_raw = value["state"]
    if not isinstance(state_raw, str) or state_raw not in DataConsentState._value2member_map_:
        _fail("invalid_state", f"{path}.state")
    state = DataConsentState(state_raw)

    approver = value["approver_ref"]
    if approver is not None:
        approver = _require_str(approver, f"{path}.approver_ref")
    valid_from = value["valid_from"]
    if valid_from is not None:
        valid_from = _require_utc_timestamp(valid_from, f"{path}.valid_from")
    expires_at = value["expires_at"]
    if expires_at is not None:
        expires_at = _require_utc_timestamp(expires_at, f"{path}.expires_at")

    # 状态矩阵（§9.8 表）：pending 双 null；approved S+T；终态同空或同非空；
    # expired 的 expires_at 必须非 null。仅固定编码形态，不新开迁移。
    if state is DataConsentState.PENDING:
        if approver is not None or valid_from is not None:
            _fail("state_matrix", f"{path}.approver_ref")
    elif state is DataConsentState.APPROVED:
        if approver is None or valid_from is None:
            _fail("state_matrix", f"{path}.approver_ref")
    else:  # revoked / expired：终态同空或同非空
        if (approver is None) != (valid_from is None):
            _fail("state_matrix", f"{path}.approver_ref")
        if state is DataConsentState.EXPIRED and expires_at is None:
            _fail("state_matrix", f"{path}.expires_at")

    if valid_from is not None and expires_at is not None and valid_from >= expires_at:
        _fail("invalid_period", f"{path}.valid_from")

    credential_version = value["credential_version"]
    if credential_version is not None:
        credential_version = _require_str(credential_version, f"{path}.credential_version")

    connection_ref = _decode_connection_authorization_ref(
        value["connection_authorization_ref"], f"{path}.connection_authorization_ref"
    )
    # 原 owner 计数器边界（§9.8）：security_revision 初始 0、下界 0；null /
    # 负值 / bool 均拒绝 INPUT_INVALID（不套通用 N 可空语义）。
    security_revision = value["security_revision"]
    if isinstance(security_revision, bool) or not isinstance(security_revision, int) or security_revision < 0:
        _fail("invalid_revision", f"{path}.security_revision")
    # 内外一致性（§9.8）：重复的 target_fingerprint/purpose/scope_version 必须等于外层。
    # 外层值先按类型/非空校验，再参与身份比对，避免拿未校验值当身份（嵌套先验后用）。
    target_fingerprint = _require_str(value["target_fingerprint"], f"{path}.target_fingerprint")
    purpose = _require_str(value["purpose"], f"{path}.purpose")
    scope_version = _require_p(value["scope_version"], f"{path}.scope_version")
    if (
        connection_ref.target_fingerprint != target_fingerprint
        or connection_ref.purpose != purpose
        or connection_ref.scope_version != scope_version
    ):
        _fail("identity_mismatch", f"{path}.connection_authorization_ref")

    if not isinstance(value["source_selections"], list):
        _fail("invalid_type", f"{path}.source_selections")
    source_items = tuple(
        decode_data_consent_source_item(item, f"{path}.source_selections[{index}]")
        for index, item in enumerate(value["source_selections"])
    )
    recomputed = data_consent_source_scope_digest(source_items)
    if _require_digest(value["source_scope_digest"], f"{path}.source_scope_digest") != recomputed:
        _fail("digest_mismatch", f"{path}.source_scope_digest")

    return DataConsentBinding(
        schema_version=1,
        consent_id=_require_str(value["consent_id"], f"{path}.consent_id"),
        consent_revision=_require_p(value["consent_revision"], f"{path}.consent_revision"),
        state=state,
        approver_ref=approver,
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        valid_from=valid_from,
        expires_at=expires_at,
        requires_egress_grant=value["requires_egress_grant"],
        connection_authorization_ref=connection_ref,
        connection_revision=_require_p(value["connection_revision"], f"{path}.connection_revision"),
        security_revision=security_revision,
        credential_version=credential_version,
        target_fingerprint=target_fingerprint,
        purpose=purpose,
        scope_version=scope_version,
        source_selections=source_items,
        source_scope_digest=recomputed,
    )


def encode_data_consent_binding(binding: DataConsentBinding) -> dict[str, Any]:
    """DataConsentBinding → wire dict（20 键全量，CanonicalJson 语义承载）。"""

    return cast(dict[str, Any], _to_jsonable(binding, "$"))


def local_target_fingerprint(
    user_scope: str,
    connection_id: str,
    connection_revision: int,
    adapter_kind: str,
) -> str:
    """AR-09 本地目标映射（拟实现合同，§9.8）：``local-model-v1:`` ＋
    ``sha256(CanonicalJson([1,"local-model",user_scope,connection_id,
    connection_revision,adapter_kind]))``。

    仅对已核实际设备内且具 Agent 聊天准入能力的 ``gguf_chat`` 计算；修订为
    拒 bool 的正整数（0/负/布尔拒绝）；未知 kind / 非聊天 ``gguf_sentiment``
    及云端路径拒绝（云端缺 api_base 由原 owner 拒绝，不在此伪造目标）。
    """

    _require_str(user_scope, "$.user_scope")
    _require_str(connection_id, "$.connection_id")
    if isinstance(connection_revision, bool) or not isinstance(connection_revision, int) or connection_revision < 1:
        _fail("invalid_revision", "$.connection_revision")
    if adapter_kind != "gguf_chat":
        _fail("invalid_adapter_kind", "$.adapter_kind")
    canonical = json_doc([1, "local-model", user_scope, connection_id, connection_revision, adapter_kind])
    return f"local-model-v1:{sha256_hex(canonical.encode('utf-8'))}"


# ---------------------------------------------------------------------------
# §4.1/§4.2 服务命令与回执合同（C-02-1）
# ---------------------------------------------------------------------------


#: 命令投递事实（§4.1「保存/投递/消费事实」；§5.1「待投递/已投递/unknown，字段分开」）。
#: wire 拼写为本轮工程冻结（设计未逐枚举值），与 §3.1 CommitState 的
#: not_committed/commit_unknown 命名风格一致；DB 值域 CHECK 待冻结确认后随
#: 后续迁移补齐（见 C-01 检查点⑥）。
class DeliveryState(Enum):
    """命令投递三态：保存成功不等于已投递（INV-06）。"""

    NOT_DELIVERED = "not_delivered"
    DELIVERED = "delivered"
    DELIVERY_UNKNOWN = "delivery_unknown"


#: 命令消费事实（§5.1「消费/冲突/unknown，字段分开」；SQ-03「同命令已消费返回原事实」）。
class ConsumptionState(Enum):
    """命令消费三态：消费与投递分列；冲突不并入消费语义（INV-06/INV-07）。"""

    NOT_CONSUMED = "not_consumed"
    CONSUMED = "consumed"
    CONSUMPTION_UNKNOWN = "consumption_unknown"


class AgentErrorCode(Enum):
    """固定错误码（HANDOFF §4.1，唯一 wire 拼写）。"""

    INPUT_INVALID = "INPUT_INVALID"
    INPUT_CONFLICT = "INPUT_CONFLICT"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    CONFIG_UNAVAILABLE = "CONFIG_UNAVAILABLE"
    CAPABILITY_UNVERIFIED = "CAPABILITY_UNVERIFIED"
    POLICY_DENIED = "POLICY_DENIED"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"
    PERSISTENCE_UNAVAILABLE = "PERSISTENCE_UNAVAILABLE"
    OPERATION_UNKNOWN = "OPERATION_UNKNOWN"
    SOURCE_DISABLED = "SOURCE_DISABLED"
    NATIVE_VERSION_MISMATCH = "NATIVE_VERSION_MISMATCH"
    CANCELLED = "CANCELLED"


class AgentServiceError(Exception):
    """类型化服务错误（§4.2）：消息已脱敏，identity 绑定原身份。

    ``last_verified_at`` 为最后可靠事实时刻（UTC 微秒 Z 文本）或 None。
    广泛 catch 后返回空页被禁止；读取失败必须抛本类型或返回合同内 unknown。
    """

    def __init__(
        self,
        code: AgentErrorCode,
        sanitized_message: str,
        identity: str,
        last_verified_at: str | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {sanitized_message}")
        self.code = code
        self.sanitized_message = sanitized_message
        self.identity = identity
        self.last_verified_at = last_verified_at


@dataclass(frozen=True)
class CommandRef:
    """命令引用（§4.2）：调用方幂等键 + 预期版；target 由各命令类型承载。"""

    user_scope: str
    source_module: str
    business_request_id: str
    #: 调用方命令键（持久 PK 按 §3.3.2 = stable_id("command",[user_scope,
    #: exact_target_id, client_command_id]) 派生，client_command_id 即本字段）。
    command_id: str
    #: 预期 revision（§3.1：目标尚不存在时允许 0）。
    expected_revision: int


_COMMAND_REF_KEYS: Final = frozenset(
    {"user_scope", "source_module", "business_request_id", "command_id", "expected_revision"}
)


def decode_command_ref(value: object, path: str = "$.command_ref") -> CommandRef:
    """严格解码 CommandRef（§3.3.1 语法基线：全键必填、禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _COMMAND_REF_KEYS, path)
    revision = value["expected_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        _fail("invalid_revision", f"{path}.expected_revision")
    return CommandRef(
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        source_module=_require_str(value["source_module"], f"{path}.source_module"),
        business_request_id=_require_str(value["business_request_id"], f"{path}.business_request_id"),
        command_id=_require_str(value["command_id"], f"{path}.command_id"),
        expected_revision=revision,
    )


def encode_command_ref(ref: CommandRef) -> dict[str, Any]:
    """CommandRef → wire dict（5 键全量）。"""

    _require_str(ref.user_scope, "$.user_scope")
    _require_str(ref.source_module, "$.source_module")
    _require_str(ref.business_request_id, "$.business_request_id")
    _require_str(ref.command_id, "$.command_id")
    if (
        isinstance(ref.expected_revision, bool)
        or not isinstance(ref.expected_revision, int)
        or ref.expected_revision < 0
    ):
        _fail("invalid_revision", "$.expected_revision")
    return {
        "user_scope": ref.user_scope,
        "source_module": ref.source_module,
        "business_request_id": ref.business_request_id,
        "command_id": ref.command_id,
        "expected_revision": ref.expected_revision,
    }


@dataclass(frozen=True)
class CommandReceipt:
    """命令回执（§4.1）：保存/投递/消费事实分列，不把保存成功显示成已生效。

    ``error_code`` 非空时表示 conflict/unknown 细节；``next_action`` 为可核
    下一步说明（如「查 read_command(command_ref)」），非空 str 或 None。
    """

    command: CommandRef
    target_id: str
    current_revision: int
    commit_state: CommitState
    delivery_state: DeliveryState
    consumption_state: ConsumptionState
    updated_at: str
    error_code: AgentErrorCode | None
    next_action: str | None


_COMMAND_RECEIPT_KEYS: Final = frozenset(
    {
        "command",
        "target_id",
        "current_revision",
        "commit_state",
        "delivery_state",
        "consumption_state",
        "updated_at",
        "error_code",
        "next_action",
    }
)


def decode_command_receipt(value: object, path: str = "$.command_receipt") -> CommandReceipt:
    """严格解码 CommandReceipt（read_command 查回原事实，§4.2）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _COMMAND_RECEIPT_KEYS, path)
    commit_raw = value["commit_state"]
    delivery_raw = value["delivery_state"]
    consumption_raw = value["consumption_state"]
    try:
        commit_state = CommitState(commit_raw)
    except ValueError:
        _fail("invalid_commit_state", f"{path}.commit_state")
    try:
        delivery_state = DeliveryState(delivery_raw)
    except ValueError:
        _fail("invalid_delivery_state", f"{path}.delivery_state")
    try:
        consumption_state = ConsumptionState(consumption_raw)
    except ValueError:
        _fail("invalid_consumption_state", f"{path}.consumption_state")
    error_raw = value["error_code"]
    if error_raw is None:
        error_code = None
    else:
        try:
            error_code = AgentErrorCode(error_raw)
        except ValueError:
            _fail("invalid_error_code", f"{path}.error_code")
    next_raw = value["next_action"]
    if next_raw is not None:
        _require_str(next_raw, f"{path}.next_action")
    revision_raw = value["current_revision"]
    if isinstance(revision_raw, bool) or not isinstance(revision_raw, int) or revision_raw < 0:
        _fail("invalid_revision", f"{path}.current_revision")
    return CommandReceipt(
        command=decode_command_ref(value["command"], f"{path}.command"),
        target_id=_require_str(value["target_id"], f"{path}.target_id"),
        current_revision=revision_raw,
        commit_state=commit_state,
        delivery_state=delivery_state,
        consumption_state=consumption_state,
        updated_at=require_utc_timestamp(value["updated_at"], f"{path}.updated_at"),
        error_code=error_code,
        next_action=next_raw,
    )


def encode_command_receipt(receipt: CommandReceipt) -> dict[str, Any]:
    """CommandReceipt → wire dict（9 键全量）。"""

    return {
        "command": encode_command_ref(receipt.command),
        "target_id": _require_str(receipt.target_id, "$.target_id"),
        "current_revision": receipt.current_revision,
        "commit_state": receipt.commit_state.value,
        "delivery_state": receipt.delivery_state.value,
        "consumption_state": receipt.consumption_state.value,
        "updated_at": require_utc_timestamp(receipt.updated_at, "$.updated_at"),
        "error_code": None if receipt.error_code is None else receipt.error_code.value,
        "next_action": receipt.next_action,
    }


@dataclass(frozen=True)
class SubmissionReceipt:
    """受理回执（SQ-01/§3.3.2）：同命令重传返回原回执，不新建研究。

    ``commit_state`` 为受理事务三态；冲突走 INPUT_CONFLICT 错误路径不构成本
    回执。``intent_digest`` 为冻结 manifest 规范摘要，受理时点不变（INV-04）。
    """

    command: CommandRef
    command_record_id: str
    session_id: str
    execution_id: str
    workflow_id: str
    intent_digest: str
    commit_state: CommitState
    verified_at: str
    next_action: str | None


_SUBMISSION_RECEIPT_KEYS: Final = frozenset(
    {
        "command",
        "command_record_id",
        "session_id",
        "execution_id",
        "workflow_id",
        "intent_digest",
        "commit_state",
        "verified_at",
        "next_action",
    }
)


def decode_submission_receipt(value: object, path: str = "$.submission_receipt") -> SubmissionReceipt:
    """严格解码 SubmissionReceipt（丢回执/重启后查原回执，§3.3.2）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SUBMISSION_RECEIPT_KEYS, path)
    commit_raw = value["commit_state"]
    try:
        commit_state = CommitState(commit_raw)
    except ValueError:
        _fail("invalid_commit_state", f"{path}.commit_state")
    next_raw = value["next_action"]
    if next_raw is not None:
        _require_str(next_raw, f"{path}.next_action")
    return SubmissionReceipt(
        command=decode_command_ref(value["command"], f"{path}.command"),
        command_record_id=_require_str(value["command_record_id"], f"{path}.command_record_id"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        workflow_id=_require_str(value["workflow_id"], f"{path}.workflow_id"),
        intent_digest=_require_digest(value["intent_digest"], f"{path}.intent_digest"),
        commit_state=commit_state,
        verified_at=require_utc_timestamp(value["verified_at"], f"{path}.verified_at"),
        next_action=next_raw,
    )


def encode_submission_receipt(receipt: SubmissionReceipt) -> dict[str, Any]:
    """SubmissionReceipt → wire dict（9 键全量）。"""

    return {
        "command": encode_command_ref(receipt.command),
        "command_record_id": _require_str(receipt.command_record_id, "$.command_record_id"),
        "session_id": _require_str(receipt.session_id, "$.session_id"),
        "execution_id": _require_str(receipt.execution_id, "$.execution_id"),
        "workflow_id": _require_str(receipt.workflow_id, "$.workflow_id"),
        "intent_digest": _require_digest(receipt.intent_digest, "$.intent_digest"),
        "commit_state": receipt.commit_state.value,
        "verified_at": require_utc_timestamp(receipt.verified_at, "$.verified_at"),
        "next_action": receipt.next_action,
    }


# ---------------------------------------------------------------------------
# §3.3.2 受理身份派生与受理输入（C-02-2）
# ---------------------------------------------------------------------------


#: 谱系边关系（§9.10 relation_kind；本轮工程冻结首批值——设计未逐枚举）。
class SourceRelationKind(Enum):
    """源-消费者谱系边语义：origin=源→其 originating message；accepted_input=源→受理输入版；
    draft_reference=源→当前会话草稿引用（F-03-4，随草稿保存替换为当前集合）；
    role_skill_binding=Skill 资产→引用它的角色版本（E-03-4，引用阻删）。"""

    ORIGIN = "origin"
    ACCEPTED_INPUT = "accepted_input"
    DRAFT_REFERENCE = "draft_reference"
    ROLE_SKILL_BINDING = "role_skill_binding"


#: 可靠事件载荷结构标识（§4.4 payload_kind；本轮工程冻结首批值——设计未逐枚举）。
class EventPayloadKind(Enum):
    """public_message_saved / input_bound 事件载荷 v1（§4.4/§9.6）。"""

    PUBLIC_MESSAGE = "public_message"
    INPUT_BINDING = "input_binding"


@dataclass(frozen=True)
class AcceptRequest:
    """受理输入（SQ-01/§3.3.2/§3.4）：受理时点冻结（INV-04）。

    ``text`` 为用户消息正文（非空，不 trim/改 Unicode）；``manifest_payload``
    为 FrozenManifest 规范 JSON（规范字节校验后参与完整意图摘要，R2-I14：正文/
    模式/用途/工作流/来源显示名/比较组路引用同入摘要）；
    ``source_display_name`` 由服务层决定（i18n 文案），DAO 不猜显示名。
    """

    user_scope: str
    session_id: str
    source_module: str
    business_request_id: str
    client_command_id: str
    #: 目标会话草稿预期版（§3.1：0=目标尚不存在）。受理事务持会话行锁与当前
    #: ``draft_revision`` 作 CAS：用户提交时看到的稿版不是最新即拒绝（R2-I15）；
    #: ``0`` 仅表示「旧客户端无草稿基线」，只对从未保存过草稿的会话成立。
    expected_revision: int
    node_id: str
    workflow_id: str
    mode: Mode
    purpose: Purpose
    text: str
    manifest_payload: CanonicalJson
    source_display_name: str
    #: F37 多模型比较组/路引用（既有列，无新表列）：同组各路共用一个 manifest，
    #: ``comparison_group_ref`` 关联共同冻结输入，``comparison_lane_ref`` 为本路
    #: 稳定身份（按所选连接身份派生，不随排队时点变化）。
    comparison_group_ref: str | None = None
    comparison_lane_ref: str | None = None
    #: 修订声明（R2-I25）：本次受理是对本会话内既有消息的修订。``revise`` 命令
    #: 只准备新稿、不改会话因果头；该声明随新意图受理进入同一事务，受理成功时
    #: 才原子切换 ``AgentSession.causal_head_ref``（源消息之前最近一条，或显式
    #: 截点；源为会话首条时切换为 ``None``/空前缀）。撤销编辑（不发此声明）不
    #: 改变既有研究关联。
    revise_source_message_id: str | None = None
    revise_causal_cutoff_message_id: str | None = None


@dataclass(frozen=True)
class AcceptIdentity:
    """受理身份派生结果（§3.3.2 稳定身份规则，纯函数）。"""

    command_record_id: str
    message_id: str
    source_id: str
    source_version: str
    source_digest: str
    intent_digest: str


#: 受理意图摘要规范版本（R2-I14）：v1 = sha256(manifest 字节)，只覆盖冻结 manifest，
#: 同 intent 换正文/模式/用途/工作流/来源显示名/比较组路引用会得到同一摘要而被误判为
#: 重放；v2 覆盖全部受理输入，旧行由 :func:`legacy_accept_intent_digest` 识别。
ACCEPT_INTENT_DIGEST_VERSION: Final = 2

#: 冻结输入段（R2-I15）：比较组 manifest 的 ``shared`` 块必须齐备的冻结范围——
#: 正文/模式/用途/cutoff、资料清单、允许历史、角色 Prompt/Skill 引用、MCP 范围与
#: 输出要求。缺项即「冻结输入不完整」，受理必须拒绝而不是按缺省放行（R21）。
FROZEN_SHARED_INPUT_KEYS: Final = frozenset(
    {
        "text",
        "mode",
        "purpose",
        "cutoff",
        "source_refs",
        "visible_history_message_ids",
        "visible_history_artifact_refs",
        "role_prompt_refs",
        "skill_refs",
        "mcp_scope_digest",
        "output_requirements",
    }
)

#: 冻结 manifest 可重复声明的受理身份字段（存在即须与本次请求一致）。
_FROZEN_MANIFEST_REQUEST_FIELDS: Final = (
    "user_scope",
    "session_id",
    "source_module",
    "business_request_id",
    "node_id",
    "workflow_id",
    "text",
    "mode",
    "purpose",
)


def _require_canonical_manifest(payload: object) -> dict[str, Any]:
    """校验冻结 manifest 为规范 JSON 对象（R2-I14：manifest 内容本身必须校验）。

    非规范字节（键序/空白/转义）、重复键与非对象一律拒绝：摘要按规范字节参与计算，
    若放行非规范输入，同一内容会有多个身份，「同输入」判定随字节表现漂移。
    """

    if not isinstance(payload, str) or payload == "":
        _fail("invalid_value", "$.manifest_payload")
    parsed = decode_json_doc(payload)
    if not isinstance(parsed, dict):
        _fail("invalid_type", "$.manifest_payload")
    if json_doc(parsed) != payload:
        _fail("non_canonical_json", "$.manifest_payload")
    return parsed


def accept_intent_digest(request: AcceptRequest) -> str:
    """服务端按实际受理内容计算的完整意图摘要（R2-I14）。

    覆盖 target（session_id）、node/workflow 身份、模式/用途、正文、冻结 manifest
    （规范字节，已校验）、来源显示名、比较组/路引用、期望版本与修订声明（R2-I25）：
    同一意图键下任一字段变化都必须得到不同摘要（→ ``INPUT_CONFLICT``），只有完整
    输入相同才是重传。命令键（``client_command_id``）不参与：重传允许换命令键，
    由命令行摘要单独核对。

    修订声明仅在声明时进入摘要：未声明修订的受理输入保持既有 v2 摘要素材，
    历史意图行（重放）不被本次扩展重分类（I14 兼容约束）。
    """

    _require_canonical_manifest(request.manifest_payload)
    payload: dict[str, Any] = {
        "schema_version": ACCEPT_INTENT_DIGEST_VERSION,
        "kind": "accept_intent",
        "session_id": request.session_id,
        "source_module": request.source_module,
        "business_request_id": request.business_request_id,
        "node_id": request.node_id,
        "workflow_id": request.workflow_id,
        "mode": request.mode.value,
        "purpose": request.purpose.value,
        "text": request.text,
        "manifest": request.manifest_payload,
        "source_display_name": request.source_display_name,
        "comparison_group_ref": request.comparison_group_ref,
        "comparison_lane_ref": request.comparison_lane_ref,
        "expected_revision": request.expected_revision,
    }
    if request.revise_source_message_id is not None:
        payload["revise"] = {
            "source_message_id": request.revise_source_message_id,
            "causal_cutoff_message_id": request.revise_causal_cutoff_message_id,
        }
    return sha256_hex(json_doc(payload))


def frozen_manifest_inconsistency(request: AcceptRequest) -> str | None:
    """冻结 manifest 与本次受理输入不一致的字段路径（R2-I15；一致返回 ``None``）。

    受理把 manifest 视为「本次明确发送的完整冻结范围」：它若重复声明请求身份
    （用户域/会话/来源/意图键/node/workflow/正文/模式/用途），声明值必须与本请求
    完全相等；否则同一份冻结输入会与实际受理正文/模式互相矛盾，读回与恢复会把
    「另一份范围」当成本次依据。比较组 manifest 的 ``shared`` 块还须齐备
    :data:`FROZEN_SHARED_INPUT_KEYS` 全部冻结段（角色/模型/资料/允许历史/输出要求）
    并与其 ``text``/``mode``/``purpose`` 一致——缺段或矛盾都不得静默放行（R21）。

    前置：manifest 已由 :func:`_require_canonical_manifest` 校验为规范 JSON 对象。
    """

    parsed = _require_canonical_manifest(request.manifest_payload)
    expected_values = {
        "user_scope": request.user_scope,
        "session_id": request.session_id,
        "source_module": request.source_module,
        "business_request_id": request.business_request_id,
        "node_id": request.node_id,
        "workflow_id": request.workflow_id,
        "text": request.text,
        "mode": request.mode.value,
        "purpose": request.purpose.value,
    }
    for field in _FROZEN_MANIFEST_REQUEST_FIELDS:
        if field in parsed and parsed[field] != expected_values[field]:
            return f"$.manifest_payload.{field}"
    shared = parsed.get("shared")
    if shared is None:
        return None
    if not isinstance(shared, dict):
        return "$.manifest_payload.shared"
    missing = sorted(FROZEN_SHARED_INPUT_KEYS.difference(shared))
    if missing:
        return f"$.manifest_payload.shared.{missing[0]}"
    for field in ("text", "mode", "purpose"):
        if shared[field] != expected_values[field]:
            return f"$.manifest_payload.shared.{field}"
    return None


def legacy_accept_intent_digest(manifest_payload: str) -> str:
    """旧版意图摘要（仅 manifest 字节）：只用于读取历史行，禁止用于新写入。"""

    return sha256_hex(manifest_payload)


def derive_accept_identity(request: AcceptRequest) -> AcceptIdentity:
    """按 §3.3.2 派生受理稳定身份并校验输入（不 trim/改 Unicode）。

    - ``command_record_id = stable_id("command",[user_scope,exact_target_id,
      client_command_id])``，exact_target_id 即目标 session_id；
    - ``message_id = stable_id("user_message",[user_scope,session_id,
      command_record_id])``，message_version="1"；
    - ``source_id = stable_id("message_source",[user_scope,session_id,
      message_id])``，source_version="1"；
    - ``source_digest = sha256(text UTF-8)``，``intent_digest =
      accept_intent_digest(request)``（完整受理输入摘要，R2-I14；不再是仅
      manifest 字节）。
    """

    user_scope = _require_str(request.user_scope, "$.user_scope")
    session_id = _require_str(request.session_id, "$.session_id")
    _require_str(request.source_module, "$.source_module")
    _require_str(request.business_request_id, "$.business_request_id")
    _require_str(request.client_command_id, "$.client_command_id")
    if (
        isinstance(request.expected_revision, bool)
        or not isinstance(request.expected_revision, int)
        or request.expected_revision < 0
    ):
        _fail("invalid_revision", "$.expected_revision")
    _require_str(request.node_id, "$.node_id")
    _require_str(request.workflow_id, "$.workflow_id")
    if not isinstance(request.mode, Mode):
        _fail("invalid_tag", "$.mode")
    if not isinstance(request.purpose, Purpose):
        _fail("invalid_tag", "$.purpose")
    if not isinstance(request.text, str) or request.text == "":
        _fail("invalid_value", "$.text")
    _require_str(request.source_display_name, "$.source_display_name")
    # F37：组/路引用同给同缺（孤立引用无法判定共同输入或本路身份）。
    if (request.comparison_group_ref is None) != (request.comparison_lane_ref is None):
        _fail("invalid_comparison_refs", "$.comparison_group_ref")
    if request.comparison_group_ref is not None:
        _require_str(request.comparison_group_ref, "$.comparison_group_ref")
        _require_str(request.comparison_lane_ref, "$.comparison_lane_ref")
    # R2-I25：修订声明成对约束——截点必须随源消息；截点等于源消息即「未修订」。
    if request.revise_causal_cutoff_message_id is not None and request.revise_source_message_id is None:
        _fail("missing_field", "$.revise_source_message_id")
    if request.revise_source_message_id is not None:
        _require_str(request.revise_source_message_id, "$.revise_source_message_id")
        if request.revise_causal_cutoff_message_id is not None:
            _require_str(request.revise_causal_cutoff_message_id, "$.revise_causal_cutoff_message_id")
            if request.revise_causal_cutoff_message_id == request.revise_source_message_id:
                _fail("invalid_value", "$.revise_causal_cutoff_message_id")

    command_record_id = stable_id("command", [user_scope, session_id, request.client_command_id])
    message_id = stable_id("user_message", [user_scope, session_id, command_record_id])
    source_id = stable_id("message_source", [user_scope, session_id, message_id])
    return AcceptIdentity(
        command_record_id=command_record_id,
        message_id=message_id,
        source_id=source_id,
        source_version="1",
        source_digest=sha256_hex(request.text),
        intent_digest=accept_intent_digest(request),
    )


def derive_round_id(*, user_scope: str, execution_id: str, workflow_id: str) -> str:
    """轮次稳定身份（§9.5）：受理派发与 ``bind_actual_input`` 必须用同一派生。

    同一持久 workflow 身份只对应一个轮次 ID，故首派发与新轮隔离都不靠调用顺序。
    """

    return stable_id(
        "round",
        [
            _require_str(user_scope, "$.user_scope"),
            _require_str(execution_id, "$.execution_id"),
            _require_str(workflow_id, "$.workflow_id"),
        ],
    )


# ---------------------------------------------------------------------------
# §9.13 DAO 事务输入/事实类型（C-02-3）
# ---------------------------------------------------------------------------


#: 精确工具许可状态（§9.8 grant_state；本轮工程冻结 wire 值 granted/claimed/
#: revoked——设计未逐枚举，见 Plans.md §A7 C-02-3 裁决⑦）。
class ToolGrantState(Enum):
    """精确工具许可状态：撤销先胜零新派发；派发先胜保真实在途（§5.4 第 6 条）。"""

    GRANTED = "granted"
    CLAIMED = "claimed"
    REVOKED = "revoked"


@dataclass(frozen=True)
class BindActualInputRequest:
    """首派发实际输入绑定（§9.13 bind_actual_input；SQ-02 W 首步调用）。

    ``frozen_manifest`` / ``actual_input`` 为 CanonicalJson，摘要由其规范字节
    单一事实源派生（``sha256``）；``workflow_id`` 为幂等重放键（§9.5 UNIQUE，
    重放需 execution/node/代码版/epoch 完整身份相等，检视 D03）。
    ``run_epoch`` 下限 1（与 ``decode_snapshot_identity`` 一致）。

    ``expected_input_revision``（R2-I16）为调用方构建冻结输入时所依据的有效输入
    版（受理=1，每次排队修订 +1）：DAO 持 execution 行锁把它与最新受理/修订单
    的 ``input_revision`` 比较，不等即 ``REVISION_CONFLICT``——排队修订先胜时
    基于旧版的冻结载荷不得被绑定成新版。回执中的 ``input_revision`` 仍由 DAO
    从权威命令派生，调用方不得自行声明已绑定版本（检视 D03）。
    """

    user_scope: str
    session_id: str
    execution_id: str
    node_id: str
    workflow_id: str
    workflow_code_version: str
    run_epoch: int
    expected_input_revision: int
    frozen_manifest: CanonicalJson
    actual_input: CanonicalJson


@dataclass(frozen=True)
class BindActualInputReceipt:
    """实际输入绑定事实（§9.5/§9.6）：轮次、双摘要与可靠事件序号。"""

    round_id: str
    round_number: int
    input_revision: int
    frozen_digest: str
    actual_digest: str
    execution_seq: int
    session_seq: int


@dataclass(frozen=True)
class SaveAnswerRequest:
    """答复保存输入（SQ-03「answer(original command)」；§5.3 答复线性化点）。

    ``expected_decision_revision`` 为决定行预期版（§9.2 通用 revision 列，
    0 表示目标决定行按调用方契约不允许——答复目标必须已存在，DAO 校验 ≥1）；
    ``answer_payload`` 为 CanonicalJson，answer digest 由规范字节派生。

    ``expected_execution_id`` / ``expected_round_id``（R2-I04）为调用方作答时
    看到的执行/轮次归属：DAO 在决定行锁内与权威行比对，不等即拒绝，证明提交的
    是「哪一轮研究提出的哪个决定」，而非仅按 decision_id 猜归属。
    """

    user_scope: str
    decision_id: str
    client_command_id: str
    expected_decision_revision: int
    expected_execution_id: str
    expected_round_id: str
    answer_payload: CanonicalJson


@dataclass(frozen=True)
class AnswerReceipt:
    """答复保存事实（INV-06）：保存成功不等于已投递或已消费，三态分列。"""

    decision_id: str
    command_record_id: str
    decision_revision: int
    delivery_state: DeliveryState
    consumption_state: ConsumptionState
    answer_digest: str
    verified_at: str


@dataclass(frozen=True)
class MarkAnswerDeliveredRequest:
    """答复投递核定输入（§5.3：同 command_id 幂等原生 send 后核定）。"""

    user_scope: str
    command_record_id: str


@dataclass(frozen=True)
class DeliveryFact:
    """投递核定事实（INV-06）：``verified_at`` 为原事实核定时刻。"""

    command_record_id: str
    delivery_state: DeliveryState
    verified_at: str


@dataclass(frozen=True)
class ConsumeAnswerRequest:
    """答复消费输入（§9.13 consume_answer：W 恢复先查原事实）。"""

    user_scope: str
    command_record_id: str


@dataclass(frozen=True)
class ConsumptionFact:
    """消费事实（SQ-03/§9.13）：已消费返回原事实，同命令同输入幂等重放。"""

    command_record_id: str
    decision_id: str
    consumption_state: ConsumptionState
    consumed_at: str
    answer_consumed_at: str


@dataclass(frozen=True)
class GrantRevisionRequest:
    """精确许可 CAS 输入（§5.4 第 6 条：撤销和派发以同 revision 单胜者）。

    claim_tool_dispatch / revoke_tool_grant 共用：``expected_grant_revision``
    为调用方持柄的许可版（claim 预期 granted；revoke 预期 granted 或 claimed）。

    ``operation_id`` 为派发认领的准确 operation 身份（检视 D07）：claim 必须
    提供并绑定到决定行 ``dispatch_operation_id``；revoke 不绑定 operation，
    该字段被忽略。
    """

    user_scope: str
    decision_id: str
    expected_grant_revision: int
    operation_id: str | None = None


@dataclass(frozen=True)
class ToolGrantFact:
    """许可 CAS 结果事实（§9.13）：胜者/重放返回当前事实，败者走 REVISION_CONFLICT。"""

    decision_id: str
    grant_id: str
    grant_revision: int
    grant_state: ToolGrantState


@dataclass(frozen=True)
class ReviseQueuedRequest:
    """队列修订输入（§9.13 revise_queued / SQ-07 排队期修订）。

    保 intent/execution/队列位；新输入版（input_revision）由既有命令版派生
    （受理=1，首次修订=2）。``text`` 为修订消息正文（非空），``manifest_payload``
    为新冻结 manifest 规范 JSON。
    """

    user_scope: str
    session_id: str
    execution_id: str
    client_command_id: str
    expected_execution_revision: int
    node_id: str
    text: str
    manifest_payload: CanonicalJson
    source_display_name: str


@dataclass(frozen=True)
class RevisionReceipt:
    """队列修订事实（§9.13）：M2/S2 新输入版与 revision CAS 结果。

    ``source_version`` 为新源自身不可变版（恒 "1"，AR-07）；本次执行输入版见
    ``input_revision``，不得把输入版写进源自身版（检视 D13）。
    """

    execution_id: str
    command_record_id: str
    message_id: str
    source_id: str
    source_version: str
    input_digest: str
    input_revision: int
    execution_revision: int
    session_seq: int


#: 命令冻结输入摘要的规范版本（检视 D05：旧摘要只覆盖正文，版本化区分新旧）。
FROZEN_COMMAND_INPUT_DIGEST_VERSION: Final = 1


def frozen_command_input_digest(
    command_kind: str,
    text: str,
    manifest_payload: CanonicalJson,
    *,
    expected_revision: int,
    node_id: str,
    revise_source_message_id: str | None = None,
    revise_causal_cutoff_message_id: str | None = None,
) -> str:
    """命令冻结输入的完整规范摘要（受理/修订共用的唯一构造入口，检视 D05）。

    覆盖正文、冻结 manifest（契约要求 CanonicalJson，按规范字节参与摘要，与
    ``intent_digest`` 同源，故键序/数值表示不同的非规范字节两侧一致判为异输入）
    、命令级期望版本（受理 expected_revision / 修订 expected_execution_revision）
    与 node 身份，并带 ``command_kind`` 区分受理与修订；同键同以上各项才算重放。
    受理的修订声明（R2-I25）同样进入摘要（声明才纳入，未声明保持既有 v1 形态，
    历史命令行重放不被重分类）。旧行摘要由 :func:`legacy_command_input_digest`
    识别，不得用于新写入。
    """

    payload: dict[str, Any] = {
        "schema_version": FROZEN_COMMAND_INPUT_DIGEST_VERSION,
        "kind": command_kind,
        "text": text,
        "manifest": manifest_payload,
        "expected_revision": expected_revision,
        "node_id": node_id,
    }
    if revise_source_message_id is not None:
        payload["revise"] = {
            "source_message_id": revise_source_message_id,
            "causal_cutoff_message_id": revise_causal_cutoff_message_id,
        }
    return sha256_hex(json_doc(payload))


def legacy_command_input_digest(text: str) -> str:
    """旧版命令输入摘要（仅正文）：只用于读取历史行的兼容判定，禁止用于新写入。"""

    return sha256_hex(text)


# ---------------------------------------------------------------------------
# §9.8 R04 owner 命令/核权合同（C-02-4a）
# ---------------------------------------------------------------------------


class DataAttemptOutcome(Enum):
    """资料核权结果（§10.2：allow/deny/unknown 三值分列；unknown 不当 allow）。"""

    ALLOW = "allow"
    DENY = "deny"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DataConsentCommand:
    """资料许可保存命令（§10.2 R04 拟扩展）：携 CommandRef、准确轮身份、完整绑定。

    ``CommandRef.expected_revision`` 即 expected_consent_revision（§3.1：0=目标
    尚不存在→建新决定行；≥1=以该版为 CAS 基准更新）。``consent_id`` 即决定行
    decision_id（§9.8），由调用方以稳定身份派生（如 stable_id），owner 合同只
    要求非空稳定身份。``binding`` 为完整 v1 绑定（20 键），其 consent_id/
    user_scope/execution_id/round_id 须与命令身份一致；建行时 consent_revision=1，
    更新时 =expected+1（§9.8 重复字段 consent_revision 与决定行 revision 同事务一致）。
    """

    command: CommandRef
    consent_id: str
    session_id: str
    execution_id: str
    round_id: str
    node_id: str
    binding: DataConsentBinding


@dataclass(frozen=True)
class DataConsentRevokeCommand:
    """资料许可撤销命令（§10.2）：携原 consent_id、预期版及命令身份。

    ``CommandRef.expected_revision`` 即预期 consent revision（≥1，撤销只针对
    已存在决定行）；撤销立即阻止新许可（先胜零新出站），已终态（revoked/
    expired）不可再撤销。
    """

    command: CommandRef
    consent_id: str


@dataclass(frozen=True)
class DataConsentCommandIdentity:
    """保存/撤销命令稳定身份与输入摘要（命令摘要覆盖完整版本化输入，§9.8）。"""

    command_record_id: str
    input_digest: str


#: 资料许可命令摘要规范版本（I35）：v1 只覆盖命令键与绑定，漏 session/node/
#: module/业务身份；v2 覆盖全部参与判定的版本化输入，旧行经 legacy 摘要区分。
DATA_CONSENT_COMMAND_INPUT_DIGEST_VERSION: Final = 2


class DataConsentDigestMatch(Enum):
    """库中命令摘要与当前输入的关系（I35：历史摘要不得静默当等价）。"""

    CURRENT = "current"
    LEGACY = "legacy"
    MISMATCH = "mismatch"


def classify_data_consent_input_digest(stored: str, *, current: str, legacy: str) -> DataConsentDigestMatch:
    """三分类判定库中摘要：``CURRENT`` / ``LEGACY`` / ``MISMATCH``（I35）。

    历史 v1 摘要只覆盖命令键与绑定，无法证明 session/node/module/业务身份
    相同，调用方必须拒绝重放而非当作等价；仅 ``CURRENT`` 可查回原回执。
    """

    if stored == current:
        return DataConsentDigestMatch.CURRENT
    if stored == legacy:
        return DataConsentDigestMatch.LEGACY
    return DataConsentDigestMatch.MISMATCH


def _check_command_ref(ref: CommandRef) -> None:
    """CommandRef 入口校验（§3.3.1 语法基线）。"""

    _require_str(ref.user_scope, "$.command.user_scope")
    _require_str(ref.source_module, "$.command.source_module")
    _require_str(ref.business_request_id, "$.command.business_request_id")
    _require_str(ref.command_id, "$.command.command_id")
    if (
        isinstance(ref.expected_revision, bool)
        or not isinstance(ref.expected_revision, int)
        or ref.expected_revision < 0
    ):
        _fail("invalid_revision", "$.command.expected_revision")


def derive_data_consent_command_identity(command: DataConsentCommand) -> DataConsentCommandIdentity:
    """校验保存命令并派生命令身份/输入摘要（§3.3.2 命令键 + §9.8 命令摘要）。

    摘要覆盖**完整版本化输入**（I35）：用户域、来源模块、业务请求身份、命令
    身份、consent ID、目标 session/execution/round/node 及完整绑定；任一项不同
    即异输入，换 session/node/module/业务身份不得误判为同一幂等请求。
    ``binding.consent_id`` 必须等于命令 ``consent_id``（身份不得跨目标串用）。
    """

    _check_command_ref(command.command)
    _require_str(command.consent_id, "$.consent_id")
    _require_str(command.session_id, "$.session_id")
    _require_str(command.execution_id, "$.execution_id")
    _require_str(command.round_id, "$.round_id")
    _require_str(command.node_id, "$.node_id")
    binding = command.binding
    if not isinstance(binding, DataConsentBinding):
        _fail("invalid_type", "$.binding")
    if binding.consent_id != command.consent_id:
        _fail("identity_mismatch", "$.binding.consent_id")
    if binding.user_scope != command.command.user_scope:
        _fail("identity_mismatch", "$.binding.user_scope")
    if binding.execution_id != command.execution_id or binding.round_id != command.round_id:
        _fail("identity_mismatch", "$.binding.execution_id")
    expected = command.command.expected_revision
    if expected == 0:
        if binding.consent_revision != 1:
            _fail("invalid_revision", "$.binding.consent_revision")
    elif binding.consent_revision != expected + 1:
        _fail("invalid_revision", "$.binding.consent_revision")
    command_record_id = stable_id(
        "command", [command.command.user_scope, command.consent_id, command.command.command_id]
    )
    canonical = json_doc(
        {
            "schema_version": DATA_CONSENT_COMMAND_INPUT_DIGEST_VERSION,
            "action": "save",
            "user_scope": command.command.user_scope,
            "source_module": command.command.source_module,
            "business_request_id": command.command.business_request_id,
            "command_id": command.command.command_id,
            "consent_id": command.consent_id,
            "expected_consent_revision": expected,
            "session_id": command.session_id,
            "execution_id": command.execution_id,
            "round_id": command.round_id,
            "node_id": command.node_id,
            "binding": encode_data_consent_binding(binding),
        }
    )
    return DataConsentCommandIdentity(command_record_id=command_record_id, input_digest=sha256_hex(canonical))


def legacy_data_consent_save_input_digest(command: DataConsentCommand) -> str:
    """历史 v1 保存摘要（仅命令键/预期版/绑定）：只用于识别旧行，禁止新写入。"""

    return sha256_hex(
        json_doc(
            {
                "schema_version": 1,
                "action": "save",
                "command_id": command.command.command_id,
                "consent_id": command.consent_id,
                "expected_consent_revision": command.command.expected_revision,
                "binding": encode_data_consent_binding(command.binding),
            }
        )
    )


def derive_data_consent_revoke_identity(command: DataConsentRevokeCommand) -> DataConsentCommandIdentity:
    """校验撤销命令并派生命令身份/输入摘要（预期版参与摘要，§9.8）。

    摘要同样覆盖完整版本化输入（I35）：用户域/来源模块/业务请求身份/命令
    身份/consent ID/预期版，换业务身份或模块不得误判为同一幂等请求。
    """

    _check_command_ref(command.command)
    _require_str(command.consent_id, "$.consent_id")
    if command.command.expected_revision < 1:
        _fail("invalid_revision", "$.command.expected_revision")
    command_record_id = stable_id(
        "command", [command.command.user_scope, command.consent_id, command.command.command_id]
    )
    canonical = json_doc(
        {
            "schema_version": DATA_CONSENT_COMMAND_INPUT_DIGEST_VERSION,
            "action": "revoke",
            "user_scope": command.command.user_scope,
            "source_module": command.command.source_module,
            "business_request_id": command.command.business_request_id,
            "command_id": command.command.command_id,
            "consent_id": command.consent_id,
            "expected_consent_revision": command.command.expected_revision,
        }
    )
    return DataConsentCommandIdentity(command_record_id=command_record_id, input_digest=sha256_hex(canonical))


def legacy_data_consent_revoke_input_digest(command: DataConsentRevokeCommand) -> str:
    """历史 v1 撤销摘要（仅命令键/预期版）：只用于识别旧行，禁止新写入。"""

    return sha256_hex(
        json_doc(
            {
                "schema_version": 1,
                "action": "revoke",
                "command_id": command.command.command_id,
                "consent_id": command.consent_id,
                "expected_consent_revision": command.command.expected_revision,
            }
        )
    )


@dataclass(frozen=True)
class DataAttemptAuthorization:
    """资料核权请求（§10.2）：完整 source_selections 随请求携带，摘要由 owner
    新鲜重算（不信调用方 digest）；实际连接/凭据安全修订须与绑定一致；
    owner/run epoch 为 claim 归属代次（§9.9）。"""

    user_scope: str
    operation_id: str
    attempt_id: str
    consent_id: str
    expected_consent_revision: int
    connection_id: str
    connection_revision: int
    security_revision: int
    credential_version: str | None
    purpose: str
    target_fingerprint: str
    scope_version: int
    source_selections: tuple[DataConsentSourceItem, ...]
    source_scope_digest: str
    owner_epoch: int
    run_epoch: int


def validate_data_attempt_authorization(request: DataAttemptAuthorization) -> str:
    """校验核权请求并新鲜重算 source_scope_digest（§9.8：每 attempt 核权均重算）。

    返回重算摘要；请求自带摘要与重算不一致即 INPUT_INVALID（调用方输入不自
    洽），授权判定一律使用重算值，不信任请求摘要字段。
    """

    _require_str(request.user_scope, "$.user_scope")
    _require_str(request.operation_id, "$.operation_id")
    _require_str(request.attempt_id, "$.attempt_id")
    _require_str(request.consent_id, "$.consent_id")
    if (
        isinstance(request.expected_consent_revision, bool)
        or not isinstance(request.expected_consent_revision, int)
        or request.expected_consent_revision < 1
    ):
        _fail("invalid_revision", "$.expected_consent_revision")
    _require_str(request.connection_id, "$.connection_id")
    if (
        isinstance(request.connection_revision, bool)
        or not isinstance(request.connection_revision, int)
        or request.connection_revision < 1
    ):
        _fail("invalid_revision", "$.connection_revision")
    if (
        isinstance(request.security_revision, bool)
        or not isinstance(request.security_revision, int)
        or request.security_revision < 0
    ):
        _fail("invalid_revision", "$.security_revision")
    if request.credential_version is not None:
        _require_str(request.credential_version, "$.credential_version")
    _require_str(request.purpose, "$.purpose")
    _require_str(request.target_fingerprint, "$.target_fingerprint")
    if (
        isinstance(request.scope_version, bool)
        or not isinstance(request.scope_version, int)
        or request.scope_version < 1
    ):
        _fail("invalid_revision", "$.scope_version")
    if not isinstance(request.source_selections, tuple):
        _fail("invalid_type", "$.source_selections")
    for index, item in enumerate(request.source_selections):
        if not isinstance(item, DataConsentSourceItem):
            _fail("invalid_type", f"$.source_selections[{index}]")
    for epoch_field in ("owner_epoch", "run_epoch"):
        value = getattr(request, epoch_field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail("invalid_revision", f"$.{epoch_field}")
    recomputed = data_consent_source_scope_digest(request.source_selections)
    if _require_digest(request.source_scope_digest, "$.source_scope_digest") != recomputed:
        _fail("digest_mismatch", "$.source_scope_digest")
    return recomputed


def _parse_wire_moment(value: str, field_path: str) -> dt:
    """UTC 微秒 Z 时刻文本 → 带时区 datetime（period 判定用）。"""

    return dt.strptime(require_utc_timestamp(value, field_path), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def evaluate_data_attempt(
    binding: DataConsentBinding,
    request: DataAttemptAuthorization,
    now: dt,
    *,
    recomputed_digest: str | None = None,
) -> str | None:
    """资料核权判定（纯函数，§10.2「核 DataConsentBinding 准确集合/版/用途/目的地/寿命」）。

    返回 None=允许；否则返回脱敏拒绝原因码（state/revision/purpose/target/
    scope_version/连接复合引用/安全修订/凭据/摘要/生效与到期逐项分列）。
    判定一律使用重算摘要（``recomputed_digest`` 缺省时内部重算）。
    """

    if binding.state is not DataConsentState.APPROVED:
        return "state_not_approved"
    if binding.consent_revision != request.expected_consent_revision:
        return "revision_mismatch"
    if binding.purpose != request.purpose:
        return "purpose_mismatch"
    if binding.target_fingerprint != request.target_fingerprint:
        return "target_mismatch"
    if binding.scope_version != request.scope_version:
        return "scope_version_mismatch"
    ref = binding.connection_authorization_ref
    if ref.connection_id != request.connection_id:
        return "connection_mismatch"
    if binding.connection_revision != request.connection_revision:
        return "connection_revision_mismatch"
    if binding.security_revision != request.security_revision:
        return "security_revision_mismatch"
    if binding.credential_version != request.credential_version:
        return "credential_mismatch"
    digest = (
        recomputed_digest
        if recomputed_digest is not None
        else data_consent_source_scope_digest(request.source_selections)
    )
    if binding.source_scope_digest != digest:
        return "scope_mismatch"
    if binding.valid_from is not None and _parse_wire_moment(binding.valid_from, "$.valid_from") > now:
        return "not_yet_valid"
    # §9.8 L1539：期限为 null 表示「期限未核定，不表示永久/不限期」——必须
    # deny/unknown，不能自动补期限或放行（fail-closed）。
    if binding.expires_at is None:
        return "expiry_unverified"
    return "expired" if _parse_wire_moment(binding.expires_at, "$.expires_at") <= now else None


@dataclass(frozen=True)
class DataAttemptReceipt:
    """资料核权回执（§10.2）：allow/deny/unknown 与原绑定/核权 revision 分列。

    ``binding`` 为核权时读到的原绑定（不可靠重读时 None）；``reason`` 为脱敏
    原因码（allow 时 None）；不把 unknown 当 allow（fail-closed）。
    """

    outcome: DataAttemptOutcome
    operation_id: str
    attempt_id: str
    consent_id: str
    consent_revision: int | None
    binding: DataConsentBinding | None
    reason: str | None
    authorized_at: str


@dataclass(frozen=True)
class DataConsentClaim:
    """operation.data_consent_claims 条目（§9.9，v1 wire 工程冻结）。

    每 attempt 一条，含 consent 身份/实际连接许可复合引用/安全修订/凭据/摘要/
    purpose/target/attempt 与 operation 身份/owner 与 run 代次/核权时间及结果；
    不另存费用。``connection_authorization_ref.verified_connection_grant_id``
    为实际核权核到的 grant ID 或显式 null——AttemptPermit 不返回 grant ID，
    首版核权记 null，不冒认已核对象（§9.8）。
    """

    schema_version: int
    consent_id: str
    consent_revision: int
    connection_authorization_ref: ConnectionAuthorizationRef
    security_revision: int
    credential_version: str | None
    scope_version: int
    source_scope_digest: str
    purpose: str
    target_fingerprint: str
    attempt_id: str
    operation_id: str
    owner_epoch: int
    run_epoch: int
    authorized_at: str
    outcome: DataAttemptOutcome


_DATA_CONSENT_CLAIM_KEYS: Final = frozenset(
    {
        "schema_version",
        "consent_id",
        "consent_revision",
        "connection_authorization_ref",
        "security_revision",
        "credential_version",
        "scope_version",
        "source_scope_digest",
        "purpose",
        "target_fingerprint",
        "attempt_id",
        "operation_id",
        "owner_epoch",
        "run_epoch",
        "authorized_at",
        "outcome",
    }
)


def encode_data_consent_claim(claim: DataConsentClaim) -> dict[str, Any]:
    """DataConsentClaim → wire dict（16 键全量，CanonicalJson 语义承载）。"""

    return cast(dict[str, Any], _to_jsonable(claim, "$"))


def decode_data_consent_claim(value: object, path: str) -> DataConsentClaim:
    """严格解码 claim（全键必填禁额外键；JSONB 重排键重读保同身份）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DATA_CONSENT_CLAIM_KEYS, path)
    schema_version = value["schema_version"]
    # §9.8：schema_version 仅整数 1（拒 bool；1.0 因 == 1 成立，必须同时拒非 int）
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        _fail("invalid_schema_version", f"{path}.schema_version")
    security_revision = value["security_revision"]
    if isinstance(security_revision, bool) or not isinstance(security_revision, int) or security_revision < 0:
        _fail("invalid_revision", f"{path}.security_revision")
    owner_epoch = value["owner_epoch"]
    if isinstance(owner_epoch, bool) or not isinstance(owner_epoch, int) or owner_epoch < 0:
        _fail("invalid_revision", f"{path}.owner_epoch")
    run_epoch = value["run_epoch"]
    if isinstance(run_epoch, bool) or not isinstance(run_epoch, int) or run_epoch < 0:
        _fail("invalid_revision", f"{path}.run_epoch")
    credential_version = value["credential_version"]
    if credential_version is not None:
        credential_version = _require_str(credential_version, f"{path}.credential_version")
    try:
        outcome = DataAttemptOutcome(value["outcome"])
    except ValueError:
        _fail("invalid_outcome", f"{path}.outcome")
    return DataConsentClaim(
        schema_version=1,
        consent_id=_require_str(value["consent_id"], f"{path}.consent_id"),
        consent_revision=_require_p(value["consent_revision"], f"{path}.consent_revision"),
        connection_authorization_ref=_decode_connection_authorization_ref(
            value["connection_authorization_ref"], f"{path}.connection_authorization_ref"
        ),
        security_revision=security_revision,
        credential_version=credential_version,
        scope_version=_require_p(value["scope_version"], f"{path}.scope_version"),
        source_scope_digest=_require_digest(value["source_scope_digest"], f"{path}.source_scope_digest"),
        purpose=_require_str(value["purpose"], f"{path}.purpose"),
        target_fingerprint=_require_str(value["target_fingerprint"], f"{path}.target_fingerprint"),
        attempt_id=_require_str(value["attempt_id"], f"{path}.attempt_id"),
        operation_id=_require_str(value["operation_id"], f"{path}.operation_id"),
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
        authorized_at=require_utc_timestamp(value["authorized_at"], f"{path}.authorized_at"),
        outcome=outcome,
    )


def validate_data_consent_claim(
    claim: DataConsentClaim, authorization: DataAttemptAuthorization, *, recomputed_digest: str
) -> None:
    """claim 与核权请求一致性（写入前核，§9.9 claim 字段语义）。

    claim 是该次核权事实的持久投影：身份/版本/连接复合引用/安全修订/凭据/摘要
    任一与请求分叉即拒绝（identity_mismatch），不允许 claim 记录与实际核权
    输入不一致的“批准”。
    """

    if claim.consent_id != authorization.consent_id:
        _fail("identity_mismatch", "$.claim.consent_id")
    if claim.consent_revision != authorization.expected_consent_revision:
        _fail("identity_mismatch", "$.claim.consent_revision")
    if claim.operation_id != authorization.operation_id or claim.attempt_id != authorization.attempt_id:
        _fail("identity_mismatch", "$.claim.operation_id")
    if claim.owner_epoch != authorization.owner_epoch or claim.run_epoch != authorization.run_epoch:
        _fail("identity_mismatch", "$.claim.owner_epoch")
    if (
        claim.purpose != authorization.purpose
        or claim.target_fingerprint != authorization.target_fingerprint
        or claim.scope_version != authorization.scope_version
    ):
        _fail("identity_mismatch", "$.claim.purpose")
    if claim.security_revision != authorization.security_revision:
        _fail("identity_mismatch", "$.claim.security_revision")
    if claim.credential_version != authorization.credential_version:
        _fail("identity_mismatch", "$.claim.credential_version")
    if claim.source_scope_digest != recomputed_digest:
        _fail("identity_mismatch", "$.claim.source_scope_digest")
    ref = claim.connection_authorization_ref
    if (
        ref.connection_id != authorization.connection_id
        or ref.target_fingerprint != authorization.target_fingerprint
        or ref.purpose != authorization.purpose
        or ref.scope_version != authorization.scope_version
    ):
        _fail("identity_mismatch", "$.claim.connection_authorization_ref")


@dataclass(frozen=True)
class DataConsentClaimFact:
    """claim 落定事实：``replayed``=同 (operation_id, attempt_id) 同 claim 重放
    （W 崩溃恢复查回原事实，不重复追加）。"""

    claim: DataConsentClaim
    replayed: bool


# ---------------------------------------------------------------------------
# §4.4 可靠事件统一包络（C-03-1）
# ---------------------------------------------------------------------------


class EventKind(Enum):
    """可靠事件类型（§4.4 枚举表，唯一 wire 拼写；临时 TextDelta 不入本表）。"""

    SESSION_CHANGED = "session_changed"
    DRAFT_SAVED = "draft_saved"
    EXECUTION_ACCEPTED = "execution_accepted"
    INPUT_BOUND = "input_bound"
    EXECUTION_CHANGED = "execution_changed"
    NODE_CHANGED = "node_changed"
    PUBLIC_MESSAGE_SAVED = "public_message_saved"
    SOURCE_READ_SAVED = "source_read_saved"
    SOURCE_DISABLED = "source_disabled"
    TOOL_CALL_RECORDED = "tool_call_recorded"
    TOOL_RESULT_SAVED = "tool_result_saved"
    DECISION_REQUIRED = "decision_required"
    COMMAND_CHANGED = "command_changed"
    ARTIFACT_SAVED = "artifact_saved"
    USAGE_CHANGED = "usage_changed"
    READ_MARK_SAVED = "read_mark_saved"
    CLEANUP_CHANGED = "cleanup_changed"


class MessageRole(Enum):
    """公开消息角色（§9.6 role 列：user/assistant/tool/public_status）。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    PUBLIC_STATUS = "public_status"


@dataclass(frozen=True)
class ResearchEvent:
    """可靠事件统一包络（§4.4，18 键 wire 工程冻结）。

    - ``event_id`` 由准确 execution/round/node/原生事件 ID 规范组合生成
      （§9.6：原公开事实去重身份，不跨 round 当全局 ID）；
    - ``session_seq`` 为会话内单调水位（UNIQUE(session_id,session_seq)），
      ``execution_seq`` 仅执行事件有（UNIQUE(execution_id,execution_seq)）；
    - ``occurred_at``/``committed_at`` 原事实时刻与保存时刻分列；
    - ``payload_canonical_json`` 为版本化事件载荷（payload_kind 指明结构；
      各 kind 载荷 schema 随生产者事务分片冻结，包络层只校验规范 JSON）；
    - 临时事件（TextDelta 等）没有可靠 session_seq，不得进入本包络。
    """

    schema_version: int
    event_id: str
    user_scope: str
    session_id: str
    execution_id: str | None
    node_id: str | None
    round_id: str | None
    input_revision: int | None
    session_seq: int
    execution_seq: int | None
    occurred_at: str
    committed_at: str
    event_kind: EventKind
    subject_id: str
    subject_revision: int
    payload_kind: str
    payload_canonical_json: CanonicalJson
    native_fact_ref: str | None


_RESEARCH_EVENT_KEYS: Final = frozenset(
    {
        "schema_version",
        "event_id",
        "user_scope",
        "session_id",
        "execution_id",
        "node_id",
        "round_id",
        "input_revision",
        "session_seq",
        "execution_seq",
        "occurred_at",
        "committed_at",
        "event_kind",
        "subject_id",
        "subject_revision",
        "payload_kind",
        "payload_canonical_json",
        "native_fact_ref",
    }
)


def derive_event_identity(
    *,
    execution_id: str | None,
    round_id: str | None,
    node_id: str | None,
    native_event_id: str,
) -> str:
    """事件去重身份（§9.6）：execution/round/node/原生事件 ID 规范组合。

    执行事件四段全非空（原生 ID 不跨 round 当全局 ID，组合保证同一原生事实
    在准确轮内单身份）；纯 session 事件无执行身份，用 ``session_event`` 段型
    组合（stable_id parts 拒空串，故执行/会话两种段型天然不碰撞）。
    """

    if not isinstance(native_event_id, str) or native_event_id == "":
        _fail("invalid_identity", "$.native_event_id")
    if execution_id is None:
        return stable_id("event", ["session_event", native_event_id])
    if not execution_id or not round_id or not node_id:
        _fail("invalid_identity", "$.execution_id")
    return stable_id("event", [execution_id, round_id, node_id, native_event_id])


def validate_research_event(event: ResearchEvent) -> None:
    """包络结构校验（执行事件身份一致性 + 水位基线，§9.6）。

    - 执行事件（execution_id 非 None）必须同时有 round_id/node_id/execution_seq
      （§9.6「执行事件必须同时有」）；
    - ``execution_seq`` 与 ``execution_id`` 严格同存同缺（UNIQUE 语义）；
    - 纯 session 事件不得携带执行身份（round/node/execution_seq 须 None）；
    - session_seq ≥1、subject_revision ≥1（§3.1 单调水位起点 1）；
    - payload 必须是可解析规范 JSON（结构 schema 随生产者冻结，包络层不猜）。
    """

    _require_str(event.event_id, "$.event_id")
    _require_str(event.user_scope, "$.user_scope")
    _require_str(event.session_id, "$.session_id")
    _require_str(event.subject_id, "$.subject_id")
    _require_str(event.payload_kind, "$.payload_kind")
    if not isinstance(event.event_kind, EventKind):
        _fail("invalid_type", "$.event_kind")
    if isinstance(event.schema_version, bool) or event.schema_version != 1:
        _fail("invalid_schema_version", "$.schema_version")
    if isinstance(event.session_seq, bool) or not isinstance(event.session_seq, int) or event.session_seq < 1:
        _fail("invalid_revision", "$.session_seq")
    if (
        isinstance(event.subject_revision, bool)
        or not isinstance(event.subject_revision, int)
        or event.subject_revision < 1
    ):
        _fail("invalid_revision", "$.subject_revision")
    if event.input_revision is not None:
        if (
            isinstance(event.input_revision, bool)
            or not isinstance(event.input_revision, int)
            or event.input_revision < 1
        ):
            _fail("invalid_revision", "$.input_revision")
    require_utc_timestamp(event.occurred_at, "$.occurred_at")
    require_utc_timestamp(event.committed_at, "$.committed_at")
    if event.execution_id is not None:
        _require_str(event.execution_id, "$.execution_id")
        if event.round_id is None or event.node_id is None or event.execution_seq is None:
            _fail("identity_mismatch", "$.execution_id")
        _require_str(event.round_id, "$.round_id")
        _require_str(event.node_id, "$.node_id")
        if isinstance(event.execution_seq, bool) or not isinstance(event.execution_seq, int) or event.execution_seq < 1:
            _fail("invalid_revision", "$.execution_seq")
    else:
        if event.execution_seq is not None or event.round_id is not None or event.node_id is not None:
            _fail("identity_mismatch", "$.execution_id")
    # event_id 与 (execution/round/node/native_event_id) 组合派生一致性需原生
    # 事件 ID（不在包络 wire 内），由调用方经 validate_event_identity 复核；
    # 纯 session 事件组合为空段，包络层无独立重算路径。
    # payload 必须可解析且为规范 JSON（decode_json_doc 拒重复键/NaN/非有限数）。
    decode_json_doc(event.payload_canonical_json)
    if event.native_fact_ref is not None:
        _require_str(event.native_fact_ref, "$.native_fact_ref")


def validate_event_identity(event: ResearchEvent, native_event_id: str) -> None:
    """event_id 与 (execution/round/node/native_event_id) 组合派生一致性（§9.6）。"""

    expected = derive_event_identity(
        execution_id=event.execution_id,
        round_id=event.round_id,
        node_id=event.node_id,
        native_event_id=native_event_id,
    )
    if event.event_id != expected:
        _fail("identity_mismatch", "$.event_id")


def encode_research_event(event: ResearchEvent) -> dict[str, Any]:
    """ResearchEvent → wire dict（18 键全量）。"""

    return cast(dict[str, Any], _to_jsonable(event, "$"))


def decode_research_event(value: object, path: str) -> ResearchEvent:
    """严格解码事件包络（全键必填禁额外键；JSONB 重排键重读保同身份）。

    身份组合与 :func:`validate_research_event` 同判据（检视 R19）：执行事件必须
    同时有 round/node/execution_seq，纯 session 事件不得携任一执行身份；载荷必须
    是可解析规范 JSON（重复键/非有限数/坏语法一律拒绝）。event_id 与原生事件 ID
    的组合派生一致性需原生事件 ID（不在包络 wire 内），仍由调用方以
    :func:`validate_event_identity` 复核。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _RESEARCH_EVENT_KEYS, path)
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        _fail("invalid_schema_version", f"{path}.schema_version")
    try:
        event_kind = EventKind(value["event_kind"])
    except ValueError:
        _fail("invalid_event_kind", f"{path}.event_kind")
    session_seq = value["session_seq"]
    if isinstance(session_seq, bool) or not isinstance(session_seq, int) or session_seq < 1:
        _fail("invalid_revision", f"{path}.session_seq")
    subject_revision = value["subject_revision"]
    if isinstance(subject_revision, bool) or not isinstance(subject_revision, int) or subject_revision < 1:
        _fail("invalid_revision", f"{path}.subject_revision")
    input_revision = value["input_revision"]
    if input_revision is not None and (
        isinstance(input_revision, bool) or not isinstance(input_revision, int) or input_revision < 1
    ):
        _fail("invalid_revision", f"{path}.input_revision")
    execution_id = value["execution_id"]
    execution_seq = value["execution_seq"]
    round_id = None if value["round_id"] is None else _require_str(value["round_id"], f"{path}.round_id")
    node_id = None if value["node_id"] is None else _require_str(value["node_id"], f"{path}.node_id")
    if execution_id is not None:
        # 执行事件必须同时有 round/node（§9.6；与 validate_research_event 一致）。
        if round_id is None:
            _fail("identity_mismatch", f"{path}.round_id")
        if node_id is None:
            _fail("identity_mismatch", f"{path}.node_id")
        if execution_seq is None:
            _fail("identity_mismatch", f"{path}.execution_seq")
    else:
        # 纯 session 事件不得携带执行身份（round/node/execution_seq 同缺）。
        if round_id is not None:
            _fail("identity_mismatch", f"{path}.round_id")
        if node_id is not None:
            _fail("identity_mismatch", f"{path}.node_id")
        if execution_seq is not None:
            _fail("identity_mismatch", f"{path}.execution_seq")
    execution_seq_v = None
    if execution_seq is not None:
        if isinstance(execution_seq, bool) or not isinstance(execution_seq, int) or execution_seq < 1:
            _fail("invalid_revision", f"{path}.execution_seq")
        execution_seq_v = execution_seq
    payload_canonical_json = _require_str(value["payload_canonical_json"], f"{path}.payload_canonical_json")
    # 载荷 JSONB 重读必须可解析为规范 JSON（坏 JSON 不冒认合法事件，R21）。
    try:
        decode_json_doc(payload_canonical_json)
    except AgentValidationError:
        _fail("invalid_json", f"{path}.payload_canonical_json")
    native_fact_ref = value["native_fact_ref"]
    if native_fact_ref is not None:
        native_fact_ref = _require_str(native_fact_ref, f"{path}.native_fact_ref")
    return ResearchEvent(
        schema_version=1,
        event_id=_require_str(value["event_id"], f"{path}.event_id"),
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=None if execution_id is None else _require_str(execution_id, f"{path}.execution_id"),
        node_id=node_id,
        round_id=round_id,
        input_revision=input_revision,
        session_seq=session_seq,
        execution_seq=execution_seq_v,
        occurred_at=require_utc_timestamp(value["occurred_at"], f"{path}.occurred_at"),
        committed_at=require_utc_timestamp(value["committed_at"], f"{path}.committed_at"),
        event_kind=event_kind,
        subject_id=_require_str(value["subject_id"], f"{path}.subject_id"),
        subject_revision=subject_revision,
        payload_kind=_require_str(value["payload_kind"], f"{path}.payload_kind"),
        payload_canonical_json=payload_canonical_json,
        native_fact_ref=native_fact_ref,
    )


@dataclass(frozen=True)
class RecordEventRequest:
    """可靠事件登记请求（C-03-1）：生产者事务侧事实 + 行载荷全量字段。

    - ``message_id``：公开消息或事件稳定身份（调用方 stable_id 派生，PK）；
    - ``body_text``/``body_ref`` 互斥（§9.6）；``search_text`` 仅可读公开文本；
    - ``committed_at`` 由 DAO 事务取库时钟，不在请求内（保存时刻=提交事实）。
    """

    user_scope: str
    session_id: str
    message_id: str
    native_event_id: str
    event_kind: EventKind
    subject_id: str
    subject_revision: int
    payload_kind: str
    payload_canonical_json: CanonicalJson
    occurred_at: str
    execution_id: str | None = None
    node_id: str | None = None
    round_id: str | None = None
    input_revision: int | None = None
    role: MessageRole | None = None
    role_id: str | None = None
    role_version: str | None = None
    body_text: str | None = None
    body_ref: str | None = None
    search_text: str | None = None
    native_fact_ref: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class EventFact:
    """事件落定事实：水位为事务内分配后的新值（单调，R22）。"""

    message_id: str
    event_id: str
    session_seq: int
    execution_seq: int | None
    session_event_seq: int
    execution_event_seq: int | None
    committed_at: str
    replayed: bool


@dataclass(frozen=True)
class EventReadQuery:
    """可靠事件读取查询（§4.5 追平/补齐）：after_seq 起按序分页。"""

    user_scope: str
    session_id: str
    after_seq: int
    limit: int = 50


@dataclass(frozen=True)
class EventPage:
    """事件页：``events`` 按 session_seq 升序；``has_more`` 指示继续追平。"""

    events: tuple[ResearchEvent, ...]
    last_seq: int
    has_more: bool


# ---------------------------------------------------------------------------
# §4.2/§4.3 读取合同类型（C-03-2a：SessionReadQuery/SessionRead/ResearchSnapshot）
# ---------------------------------------------------------------------------

# 设计正本 §4.3 为字段→内容映射表（无逐键 wire 冻结表），本节工程裁决：
# frozen dataclass + decode strict 全键必填禁额外键，encode 走 to_wire_dict 泛型；
# 未冻结结构的 JSONB 以 CanonicalJson 文本承载（不臆造结构）；None 保真（R21）。
# 值集常量与 data/persistence/models.py 的冻结 CHECK 同步（漂移守护见
# tests/unit/test_agent_snapshot_types.py）。


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail("invalid_type", path)
    return value


def _require_text(value: object, path: str) -> str:
    """自由文本列（title/summary 等）：须为 str，允许空串（DB 不约束非空）。"""

    if not isinstance(value, str):
        _fail("invalid_type", path)
    return value


def _opt_str(value: object, path: str) -> str | None:
    return None if value is None else _require_str(value, path)


def _opt_text(value: object, path: str) -> str | None:
    return None if value is None else _require_text(value, path)


def _opt_digest(value: object, path: str) -> str | None:
    return None if value is None else _require_digest(value, path)


def _opt_timestamp(value: object, path: str) -> str | None:
    return None if value is None else _require_utc_timestamp(value, path)


def _opt_int(value: object, path: str, *, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("invalid_range", path)
    return value


def _nonneg_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("invalid_range", path)
    return value


def _require_enum[E: Enum](value: object, enum_cls: type[E], path: str, code: str) -> E:
    """枚举解码（沿 decode_research_event 惯例：非 str/未知值统一给稳定错误码）。"""

    try:
        return enum_cls(value)  # type: ignore[call-arg]  # Enum 按值查找，非法输入由 except 拦截
    except (ValueError, TypeError, KeyError):
        _fail(code, path)


def _require_choice(value: object, allowed: AbstractSet[str], path: str, code: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail(code, path)
    return value


def _opt_json_text(value: object, path: str) -> CanonicalJson | None:
    """未冻结结构 JSONB 投影：CanonicalJson 文本承载，非规范 JSON 拒绝（fail-closed）。"""

    if value is None:
        return None
    text = _require_str(value, path)
    try:
        decode_json_doc(text)
    except AgentValidationError:
        _fail("invalid_json", path)
    return text


#: 执行类别（§9.4 CHECK kind）。
EXECUTION_KINDS: Final[frozenset[str]] = frozenset({"root", "leaf", "comparison_lane"})

#: 展示状态 15 值（§9.4 CHECK display_state；排队/运行/依赖/人工等待/暂停/终止/unknown/终态）。
DISPLAY_STATES: Final[frozenset[str]] = frozenset(
    {
        "queued",
        "running",
        "waiting_dependency",
        "waiting_user",
        "pausing",
        "paused",
        "ending",
        "unknown",
        "ended",
        "failed",
        "complete",
        "partial",
        "planned",
        "withdrawn",
        "closed_unknown",
    }
)

#: 目标覆盖 4 值（§9.4/§9.5 CHECK goal_coverage）。
GOAL_COVERAGES: Final[frozenset[str]] = frozenset({"full", "partial", "none", "unknown"})

#: 研究完成结论 4 值（F04：完整/部分/失败/未知分别判定；未知优先于失败）。
RESEARCH_OUTCOMES: Final[frozenset[str]] = frozenset({"complete", "partial", "failed", "unknown"})

#: 对象级缺口类型（F04/AC04：缺对象/缺期间/来源冲突/论据未核验/数据缺失/无成果/结果未确认）。
RESEARCH_GAP_KINDS: Final[frozenset[str]] = frozenset(
    {
        "missing_object",
        "missing_period",
        "source_conflict",
        "unverified_claim",
        "missing_data",
        "no_result",
        "result_unconfirmed",
    }
)

#: 成果类别 8 值（§9.7 CHECK kind）。
ARTIFACT_KINDS: Final[frozenset[str]] = frozenset(
    {"plan", "explanation", "comparison", "candidate", "news", "backtest", "period", "stock_final"}
)

#: 受管资产类别 8 值（§9.10 CHECK kind）。
ASSET_KINDS: Final[frozenset[str]] = frozenset(
    {"source", "skill", "role", "prompt", "mcp_schema", "plan", "comparison_manifest", "summary"}
)

#: 来源类别 6 值（§3.1 SourceKind wire 拼写；source_reads.source_kind 的合法值集）。
SOURCE_KINDS: Final[frozenset[str]] = frozenset(kind.value for kind in SourceKind)

#: 来源谱系消费方类别 6 值（§9.10 CHECK consumer_kind）。
SOURCE_CONSUMER_KINDS: Final[frozenset[str]] = frozenset(
    {"draft", "execution", "round", "message", "artifact", "asset"}
)

#: 操作阶段 6 值（§9.9 CHECK phase）。
OPERATION_PHASES: Final[frozenset[str]] = frozenset(
    {"prepared", "dispatch_claimed", "dispatched", "result_saved", "failed_confirmed", "unknown"}
)

#: 工具 grant 状态（C-02-3 工程冻结 wire 值，无 DB CHECK）。
GRANT_STATES: Final[frozenset[str]] = frozenset({"granted", "claimed", "revoked"})

#: 游标最小编码（工程裁决，§4.2 L402）：message ``m:<session_seq>``。
_MESSAGE_CURSOR_RE: Final = re.compile(r"m:(?:0|[1-9][0-9]*)")
#: artifact ``a:<published_at wire>:<artifact_id>:<version>``（artifact_id 无冒号）。
# NOTE(lazy): 分页游标锚段 [^:\s]+ 禁冒号，含冒号的 artifact_id/message_id 无法编码进游标（该 ID 翻页失效，检视 m-5）. ceiling: 项目内 ID 均由 stable_id（kind_sha256 十六进制）等内部生成器产出，不含冒号. upgrade: 引入接受外部自定义 ID 的入口或人工构造含冒号 ID 的路径时，改为锚段转义编码（cursor 格式需同步升版）.
_ARTIFACT_CURSOR_RE: Final = re.compile(r"a:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z):([^:\s]+):([1-9][0-9]*)")


def _opt_cursor(value: object, path: str) -> str | None:
    """游标字面量：None 透传；非 str 拒绝（语法校验统一在 _validate 阶段给 invalid_cursor）。"""

    if value is None:
        return None
    if not isinstance(value, str):
        _fail("invalid_type", path)
    return value


@dataclass(frozen=True, slots=True)
class SessionReadQuery:
    """会话首读查询（§4.2 SessionReadQuery 行；limit 为工程读取边界 1—50）。

    准确选择不使用 latest 覆盖（无 latest 字段）；游标只做语法校验，绑定
    user_scope/查询 digest/修订指纹失效属 HistoryQuery/HistoryPage（R02，C-03-3）。
    """

    session_id: str
    limit: int = 50
    execution_id: str | None = None
    round_id: str | None = None
    draft_revision: int | None = None
    message_cursor: str | None = None
    artifact_cursor: str | None = None


_SESSION_READ_QUERY_KEYS: Final = frozenset(
    {
        "session_id",
        "execution_id",
        "round_id",
        "draft_revision",
        "message_cursor",
        "artifact_cursor",
        "limit",
    }
)


def _validate_session_read_query(query: SessionReadQuery, path: str) -> None:
    if isinstance(query.limit, bool) or not isinstance(query.limit, int):
        _fail("invalid_type", f"{path}.limit")
    if not 1 <= query.limit <= 50:
        _fail("invalid_range", f"{path}.limit")
    if query.draft_revision is not None and (
        isinstance(query.draft_revision, bool) or not isinstance(query.draft_revision, int) or query.draft_revision < 1
    ):
        _fail("invalid_range", f"{path}.draft_revision")
    if query.execution_id is not None:
        _require_str(query.execution_id, f"{path}.execution_id")
    if query.round_id is not None:
        _require_str(query.round_id, f"{path}.round_id")
    if query.message_cursor is not None:
        if not isinstance(query.message_cursor, str):
            _fail("invalid_type", f"{path}.message_cursor")
        if _MESSAGE_CURSOR_RE.fullmatch(query.message_cursor) is None:
            _fail("invalid_cursor", f"{path}.message_cursor")
    if query.artifact_cursor is not None:
        if not isinstance(query.artifact_cursor, str):
            _fail("invalid_type", f"{path}.artifact_cursor")
        if _ARTIFACT_CURSOR_RE.fullmatch(query.artifact_cursor) is None:
            _fail("invalid_cursor", f"{path}.artifact_cursor")


def validate_session_read_query(query: SessionReadQuery) -> None:
    """SessionReadQuery 结构校验：limit 边界/草稿修订/游标语法（§4.2 L402）。"""

    _validate_session_read_query(query, "$")


def decode_session_read_query(value: object, path: str = "$.session_read_query") -> SessionReadQuery:
    """严格解码会话首读查询（7 键全必填禁额外键；构造后即校验）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SESSION_READ_QUERY_KEYS, path)
    limit = value["limit"]
    if isinstance(limit, bool) or not isinstance(limit, int):
        _fail("invalid_type", f"{path}.limit")
    draft_revision = _opt_int(value["draft_revision"], f"{path}.draft_revision", minimum=1)
    query = SessionReadQuery(
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        limit=limit,
        execution_id=_opt_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_opt_str(value["round_id"], f"{path}.round_id"),
        draft_revision=draft_revision,
        message_cursor=_opt_cursor(value["message_cursor"], f"{path}.message_cursor"),
        artifact_cursor=_opt_cursor(value["artifact_cursor"], f"{path}.artifact_cursor"),
    )
    _validate_session_read_query(query, path)
    return query


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """agent_session 行投影（§4.2 SessionRead 元数据；草稿只携引用不载正文）。"""

    user_scope: str
    session_id: str
    title: str
    title_user_set: bool
    pinned: bool
    archived: bool
    draft_revision: int
    event_seq: int
    created_at: str
    updated_at: str
    revision: int
    draft_transfer_ref: str | None
    causal_head_ref: str | None
    source_module: str | None
    origin_ref: str | None
    parent_session_id: str | None
    branch_metadata: CanonicalJson | None
    cleaned_at: str | None


_SESSION_SUMMARY_KEYS: Final = frozenset(
    {
        "user_scope",
        "session_id",
        "title",
        "title_user_set",
        "pinned",
        "archived",
        "draft_revision",
        "event_seq",
        "created_at",
        "updated_at",
        "revision",
        "draft_transfer_ref",
        "causal_head_ref",
        "source_module",
        "origin_ref",
        "parent_session_id",
        "branch_metadata",
        "cleaned_at",
    }
)


def decode_session_summary(value: object, path: str = "$.session_summary") -> SessionSummary:
    """严格解码会话摘要（18 键全必填禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SESSION_SUMMARY_KEYS, path)
    return SessionSummary(
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        title=_require_text(value["title"], f"{path}.title"),
        title_user_set=_require_bool(value["title_user_set"], f"{path}.title_user_set"),
        pinned=_require_bool(value["pinned"], f"{path}.pinned"),
        archived=_require_bool(value["archived"], f"{path}.archived"),
        draft_revision=cast(int, require_revision(value["draft_revision"], f"{path}.draft_revision")),
        event_seq=_nonneg_int(value["event_seq"], f"{path}.event_seq"),
        created_at=_require_utc_timestamp(value["created_at"], f"{path}.created_at"),
        updated_at=_require_utc_timestamp(value["updated_at"], f"{path}.updated_at"),
        revision=cast(int, require_revision(value["revision"], f"{path}.revision")),
        draft_transfer_ref=_opt_str(value["draft_transfer_ref"], f"{path}.draft_transfer_ref"),
        causal_head_ref=_opt_str(value["causal_head_ref"], f"{path}.causal_head_ref"),
        source_module=_opt_str(value["source_module"], f"{path}.source_module"),
        origin_ref=_opt_str(value["origin_ref"], f"{path}.origin_ref"),
        parent_session_id=_opt_str(value["parent_session_id"], f"{path}.parent_session_id"),
        branch_metadata=_opt_json_text(value["branch_metadata"], f"{path}.branch_metadata"),
        cleaned_at=_opt_timestamp(value["cleaned_at"], f"{path}.cleaned_at"),
    )


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    """agent_execution 行投影（§4.3 execution/nodes 行：状态/覆盖/计数/水位分列）。"""

    user_scope: str
    session_id: str
    execution_id: str
    execution_kind: str
    node_id: str
    display_state: str
    goal_coverage: str
    owner_epoch: int
    dispatch_closed: bool
    deletion_fenced: bool
    model_attempt_count: int
    tool_call_count: int
    active_ms: int
    event_seq: int
    created_at: str
    updated_at: str
    revision: int
    source_module: str | None
    business_request_id: str | None
    intent_digest: str | None
    parent_execution_id: str | None
    comparison_group_ref: str | None
    comparison_lane_ref: str | None
    current_round_id: str | None
    last_verified_at: str | None
    ended_at: str | None
    unknown_refs: CanonicalJson | None


_EXECUTION_SUMMARY_KEYS: Final = frozenset(
    {
        "user_scope",
        "session_id",
        "execution_id",
        "execution_kind",
        "node_id",
        "display_state",
        "goal_coverage",
        "owner_epoch",
        "dispatch_closed",
        "deletion_fenced",
        "model_attempt_count",
        "tool_call_count",
        "active_ms",
        "event_seq",
        "created_at",
        "updated_at",
        "revision",
        "source_module",
        "business_request_id",
        "intent_digest",
        "parent_execution_id",
        "comparison_group_ref",
        "comparison_lane_ref",
        "current_round_id",
        "last_verified_at",
        "ended_at",
        "unknown_refs",
    }
)


def decode_execution_summary(value: object, path: str = "$.execution_summary") -> ExecutionSummary:
    """严格解码执行摘要（27 键全必填禁额外键；值集与 §9.4 CHECK 同步）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _EXECUTION_SUMMARY_KEYS, path)
    return ExecutionSummary(
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        execution_kind=_require_choice(
            value["execution_kind"], EXECUTION_KINDS, f"{path}.execution_kind", "invalid_execution_kind"
        ),
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        display_state=_require_choice(
            value["display_state"], DISPLAY_STATES, f"{path}.display_state", "invalid_display_state"
        ),
        goal_coverage=_require_choice(
            value["goal_coverage"], GOAL_COVERAGES, f"{path}.goal_coverage", "invalid_goal_coverage"
        ),
        owner_epoch=_nonneg_int(value["owner_epoch"], f"{path}.owner_epoch"),
        dispatch_closed=_require_bool(value["dispatch_closed"], f"{path}.dispatch_closed"),
        deletion_fenced=_require_bool(value["deletion_fenced"], f"{path}.deletion_fenced"),
        model_attempt_count=_nonneg_int(value["model_attempt_count"], f"{path}.model_attempt_count"),
        tool_call_count=_nonneg_int(value["tool_call_count"], f"{path}.tool_call_count"),
        active_ms=_nonneg_int(value["active_ms"], f"{path}.active_ms"),
        event_seq=_nonneg_int(value["event_seq"], f"{path}.event_seq"),
        created_at=_require_utc_timestamp(value["created_at"], f"{path}.created_at"),
        updated_at=_require_utc_timestamp(value["updated_at"], f"{path}.updated_at"),
        revision=cast(int, require_revision(value["revision"], f"{path}.revision")),
        source_module=_opt_str(value["source_module"], f"{path}.source_module"),
        business_request_id=_opt_str(value["business_request_id"], f"{path}.business_request_id"),
        intent_digest=_opt_digest(value["intent_digest"], f"{path}.intent_digest"),
        parent_execution_id=_opt_str(value["parent_execution_id"], f"{path}.parent_execution_id"),
        comparison_group_ref=_opt_str(value["comparison_group_ref"], f"{path}.comparison_group_ref"),
        comparison_lane_ref=_opt_str(value["comparison_lane_ref"], f"{path}.comparison_lane_ref"),
        current_round_id=_opt_str(value["current_round_id"], f"{path}.current_round_id"),
        last_verified_at=_opt_timestamp(value["last_verified_at"], f"{path}.last_verified_at"),
        ended_at=_opt_timestamp(value["ended_at"], f"{path}.ended_at"),
        unknown_refs=_opt_json_text(value["unknown_refs"], f"{path}.unknown_refs"),
    )


@dataclass(frozen=True, slots=True)
class MessagePageItem:
    """agent_message 首屏投影（§4.3 messages_page 行）：body_ref 行不载正文，正文按需读取。

    行身份/事件身份/角色/正文或 chunk 引用/规范载荷文本；``body_text`` 与
    ``body_ref`` 互斥（§9.6 CHECK body_exclusive）。
    """

    message_id: str
    session_id: str
    event_id: str
    session_seq: int
    event_kind: EventKind
    payload_kind: str
    subject_id: str
    subject_revision: int
    occurred_at: str
    committed_at: str
    execution_id: str | None
    round_id: str | None
    node_id: str | None
    execution_seq: int | None
    input_revision: int | None
    role: MessageRole | None
    role_id: str | None
    role_version: str | None
    body_text: str | None
    body_ref: str | None
    payload: CanonicalJson | None
    native_fact_ref: str | None
    tool_call_id: str | None


_MESSAGE_PAGE_ITEM_KEYS: Final = frozenset(
    {
        "message_id",
        "session_id",
        "event_id",
        "session_seq",
        "event_kind",
        "payload_kind",
        "subject_id",
        "subject_revision",
        "occurred_at",
        "committed_at",
        "execution_id",
        "round_id",
        "node_id",
        "execution_seq",
        "input_revision",
        "role",
        "role_id",
        "role_version",
        "body_text",
        "body_ref",
        "payload",
        "native_fact_ref",
        "tool_call_id",
    }
)


def decode_message_page_item(value: object, path: str = "$.message") -> MessagePageItem:
    """严格解码消息首屏项（23 键全必填禁额外键；行归属与 §9.6 CHECK 镜像）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _MESSAGE_PAGE_ITEM_KEYS, path)
    execution_id = _opt_str(value["execution_id"], f"{path}.execution_id")
    round_id = _opt_str(value["round_id"], f"{path}.round_id")
    execution_seq = _opt_int(value["execution_seq"], f"{path}.execution_seq", minimum=1)
    if (execution_id is None) != (round_id is None):
        _fail("identity_mismatch", f"{path}.round_id")
    if execution_id is not None and execution_seq is None:
        _fail("identity_mismatch", f"{path}.execution_seq")
    if execution_id is None and execution_seq is not None:
        _fail("identity_mismatch", f"{path}.execution_seq")
    body_text = _opt_text(value["body_text"], f"{path}.body_text")
    body_ref = _opt_str(value["body_ref"], f"{path}.body_ref")
    if body_text is not None and body_ref is not None:
        _fail("body_conflict", f"{path}.body_ref")
    return MessagePageItem(
        message_id=_require_str(value["message_id"], f"{path}.message_id"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        event_id=_require_str(value["event_id"], f"{path}.event_id"),
        session_seq=cast(int, require_revision(value["session_seq"], f"{path}.session_seq")),
        event_kind=_require_enum(value["event_kind"], EventKind, f"{path}.event_kind", "invalid_event_kind"),
        payload_kind=_require_str(value["payload_kind"], f"{path}.payload_kind"),
        subject_id=_require_str(value["subject_id"], f"{path}.subject_id"),
        subject_revision=cast(int, require_revision(value["subject_revision"], f"{path}.subject_revision")),
        occurred_at=_require_utc_timestamp(value["occurred_at"], f"{path}.occurred_at"),
        committed_at=_require_utc_timestamp(value["committed_at"], f"{path}.committed_at"),
        execution_id=execution_id,
        round_id=round_id,
        node_id=_opt_str(value["node_id"], f"{path}.node_id"),
        execution_seq=execution_seq,
        input_revision=_opt_int(value["input_revision"], f"{path}.input_revision", minimum=1),
        role=_require_enum(value["role"], MessageRole, f"{path}.role", "invalid_role")
        if value["role"] is not None
        else None,
        role_id=_opt_str(value["role_id"], f"{path}.role_id"),
        role_version=_opt_str(value["role_version"], f"{path}.role_version"),
        body_text=body_text,
        body_ref=body_ref,
        payload=_opt_json_text(value["payload"], f"{path}.payload"),
        native_fact_ref=_opt_str(value["native_fact_ref"], f"{path}.native_fact_ref"),
        tool_call_id=_opt_str(value["tool_call_id"], f"{path}.tool_call_id"),
    )


@dataclass(frozen=True, slots=True)
class ArtifactPageItem:
    """agent_artifact 首屏投影（§4.3 artifacts_page 行）：身份/版本/摘要/正文或 chunk 引用。

    ``result_payload`` 结构未冻结，以 CanonicalJson 文本承载；完整成果正文经
    read_artifact 按需读取（首屏不复制完整正文）。
    """

    artifact_id: str
    artifact_version: int
    session_id: str
    execution_id: str
    round_id: str
    artifact_kind: str
    input_revision: int
    actual_input_digest: str
    actual_model_ref: str
    result_digest: str
    source_available_state: str
    created_at: str
    updated_at: str
    revision: int
    summary: str | None
    body_text: str | None
    body_ref: str | None
    result_payload: CanonicalJson | None
    published_at: str | None
    cleaned_at: str | None


_ARTIFACT_PAGE_ITEM_KEYS: Final = frozenset(
    {
        "artifact_id",
        "artifact_version",
        "session_id",
        "execution_id",
        "round_id",
        "artifact_kind",
        "input_revision",
        "actual_input_digest",
        "actual_model_ref",
        "result_digest",
        "source_available_state",
        "created_at",
        "updated_at",
        "revision",
        "summary",
        "body_text",
        "body_ref",
        "result_payload",
        "published_at",
        "cleaned_at",
    }
)


def decode_artifact_page_item(value: object, path: str = "$.artifact") -> ArtifactPageItem:
    """严格解码成果首屏项（20 键全必填禁额外键；值集与 §9.7 CHECK 同步）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _ARTIFACT_PAGE_ITEM_KEYS, path)
    body_text = _opt_text(value["body_text"], f"{path}.body_text")
    body_ref = _opt_str(value["body_ref"], f"{path}.body_ref")
    if body_text is not None and body_ref is not None:
        _fail("body_conflict", f"{path}.body_ref")
    return ArtifactPageItem(
        artifact_id=_require_str(value["artifact_id"], f"{path}.artifact_id"),
        artifact_version=cast(int, require_revision(value["artifact_version"], f"{path}.artifact_version")),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        artifact_kind=_require_choice(
            value["artifact_kind"], ARTIFACT_KINDS, f"{path}.artifact_kind", "invalid_artifact_kind"
        ),
        input_revision=cast(int, require_revision(value["input_revision"], f"{path}.input_revision")),
        actual_input_digest=_require_digest(value["actual_input_digest"], f"{path}.actual_input_digest"),
        actual_model_ref=_require_str(value["actual_model_ref"], f"{path}.actual_model_ref"),
        result_digest=_require_digest(value["result_digest"], f"{path}.result_digest"),
        source_available_state=_require_str(value["source_available_state"], f"{path}.source_available_state"),
        created_at=_require_utc_timestamp(value["created_at"], f"{path}.created_at"),
        updated_at=_require_utc_timestamp(value["updated_at"], f"{path}.updated_at"),
        revision=cast(int, require_revision(value["revision"], f"{path}.revision")),
        summary=_opt_text(value["summary"], f"{path}.summary"),
        body_text=body_text,
        body_ref=body_ref,
        result_payload=_opt_json_text(value["result_payload"], f"{path}.result_payload"),
        published_at=_opt_timestamp(value["published_at"], f"{path}.published_at"),
        cleaned_at=_opt_timestamp(value["cleaned_at"], f"{path}.cleaned_at"),
    )


@dataclass(frozen=True, slots=True)
class DecisionSummary:
    """agent_decision 行投影（§4.3 decisions 行）：载荷以 CanonicalJson 文本承载。

    原回执经 ``answer_command_id`` 引用 + read_command 按需查询，快照不复制
    完整回执正文（SessionRead「不复制完整正文」同原则）；保存成功不冒认批准生效。
    """

    decision_id: str
    session_id: str
    execution_id: str
    round_id: str
    node_id: str
    decision_kind: str
    presentation_batch_seq: int
    created_at: str
    updated_at: str
    revision: int
    tool_call_id: str | None
    question_payload: CanonicalJson | None
    answer_schema: CanonicalJson | None
    parameter_digest: str | None
    source_scope_digest: str | None
    service_ref: str | None
    tool_ref: str | None
    tool_schema_version: str | None
    expires_at: str | None
    answer_consumed_at: str | None
    answer_command_id: str | None
    grant_id: str | None
    grant_revision: int | None
    grant_state: str | None
    dispatch_operation_id: str | None
    data_consent_payload: CanonicalJson | None


_DECISION_SUMMARY_KEYS: Final = frozenset(
    {
        "decision_id",
        "session_id",
        "execution_id",
        "round_id",
        "node_id",
        "decision_kind",
        "presentation_batch_seq",
        "created_at",
        "updated_at",
        "revision",
        "tool_call_id",
        "question_payload",
        "answer_schema",
        "parameter_digest",
        "source_scope_digest",
        "service_ref",
        "tool_ref",
        "tool_schema_version",
        "expires_at",
        "answer_consumed_at",
        "answer_command_id",
        "grant_id",
        "grant_revision",
        "grant_state",
        "dispatch_operation_id",
        "data_consent_payload",
    }
)


def decode_decision_summary(value: object, path: str = "$.decision") -> DecisionSummary:
    """严格解码决定摘要（26 键全必填禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DECISION_SUMMARY_KEYS, path)
    return DecisionSummary(
        decision_id=_require_str(value["decision_id"], f"{path}.decision_id"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        decision_kind=_require_str(value["decision_kind"], f"{path}.decision_kind"),
        presentation_batch_seq=_nonneg_int(value["presentation_batch_seq"], f"{path}.presentation_batch_seq"),
        created_at=_require_utc_timestamp(value["created_at"], f"{path}.created_at"),
        updated_at=_require_utc_timestamp(value["updated_at"], f"{path}.updated_at"),
        revision=cast(int, require_revision(value["revision"], f"{path}.revision")),
        tool_call_id=_opt_str(value["tool_call_id"], f"{path}.tool_call_id"),
        question_payload=_opt_json_text(value["question_payload"], f"{path}.question_payload"),
        answer_schema=_opt_json_text(value["answer_schema"], f"{path}.answer_schema"),
        parameter_digest=_opt_digest(value["parameter_digest"], f"{path}.parameter_digest"),
        source_scope_digest=_opt_digest(value["source_scope_digest"], f"{path}.source_scope_digest"),
        service_ref=_opt_str(value["service_ref"], f"{path}.service_ref"),
        tool_ref=_opt_str(value["tool_ref"], f"{path}.tool_ref"),
        tool_schema_version=_opt_str(value["tool_schema_version"], f"{path}.tool_schema_version"),
        expires_at=_opt_timestamp(value["expires_at"], f"{path}.expires_at"),
        answer_consumed_at=_opt_timestamp(value["answer_consumed_at"], f"{path}.answer_consumed_at"),
        answer_command_id=_opt_str(value["answer_command_id"], f"{path}.answer_command_id"),
        grant_id=_opt_str(value["grant_id"], f"{path}.grant_id"),
        grant_revision=_opt_int(value["grant_revision"], f"{path}.grant_revision", minimum=0),
        grant_state=_opt_choice_safe(value["grant_state"], f"{path}.grant_state"),
        dispatch_operation_id=_opt_str(value["dispatch_operation_id"], f"{path}.dispatch_operation_id"),
        data_consent_payload=_opt_json_text(value["data_consent_payload"], f"{path}.data_consent_payload"),
    )


def _opt_choice_safe(value: object, path: str) -> str | None:
    return None if value is None else _require_choice(value, GRANT_STATES, path, "invalid_grant_state")


@dataclass(frozen=True, slots=True)
class MessagePage:
    """消息首屏页（§4.2/§4.3 messages_page：分页上界 + 下一游标，不静默截断）。"""

    items: tuple[MessagePageItem, ...]
    has_more: bool
    next_cursor: str | None


_MESSAGE_PAGE_KEYS: Final = frozenset({"items", "has_more", "next_cursor"})


def decode_message_page(value: object, path: str = "$.messages_page") -> MessagePage:
    """严格解码消息页（3 键全必填；has_more=True 必须携下一游标）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _MESSAGE_PAGE_KEYS, path)
    items_value = value["items"]
    if not isinstance(items_value, list):
        _fail("invalid_type", f"{path}.items")
    items = tuple(decode_message_page_item(item, f"{path}.items[{index}]") for index, item in enumerate(items_value))
    has_more = _require_bool(value["has_more"], f"{path}.has_more")
    next_cursor = _opt_str(value["next_cursor"], f"{path}.next_cursor")
    if has_more and next_cursor is None:
        _fail("invalid_cursor", f"{path}.next_cursor")
    return MessagePage(items=items, has_more=has_more, next_cursor=next_cursor)


@dataclass(frozen=True, slots=True)
class ArtifactPage:
    """成果首屏页（§4.2/§4.3 artifacts_page：分页上界 + 下一游标，不静默截断）。"""

    items: tuple[ArtifactPageItem, ...]
    has_more: bool
    next_cursor: str | None


_ARTIFACT_PAGE_KEYS: Final = frozenset({"items", "has_more", "next_cursor"})


def decode_artifact_page(value: object, path: str = "$.artifacts_page") -> ArtifactPage:
    """严格解码成果页（3 键全必填；has_more=True 必须携下一游标）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _ARTIFACT_PAGE_KEYS, path)
    items_value = value["items"]
    if not isinstance(items_value, list):
        _fail("invalid_type", f"{path}.items")
    items = tuple(decode_artifact_page_item(item, f"{path}.items[{index}]") for index, item in enumerate(items_value))
    has_more = _require_bool(value["has_more"], f"{path}.has_more")
    next_cursor = _opt_str(value["next_cursor"], f"{path}.next_cursor")
    if has_more and next_cursor is None:
        _fail("invalid_cursor", f"{path}.next_cursor")
    return ArtifactPage(items=items, has_more=has_more, next_cursor=next_cursor)


@dataclass(frozen=True, slots=True)
class SessionRead:
    """会话首读结果（§4.2 read_session 五要素；不复制完整成果正文，VM 按需加载）。"""

    summary: SessionSummary
    executions: tuple[ExecutionSummary, ...]
    messages: MessagePage
    artifacts: ArtifactPage
    decisions: tuple[DecisionSummary, ...]


_SESSION_READ_KEYS: Final = frozenset({"summary", "executions", "messages", "artifacts", "decisions"})


def decode_session_read(value: object, path: str = "$.session_read") -> SessionRead:
    """严格解码会话首读结果（5 键全必填）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SESSION_READ_KEYS, path)
    executions_value = value["executions"]
    decisions_value = value["decisions"]
    if not isinstance(executions_value, list):
        _fail("invalid_type", f"{path}.executions")
    if not isinstance(decisions_value, list):
        _fail("invalid_type", f"{path}.decisions")
    return SessionRead(
        summary=decode_session_summary(value["summary"], f"{path}.summary"),
        executions=tuple(
            decode_execution_summary(item, f"{path}.executions[{index}]") for index, item in enumerate(executions_value)
        ),
        messages=decode_message_page(value["messages"], f"{path}.messages"),
        artifacts=decode_artifact_page(value["artifacts"], f"{path}.artifacts"),
        decisions=tuple(
            decode_decision_summary(item, f"{path}.decisions[{index}]") for index, item in enumerate(decisions_value)
        ),
    )


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    """快照身份（§4.3 identity 行）：用户域/会话/执行/当前轮与准确 native workflow 引用。

    ``workflow_id``/``run_epoch`` 来自当前 round（§9.5）；排队执行无 round 时为
    None，且 round 存在必携 input_revision（round 行 NOT NULL）。
    """

    user_scope: str
    session_id: str
    execution_id: str
    node_id: str
    round_id: str | None
    input_revision: int | None
    workflow_id: str | None
    run_epoch: int | None


_SNAPSHOT_IDENTITY_KEYS: Final = frozenset(
    {
        "user_scope",
        "session_id",
        "execution_id",
        "node_id",
        "round_id",
        "input_revision",
        "workflow_id",
        "run_epoch",
    }
)


def decode_snapshot_identity(value: object, path: str = "$.identity") -> SnapshotIdentity:
    """严格解码快照身份（8 键全必填；round/workflow 耦合校验）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SNAPSHOT_IDENTITY_KEYS, path)
    round_id = _opt_str(value["round_id"], f"{path}.round_id")
    input_revision = _opt_int(value["input_revision"], f"{path}.input_revision", minimum=1)
    workflow_id = _opt_str(value["workflow_id"], f"{path}.workflow_id")
    run_epoch = _opt_int(value["run_epoch"], f"{path}.run_epoch", minimum=1)
    if (workflow_id is not None or run_epoch is not None) and round_id is None:
        _fail("identity_mismatch", f"{path}.round_id")
    if round_id is not None and input_revision is None:
        _fail("identity_mismatch", f"{path}.input_revision")
    return SnapshotIdentity(
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        round_id=round_id,
        input_revision=input_revision,
        workflow_id=workflow_id,
        run_epoch=run_epoch,
    )


@dataclass(frozen=True, slots=True)
class SourceReadSummary:
    """来源读取摘要（§4.3 source_reads 行）：source_link 行 + agent_asset_version join 元数据。"""

    link_id: str
    source_id: str
    source_version: str
    #: 来源类别；已清理（tombstoned）资产元数据被删除时为 None（未知，不猜默认值）。
    source_kind: str | None
    display_name: str
    digest: str
    read_state: ReadState
    verification_state: VerificationState
    consumer_kind: str
    consumer_id: str
    consumer_version: str
    relation_kind: str
    disabled_inherited: bool
    tombstoned: bool
    available_at: str | None
    disabled_at: str | None
    locator_payload: CanonicalJson | None
    claim_ref: str | None


_SOURCE_READ_SUMMARY_KEYS: Final = frozenset(
    {
        "link_id",
        "source_id",
        "source_version",
        "source_kind",
        "display_name",
        "digest",
        "read_state",
        "verification_state",
        "consumer_kind",
        "consumer_id",
        "consumer_version",
        "relation_kind",
        "disabled_inherited",
        "tombstoned",
        "available_at",
        "disabled_at",
        "locator_payload",
        "claim_ref",
    }
)


def decode_source_read_summary(value: object, path: str = "$.source_read") -> SourceReadSummary:
    """严格解码来源读取摘要（18 键全必填；source_kind 为 SourceKind 或清理后的 None）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SOURCE_READ_SUMMARY_KEYS, path)
    return SourceReadSummary(
        link_id=_require_str(value["link_id"], f"{path}.link_id"),
        source_id=_require_str(value["source_id"], f"{path}.source_id"),
        source_version=_require_str(value["source_version"], f"{path}.source_version"),
        source_kind=None
        if value["source_kind"] is None
        else _require_choice(value["source_kind"], SOURCE_KINDS, f"{path}.source_kind", "invalid_source_kind"),
        display_name=_require_str(value["display_name"], f"{path}.display_name"),
        digest=_require_digest(value["digest"], f"{path}.digest"),
        read_state=_require_enum(value["read_state"], ReadState, f"{path}.read_state", "invalid_read_state"),
        verification_state=_require_enum(
            value["verification_state"], VerificationState, f"{path}.verification_state", "invalid_verification_state"
        ),
        consumer_kind=_require_choice(
            value["consumer_kind"], SOURCE_CONSUMER_KINDS, f"{path}.consumer_kind", "invalid_consumer_kind"
        ),
        consumer_id=_require_str(value["consumer_id"], f"{path}.consumer_id"),
        consumer_version=_require_str(value["consumer_version"], f"{path}.consumer_version"),
        relation_kind=_require_str(value["relation_kind"], f"{path}.relation_kind"),
        disabled_inherited=_require_bool(value["disabled_inherited"], f"{path}.disabled_inherited"),
        tombstoned=_require_bool(value["tombstoned"], f"{path}.tombstoned"),
        available_at=_opt_timestamp(value["available_at"], f"{path}.available_at"),
        disabled_at=_opt_timestamp(value["disabled_at"], f"{path}.disabled_at"),
        locator_payload=_opt_json_text(value["locator_payload"], f"{path}.locator_payload"),
        claim_ref=_opt_str(value["claim_ref"], f"{path}.claim_ref"),
    )


@dataclass(frozen=True, slots=True)
class OperationSummary:
    """工具/IO 操作摘要（§4.3 operation_summaries 行）：phase/effect/io 分列。

    工具返回用 ``result_ref`` 引用（完整返回经 read_operation 按需读取）；
    ``io_state``/``error_category`` 为未冻结 wire 值（C-01 留痕），strict str。
    ``invocation_id``/``attempt_refs`` 为原模型调度账本引用（ADR-0014：模型
    attempt 信息只经引用关联原账本，不重复写费用），费用经
    ``AgentService.read_cost_projection`` 只读投影；引用形状未冻结，
    ``attempt_refs`` 以 CanonicalJson 原样承载（不臆造结构）。
    """

    operation_id: str
    phase: str
    effect_state: EffectState
    io_state: str
    owner_epoch: int
    run_epoch: int
    active_ms: int
    elapsed_ms: int
    session_id: str | None
    execution_id: str | None
    round_id: str | None
    tool_call_id: str | None
    grant_id: str | None
    grant_revision: int | None
    result_ref: str | None
    error_code: str | None
    error_category: str | None
    deadline_at: str | None
    dispatched_at: str | None
    exited_at: str | None
    verified_at: str | None
    artifact_id: str | None
    artifact_version: int | None
    invocation_id: str | None
    attempt_refs: CanonicalJson | None


_OPERATION_SUMMARY_KEYS: Final = frozenset(
    {
        "operation_id",
        "phase",
        "effect_state",
        "io_state",
        "owner_epoch",
        "run_epoch",
        "active_ms",
        "elapsed_ms",
        "session_id",
        "execution_id",
        "round_id",
        "tool_call_id",
        "grant_id",
        "grant_revision",
        "result_ref",
        "error_code",
        "error_category",
        "deadline_at",
        "dispatched_at",
        "exited_at",
        "verified_at",
        "artifact_id",
        "artifact_version",
        "invocation_id",
        "attempt_refs",
    }
)


def decode_operation_summary(value: object, path: str = "$.operation_summary") -> OperationSummary:
    """严格解码操作摘要（25 键全必填；值集与 §9.9 CHECK 同步）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _OPERATION_SUMMARY_KEYS, path)
    artifact_id = _opt_str(value["artifact_id"], f"{path}.artifact_id")
    artifact_version = _opt_int(value["artifact_version"], f"{path}.artifact_version", minimum=1)
    if (artifact_id is None) != (artifact_version is None):
        _fail("identity_mismatch", f"{path}.artifact_version")
    return OperationSummary(
        operation_id=_require_str(value["operation_id"], f"{path}.operation_id"),
        phase=_require_choice(value["phase"], OPERATION_PHASES, f"{path}.phase", "invalid_phase"),
        effect_state=_require_enum(value["effect_state"], EffectState, f"{path}.effect_state", "invalid_effect_state"),
        io_state=_require_str(value["io_state"], f"{path}.io_state"),
        owner_epoch=_nonneg_int(value["owner_epoch"], f"{path}.owner_epoch"),
        run_epoch=_nonneg_int(value["run_epoch"], f"{path}.run_epoch"),
        active_ms=_nonneg_int(value["active_ms"], f"{path}.active_ms"),
        elapsed_ms=_nonneg_int(value["elapsed_ms"], f"{path}.elapsed_ms"),
        session_id=_opt_str(value["session_id"], f"{path}.session_id"),
        execution_id=_opt_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_opt_str(value["round_id"], f"{path}.round_id"),
        tool_call_id=_opt_str(value["tool_call_id"], f"{path}.tool_call_id"),
        grant_id=_opt_str(value["grant_id"], f"{path}.grant_id"),
        grant_revision=_opt_int(value["grant_revision"], f"{path}.grant_revision", minimum=0),
        result_ref=_opt_str(value["result_ref"], f"{path}.result_ref"),
        error_code=_opt_str(value["error_code"], f"{path}.error_code"),
        error_category=_opt_str(value["error_category"], f"{path}.error_category"),
        deadline_at=_opt_timestamp(value["deadline_at"], f"{path}.deadline_at"),
        dispatched_at=_opt_timestamp(value["dispatched_at"], f"{path}.dispatched_at"),
        exited_at=_opt_timestamp(value["exited_at"], f"{path}.exited_at"),
        verified_at=_opt_timestamp(value["verified_at"], f"{path}.verified_at"),
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        invocation_id=_opt_str(value["invocation_id"], f"{path}.invocation_id"),
        attempt_refs=_opt_json_text(value["attempt_refs"], f"{path}.attempt_refs"),
    )


#: §4.3 usage 费用状态四态（ADR-0014 原账本口径，保真不合并）：
#: ``known`` = 引用到的 attempt 全部已计价（含已知零费用）；
#: ``unpriced`` = 存在 cost 为 NULL 或引用到却无账本行的 attempt（未知成本）；
#: ``reserved`` = 已计价但仍有活跃预留未结算（金额已预留、结果未定）；
#: ``unknown`` = 无引用/引用不可读/账本无可计价行，未确认（禁止伪装为零，R21）。
COST_STATE_KNOWN: Final = "known"
COST_STATE_UNPRICED: Final = "unpriced"
COST_STATE_RESERVED: Final = "reserved"
COST_STATE_UNKNOWN: Final = "unknown"
COST_STATES: Final = frozenset({COST_STATE_KNOWN, COST_STATE_UNPRICED, COST_STATE_RESERVED, COST_STATE_UNKNOWN})


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """用量摘要（§4.3 usage 行）：实际 attempt/tool/active 累计 + 账本费用状态。

    ``cost_state`` 按原账本（``ai_invocation`` / ``ai_attempt``）如实投影
    known/unpriced/reserved/unknown（见 :data:`COST_STATES`）；读取不到证明时保持
    unknown，不填 0 冒认免费（R21）。费用金额经 ``AgentService.read_cost_projection``
    以 operation 行的 ``invocation_id`` / ``attempt_refs`` 引用原账本只读投影，
    本摘要不重复承载金额，只承载聚合状态。
    """

    model_attempt_count: int
    tool_call_count: int
    active_ms: int
    cost_state: str = COST_STATE_UNKNOWN


_USAGE_SUMMARY_KEYS: Final = frozenset({"model_attempt_count", "tool_call_count", "active_ms", "cost_state"})


def decode_usage_summary(value: object, path: str = "$.usage") -> UsageSummary:
    """严格解码用量摘要（4 键全必填；cost_state ∈ :data:`COST_STATES`）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _USAGE_SUMMARY_KEYS, path)
    cost_state = _require_choice(value["cost_state"], COST_STATES, f"{path}.cost_state", "invalid_cost_state")
    return UsageSummary(
        model_attempt_count=_nonneg_int(value["model_attempt_count"], f"{path}.model_attempt_count"),
        tool_call_count=_nonneg_int(value["tool_call_count"], f"{path}.tool_call_count"),
        active_ms=_nonneg_int(value["active_ms"], f"{path}.active_ms"),
        cost_state=cost_state,
    )


#: 费用投影的读取状态（读取失败独立成态，不得合并进「零费用」，R21）
COST_PROJECTION_READ_STATES: Final = frozenset({"ok", "failed"})


@dataclass(frozen=True, slots=True)
class CostProjection:
    """执行费用的只读投影（§4.3 usage 费用面；ADR-0014 引用原账本，不重复写费用）。

    由 ``AgentService.read_cost_projection`` 以 operation 行的 ``invocation_id`` /
    ``attempt_refs`` 引用原账本（``ai_invocation`` / ``ai_attempt``）只读聚合；
    不写库、不估算补齐。五种状态分列保真，禁止合并成一个总数：

    - **读取失败**：``read_state="failed"`` + ``read_error``（错误分类码），其余
      金额/计数保持缺省，不倒推为「零费用」（未知 ≠ 0）；
    - **0（确实为零）**：``zero_cost_attempts`` 为已计价且金额恰为 0 的 attempt 数，
      此时 ``known_cny`` 可为 ``Decimal("0")``（已知零，与未知可区分）；
    - **已知金额**：``known_cny`` 为已计价 attempt 金额合计；无任何已计价 attempt
      时为 ``None``（未知，禁止填 0）；
    - **预留中**：``reserved_cny`` 为活跃预留（reserved / dispatched /
      held_unconfirmed）金额合计；账本无活跃预留为 ``Decimal("0")``（事实零），
      无引用可投影时为 ``None``；
    - **未计价**：``unpriced_attempts``（cost 为 NULL，或引用存在但账本无行）与
      对应 ``unpriced_input_tokens`` / ``unpriced_output_tokens``（未知成本的用量规模）。

    ``ledger_refs`` / ``attempt_refs`` 去重保序回传，引用不因读取失败或账本缺行丢失。
    """

    read_state: str
    read_error: str | None
    ledger_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    zero_cost_attempts: int
    known_cny: Decimal | None
    reserved_cny: Decimal | None
    unpriced_attempts: int
    unpriced_input_tokens: int
    unpriced_output_tokens: int


@dataclass(frozen=True, slots=True)
class ReadMarkEntry:
    """阅读水位标记投影（§4.3 read_marks 行：last/batch/postponed 三单调水位，R22）。"""

    marker_id: str
    marker_kind: str
    target_id: str
    last_seq: int
    batch_seq: int
    postponed_through_seq: int
    session_id: str | None
    read_targets: tuple[str, ...] | None


_READ_MARK_ENTRY_KEYS: Final = frozenset(
    {
        "marker_id",
        "marker_kind",
        "target_id",
        "last_seq",
        "batch_seq",
        "postponed_through_seq",
        "session_id",
        "read_targets",
    }
)


def decode_read_mark_entry(value: object, path: str = "$.read_mark") -> ReadMarkEntry:
    """严格解码阅读水位标记（8 键全必填；read_targets 为精确已读 ID 或 None）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _READ_MARK_ENTRY_KEYS, path)
    read_targets_value = value["read_targets"]
    read_targets: tuple[str, ...] | None
    if read_targets_value is None:
        read_targets = None
    else:
        if not isinstance(read_targets_value, list):
            _fail("invalid_type", f"{path}.read_targets")
        read_targets = tuple(
            _require_str(item, f"{path}.read_targets[{index}]") for index, item in enumerate(read_targets_value)
        )
    return ReadMarkEntry(
        marker_id=_require_str(value["marker_id"], f"{path}.marker_id"),
        marker_kind=_require_str(value["marker_kind"], f"{path}.marker_kind"),
        target_id=_require_str(value["target_id"], f"{path}.target_id"),
        last_seq=_nonneg_int(value["last_seq"], f"{path}.last_seq"),
        batch_seq=_nonneg_int(value["batch_seq"], f"{path}.batch_seq"),
        postponed_through_seq=_nonneg_int(value["postponed_through_seq"], f"{path}.postponed_through_seq"),
        session_id=_opt_str(value["session_id"], f"{path}.session_id"),
        read_targets=read_targets,
    )


@dataclass(frozen=True, slots=True)
class Observation:
    """观测状态（§4.3 observation 行）：当前查询是否可靠、最后核对时刻；unknown 不冒充可靠。"""

    reliable: bool
    last_verified_at: str | None


_OBSERVATION_KEYS: Final = frozenset({"reliable", "last_verified_at"})


def decode_observation(value: object, path: str = "$.observation") -> Observation:
    """严格解码观测状态（2 键全必填）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _OBSERVATION_KEYS, path)
    return Observation(
        reliable=_require_bool(value["reliable"], f"{path}.reliable"),
        last_verified_at=_opt_timestamp(value["last_verified_at"], f"{path}.last_verified_at"),
    )


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    """执行快照（§4.3 完整结构逐行映射）：

    identity→:class:`SnapshotIdentity`；revision/双水位/updated_at→同一可靠读取
    截点（水位是已提交事件，不是 SDK delta 计数）；execution/nodes→
    :class:`ExecutionSummary`（一层叶子；dependencies 由 actual_input 前序引用
    承载——agent_execution 无依赖边表，故无独立 dependencies 字段）；
    frozen_scope/actual_input→round JSONB 规范化 CanonicalJson 文本（结构未冻结
    不臆造）；messages_page/artifacts_page→分页上界；source_reads/decisions/
    operation_summaries→行投影；usage→:class:`UsageSummary`（cost_state 按原账本
    投影 known/unpriced/reserved/unknown，无证明保 unknown 不填 0）；goal_coverage/
    limitations→覆盖值集与脱敏限制记录（空元组=本次读取未核定到条目，不等于
    「无限制」承诺）；read_marks/observation→阅读水位与可靠性。
    """

    identity: SnapshotIdentity
    revision: int
    execution_watermark: int
    session_watermark: int
    updated_at: str
    execution: ExecutionSummary
    nodes: tuple[ExecutionSummary, ...]
    frozen_scope: CanonicalJson | None
    actual_input: CanonicalJson | None
    messages_page: MessagePage
    artifacts_page: ArtifactPage
    source_reads: tuple[SourceReadSummary, ...]
    decisions: tuple[DecisionSummary, ...]
    operation_summaries: tuple[OperationSummary, ...]
    usage: UsageSummary
    goal_coverage: str
    limitations: tuple[RangeLimitation, ...]
    read_marks: tuple[ReadMarkEntry, ...]
    observation: Observation


_RESEARCH_SNAPSHOT_KEYS: Final = frozenset(
    {
        "identity",
        "revision",
        "execution_watermark",
        "session_watermark",
        "updated_at",
        "execution",
        "nodes",
        "frozen_scope",
        "actual_input",
        "messages_page",
        "artifacts_page",
        "source_reads",
        "decisions",
        "operation_summaries",
        "usage",
        "goal_coverage",
        "limitations",
        "read_marks",
        "observation",
    }
)


def _decode_tuple_of(
    value: object,
    path: str,
    decode_item: Any,
) -> tuple[Any, ...]:
    if not isinstance(value, list):
        _fail("invalid_type", path)
    return tuple(decode_item(item, f"{path}[{index}]") for index, item in enumerate(value))


def _validate_snapshot_object_graph(snapshot: ResearchSnapshot, path: str) -> None:
    """快照对象图身份一致性（identity ↔ execution/nodes/messages/decisions/operations）。

    producer 只按 identity 读取对象图；wire 重读时任一子行身份与 identity 分叉即
    「对象身份错误」（同一快照描述两个不同会话/执行），不静默接受。同源重复投影
    （revision/执行水位/goal_coverage/updated_at）分叉同样拒绝——它们由同一行派生，
    不一致即证明子行并非 identity 指向的对象（检视 R19）。
    """

    identity = snapshot.identity
    execution = snapshot.execution
    if (
        execution.user_scope != identity.user_scope
        or execution.session_id != identity.session_id
        or execution.execution_id != identity.execution_id
        or execution.node_id != identity.node_id
    ):
        _fail("identity_mismatch", f"{path}.execution")
    if identity.round_id is None and execution.current_round_id is not None:
        _fail("identity_mismatch", f"{path}.execution.current_round_id")
    if snapshot.revision != execution.revision:
        _fail("identity_mismatch", f"{path}.revision")
    if snapshot.execution_watermark != execution.event_seq:
        _fail("identity_mismatch", f"{path}.execution_watermark")
    if snapshot.goal_coverage != execution.goal_coverage:
        _fail("identity_mismatch", f"{path}.goal_coverage")
    if snapshot.updated_at != execution.updated_at:
        _fail("identity_mismatch", f"{path}.updated_at")
    for index, node in enumerate(snapshot.nodes):
        if (
            node.user_scope != identity.user_scope
            or node.session_id != identity.session_id
            or node.parent_execution_id != identity.execution_id
        ):
            _fail("identity_mismatch", f"{path}.nodes[{index}]")
    for index, item in enumerate(snapshot.messages_page.items):
        if item.session_id != identity.session_id:
            _fail("identity_mismatch", f"{path}.messages_page.items[{index}]")
        if item.execution_id is not None and item.execution_id != identity.execution_id:
            _fail("identity_mismatch", f"{path}.messages_page.items[{index}]")
        if identity.round_id is not None and item.round_id is not None and item.round_id != identity.round_id:
            _fail("identity_mismatch", f"{path}.messages_page.items[{index}]")
    for index, decision in enumerate(snapshot.decisions):
        if decision.session_id != identity.session_id or decision.execution_id != identity.execution_id:
            _fail("identity_mismatch", f"{path}.decisions[{index}]")
    for index, operation in enumerate(snapshot.operation_summaries):
        if operation.execution_id != identity.execution_id:
            _fail("identity_mismatch", f"{path}.operation_summaries[{index}]")


def decode_research_snapshot(value: object, path: str = "$.snapshot") -> ResearchSnapshot:
    """严格解码执行快照（19 键全必填禁额外键；嵌套递归 strict）。

    解码后核对对象图身份一致性（identity 与执行/叶/消息/决定/操作子行同一对象，
    检视 R19），分叉即拒绝。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _RESEARCH_SNAPSHOT_KEYS, path)
    snapshot = ResearchSnapshot(
        identity=decode_snapshot_identity(value["identity"], f"{path}.identity"),
        revision=cast(int, require_revision(value["revision"], f"{path}.revision")),
        execution_watermark=_nonneg_int(value["execution_watermark"], f"{path}.execution_watermark"),
        session_watermark=_nonneg_int(value["session_watermark"], f"{path}.session_watermark"),
        updated_at=_require_utc_timestamp(value["updated_at"], f"{path}.updated_at"),
        execution=decode_execution_summary(value["execution"], f"{path}.execution"),
        nodes=_decode_tuple_of(value["nodes"], f"{path}.nodes", decode_execution_summary),
        frozen_scope=_opt_json_text(value["frozen_scope"], f"{path}.frozen_scope"),
        actual_input=_opt_json_text(value["actual_input"], f"{path}.actual_input"),
        messages_page=decode_message_page(value["messages_page"], f"{path}.messages_page"),
        artifacts_page=decode_artifact_page(value["artifacts_page"], f"{path}.artifacts_page"),
        source_reads=_decode_tuple_of(value["source_reads"], f"{path}.source_reads", decode_source_read_summary),
        decisions=_decode_tuple_of(value["decisions"], f"{path}.decisions", decode_decision_summary),
        operation_summaries=_decode_tuple_of(
            value["operation_summaries"], f"{path}.operation_summaries", decode_operation_summary
        ),
        usage=decode_usage_summary(value["usage"], f"{path}.usage"),
        goal_coverage=_require_choice(
            value["goal_coverage"], GOAL_COVERAGES, f"{path}.goal_coverage", "invalid_goal_coverage"
        ),
        limitations=_decode_tuple_of(value["limitations"], f"{path}.limitations", decode_range_limitation),
        read_marks=_decode_tuple_of(value["read_marks"], f"{path}.read_marks", decode_read_mark_entry),
        observation=decode_observation(value["observation"], f"{path}.observation"),
    )
    _validate_snapshot_object_graph(snapshot, path)
    return snapshot


# ---------------------------------------------------------------------------
# §4.2 R02 历史查询合同（C-03-3a：HistoryQuery/HistoryHit/HistoryPage）
# ---------------------------------------------------------------------------

#: §4.2 L431/L406-408 规范化裁决（工程冻结，值集留痕）：
#: - scope 三范围：all_saved（不带局部目标）/current_session（必带主 session_id）/
#:   current_child（必带主 session_id + child_target）；
#: - round_selection 必填：all_rounds 或 exact{execution_id, round_id}；
#: - agent_filter 两 kind 不互换：role_identity{role_id, version_selection,
#:   role_version?} / execution_agent{execution_id, node_id, round_id}；
#: - type_filter 仅为 main/child（main=root 执行 + 会话级消息；child=leaf/
#:   comparison_lane，一律按 ``agent_execution.execution_kind`` 权威判定，不以
#:   execution_id 是否为空代替，检视 D17）；message_role_filter 仅为 user/assistant/tool/
#:   public_status；state_filter 值集未冻结（strict str，留痕不臆造）；
#: - time_basis=created/updated/message；sort=oldest_first/newest_first（方向独立
#:   维度，工程冻结）；
#: - 全部条件、空值含义、版本选择、规范关键词和准确祖先进入 query_digest；
#: - HistoryPage 修订指纹为 sha256 摘要（覆盖域成员/水位经 DAO CanonicalJson
#:   组装后哈希）；游标 h:{查询摘要}:{上界ts}:{排序键ts}:{命中类型}:{锚ID}:{版本}
#:   （摘要绑定规范查询身份、上界绑定翻页；命中类型与版本承载历史搜索全序键的
#:   后两段，见 DAO ``search_history``；换筛选/换范围复用游标由 DAO 拒绝）。

HISTORY_SCOPES: Final[frozenset[str]] = frozenset({"all_saved", "current_session", "current_child"})
ROUND_SELECTION_KINDS: Final[frozenset[str]] = frozenset({"all_rounds", "exact"})
AGENT_FILTER_KINDS: Final[frozenset[str]] = frozenset({"role_identity", "execution_agent"})
VERSION_SELECTIONS: Final[frozenset[str]] = frozenset({"all_versions", "exact"})
TYPE_FILTERS: Final[frozenset[str]] = frozenset({"main", "child"})
MESSAGE_ROLE_FILTERS: Final[frozenset[str]] = frozenset({"user", "assistant", "tool", "public_status"})
HISTORY_TIME_BASES: Final[frozenset[str]] = frozenset({"created", "updated", "message"})
HISTORY_SORTS: Final[frozenset[str]] = frozenset({"oldest_first", "newest_first"})
HISTORY_HIT_KINDS: Final[frozenset[str]] = frozenset({"message", "artifact"})

_DATE_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_HISTORY_CURSOR_RE: Final = re.compile(
    r"h:([0-9a-f]{64}):(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z):"
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z):(message|artifact):([^:\s]+):(0|[1-9][0-9]*)"
)


@dataclass(frozen=True, slots=True)
class HistoryChildTarget:
    """current_child 范围的准确叶目标（§4.2 L406）：execution_id 为准确叶。"""

    parent_execution_id: str
    execution_id: str
    node_id: str


_HISTORY_CHILD_TARGET_KEYS: Final = frozenset({"parent_execution_id", "execution_id", "node_id"})


def decode_history_child_target(value: object, path: str = "$.child_target") -> HistoryChildTarget:
    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _HISTORY_CHILD_TARGET_KEYS, path)
    parent_execution_id = _require_str(value["parent_execution_id"], f"{path}.parent_execution_id")
    execution_id = _require_str(value["execution_id"], f"{path}.execution_id")
    if execution_id == parent_execution_id:
        _fail("identity_mismatch", f"{path}.execution_id")
    return HistoryChildTarget(
        parent_execution_id=parent_execution_id,
        execution_id=execution_id,
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
    )


@dataclass(frozen=True, slots=True)
class HistoryRoundSelection:
    """轮次选择（§4.2 L406）：all_rounds 或 exact{execution_id, round_id}。"""

    kind: str
    execution_id: str | None = None
    round_id: str | None = None


def decode_history_round_selection(value: object, path: str = "$.round_selection") -> HistoryRoundSelection:
    """轮次选择解码：all_rounds 无其余键、exact 必携双 ID（identity_mismatch）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    if not isinstance(value.get("kind"), str) or value["kind"] not in ROUND_SELECTION_KINDS:
        _fail("invalid_round_selection_kind", f"{path}.kind")
    if value["kind"] == "all_rounds":
        _require_keys(value, frozenset({"kind"}), path)
        return HistoryRoundSelection(kind="all_rounds")
    _require_keys(value, frozenset({"kind", "execution_id", "round_id"}), path)
    return HistoryRoundSelection(
        kind="exact",
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
    )


@dataclass(frozen=True, slots=True)
class HistoryAgentFilter:
    """历史 Agent 过滤（§4.2 L408）：两 kind 不以显示名/模型名/消息 role 互换。"""

    kind: str
    role_id: str | None = None
    version_selection: str | None = None
    role_version: str | None = None
    execution_id: str | None = None
    node_id: str | None = None
    round_id: str | None = None


def decode_history_agent_filter(value: object, path: str = "$.agent_filter") -> HistoryAgentFilter:
    """Agent 过滤解码：kind 分支键集互斥（role_identity/execution_agent）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    if not isinstance(value.get("kind"), str) or value["kind"] not in AGENT_FILTER_KINDS:
        _fail("invalid_agent_filter_kind", f"{path}.kind")
    if value["kind"] == "role_identity":
        _require_keys_with_optional(
            value, frozenset({"kind", "role_id", "version_selection"}), frozenset({"role_version"}), path
        )
        role_id = _require_str(value["role_id"], f"{path}.role_id")
        version_selection = _require_choice(
            value["version_selection"], VERSION_SELECTIONS, f"{path}.version_selection", "invalid_version_selection"
        )
        role_version = _opt_str(value.get("role_version"), f"{path}.role_version")
        if version_selection == "exact" and role_version is None:
            _fail("identity_mismatch", f"{path}.role_version")
        if version_selection == "all_versions" and role_version is not None:
            _fail("identity_mismatch", f"{path}.role_version")
        return HistoryAgentFilter(
            kind="role_identity",
            role_id=role_id,
            version_selection=version_selection,
            role_version=role_version,
        )
    _require_keys(value, frozenset({"kind", "execution_id", "node_id", "round_id"}), path)
    return HistoryAgentFilter(
        kind="execution_agent",
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
    )


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    """历史查询（§4.2 L431 全字段；R02 范围规则构造后即校验）。

    ``query_text`` 必填（空串=无关键词过滤，空值含义入 digest）；``state_filter``
    值集未冻结（strict str|None，留痕）。日期过滤为 UTC 日期文本（含当天）。
    """

    scope: str
    round_selection: HistoryRoundSelection
    query_text: str
    time_basis: str
    sort: str
    limit: int = 50
    session_id: str | None = None
    child_target: HistoryChildTarget | None = None
    agent_filter: HistoryAgentFilter | None = None
    message_role_filter: str | None = None
    type_filter: str | None = None
    state_filter: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    cursor: str | None = None


_HISTORY_QUERY_KEYS: Final = frozenset(
    {
        "scope",
        "round_selection",
        "query_text",
        "time_basis",
        "sort",
        "limit",
        "session_id",
        "child_target",
        "agent_filter",
        "message_role_filter",
        "type_filter",
        "state_filter",
        "date_from",
        "date_to",
        "cursor",
    }
)


def validate_history_query(query: HistoryQuery) -> None:
    """HistoryQuery 结构校验（R02 范围规则 + 值集 + 游标语法）。"""

    if query.scope not in HISTORY_SCOPES:
        _fail("invalid_scope", "$.scope")
    if isinstance(query.limit, bool) or not isinstance(query.limit, int):
        _fail("invalid_type", "$.limit")
    if not 1 <= query.limit <= 50:
        _fail("invalid_range", "$.limit")
    if not isinstance(query.query_text, str):
        _fail("invalid_type", "$.query_text")
    if query.time_basis not in HISTORY_TIME_BASES:
        _fail("invalid_time_basis", "$.time_basis")
    if query.sort not in HISTORY_SORTS:
        _fail("invalid_sort", "$.sort")
    if query.session_id is not None:
        _require_str(query.session_id, "$.session_id")
    if query.message_role_filter is not None and query.message_role_filter not in MESSAGE_ROLE_FILTERS:
        _fail("invalid_message_role_filter", "$.message_role_filter")
    if query.type_filter is not None and query.type_filter not in TYPE_FILTERS:
        _fail("invalid_type_filter", "$.type_filter")
    if query.state_filter is not None and (not isinstance(query.state_filter, str) or query.state_filter == ""):
        _fail("invalid_range", "$.state_filter")
    for field in ("date_from", "date_to"):
        value = getattr(query, field)
        if value is not None and (not isinstance(value, str) or _DATE_RE.fullmatch(value) is None):
            _fail("invalid_date", f"$.{field}")
    if query.date_from is not None and query.date_to is not None and query.date_from > query.date_to:
        _fail("invalid_range", "$.date_to")
    if query.cursor is not None:
        if not isinstance(query.cursor, str) or _HISTORY_CURSOR_RE.fullmatch(query.cursor) is None:
            _fail("invalid_cursor", "$.cursor")
        # 正则放过任意非负版本号，语义收紧（命中类型/版本配对）在同源解析里做
        parse_history_cursor(query.cursor)
    # 复用 wire 严格解码做内存对象复核：kind 原样透传（不做 else 兜底），
    # 未知 kind 即使携合法 ID 也在受理阶段拒绝（D15：不放大搜索范围）。
    round_selection_value: dict[str, object] = {"kind": query.round_selection.kind}
    if query.round_selection.kind == "exact":
        round_selection_value["execution_id"] = query.round_selection.execution_id
        round_selection_value["round_id"] = query.round_selection.round_id
    decode_history_round_selection(round_selection_value, "$.round_selection")
    if query.agent_filter is not None:
        agent_filter = query.agent_filter
        agent_filter_value: dict[str, object] = {"kind": agent_filter.kind}
        if agent_filter.kind == "role_identity":
            agent_filter_value["role_id"] = agent_filter.role_id
            agent_filter_value["version_selection"] = agent_filter.version_selection
            agent_filter_value["role_version"] = agent_filter.role_version
        elif agent_filter.kind == "execution_agent":
            agent_filter_value["execution_id"] = agent_filter.execution_id
            agent_filter_value["node_id"] = agent_filter.node_id
            agent_filter_value["round_id"] = agent_filter.round_id
        decode_history_agent_filter(agent_filter_value, "$.agent_filter")
    # R02 范围规则（L406）
    if query.scope == "all_saved":
        if query.session_id is not None or query.child_target is not None:
            _fail("identity_mismatch", "$.session_id")
    elif query.scope == "current_session":
        if query.session_id is None:
            _fail("identity_mismatch", "$.session_id")
        if query.child_target is not None:
            _fail("identity_mismatch", "$.child_target")
    else:  # current_child
        if query.session_id is None or query.child_target is None:
            _fail("identity_mismatch", "$.child_target")
        if (
            query.round_selection.kind == "exact"
            and query.round_selection.execution_id != query.child_target.execution_id
        ):
            _fail("identity_mismatch", "$.round_selection")


def decode_history_query(value: object, path: str = "$.history_query") -> HistoryQuery:
    """严格解码历史查询（15 键全必填禁额外键；构造后即 R02 校验）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _HISTORY_QUERY_KEYS, path)
    limit = value["limit"]
    if isinstance(limit, bool) or not isinstance(limit, int):
        _fail("invalid_type", f"{path}.limit")
    child_target_value = value["child_target"]
    agent_filter_value = value["agent_filter"]
    message_role_value = value["message_role_filter"]
    type_filter_value = value["type_filter"]
    query = HistoryQuery(
        scope=_require_choice(value["scope"], HISTORY_SCOPES, f"{path}.scope", "invalid_scope"),
        round_selection=decode_history_round_selection(value["round_selection"], f"{path}.round_selection"),
        query_text=_require_text(value["query_text"], f"{path}.query_text"),
        time_basis=_require_choice(value["time_basis"], HISTORY_TIME_BASES, f"{path}.time_basis", "invalid_time_basis"),
        sort=_require_choice(value["sort"], HISTORY_SORTS, f"{path}.sort", "invalid_sort"),
        limit=limit,
        session_id=_opt_str(value["session_id"], f"{path}.session_id"),
        child_target=(
            decode_history_child_target(child_target_value, f"{path}.child_target")
            if child_target_value is not None
            else None
        ),
        agent_filter=(
            decode_history_agent_filter(agent_filter_value, f"{path}.agent_filter")
            if agent_filter_value is not None
            else None
        ),
        message_role_filter=(
            None
            if message_role_value is None
            else _require_choice(
                message_role_value, MESSAGE_ROLE_FILTERS, f"{path}.message_role_filter", "invalid_message_role_filter"
            )
        ),
        type_filter=(
            None
            if type_filter_value is None
            else _require_choice(type_filter_value, TYPE_FILTERS, f"{path}.type_filter", "invalid_type_filter")
        ),
        state_filter=_opt_text(value["state_filter"], f"{path}.state_filter"),
        date_from=_opt_str(value["date_from"], f"{path}.date_from"),
        date_to=_opt_str(value["date_to"], f"{path}.date_to"),
        cursor=_opt_cursor(value["cursor"], f"{path}.cursor"),
    )
    validate_history_query(query)
    return query


def normalize_history_query(query: HistoryQuery) -> CanonicalJson:
    """规范化查询 wire（全部条件/空值含义入 digest，§4.2 L408）。

    条件缺省一律显式 ``null``（空值含义不因省略键而歧义）；round_selection/
    agent_filter 展开为全键 wire。游标与 limit 不入 digest（同一查询翻页共享摘要）。
    """

    selection: dict[str, Any] = {"kind": query.round_selection.kind}
    if query.round_selection.kind == "exact":
        selection["execution_id"] = query.round_selection.execution_id
        selection["round_id"] = query.round_selection.round_id
    agent_filter: dict[str, Any] | None = None
    if query.agent_filter is not None:
        agent_filter = {"kind": query.agent_filter.kind}
        if query.agent_filter.kind == "role_identity":
            agent_filter["role_id"] = query.agent_filter.role_id
            agent_filter["version_selection"] = query.agent_filter.version_selection
            agent_filter["role_version"] = query.agent_filter.role_version
        else:
            agent_filter["execution_id"] = query.agent_filter.execution_id
            agent_filter["node_id"] = query.agent_filter.node_id
            agent_filter["round_id"] = query.agent_filter.round_id
    return json_doc(
        {
            "scope": query.scope,
            "session_id": query.session_id,
            "child_target": to_wire_dict(query.child_target) if query.child_target is not None else None,
            "round_selection": selection,
            "query_text": query.query_text,
            "agent_filter": agent_filter,
            "message_role_filter": query.message_role_filter,
            "type_filter": query.type_filter,
            "state_filter": query.state_filter,
            "date_from": query.date_from,
            "date_to": query.date_to,
            "time_basis": query.time_basis,
            "sort": query.sort,
        }
    )


def compute_history_query_digest(query: HistoryQuery) -> str:
    """``sha256(CanonicalJson(规范化查询))``：游标/翻页/指纹绑定该摘要（L408）。"""

    return sha256_hex(normalize_history_query(query))


def parse_history_cursor(cursor: str) -> tuple[str, str, str, str, str, int]:
    """游标 ``h:{摘要}:{上界ts}:{排序键ts}:{命中类型}:{锚ID}:{版本}`` → 六段。

    返回 (查询摘要, 固定上界, 排序键时刻, 命中类型, 锚 ID, 版本)，其中命中类型为
    ``message`` / ``artifact``，版本对消息固定为 0、对成果为该成果版本号（>=1）。
    语法已由 validate_history_query 冻结；摘要绑定规范查询身份（DAO 续读前
    必须与当前查询摘要比对，不一致即拒绝换筛选复用），上界供 DAO 组装
    keyset 续读；命中类型与版本是历史搜索全序键
    ``(sort_moment, hit_kind, anchor_id, artifact_version)`` 的后两段，DAO 续读
    必须用同一套分量比较，否则同 ID 多版本同时间会重复或漏页。
    """

    match = _HISTORY_CURSOR_RE.fullmatch(cursor)
    if match is None:
        _fail("invalid_cursor", "$.cursor")
    hit_kind = match.group(4)
    version = int(match.group(6))
    # 正则放过任意非负整数，语义在此收紧：消息无版本（恒 0）、成果版本 >= 1，
    # 组合不成立即拒绝（不留可解析但语义错位的游标）。
    if (hit_kind == "message") != (version == 0):
        _fail("invalid_cursor", "$.cursor")
    return match.group(1), match.group(2), match.group(3), hit_kind, match.group(5), version


@dataclass(frozen=True, slots=True)
class HistoryHit:
    """历史命中（§4.2 L442）：命中摘要 + session/execution/node/round/message
    或 artifact 准确地址；``sort_moment`` 为排序键时刻（翻页 keyset 键值）。"""

    hit_kind: str
    session_id: str
    sort_moment: str
    created_at: str
    session_seq: int | None
    execution_id: str | None
    node_id: str | None
    round_id: str | None
    message_id: str | None
    artifact_id: str | None
    artifact_version: int | None
    role: str | None
    payload_kind: str | None
    artifact_kind: str | None
    summary_text: str | None


_HISTORY_HIT_KEYS: Final = frozenset(
    {
        "hit_kind",
        "session_id",
        "sort_moment",
        "created_at",
        "session_seq",
        "execution_id",
        "node_id",
        "round_id",
        "message_id",
        "artifact_id",
        "artifact_version",
        "role",
        "payload_kind",
        "artifact_kind",
        "summary_text",
    }
)


def decode_history_hit(value: object, path: str = "$.hits") -> HistoryHit:
    """严格解码历史命中（15 键全必填；message/artifact 地址组互斥归属）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _HISTORY_HIT_KEYS, path)
    hit_kind = _require_choice(value["hit_kind"], HISTORY_HIT_KINDS, f"{path}.hit_kind", "invalid_hit_kind")
    message_id = _opt_str(value["message_id"], f"{path}.message_id")
    artifact_id = _opt_str(value["artifact_id"], f"{path}.artifact_id")
    artifact_version = _opt_int(value["artifact_version"], f"{path}.artifact_version", minimum=1)
    if hit_kind == "message":
        if message_id is None or artifact_id is not None or artifact_version is not None:
            _fail("identity_mismatch", f"{path}.message_id")
        if _opt_int(value["session_seq"], f"{path}.session_seq", minimum=0) is None:
            _fail("identity_mismatch", f"{path}.session_seq")
    else:
        if artifact_id is None or artifact_version is None or message_id is not None:
            _fail("identity_mismatch", f"{path}.artifact_id")
    return HistoryHit(
        hit_kind=hit_kind,
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        sort_moment=_require_utc_timestamp(value["sort_moment"], f"{path}.sort_moment"),
        created_at=_require_utc_timestamp(value["created_at"], f"{path}.created_at"),
        session_seq=_opt_int(value["session_seq"], f"{path}.session_seq", minimum=0),
        execution_id=_opt_str(value["execution_id"], f"{path}.execution_id"),
        node_id=_opt_str(value["node_id"], f"{path}.node_id"),
        round_id=_opt_str(value["round_id"], f"{path}.round_id"),
        message_id=message_id,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        role=_opt_str(value["role"], f"{path}.role"),
        payload_kind=_opt_str(value["payload_kind"], f"{path}.payload_kind"),
        artifact_kind=_opt_str(value["artifact_kind"], f"{path}.artifact_kind"),
        summary_text=_opt_text(value["summary_text"], f"{path}.summary_text"),
    )


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """历史页（§4.2 L442）：规范摘要/固定上界/覆盖域修订指纹/命中/下一游标。

    无命中（空 hits 且无游标）、游标失效（REVISION_CONFLICT）与查询失败（异常）
    分开表达；``next_cursor`` 为 None 即末页（不另设 has_more，工程裁决留痕）。
    """

    query_digest: str
    fixed_upper_bound: str
    revision_fingerprint: str
    hits: tuple[HistoryHit, ...]
    next_cursor: str | None
    read_moment: str


_HISTORY_PAGE_KEYS: Final = frozenset(
    {"query_digest", "fixed_upper_bound", "revision_fingerprint", "hits", "next_cursor", "read_moment"}
)


def decode_history_page(value: object, path: str = "$.history_page") -> HistoryPage:
    """严格解码历史页（6 键全必填；指纹/摘要为 64 位摘要）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _HISTORY_PAGE_KEYS, path)
    hits_value = value["hits"]
    if not isinstance(hits_value, list):
        _fail("invalid_type", f"{path}.hits")
    hits = tuple(decode_history_hit(item, f"{path}.hits[{index}]") for index, item in enumerate(hits_value))
    next_cursor = _opt_str(value["next_cursor"], f"{path}.next_cursor")
    if next_cursor is not None:
        try:
            parse_history_cursor(next_cursor)
        except AgentValidationError:
            _fail("invalid_cursor", f"{path}.next_cursor")
    return HistoryPage(
        query_digest=_require_digest(value["query_digest"], f"{path}.query_digest"),
        fixed_upper_bound=_require_utc_timestamp(value["fixed_upper_bound"], f"{path}.fixed_upper_bound"),
        revision_fingerprint=_require_digest(value["revision_fingerprint"], f"{path}.revision_fingerprint"),
        hits=hits,
        next_cursor=next_cursor,
        read_moment=_require_utc_timestamp(value["read_moment"], f"{path}.read_moment"),
    )


# ======================================================================
# C-04 资源票据/计数/源禁用/阅读批次合同（§5.4/§5.5/§9.11/§9.13/§4.2 L436）
# ======================================================================


class ResourceSlotKind(Enum):
    """资源票据种类（§9.11）：global_guard 为全局仲裁行，不分配研究。"""

    TOTAL = "total"
    LEAF = "leaf"
    GLOBAL_GUARD = "global_guard"


@dataclass(frozen=True)
class ResourceClaimRequest:
    """资源票据认领请求（§5.5：票据至少 kind/slot_id/execution_id/node_id/owner_epoch）。

    ``slot_id`` 由调用方按稳定身份派生（stable_id），同 slot_id 同输入为幂等重放，
    异输入为 ``INPUT_CONFLICT``；已释出票据的 slot_id 不得复用（零复活）。

    ``operation_refs`` 唯一载形为 CanonicalJson 规范 JSON 对象文本（非 dict、
    非非规范形态）；``None`` 表示无引用。DAO 落 JSONB 前解析为对象。
    """

    user_scope: str
    slot_id: str
    slot_kind: ResourceSlotKind
    execution_id: str
    node_id: str
    round_id: str | None
    owner_epoch: int
    operation_refs: CanonicalJson | None


@dataclass(frozen=True)
class ResourceTicket:
    """资源票据（§5.5）：认领成功或幂等重放返回的准确票据。"""

    slot_id: str
    slot_kind: ResourceSlotKind
    execution_id: str
    node_id: str
    round_id: str | None
    owner_epoch: int
    run_epoch: int
    claimed_at: str


@dataclass(frozen=True)
class ResourceReleaseRequest:
    """资源票据释放请求（§5.5：释放必须完整匹配，不用 TTL/租约/旧 owner）。

    ``exit_verified=False`` 表示退出尾段未知：票据保持占用（release_verified
    不放空），须实际全退或用户保未知结案后以 ``exit_verified=True`` 再释放。
    """

    user_scope: str
    slot_id: str
    execution_id: str
    node_id: str
    owner_epoch: int
    exit_verified: bool


@dataclass(frozen=True)
class ResourceReleaseReceipt:
    """资源票据释放回执：``released=False`` 表示容量保留（未知在途不放空）。"""

    slot_id: str
    released: bool
    release_verified: bool
    released_at: str | None
    owner_epoch: int


@dataclass(frozen=True)
class UsageCounterUpdate:
    """执行累计计数更新（§5.5/§9.4）：绝对累计值 GREATEST 合并（R22）。

    ``active_ms`` 为已核可靠下界；未关闭区间尾段未知不记 0、不伪完整用时。
    """

    user_scope: str
    execution_id: str
    model_attempt_count: int
    tool_call_count: int
    active_ms: int


@dataclass(frozen=True)
class UsageCounterFact:
    """执行累计计数落定事实：三计数均为 GREATEST 合并后的落定值。"""

    execution_id: str
    model_attempt_count: int
    tool_call_count: int
    active_ms: int


@dataclass(frozen=True)
class SourceStopCommand:
    """停止使用来源命令（§3.3.2/§5.4）：已解析的准确 SourceVersionRef。

    ``command_id`` 为调用方 stop_using 命令键（幂等重放）；同键异输入
    ``INPUT_CONFLICT``。禁用先胜零受影响新出站，claim 先胜保在途事实。
    """

    user_scope: str
    source_id: str
    source_version: str
    command_id: str


@dataclass(frozen=True)
class SourceStopReceipt:
    """停止使用来源回执：disabled_revision 为 GREATEST 落定值（零复活）。"""

    source_id: str
    source_version: str
    disabled_revision: int
    already_disabled: bool
    disabled_at: str


@dataclass(frozen=True)
class SourceClaimRequest:
    """源派发认领请求（§5.4 共同仲裁）：持锁事务内新鲜重读禁用状态。

    ``claim_ref`` 为绑定 revision 的原 operation 认领身份（幂等重放键）；
    sources 按规范排序在锁内核禁用/谱系，任一已禁用 → ``SOURCE_DISABLED``。
    """

    user_scope: str
    claim_ref: str
    operation_id: str
    sources: tuple[SourceVersionRef, ...]


@dataclass(frozen=True)
class SourceClaimBinding:
    """单源版本认领绑定：认领时新鲜重读到的身份四值与 disabled_revision（claim 锚定值）。

    ``source_kind`` / ``source_digest`` 一并绑定：同 ID/版本但类别或摘要不同的
    认领不得互认，重放也按四值身份比较（§5.4）。
    """

    source_id: str
    source_version: str
    source_kind: SourceKind
    source_digest: str
    disabled_revision: int


@dataclass(frozen=True)
class SourceClaimFact:
    """源派发认领事实：``replayed=True`` 为同 claim_ref 重放返回原事实。"""

    claim_ref: str
    bindings: tuple[SourceClaimBinding, ...]
    replayed: bool


@dataclass(frozen=True)
class SourceConsumerRef:
    """来源消费者谱系项（§9.10）：已知衍生闭包的单条边投影。"""

    link_id: str
    source_id: str
    source_version: str
    consumer_kind: str
    consumer_id: str
    consumer_version: str
    relation_kind: str
    disabled_inherited: bool


@dataclass(frozen=True)
class SourceConsumersPage:
    """来源消费者列举页：已知边全量返回（边表即已知闭包）；未知关联不在其内，
    不将未知当空集（阻断判定由派发方按关联不明处理）。"""

    source_id: str
    source_version: str
    consumers: tuple[SourceConsumerRef, ...]
    read_moment: str


@dataclass(frozen=True)
class ReadMarkCommand:
    """已读标记命令（§4.2 L436）：只有准确已读身份或经核连续已读水位。

    ``observed_through_seq`` 与 ``observation_scope_digest`` 必须同给同缺；
    不全局清零，乱序最终取 max（R22）。
    """

    user_scope: str
    session_id: str
    command_id: str
    message_ids: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    observed_through_seq: int | None
    observation_scope_digest: str | None


@dataclass(frozen=True)
class ReadMarkReceipt:
    """已读标记回执：last_seq/read_targets 为 GREATEST/并集合并后的落定值。"""

    session_id: str
    command_id: str
    last_seq: int
    read_target_count: int
    replayed: bool
    marked_at: str


@dataclass(frozen=True)
class DecisionBatchFact:
    """待决定批次事实（§9.11/AC33）：新批次行锁下递增 batch_seq；稍后只推
    postponed_through_seq=GREATEST，同项状态变更不造新批次。"""

    batch_seq: int
    postponed_through_seq: int | None
    replayed: bool
    recorded_at: str


def validate_resource_claim_request(request: ResourceClaimRequest) -> None:
    """资源认领请求结构校验（§5.5）：guard 不经本入口、slot_id 不与保留键冲突。"""

    _require_str(request.user_scope, "$.user_scope")
    if request.slot_kind is ResourceSlotKind.GLOBAL_GUARD:
        _fail("guard_not_claimable", "$.slot_kind")
    if request.slot_id == "global_guard":
        _fail("reserved_slot_id", "$.slot_id")
    _require_str(request.slot_id, "$.slot_id")
    _require_str(request.execution_id, "$.execution_id")
    _require_str(request.node_id, "$.node_id")
    if request.round_id is not None:
        _require_str(request.round_id, "$.round_id")
    _require_p(request.owner_epoch, "$.owner_epoch")
    if request.operation_refs is not None:
        # 唯一载形：CanonicalJson 规范 JSON **对象**文本（§3.1）。字符串以外的
        # 载形（含结构化 dict）一律拒绝，避免 wire 与 JSONB 两套表示分叉。
        if not isinstance(request.operation_refs, str):
            _fail("invalid_type", "$.operation_refs")
        try:
            refs = decode_json_doc(request.operation_refs)
        except AgentValidationError:
            _fail("invalid_json", "$.operation_refs")
        if not isinstance(refs, dict):
            _fail("invalid_type", "$.operation_refs")
        if json_doc(refs) != request.operation_refs:
            _fail("invalid_json", "$.operation_refs")


def validate_read_mark_command(command: ReadMarkCommand) -> None:
    """已读标记命令校验（§4.2 L436）：水位与 scope digest 同给同缺。"""

    _require_str(command.user_scope, "$.user_scope")
    _require_str(command.session_id, "$.session_id")
    _require_str(command.command_id, "$.command_id")
    if (command.observed_through_seq is None) != (command.observation_scope_digest is None):
        _fail("scope_pair_required", "$.observed_through_seq")
    if command.observed_through_seq is not None:
        _require_n(command.observed_through_seq, "$.observed_through_seq")
        _require_digest(command.observation_scope_digest, "$.observation_scope_digest")
    for index, message_id in enumerate(command.message_ids):
        _require_str(message_id, f"$.message_ids[{index}]")
    for index, ref in enumerate(command.artifact_refs):
        _require_str(ref, f"$.artifact_refs[{index}]")


# ---------------------------------------------------------------------------
# PlanSpec 提案（DD §8 F04 / SQ-04；HANDOFF §7.2，D-02）
# ---------------------------------------------------------------------------
#
# 派发前程序校验（模型提案不授自身权限）：无环、自依赖、缺引用、重复、角色能力、
# 最多 20 叶、一层。一层由扁平节点表结构保证（无嵌套子计划字段），decode 严格
# 拒额外键；保存计划版（artifact result_kind=plan）与原生引用后才派发。

#: 一层叶上限（HANDOFF §7.2「最多 20 叶」；超一拒绝、恰等合法）。
MAX_PLAN_LEAVES: Final[int] = 20

#: 计划提案结构版本（进入规范摘要前缀，版本演进不改写旧版身份）。
PLAN_SPEC_VERSION: Final[int] = 1

_PLAN_SPEC_KEYS: Final = frozenset({"nodes"})
_PLAN_NODE_KEYS: Final = frozenset({"node_id", "target", "role_key", "depends_on", "capabilities", "source_refs"})


@dataclass(frozen=True, slots=True)
class PlanNodeSpec:
    """一层叶节点提案：节点身份/目标/角色/依赖/能力/来源（`扁平等价一层`）。

    ``target`` 为用户可见正文，原样保留（不 trim、不改 Unicode）。
    """

    node_id: str
    target: str
    role_key: str
    depends_on: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    source_refs: tuple[SourceVersionRef, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanSpecProposal:
    """模型提交的计划提案（派发前必须经 :func:`validate_plan_spec_proposal`）。

    节点顺序属计划版身份（同一内容不同顺序是不同提案），摘要不排序。
    """

    nodes: tuple[PlanNodeSpec, ...]


def _require_text_nonempty(value: object, path: str) -> str:
    """用户可见正文：须非空 str；不 trim、不改 Unicode。"""

    if not isinstance(value, str) or not value:
        _fail("invalid_text", path)
    return value


def _decode_str_tuple(value: object, path: str) -> tuple[str, ...]:
    """wire 字符串数组（允许空；重复项留给语义校验报专属错误码）。"""

    if not isinstance(value, list):
        _fail("invalid_type", path)
    return tuple(_require_str(item, f"{path}[{index}]") for index, item in enumerate(value))


def _require_plan_node_shapes(node: PlanNodeSpec, path: str) -> None:
    """节点容器类型校验（冻结 dataclass 的不可变容器契约）。"""

    for field_name in ("depends_on", "capabilities", "source_refs"):
        if not isinstance(getattr(node, field_name), tuple):
            _fail("invalid_type", f"{path}.{field_name}")


def decode_plan_node_spec(value: object, path: str = "$.node") -> PlanNodeSpec:
    """严格解码节点提案（六键必填、禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _PLAN_NODE_KEYS, path)
    raw_refs = value["source_refs"]
    if not isinstance(raw_refs, list):
        _fail("invalid_type", f"{path}.source_refs")
    return PlanNodeSpec(
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        target=_require_text_nonempty(value["target"], f"{path}.target"),
        role_key=_require_str(value["role_key"], f"{path}.role_key"),
        depends_on=_decode_str_tuple(value["depends_on"], f"{path}.depends_on"),
        capabilities=_decode_str_tuple(value["capabilities"], f"{path}.capabilities"),
        source_refs=tuple(
            decode_source_version_ref(ref, f"{path}.source_refs[{index}]") for index, ref in enumerate(raw_refs)
        ),
    )


def decode_plan_spec_proposal(value: object, path: str = "$.plan_spec") -> PlanSpecProposal:
    """严格解码计划提案（``nodes`` 必填、``plan_spec_version`` 可选且须为 1）。

    模型提案 wire 只须 ``nodes``；规范化保存形态另带 ``plan_spec_version``，
    两者都可解（往返语义一致）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys_with_optional(value, _PLAN_SPEC_KEYS, {"plan_spec_version"}, path)
    if "plan_spec_version" in value:
        version = value["plan_spec_version"]
        # 结构版本必须是整数 1：bool 是 int 子类、1.0/Decimal("1.0") 与 1 等值，
        # 仅做等值比较会放行这些输入，故先显式拒 bool 与非整数类型，再核版本值。
        if isinstance(version, bool) or not isinstance(version, int) or version != PLAN_SPEC_VERSION:
            _fail("unsupported_version", f"{path}.plan_spec_version")
    raw_nodes = value["nodes"]
    if not isinstance(raw_nodes, list):
        _fail("invalid_type", f"{path}.nodes")
    return PlanSpecProposal(
        nodes=tuple(decode_plan_node_spec(item, f"{path}.nodes[{index}]") for index, item in enumerate(raw_nodes))
    )


def normalize_plan_spec_proposal(plan: PlanSpecProposal) -> CanonicalJson:
    """规范化计划提案 wire（结构版本 + 全键显式；用于保存计划版与摘要）。"""

    if not isinstance(plan, PlanSpecProposal):
        _fail("invalid_type", "$.plan_spec")
    return json_doc({"plan_spec_version": PLAN_SPEC_VERSION, "nodes": list(plan.nodes)})


def plan_spec_digest(plan: PlanSpecProposal) -> str:
    """计划版内容摘要（SHA-256，绑定结构版本；节点顺序入身份）。"""

    if not isinstance(plan, PlanSpecProposal):
        _fail("invalid_type", "$.plan_spec")
    return sha256_hex(json_doc(["plan_spec", PLAN_SPEC_VERSION, list(plan.nodes)]))


def plan_node_digest(node: PlanNodeSpec) -> str:
    """单个节点提案内容摘要（节点 wire 全字段入身份，含来源版本引用）。"""

    if not isinstance(node, PlanNodeSpec):
        _fail("invalid_type", "$.node")
    return sha256_hex(json_doc(["plan_node", PLAN_SPEC_VERSION, node]))


def node_input_digest(
    *,
    node_digest: str,
    dependency_input_digests: Sequence[str],
    input_version_digest: str | None,
) -> str:
    """节点实际输入指纹（R2-I09）：节点内容 + 前置输入指纹（依赖序）+ 输入版本。

    ``input_version_digest`` 为 ``None`` 表示当前输入版本无法核定；此时指纹仍可
    计算（以显式 ``unknown`` 哨兵入摘要），但**不构成「仍适用」证据**——重试轮
    保留前置在输入版本未知时不得复用（判据在服务层，见 workflows）。
    """

    if not is_digest(node_digest):
        _fail("invalid_digest", "$.node_digest")
    for index, dependency_digest in enumerate(dependency_input_digests):
        if not is_digest(dependency_digest):
            _fail("invalid_digest", f"$.dependency_input_digests[{index}]")
    if input_version_digest is not None and not is_digest(input_version_digest):
        _fail("invalid_digest", "$.input_version_digest")
    return sha256_hex(
        json_doc(
            [
                "agent_node_input",
                PLAN_SPEC_VERSION,
                node_digest,
                list(dependency_input_digests),
                input_version_digest if input_version_digest is not None else "unknown",
            ]
        )
    )


def validate_plan_spec_proposal(
    plan: PlanSpecProposal,
    *,
    allowed_role_capabilities: Mapping[str, AbstractSet[str]],
    satisfied_external_dependencies: AbstractSet[str] = frozenset(),
) -> None:
    """计划提案程序校验（SQ-04：先核循环/自依赖/缺引用/角色/能力/20 叶）。

    Args:
        plan: 待校验提案。
        allowed_role_capabilities: 本次角色 → 允许能力键集合（由角色/授权配置
            提供）。角色缺失即拒绝；节点能力必须落在该角色集合内（模型提案不授
            自身权限，DD-F03/F04）。
        satisfied_external_dependencies: 重试轮由保留前置满足的依赖身份（不在本
            计划内）；命中即不判缺引用，也不参与无环计算的入度/边（其成果与适用
            性由服务层单独核定）。

    Raises:
        AgentValidationError: ``empty_plan`` / ``too_many_leaves`` /
            ``duplicate_node`` / ``self_dependency`` / ``missing_reference`` /
            ``plan_cycle`` / ``duplicate_dependency`` / ``duplicate_capability`` /
            ``duplicate_source_ref`` / ``unknown_role`` / ``capability_not_allowed``
            及通用类型/身份错误码。
    """

    if not isinstance(plan, PlanSpecProposal):
        _fail("invalid_type", "$.plan_spec")
    nodes = plan.nodes
    if not isinstance(nodes, tuple):
        _fail("invalid_type", "$.nodes")
    if not nodes:
        # F03：无工具/分工时不制造空任务树（简单目标应直答）。
        _fail("empty_plan", "$.nodes")
    if len(nodes) > MAX_PLAN_LEAVES:
        _fail("too_many_leaves", "$.nodes")

    seen_index: dict[str, int] = {}
    for index, node in enumerate(nodes):
        path = f"$.nodes[{index}]"
        if not isinstance(node, PlanNodeSpec):
            _fail("invalid_type", path)
        _require_plan_node_shapes(node, path)
        _require_str(node.node_id, f"{path}.node_id")
        _require_text_nonempty(node.target, f"{path}.target")
        _require_str(node.role_key, f"{path}.role_key")
        if node.node_id in seen_index:
            _fail("duplicate_node", f"{path}.node_id")
        seen_index[node.node_id] = index

        allowed_caps = allowed_role_capabilities.get(node.role_key)
        if allowed_caps is None:
            # 角色能力交集核验：未知角色不派发（不默认放行）。
            _fail("unknown_role", f"{path}.role_key")
        if len(set(node.capabilities)) != len(node.capabilities):
            _fail("duplicate_capability", f"{path}.capabilities")
        for cap_index, capability in enumerate(node.capabilities):
            _require_str(capability, f"{path}.capabilities[{cap_index}]")
            if capability not in allowed_caps:
                _fail("capability_not_allowed", f"{path}.capabilities[{cap_index}]")

        if len(set(node.depends_on)) != len(node.depends_on):
            _fail("duplicate_dependency", f"{path}.depends_on")
        for dep_index, dependency in enumerate(node.depends_on):
            _require_str(dependency, f"{path}.depends_on[{dep_index}]")
            if dependency == node.node_id:
                _fail("self_dependency", f"{path}.depends_on[{dep_index}]")

        seen_refs: set[tuple[str, str, str]] = set()
        for ref_index, ref in enumerate(node.source_refs):
            ref_path = f"{path}.source_refs[{ref_index}]"
            if not isinstance(ref, SourceVersionRef):
                _fail("invalid_type", ref_path)
            _require_str(ref.source_id, f"{ref_path}.source_id")
            _require_str(ref.source_version, f"{ref_path}.source_version")
            _require_digest(ref.source_digest, f"{ref_path}.source_digest")
            ref_key = (ref.source_kind.value, ref.source_id, ref.source_version)
            if ref_key in seen_refs:
                _fail("duplicate_source_ref", ref_path)
            seen_refs.add(ref_key)

    # 缺引用：依赖目标必须在本计划内（一层叶，不跨计划引用）；重试轮由保留
    # 前置满足的外部依赖不在此列。
    for index, node in enumerate(nodes):
        for dep_index, dependency in enumerate(node.depends_on):
            if dependency not in seen_index and dependency not in satisfied_external_dependencies:
                _fail("missing_reference", f"$.nodes[{index}].depends_on[{dep_index}]")

    # 无环：Kahn 拓扑排序（只计本计划内边）；未全部出队即有环（非法图零执行）。
    in_degree = {node.node_id: sum(1 for dependency in node.depends_on if dependency in seen_index) for node in nodes}
    children: dict[str, list[str]] = {node.node_id: [] for node in nodes}
    for node in nodes:
        for dependency in node.depends_on:
            if dependency in seen_index:
                children[dependency].append(node.node_id)
    ready = [node_id for node_id, degree in in_degree.items() if degree == 0]
    visited = 0
    while ready:
        node_id = ready.pop()
        visited += 1
        for child in children[node_id]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
    if visited != len(nodes):
        _fail("plan_cycle", "$.nodes")


# ---------------------------------------------------------------------------
# D-03：启动隔离、派发门控与手动恢复（HANDOFF §8.4 / 设计 §9.2/§9.5）
# ---------------------------------------------------------------------------

#: 轮次停止原因 wire 值（§9.5 `stop_reason`：启动隔离与用户终止必须可区分，
#: 只有启动隔离可经核验手动恢复；用户终止不复活，HANDOFF §8.4 末段）。
ROUND_STOP_REASON_STARTUP_ISOLATION: Final[str] = "startup_isolation"
ROUND_STOP_REASON_USER_TERMINATION: Final[str] = "user_termination"

#: 不可恢复的终态投影（这些状态不进入手动恢复，也不作为启动隔离候选的
#: 「在途」语义；`partial` 为已定部分结果，同样不再续跑）。
NON_RECOVERABLE_DISPLAY_STATES: Final[frozenset[str]] = frozenset(
    {"ended", "failed", "complete", "partial", "withdrawn", "closed_unknown"}
)


@dataclass(frozen=True)
class RecoveryExecution:
    """恢复候选执行投影（§9.2/§9.5）：应用登记事实 + 当前有效轮的原生引用。

    ``workflow_id`` / ``run_epoch`` / ``stop_reason`` 来自当前有效轮（无轮时
    为 None）；``dispatch_closed`` 为应用派发门闩（不等于已停止）。叶执行的
    原生叶 workflow 身份由原生列举覆盖（应用侧未逐叶登记 workflow_id）。
    """

    execution_id: str
    session_id: str
    execution_kind: str
    display_state: str
    owner_epoch: int
    dispatch_closed: bool
    ended_at: str | None
    round_id: str | None
    round_number: int | None
    workflow_id: str | None
    run_epoch: int | None
    stop_reason: str | None


@dataclass(frozen=True)
class StartupIsolationRecord:
    """启动隔离命令记录（HANDOFF §8.4 第 2 条：固定快照后先保存再取消）。

    ``snapshot`` / ``receipt_payload`` 为 CanonicalJson：快照含被隔离 workflow
    ID 与快照时点原生状态/归属登记事实；回执含就绪与否及阻塞原因。二者均不含
    凭据或私人正文（R9）。
    """

    user_scope: str
    client_command_id: str
    target_id: str
    snapshot: CanonicalJson
    receipt_payload: CanonicalJson


@dataclass(frozen=True)
class DispatchCloseRequest:
    """应用派发门闩关闭请求（§9.2 `dispatch_closed`：启动隔离/手动恢复共用）。

    以 workflow 身份定位执行行；未匹配的 ID 不计失败（原生有而应用未登记）。
    """

    user_scope: str
    workflow_ids: tuple[str, ...]


@dataclass(frozen=True)
class DispatchCloseFact:
    """派发门闩关闭事实：``closed_execution_ids`` 为门闩已关闭（或本已关闭）的执行。"""

    user_scope: str
    closed_execution_ids: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryGrantRequest:
    """手动恢复准入代次授予请求（HANDOFF §8.4 第 5 条：只授精确选择集合）。

    授予前逐项核定：不可恢复终态/未核状态拒绝、用户终止不复活、必须有活动轮、
    轮代码版本须等于 ``application_version``（兼容矩阵门），旧执行不得仍有未
    收口 operation（§8.5「unknown 先核原 operation」）；数据/权限在每次实际派发
    边界重核（§8.4 末段），不在本机械门内重复。``owner_epoch`` / ``run_epoch``
    必须严格大于现值（单调，R22）。
    """

    user_scope: str
    execution_ids: tuple[str, ...]
    owner_epoch: int
    run_epoch: int
    application_version: str


@dataclass(frozen=True)
class RecoveryGrantFact:
    """恢复准入授予事实：``workflow_ids`` 为可 resume 的精确原生集合（不含被阻项）。"""

    user_scope: str
    owner_epoch: int
    run_epoch: int
    granted_execution_ids: tuple[str, ...]
    workflow_ids: tuple[str, ...]
    blocked: tuple[tuple[str, str], ...]


#: 未收口 operation 阶段（恢复前必须确认旧执行已退出；unknown 亦先核原 operation）。
OPEN_OPERATION_PHASES: Final[frozenset[str]] = frozenset({"prepared", "dispatch_claimed", "dispatched", "unknown"})

#: 恢复候选的「未核状态」（unknown/ending/paused/pausing 不得静默恢复：
#: unknown/ending 可能仍有写者；paused/pausing 须走「总继续/单叶继续」语义，
#: 直接 resume 会绕过暂停原因清理）。
RECOVERY_UNVERIFIED_DISPLAY_STATES: Final[frozenset[str]] = frozenset({"unknown", "ending", "paused", "pausing"})


# ---------------------------------------------------------------------------
# §3.3 来源读取与事实（E-01）
# ---------------------------------------------------------------------------

#: FactValue 外层原始类型（§3.3 agent-number-v1；字符串形似数字仍是 string）。
FACT_RAW_TYPES: Final[frozenset[str]] = frozenset({"json_integer", "json_decimal", "string", "bool", "null"})

#: XLSX 缓存带类型表示（§10.4：数值沿 agent-number-v1，另含 error/datetime）。
XLSX_CACHE_RAW_TYPES: Final[frozenset[str]] = frozenset(
    {INTEGER_RAW_TYPE, DECIMAL_RAW_TYPE, "string", "bool", "null", "error", "datetime"}
)

#: SourceRead 覆盖语义：完整/受限/未知（未知不得当完整）。
SOURCE_COVERAGES: Final[frozenset[str]] = frozenset({"full", "limited", "unknown"})


#: 附件类别（D06：TXT/MD/CSV/JSON/XLSX/文本 PDF；PNG/JPEG 仅核验兼容模型）。
class AttachmentKind(Enum):
    TXT = "txt"
    MD = "md"
    CSV = "csv"
    JSON = "json"
    XLSX = "xlsx"
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"


@dataclass(frozen=True, slots=True)
class XlsxCellMeta:
    """XLSX 单元格元数据（§10.4，schema_version=1，全部键必填）。

    ``cache_present=true`` 只证明同源单元格确有缓存载荷，不证明正确或新鲜；
    ``false`` 表示已核不存在；``null`` 表示未读/不能核定，须各保原因。
    非公式单元格 ``formula_text=None``、``cache_present=False``、
    ``cache_applicable=False`` 且原因为 ``not_applicable``。
    """

    sheet: str
    cell: str
    formula_text: str | None = None
    cache_present: bool | None = None
    cache_applicable: bool | None = None
    cache_raw_type: str | None = None
    cache_raw_value: CanonicalJson | None = None
    cache_verification: VerificationState | None = None
    cache_missing_reason: str | None = None
    date_epoch: str | None = None
    number_format: str | None = None
    xml_value_lexeme: str | None = None
    schema_version: str = "1"


_XLSX_CELL_META_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "sheet",
        "cell",
        "formula_text",
        "cache_present",
        "cache_applicable",
        "cache_raw_type",
        "cache_raw_value",
        "cache_verification",
        "cache_missing_reason",
        "date_epoch",
        "number_format",
        "xml_value_lexeme",
    }
)


def _decode_xlsx_cache_value(raw_type: str, text: CanonicalJson, path: str) -> None:
    """按 ``cache_raw_type`` 闭合 XLSX 缓存载荷（数值走同一 exact codec，§10.4）。

    数值载荷必须是 agent-number-v1 记录且内层 ``raw_type`` 与 ``cache_raw_type``
    一致；``string`` / ``bool`` / ``null`` / ``error`` / ``datetime`` 为
    ``{"raw_type", "lexeme"}`` 记录（datetime 另允许 ``serial`` / ``date_epoch``），
    禁额外键。伪内层数值不得冒充缓存数值。
    """

    parsed = decode_json_doc(text)
    if not isinstance(parsed, dict):
        _fail("invalid_type", path)
    if raw_type in (INTEGER_RAW_TYPE, DECIMAL_RAW_TYPE):
        decode_exact_number(parsed, path)
        if parsed["raw_type"] != raw_type:
            _fail("invalid_number", f"{path}.raw_type")
        return
    allowed = {"raw_type", "lexeme"}
    if raw_type == "datetime":
        allowed |= {"serial", "date_epoch"}
    missing = sorted({"raw_type", "lexeme"} - parsed.keys())
    if missing:
        _fail("missing_field", f"{path}.{missing[0]}")
    extra = sorted(parsed.keys() - allowed)
    if extra:
        _fail("unknown_field", f"{path}.{extra[0]}")
    if parsed["raw_type"] != raw_type:
        _fail("invalid_raw_type", f"{path}.raw_type")
    lexeme = parsed["lexeme"]
    if lexeme is not None and not isinstance(lexeme, str):
        _fail("invalid_type", f"{path}.lexeme")
    for key in ("serial", "date_epoch"):
        item = parsed.get(key)
        if item is not None and not isinstance(item, str):
            _fail("invalid_type", f"{path}.{key}")


def decode_xlsx_cell_meta(value: object, path: str = "$.xlsx_cell_meta") -> XlsxCellMeta:
    """严格解码 XLSX 单元格元数据（13 键全必填、禁额外键、缓存标志与载荷闭合）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _XLSX_CELL_META_KEYS, path)
    if _require_str(value["schema_version"], f"{path}.schema_version") != "1":
        _fail("invalid_version", f"{path}.schema_version")
    cache_present = _opt_bool(value["cache_present"], f"{path}.cache_present")
    cache_applicable = _opt_bool(value["cache_applicable"], f"{path}.cache_applicable")
    cache_raw_type = _opt_str(value["cache_raw_type"], f"{path}.cache_raw_type")
    cache_raw_value = _opt_json_text(value["cache_raw_value"], f"{path}.cache_raw_value")
    if cache_raw_type is not None and cache_raw_type not in XLSX_CACHE_RAW_TYPES:
        _fail("invalid_raw_type", f"{path}.cache_raw_type")
    if (cache_raw_type is None) != (cache_raw_value is None):
        _fail("invalid_cache_state", path)
    if cache_present is not True and (cache_raw_type is not None or cache_applicable is True):
        _fail("invalid_cache_state", path)
    if cache_raw_type is not None and cache_raw_value is not None:
        _decode_xlsx_cache_value(cache_raw_type, cache_raw_value, f"{path}.cache_raw_value")
    return XlsxCellMeta(
        sheet=_require_str(value["sheet"], f"{path}.sheet"),
        cell=_require_str(value["cell"], f"{path}.cell"),
        formula_text=_opt_str(value["formula_text"], f"{path}.formula_text"),
        cache_present=cache_present,
        cache_applicable=cache_applicable,
        cache_raw_type=cache_raw_type,
        cache_raw_value=cache_raw_value,
        cache_verification=_opt_enum(
            value["cache_verification"], VerificationState, f"{path}.cache_verification", "invalid_verification_state"
        ),
        cache_missing_reason=_opt_str(value["cache_missing_reason"], f"{path}.cache_missing_reason"),
        date_epoch=_opt_str(value["date_epoch"], f"{path}.date_epoch"),
        number_format=_opt_str(value["number_format"], f"{path}.number_format"),
        xml_value_lexeme=_opt_str(value["xml_value_lexeme"], f"{path}.xml_value_lexeme"),
        schema_version="1",
    )


def _decode_xlsx_cell_meta_text(value: object, path: str) -> CanonicalJson | None:
    """FactValue.xlsx_cell_meta：JSON 文本承载，须整键整值通过完整 meta codec。"""

    if value is None:
        return None
    text = _require_str(value, path)
    try:
        decoded = decode_json_doc(text)
    except AgentValidationError:
        _fail("invalid_json", path)
    decode_xlsx_cell_meta(decoded, path)
    return text


@dataclass(frozen=True, slots=True)
class SourceRef:
    """来源元数据（§3.3）：准确内容定位，工作台不得按名称搜索相似文件代替原件。

    ``available_at`` 为可得时点（UTC 微秒文本）；历史决策用途下晚于 cutoff 的
    来源必须拒绝登记（PIT，R24）。``missing_reason`` 只描述已知缺失，不把未知
    写成缺失或空成功。
    """

    user_scope: str
    source_id: str
    version: str
    digest: str
    source_kind: SourceKind
    display_name: str
    object_ref: str | None
    locator: SourceLocator
    available_at: str | None
    period: str | None
    unit_note: str | None
    read_state: ReadState
    missing_reason: str | None
    provenance_refs: tuple[ProvenanceRef, ...]


_SOURCE_REF_KEYS: Final[frozenset[str]] = frozenset(
    {
        "user_scope",
        "source_id",
        "version",
        "digest",
        "source_kind",
        "display_name",
        "object_ref",
        "locator",
        "available_at",
        "period",
        "unit_note",
        "read_state",
        "missing_reason",
        "provenance_refs",
    }
)


def encode_source_ref(ref: SourceRef) -> dict[str, Any]:
    """SourceRef → wire 对象（14 键全在，null 不省略）。"""

    return to_wire_dict(ref)


def decode_source_ref(value: object, path: str = "$.source_ref") -> SourceRef:
    """严格解码来源元数据（14 键全必填、禁额外键）。

    公共入口逐值闭合：顶层 ``source_kind/source_id/version/digest`` 四值必须与
    ``locator.origin_ref`` 完全一致（同一受管源版，冗余承载不得分叉，§3.3）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SOURCE_REF_KEYS, path)
    user_scope = _require_str(value["user_scope"], f"{path}.user_scope")
    source_id = _require_str(value["source_id"], f"{path}.source_id")
    version = _require_str(value["version"], f"{path}.version")
    digest = _require_digest(value["digest"], f"{path}.digest")
    source_kind = _require_enum(value["source_kind"], SourceKind, f"{path}.source_kind", "invalid_source_kind")
    locator = decode_source_locator(value["locator"], f"{path}.locator")
    origin = locator.origin_ref
    if (
        source_kind is not origin.source_kind
        or source_id != origin.source_id
        or version != origin.source_version
        or digest != origin.source_digest
    ):
        _fail("identity_mismatch", f"{path}.locator.origin_ref")
    return SourceRef(
        user_scope=user_scope,
        source_id=source_id,
        version=version,
        digest=digest,
        source_kind=source_kind,
        display_name=_require_str(value["display_name"], f"{path}.display_name"),
        object_ref=_opt_str(value["object_ref"], f"{path}.object_ref"),
        locator=locator,
        available_at=_opt_timestamp(value["available_at"], f"{path}.available_at"),
        period=_opt_str(value["period"], f"{path}.period"),
        unit_note=_opt_str(value["unit_note"], f"{path}.unit_note"),
        read_state=_require_enum(value["read_state"], ReadState, f"{path}.read_state", "invalid_read_state"),
        missing_reason=_opt_str(value["missing_reason"], f"{path}.missing_reason"),
        provenance_refs=decode_provenance_refs(value["provenance_refs"], f"{path}.provenance_refs"),
    )


@dataclass(frozen=True, slots=True)
class FactValue:
    """带类型事实值（§3.3/V04）：原始词法/单位/时点/核验与缺失原因一并保留。

    ``raw_value`` 是 agent-number-v1 的规范 JSON 文本（JSONB 结构中的字符串值），
    不能用显示舍入值或普通 float 往返。
    """

    field_key: str
    object_ref: str
    raw_type: str
    raw_value: CanonicalJson
    numeric_lexeme: str | None
    numeric_encoding: str | None
    source_unit: str | None
    currency: str | None
    display_unit: str | None
    conversion_ref: str | None
    period: str | None
    basis: str | None
    source_locator: SourceLocator
    verification: VerificationState
    missing_reason: str | None
    xlsx_cell_meta: CanonicalJson | None


_FACT_VALUE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "field_key",
        "object_ref",
        "raw_type",
        "raw_value",
        "numeric_lexeme",
        "numeric_encoding",
        "source_unit",
        "currency",
        "display_unit",
        "conversion_ref",
        "period",
        "basis",
        "source_locator",
        "verification",
        "missing_reason",
        "xlsx_cell_meta",
    }
)


def encode_fact_value(fact: FactValue) -> dict[str, Any]:
    """FactValue → wire 对象（16 键全在，null 不省略）。"""

    return to_wire_dict(fact)


def _decode_fact_raw_lexeme(raw_type: str, text: CanonicalJson, path: str) -> str | None:
    """按 ``raw_type`` 闭合 FactValue.raw_value 的内层值（§3.3/V04）。

    数值类型必须是 agent-number-v1 记录且内外 ``raw_type`` 一致（复用
    :func:`decode_exact_number` 的同一判定）；``string`` / ``bool`` / ``null``
    必须是同型 JSON 标量，形似数字的普通值不得冒充数值事实。

    返回内层数值词法（仅数值类型），供外层 ``numeric_lexeme`` 逐值闭合。
    """

    parsed = decode_json_doc(text)
    if raw_type in (INTEGER_RAW_TYPE, DECIMAL_RAW_TYPE):
        if not isinstance(parsed, dict):
            _fail("invalid_number", path)
        decode_exact_number(parsed, path)
        if parsed["raw_type"] != raw_type:
            _fail("invalid_number", f"{path}.raw_type")
        return cast(str, parsed["lexeme"])
    if raw_type == "string":
        matched = isinstance(parsed, str)
    elif raw_type == "bool":
        matched = isinstance(parsed, bool)
    else:
        matched = parsed is None
    if not matched:
        _fail("invalid_raw_type", path)
    return None


def decode_fact_value(value: object, path: str = "$.fact") -> FactValue:
    """严格解码事实值（16 键全必填、禁额外键；raw_type 限封闭值域）。

    ``raw_value`` 按 ``raw_type`` 闭合：数值须为 agent-number-v1 记录且内外
    ``raw_type`` 一致，``numeric_lexeme`` / ``numeric_encoding`` 与内层逐值一致；
    非数值禁止携带数值元数据。``xlsx_cell_meta`` 非 null 时须整键整值通过
    :func:`decode_xlsx_cell_meta`。闭合失败即拒绝，``program_verified`` 不会
    随未核闭合的内层值或空 meta 幸存（R21）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _FACT_VALUE_KEYS, path)
    raw_type = _require_choice(value["raw_type"], FACT_RAW_TYPES, f"{path}.raw_type", "invalid_raw_type")
    raw_value = _require_json_text(value["raw_value"], f"{path}.raw_value")
    numeric_lexeme = _opt_str(value["numeric_lexeme"], f"{path}.numeric_lexeme")
    numeric_encoding = _opt_str(value["numeric_encoding"], f"{path}.numeric_encoding")
    closed_lexeme = _decode_fact_raw_lexeme(raw_type, raw_value, f"{path}.raw_value")
    if raw_type in (INTEGER_RAW_TYPE, DECIMAL_RAW_TYPE):
        if numeric_encoding != AGENT_NUMBER_ENCODING or numeric_lexeme != closed_lexeme:
            _fail("invalid_number", f"{path}.numeric_lexeme")
    elif numeric_lexeme is not None or numeric_encoding is not None:
        _fail("invalid_raw_type", f"{path}.numeric_encoding")
    return FactValue(
        field_key=_require_str(value["field_key"], f"{path}.field_key"),
        object_ref=_require_str(value["object_ref"], f"{path}.object_ref"),
        raw_type=raw_type,
        raw_value=raw_value,
        numeric_lexeme=numeric_lexeme,
        numeric_encoding=numeric_encoding,
        source_unit=_opt_str(value["source_unit"], f"{path}.source_unit"),
        currency=_opt_str(value["currency"], f"{path}.currency"),
        display_unit=_opt_str(value["display_unit"], f"{path}.display_unit"),
        conversion_ref=_opt_str(value["conversion_ref"], f"{path}.conversion_ref"),
        period=_opt_str(value["period"], f"{path}.period"),
        basis=_opt_str(value["basis"], f"{path}.basis"),
        source_locator=decode_source_locator(value["source_locator"], f"{path}.source_locator"),
        verification=_require_enum(
            value["verification"], VerificationState, f"{path}.verification", "invalid_verification_state"
        ),
        missing_reason=_opt_str(value["missing_reason"], f"{path}.missing_reason"),
        xlsx_cell_meta=_decode_xlsx_cell_meta_text(value["xlsx_cell_meta"], f"{path}.xlsx_cell_meta"),
    )


@dataclass(frozen=True, slots=True)
class SourceRead:
    """来源读取结果（§3.3/§4.2）：读取成功与事实核验成功是不同字段。

    ``available=False`` 只说明当前原依据不可得，不把历史已保存内容改写为空；
    ``coverage`` 未知时不得当完整。
    """

    source: SourceRef
    selected_range: SelectedRange
    read_state: ReadState
    body_text: str | None
    structured_blocks: tuple[CanonicalJson, ...]
    facts: tuple[FactValue, ...]
    coverage: str
    verification: VerificationState
    limitations: tuple[RangeLimitation, ...]
    available: bool
    missing_reason: str | None


_SOURCE_READ_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source",
        "selected_range",
        "read_state",
        "body_text",
        "structured_blocks",
        "facts",
        "coverage",
        "verification",
        "limitations",
        "available",
        "missing_reason",
    }
)


def encode_source_read(read: SourceRead) -> dict[str, Any]:
    """SourceRead → wire 对象（11 键全在）。"""

    return to_wire_dict(read)


def decode_source_read(value: object, path: str = "$.source_read") -> SourceRead:
    """严格解码来源读取结果（11 键全必填、禁额外键）。

    ``selected_range.selection`` 必须与 ``source.locator.selection`` 一致
    （all/limited 不得互当），``accepted_limited_version`` 只能等于源版本。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SOURCE_READ_KEYS, path)
    blocks = value["structured_blocks"]
    if not isinstance(blocks, list):
        _fail("invalid_type", f"{path}.structured_blocks")
    facts = value["facts"]
    if not isinstance(facts, list):
        _fail("invalid_type", f"{path}.facts")
    limitations = value["limitations"]
    if not isinstance(limitations, list):
        _fail("invalid_type", f"{path}.limitations")
    source = decode_source_ref(value["source"], f"{path}.source")
    selected_range = decode_selected_range(value["selected_range"], f"{path}.selected_range")
    _require_same_selection(selected_range.selection, source.locator.selection, f"{path}.selected_range.selection")
    accepted = selected_range.accepted_limited_version
    if accepted is not None and accepted != source.version:
        _fail("invalid_accepted_version", f"{path}.selected_range.accepted_limited_version")
    return SourceRead(
        source=source,
        selected_range=selected_range,
        read_state=_require_enum(value["read_state"], ReadState, f"{path}.read_state", "invalid_read_state"),
        body_text=_opt_text(value["body_text"], f"{path}.body_text"),
        structured_blocks=tuple(
            _require_json_text(item, f"{path}.structured_blocks[{index}]") for index, item in enumerate(blocks)
        ),
        facts=tuple(decode_fact_value(item, f"{path}.facts[{index}]") for index, item in enumerate(facts)),
        coverage=_require_choice(value["coverage"], SOURCE_COVERAGES, f"{path}.coverage", "invalid_coverage"),
        verification=_require_enum(
            value["verification"], VerificationState, f"{path}.verification", "invalid_verification_state"
        ),
        limitations=tuple(
            decode_range_limitation(item, f"{path}.limitations[{index}]") for index, item in enumerate(limitations)
        ),
        available=_require_bool(value["available"], f"{path}.available"),
        missing_reason=_opt_str(value["missing_reason"], f"{path}.missing_reason"),
    )


@dataclass(frozen=True, slots=True)
class SourceReadQuery:
    """来源读取查询（§4.2 ``read_source``）：准确源版/摘要/已选定位 + 上下文引用。

    ``locator`` 是调用方已展示的准确范围；读取必须与已存来源元数据逐字段一致，
    不按名称/摘要模糊匹配代替原件（§3.3）。上下文二选一：草稿修订，或执行（可
    含轮次与输入修订引用）；同时给出或都缺失均为 ``INPUT_INVALID``。
    """

    source_id: str
    version: str
    digest: str
    locator: SourceLocator
    draft_revision: int | None = None
    execution_id: str | None = None
    round_id: str | None = None
    input_revision: int | None = None


def encode_source_read_query(query: SourceReadQuery, path: str = "$.source_read_query") -> dict[str, Any]:
    """校验并返回规范 wire 对象（身份/上下文约束不满足抛 ``AgentValidationError``）。"""

    if not isinstance(query, SourceReadQuery):
        _fail("invalid_type", path)
    source_id = _require_str(query.source_id, f"{path}.source_id")
    version = _require_str(query.version, f"{path}.version")
    digest = _require_digest(query.digest, f"{path}.digest")
    locator = decode_source_locator(to_wire_dict(query.locator), f"{path}.locator")
    draft_revision = _opt_int(query.draft_revision, f"{path}.draft_revision", minimum=1)
    execution_id = _opt_str(query.execution_id, f"{path}.execution_id")
    round_id = _opt_str(query.round_id, f"{path}.round_id")
    input_revision = _opt_int(query.input_revision, f"{path}.input_revision", minimum=1)
    if (
        locator.origin_ref.source_id != source_id
        or locator.origin_ref.source_version != version
        or locator.origin_ref.source_digest != digest
    ):
        # 定位的源版摘要必须与查询摘要逐值一致：同 ID 异 digest 不得当作同一原件
        _fail("identity_mismatch", f"{path}.locator")
    if draft_revision is not None and execution_id is not None:
        _fail("context_conflict", f"{path}.execution_id")
    if draft_revision is None and execution_id is None:
        _fail("context_missing", f"{path}.context")
    if draft_revision is not None and (round_id is not None or input_revision is not None):
        _fail("context_conflict", f"{path}.round_id")
    if execution_id is not None and (round_id is None) != (input_revision is None):
        _fail("context_incomplete", f"{path}.round_id")
    return to_wire_dict(
        SourceReadQuery(
            source_id=source_id,
            version=version,
            digest=digest,
            locator=locator,
            draft_revision=draft_revision,
            execution_id=execution_id,
            round_id=round_id,
            input_revision=input_revision,
        )
    )


#: 正文档一页可载码点数（``limit`` 每单位；默认 limit=50 → 100,000 码点，
#: 与 D06 单件提取上界一致，默认一次取得完整表示；小 limit 用于分块传输）。
_BODY_CODEPOINTS_PER_LIMIT: Final = 2000

#: 列表/正文读取的工程上限（DD §4.2：``limit`` 取 1—50）。
READ_LIMIT_MAX: Final = 50


def parse_body_cursor(cursor: str, *, prefix: str, identity: str, path: str = "$.body_cursor") -> int:
    """解析正文游标 ``{prefix}:{identity}:{start}``（1 基）为起始码点。

    游标绑定对象身份（成果 ``id:version`` / operation id）与查询上下文；跨对象
    或错前缀复用 → ``invalid_cursor``（§4.2「游标绑定准确身份」）。用户域由查询
    的 ``user_scope`` 过滤保证，不写入游标。只核语法与身份，不核页边界。
    """

    marker = f"{prefix}:{identity}:"
    if not isinstance(cursor, str) or not cursor.startswith(marker):
        _fail("invalid_cursor", path)
    raw = cursor[len(marker) :]
    if not raw.isdigit() or raw.startswith("0"):
        _fail("invalid_cursor", path)
    return int(raw)


def slice_body_page(
    body_text: str,
    *,
    cursor: str | None,
    prefix: str,
    identity: str,
    limit: int,
    path: str = "$.body_cursor",
) -> tuple[str, str | None]:
    """按**码点**切一页正文并给出下一游标（传输分块，不是存储分块，§9.10.1）。

    游标格式 ``{prefix}:{identity}:{start}``（1 基，start 之前的码点已返回）。
    ``start == len+1`` 表示已读完（返回空页、无下一游标，合法）；``start > len+1``
    或身份/前缀不符视为失效游标（``invalid_cursor``），由调用方转译为
    ``INPUT_INVALID``。
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= READ_LIMIT_MAX:
        _fail("invalid_range", path)
    start = parse_body_cursor(cursor, prefix=prefix, identity=identity, path=path) if cursor is not None else 1
    if start > len(body_text) + 1:
        _fail("invalid_cursor", path)
    begin = start - 1
    end = min(len(body_text), begin + limit * _BODY_CODEPOINTS_PER_LIMIT)
    next_cursor = f"{prefix}:{identity}:{end + 1}" if end < len(body_text) else None
    return body_text[begin:end], next_cursor


@dataclass(frozen=True, slots=True)
class ArtifactReadQuery:
    """成果读取查询（§4.2 ``read_artifact``）：准确版本 + 所属执行/轮次 + 正文游标。"""

    artifact_id: str
    artifact_version: int
    execution_id: str
    round_id: str
    body_cursor: str | None = None
    limit: int = READ_LIMIT_MAX


def validate_artifact_read_query(query: ArtifactReadQuery) -> ArtifactReadQuery:
    """校验成果读取查询（身份非空、版本 ≥1、limit 1—50、游标语法）。"""

    if not isinstance(query, ArtifactReadQuery):
        _fail("invalid_type", "$.artifact_read_query")
    artifact_id = _require_str(query.artifact_id, "$.artifact_read_query.artifact_id")
    artifact_version = _require_p(query.artifact_version, "$.artifact_read_query.artifact_version")
    execution_id = _require_str(query.execution_id, "$.artifact_read_query.execution_id")
    round_id = _require_str(query.round_id, "$.artifact_read_query.round_id")
    limit = _require_p(query.limit, "$.artifact_read_query.limit")
    if limit > READ_LIMIT_MAX:
        _fail("invalid_range", "$.artifact_read_query.limit")
    cursor = _opt_str(query.body_cursor, "$.artifact_read_query.body_cursor")
    # 契约只校验游标语法/身份；切页时按正文长度再核越界。
    if cursor is not None:
        parse_body_cursor(
            cursor,
            prefix="b",
            identity=f"{artifact_id}:{artifact_version}",
            path="$.artifact_read_query.body_cursor",
        )
    return ArtifactReadQuery(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        execution_id=execution_id,
        round_id=round_id,
        body_cursor=cursor,
        limit=limit,
    )


@dataclass(frozen=True, slots=True)
class ArtifactRead:
    """准确成果版本与（分块）完整正文（§4.2 ``ArtifactRead``）。

    ``body_available=False`` 时 ``unavailable_reason`` 说明是正文未保存还是已按
    用户确认清理删除；``next_cursor`` 只在正文还有后续码点时给出。``result_payload``
    保留结构/限制/证据等元数据，不复制整份正文（§9.7）。
    """

    artifact_id: str
    artifact_version: int
    session_id: str
    execution_id: str
    round_id: str
    artifact_kind: str
    input_revision: int
    actual_input_digest: str
    actual_model_ref: str
    result_digest: str
    source_available_state: str
    summary: str | None
    result_payload: CanonicalJson | None
    published_at: str | None
    cleaned_at: str | None
    updated_at: str
    revision: int
    body_text: str | None
    next_cursor: str | None
    body_available: bool
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class OperationReadQuery:
    """操作读取查询（§4.2 ``read_operation``）：准确 operation 及可选归属/工具调用。"""

    operation_id: str
    execution_id: str | None = None
    round_id: str | None = None
    tool_call_id: str | None = None
    body_cursor: str | None = None
    limit: int = READ_LIMIT_MAX


def validate_operation_read_query(query: OperationReadQuery) -> OperationReadQuery:
    """校验操作读取查询（轮次必须挂在执行下；limit 1—50；游标语法）。"""

    if not isinstance(query, OperationReadQuery):
        _fail("invalid_type", "$.operation_read_query")
    operation_id = _require_str(query.operation_id, "$.operation_read_query.operation_id")
    execution_id = _opt_str(query.execution_id, "$.operation_read_query.execution_id")
    round_id = _opt_str(query.round_id, "$.operation_read_query.round_id")
    tool_call_id = _opt_str(query.tool_call_id, "$.operation_read_query.tool_call_id")
    if round_id is not None and execution_id is None:
        _fail("identity_mismatch", "$.operation_read_query.round_id")
    limit = _require_p(query.limit, "$.operation_read_query.limit")
    if limit > READ_LIMIT_MAX:
        _fail("invalid_range", "$.operation_read_query.limit")
    cursor = _opt_str(query.body_cursor, "$.operation_read_query.body_cursor")
    if cursor is not None:
        parse_body_cursor(cursor, prefix="o", identity=operation_id, path="$.operation_read_query.body_cursor")
    return OperationReadQuery(
        operation_id=operation_id,
        execution_id=execution_id,
        round_id=round_id,
        tool_call_id=tool_call_id,
        body_cursor=cursor,
        limit=limit,
    )


@dataclass(frozen=True, slots=True)
class OperationRead:
    """操作事实与（分块）已保存返回（§4.2 ``OperationRead``）：另含 effect/io 事实。

    ``phase='unknown'`` 或 ``effect_state=unknown`` 原样返回并置
    ``result_available=False``/``unavailable_reason='effect_unknown'``——不猜成功
    或失败（§9.9）；``result_ref`` 指向同库正文时按游标分页。清理后结果不可查时
    置 ``unavailable_reason='result_cleaned'``（与 ``read_artifact`` 的
    ``body_cleaned`` 同构，不把已清当坏行、不用空结果冒充成功）。
    """

    operation_id: str
    session_id: str | None
    execution_id: str | None
    round_id: str | None
    tool_call_id: str | None
    workflow_id: str | None
    step_id: str | None
    call_kind: str | None
    ordinal: int | None
    phase: str
    effect_state: EffectState
    io_state: str
    owner_epoch: int
    run_epoch: int
    grant_id: str | None
    grant_revision: int | None
    parameter_digest: str | None
    request_ref: str | None
    result_ref: str | None
    result_payload: CanonicalJson | None
    worker_refs: CanonicalJson | None
    deadline_at: str | None
    dispatched_at: str | None
    exited_at: str | None
    verified_at: str | None
    active_ms: int
    elapsed_ms: int
    error_code: str | None
    error_category: str | None
    artifact_id: str | None
    artifact_version: int | None
    updated_at: str
    revision: int
    result_text: str | None
    next_cursor: str | None
    result_available: bool
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    """依据边（§3.3）：链接存在不等于支持断言，``supports_claim`` 未知用 null。"""

    claim_id: str
    source_id: str
    source_version: str
    locator: SourceLocator
    source_exists: bool
    supports_claim: bool | None
    verification: VerificationState
    verified_at: str | None
    limitations: tuple[RangeLimitation, ...]


_EVIDENCE_LINK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "claim_id",
        "source_id",
        "source_version",
        "locator",
        "source_exists",
        "supports_claim",
        "verification",
        "verified_at",
        "limitations",
    }
)


def encode_evidence_link(link: EvidenceLink) -> dict[str, Any]:
    """EvidenceLink → wire 对象（9 键全在）。"""

    return to_wire_dict(link)


def decode_evidence_link(value: object, path: str = "$.evidence_link") -> EvidenceLink:
    """严格解码依据边（9 键全必填、禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _EVIDENCE_LINK_KEYS, path)
    limitations = value["limitations"]
    if not isinstance(limitations, list):
        _fail("invalid_type", f"{path}.limitations")
    return EvidenceLink(
        claim_id=_require_str(value["claim_id"], f"{path}.claim_id"),
        source_id=_require_str(value["source_id"], f"{path}.source_id"),
        source_version=_require_str(value["source_version"], f"{path}.source_version"),
        locator=decode_source_locator(value["locator"], f"{path}.locator"),
        source_exists=_require_bool(value["source_exists"], f"{path}.source_exists"),
        supports_claim=_opt_bool(value["supports_claim"], f"{path}.supports_claim"),
        verification=_require_enum(
            value["verification"], VerificationState, f"{path}.verification", "invalid_verification_state"
        ),
        verified_at=_opt_timestamp(value["verified_at"], f"{path}.verified_at"),
        limitations=tuple(
            decode_range_limitation(item, f"{path}.limitations[{index}]") for index, item in enumerate(limitations)
        ),
    )


# ---- 可选字段解码基元（null 仅合同允许处；bool 不得冒充整数） ----


def _opt_bool(value: object, path: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        _fail("invalid_type", path)
    return value


def _opt_enum[E: Enum](value: object, enum_cls: type[E], path: str, code: str) -> E | None:
    if value is None:
        return None
    return _require_enum(value, enum_cls, path, code)


def _require_json_text(value: object, path: str) -> CanonicalJson:
    """必填规范 JSON 文本（agent-number-v1/FactValue.raw_value 等）。"""

    if value is None:
        _fail("invalid_type", path)
    text = _require_str(value, path)
    try:
        decode_json_doc(text)
    except AgentValidationError:
        _fail("invalid_json", path)
    return text


# ---- D06 附件预算（REQ D06 / DD §12.4 恰等与超一边界） ----


@dataclass(frozen=True, slots=True)
class AttachmentBudget:
    """D06 附件限额（十进制字节；每文件独立累计，跨文件不合并字符额度）。

    恰等通过、超一拒绝；``None``/未知统计不得据此放开上限（未知由调用方保持
    ``read_state=unknown/partial`` 与 limitation，而不是跳过本类校验）。
    """

    max_files: int = 5
    max_source_bytes: int = 20_000_000
    max_pages: int = 100
    max_sheets: int = 10
    max_rows: int = 50_000
    max_codepoints: int = 100_000
    extract_timeout_s: float = 30.0

    def enforce_file_count(self, count: int, path: str = "$.attachments.file_count") -> None:
        _enforce_limit(count, self.max_files, "attachment_count_exceeded", path)

    def enforce_source_bytes(self, byte_length: int, path: str = "$.attachments.source_bytes") -> None:
        _enforce_limit(byte_length, self.max_source_bytes, "attachment_bytes_exceeded", path)

    def enforce_pages(self, pages: int, path: str = "$.attachments.pages") -> None:
        _enforce_limit(pages, self.max_pages, "attachment_pages_exceeded", path)

    def enforce_sheets(self, sheets: int, path: str = "$.attachments.sheets") -> None:
        _enforce_limit(sheets, self.max_sheets, "attachment_sheets_exceeded", path)

    def enforce_rows(self, rows: int, path: str = "$.attachments.rows") -> None:
        _enforce_limit(rows, self.max_rows, "attachment_rows_exceeded", path)

    def enforce_codepoints(self, codepoints: int, path: str = "$.attachments.codepoints") -> None:
        _enforce_limit(codepoints, self.max_codepoints, "attachment_codepoints_exceeded", path)

    def enforce_elapsed(self, elapsed_s: float, path: str = "$.attachments.elapsed_s") -> None:
        if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, (int, float)):
            _fail("invalid_type", path)
        if elapsed_s > self.extract_timeout_s:
            _fail("attachment_time_exceeded", path)


#: 首版 D06 默认预算（UI 提前说明限额与 worker 共用）。
DEFAULT_ATTACHMENT_BUDGET: Final = AttachmentBudget()


def _enforce_limit(value: object, limit: int, code: str, path: str) -> None:
    """整数限额校验：bool/非整数拒绝；恰等通过、超一拒绝。"""

    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_type", path)
    if value < 0:
        _fail("invalid_range", path)
    if value > limit:
        _fail(code, path)


#: 业务来源登记的消费者类别（§9.10 consumer_kind 子集）。
#: ``draft`` = 研究发起前的草稿引用（R2-I06）：业务入口只登记「准确对象/版本」，
#: 发送/受理时再按截止界重判准入（草稿期不冒充已准入）。
SOURCE_REGISTRATION_CONSUMER_KINDS: Final[frozenset[str]] = frozenset({"execution", "round", "message", "draft"})


@dataclass(frozen=True, slots=True)
class BusinessSourceItem:
    """一条业务来源（元数据 + 核验状态）：登记事实，不等同读取或支持断言。"""

    source: SourceRef
    verification: VerificationState


@dataclass(frozen=True, slots=True)
class BusinessSourceRegistrationRequest:
    """来源登记请求（同事务 asset + link；仅显式选择集合）。"""

    user_scope: str
    consumer_kind: str
    consumer_id: str
    consumer_version: str
    items: tuple[BusinessSourceItem, ...]


@dataclass(frozen=True, slots=True)
class BusinessSourceRegistrationReceipt:
    """登记回执：已登记（或幂等复用）的来源版本与边身份。"""

    registered: tuple[SourceVersionRef, ...]
    link_ids: tuple[str, ...]
    replayed: bool
    #: 事后解释用途下「决策界之后才披露」的补充资料（可解释、不得伪当时已知/入历史评分）。
    retrospective: tuple[SourceVersionRef, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceRegistrationRequest:
    """含用途/时点的登记请求（PIT 在服务层拒绝，DAOs 只落已核事实）。

    ``decision_cutoff`` 为事件决策界（历史决策/评分严格不可越过）；
    ``explanation_cutoff`` 仅事后解释用途使用，是解释资料可得界（可晚于决策界），
    落在两者之间的来源登记为 ``retrospective``，不得用于历史评分。
    """

    user_scope: str
    consumer_kind: str
    consumer_id: str
    consumer_version: str
    purpose: Purpose
    decision_cutoff: str | None
    selected: tuple[BusinessSourceItem, ...]
    unselected: tuple[BusinessSourceItem, ...] = ()
    explanation_cutoff: str | None = None


# ---------------------------------------------------------------------------
# E-02 共享 MCP 契约（§10.4 / HANDOFF §9）——本轮只承载静态可验证的准入合同；
# SDK transport 内部插入点、预算数值与每 send 门仍为 needs-spike，代码不冒充已验证。
# ---------------------------------------------------------------------------


class MCPTransportKind(Enum):
    """MCP 传输（唯一 wire 拼写）。SSE 仅在兼容契约对齐后由用户显式选择。"""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


@dataclass(frozen=True, slots=True)
class MCPServiceIdentity:
    """共享连接的稳定 key：服务身份 + 配置版本；显示名不参与（HANDOFF §9）。"""

    service_id: str
    config_version: int

    def stable_key(self) -> str:
        """稳定键；每次调用都复核身份与版本合法（不缓存非法输入）。"""

        service_id = require_identity(self.service_id, "$.mcp_service.service_id")
        config_version = require_revision(self.config_version, "$.mcp_service.config_version")
        return stable_id("mcp_service", (service_id, str(config_version)))


_MCP_SERVICE_IDENTITY_KEYS: Final = frozenset({"service_id", "config_version"})


def decode_mcp_service_identity(value: object, path: str = "$.mcp_service") -> MCPServiceIdentity:
    """严格解码共享服务身份：全键必填、禁额外键、版本从 1 起。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _MCP_SERVICE_IDENTITY_KEYS, path)
    service_id = value["service_id"]
    if not isinstance(service_id, str):
        _fail("invalid_type", f"{path}.service_id")
    return MCPServiceIdentity(
        service_id=_require_str(service_id, f"{path}.service_id"),
        config_version=require_revision(value["config_version"], f"{path}.config_version"),
    )


def encode_mcp_service_identity(identity: MCPServiceIdentity) -> dict[str, Any]:
    """MCPServiceIdentity → wire 两键（service_id/config_version）；显示名不参与。"""

    return {
        "service_id": require_identity(identity.service_id, "$.mcp_service.service_id"),
        "config_version": require_revision(identity.config_version, "$.mcp_service.config_version"),
    }


#: 连接与单次工具各自独立的单一总 deadline（秒）；progress/分片不重置（HANDOFF §9）。
MCP_DEFAULT_DEADLINE_S: Final = 30
MCP_MIN_DEADLINE_S: Final = 1
MCP_MAX_DEADLINE_S: Final = 120


def validate_mcp_deadline(value: object, path: str = "$.mcp.deadline_s") -> int:
    """deadline 校验：``None`` 取默认 30；仅整数 1—120 合法（bool/浮点/字符串拒绝）。"""

    if value is None:
        return MCP_DEFAULT_DEADLINE_S
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_deadline", path)
    if value < MCP_MIN_DEADLINE_S or value > MCP_MAX_DEADLINE_S:
        _fail("invalid_deadline", path)
    return value


#: 前置接收/留存预算字段；每个字段对应 HANDOFF §9 的一个预算族
#: （接收字节 / 解码表示 / 结构深度 / 目录累计数量 / 目录累计字节 /
#: schema 累计 / 单结果 / 受管留存）。
MCP_BUDGET_FIELDS: Final[tuple[str, ...]] = (
    "receive_bytes",
    "decoded_bytes",
    "structure_depth",
    "catalog_items",
    "catalog_bytes",
    "schema_bytes",
    "single_result_bytes",
    "retention_bytes",
)


@dataclass(frozen=True, slots=True)
class MCPBudgetProfile:
    """启连/发现/调用前必须冻结的接收预算；``None`` 表示未冻结（该传输不准入）。

    设计明确不新增无依据产品数值：本类不提供任何默认上限，未冻结即拒绝准入。
    """

    receive_bytes: int | None = None
    decoded_bytes: int | None = None
    structure_depth: int | None = None
    catalog_items: int | None = None
    catalog_bytes: int | None = None
    schema_bytes: int | None = None
    single_result_bytes: int | None = None
    retention_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in MCP_BUDGET_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                _fail("invalid_budget", f"$.mcp.budget.{name}")

    def unfrozen_fields(self) -> tuple[str, ...]:
        """仍未冻结的预算字段（按固定声明顺序）。"""

        return tuple(name for name in MCP_BUDGET_FIELDS if getattr(self, name) is None)

    def is_admitted(self) -> bool:
        """全部预算族冻结才允许启连/发现/调用；任一缺失即不准入。"""

        return not self.unfrozen_fields()


def resolve_tool_allowlist(
    *,
    service_tools: Iterable[str],
    role_tools: Iterable[str],
    request_tools: Iterable[str],
    permission_tools: Iterable[str],
) -> tuple[str, ...]:
    """工具白名单 = 服务 ∩ 角色 ∩ 请求 ∩ 新鲜许可；空集合确实为空（真空）。

    ``readOnlyHint`` 与工具是否被发现不参与本判定；返回值按名称排序以保证确定顺序。
    """

    scopes = []
    for scope in (service_tools, role_tools, request_tools, permission_tools):
        names = set()
        for name in scope:
            names.add(require_identity(name, "$.mcp.tool_name"))
        scopes.append(names)
    return tuple(sorted(scopes[0].intersection(*scopes[1:])))


@dataclass(frozen=True, slots=True)
class MCPReadOnlyAudit:
    """只读审核记录：绑定实际实现指纹，schema 相同但载荷替换即失效（§10.4）。"""

    service: MCPServiceIdentity
    tool_scope: tuple[str, ...]
    #: 本机实际实现（executable/入口/关键载荷/依赖版本）摘要；``None`` = 未核定。
    implementation_fingerprint: str | None
    evidence_source: str
    reviewed_at: str
    invalidation_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCPReadOnlyAdmission:
    """只读准入结论：``reason`` 为稳定原因码，``None`` 仅当准入。"""

    admitted: bool
    reason: str | None


_MCP_AUDIT_KEYS: Final = frozenset(
    {
        "service",
        "tool_scope",
        "implementation_fingerprint",
        "evidence_source",
        "reviewed_at",
        "invalidation_conditions",
    }
)


def _validate_mcp_read_only_audit(audit: MCPReadOnlyAudit) -> None:
    audit.service.stable_key()
    for name in audit.tool_scope:
        require_identity(name, "$.mcp.audit.tool_scope")
    if audit.implementation_fingerprint is not None:
        _require_digest(audit.implementation_fingerprint, "$.mcp.audit.implementation_fingerprint")
    _require_str(audit.evidence_source, "$.mcp.audit.evidence_source")
    require_utc_timestamp(audit.reviewed_at, "$.mcp.audit.reviewed_at")
    for condition in audit.invalidation_conditions:
        require_identity(condition, "$.mcp.audit.invalidation_conditions")


def encode_mcp_read_only_audit(audit: MCPReadOnlyAudit) -> dict[str, Any]:
    """审核记录 → wire（全键；fingerprint 可为 null 表示未核定）。"""

    _validate_mcp_read_only_audit(audit)
    return {
        "service": {
            "service_id": audit.service.service_id,
            "config_version": audit.service.config_version,
        },
        "tool_scope": list(audit.tool_scope),
        "implementation_fingerprint": audit.implementation_fingerprint,
        "evidence_source": audit.evidence_source,
        "reviewed_at": audit.reviewed_at,
        "invalidation_conditions": list(audit.invalidation_conditions),
    }


def decode_mcp_read_only_audit(value: object, path: str = "$.mcp.audit") -> MCPReadOnlyAudit:
    """严格解码审核记录：全键必填、禁额外键（``read_only_hint`` 不是审核字段）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _MCP_AUDIT_KEYS, path)
    fingerprint = value["implementation_fingerprint"]
    if fingerprint is not None:
        _require_digest(fingerprint, f"{path}.implementation_fingerprint")
    audit = MCPReadOnlyAudit(
        service=decode_mcp_service_identity(value["service"], f"{path}.service"),
        tool_scope=_require_str_list(value["tool_scope"], f"{path}.tool_scope", allow_empty=True),
        implementation_fingerprint=cast("str | None", fingerprint),
        evidence_source=_require_str(value["evidence_source"], f"{path}.evidence_source"),
        reviewed_at=require_utc_timestamp(value["reviewed_at"], f"{path}.reviewed_at"),
        invalidation_conditions=_require_str_list(value["invalidation_conditions"], f"{path}.invalidation_conditions"),
    )
    _validate_mcp_read_only_audit(audit)
    return audit


def evaluate_read_only_audit(
    audit: MCPReadOnlyAudit | None,
    current_implementation_fingerprint: str | None,
) -> MCPReadOnlyAdmission:
    """只读准入：无审核、实现未核定或实现变化一律拒绝（未知不当通过）。"""

    if audit is None:
        return MCPReadOnlyAdmission(False, "audit_missing")
    _validate_mcp_read_only_audit(audit)
    if audit.implementation_fingerprint is None:
        return MCPReadOnlyAdmission(False, "implementation_unverified")
    if current_implementation_fingerprint is None:
        return MCPReadOnlyAdmission(False, "implementation_unverified")
    _require_digest(current_implementation_fingerprint, "$.mcp.current_implementation_fingerprint")
    if current_implementation_fingerprint != audit.implementation_fingerprint:
        return MCPReadOnlyAdmission(False, "implementation_changed")
    return MCPReadOnlyAdmission(True, None)


# ---------------------------------------------------------------------------
# E-03 D02 Skill 预算与角色分配契约（§10.3 / DD §12 阈值表）
# ---------------------------------------------------------------------------

#: Skill 包四阈值（D02，设计 §12 阈值表冻结值）。
SKILL_MAX_ARCHIVE_BYTES: Final = 16_777_216
SKILL_MAX_EXPANDED_BYTES: Final = 64 * 1024 * 1024
SKILL_MAX_OBJECTS: Final = 1024
SKILL_MAX_PREFLIGHT_SECONDS: Final = 10.0


@dataclass(frozen=True, slots=True)
class SkillBudget:
    """Skill 预检/展开预算：恰等通过、超一拒绝；不在展开后才检查。"""

    max_archive_bytes: int = SKILL_MAX_ARCHIVE_BYTES
    max_expanded_bytes: int = SKILL_MAX_EXPANDED_BYTES
    max_objects: int = SKILL_MAX_OBJECTS
    max_preflight_seconds: float = SKILL_MAX_PREFLIGHT_SECONDS

    def enforce_archive_bytes(self, byte_length: int, path: str = "$.skill.archive_bytes") -> None:
        _enforce_limit(byte_length, self.max_archive_bytes, "skill_archive_bytes_exceeded", path)

    def enforce_expanded_bytes(self, byte_length: int, path: str = "$.skill.expanded_bytes") -> None:
        _enforce_limit(byte_length, self.max_expanded_bytes, "skill_expanded_bytes_exceeded", path)

    def enforce_objects(self, count: int, path: str = "$.skill.objects") -> None:
        _enforce_limit(count, self.max_objects, "skill_objects_exceeded", path)

    def enforce_elapsed(self, elapsed_s: float, path: str = "$.skill.elapsed_s") -> None:
        if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, (int, float)):
            _fail("invalid_type", path)
        if elapsed_s > self.max_preflight_seconds:
            _fail("skill_time_exceeded", path)


#: 首版 D02 默认预算（管理入口与 worker 共用同一实例语义）。
DEFAULT_SKILL_BUDGET: Final = SkillBudget()


@dataclass(frozen=True, slots=True)
class SkillVersionRef:
    """Skill 版本身份：skill_id + 版本（从 1 起）；显示名不参与身份。"""

    skill_id: str
    version: int

    def stable_key(self) -> str:
        return stable_id(
            "skill_version",
            (
                require_identity(self.skill_id, "$.skill.skill_id"),
                str(require_revision(self.version, "$.skill.version")),
            ),
        )


def _skill_keys(refs: Iterable[SkillVersionRef]) -> set[tuple[str, int]]:
    return {(ref.skill_id, ref.version) for ref in refs}


_SKILL_VERSION_REF_KEYS: Final = frozenset({"skill_id", "version"})


def encode_skill_version_ref(ref: SkillVersionRef) -> dict[str, Any]:
    """SkillVersionRef → wire 两键（skill_id/version）；显示名不参与身份。"""

    return {
        "skill_id": require_identity(ref.skill_id, "$.skill.skill_id"),
        "version": require_revision(ref.version, "$.skill.version"),
    }


def decode_skill_version_ref(value: object, path: str = "$.skill_version_ref") -> SkillVersionRef:
    """严格解码 SkillVersionRef：全键必填、禁额外键、版本从 1 起（bool 拒绝）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SKILL_VERSION_REF_KEYS, path)
    return SkillVersionRef(
        skill_id=require_identity(value["skill_id"], f"{path}.skill_id"),
        version=require_revision(value["version"], f"{path}.version"),
    )


def validate_skill_assignment(
    *,
    enabled: Sequence[SkillVersionRef],
    defaults: Sequence[SkillVersionRef],
) -> None:
    """角色默认 Skill 必须是启用集合子集（默认⊆启用，不以默认隐式启用）。"""

    if not _skill_keys(defaults).issubset(_skill_keys(enabled)):
        _fail("skill_default_not_enabled", "$.agent.skills.defaults")


def resolve_skill_selection(
    *,
    enabled: Iterable[SkillVersionRef],
    defaults: Iterable[SkillVersionRef],
    hand_picked: Iterable[SkillVersionRef],
    is_child: bool,
) -> tuple[SkillVersionRef, ...]:
    """实际能力集合：主 = 默认 ∪ 手选，子 = 仅自身默认（不继承主手选）。

    只在启用集合内取交集；空集合确实为空（不解释为「全部」）。
    同一 ``skill_id`` 在候选中出现多个不同版本即冲突（F28）：默认与手选互相冲突、
    手选内部冲突均拒绝，不静默任选其一；冲突时不产出部分选择。
    """

    enabled_keys = _skill_keys(enabled)
    candidates = list(defaults)
    if not is_child:
        candidates.extend(hand_picked)
    chosen: dict[str, int] = {}
    for ref in candidates:
        if (ref.skill_id, ref.version) not in enabled_keys:
            continue
        existing_version = chosen.get(ref.skill_id)
        if existing_version is not None and existing_version != ref.version:
            _fail("skill_version_conflict", "$.agent.skills")
        chosen[ref.skill_id] = ref.version
    return tuple(SkillVersionRef(skill_id=skill_id, version=chosen[skill_id]) for skill_id in sorted(chosen))


# ---------------------------------------------------------------------------
# E-03-4 Skill 资产与角色版本引用（§9.10 / §10.3 / F12-F13）
# ---------------------------------------------------------------------------

#: Skill 资产元数据与角色分配 wire 结构版本。
SKILL_ASSET_METADATA_VERSION: Final = 1
ROLE_SKILL_ASSIGNMENT_VERSION: Final = 1

_ROLE_SKILL_ASSIGNMENT_KEYS: Final = frozenset({"schema_version", "role_id", "role_version", "enabled", "defaults"})


@dataclass(frozen=True, slots=True)
class SkillAssetRegistration:
    """受管 Skill 版本落库请求（agent_asset_version，§9.10）。

    ``digest`` 为规范内容摘要；``archive_digest``、文件数/展开字节、许可、依赖与
    行为字段全部进入 ``metadata_payload``，不复制脚本或凭据到数据库。
    """

    user_scope: str
    skill_id: str
    version: int
    display_name: str
    content_digest: str
    normalized_name: str
    license_id: str
    archive_digest: str | None
    file_count: int
    expanded_bytes: int
    source_kind: str = "zip"
    dependencies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    ignored_behavioral_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillAssetReceipt:
    """Skill 资产落库回执：同输入重放 ``replayed=True``，零新事实。"""

    user_scope: str
    skill_id: str
    version: int
    content_digest: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class RoleSkillAssignment:
    """角色版本对 Skill 版本的引用（F13）。

    ``defaults`` 必须是 ``enabled`` 的精确版本子集；空集合确实为空（不解释为全部）。
    角色版本不可变：同 role_id/role_version 不同内容报冲突，不静默覆盖。
    """

    role_id: str
    role_version: str
    enabled: tuple[SkillVersionRef, ...]
    defaults: tuple[SkillVersionRef, ...]

    def wire(self) -> dict[str, Any]:
        """规范 wire（键排序由 CanonicalJson 承担；集合按身份排序稳定）。"""

        return {
            "schema_version": ROLE_SKILL_ASSIGNMENT_VERSION,
            "role_id": require_identity(self.role_id, "$.role.role_id"),
            "role_version": require_identity(self.role_version, "$.role.role_version"),
            "enabled": [encode_skill_version_ref(ref) for ref in _sorted_skill_refs(self.enabled)],
            "defaults": [encode_skill_version_ref(ref) for ref in _sorted_skill_refs(self.defaults)],
        }


@dataclass(frozen=True, slots=True)
class RoleSkillAssignmentReceipt:
    """角色版本落库回执：摘要绑定完整分配内容。"""

    user_scope: str
    role_id: str
    role_version: str
    digest: str
    linked_skill_versions: tuple[SkillVersionRef, ...]
    replayed: bool


def validate_role_skill_assignment(assignment: RoleSkillAssignment) -> None:
    """校验角色分配：身份非空、无重复版本、默认⊆启用（F13）。"""

    require_identity(assignment.role_id, "$.role.role_id")
    require_identity(assignment.role_version, "$.role.role_version")
    for field_name, refs in (("enabled", assignment.enabled), ("defaults", assignment.defaults)):
        for index, ref in enumerate(refs):
            require_identity(ref.skill_id, f"$.role.{field_name}[{index}].skill_id")
            require_revision(ref.version, f"$.role.{field_name}[{index}].version")
        if len(_skill_keys(refs)) != len(refs):
            _fail("duplicate_skill_version", f"$.role.{field_name}")
    validate_skill_assignment(enabled=assignment.enabled, defaults=assignment.defaults)


def decode_role_skill_assignment(value: object, path: str = "$.role_skill_assignment") -> RoleSkillAssignment:
    """严格解码角色分配 wire：全键必填、禁额外键、版本从 1 起、默认⊆启用。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _ROLE_SKILL_ASSIGNMENT_KEYS, path)
    if value["schema_version"] != ROLE_SKILL_ASSIGNMENT_VERSION or isinstance(value["schema_version"], bool):
        _fail("invalid_schema_version", f"{path}.schema_version")
    enabled_value = value["enabled"]
    defaults_value = value["defaults"]
    if not isinstance(enabled_value, list) or not isinstance(defaults_value, list):
        _fail("invalid_type", f"{path}.enabled")
    enabled = tuple(
        decode_skill_version_ref(item, f"{path}.enabled[{index}]") for index, item in enumerate(enabled_value)
    )
    defaults = tuple(
        decode_skill_version_ref(item, f"{path}.defaults[{index}]") for index, item in enumerate(defaults_value)
    )
    assignment = RoleSkillAssignment(
        role_id=require_identity(value["role_id"], f"{path}.role_id"),
        role_version=require_identity(value["role_version"], f"{path}.role_version"),
        enabled=enabled,
        defaults=defaults,
    )
    validate_role_skill_assignment(assignment)
    return assignment


def role_skill_assignment_digest(assignment: RoleSkillAssignment) -> str:
    """分配摘要：覆盖角色身份与完整启用/默认集合（规范字节）。"""

    validate_role_skill_assignment(assignment)
    return sha256_hex(json_doc(assignment.wire()))


# ---------------------------------------------------------------------------
# E-06 联合清理契约（§9.11 / §10.7 / SQ-10）——本轮承载静态可验证的准入与状态合同
# ---------------------------------------------------------------------------

#: 联合清理单批准确 ID 上限（§12 阈值表 200/201）。
CLEANUP_MAX_BATCH_IDS: Final = 200

#: 九类原生目标（表, 常量匹配键）；只按常量键参数化删除，禁止 LIKE/前缀/全表。
CLEANUP_NATIVE_TARGETS: Final[tuple[tuple[str, str], ...]] = (
    ("dbos.workflow_status", "workflow_uuid"),
    ("dbos.workflow_input", "workflow_uuid"),
    ("dbos.workflow_output", "workflow_uuid"),
    ("dbos.operation_outputs", "workflow_uuid"),
    ("dbos.workflow_events", "workflow_uuid"),
    ("dbos.workflow_events_history", "workflow_uuid"),
    ("dbos.streams", "workflow_uuid"),
    ("dbos.notifications", "destination_uuid"),
    ("agent_ds.datasource_outputs", "workflow_id"),
)

#: NOT NULL 文本列的固定 tombstone：绝不含原正文/标题/文件名。
CLEANUP_TOMBSTONE: Final = "[已清理]"

#: NOT NULL 摘要列的固定 tombstone 摘要（§10.7）：固定公开常量的 SHA-256，
#: 不携带、也不能被低熵字典反推原正文摘要。
CLEANUP_TOMBSTONE_DIGEST: Final = sha256_hex(CLEANUP_TOMBSTONE.encode("utf-8"))


class CleanupPhase(Enum):
    """清理作业阶段：准备 → （中断/失败）重试待处理 → 逐域查 0 后 verified。"""

    PREPARED = "prepared"
    RETRY_PENDING = "retry_pending"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class CleanupPrecondition:
    """统一清理准入谓词（Q02）：准备/确认/重试共用。"""

    scope_settled: bool
    writers_exited: bool
    closure_known: bool
    preview_valid: bool
    new_reference_conflict: bool


@dataclass(frozen=True, slots=True)
class CleanupAdmission:
    allowed: bool
    reason: str | None


def evaluate_cleanup_admission(precondition: CleanupPrecondition) -> CleanupAdmission:
    """按固定顺序判定；未知不当通过，新引用按原 fence 仲裁。"""

    if not precondition.scope_settled:
        return CleanupAdmission(False, "scope_not_settled")
    if not precondition.writers_exited:
        return CleanupAdmission(False, "writers_still_running")
    if not precondition.closure_known:
        return CleanupAdmission(False, "closure_unknown")
    if not precondition.preview_valid:
        return CleanupAdmission(False, "preview_invalid")
    if precondition.new_reference_conflict:
        return CleanupAdmission(False, "new_reference_conflict")
    return CleanupAdmission(True, None)


def normalize_cleanup_ids(ids: Iterable[object]) -> tuple[str, ...]:
    """非空/去重/排序；超过 200 明确拒绝并提示分批次确认，不递归扩集合。"""

    normalized: set[str] = set()
    for value in ids:
        if not isinstance(value, str) or not value:
            _fail("invalid_cleanup_id", "$.cleanup.ids")
        normalized.add(value)
    if len(normalized) > CLEANUP_MAX_BATCH_IDS:
        _fail("cleanup_batch_limit_exceeded", "$.cleanup.ids")
    return tuple(sorted(normalized))


#: ``verified`` 阶段必须覆盖的计数域：九类原生目标，其中 ``agent_ds.datasource_outputs``
#: 为应用 Datasource 输出正文域。缺任一域（含应用正文域）都不得置 verified/complete。
CLEANUP_VERIFIED_DOMAINS: Final[tuple[str, ...]] = tuple(table for table, _key in CLEANUP_NATIVE_TARGETS)


def _is_strict_count(value: object) -> bool:
    """计数必须是严格 int：``bool`` 是 ``int`` 子类，``False == 0`` 不得冒充已清零。"""

    return isinstance(value, int) and not isinstance(value, bool)


def validate_cleanup_phase_write(
    phase: CleanupPhase,
    verified_counts: Mapping[str, object] | None,
    *,
    complete: bool,
) -> None:
    """核验清理阶段写入的计数与 ``complete`` 一致性（D31 / §9.11 / §10.7）。

    - 任一计数域必须是严格 int（``bool`` 拒绝，因 ``False == 0`` 会冒充已清零）；
    - ``verified``：必须 ``complete=True``，覆盖全部九域（含应用 Datasource 输出
      正文域）且逐域严格为 0；缺域/额外域/非零/``complete=False`` 一律拒绝；
    - 其他阶段：必须 ``complete=False``；计数可缺省或部分（重试允许非零），但出现
      时必须为非负严格 int。
    """

    if not isinstance(phase, CleanupPhase):
        _fail("invalid_type", "$.cleanup.phase")
    if phase is CleanupPhase.VERIFIED:
        if complete is not True:
            _fail("invalid_cleanup_state", "$.cleanup.complete")
        if not isinstance(verified_counts, Mapping):
            _fail("missing_field", "$.cleanup.verified_counts")
        keys = set(verified_counts.keys())
        if not all(isinstance(key, str) and key for key in keys):
            _fail("invalid_type", "$.cleanup.verified_counts")
        required = frozenset(CLEANUP_VERIFIED_DOMAINS)
        missing = sorted(required - keys)
        if missing:
            _fail("missing_field", f"$.cleanup.verified_counts.{missing[0]}")
        extra = sorted(keys - required)
        if extra:
            _fail("unknown_field", f"$.cleanup.verified_counts.{extra[0]}")
        for table in CLEANUP_VERIFIED_DOMAINS:
            value = verified_counts[table]
            if isinstance(value, bool) or not isinstance(value, int):
                _fail("invalid_type", f"$.cleanup.verified_counts.{table}")
            if value != 0:
                _fail("invalid_range", f"$.cleanup.verified_counts.{table}")
        return
    if complete is not False:
        _fail("invalid_cleanup_state", "$.cleanup.complete")
    if verified_counts is None:
        return
    if not isinstance(verified_counts, Mapping):
        _fail("invalid_type", "$.cleanup.verified_counts")
    for key, value in verified_counts.items():
        if not isinstance(key, str) or not key:
            _fail("invalid_type", "$.cleanup.verified_counts")
        if isinstance(value, bool) or not isinstance(value, int):
            _fail("invalid_type", f"$.cleanup.verified_counts.{key}")
        if value < 0:
            _fail("invalid_range", f"$.cleanup.verified_counts.{key}")


@dataclass(frozen=True, slots=True)
class CleanupState:
    """清理作业状态：fence 提交后才允许删除；九域全部查 0 才 verified。"""

    phase: CleanupPhase
    fence_committed: bool
    exact_ids: tuple[str, ...] = ()

    def can_delete(self) -> bool:
        return self.fence_committed and self.phase is not CleanupPhase.VERIFIED

    def can_mark_verified(self, verified_counts: Mapping[str, object]) -> bool:
        if not self.fence_committed:
            return False
        for table, _key in CLEANUP_NATIVE_TARGETS:
            value = verified_counts.get(table)
            if not _is_strict_count(value) or value != 0:
                return False
        return True


def tombstone_text(original: str | None) -> str:
    """NOT NULL 文本字段清理后的固定占位；不保留任何原文。"""

    del original
    return CLEANUP_TOMBSTONE


def normalize_cleanup_asset_keys(keys: Iterable[object]) -> tuple[tuple[str, str], ...]:
    """资产版本键 ``(asset_id, asset_version)``：非空/去重/排序 + 200 上限。

    资产版本是稳定身份（不是正文），单独规范化以免与原生 ID 混算。
    """

    normalized: set[tuple[str, str]] = set()
    for key in keys:
        if not isinstance(key, (tuple, list)) or len(key) != 2:
            _fail("invalid_cleanup_asset_key", "$.cleanup.asset_keys")
        asset_id, asset_version = key
        if not isinstance(asset_id, str) or not asset_id:
            _fail("invalid_cleanup_asset_key", "$.cleanup.asset_keys")
        if not isinstance(asset_version, str) or not asset_version:
            _fail("invalid_cleanup_asset_key", "$.cleanup.asset_keys")
        normalized.add((asset_id, asset_version))
    if len(normalized) > CLEANUP_MAX_BATCH_IDS:
        _fail("cleanup_batch_limit_exceeded", "$.cleanup.asset_keys")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class CleanupIdentitySet:
    """清理的不可变身份集合（§9.11）：原生 ID + 应用会话 + 资产版本键。

    只承载**身份**，不承载正文、摘要或路径；集合一经 fence 冻结不得扩缩。
    """

    native_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    asset_keys: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CleanupJobRecord:
    """清理作业事实（§9.11/§10.7）：固定集合 + 原预览摘要 + 修订 + fence。"""

    user_scope: str
    cleanup_job_id: str
    owner_ref: str
    preview_digest: str
    expected_revision: int
    exact_ids: tuple[str, ...]
    phase: CleanupPhase
    fence_committed: bool
    session_ids: tuple[str, ...] = ()
    asset_keys: tuple[tuple[str, str], ...] = ()
    prior_error_category: str | None = None
    """上次失败所处阶段（§10.7 重试豁免判据：仅「已尝试删除」的失败可放行原生行缺失）。"""

    def identities(self) -> CleanupIdentitySet:
        return CleanupIdentitySet(self.exact_ids, self.session_ids, self.asset_keys)


_CLEANUP_IDENTITY_SET_KEYS: Final = frozenset({"native_ids", "session_ids", "asset_keys"})


def encode_cleanup_identity_set(record: CleanupJobRecord) -> dict[str, object]:
    """规范 JSONB 载形：集合先规范化，键序固定，供持久化与摘要核对。"""

    identities = record.identities()
    return {
        "native_ids": list(normalize_cleanup_ids(identities.native_ids)),
        "session_ids": list(normalize_cleanup_ids(identities.session_ids)),
        "asset_keys": [list(key) for key in normalize_cleanup_asset_keys(identities.asset_keys)],
    }


def decode_cleanup_identity_set(value: object, path: str = "$.cleanup.identity_set") -> CleanupIdentitySet:
    """严格解码身份集合：全键/额外键/类型/上限逐项校验后规范化。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _CLEANUP_IDENTITY_SET_KEYS, path)
    native_ids = value["native_ids"]
    session_ids = value["session_ids"]
    asset_keys = value["asset_keys"]
    if not isinstance(native_ids, list) or not isinstance(session_ids, list) or not isinstance(asset_keys, list):
        _fail("invalid_type", path)
    return CleanupIdentitySet(
        native_ids=normalize_cleanup_ids(native_ids),
        session_ids=normalize_cleanup_ids(session_ids),
        asset_keys=normalize_cleanup_asset_keys(asset_keys),
    )


# ---------------------------------------------------------------------------
# F-03 草稿持久契约（§4.2 save_draft / §9.3 draft_revision+draft_payload）
# ---------------------------------------------------------------------------


#: 草稿载荷 schema 版本：v2 起「研究日期」与「精确截止」分列（I33）。
#: v1 旧稿只有日界 ``cutoff``（纯日期），按只读兼容读取：日期提升为
#: ``research_date``，精确截止保持 ``None``（未知），不得补造（R21）。
DRAFT_PAYLOAD_SCHEMA_VERSION: Final = 2
DRAFT_PAYLOAD_V1_SCHEMA_VERSION: Final = 1

#: v1/v2 共用键（不含两个边界字段）。
_DRAFT_PAYLOAD_COMMON_KEYS: Final = frozenset(
    {
        "schema_version",
        "text",
        "mode",
        "model_selection",
        "source_refs",
        "skill_versions",
        "mcp_selection",
        "purpose",
        "send_target",
        "object_context",
        "role_prompt_selection",
        "customized_fields",
        "defaults_baseline_digest",
    }
)

#: 草稿载荷全键（§4.2 DraftSaveCommand 业务字段 + schema_version 信封）。
DRAFT_PAYLOAD_KEYS: Final = frozenset(_DRAFT_PAYLOAD_COMMON_KEYS | {"research_date", "cutoff"})

#: v1 旧稿键集（仅 ``cutoff`` 日界）：只读兼容输入，不得用于新写入。
DRAFT_PAYLOAD_V1_KEYS: Final = frozenset(_DRAFT_PAYLOAD_COMMON_KEYS | {"cutoff"})

#: 草稿载荷中**不得出现**的键（递归小写比对）：凭据与有效批准不随草稿持久化
#: （§9.3「无凭据/有效批准/额外源副本」、R9/R10）。
_DRAFT_FORBIDDEN_KEYS: Final = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "approval",
        "approvals",
        "grant",
        "grants",
    }
)

#: 研究日期：日历日（无时区、无时刻）``YYYY-MM-DD``。
_DATE_ONLY_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _reject_draft_forbidden_keys(value: object, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid_json", path)
            if key.lower() in _DRAFT_FORBIDDEN_KEYS:
                _fail("draft_secret_field_forbidden", f"{path}.{key}")
            _reject_draft_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_draft_forbidden_keys(item, f"{path}[{index}]")


def _draft_fragment(value: object, path: str, *, require_source_module: bool = False) -> CanonicalJson:
    """草稿内**不透明选择片段**的规范 JSON 校验（§4.2 未逐字段冻结的引用集合）。

    设计只冻结信封键与「只存引用、不存凭据/批准」的语义；子结构由后续配置域
    （模型/角色/Prompt/发送目标）拥有。这里执行：必须是 JSON 对象、经
    :func:`json_doc` 拒绝 float/NaN/非 JSON 类型、递归禁凭据/批准键；
    ``object_context`` 额外要求非空 ``source_module``（准确业务入口）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    try:
        doc = json_doc(value)
    except AgentValidationError as exc:
        # json_doc 以 "$" 为根：把片段路径译回调用方路径，错误定位仍准确。
        suffix = exc.field_path[1:] if exc.field_path.startswith("$") else f".{exc.field_path}"
        _fail(exc.code, f"{path}{suffix}")
    _reject_draft_forbidden_keys(value, path)
    if require_source_module:
        source_module = value.get("source_module")
        if not isinstance(source_module, str) or source_module == "":
            _fail("missing_field", f"{path}.source_module")
    return doc


def _sorted_source_refs(refs: Iterable[SourceVersionRef]) -> tuple[SourceVersionRef, ...]:
    items = tuple(refs)
    encoded = [encode_source_version_ref(ref) for ref in items]
    keys = [(item["source_kind"], item["source_id"], item["source_version"]) for item in encoded]
    if len(set(keys)) != len(keys):
        _fail("duplicate_source_ref", "$.payload.source_refs")
    return tuple(sorted(items, key=lambda ref: (ref.source_kind.value, ref.source_id, ref.source_version)))


def _sorted_skill_refs(refs: Iterable[SkillVersionRef]) -> tuple[SkillVersionRef, ...]:
    items = tuple(refs)
    encoded = [encode_skill_version_ref(ref) for ref in items]
    keys = [(item["skill_id"], item["version"]) for item in encoded]
    if len(set(keys)) != len(keys):
        _fail("duplicate_skill_version", "$.payload.skill_versions")
    return tuple(sorted(items, key=lambda ref: (ref.skill_id, ref.version)))


def _sorted_mcp_selection(refs: Iterable[MCPServiceIdentity]) -> tuple[MCPServiceIdentity, ...]:
    items = tuple(refs)
    encoded = [encode_mcp_service_identity(ref) for ref in items]
    keys = [(item["service_id"], item["config_version"]) for item in encoded]
    if len(set(keys)) != len(keys):
        _fail("duplicate_mcp_service", "$.payload.mcp_selection")
    return tuple(sorted(items, key=lambda ref: (ref.service_id, ref.config_version)))


def _validate_research_date(value: object, path: str) -> str:
    """研究日期：仅 ``YYYY-MM-DD`` 日历日（带时刻/时区一律拒绝）。"""

    if not isinstance(value, str) or _DATE_ONLY_RE.fullmatch(value) is None:
        _fail("invalid_research_date", path)
    try:
        dt.fromisoformat(value)
    except ValueError:
        _fail("invalid_research_date", path)
    return value


def _validate_cutoff(value: object, path: str) -> str:
    """精确截止：ISO-8601 时点且必须带时区（与受理 ``decision_cutoff`` 同口径）。

    纯日期会解析为无时区的午夜——语义上只是日界而非精确时刻，必须拒绝，
    避免把「17:00 截止」静默降级成日界（R21）。
    """

    if not isinstance(value, str) or value == "":
        _fail("invalid_cutoff", path)
    try:
        parsed = dt.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_cutoff", path)
    if parsed.tzinfo is None:
        _fail("invalid_cutoff", path)
    return value


def _validate_legacy_cutoff(value: object, path: str) -> str:
    """v1 旧稿 ``cutoff``：只接受纯日期（旧口径日界），仅用于只读兼容。"""

    if not isinstance(value, str) or _DATE_ONLY_RE.fullmatch(value) is None:
        _fail("invalid_cutoff", path)
    try:
        dt.fromisoformat(value)
    except ValueError:
        _fail("invalid_cutoff", path)
    return value


@dataclass(frozen=True, slots=True)
class DraftPayload:
    """草稿当前内容（§9.3 ``draft_payload``）：正文 + 未发送选择，只存引用。

    ``model_selection`` / ``send_target`` / ``object_context`` /
    ``role_prompt_selection`` 为不透明规范 JSON 片段（设计未逐字段冻结其子结构，
    由对应配置域拥有；见 :func:`_draft_fragment`）。``customized_fields`` 与
    ``defaults_baseline_digest`` 成对区分「未触碰默认」与用户手选（§4.2 R03）：
    非空自定义必须带基线摘要，空自定义不得带。

    ``research_date`` 为研究日期（纯日历日 ``YYYY-MM-DD``）；``cutoff`` 为精确
    截止（带时区 ISO-8601 时点，与受理 ``decision_cutoff`` 同口径）。两者分列：
    17:00 截止不会被降级成日界，排队（19:00）也不改写它。``cutoff=None`` 表示
    精确截止未知（未选择或旧 v1 稿仅日界），排队前须澄清（:func:`draft_cutoff_requires_clarification`）。
    """

    schema_version: int
    text: str
    mode: Mode
    model_selection: CanonicalJson | None
    source_refs: tuple[SourceVersionRef, ...]
    skill_versions: tuple[SkillVersionRef, ...]
    mcp_selection: tuple[MCPServiceIdentity, ...]
    purpose: Purpose | None
    research_date: str | None
    cutoff: str | None
    send_target: CanonicalJson | None
    object_context: CanonicalJson | None
    role_prompt_selection: CanonicalJson | None
    customized_fields: tuple[str, ...]
    defaults_baseline_digest: str | None


def draft_cutoff_requires_clarification(payload: DraftPayload) -> bool:
    """精确截止未知时排队前必须澄清，不得按日界或队列时点补造（R21/R24）。

    概念问答（``Purpose.CONCEPT``）没有截止界，不算待澄清；其余用途（含尚未
    选择用途的未完成草稿）以 ``cutoff is None`` 作为「精确截止未知」哨兵。
    """

    return payload.cutoff is None and payload.purpose is not Purpose.CONCEPT


def encode_draft_payload(payload: DraftPayload) -> dict[str, Any]:
    """DraftPayload → 规范 wire 对象（v2 15 键全量，集合规范化排序去重）。"""

    if isinstance(payload.schema_version, bool) or payload.schema_version != DRAFT_PAYLOAD_SCHEMA_VERSION:
        _fail("invalid_schema_version", "$.payload.schema_version")
    if not isinstance(payload.text, str):
        _fail("invalid_type", "$.payload.text")
    if not isinstance(payload.mode, Mode):
        _fail("invalid_tag", "$.payload.mode")
    if payload.purpose is not None and not isinstance(payload.purpose, Purpose):
        _fail("invalid_tag", "$.payload.purpose")
    if payload.model_selection is not None:
        _draft_fragment(decode_json_doc(payload.model_selection), "$.payload.model_selection")
    if payload.send_target is not None:
        _draft_fragment(decode_json_doc(payload.send_target), "$.payload.send_target")
    if payload.object_context is not None:
        _draft_fragment(
            decode_json_doc(payload.object_context),
            "$.payload.object_context",
            require_source_module=True,
        )
    if payload.role_prompt_selection is not None:
        _draft_fragment(decode_json_doc(payload.role_prompt_selection), "$.payload.role_prompt_selection")
    customized = _require_str_list(list(payload.customized_fields), "$.payload.customized_fields", allow_empty=True)
    customized = tuple(sorted(customized))
    if customized:
        if payload.defaults_baseline_digest is None:
            _fail("missing_field", "$.payload.defaults_baseline_digest")
        digest = _require_digest(payload.defaults_baseline_digest, "$.payload.defaults_baseline_digest")
    else:
        if payload.defaults_baseline_digest is not None:
            _fail("invalid_value", "$.payload.defaults_baseline_digest")
        digest = None
    return {
        "schema_version": DRAFT_PAYLOAD_SCHEMA_VERSION,
        "text": payload.text,
        "mode": payload.mode.value,
        "model_selection": None if payload.model_selection is None else decode_json_doc(payload.model_selection),
        "source_refs": [encode_source_version_ref(ref) for ref in _sorted_source_refs(payload.source_refs)],
        "skill_versions": [encode_skill_version_ref(ref) for ref in _sorted_skill_refs(payload.skill_versions)],
        "mcp_selection": [encode_mcp_service_identity(ref) for ref in _sorted_mcp_selection(payload.mcp_selection)],
        "purpose": None if payload.purpose is None else payload.purpose.value,
        "research_date": (
            None
            if payload.research_date is None
            else _validate_research_date(payload.research_date, "$.payload.research_date")
        ),
        "cutoff": None if payload.cutoff is None else _validate_cutoff(payload.cutoff, "$.payload.cutoff"),
        "send_target": None if payload.send_target is None else decode_json_doc(payload.send_target),
        "object_context": None if payload.object_context is None else decode_json_doc(payload.object_context),
        "role_prompt_selection": (
            None if payload.role_prompt_selection is None else decode_json_doc(payload.role_prompt_selection)
        ),
        "customized_fields": list(customized),
        "defaults_baseline_digest": digest,
    }


def decode_draft_payload(value: object, path: str = "$.payload") -> DraftPayload:
    """严格解码草稿载荷：全键必填、禁额外键、类型/标签/摘要逐项校验。

    v2 为当前写入形态（研究日期 + 精确截止分列）。v1 旧稿只读兼容：旧
    ``cutoff`` 为纯日期日界，提升为 ``research_date``；精确 ``cutoff`` 置
    ``None``（未知），不补造时点，排队前须澄清（R21）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    if "schema_version" not in value:
        _fail("missing_field", f"{path}.schema_version")
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or schema_version not in (
        DRAFT_PAYLOAD_V1_SCHEMA_VERSION,
        DRAFT_PAYLOAD_SCHEMA_VERSION,
    ):
        _fail("invalid_schema_version", f"{path}.schema_version")
    if schema_version == DRAFT_PAYLOAD_V1_SCHEMA_VERSION:
        _require_keys(value, DRAFT_PAYLOAD_V1_KEYS, path)
        legacy_cutoff = value["cutoff"]
        research_date = None if legacy_cutoff is None else _validate_legacy_cutoff(legacy_cutoff, f"{path}.cutoff")
        cutoff = None
    else:
        _require_keys(value, DRAFT_PAYLOAD_KEYS, path)
        research_date = (
            None
            if value["research_date"] is None
            else _validate_research_date(value["research_date"], f"{path}.research_date")
        )
        cutoff = None if value["cutoff"] is None else _validate_cutoff(value["cutoff"], f"{path}.cutoff")
    text = value["text"]
    if not isinstance(text, str):
        _fail("invalid_type", f"{path}.text")
    mode_raw = value["mode"]
    if not isinstance(mode_raw, str):
        _fail("invalid_tag", f"{path}.mode")
    try:
        mode = Mode(mode_raw)
    except ValueError:
        _fail("invalid_tag", f"{path}.mode")
    purpose_raw = value["purpose"]
    purpose: Purpose | None
    if purpose_raw is None:
        purpose = None
    elif isinstance(purpose_raw, str):
        try:
            purpose = Purpose(purpose_raw)
        except ValueError:
            _fail("invalid_tag", f"{path}.purpose")
    else:
        _fail("invalid_tag", f"{path}.purpose")
    source_refs_raw = value["source_refs"]
    skill_versions_raw = value["skill_versions"]
    mcp_selection_raw = value["mcp_selection"]
    customized_raw = value["customized_fields"]
    if not isinstance(source_refs_raw, list) or not isinstance(skill_versions_raw, list):
        _fail("invalid_type", f"{path}.source_refs")
    if not isinstance(mcp_selection_raw, list) or not isinstance(customized_raw, list):
        _fail("invalid_type", f"{path}.mcp_selection")
    customized = _require_str_list(customized_raw, f"{path}.customized_fields", allow_empty=True)
    customized = tuple(sorted(customized))
    baseline_raw = value["defaults_baseline_digest"]
    if customized:
        digest = _require_digest(baseline_raw, f"{path}.defaults_baseline_digest")
    else:
        if baseline_raw is not None:
            _fail("invalid_value", f"{path}.defaults_baseline_digest")
        digest = None
    return DraftPayload(
        schema_version=DRAFT_PAYLOAD_SCHEMA_VERSION,
        text=text,
        mode=mode,
        model_selection=_opt_draft_fragment(value["model_selection"], f"{path}.model_selection"),
        source_refs=_sorted_source_refs(
            decode_source_version_ref(item, f"{path}.source_refs[{index}]")
            for index, item in enumerate(source_refs_raw)
        ),
        skill_versions=_sorted_skill_refs(
            decode_skill_version_ref(item, f"{path}.skill_versions[{index}]")
            for index, item in enumerate(skill_versions_raw)
        ),
        mcp_selection=_sorted_mcp_selection(
            decode_mcp_service_identity(item, f"{path}.mcp_selection[{index}]")
            for index, item in enumerate(mcp_selection_raw)
        ),
        purpose=purpose,
        research_date=research_date,
        cutoff=cutoff,
        send_target=_opt_draft_fragment(value["send_target"], f"{path}.send_target"),
        object_context=_opt_draft_fragment(
            value["object_context"], f"{path}.object_context", require_source_module=True
        ),
        role_prompt_selection=_opt_draft_fragment(value["role_prompt_selection"], f"{path}.role_prompt_selection"),
        customized_fields=customized,
        defaults_baseline_digest=digest,
    )


def _opt_draft_fragment(value: object, path: str, *, require_source_module: bool = False) -> CanonicalJson | None:
    if value is None:
        return None
    return _draft_fragment(value, path, require_source_module=require_source_module)


@dataclass(frozen=True, slots=True)
class DraftSaveCommand:
    """本地自动草稿保存（§4.2 ``save_draft``）：命令身份 + 目标会话 + 预期草稿版。

    ``command.expected_revision`` 与 ``expected_draft_revision`` 必须相等（同一
    事实的两种承载，不一致即拒绝）；``expected_draft_revision`` 从 1 起（会话行
    存在即已有草稿版本；0 只用于由命令创建目标的情形）。
    """

    command: CommandRef
    session_id: str
    expected_draft_revision: int
    payload: DraftPayload


_DRAFT_SAVE_COMMAND_KEYS: Final = frozenset({"command", "session_id", "expected_draft_revision", "payload"})


def encode_draft_save_command(command: DraftSaveCommand) -> dict[str, Any]:
    """DraftSaveCommand → wire 对象（4 键；含命令引用与草稿载荷）。"""

    expected = command.expected_draft_revision
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        _fail("invalid_revision", "$.expected_draft_revision")
    if command.command.expected_revision != expected:
        _fail("invalid_revision", "$.command.expected_revision")
    return {
        "command": encode_command_ref(command.command),
        "session_id": _require_str(command.session_id, "$.session_id"),
        "expected_draft_revision": expected,
        "payload": encode_draft_payload(command.payload),
    }


def decode_draft_save_command(value: object, path: str = "$.draft_save_command") -> DraftSaveCommand:
    """严格解码 DraftSaveCommand（4 键全必填、禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DRAFT_SAVE_COMMAND_KEYS, path)
    expected = value["expected_draft_revision"]
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        _fail("invalid_revision", f"{path}.expected_draft_revision")
    command = decode_command_ref(value["command"], f"{path}.command")
    if command.expected_revision != expected:
        _fail("invalid_revision", f"{path}.command.expected_revision")
    return DraftSaveCommand(
        command=command,
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        expected_draft_revision=expected,
        payload=decode_draft_payload(value["payload"], f"{path}.payload"),
    )


def validate_draft_save_command(command: DraftSaveCommand) -> None:
    """保存前整份校验（纯函数）；失败抛 :class:`AgentValidationError`。"""

    encode_draft_save_command(command)


@dataclass(frozen=True, slots=True)
class DraftReceipt:
    """草稿保存回执（§4.2 ``DraftReceipt``）：可靠 draft revision，不等于已发送。

    ``error`` 只在冲突/未知时携带稳定错误码；成功保存为 None。
    """

    session_id: str
    command_record_id: str
    commit_state: CommitState
    draft_revision: int
    verified_at: str
    error: AgentErrorCode | None


_DRAFT_RECEIPT_KEYS: Final = frozenset(
    {"session_id", "command_record_id", "commit_state", "draft_revision", "verified_at", "error"}
)


def encode_draft_receipt(receipt: DraftReceipt) -> dict[str, Any]:
    """DraftReceipt → wire 6 键（规范 JSONB 回执载荷）。"""

    if not isinstance(receipt.commit_state, CommitState):
        _fail("invalid_commit_state", "$.commit_state")
    return {
        "session_id": _require_str(receipt.session_id, "$.session_id"),
        "command_record_id": _require_str(receipt.command_record_id, "$.command_record_id"),
        "commit_state": receipt.commit_state.value,
        "draft_revision": require_revision(receipt.draft_revision, "$.draft_revision"),
        "verified_at": _require_utc_timestamp(receipt.verified_at, "$.verified_at"),
        "error": None if receipt.error is None else receipt.error.value,
    }


def decode_draft_receipt(value: object, path: str = "$.draft_receipt") -> DraftReceipt:
    """严格解码 DraftReceipt（6 键；坏枚举/坏时刻 fail-closed）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DRAFT_RECEIPT_KEYS, path)
    commit_raw = value["commit_state"]
    if not isinstance(commit_raw, str):
        _fail("invalid_commit_state", f"{path}.commit_state")
    try:
        commit_state = CommitState(commit_raw)
    except ValueError:
        _fail("invalid_commit_state", f"{path}.commit_state")
    error_raw = value["error"]
    error: AgentErrorCode | None
    if error_raw is None:
        error = None
    elif isinstance(error_raw, str):
        try:
            error = AgentErrorCode(error_raw)
        except ValueError:
            _fail("invalid_error_code", f"{path}.error")
    else:
        _fail("invalid_error_code", f"{path}.error")
    return DraftReceipt(
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        command_record_id=_require_str(value["command_record_id"], f"{path}.command_record_id"),
        commit_state=commit_state,
        draft_revision=cast(int, require_revision(value["draft_revision"], f"{path}.draft_revision")),
        verified_at=_require_utc_timestamp(value["verified_at"], f"{path}.verified_at"),
        error=error,
    )


def draft_save_input_digest(command: DraftSaveCommand) -> str:
    """命令输入摘要：预期草稿版 + 规范载荷（同键同输入幂等、异输入冲突）。"""

    return sha256_hex(
        json_doc(
            {
                "expected_draft_revision": command.expected_draft_revision,
                "payload": encode_draft_payload(command.payload),
            }
        )
    )


# ---------------------------------------------------------------------------
# F-03-2 会话元数据命令（§4.2 change_session / §9.3）
# ---------------------------------------------------------------------------


class SessionAction(Enum):
    """会话命令动作（§4.2 L436 唯一 wire 拼写；各 action 只接受自身字段）。"""

    CREATE = "create"
    RENAME = "rename"
    PIN = "pin"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    REVISE = "revise"
    BRANCH = "branch"


#: 已接地的动作全集（revise/branch 随 F-03-2b 交付：只保存新因果前缀/分支
#: 会话与源消息引用，不改原消息/成果正文，也不派发研究）。
SUPPORTED_SESSION_ACTIONS: Final = frozenset(
    {
        SessionAction.CREATE,
        SessionAction.RENAME,
        SessionAction.PIN,
        SessionAction.ARCHIVE,
        SessionAction.UNARCHIVE,
        SessionAction.REVISE,
        SessionAction.BRANCH,
    }
)


@dataclass(frozen=True, slots=True)
class SessionChangeCommand:
    """会话元数据命令（§4.2 ``SessionChangeCommand``）：命令身份 + 预期会话版。

    ``command.expected_revision`` 必须等于 ``expected_session_revision``；``create``
    与 ``branch`` 要求预期版 0（目标尚不存在），其余动作要求 ≥1。``title`` 仅
    create/rename/branch 必填且非空（DAO 不猜显示名），其余动作携带标题即拒绝。

    修订/分支字段（§4.2 L436「源message/修订/因果截点」）：

    - ``source_message_id`` + ``source_message_revision`` 是原消息身份与版本，
      版本映射 ``agent_message.subject_revision``（消息事实版本，不用可变行
      revision）；
    - ``causal_cutoff_message_id`` 为可选因果截点（最后保留的消息，必须严格早于
      源消息）；缺省时取源消息之前最近一条消息；源为会话首条时前缀为空；
    - ``revise`` 在原会话保存新因果前缀后由用户另行发送（本命令不建消息/不派发）；
      ``branch`` 新建会话并以 ``parent_session_id`` + ``branch_metadata`` 引用原
      前缀，不复制正文、不迁 workflow/批准。
    """

    command: CommandRef
    action: SessionAction
    session_id: str
    expected_session_revision: int
    title: str | None = None
    source_message_id: str | None = None
    source_message_revision: int | None = None
    causal_cutoff_message_id: str | None = None


_SESSION_CHANGE_COMMAND_KEYS: Final = frozenset(
    {
        "command",
        "action",
        "session_id",
        "expected_session_revision",
        "title",
        "source_message_id",
        "source_message_revision",
        "causal_cutoff_message_id",
    }
)

#: 需要「源消息身份/版本」的动作（revise/branch 共用同一组自身字段）。
_SOURCE_MESSAGE_ACTIONS: Final = frozenset({SessionAction.REVISE, SessionAction.BRANCH})

#: 需要新建会话（预期版 0）的动作。
_CREATE_SESSION_ACTIONS: Final = frozenset({SessionAction.CREATE, SessionAction.BRANCH})

#: 需要标题的动作（新建会话与改名必须有可读显示名）。
_TITLE_ACTIONS: Final = frozenset({SessionAction.CREATE, SessionAction.RENAME, SessionAction.BRANCH})


def encode_session_change_command(command: SessionChangeCommand) -> dict[str, Any]:
    """SessionChangeCommand → wire 8 键（含动作自身字段的空值约定）。"""

    if not isinstance(command.action, SessionAction):
        _fail("invalid_action", "$.action")
    expected = command.expected_session_revision
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        _fail("invalid_revision", "$.expected_session_revision")
    if command.command.expected_revision != expected:
        _fail("invalid_revision", "$.command.expected_revision")
    if command.action in _CREATE_SESSION_ACTIONS and expected != 0:
        _fail("invalid_revision", "$.expected_session_revision")
    if command.action not in _CREATE_SESSION_ACTIONS and expected < 1:
        _fail("invalid_revision", "$.expected_session_revision")
    title = command.title
    if title is not None and (not isinstance(title, str) or title == ""):
        _fail("invalid_value", "$.title")
    if command.action in _TITLE_ACTIONS and title is None:
        _fail("missing_field", "$.title")
    if command.action not in _TITLE_ACTIONS and title is not None:
        # 各 action 只接受自身字段：非标题动作携带标题即拒绝（不静默丢弃）。
        _fail("invalid_value", "$.title")
    source_message_id: str | None = None
    source_message_revision: int | None = None
    causal_cutoff_message_id: str | None = None
    if command.action in _SOURCE_MESSAGE_ACTIONS:
        source_message_id = _require_str(command.source_message_id, "$.source_message_id")
        source_message_revision = _require_positive_revision(
            command.source_message_revision, "$.source_message_revision"
        )
        if command.causal_cutoff_message_id is not None:
            causal_cutoff_message_id = _require_str(command.causal_cutoff_message_id, "$.causal_cutoff_message_id")
            if causal_cutoff_message_id == source_message_id:
                # 截点包含源消息等于「未修订」；显式拒绝而非静默改语义。
                _fail("invalid_value", "$.causal_cutoff_message_id")
    else:
        for raw, path in (
            (command.source_message_id, "$.source_message_id"),
            (command.source_message_revision, "$.source_message_revision"),
            (command.causal_cutoff_message_id, "$.causal_cutoff_message_id"),
        ):
            if raw is not None:
                _fail("invalid_value", path)
    if command.action not in SUPPORTED_SESSION_ACTIONS:
        _fail("unsupported_action", "$.action")
    return {
        "command": encode_command_ref(command.command),
        "action": command.action.value,
        "session_id": _require_str(command.session_id, "$.session_id"),
        "expected_session_revision": expected,
        "title": title,
        "source_message_id": source_message_id,
        "source_message_revision": source_message_revision,
        "causal_cutoff_message_id": causal_cutoff_message_id,
    }


def decode_session_change_command(value: object, path: str = "$.session_change_command") -> SessionChangeCommand:
    """严格解码会话命令（8 键全必填、禁额外键、动作自身字段约束）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _SESSION_CHANGE_COMMAND_KEYS, path)
    action_raw = value["action"]
    if not isinstance(action_raw, str):
        _fail("invalid_action", f"{path}.action")
    try:
        action = SessionAction(action_raw)
    except ValueError:
        _fail("invalid_action", f"{path}.action")
    expected = value["expected_session_revision"]
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        _fail("invalid_revision", f"{path}.expected_session_revision")
    command = decode_command_ref(value["command"], f"{path}.command")
    if command.expected_revision != expected:
        _fail("invalid_revision", f"{path}.command.expected_revision")
    title = value["title"]
    if title is not None and not isinstance(title, str):
        _fail("invalid_type", f"{path}.title")
    source_message_id = value["source_message_id"]
    if source_message_id is not None and not isinstance(source_message_id, str):
        _fail("invalid_type", f"{path}.source_message_id")
    source_revision_raw = value["source_message_revision"]
    if source_revision_raw is not None and not isinstance(source_revision_raw, int):
        _fail("invalid_type", f"{path}.source_message_revision")
    cutoff = value["causal_cutoff_message_id"]
    if cutoff is not None and not isinstance(cutoff, str):
        _fail("invalid_type", f"{path}.causal_cutoff_message_id")
    decoded = SessionChangeCommand(
        command=command,
        action=action,
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        expected_session_revision=expected,
        title=title,
        source_message_id=source_message_id,
        source_message_revision=cast(int | None, source_revision_raw),
        causal_cutoff_message_id=cutoff,
    )
    try:
        encode_session_change_command(decoded)
    except AgentValidationError as exc:
        suffix = exc.field_path[1:] if exc.field_path.startswith("$") else f".{exc.field_path}"
        _fail(exc.code, f"{path}{suffix}")
    return decoded


def validate_session_change_command(command: SessionChangeCommand) -> None:
    """会话命令整份校验（纯函数）；失败抛 :class:`AgentValidationError`。"""

    encode_session_change_command(command)


def session_change_input_digest(command: SessionChangeCommand) -> str:
    """命令输入摘要：动作 + 目标 + 预期版 + 标题 + 源消息身份/版本 + 因果截点。"""

    return sha256_hex(
        json_doc(
            {
                "action": command.action.value,
                "session_id": command.session_id,
                "expected_session_revision": command.expected_session_revision,
                "title": command.title,
                "source_message_id": command.source_message_id,
                "source_message_revision": command.source_message_revision,
                "causal_cutoff_message_id": command.causal_cutoff_message_id,
            }
        )
    )


# ---------------------------------------------------------------------------
# F-03-3 草稿转移契约（§4.2 L434-435 DraftTransferCommand / DraftTransferReceipt）
# ---------------------------------------------------------------------------


class DraftTransferSourceKind(Enum):
    """转移源类别（§4.2 L434 唯一 wire 拼写；与 :class:`SourceKind` 不混用）。"""

    SESSION_DRAFT = "session_draft"
    BUSINESS_PREPARATION = "business_preparation"


class DraftTransferPhase(Enum):
    """原转移阶段（§4.2 L435；§9.8 ``agent_command.transfer_phase`` 值域）。

    本片单事务实现直接落 ``completed``，其余值供分阶段实现与重启核查：
    unknown 不等于失败或成功（INV-07）。
    """

    PREPARED = "prepared"
    TARGET_INSTALLED = "target_installed"
    COMPLETED = "completed"
    CONFLICT = "conflict"
    NOT_COMMITTED = "not_committed"
    UNKNOWN = "unknown"


class SendableOwner(Enum):
    """可发送归属（§4.2 L435）：最多一个 owner；未决时双方可为 ``none``。"""

    SOURCE = "source"
    TARGET = "target"
    NONE = "none"


_DRAFT_TRANSFER_COMMAND_KEYS: Final = frozenset(
    {
        "command",
        "transfer_id",
        "source_kind",
        "source_id",
        "source_session_id",
        "expected_source_draft_revision",
        "target_session_id",
        "expected_target_draft_revision",
        "payload",
        "payload_digest",
        "confirmed",
    }
)

_DRAFT_TRANSFER_RECEIPT_KEYS: Final = frozenset(
    {
        "command_record_id",
        "transfer_id",
        "source_kind",
        "source_id",
        "source_session_id",
        "target_session_id",
        "expected_source_draft_revision",
        "expected_target_draft_revision",
        "payload_digest",
        "commit_state",
        "phase",
        "sendable_owner",
        "source_revision",
        "target_revision",
        "verified_at",
        "next_action",
    }
)


@dataclass(frozen=True, slots=True)
class DraftTransferCommand:
    """草稿移入命令（§4.2 L434）：双方身份/预期版 + 完整 payload，不发送。

    工程冻结（§4.2/§9.8 未逐字段说明处，按可辩护最小语义）：

    - ``command.expected_revision`` 必须等于 ``expected_target_draft_revision``
      （命令的准确目标是目标会话；源版另有显式字段，不重复承载）；
    - ``transfer_id`` 沿原命令稳定派生（:func:`derive_draft_transfer_id`），
      不含 payload 摘要——同 ID 异输入由命令键摘要判冲突；
    - ``source_kind=session_draft``：``source_id`` 即源会话身份，
      ``source_session_id`` 必填且必须相等（源草稿存于该会话行）；目标不得为源自身；
    - ``source_kind=business_preparation``：源由业务准备稿生产者持有，本库无源行，
      ``source_session_id`` 必须为 None；源版/清源由生产者按同一 claim 键自行核验；
    - ``payload`` 为 :class:`DraftPayload`（不含原命令/保存版/凭据/有效批准）；
      ``payload_digest`` 必须等于该载荷的规范摘要（防调用方自述与载荷不一致）；
    - ``confirmed`` 必须为 ``True``（用户已预览完整载荷并确认）。
    """

    command: CommandRef
    transfer_id: str
    source_kind: DraftTransferSourceKind
    source_id: str
    source_session_id: str | None
    expected_source_draft_revision: int
    target_session_id: str
    expected_target_draft_revision: int
    payload: DraftPayload
    payload_digest: str
    confirmed: bool


def draft_transfer_payload_digest(payload: DraftPayload) -> str:
    """完整草稿载荷的规范摘要（SHA-256 over canonical payload）。"""

    return sha256_hex(json_doc(encode_draft_payload(payload)))


def derive_draft_transfer_id(command: DraftTransferCommand) -> str:
    """转移稳定身份：``user_scope + 原命令键 + 源类别/身份 + 目标会话``。

    不含 payload 摘要（§4.2 L415）：同 ID 异 payload 必须报冲突，而不是新转移。
    """

    if not isinstance(command.source_kind, DraftTransferSourceKind):
        _fail("invalid_tag", "$.source_kind")
    return stable_id(
        "draft_transfer",
        [
            command.command.user_scope,
            command.command.command_id,
            command.source_kind.value,
            command.source_id,
            command.target_session_id,
        ],
    )


def is_empty_draft_payload(payload: DraftPayload | None) -> bool:
    """目标是否为空稿（§4.2 L413 R03）。

    ``None``（无稿）与「空稿且无任何未发送选择」均可接收移入；**正文空但有
    对象/来源/手选模型/模式/用途/研究日期/精确截止/发送目标/Skill/MCP/角色/Prompt 等
    未发送选择不是空位**。``customized_fields`` 非空即表示用户触碰过默认值
    （含把默认项显式选成默认值），按有稿处理；``mode`` 必须是默认 ``EXECUTE``
    （需求 F21「新会话默认执行」），其他模式即使调用方漏填 ``customized_fields``
    也按已选模式处理（fail-closed，不覆盖用户选择）。
    """

    if payload is None:
        return True
    return (
        payload.text == ""
        and payload.mode is Mode.EXECUTE
        and payload.model_selection is None
        and not payload.source_refs
        and not payload.skill_versions
        and not payload.mcp_selection
        and payload.purpose is None
        and payload.research_date is None
        and payload.cutoff is None
        and payload.send_target is None
        and payload.object_context is None
        and payload.role_prompt_selection is None
        and not payload.customized_fields
    )


@dataclass(frozen=True, slots=True)
class DraftReadQuery:
    """草稿读回查询（§4.2 读取面）：目标会话 + 可选准确稿版。

    ``draft_revision`` 给定时必须等于会话当前草稿版，否则读取方拒绝
    （``REVISION_CONFLICT``），不把旧稿冒充所请求的版本。
    """

    session_id: str
    draft_revision: int | None = None


def validate_draft_read_query(query: DraftReadQuery) -> None:
    """草稿读回查询结构校验（会话非空文本；稿版从 1 起），失败抛 AgentValidationError。"""

    _require_str(query.session_id, "$.session_id")
    if query.draft_revision is not None and (
        isinstance(query.draft_revision, bool) or not isinstance(query.draft_revision, int) or query.draft_revision < 1
    ):
        _fail("invalid_range", "$.draft_revision")


@dataclass(frozen=True, slots=True)
class DraftRead:
    """按会话读回的草稿完整事实（只读；读取不发送、不推进任何命令）。

    ``payload=None`` 单独不表示「空稿」：``cleaned``（清理后正文已删除）、
    ``transferred``（正文已移出到其他会话）与两者皆否（从未保存过草稿）是
    不同事实。``is_empty`` 只在确有已保存载荷且载荷内容为空时为 True（R21）。
    """

    session_id: str
    draft_revision: int
    payload: DraftPayload | None
    is_empty: bool
    cleaned: bool
    archived: bool
    transferred: bool
    read_at: str


def _require_positive_revision(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_revision", path)
    return value


def encode_draft_transfer_command(command: DraftTransferCommand) -> dict[str, Any]:
    """DraftTransferCommand → wire 11 键（双方身份/预期版 + 完整 payload + 摘要）。"""

    if not isinstance(command.source_kind, DraftTransferSourceKind):
        _fail("invalid_tag", "$.source_kind")
    source_expected = _require_positive_revision(
        command.expected_source_draft_revision, "$.expected_source_draft_revision"
    )
    target_expected = _require_positive_revision(
        command.expected_target_draft_revision, "$.expected_target_draft_revision"
    )
    if command.command.expected_revision != target_expected:
        _fail("invalid_revision", "$.command.expected_revision")
    source_id = _require_str(command.source_id, "$.source_id")
    target_session_id = _require_str(command.target_session_id, "$.target_session_id")
    if command.source_kind is DraftTransferSourceKind.SESSION_DRAFT:
        source_session_id: str | None = _require_str(command.source_session_id, "$.source_session_id")
        if source_session_id != source_id:
            _fail("invalid_value", "$.source_session_id")
        if source_id == target_session_id:
            _fail("invalid_value", "$.target_session_id")
    else:
        if command.source_session_id is not None:
            _fail("invalid_value", "$.source_session_id")
        source_session_id = None
    if command.confirmed is not True:
        _fail("unconfirmed_transfer", "$.confirmed")
    payload_object = encode_draft_payload(command.payload)
    payload_digest = _require_digest(command.payload_digest, "$.payload_digest")
    if payload_digest != sha256_hex(json_doc(payload_object)):
        _fail("invalid_digest", "$.payload_digest")
    transfer_id = _require_str(command.transfer_id, "$.transfer_id")
    if transfer_id != derive_draft_transfer_id(command):
        _fail("invalid_value", "$.transfer_id")
    return {
        "command": encode_command_ref(command.command),
        "transfer_id": transfer_id,
        "source_kind": command.source_kind.value,
        "source_id": source_id,
        "source_session_id": source_session_id,
        "expected_source_draft_revision": source_expected,
        "target_session_id": target_session_id,
        "expected_target_draft_revision": target_expected,
        "payload": payload_object,
        "payload_digest": payload_digest,
        "confirmed": True,
    }


def decode_draft_transfer_command(value: object, path: str = "$.draft_transfer_command") -> DraftTransferCommand:
    """严格解码草稿转移命令（11 键全必填、禁额外键、跨字段一致性复核）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DRAFT_TRANSFER_COMMAND_KEYS, path)
    source_kind_raw = value["source_kind"]
    if not isinstance(source_kind_raw, str):
        _fail("invalid_tag", f"{path}.source_kind")
    try:
        source_kind = DraftTransferSourceKind(source_kind_raw)
    except ValueError:
        _fail("invalid_tag", f"{path}.source_kind")
    source_session_raw = value["source_session_id"]
    if source_session_raw is not None and not isinstance(source_session_raw, str):
        _fail("invalid_type", f"{path}.source_session_id")
    confirmed_raw = value["confirmed"]
    if not isinstance(confirmed_raw, bool):
        _fail("invalid_type", f"{path}.confirmed")
    decoded = DraftTransferCommand(
        command=decode_command_ref(value["command"], f"{path}.command"),
        transfer_id=_require_str(value["transfer_id"], f"{path}.transfer_id"),
        source_kind=source_kind,
        source_id=_require_str(value["source_id"], f"{path}.source_id"),
        source_session_id=source_session_raw,
        expected_source_draft_revision=_require_positive_revision(
            value["expected_source_draft_revision"], f"{path}.expected_source_draft_revision"
        ),
        target_session_id=_require_str(value["target_session_id"], f"{path}.target_session_id"),
        expected_target_draft_revision=_require_positive_revision(
            value["expected_target_draft_revision"], f"{path}.expected_target_draft_revision"
        ),
        payload=decode_draft_payload(value["payload"], f"{path}.payload"),
        payload_digest=_require_digest(value["payload_digest"], f"{path}.payload_digest"),
        confirmed=confirmed_raw,
    )
    try:
        encode_draft_transfer_command(decoded)
    except AgentValidationError as exc:
        suffix = exc.field_path[1:] if exc.field_path.startswith("$") else f".{exc.field_path}"
        _fail(exc.code, f"{path}{suffix}")
    return decoded


def validate_draft_transfer_command(command: DraftTransferCommand) -> None:
    """转移前整份校验（纯函数）；失败抛 :class:`AgentValidationError`。"""

    encode_draft_transfer_command(command)


def draft_transfer_input_digest(command: DraftTransferCommand) -> str:
    """命令输入摘要：源/目标身份与预期版 + 完整载荷 + 摘要 + 确认位。

    同命令键同输入查回原阶段；异 payload/身份/版本一律冲突（§4.2 L415）。
    """

    return sha256_hex(
        json_doc(
            {
                "transfer_id": command.transfer_id,
                "source_kind": command.source_kind.value,
                "source_id": command.source_id,
                "source_session_id": command.source_session_id,
                "expected_source_draft_revision": command.expected_source_draft_revision,
                "target_session_id": command.target_session_id,
                "expected_target_draft_revision": command.expected_target_draft_revision,
                "payload": encode_draft_payload(command.payload),
                "payload_digest": command.payload_digest,
                "confirmed": command.confirmed,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class DraftTransferReceipt:
    """原转移回执（§4.2 L435）：阶段/唯一可发送归属/可靠双方 revision。

    ``source_revision`` / ``target_revision`` 为本次成功提交后的可靠当前版本；
    事实不足时以 ``None`` 表示 unknown（不当 0 或「未变」）。来源侧完成移入后
    仅保留不可发送恢复记录，``expected_source_draft_revision`` 才是被消费的源版。
    """

    command_record_id: str
    transfer_id: str
    source_kind: DraftTransferSourceKind
    source_id: str
    source_session_id: str | None
    target_session_id: str
    expected_source_draft_revision: int
    expected_target_draft_revision: int
    payload_digest: str
    commit_state: CommitState
    phase: DraftTransferPhase
    sendable_owner: SendableOwner
    source_revision: int | None
    target_revision: int | None
    verified_at: str
    next_action: str | None


def encode_draft_transfer_receipt(receipt: DraftTransferReceipt) -> dict[str, Any]:
    """DraftTransferReceipt → wire 16 键（规范 JSONB 回执载荷）。"""

    if not isinstance(receipt.source_kind, DraftTransferSourceKind):
        _fail("invalid_tag", "$.source_kind")
    if not isinstance(receipt.commit_state, CommitState):
        _fail("invalid_commit_state", "$.commit_state")
    if not isinstance(receipt.phase, DraftTransferPhase):
        _fail("invalid_phase", "$.phase")
    if not isinstance(receipt.sendable_owner, SendableOwner):
        _fail("invalid_owner", "$.sendable_owner")
    source_session_id: str | None = None
    if receipt.source_kind is DraftTransferSourceKind.SESSION_DRAFT:
        source_session_id = _require_str(receipt.source_session_id, "$.source_session_id")
    elif receipt.source_session_id is not None:
        _fail("invalid_value", "$.source_session_id")
    return {
        "command_record_id": _require_str(receipt.command_record_id, "$.command_record_id"),
        "transfer_id": _require_str(receipt.transfer_id, "$.transfer_id"),
        "source_kind": receipt.source_kind.value,
        "source_id": _require_str(receipt.source_id, "$.source_id"),
        "source_session_id": source_session_id,
        "target_session_id": _require_str(receipt.target_session_id, "$.target_session_id"),
        "expected_source_draft_revision": _require_positive_revision(
            receipt.expected_source_draft_revision, "$.expected_source_draft_revision"
        ),
        "expected_target_draft_revision": _require_positive_revision(
            receipt.expected_target_draft_revision, "$.expected_target_draft_revision"
        ),
        "payload_digest": _require_digest(receipt.payload_digest, "$.payload_digest"),
        "commit_state": receipt.commit_state.value,
        "phase": receipt.phase.value,
        "sendable_owner": receipt.sendable_owner.value,
        "source_revision": _opt_positive_revision(receipt.source_revision, "$.source_revision"),
        "target_revision": _opt_positive_revision(receipt.target_revision, "$.target_revision"),
        "verified_at": _require_utc_timestamp(receipt.verified_at, "$.verified_at"),
        "next_action": None if receipt.next_action is None else _require_str(receipt.next_action, "$.next_action"),
    }


def _opt_positive_revision(value: object, path: str) -> int | None:
    if value is None:
        return None
    return _require_positive_revision(value, path)


def decode_draft_transfer_receipt(value: object, path: str = "$.draft_transfer_receipt") -> DraftTransferReceipt:
    """严格解码转移回执（16 键；坏枚举/坏时刻 fail-closed，unknown 不当成功）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DRAFT_TRANSFER_RECEIPT_KEYS, path)

    def _enum(raw: object, enum_type: type[Enum], code: str, field: str) -> Any:
        if not isinstance(raw, str):
            _fail(code, f"{path}.{field}")
        try:
            return enum_type(raw)
        except ValueError:
            _fail(code, f"{path}.{field}")

    source_kind = cast(
        DraftTransferSourceKind, _enum(value["source_kind"], DraftTransferSourceKind, "invalid_tag", "source_kind")
    )
    source_session_raw = value["source_session_id"]
    if source_session_raw is not None and not isinstance(source_session_raw, str):
        _fail("invalid_type", f"{path}.source_session_id")
    if source_kind is DraftTransferSourceKind.BUSINESS_PREPARATION and source_session_raw is not None:
        _fail("invalid_value", f"{path}.source_session_id")
    if source_kind is DraftTransferSourceKind.SESSION_DRAFT and source_session_raw is None:
        _fail("invalid_identity", f"{path}.source_session_id")
    next_action_raw = value["next_action"]
    if next_action_raw is not None and not isinstance(next_action_raw, str):
        _fail("invalid_type", f"{path}.next_action")
    return DraftTransferReceipt(
        command_record_id=_require_str(value["command_record_id"], f"{path}.command_record_id"),
        transfer_id=_require_str(value["transfer_id"], f"{path}.transfer_id"),
        source_kind=source_kind,
        source_id=_require_str(value["source_id"], f"{path}.source_id"),
        source_session_id=source_session_raw,
        target_session_id=_require_str(value["target_session_id"], f"{path}.target_session_id"),
        expected_source_draft_revision=_require_positive_revision(
            value["expected_source_draft_revision"], f"{path}.expected_source_draft_revision"
        ),
        expected_target_draft_revision=_require_positive_revision(
            value["expected_target_draft_revision"], f"{path}.expected_target_draft_revision"
        ),
        payload_digest=_require_digest(value["payload_digest"], f"{path}.payload_digest"),
        commit_state=cast(
            CommitState, _enum(value["commit_state"], CommitState, "invalid_commit_state", "commit_state")
        ),
        phase=cast(DraftTransferPhase, _enum(value["phase"], DraftTransferPhase, "invalid_phase", "phase")),
        sendable_owner=cast(
            SendableOwner, _enum(value["sendable_owner"], SendableOwner, "invalid_owner", "sendable_owner")
        ),
        source_revision=_opt_positive_revision(value["source_revision"], f"{path}.source_revision"),
        target_revision=_opt_positive_revision(value["target_revision"], f"{path}.target_revision"),
        verified_at=_require_utc_timestamp(value["verified_at"], f"{path}.verified_at"),
        next_action=next_action_raw,
    )


# ---------------------------------------------------------------------------
# E-05 成果交付命令与 Windows 目标保护契约（§4.2 L438/L445、§10.5、HANDOFF §10.2）
# ---------------------------------------------------------------------------

#: 已保存成果导出格式（§10.2：Markdown / BOM UTF-8 CSV / XLSX，默认 Markdown）。
DELIVERY_FORMATS: Final[frozenset[str]] = frozenset({"md", "csv", "xlsx"})

#: 交付格式规则版本（与实现绑定；规则变更时递增，旧 manifest 保留原值）。
DELIVERY_FORMAT_RULE_VERSION: Final = 1

#: 单个可逆分段上限：30,000 UTF-16 单元（恰等通过；超一必须完整分条而非截断）。
DELIVERY_CHUNK_MAX_UTF16: Final = 30_000

#: 交付阶段（§4.2 L555 / HANDOFF §10.2；unknown 不等于失败或成功）。
DELIVERY_PHASES: Final[frozenset[str]] = frozenset(
    {
        "manifest_saved",
        "staged",
        "validated",
        "publish_claimed",
        "published_confirmed",
        "cancelled_before_publish",
        "unknown",
    }
)

#: 交付时原来源当前状态（与读取时点分开；Release 前重新核验）。
DELIVERY_SOURCE_STATES: Final[frozenset[str]] = frozenset({"available", "unavailable", "disabled", "unknown"})


def canonical_delivery_source_state(value: object) -> str:
    """来源状态统一词表口径：库内自由文本不在四值内时按 ``unknown``，不改写原事实。

    ``agent_artifact.source_available_state`` 是自由文本列（成果保存时核定），
    交付文件与 manifest 只接受 ``DELIVERY_SOURCE_STATES``；非标准取值既不得
    伪装成 ``available``，也不得把历史取值冒充当次核定结果。
    """

    return value if isinstance(value, str) and value in DELIVERY_SOURCE_STATES else "unknown"


#: 发布效果（未尝试 / 已发布 / 取消先胜 / 无法确证）。
DELIVERY_PUBLISH_EFFECTS: Final[frozenset[str]] = frozenset(
    {"not_attempted", "published", "cancelled_before_publish", "unknown"}
)

#: 取消效果（未请求 / 取消先胜 / 无法确证）。
DELIVERY_CANCEL_EFFECTS: Final[frozenset[str]] = frozenset({"not_requested", "cancelled_before_publish", "unknown"})

_WINDOWS_DELIVERY_INVALID_CHARS: Final = frozenset('<>:"|?*')

#: Win32 设备名（基名不区分大小写；带扩展名同样拒绝）。
_WINDOWS_DELIVERY_RESERVED_NAMES: Final = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)


def validate_delivery_target_path(value: object, path: str = "$.delivery.target.target_path") -> str:
    """校验本机交付目标：绝对盘符路径，拒绝 URL/UNC/设备命名空间/ADS/重解析前缀。

    这里只做**语法与名称**策略（§10.5）；最终句柄/卷/父目录身份与重解析点
    由文件适配器在真实句柄上核验，不能由本函数替代。
    """

    if not isinstance(value, str) or value == "" or "\x00" in value:
        _fail("invalid_target_path", path)
    if "/" in value:
        # 只接受 Windows 反斜杠规范路径：避免同一目标出现多种等价写法。
        _fail("invalid_target_path", path)
    if len(value) < 4 or value[1] != ":" or not value[2:].startswith("\\"):
        # 拒绝相对路径、URL、UNC（``\\\\server\\share``）、设备命名空间（``\\\\?\\``）。
        _fail("invalid_target_path", path)
    if not ("A" <= value[0].upper() <= "Z"):
        _fail("invalid_target_path", path)
    if ":" in value[2:]:
        # 盘符后的冒号只能是 ADS 分隔符（``file.txt:stream``），拒绝。
        _fail("invalid_target_path", path)
    pure = PureWindowsPath(value)
    if len(pure.parts) < 2:
        _fail("invalid_target_path", path)
    for part in pure.parts[1:]:
        if part in (".", ".."):
            _fail("invalid_target_path", path)
        if part != part.rstrip(" ."):
            _fail("invalid_target_path", path)
        if any(character in _WINDOWS_DELIVERY_INVALID_CHARS or ord(character) < 32 for character in part):
            _fail("invalid_target_path", path)
        if part.split(".")[0].upper() in _WINDOWS_DELIVERY_RESERVED_NAMES:
            _fail("invalid_target_path", path)
    return str(pure)


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    """用户确认的本机目标（HANDOFF §10.2「规范目标/覆盖依据」）。

    ``expected_existing`` 为 False 表示确认时目标不存在；True 时必须带当时
    文件内容摘要，且必须已确认覆盖。目标随后变化即原确认失效（由交付编排核验）。
    """

    target_path: str
    expected_existing: bool
    expected_existing_digest: str | None
    overwrite_confirmed: bool


_DELIVERY_TARGET_KEYS: Final = frozenset(
    {"target_path", "expected_existing", "expected_existing_digest", "overwrite_confirmed"}
)


def encode_delivery_target(target: DeliveryTarget) -> dict[str, Any]:
    """DeliveryTarget → wire 4 键（覆盖依据与确认位必须一致）。"""

    target_path = validate_delivery_target_path(target.target_path)
    if not isinstance(target.expected_existing, bool) or not isinstance(target.overwrite_confirmed, bool):
        _fail("invalid_type", "$.delivery.target")
    if target.expected_existing:
        digest = _require_digest(target.expected_existing_digest, "$.delivery.target.expected_existing_digest")
        if not target.overwrite_confirmed:
            _fail("unconfirmed_overwrite", "$.delivery.target.overwrite_confirmed")
    else:
        if target.expected_existing_digest is not None:
            _fail("invalid_value", "$.delivery.target.expected_existing_digest")
        if target.overwrite_confirmed:
            _fail("invalid_value", "$.delivery.target.overwrite_confirmed")
        digest = None
    return {
        "target_path": target_path,
        "expected_existing": target.expected_existing,
        "expected_existing_digest": digest,
        "overwrite_confirmed": target.overwrite_confirmed,
    }


def decode_delivery_target(value: object, path: str = "$.delivery.target") -> DeliveryTarget:
    """严格解码交付目标（4 键全必填、禁额外键）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DELIVERY_TARGET_KEYS, path)
    existing_raw = value["expected_existing"]
    confirmed_raw = value["overwrite_confirmed"]
    if not isinstance(existing_raw, bool) or not isinstance(confirmed_raw, bool):
        _fail("invalid_type", path)
    digest_raw = value["expected_existing_digest"]
    if existing_raw:
        digest: str | None = _require_digest(digest_raw, f"{path}.expected_existing_digest")
    else:
        if digest_raw is not None:
            _fail("invalid_value", f"{path}.expected_existing_digest")
        digest = None
    return DeliveryTarget(
        target_path=validate_delivery_target_path(value["target_path"], f"{path}.target_path"),
        expected_existing=existing_raw,
        expected_existing_digest=digest,
        overwrite_confirmed=confirmed_raw,
    )


@dataclass(frozen=True, slots=True)
class ArtifactDeliveryCommand:
    """显式成果导出（§4.2 L438）：用户确认目标与覆盖，不是模型任意写工具。

    ``command.expected_revision`` 与 ``expected_delivery_revision`` 必须一致；
    ``delivery_id`` 沿原命令与成果身份稳定派生（不含目标，换目标仍属同一交付，
    必须报冲突而不是新交付）。
    """

    command: CommandRef
    artifact_id: str
    artifact_version: int
    format: str
    format_rule_version: int
    delivery_id: str
    target: DeliveryTarget
    expected_delivery_revision: int


_DELIVERY_COMMAND_KEYS: Final = frozenset(
    {
        "command",
        "artifact_id",
        "artifact_version",
        "format",
        "format_rule_version",
        "delivery_id",
        "target",
        "expected_delivery_revision",
    }
)


def derive_delivery_id(command: ArtifactDeliveryCommand) -> str:
    """交付稳定身份：``user_scope + 命令键 + 成果版本 + 格式``（不含目标）。"""

    return stable_id(
        "delivery",
        [
            command.command.user_scope,
            command.command.command_id,
            command.artifact_id,
            str(command.artifact_version),
            command.format,
        ],
    )


def encode_artifact_delivery_command(command: ArtifactDeliveryCommand) -> dict[str, Any]:
    """ArtifactDeliveryCommand → wire 8 键（格式/规则版/目标/预期版逐项校验）。"""

    expected = command.expected_delivery_revision
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        _fail("invalid_revision", "$.expected_delivery_revision")
    if command.command.expected_revision != expected:
        _fail("invalid_revision", "$.command.expected_revision")
    artifact_id = _require_str(command.artifact_id, "$.artifact_id")
    artifact_version = _require_positive_revision(command.artifact_version, "$.artifact_version")
    format_name = _require_choice(command.format, DELIVERY_FORMATS, "$.format", "invalid_delivery_format")
    rule_version = _require_positive_revision(command.format_rule_version, "$.format_rule_version")
    if rule_version != DELIVERY_FORMAT_RULE_VERSION:
        _fail("unsupported_format_rule_version", "$.format_rule_version")
    delivery_id = _require_str(command.delivery_id, "$.delivery_id")
    if delivery_id != derive_delivery_id(command):
        _fail("invalid_value", "$.delivery_id")
    return {
        "command": encode_command_ref(command.command),
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "format": format_name,
        "format_rule_version": rule_version,
        "delivery_id": delivery_id,
        "target": encode_delivery_target(command.target),
        "expected_delivery_revision": expected,
    }


def decode_artifact_delivery_command(
    value: object, path: str = "$.artifact_delivery_command"
) -> ArtifactDeliveryCommand:
    """严格解码交付命令（8 键全必填、禁额外键、摘要/版本交叉复核）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DELIVERY_COMMAND_KEYS, path)
    expected = value["expected_delivery_revision"]
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        _fail("invalid_revision", f"{path}.expected_delivery_revision")
    command = decode_command_ref(value["command"], f"{path}.command")
    if command.expected_revision != expected:
        _fail("invalid_revision", f"{path}.command.expected_revision")
    artifact_version = _require_positive_revision(value["artifact_version"], f"{path}.artifact_version")
    format_name = _require_choice(value["format"], DELIVERY_FORMATS, f"{path}.format", "invalid_delivery_format")
    rule_version = _require_positive_revision(value["format_rule_version"], f"{path}.format_rule_version")
    if rule_version != DELIVERY_FORMAT_RULE_VERSION:
        _fail("unsupported_format_rule_version", f"{path}.format_rule_version")
    decoded = ArtifactDeliveryCommand(
        command=command,
        artifact_id=_require_str(value["artifact_id"], f"{path}.artifact_id"),
        artifact_version=artifact_version,
        format=format_name,
        format_rule_version=rule_version,
        delivery_id=_require_str(value["delivery_id"], f"{path}.delivery_id"),
        target=decode_delivery_target(value["target"], f"{path}.target"),
        expected_delivery_revision=expected,
    )
    if decoded.delivery_id != derive_delivery_id(decoded):
        _fail("invalid_value", f"{path}.delivery_id")
    return decoded


def validate_artifact_delivery_command(command: ArtifactDeliveryCommand) -> None:
    """交付前整份校验（纯函数）；失败抛 :class:`AgentValidationError`。"""

    encode_artifact_delivery_command(command)


def delivery_input_digest(command: ArtifactDeliveryCommand) -> str:
    """命令输入摘要：成果版本 + 格式规则 + 目标/覆盖确认 + 预期版。"""

    return sha256_hex(
        json_doc(
            {
                "artifact_id": command.artifact_id,
                "artifact_version": command.artifact_version,
                "format": command.format,
                "format_rule_version": command.format_rule_version,
                "target": encode_delivery_target(command.target),
                "expected_delivery_revision": command.expected_delivery_revision,
            }
        )
    )


def split_utf16_chunks(text: object, max_units: int = DELIVERY_CHUNK_MAX_UTF16) -> tuple[str, ...]:
    """把长文本切成每段 ≤ ``max_units`` UTF-16 单元的可逆分条（不截断、不拆代理对）。

    §10.2 / QAC13：``record/field/chunk/value`` 分条上限 30,000 UTF-16 单元；
    非 BMP 字符计 2 单元但提取仍是 1 个 Unicode 码点。空文本返回单个空段
    （保留“空正文”事实，不解释为无内容）。
    """

    if not isinstance(text, str):
        _fail("invalid_type", "$.delivery.chunk.text")
    if isinstance(max_units, bool) or not isinstance(max_units, int) or max_units < 2:
        _fail("invalid_chunk_limit", "$.delivery.chunk.max_units")
    chunks: list[str] = []
    current: list[str] = []
    units = 0
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        if units + width > max_units:
            chunks.append("".join(current))
            current = []
            units = 0
        current.append(character)
        units += width
    if current or not chunks:
        chunks.append("".join(current))
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """交付回执（§4.2 L445）：阶段、发布/取消效果、完整输出摘要与 unknown 原因。

    ``phase`` 与效果位必须自洽：发布成功才可 ``published``；取消先胜不发布；
    ``unknown`` 必须带错误码，禁止伪装成失败或取消成功（INV-07）。
    """

    command_record_id: str
    delivery_id: str
    artifact_id: str
    artifact_version: int
    artifact_digest: str
    format: str
    phase: str
    publish_effect: str
    cancel_effect: str
    output_digest: str | None
    verified_at: str
    error_code: str | None
    next_action: str | None


_DELIVERY_RECEIPT_KEYS: Final = frozenset(
    {
        "command_record_id",
        "delivery_id",
        "artifact_id",
        "artifact_version",
        "artifact_digest",
        "format",
        "phase",
        "publish_effect",
        "cancel_effect",
        "output_digest",
        "verified_at",
        "error_code",
        "next_action",
    }
)

#: 已进入暂存/校验/发布认领的阶段必须有输出摘要。
_DELIVERY_PHASES_REQUIRING_OUTPUT: Final = frozenset({"staged", "validated", "publish_claimed", "published_confirmed"})


def _validate_delivery_effects(phase: str, publish_effect: str, cancel_effect: str, output_digest: str | None) -> None:
    if phase == "published_confirmed":
        if output_digest is None:
            _fail("missing_field", "$.output_digest")
        if publish_effect != "published" or cancel_effect != "not_requested":
            _fail("invalid_delivery_phase", "$.phase")
        return
    if phase == "cancelled_before_publish":
        if publish_effect != "not_attempted" or cancel_effect != "cancelled_before_publish":
            _fail("invalid_delivery_phase", "$.phase")
        return
    if phase == "unknown":
        if publish_effect != "unknown" or cancel_effect not in {"unknown", "not_requested"}:
            _fail("invalid_delivery_phase", "$.phase")
        return
    if publish_effect != "not_attempted" or cancel_effect != "not_requested":
        _fail("invalid_delivery_phase", "$.phase")
    if phase in _DELIVERY_PHASES_REQUIRING_OUTPUT and output_digest is None:
        _fail("missing_field", "$.output_digest")


def encode_delivery_receipt(receipt: DeliveryReceipt) -> dict[str, Any]:
    """DeliveryReceipt → wire 13 键（阶段与效果位自洽校验）。"""

    phase = _require_choice(receipt.phase, DELIVERY_PHASES, "$.phase", "invalid_delivery_phase")
    publish_effect = _require_choice(
        receipt.publish_effect, DELIVERY_PUBLISH_EFFECTS, "$.publish_effect", "invalid_publish_effect"
    )
    cancel_effect = _require_choice(
        receipt.cancel_effect, DELIVERY_CANCEL_EFFECTS, "$.cancel_effect", "invalid_cancel_effect"
    )
    output_digest = None if receipt.output_digest is None else _require_digest(receipt.output_digest, "$.output_digest")
    _validate_delivery_effects(phase, publish_effect, cancel_effect, output_digest)
    if phase == "unknown" and receipt.error_code is None:
        _fail("missing_field", "$.error_code")
    return {
        "command_record_id": _require_str(receipt.command_record_id, "$.command_record_id"),
        "delivery_id": _require_str(receipt.delivery_id, "$.delivery_id"),
        "artifact_id": _require_str(receipt.artifact_id, "$.artifact_id"),
        "artifact_version": _require_positive_revision(receipt.artifact_version, "$.artifact_version"),
        "artifact_digest": _require_digest(receipt.artifact_digest, "$.artifact_digest"),
        "format": _require_choice(receipt.format, DELIVERY_FORMATS, "$.format", "invalid_delivery_format"),
        "phase": phase,
        "publish_effect": publish_effect,
        "cancel_effect": cancel_effect,
        "output_digest": output_digest,
        "verified_at": _require_utc_timestamp(receipt.verified_at, "$.verified_at"),
        "error_code": None if receipt.error_code is None else _require_str(receipt.error_code, "$.error_code"),
        "next_action": None if receipt.next_action is None else _require_str(receipt.next_action, "$.next_action"),
    }


def decode_delivery_receipt(value: object, path: str = "$.delivery_receipt") -> DeliveryReceipt:
    """严格解码交付回执（13 键；坏枚举/不自洽阶段 fail-closed）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DELIVERY_RECEIPT_KEYS, path)
    phase = _require_choice(value["phase"], DELIVERY_PHASES, f"{path}.phase", "invalid_delivery_phase")
    publish_effect = _require_choice(
        value["publish_effect"], DELIVERY_PUBLISH_EFFECTS, f"{path}.publish_effect", "invalid_publish_effect"
    )
    cancel_effect = _require_choice(
        value["cancel_effect"], DELIVERY_CANCEL_EFFECTS, f"{path}.cancel_effect", "invalid_cancel_effect"
    )
    output_raw = value["output_digest"]
    output_digest = None if output_raw is None else _require_digest(output_raw, f"{path}.output_digest")
    _validate_delivery_effects(phase, publish_effect, cancel_effect, output_digest)
    error_raw = value["error_code"]
    if error_raw is not None and not isinstance(error_raw, str):
        _fail("invalid_type", f"{path}.error_code")
    if phase == "unknown" and error_raw is None:
        _fail("missing_field", f"{path}.error_code")
    next_action_raw = value["next_action"]
    if next_action_raw is not None and not isinstance(next_action_raw, str):
        _fail("invalid_type", f"{path}.next_action")
    return DeliveryReceipt(
        command_record_id=_require_str(value["command_record_id"], f"{path}.command_record_id"),
        delivery_id=_require_str(value["delivery_id"], f"{path}.delivery_id"),
        artifact_id=_require_str(value["artifact_id"], f"{path}.artifact_id"),
        artifact_version=_require_positive_revision(value["artifact_version"], f"{path}.artifact_version"),
        artifact_digest=_require_digest(value["artifact_digest"], f"{path}.artifact_digest"),
        format=_require_choice(value["format"], DELIVERY_FORMATS, f"{path}.format", "invalid_delivery_format"),
        phase=phase,
        publish_effect=publish_effect,
        cancel_effect=cancel_effect,
        output_digest=output_digest,
        verified_at=_require_utc_timestamp(value["verified_at"], f"{path}.verified_at"),
        error_code=error_raw,
        next_action=next_action_raw,
    )


#: 交付 manifest 结构版本（JSONB ``agent_operation.delivery_manifest``，§9.9/§10.2）。
DELIVERY_MANIFEST_VERSION: Final = 1

#: 允许的阶段迁移；``unknown`` 与终态不可再迁移（unknown 不得借新 delivery 绕过）。
_DELIVERY_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "manifest_saved": frozenset({"staged", "cancelled_before_publish", "unknown"}),
    "staged": frozenset({"validated", "cancelled_before_publish", "unknown"}),
    "validated": frozenset({"publish_claimed", "cancelled_before_publish", "unknown"}),
    "publish_claimed": frozenset({"published_confirmed", "unknown"}),
    "published_confirmed": frozenset(),
    "cancelled_before_publish": frozenset(),
    "unknown": frozenset(),
}


@dataclass(frozen=True, slots=True)
class DeliveryManifest:
    """文件交付完整合同（§9.9 ``delivery_manifest``）：目标、格式、成果版本/摘要、
    暂存输出摘要与当前来源状态；``phase`` 只表示交付阶段，不表示成果质量。"""

    delivery_id: str
    command: CommandRef
    artifact_id: str
    artifact_version: int
    artifact_digest: str
    format: str
    format_rule_version: int
    target: DeliveryTarget
    phase: str
    source_state: str
    output_digest: str | None
    output_bytes: int | None
    staged_path: str | None
    verified_at: str
    error_code: str | None


_DELIVERY_MANIFEST_KEYS: Final = frozenset(
    {
        "schema_version",
        "delivery_id",
        "command",
        "artifact_id",
        "artifact_version",
        "artifact_digest",
        "format",
        "format_rule_version",
        "target",
        "phase",
        "source_state",
        "output_digest",
        "output_bytes",
        "staged_path",
        "verified_at",
        "error_code",
    }
)


def validate_delivery_transition(previous: str, following: str) -> None:
    """校验交付阶段迁移；同阶段视为幂等重放，由调用方按内容判定。"""

    if previous not in DELIVERY_PHASES or following not in DELIVERY_PHASES:
        _fail("invalid_delivery_phase", "$.delivery_manifest.phase")
    if following == previous:
        return
    if following not in _DELIVERY_TRANSITIONS[previous]:
        _fail("invalid_delivery_transition", "$.delivery_manifest.phase")


def _validate_manifest_phase_fields(
    phase: str,
    output_digest: str | None,
    output_bytes: int | None,
    staged_path: str | None,
    error_code: str | None,
) -> None:
    if (output_digest is None) != (output_bytes is None):
        _fail("missing_field", "$.delivery_manifest.output_bytes")
    if phase == "manifest_saved":
        if output_digest is not None or staged_path is not None:
            _fail("invalid_delivery_phase", "$.delivery_manifest.phase")
    elif phase in {"staged", "validated", "publish_claimed"}:
        if output_digest is None:
            _fail("missing_field", "$.delivery_manifest.output_digest")
        if staged_path is None:
            _fail("missing_field", "$.delivery_manifest.staged_path")
    elif phase in {"published_confirmed", "cancelled_before_publish"}:
        if staged_path is not None:
            _fail("invalid_delivery_phase", "$.delivery_manifest.staged_path")
    if phase == "published_confirmed" and output_digest is None:
        _fail("missing_field", "$.delivery_manifest.output_digest")
    if phase == "unknown" and error_code is None:
        _fail("missing_field", "$.delivery_manifest.error_code")


def encode_delivery_manifest(manifest: DeliveryManifest) -> dict[str, Any]:
    """DeliveryManifest → wire 16 键（命令/目标/成果身份/阶段字段逐项复核）。"""

    command_wire = encode_artifact_delivery_command(
        ArtifactDeliveryCommand(
            command=manifest.command,
            artifact_id=manifest.artifact_id,
            artifact_version=manifest.artifact_version,
            format=manifest.format,
            format_rule_version=manifest.format_rule_version,
            delivery_id=manifest.delivery_id,
            target=manifest.target,
            expected_delivery_revision=manifest.command.expected_revision,
        )
    )
    phase = _require_choice(manifest.phase, DELIVERY_PHASES, "$.delivery_manifest.phase", "invalid_delivery_phase")
    source_state = _require_choice(
        manifest.source_state, DELIVERY_SOURCE_STATES, "$.delivery_manifest.source_state", "invalid_source_state"
    )
    output_digest = (
        None
        if manifest.output_digest is None
        else _require_digest(manifest.output_digest, "$.delivery_manifest.output_digest")
    )
    output_bytes = manifest.output_bytes
    if output_bytes is not None and (isinstance(output_bytes, bool) or not isinstance(output_bytes, int)):
        _fail("invalid_type", "$.delivery_manifest.output_bytes")
    if output_bytes is not None and output_bytes < 0:
        _fail("invalid_range", "$.delivery_manifest.output_bytes")
    staged_path = manifest.staged_path
    if staged_path is not None:
        staged_path = validate_delivery_target_path(staged_path, "$.delivery_manifest.staged_path")
    error_raw = manifest.error_code
    if error_raw is not None and not isinstance(error_raw, str):
        _fail("invalid_type", "$.delivery_manifest.error_code")
    _validate_manifest_phase_fields(phase, output_digest, output_bytes, staged_path, error_raw)
    return {
        "schema_version": DELIVERY_MANIFEST_VERSION,
        "delivery_id": command_wire["delivery_id"],
        "command": command_wire["command"],
        "artifact_id": command_wire["artifact_id"],
        "artifact_version": command_wire["artifact_version"],
        "artifact_digest": _require_digest(manifest.artifact_digest, "$.delivery_manifest.artifact_digest"),
        "format": command_wire["format"],
        "format_rule_version": command_wire["format_rule_version"],
        "target": command_wire["target"],
        "phase": phase,
        "source_state": source_state,
        "output_digest": output_digest,
        "output_bytes": output_bytes,
        "staged_path": staged_path,
        "verified_at": _require_utc_timestamp(manifest.verified_at, "$.delivery_manifest.verified_at"),
        "error_code": error_raw,
    }


def decode_delivery_manifest(value: object, path: str = "$.delivery_manifest") -> DeliveryManifest:
    """严格解码交付 manifest（16 键、版本 1、阶段字段自洽）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DELIVERY_MANIFEST_KEYS, path)
    version = value["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        _fail("invalid_type", f"{path}.schema_version")
    if version != DELIVERY_MANIFEST_VERSION:
        _fail("unsupported_schema_version", f"{path}.schema_version")
    command_wire = {
        "command": value["command"],
        "artifact_id": value["artifact_id"],
        "artifact_version": value["artifact_version"],
        "format": value["format"],
        "format_rule_version": value["format_rule_version"],
        "delivery_id": value["delivery_id"],
        "target": value["target"],
        "expected_delivery_revision": None,
    }
    command_ref = decode_command_ref(value["command"], f"{path}.command")
    command_wire["expected_delivery_revision"] = command_ref.expected_revision
    decoded_command = decode_artifact_delivery_command(command_wire, f"{path}.command_input")
    phase = _require_choice(value["phase"], DELIVERY_PHASES, f"{path}.phase", "invalid_delivery_phase")
    source_state = _require_choice(
        value["source_state"], DELIVERY_SOURCE_STATES, f"{path}.source_state", "invalid_source_state"
    )
    output_raw = value["output_digest"]
    output_digest = None if output_raw is None else _require_digest(output_raw, f"{path}.output_digest")
    output_bytes_raw = value["output_bytes"]
    if output_bytes_raw is not None and (
        isinstance(output_bytes_raw, bool) or not isinstance(output_bytes_raw, int) or output_bytes_raw < 0
    ):
        _fail("invalid_type", f"{path}.output_bytes")
    staged_raw = value["staged_path"]
    staged_path = None if staged_raw is None else validate_delivery_target_path(staged_raw, f"{path}.staged_path")
    error_raw = value["error_code"]
    if error_raw is not None and not isinstance(error_raw, str):
        _fail("invalid_type", f"{path}.error_code")
    _validate_manifest_phase_fields(phase, output_digest, output_bytes_raw, staged_path, error_raw)
    return DeliveryManifest(
        delivery_id=decoded_command.delivery_id,
        command=decoded_command.command,
        artifact_id=decoded_command.artifact_id,
        artifact_version=decoded_command.artifact_version,
        artifact_digest=_require_digest(value["artifact_digest"], f"{path}.artifact_digest"),
        format=decoded_command.format,
        format_rule_version=decoded_command.format_rule_version,
        target=decoded_command.target,
        phase=phase,
        source_state=source_state,
        output_digest=output_digest,
        output_bytes=output_bytes_raw,
        staged_path=staged_path,
        verified_at=_require_utc_timestamp(value["verified_at"], f"{path}.verified_at"),
        error_code=error_raw,
    )


@dataclass(frozen=True, slots=True)
class DeliveryPhaseReceipt:
    """交付阶段 CAS 回执（revision 单调；``idempotent`` 表示同内容重放）。"""

    delivery_id: str
    operation_id: str
    phase: str
    revision: int
    artifact_id: str
    artifact_version: int
    idempotent: bool


@dataclass(frozen=True, slots=True)
class DeliveryRead:
    """原交付查回（§10.5）：operation 行当前修订 + 已存 manifest。

    ``manifest is None`` 表示该 operation 尚无交付记录（初始态），与「查不到
    operation」（``INPUT_INVALID``）和「坏行」（``OPERATION_UNKNOWN``）分开，
    不得互相冒充。会话已完成联合清理且该 operation 曾落过交付（artifact 身份
    已写）时 ``manifest_cleaned=True``：交付事实存在但回执正文按 §10.7 清理，
    不得读成「从未交付」（R21）；从未交付的 operation 仍报初始态。
    """

    operation_id: str
    revision: int
    manifest: DeliveryManifest | None
    manifest_cleaned: bool = False


# ---------------------------------------------------------------------------
# E-05-3 三格式渲染与逐字段回读契约（DD-F26 / §10.5 / HANDOFF §10.2）
# ---------------------------------------------------------------------------

#: 交付文件记录域：成果元数据、完整正文、``result_payload`` 结构字段。
DELIVERY_RENDER_RECORDS: Final[frozenset[str]] = frozenset({"metadata", "body", "payload"})

#: 显式值类型（None/0/False/空串互不混同；数字保留原词法）。
DELIVERY_VALUE_TYPES: Final[frozenset[str]] = frozenset({"null", "string", "int", "float", "bool", "object", "array"})

#: 三格式共用的可逆列（表头文本即 wire 拼写）。
DELIVERY_RENDER_COLUMNS: Final[tuple[str, ...]] = ("path", "value_type", "chunk_index", "chunk_count", "value")

#: 元数据字段固定顺序（渲染顺序稳定 → 摘要可复现）。
_DELIVERY_METADATA_ORDER: Final[tuple[str, ...]] = (
    "artifact_id",
    "artifact_version",
    "session_id",
    "execution_id",
    "round_id",
    "artifact_kind",
    "input_revision",
    "actual_input_digest",
    "actual_model_ref",
    "result_digest",
    "source_available_state",
    "current_source_state",
    "summary",
    "published_at",
    "cleaned_at",
    "updated_at",
    "revision",
    "body_available",
    "unavailable_reason",
)

_METADATA_POINTER_RE: Final = re.compile(r"[a-z][a-z0-9_]*")
_JSON_POINTER_RE: Final = re.compile(r"(?:/(?:[^~/]|~[01])*)*")
_JSON_INTEGER_RE: Final = re.compile(r"-?(?:0|[1-9][0-9]*)")


@dataclass(frozen=True, slots=True)
class DeliveryField:
    """交付文件的一行：``record:pointer`` 身份 + 显式类型 + 可逆分段。

    ``path`` 为 ``record`` 与 ``pointer`` 的稳定拼接（元数据指针为字段名、
    正文为空、``result_payload`` 为 RFC6901 JSON Pointer）；同一 ``path`` 的
    分段必须从 1 连续到 ``chunk_count``，拼接即原值，不截断。
    """

    record: str
    pointer: str
    value_type: str
    value_text: str
    chunk_index: int = 1
    chunk_count: int = 1

    @property
    def path(self) -> str:
        return f"{self.record}:{self.pointer}"


@dataclass(frozen=True, slots=True)
class RenderedDelivery:
    """已渲染的交付内容（字节 + 摘要）；格式规则版本见 ``DELIVERY_FORMAT_RULE_VERSION``。"""

    format: str
    content: bytes
    digest: str
    field_count: int
    chunk_count: int


def _utf16_units(text: str) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _escape_json_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _chunked_fields(record: str, pointer: str, value_type: str, text: str) -> tuple[DeliveryField, ...]:
    """按 30,000 UTF-16 单元切分单个字段（非字符串值单段）。"""

    if value_type != "string":
        return (DeliveryField(record, pointer, value_type, text),)
    chunks = split_utf16_chunks(text)
    count = len(chunks)
    return tuple(
        DeliveryField(record, pointer, value_type, chunk, index, count) for index, chunk in enumerate(chunks, start=1)
    )


def _scalar_field(record: str, pointer: str, value: object, path: str) -> tuple[DeliveryField, ...]:
    """元数据标量 → 显式类型字段（None/0/False/空串各自保留）。"""

    if value is None:
        return _chunked_fields(record, pointer, "null", "")
    if isinstance(value, bool):
        return _chunked_fields(record, pointer, "bool", "true" if value else "false")
    if isinstance(value, int):
        return _chunked_fields(record, pointer, "int", str(value))
    if isinstance(value, str):
        return _chunked_fields(record, pointer, "string", value)
    _fail("invalid_type", path)


def _payload_fields(value: object, pointer: str) -> tuple[DeliveryField, ...]:
    """把 ``result_payload`` 展开成逐节点字段（容器显式标记类型）。"""

    if isinstance(value, dict):
        fields = [DeliveryField("payload", pointer, "object", "")]
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("invalid_json", "$.delivery.payload")
            token = _escape_json_pointer_token(key)
            fields.extend(_payload_fields(child, f"{pointer}/{token}"))
        return tuple(fields)
    if isinstance(value, list):
        fields = [DeliveryField("payload", pointer, "array", "")]
        for index, child in enumerate(value):
            fields.extend(_payload_fields(child, f"{pointer}/{index}"))
        return tuple(fields)
    if isinstance(value, ExactNumber):
        kind = "int" if value.raw_type == INTEGER_RAW_TYPE else "float"
        return _chunked_fields("payload", pointer, kind, value.lexeme)
    if value is None:
        return _chunked_fields("payload", pointer, "null", "")
    if isinstance(value, bool):
        return _chunked_fields("payload", pointer, "bool", "true" if value else "false")
    if isinstance(value, str):
        return _chunked_fields("payload", pointer, "string", value)
    _fail("invalid_json", "$.delivery.payload")


def _validate_delivery_pointer(record: str, pointer: object, path: str) -> str:
    if not isinstance(pointer, str):
        _fail("invalid_pointer", path)
    if record == "metadata":
        if _METADATA_POINTER_RE.fullmatch(pointer) is None:
            _fail("invalid_pointer", path)
        return pointer
    if record == "body":
        if pointer != "":
            _fail("invalid_pointer", path)
        return pointer
    if pointer != "" and _JSON_POINTER_RE.fullmatch(pointer) is None:
        _fail("invalid_pointer", path)
    return pointer


def _validate_delivery_value(field: DeliveryField, path: str) -> None:
    value_type = field.value_type
    text = field.value_text
    if value_type in {"null", "object", "array"}:
        if text != "":
            _fail("invalid_value", path)
    elif value_type == "bool":
        if text not in {"true", "false"}:
            _fail("invalid_value", path)
    elif value_type == "int":
        if _JSON_INTEGER_RE.fullmatch(text) is None:
            _fail("invalid_value", path)
    elif value_type == "float":
        if _JSON_NUMBER_RE.fullmatch(text) is None or not ("." in text or "e" in text or "E" in text):
            _fail("invalid_value", path)
    if value_type != "string" and field.chunk_count != 1:
        _fail("invalid_chunk_range", path)


def validate_delivery_fields(fields: object) -> tuple[DeliveryField, ...]:
    """校验交付字段（类型/指针/分段连续/段长上限）；失败抛 :class:`AgentValidationError`。"""

    if not isinstance(fields, (tuple, list)) or not fields:
        _fail("invalid_type", "$.delivery.fields")
    validated: list[DeliveryField] = []
    groups: dict[str, dict[str, object]] = {}
    for index, field in enumerate(fields):
        path = f"$.delivery.fields[{index}]"
        if not isinstance(field, DeliveryField):
            _fail("invalid_type", path)
        if field.record not in DELIVERY_RENDER_RECORDS:
            _fail("invalid_record", f"{path}.record")
        pointer = _validate_delivery_pointer(field.record, field.pointer, f"{path}.pointer")
        if field.value_type not in DELIVERY_VALUE_TYPES:
            _fail("invalid_value_type", f"{path}.value_type")
        if not isinstance(field.value_text, str):
            _fail("invalid_type", f"{path}.value_text")
        chunk_index = field.chunk_index
        chunk_count = field.chunk_count
        if (
            isinstance(chunk_index, bool)
            or isinstance(chunk_count, bool)
            or not isinstance(chunk_index, int)
            or not isinstance(chunk_count, int)
            or chunk_index < 1
            or chunk_count < 1
            or chunk_index > chunk_count
        ):
            _fail("invalid_chunk_range", f"{path}.chunk_index")
        if _utf16_units(field.value_text) > DELIVERY_CHUNK_MAX_UTF16:
            _fail("chunk_too_large", f"{path}.value_text")
        _validate_delivery_value(field, path)
        key = f"{field.record}:{pointer}"
        group = groups.setdefault(key, {"type": field.value_type, "count": chunk_count, "chunks": set()})
        if group["type"] != field.value_type or group["count"] != chunk_count:
            _fail("inconsistent_chunks", path)
        chunk_set = cast(set[int], group["chunks"])
        if chunk_index in chunk_set:
            _fail("duplicate_field", path)
        chunk_set.add(chunk_index)
        validated.append(
            DeliveryField(field.record, pointer, field.value_type, field.value_text, chunk_index, chunk_count)
        )
    for key, group in groups.items():
        chunk_set = cast(set[int], group["chunks"])
        count = cast(int, group["count"])
        if chunk_set != set(range(1, count + 1)):
            _fail("missing_chunk", f"$.delivery.fields.{key}")
        if count > 1 and any(field.value_text == "" for field in validated if f"{field.record}:{field.pointer}" == key):
            _fail("invalid_chunk_range", f"$.delivery.fields.{key}")
    return tuple(validated)


def build_delivery_fields(
    read: object, *, body_text: str | None = None, current_source_state: str | None = None
) -> tuple[DeliveryField, ...]:
    """把已保存成果版本转换为交付字段（完整正文 + 元数据 + 结构字段）。

    ``body_text`` 提供跨页组装后的完整正文；读取结果仍有 ``next_cursor`` 且
    未提供完整正文时拒绝（导出分页正文等同静默截断）。``result_payload`` 为
    ``None`` 表示未保存结构字段，与保存了空容器不同，不补造字段（R21）。

    来源状态分列两列，不得混同：``source_available_state`` 是成果保存时记录
    的历史状态；``current_source_state`` 是本次交付的核定状态，未显式提供时
    按 ``unknown``（未核 ≠ 可用，R21）。两者都按统一词表口径规范化。
    """

    if not isinstance(read, ArtifactRead):
        _fail("invalid_type", "$.delivery.artifact_read")
    if read.next_cursor is not None and body_text is None:
        _fail("page_not_complete", "$.delivery.artifact_read.next_cursor")
    full_body = body_text if body_text is not None else read.body_text
    if read.body_available and full_body is None:
        _fail("body_missing", "$.delivery.artifact_read.body_text")

    fields: list[DeliveryField] = []
    metadata: dict[str, object] = {
        "artifact_id": read.artifact_id,
        "artifact_version": read.artifact_version,
        "session_id": read.session_id,
        "execution_id": read.execution_id,
        "round_id": read.round_id,
        "artifact_kind": read.artifact_kind,
        "input_revision": read.input_revision,
        "actual_input_digest": read.actual_input_digest,
        "actual_model_ref": read.actual_model_ref,
        "result_digest": read.result_digest,
        "source_available_state": canonical_delivery_source_state(read.source_available_state),
        "current_source_state": canonical_delivery_source_state(current_source_state),
        "summary": read.summary,
        "published_at": read.published_at,
        "cleaned_at": read.cleaned_at,
        "updated_at": read.updated_at,
        "revision": read.revision,
        "body_available": read.body_available,
        "unavailable_reason": read.unavailable_reason,
    }
    for key in _DELIVERY_METADATA_ORDER:
        fields.extend(_scalar_field("metadata", key, metadata[key], f"$.delivery.metadata.{key}"))
    if read.body_available and full_body is not None:
        fields.extend(_chunked_fields("body", "", "string", full_body))
    if read.result_payload is not None:
        fields.extend(_payload_fields(parse_preserving_numbers(read.result_payload), ""))
    return validate_delivery_fields(tuple(fields))


# ---------------------------------------------------------------------------
# 计划提案（DD §8 F21 / §10.3；HANDOFF §7.3，E-04）
# ---------------------------------------------------------------------------
#
# 计划提案是 Harness ``PlanStore`` 的私有工作状态：以不可变 asset 版本保存
# （``asset_kind='plan'``），绑定 execution 与 plan_revision；只存提案，不更新
# ``agent_round``、不改已派发图、不成为第二任务状态机。``plan_revision=0``
# 表示尚无提案；每次写入以预期版 CAS 推进到 +1。
# 本模块只做结构 codec 与摘要，不做 DAG/角色校验（那是派发前
# ``validate_plan_spec_proposal`` 的职责）。

#: 计划提案结构版本（进入规范摘要前缀，版本演进不改写旧版身份）。
PLAN_STORE_VERSION: Final[int] = 1

#: Harness ``TaskStatus`` 冻结五值（blocked 仅在启用子任务时可产生）。
PLAN_ITEM_STATUSES: Final[frozenset[str]] = frozenset({"pending", "in_progress", "completed", "cancelled", "blocked"})

_PLAN_PROPOSAL_KEYS: Final = frozenset({"plan_store_version", "plan_revision", "items"})
_PLAN_ITEM_KEYS: Final = frozenset({"id", "content", "status", "active_form", "parent_id", "depends_on"})


@dataclass(frozen=True, slots=True)
class PlanItemSnapshot:
    """单个计划步骤的冻结快照（对应 Harness ``PlanItem``）。

    ``content``/``active_form`` 为用户可见正文，原样保留（不 trim、不改
    Unicode）；``id`` 非空且在文档内唯一；数组顺序即身份（不排序）。
    """

    id: str
    content: str
    status: str
    active_form: str
    parent_id: str | None
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanProposal:
    """一次计划提案版本（不可变；``plan_revision`` 每 execution 单调递增）。"""

    execution_id: str
    plan_revision: int
    items: tuple[PlanItemSnapshot, ...]


def plan_asset_id(execution_id: str) -> str:
    """计划提案资产身份：每 execution 一个资产，``plan_revision`` 为版本。"""

    return f"plan:{require_identity(execution_id, '$.execution_id')}"


def _validate_plan_item_snapshot(item: PlanItemSnapshot, path: str) -> None:
    """内存对象结构校验（与 wire 解码同判据，防止绕过 codec 写入坏行）。"""

    require_identity(item.id, f"{path}.id")
    if not isinstance(item.content, str):
        _fail("invalid_type", f"{path}.content")
    if not isinstance(item.active_form, str):
        _fail("invalid_type", f"{path}.active_form")
    if not isinstance(item.status, str) or item.status not in PLAN_ITEM_STATUSES:
        _fail("invalid_enum", f"{path}.status")
    if item.parent_id is not None:
        require_identity(item.parent_id, f"{path}.parent_id")
    if not isinstance(item.depends_on, tuple):
        _fail("invalid_type", f"{path}.depends_on")
    for index, dependency in enumerate(item.depends_on):
        require_identity(dependency, f"{path}.depends_on[{index}]")


def decode_plan_item_snapshot(value: object, path: str = "$.plan_item") -> PlanItemSnapshot:
    """严格解码单个步骤（六键必填、禁额外键；status 只接受冻结五值）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _PLAN_ITEM_KEYS, path)
    status = value["status"]
    if not isinstance(status, str) or status not in PLAN_ITEM_STATUSES:
        _fail("invalid_enum", f"{path}.status")
    content = value["content"]
    if not isinstance(content, str):
        _fail("invalid_type", f"{path}.content")
    active_form = value["active_form"]
    if not isinstance(active_form, str):
        _fail("invalid_type", f"{path}.active_form")
    raw_depends = value["depends_on"]
    if not isinstance(raw_depends, list):
        _fail("invalid_type", f"{path}.depends_on")
    return PlanItemSnapshot(
        id=_require_str(value["id"], f"{path}.id"),
        content=content,
        status=status,
        active_form=active_form,
        parent_id=_opt_str(value["parent_id"], f"{path}.parent_id"),
        depends_on=tuple(_require_str(item, f"{path}.depends_on[{index}]") for index, item in enumerate(raw_depends)),
    )


def decode_plan_proposal(value: object, *, execution_id: str, path: str = "$.plan_proposal") -> PlanProposal:
    """严格解码计划提案文档（``execution_id`` 来自存储绑定，不在文档内）。

    Raises:
        AgentValidationError: ``invalid_keys_missing`` / ``invalid_keys_unknown`` /
            ``unsupported_version`` / ``invalid_type`` / ``invalid_range`` /
            ``invalid_enum`` / ``duplicate_plan_item`` 及通用身份错误码。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _PLAN_PROPOSAL_KEYS, path)
    version = value["plan_store_version"]
    # 结构版本必须是整数 1：bool 是 int 子类、1.0/Decimal("1.0") 与 1 等值，
    # 仅做等值比较会放行这些输入，故先显式拒 bool 与非整数类型，再核版本值。
    if isinstance(version, bool) or not isinstance(version, int) or version != PLAN_STORE_VERSION:
        _fail("unsupported_version", f"{path}.plan_store_version")
    revision = require_revision(value["plan_revision"], f"{path}.plan_revision")
    raw_items = value["items"]
    if not isinstance(raw_items, list):
        _fail("invalid_type", f"{path}.items")
    items = tuple(decode_plan_item_snapshot(item, f"{path}.items[{index}]") for index, item in enumerate(raw_items))
    seen: set[str] = set()
    for index, item in enumerate(items):
        if item.id in seen:
            _fail("duplicate_plan_item", f"{path}.items[{index}].id")
        seen.add(item.id)
    return PlanProposal(
        execution_id=require_identity(execution_id, f"{path}.execution_id"),
        plan_revision=revision,
        items=items,
    )


def decode_plan_proposal_document(document: str, *, execution_id: str) -> PlanProposal:
    """规范 JSON 文本 → 计划提案（落库正文读回入口）。"""

    return decode_plan_proposal(decode_json_doc(document), execution_id=execution_id)


def normalize_plan_proposal(proposal: PlanProposal) -> CanonicalJson:
    """规范化计划提案 wire（结构版本 + revision + 全键显式；顺序即身份）。"""

    if not isinstance(proposal, PlanProposal):
        _fail("invalid_type", "$.plan_proposal")
    require_identity(proposal.execution_id, "$.plan_proposal.execution_id")
    require_revision(proposal.plan_revision, "$.plan_proposal.plan_revision")
    if not isinstance(proposal.items, tuple):
        _fail("invalid_type", "$.plan_proposal.items")
    seen: set[str] = set()
    for index, item in enumerate(proposal.items):
        if not isinstance(item, PlanItemSnapshot):
            _fail("invalid_type", f"$.plan_proposal.items[{index}]")
        _validate_plan_item_snapshot(item, f"$.plan_proposal.items[{index}]")
        if item.id in seen:
            _fail("duplicate_plan_item", f"$.plan_proposal.items[{index}].id")
        seen.add(item.id)
    return json_doc(
        {
            "plan_store_version": PLAN_STORE_VERSION,
            "plan_revision": proposal.plan_revision,
            "items": list(proposal.items),
        }
    )


def plan_proposal_digest(proposal: PlanProposal) -> str:
    """计划提案内容摘要（SHA-256，绑定结构版本与 revision；顺序入身份）。"""

    normalize_plan_proposal(proposal)
    return sha256_hex(json_doc(["plan_proposal", PLAN_STORE_VERSION, proposal.plan_revision, list(proposal.items)]))
