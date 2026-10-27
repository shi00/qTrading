"""首页快讯与个股的确定性直接关联 + 去重纯逻辑（B4）。

本模块位于 ``data/`` 层，只依赖标准库，不导入 services/strategies/ui（R1 分层）。
首期只做**高精度直接关联**：股票代码 / 公司全称 / 唯一简称；行业、产品、人物、
地域、供应链、宏观等推理型关联一律不命中（保持只报确定、不报推测）。

去重规则（§8.4）：URL 相同 / 标准化标题相同且发布时间接近（同源 10 分钟内，
公告同日算接近）/ content_hash 相同 → 视为重复；跨 source_kind 不互去重。
"""

import datetime
import re

# 完整股票代码 token：`SH600519` / `600519.SH` / `sh600519` 等；前后不得紧邻字母数字
_FULL_CODE_RE = re.compile(r"(?<![0-9A-Za-z])(?:[A-Za-z]{2}\d{6}|\d{6}\.[A-Za-z]{1,3})(?![0-9A-Za-z])")
# 裸 6 位数字 token：前后不得紧邻字母数字（排除被其他数字/字母拼入的部分）
_BARE_CODE_RE = re.compile(r"(?<![0-9A-Za-z])\d{6}(?![0-9A-Za-z])")
_HANZI_RE = re.compile(r"[\u4e00-\u9fff]")

# 同源标题去重时间窗（秒）：news 10 分钟内；announcement（同日算接近）单独处理
_NEWS_NEAR_WINDOW_SECONDS = 600
# 公司全称最短汉字数（>= 此值视为全称直接匹配）
_FULL_NAME_MIN_CN = 4
# 简称最短汉字数（>= 此值且候选池唯一时才直接匹配）
_SHORT_NAME_MIN_CN = 3

# 去重触发原因（dropped 项使用）
_REASON_URL_SAME = "url_same"
_REASON_HASH_SAME = "content_hash_same"
_REASON_TITLE_TIME = "title_time_similar"


def _cn_len(name: str) -> int:
    """汉字数统计（用于区分全称/简称长度）。"""
    return len(_HANZI_RE.findall(name or ""))


def _is_excluded_number(six: str) -> bool:
    """裸 6 位代码的排除段：年份/日期段（19xx/20xx）、纯重复数字、连续递增/递减。"""
    if six.startswith(("19", "20")):
        return True  # 年份/日期段，如 202403
    if len(set(six)) == 1:
        return True  # 连续重复，如 888888
    digits = [int(ch) for ch in six]
    return all(digits[i + 1] == digits[i] + 1 for i in range(5)) or all(
        digits[i + 1] == digits[i] - 1 for i in range(5)
    )


def _full_code_tokens(text: str) -> list[str]:
    """从文本提取完整股票代码 token（大小写统一）。"""
    return [t.upper() for t in _FULL_CODE_RE.findall(text or "")]


# A 股交易所后缀（沪/深/北）；缺后缀或未知后缀的候选不猜测交易所
_EXCHANGES = frozenset({"SH", "SZ", "BJ"})


def _ts_code_exchange(code: str) -> str | None:
    """从标准 ts_code 取交易所后缀（大写）；缺后缀或非 A 股交易所返回 None。"""
    _, sep, exchange = code.partition(".")
    if not sep:
        return None
    exchange = exchange.upper()
    return exchange if exchange in _EXCHANGES else None


