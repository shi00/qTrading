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


def _require_keys(obj: dict[str, object], required: set[str], path: str) -> None:
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
