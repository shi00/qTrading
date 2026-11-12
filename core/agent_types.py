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
from collections.abc import Sequence, Set as AbstractSet
from dataclasses import dataclass, is_dataclass
from datetime import datetime as dt
from datetime import UTC
from decimal import Decimal
from enum import Enum
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

    重算 ``selected_range_digest`` 并与载荷比对：不一致即拒绝（保存/决定重读/
    每 attempt 核权均重算，不信调用方 digest）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys(value, _DATA_CONSENT_SOURCE_ITEM_KEYS, path)

    origin = decode_source_version_ref(value["locator"]["origin_ref"], f"{path}.locator.origin_ref")
    # 源项顶层身份与 locator.origin_ref 一致（同一受管源版，冗余字段不得分叉）
    if (
        value["source_kind"] != origin.source_kind.value
        or value["source_id"] != origin.source_id
        or value["source_version"] != origin.source_version
        or value["source_digest"] != origin.source_digest
    ):
        _fail("identity_mismatch", path)

    selected_range = decode_selected_range(value["selected_range"], f"{path}.selected_range")
    digest = _require_digest(value["selected_range_digest"], f"{path}.selected_range_digest")
    if digest != selected_range_digest(selected_range):
        _fail("digest_mismatch", f"{path}.selected_range_digest")

    return DataConsentSourceItem(
        source_kind=origin.source_kind,
        source_id=origin.source_id,
        source_version=origin.source_version,
        source_digest=origin.source_digest,
        selected_range=selected_range,
        locator=decode_source_locator(value["locator"], f"{path}.locator"),
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
    if grant_id is not None and not isinstance(grant_id, str):
        _fail("invalid_type", f"{path}.verified_connection_grant_id")
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
    # §9.8：schema_version 仅整数 1（拒 bool）
    if isinstance(schema_version, bool) or schema_version != 1:
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
    # 内外一致性（§9.8）：重复的 target_fingerprint/purpose/scope_version 必须等于外层
    if (
        connection_ref.target_fingerprint != value["target_fingerprint"]
        or connection_ref.purpose != value["purpose"]
        or connection_ref.scope_version != value["scope_version"]
    ):
        _fail("identity_mismatch", f"{path}.connection_authorization_ref")

    target_fingerprint = _require_str(value["target_fingerprint"], f"{path}.target_fingerprint")
    purpose = _require_str(value["purpose"], f"{path}.purpose")
    scope_version = _require_p(value["scope_version"], f"{path}.scope_version")

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
    """源-消费者谱系边语义：origin=源→其 originating message；accepted_input=源→受理输入版。"""

    ORIGIN = "origin"
    ACCEPTED_INPUT = "accepted_input"


#: 可靠事件载荷结构标识（§4.4 payload_kind；本轮工程冻结首批值——设计未逐枚举）。
class EventPayloadKind(Enum):
    """public_message_saved / input_bound 事件载荷 v1（§4.4/§9.6）。"""

    PUBLIC_MESSAGE = "public_message"
    INPUT_BINDING = "input_binding"


@dataclass(frozen=True)
class AcceptRequest:
    """受理输入（SQ-01/§3.3.2/§3.4）：受理时点冻结（INV-04）。

    ``text`` 为用户消息正文（非空，不 trim/改 Unicode）；``manifest_payload``
    为 FrozenManifest 规范 JSON（intent_digest 由其规范字节派生，单一事实源）；
    ``source_display_name`` 由服务层决定（i18n 文案），DAO 不猜显示名。
    """

    user_scope: str
    session_id: str
    source_module: str
    business_request_id: str
    client_command_id: str
    #: 目标会话草稿预期版（§3.1：0=目标尚不存在）。
    expected_revision: int
    node_id: str
    workflow_id: str
    mode: Mode
    purpose: Purpose
    text: str
    manifest_payload: CanonicalJson
    source_display_name: str


@dataclass(frozen=True)
class AcceptIdentity:
    """受理身份派生结果（§3.3.2 稳定身份规则，纯函数）。"""

    command_record_id: str
    message_id: str
    source_id: str
    source_version: str
    source_digest: str
    intent_digest: str


def derive_accept_identity(request: AcceptRequest) -> AcceptIdentity:
    """按 §3.3.2 派生受理稳定身份并校验输入（不 trim/改 Unicode）。

    - ``command_record_id = stable_id("command",[user_scope,exact_target_id,
      client_command_id])``，exact_target_id 即目标 session_id；
    - ``message_id = stable_id("user_message",[user_scope,session_id,
      command_record_id])``，message_version="1"；
    - ``source_id = stable_id("message_source",[user_scope,session_id,
      message_id])``，source_version="1"；
    - ``source_digest = sha256(text UTF-8)``，``intent_digest =
      sha256(manifest_payload UTF-8)``（manifest 已是规范 JSON）。
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
    if not isinstance(request.manifest_payload, str) or request.manifest_payload == "":
        _fail("invalid_value", "$.manifest_payload")
    _require_str(request.source_display_name, "$.source_display_name")

    command_record_id = stable_id("command", [user_scope, session_id, request.client_command_id])
    message_id = stable_id("user_message", [user_scope, session_id, command_record_id])
    source_id = stable_id("message_source", [user_scope, session_id, message_id])
    return AcceptIdentity(
        command_record_id=command_record_id,
        message_id=message_id,
        source_id=source_id,
        source_version="1",
        source_digest=sha256_hex(request.text),
        intent_digest=sha256_hex(request.manifest_payload),
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
    单一事实源派生（``sha256``）；``workflow_id`` 为幂等重放键（§9.5 UNIQUE）。
    """

    user_scope: str
    session_id: str
    execution_id: str
    node_id: str
    workflow_id: str
    workflow_code_version: str
    run_epoch: int
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
    """

    user_scope: str
    decision_id: str
    client_command_id: str
    expected_decision_revision: int
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
    """

    user_scope: str
    decision_id: str
    expected_grant_revision: int


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
    """队列修订事实（§9.13）：M2/S2 新输入版与 revision CAS 结果。"""

    execution_id: str
    command_record_id: str
    message_id: str
    source_id: str
    source_version: str
    input_digest: str
    input_revision: int
    execution_revision: int
    session_seq: int


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
    要求非空稳定身份。``binding`` 为完整 v1 绑定（20 键），其 user_scope/
    execution_id/round_id 须与命令身份一致；建行时 consent_revision=1，更新时
    =expected+1（§9.8 重复字段 consent_revision 与决定行 revision 同事务一致）。
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
    """校验保存命令并派生命令身份/输入摘要（§3.3.2 命令键 + §9.8 命令摘要）。"""

    _check_command_ref(command.command)
    _require_str(command.consent_id, "$.consent_id")
    _require_str(command.session_id, "$.session_id")
    _require_str(command.execution_id, "$.execution_id")
    _require_str(command.round_id, "$.round_id")
    _require_str(command.node_id, "$.node_id")
    binding = command.binding
    if not isinstance(binding, DataConsentBinding):
        _fail("invalid_type", "$.binding")
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
            "schema_version": 1,
            "action": "save",
            "command_id": command.command.command_id,
            "consent_id": command.consent_id,
            "expected_consent_revision": expected,
            "binding": encode_data_consent_binding(binding),
        }
    )
    return DataConsentCommandIdentity(command_record_id=command_record_id, input_digest=sha256_hex(canonical))