# 裸 6 位数字的计量语境（UIR-F04-006 明文契约）：该数字是金额/数量而非股票代码。
# 「股」后接 票/价/权/东 属股票指代（如「600519股票」），不算单位；
# 刻意不收「只/头/台/套/辆/个」等与常见字词连读的单字单位（如「600519只在主板上市」）。
_CN_AMOUNT_UNITS = (
    "亿元|千万元|百万元|万元|千元|元|亿|万|"
    "亿股|万股|千股|万手|万吨|手|股(?![票价权东])|份|张|吨|公斤|千克|克|"
    "米|公里|平方米|平米|立方米|千瓦时|兆瓦|桶|箱|瓶|袋|件|笔|户|"
    "块钱|美元|港元|港币|欧元|日元|人民币"
)
_EN_AMOUNT_UNITS = r"yuan|rmb|cny|usd|hkd|eur|jpy|dollars?|shares?|lots?|tons?|kg|million|billion|thousand"
# 数字之后：可选小数尾数 + 可选空白/逗号/右括号引号 + 单位
_AMOUNT_TAIL_RE = re.compile(
    rf"(?:\s*[.．]\d+)?[\s,，)）】」』”’\"']*(?:{_CN_AMOUNT_UNITS}|(?:{_EN_AMOUNT_UNITS})\b)",
    re.IGNORECASE,
)
# 数字之前紧邻的货币符号/币种（如 ``$600519`` / ``人民币600519``）
_AMOUNT_PREFIX_RE = re.compile(
    r"(?:US\$|HK\$|RMB|CNY|USD|HKD|EUR|JPY|[¥￥$]|人民币|美元|港元|港币)\s*$",
    re.IGNORECASE,
)
# 数字后直接接小数位：是数值（如 ``600519.00``），不是股票代码
_DECIMAL_TAIL_RE = re.compile(r"[.．]\d+")


def _is_measurement_context(text: str, start: int, token: str) -> bool:
    """裸数字是否处于金额/数量计量语境（前后紧邻判定，单位清单见 ``_bare_code_tokens``）。"""
    end = start + len(token)
    if _AMOUNT_PREFIX_RE.search(text, 0, start):
        return True
    if _DECIMAL_TAIL_RE.match(text, end):
        return True
    return _AMOUNT_TAIL_RE.match(text, end) is not None


def _bare_code_tokens(text: str) -> set[str]:
    """从文本提取可作为股票代码证据的裸 6 位数字 token（逐 token 按位置判定）。

    拒绝紧邻的金额/数量计量语境（裸数字是数值，不是股票引用）：
      - 金额单位：元、千元、万元、百万元、千万元、亿元、亿、万、块钱、美元、港元、港币、
        欧元、日元、人民币、RMB、CNY、USD、HKD、EUR、JPY、yuan、dollar(s)、
        thousand、million、billion；
      - 数量单位：股、万股、亿股、千股、手、万手、份、张、吨、万吨、公斤、千克、克、
        米、公里、平方米、平米、立方米、千瓦时、兆瓦、桶、箱、瓶、袋、件、笔、户、
        share(s)、lot(s)、ton(s)、kg；
      - 前缀货币符号/币种：``$`` / ``¥`` / ``￥`` / ``US$`` / ``HK$`` / 人民币 / 美元 / 港元；
      - 数字与单位之间允许空白、逗号、右括号/引号，并允许小数尾数（如 ``600519.00 元``）；
      - 数字后直接接小数位（如 ``600519.00``）视为数值，即使后面没有单位。

    逐 token 判定：同一文本出现多处同值数字时，只要有一处不在计量语境即保留；
    ``600519股票`` / ``600519股权`` 等股票指代保持兼容；本函数不删除既有合法裸代码支持。
    """
    src = text or ""
    tokens: set[str] = set()
    for match in _BARE_CODE_RE.finditer(src):
        token = match.group()
        if not _is_measurement_context(src, match.start(), token):
            tokens.add(token)
    return tokens


def _unique_ts_suffixes(candidates: list[dict]) -> dict[str, str]:
    """suffix6（纯 6 位）→ ts_code 映射；同一 suffix 重复不影响（同代码即同股）。"""
    mapping: dict[str, str] = {}
    for c in candidates:
        code = (c.get("ts_code") or "").strip()
        suffix = code.split(".")[0]
        if suffix:
            mapping[suffix] = code
    return mapping


def match_news_to_stock(news_text: str | None, candidates: list[dict]) -> list[str]:
    """判定一条 market_news 文本是否直接关联到候选股，返回关联的 ts_code 列表。

    candidates: list[dict]，每项含 ``ts_code`` 与 ``name``（股票名/简称，可为空）。
    高精度规则（命中之一即关联）：
      1. 完整股票代码：``SH600519`` / ``600519.SH`` 等（大小写不敏感）；前缀形式
         须与候选 ts_code 的交易所一致，缺后缀/未知交易所的候选不猜测交易所；
      2. 裸 6 位：满足 ``(?<![0-9A-Za-z])\\d{6}(?![0-9A-Za-z])`` 且非日期段/连续数字，
         且落在候选股 ts_code 池；紧邻金额/数量单位的数值不是代码证据（单位清单与
         位置判定契约见 :func:`_bare_code_tokens` 文档串）；
      3. 公司全称（>= 4 汉字）直接出现在文本；
      4. 简称在候选池唯一且 >= 3 汉字才直接匹配（歧义简称不关联）。
    """
    text = (news_text or "").strip()
    if not text:
        return []
    upper_text = text.upper()
    full_tokens = _full_code_tokens(upper_text)
    bare_tokens = _bare_code_tokens(text)
    suffix_to_code = _unique_ts_suffixes(candidates)

    # 名称出现频次（判定简称是否唯一）
    name_count: dict[str, int] = {}
    for c in candidates:
        name = (c.get("name") or "").strip()
        if name:
            name_count[name] = name_count.get(name, 0) + 1

    matched: list[str] = []
    matched_codes: set[str] = set()
    for c in candidates:
        code = (c.get("ts_code") or "").strip()
        if not code:
            continue
        suffix = code.split(".")[0]
        exchange = _ts_code_exchange(code)
        prefix_code = f"{exchange}{suffix}" if exchange else None
        matched_by: str | None = None

        # 1) 完整股票代码：规范后缀形式，或与候选同交易所的前缀形式（大小写已归一）
        if code.upper() in full_tokens or (prefix_code is not None and prefix_code in full_tokens):
            matched_by = "full_code"
        # 2) 裸 6 位（命中候选池 + 排除段）
        elif suffix in bare_tokens and suffix in suffix_to_code and not _is_excluded_number(suffix):
            matched_by = "bare_code"
        # 3) 公司全称 / 4) 唯一简称
        else:
            name = (c.get("name") or "").strip()
            if name in text:  # 全名或简称直接出现在文本
                cn = _cn_len(name)
                if cn >= _FULL_NAME_MIN_CN:
                    matched_by = "full_name"
                elif cn >= _SHORT_NAME_MIN_CN and name_count.get(name, 0) == 1:
                    matched_by = "unique_short_name"

        if matched_by and code not in matched_codes:
            matched_codes.add(code)
            matched.append(code)
    return matched


def _normalize_title(title: str | None) -> str:
    """标准化标题：去首尾空白、压缩内部空白、统一小写。"""
    return " ".join((title or "").split()).lower()


def _time_near(a: dict, b: dict) -> bool:
    """发布时间接近：announcement（同日算接近）；news 10 分钟内。"""
    pa: datetime.datetime | None = a.get("publish_time")
    pb: datetime.datetime | None = b.get("publish_time")
    if not pa or not pb:
        return False
    if a.get("source_kind") == "announcement" or b.get("source_kind") == "announcement":
        return pa.date() == pb.date()
    return abs(pa - pb).total_seconds() <= _NEWS_NEAR_WINDOW_SECONDS


def _duplicate_reason(a: dict, b: dict) -> str | None:
    """判定 a、b 是否重复并给出原因；None 表示不重复。仅同 source_kind 比较。"""
    a_url = a.get("url")
    b_url = b.get("url")
    if a_url and b_url and a_url == b_url:
        return _REASON_URL_SAME
    a_hash = a.get("content_hash")
    b_hash = b.get("content_hash")
    if a_hash and b_hash and a_hash == b_hash:
        return _REASON_HASH_SAME
    if _normalize_title(a.get("title")) and _normalize_title(a.get("title")) == _normalize_title(b.get("title")):
        if _time_near(a, b):
            return _REASON_TITLE_TIME
    return None


def dedupe_documents(docs: list[dict]) -> tuple[list[dict], list[dict]]:
    """确定性去重（§8.4）。返回 ``(kept, dropped)``。

    - 仅在相同 ``source_kind`` 之间去重；跨 source_kind 不互去重。
    - 保留先出现的文档；重复文档附加 ``dedup_reason`` 原因后放入 dropped。
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for doc in docs:
        reason: str | None = None
        for kept_doc in kept:
            if kept_doc.get("source_kind") != doc.get("source_kind"):
                continue
            reason = _duplicate_reason(kept_doc, doc)
            if reason:
                break
        if reason:
            dropped.append({**doc, "dedup_reason": reason})
        else:
            kept.append(doc)
    return kept, dropped