def derive_data_consent_revoke_identity(command: DataConsentRevokeCommand) -> DataConsentCommandIdentity:
    """校验撤销命令并派生命令身份/输入摘要（预期版参与摘要，§9.8）。"""

    _check_command_ref(command.command)
    _require_str(command.consent_id, "$.consent_id")
    if command.command.expected_revision < 1:
        _fail("invalid_revision", "$.command.expected_revision")
    command_record_id = stable_id(
        "command", [command.command.user_scope, command.consent_id, command.command.command_id]
    )
    canonical = json_doc(
        {
            "schema_version": 1,
            "action": "revoke",
            "command_id": command.command.command_id,
            "consent_id": command.consent_id,
            "expected_consent_revision": command.command.expected_revision,
        }
    )
    return DataConsentCommandIdentity(command_record_id=command_record_id, input_digest=sha256_hex(canonical))


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
    if isinstance(schema_version, bool) or schema_version != 1:
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

    执行事件身份一致性需原生事件 ID（不在包络 wire 内），由调用方以
    :func:`validate_event_identity` 复核；本解码只做结构层。
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
    if execution_id is not None:
        if execution_seq is None:
            _fail("identity_mismatch", f"{path}.execution_seq")
    elif execution_seq is not None:
        _fail("identity_mismatch", f"{path}.execution_seq")
    execution_seq_v = None
    if execution_seq is not None:
        if isinstance(execution_seq, bool) or not isinstance(execution_seq, int) or execution_seq < 1:
            _fail("invalid_revision", f"{path}.execution_seq")
        execution_seq_v = execution_seq
    native_fact_ref = value["native_fact_ref"]
    if native_fact_ref is not None:
        native_fact_ref = _require_str(native_fact_ref, f"{path}.native_fact_ref")
    return ResearchEvent(
        schema_version=1,
        event_id=_require_str(value["event_id"], f"{path}.event_id"),
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        execution_id=None if execution_id is None else _require_str(execution_id, f"{path}.execution_id"),
        node_id=None if value["node_id"] is None else _require_str(value["node_id"], f"{path}.node_id"),
        round_id=None if value["round_id"] is None else _require_str(value["round_id"], f"{path}.round_id"),
        input_revision=input_revision,
        session_seq=session_seq,
        execution_seq=execution_seq_v,
        occurred_at=require_utc_timestamp(value["occurred_at"], f"{path}.occurred_at"),
        committed_at=require_utc_timestamp(value["committed_at"], f"{path}.committed_at"),
        event_kind=event_kind,
        subject_id=_require_str(value["subject_id"], f"{path}.subject_id"),
        subject_revision=subject_revision,
        payload_kind=_require_str(value["payload_kind"], f"{path}.payload_kind"),
        payload_canonical_json=_require_str(value["payload_canonical_json"], f"{path}.payload_canonical_json"),
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
