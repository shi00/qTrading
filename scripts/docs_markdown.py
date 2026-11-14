"""docs_markdown.py — 文档 Markdown 统一解析视图（检视 §5.1，工作包 D3）。

职责边界（检视 §5.1 职责表）：一次解析形成标题/锚点、标准与引用式链接、HTML ID 与
HTML href、代码块/行号映射的不可变视图；缓存限本次调用。**不承担**：编辑文档、读秘密、联网、
业务导入 —— 本模块仅依赖标准库与 docs 组（markdown-it-py / github-slugger），
保证「只装 docs 组」的独立 hook 环境可运行（检视 §6.4 依赖/测试负例、AC15）。

错误处理：本模块为纯静态解析，外部 IO 仅文件读取（load_document_view）。按项目
错误处理标准，文件读取属直接传播边界，异常（FileNotFoundError / UnicodeDecodeError
等）原样向上传播，由调用方（check_docs_consistency.py）负责 classify_error 分级。

解析配置（检视 §5.1）：CommonMark + table/strikethrough；允许读取原文 HTML token
（raw_html_tokens 供原验证者核验 fragment）；不启用 linkify / typographer / 宏 /
远程 include，不新增插件栈。未知或解析器无法语义化的结构不静默采样：要么原样保留
在 raw_html_tokens（「未核」通道），要么异常显式传播（显式失败）。

公共 API（预留供 docs_generated.py / check_docs_consistency.py 复用）：
    parse_text(text) -> DocumentView
    load_document_view(path) -> DocumentView
    create_slugger() -> Slugger        # GitHub 锚点语义
    SLUGGER_BACKEND                    # 当前锚点实现后端声明
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

from markdown_it import MarkdownIt
from markdown_it.token import Token

__all__ = [
    "SLUGGER_BACKEND",
    "CodeBlockInfo",
    "CodeSpanInfo",
    "CompatGithubSlugger",
    "DocumentView",
    "HeadingInfo",
    "HtmlHrefInfo",
    "HtmlIdInfo",
    "LinkInfo",
    "ReferenceDefInfo",
    "Slugger",
    "UnresolvedRef",
    "create_slugger",
    "load_document_view",
    "parse_text",
]

# 解析模式固定：CommonMark + table/strikethrough（检视 §5.1），模块级单例，
# parse 无共享可变状态（env 由每次调用新建），解析结果不做跨调用缓存。
_PARSER = MarkdownIt("commonmark").enable(["table", "strikethrough"])

# 引用式链接残余形态（仅作用于解析器判定未成链的 text token 内）：
# full 形态 `[text][label]` 与 collapsed 形态 `[text][]`，兼容图片引用 `![alt][label]`。
_REFERENCE_RE = re.compile(r"!?\[([^\[\]]*)\]\[([^\[\]]*)\]")


# ---------------------------------------------------------------------------
# 锚点生成（github-slugger 语义）
# ---------------------------------------------------------------------------


class Slugger(Protocol):
    """GitHub 风格锚点生成器协议（结构化兼容 github_slugger.GithubSlugger）。"""

    def slug(self, value: str, maintain_case: bool = False) -> str:
        """生成唯一 slug；重复标题自动追加序号。"""
        raise NotImplementedError  # Protocol 结构化声明，运行期由具体后端实现


try:  # 权威来源 pin github-slugger==0.0.3：可用则优先（未来修复版自动切回）
    from github_slugger import GithubSlugger as _GithubSlugger  # type: ignore[reportMissingImports]  # 包在 Python 3.13 环境导入即崩（见 CompatGithubSlugger docstring），未安装时经 except 回退 compat 实现，pyright 环境未必安装该包

    SLUGGER_BACKEND = "github-slugger"
except (ImportError, UnicodeEncodeError):
    _GithubSlugger = None
    SLUGGER_BACKEND = "compat"


# github-slugger==0.0.3 的字符分类表：``regex.py`` 的 ``single_byte`` /
# ``multi_byte`` 原样复制，仅作 CompatGithubSlugger 的固定上游数据正本。
# 上游为 ISC 许可，随附许可声明：
# Copyright (c) 2015, Dan Flettre <fletd01@yahoo.com>
# Permission to use, copy, modify, and/or distribute this software for any purpose
# with or without fee is hereby granted, provided that the above copyright notice
# and this permission notice appear in all copies.
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
# OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
# TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
# THIS SOFTWARE.
# 表内独立代理是上游源码的一部分，只能放在普通字符串常量中——写入 docstring 会让
# CPython 3.13+ 在编译期编码 pyc 时抛 UnicodeEncodeError（即上游包不可导入的根因）。
_UPSTREAM_SINGLE_BYTE = "\x00-\x1f!-,./:-@[-^`{-©«-´¶-¹»-¿×÷˂-˅˒-˟˥-˫˭˯-˿͵\u0378\u0379;\u0380-΅·\u038b\u038d\u03a2϶҂\u0530\u0557\u0558՚-՟։-\u0590־׀׃׆\u05c8-\u05cf\u05eb-\u05ee׳-؏؛-؟٪-٭۔\u06dd۞۩۽۾܀-\u070f\u074b\u074c\u07b2-\u07bf߶-߹\u07fb\u07fc߾߿\u082e-\u083f\u085c-\u085f\u086b-࢟ࢵࣈ-࣒\u08e2।॥॰\u0984\u098d\u098e\u0991\u0992\u09a9\u09b1\u09b3-\u09b5\u09ba\u09bb\u09c5\u09c6\u09c9\u09ca\u09cf-\u09d6\u09d8-\u09db\u09de\u09e4\u09e5৲-৻৽\u09ff\u0a00\u0a04\u0a0b-\u0a0e\u0a11\u0a12\u0a29\u0a31\u0a34\u0a37\u0a3a\u0a3b\u0a3d\u0a43-\u0a46\u0a49\u0a4a\u0a4e-\u0a50\u0a52-\u0a58\u0a5d\u0a5f-\u0a65੶-\u0a80\u0a84\u0a8e\u0a92\u0aa9\u0ab1\u0ab4\u0aba\u0abb\u0ac6\u0aca\u0ace\u0acf\u0ad1-\u0adf\u0ae4\u0ae5૰-\u0af8\u0b00\u0b04\u0b0d\u0b0e\u0b11\u0b12\u0b29\u0b31\u0b34\u0b3a\u0b3b\u0b45\u0b46\u0b49\u0b4a\u0b4e-\u0b54\u0b58-\u0b5b\u0b5e\u0b64\u0b65୰୲-\u0b81\u0b84\u0b8b-\u0b8d\u0b91\u0b96-\u0b98\u0b9b\u0b9d\u0ba0-\u0ba2\u0ba5-\u0ba7\u0bab-\u0bad\u0bba-\u0bbd\u0bc3-\u0bc5\u0bc9\u0bce\u0bcf\u0bd1-\u0bd6\u0bd8-\u0be5௰-\u0bff\u0c0d\u0c11\u0c29\u0c3a-఼\u0c45\u0c49\u0c4e-\u0c54\u0c57\u0c5b-\u0c5f\u0c64\u0c65\u0c70-౿಄\u0c8d\u0c91\u0ca9\u0cb4\u0cba\u0cbb\u0cc5\u0cc9\u0cce-\u0cd4\u0cd7-ೝ\u0cdf\u0ce4\u0ce5\u0cf0ೳ-\u0cff\u0d0d\u0d11\u0d45\u0d49൏-\u0d53൘-൞\u0d64\u0d65൰-൹\u0d80\u0d84\u0d97-\u0d99\u0db2\u0dbc\u0dbe\u0dbf\u0dc7-\u0dc9\u0dcb-\u0dce\u0dd5\u0dd7\u0de0-\u0de5\u0df0\u0df1෴-\u0e00\u0e3b-฿๏๚-\u0e80\u0e83\u0e85\u0e8b\u0ea4\u0ea6\u0ebe\u0ebf\u0ec5\u0ec7໎\u0ecf\u0eda\u0edb\u0ee0-\u0eff༁-༗༚-༟༪-༴༶༸༺-༽\u0f48\u0f6d-\u0f70྅\u0f98\u0fbd-࿅࿇-\u0fff၊-၏႞႟\u10c6\u10c8-\u10cc\u10ce\u10cf჻\u1249\u124e\u124f\u1257\u1259\u125e\u125f\u1289\u128e\u128f\u12b1\u12b6\u12b7\u12bf\u12c1\u12c6\u12c7\u12d7\u1311\u1316\u1317\u135b\u135c፠-\u137f᎐-\u139f\u13f6\u13f7\u13fe-᐀᙭᙮\u1680᚛-\u169f᛫-᛭\u16f9-\u16ffᜍ᜕-ᜟ᜵-\u173f\u1754-\u175f\u176d\u1771\u1774-\u177f។-៖៘-៛\u17de\u17df\u17ea-᠊\u180e᠏\u181a-\u181f\u1879-\u187f\u18ab-\u18af\u18f6-\u18ff\u191f\u192c-\u192f\u193c-᥅\u196e\u196f\u1975-\u197f\u19ac-\u19af\u19ca-\u19cf᧚-᧿\u1a1c-᨟\u1a5f\u1a7d\u1a7e\u1a8a-\u1a8f\u1a9a-᪦᪨-\u1aaf᫁-\u1affᭌ-᭏᭚-᭪᭴-᭿\u1bf4-᯿\u1c38-᰿\u1c4a-\u1c4c᱾᱿Ᲊ-\u1c8f\u1cbb\u1cbc᳀-\u1ccf᳓\u1cfb-\u1cff᷺\u1f16\u1f17\u1f1e\u1f1f\u1f46\u1f47\u1f4e\u1f4f\u1f58\u1f5a\u1f5c\u1f5e\u1f7e\u1f7f\u1fb5᾽᾿-῁\u1fc5῍-῏\u1fd4\u1fd5\u1fdc-῟῭-\u1ff1\u1ff5´-‾⁁-⁓⁕-⁰\u2072-⁾₀-\u208f\u209d-\u20cf\u20f1-℁℃-℆℈℉℔№-℘℞-℣℥℧℩℮℺℻⅀-⅄⅊-⅍⅏-⅟↉-⒵⓪-⯿Ⱟⱟ⳥-⳪\u2cf4-⳿\u2d26\u2d28-\u2d2c\u2d2e\u2d2f\u2d68-\u2d6e⵰-\u2d7e\u2d97-\u2d9f\u2da7\u2daf\u2db7\u2dbf\u2dc7\u2dcf\u2dd7\u2ddf⸀-⸮⸰-〄〈-〠〰〶〷〽-\u3040\u3097\u3098゛゜゠・\u3100-\u3104\u3130\u318f-㆟㇀-㇯㈀-㏿䷀-䷿鿽-鿿\ua48d-\ua4cf꓾꓿꘍-꘏\ua62c-\ua63f꙳꙾꛲-꜖꜠꜡꞉꞊ꟀꟁꟋ-ꟴ꠨-꠫\ua82d-\ua83f꡴-\ua87f\ua8c6-꣏\ua8da-\ua8df꣸-꣺꣼꤮꤯\ua954-꥟\ua97d-\ua97f꧁-\ua9ce\ua9da-꧟\ua9ff\uaa37-\uaa3f\uaa4e\uaa4f\uaa5a-꩟꩷-꩹\uaac3-\uaada꫞꫟꫰꫱\uaaf7-\uab00\uab07\uab08\uab0f\uab10\uab17-\uab1f\uab27\uab2f꭛꭪-\uab6f꯫\uabee\uabef\uabfa-\uabff\ud7a4-\ud7af\ud7c7-\ud7ca\ud7fc-\ud7ff\ue000-\uf8ff\ufa6e\ufa6f\ufada-\ufaff\ufb07-\ufb12\ufb18-\ufb1c﬩\ufb37\ufb3d\ufb3f\ufb42\ufb45﮲-\ufbd2﴾-﵏\ufd90\ufd91\ufdc8-\ufdef﷼-﷿︐-\ufe1f︰-︲︵-﹌﹐-\ufe6f\ufe75\ufefd-／：-＠［-＾｀｛-･\uffbf-\uffc1\uffc8\uffc9\uffd0\uffd1\uffd8\uffd9\uffdd-\uffff"
_UPSTREAM_MULTI_BYTE: tuple[tuple[str, str], ...] = (
    (
        "\ud800",
        "\udc0c\udc27\udc3b\udc3e\udc4e\udc4f\udc5e-\udc7f\udcfb-\udd3f\udd75-\uddfc\uddfe-\ude7f\ude9d-\ude9f\uded1-\udedf\udee1-\udeff\udf20-\udf2c\udf4b-\udf4f\udf7b-\udf7f\udf9e\udf9f\udfc4-\udfc7\udfd0\udfd6-\udfff",
    ),
    (
        "\ud801",
        "\udc9e\udc9f\udcaa-\udcaf\udcd4-\udcd7\udcfc-\udcff\udd28-\udd2f\udd64-\uddff\udf37-\udf3f\udf56-\udf5f\udf68-\udfff",
    ),
    (
        "\ud802",
        "\udc06\udc07\udc09\udc36\udc39-\udc3b\udc3d\udc3e\udc56-\udc5f\udc77-\udc7f\udc9f-\udcdf\udcf3\udcf6-\udcff\udd16-\udd1f\udd3a-\udd7f\uddb8-\uddbd\uddc0-\uddff\ude04\ude07-\ude0b\ude14\ude18\ude36\ude37\ude3b-\ude3e\ude40-\ude5f\ude7d-\ude7f\ude9d-\udebf\udec8\udee7-\udeff\udf36-\udf3f\udf56-\udf5f\udf73-\udf7f\udf92-\udfff",
    ),
    (
        "\ud803",
        "\udc49-\udc7f\udcb3-\udcbf\udcf3-\udcff\udd28-\udd2f\udd3a-\ude7f\udeaa\udead-\udeaf\udeb2-\udeff\udf1d-\udf26\udf28-\udf2f\udf51-\udfaf\udfc5-\udfdf\udff7-\udfff",
    ),
    (
        "\ud804",
        "\udc47-\udc65\udc70-\udc7e\udcbb-\udccf\udce9-\udcef\udcfa-\udcff\udd35\udd40-\udd43\udd48-\udd4f\udd74\udd75\udd77-\udd7f\uddc5-\uddc8\uddcd\udddb\udddd-\uddff\ude12\ude38-\ude3d\ude3f-\ude7f\ude87\ude89\ude8e\ude9e\udea9-\udeaf\udeeb-\udeef\udefa-\udeff\udf04\udf0d\udf0e\udf11\udf12\udf29\udf31\udf34\udf3a\udf45\udf46\udf49\udf4a\udf4e\udf4f\udf51-\udf56\udf58-\udf5c\udf64\udf65\udf6d-\udf6f\udf75-\udfff",
    ),
    (
        "\ud805",
        "\udc4b-\udc4f\udc5a-\udc5d\udc62-\udc7f\udcc6\udcc8-\udccf\udcda-\udd7f\uddb6\uddb7\uddc1-\uddd7\uddde-\uddff\ude41-\ude43\ude45-\ude4f\ude5a-\ude7f\udeb9-\udebf\udeca-\udeff\udf1b\udf1c\udf2c-\udf2f\udf3a-\udfff",
    ),
    (
        "\ud806",
        "\udc3b-\udc9f\udcea-\udcfe\udd07\udd08\udd0a\udd0b\udd14\udd17\udd36\udd39\udd3a\udd44-\udd4f\udd5a-\udd9f\udda8\udda9\uddd8\uddd9\udde2\udde5-\uddff\ude3f-\ude46\ude48-\ude4f\ude9a-\ude9c\ude9e-\udebf\udef9-\udfff",
    ),
    (
        "\ud807",
        "\udc09\udc37\udc41-\udc4f\udc5a-\udc71\udc90\udc91\udca8\udcb7-\udcff\udd07\udd0a\udd37-\udd39\udd3b\udd3e\udd48-\udd4f\udd5a-\udd5f\udd66\udd69\udd8f\udd92\udd99-\udd9f\uddaa-\udedf\udef7-\udfaf\udfb1-\udfff",
    ),
    ("\ud808", "\udf9a-\udfff"),
    ("\ud809", "\udc6f-\udc7f\udd44-\udfff"),
    (
        "\ud80a\ud80b\ud80e-\ud810\ud812-\ud819\ud824-\ud82b\ud82d\ud82e\ud830-\ud833\ud837\ud839\ud83d\ud83f\ud87b-\ud87d\ud87f\ud885-\udb3f\udb41-\udbff",
        "\udc00-\udfff",
    ),
    ("\ud80d", "\udc2f-\udfff"),
    ("\ud811", "\ude47-\udfff"),
    (
        "\ud81a",
        "\ude39-\ude3f\ude5f\ude6a-\udecf\udeee\udeef\udef5-\udeff\udf37-\udf3f\udf44-\udf4f\udf5a-\udf62\udf78-\udf7c\udf90-\udfff",
    ),
    ("\ud81b", "\udc00-\ude3f\ude80-\udeff\udf4b-\udf4e\udf88-\udf8e\udfa0-\udfdf\udfe2\udfe5-\udfef\udff2-\udfff"),
    ("\ud821", "\udff8-\udfff"),
    ("\ud823", "\udcd6-\udcff\udd09-\udfff"),
    ("\ud82c", "\udd1f-\udd4f\udd53-\udd63\udd68-\udd6f\udefc-\udfff"),
    ("\ud82f", "\udc6b-\udc6f\udc7d-\udc7f\udc89-\udc8f\udc9a-\udc9c\udc9f-\udfff"),
    ("\ud834", "\udc00-\udd64\udd6a-\udd6c\udd73-\udd7a\udd83\udd84\udd8c-\udda9\uddae-\ude41\ude45-\udfff"),
    (
        "\ud835",
        "\udc55\udc9d\udca0\udca1\udca3\udca4\udca7\udca8\udcad\udcba\udcbc\udcc4\udd06\udd0b\udd0c\udd15\udd1d\udd3a\udd3f\udd45\udd47-\udd49\udd51\udea6\udea7\udec1\udedb\udefb\udf15\udf35\udf4f\udf6f\udf89\udfa9\udfc3\udfcc\udfcd",
    ),
    ("\ud836", "\udc00-\uddff\ude37-\ude3a\ude6d-\ude74\ude76-\ude83\ude85-\ude9a\udea0\udeb0-\udfff"),
    (
        "\ud838",
        "\udc07\udc19\udc1a\udc22\udc25\udc2b-\udcff\udd2d-\udd2f\udd3e\udd3f\udd4a-\udd4d\udd4f-\udebf\udefa-\udfff",
    ),
    ("\ud83a", "\udcc5-\udccf\udcd7-\udcff\udd4c-\udd4f\udd5a-\udfff"),
    (
        "\ud83b",
        "\udc00-\uddff\ude04\ude20\ude23\ude25\ude26\ude28\ude33\ude38\ude3a\ude3c-\ude41\ude43-\ude46\ude48\ude4a\ude4c\ude50\ude53\ude55\ude56\ude58\ude5a\ude5c\ude5e\ude60\ude63\ude65\ude66\ude6b\ude73\ude78\ude7d\ude7f\ude8a\ude9c-\udea0\udea4\udeaa\udebc-\udfff",
    ),
    ("\ud83c", "\udc00-\udd2f\udd4a-\udd4f\udd6a-\udd6f\udd8a-\udfff"),
    ("\ud83e", "\udc00-\udfef\udffa-\udfff"),
    ("\ud869", "\udede-\udeff"),
    ("\ud86d", "\udf35-\udf3f"),
    ("\ud86e", "\udc1e\udc1f"),
    ("\ud873", "\udea2-\udeaf"),
    ("\ud87a", "\udfe1-\udfff"),
    ("\ud87e", "\ude1e-\udfff"),
    ("\ud884", "\udf4b-\udfff"),
    ("\udb40", "\udc00-\udcff\uddf0-\udfff"),
)


def _parse_char_spec(spec: str) -> tuple[tuple[int, int], ...]:
    """把上游字符类规格（单字符 / ``a-b`` 区间串）解析为闭区间码点列表。

    语义与上游 ``replace.range()`` 一致：``ab`` 表示两个单字符，``a-b`` 表示区间；
    规格串由固定表提供，不含歧义输入。
    """
    ranges: list[tuple[int, int]] = []
    pos = 0
    length = len(spec)
    while pos < length:
        if pos + 2 < length and spec[pos + 1] == "-":
            ranges.append((ord(spec[pos]), ord(spec[pos + 2])))
            pos += 3
        else:
            code = ord(spec[pos])
            ranges.append((code, code))
            pos += 1
    return tuple(ranges)


_SINGLE_BYTE_RANGES = _parse_char_spec(_UPSTREAM_SINGLE_BYTE)
_SINGLE_BYTE_REMOVED = frozenset(unit for lower, upper in _SINGLE_BYTE_RANGES for unit in range(lower, upper + 1))
_MULTI_BYTE_RANGES: tuple[tuple[frozenset[int], tuple[tuple[int, int], ...]], ...] = tuple(
    (
        frozenset(unit for lower, upper in _parse_char_spec(high) for unit in range(lower, upper + 1)),
        _parse_char_spec(low),
    )
    for high, low in _UPSTREAM_MULTI_BYTE
)


def _multi_second_ranges(unit: int) -> tuple[tuple[int, int], ...] | None:
    """上游 ``multi_match``：返回首个命中首代理/码元对应的次码元区间，否则 None。"""
    for first_units, second_ranges in _MULTI_BYTE_RANGES:
        if unit in first_units:
            return second_ranges
    return None


def _replace(value: str) -> str:
    """逐 UTF-16 码元复刻 github-slugger==0.0.3 的 ``replace.replace()``。

    该实现按 UTF-16 码元（而非 Python 码点）处理：ASCII 空格（U+0020）转 ``-``；
    命中 ``single_byte`` 的码元删除；命中 ``multi_byte`` 的代理对整体删除；其余保留。
    刻意保留上游全部行为（含不 trim 首尾空白、仅空格转连字符、组合附加符号保留），
    不做「更合理」的改写，否则不再与锁定版本等价。
    """
    data = value.encode("utf-16-le")
    out: list[int] = []
    pos = 0
    length = len(data)
    while pos < length:
        unit = data[pos] | (data[pos + 1] << 8)
        pos += 2
        if unit == 0x20:
            out.extend((0x2D, 0))
            continue
        if unit in _SINGLE_BYTE_REMOVED:
            continue
        second_ranges = _multi_second_ranges(unit)
        if second_ranges is not None:
            if pos >= length:
                break
            following = data[pos] | (data[pos + 1] << 8)
            if any(lower <= following <= upper for lower, upper in second_ranges):
                pos += 2
                continue
        out.extend((unit & 0xFF, unit >> 8))
    return bytes(out).decode("utf-16-le")


class CompatGithubSlugger:
    """github-slugger==0.0.3 的等价实现（该包在 Python 3.13+ 导入即崩时回退用）。

    背景：github-slugger==0.0.3 的 ``replace.py`` docstring 含孤立代理转义（U+D80A），
    CPython 3.13+ 编译 docstring 写 pyc 时抛 UnicodeEncodeError，包在 requires-python>=3.13
    环境导入即崩（PyPI 上 0.0.1-0.0.3 无修复版本）。本实现按上游 ``slug.py`` /
    ``replace.py`` / ``regex.py`` 语义等价移植，字符分类表原样固定，不静默冒充包实现：
    ``SLUGGER_BACKEND`` 声明实际生效后端，``upstream_version`` 记录被复刻的上游版本。

    与上游一致的关键行为（常见错误假设的反例）：首尾空格不 trim（生成首尾 ``-``）；
    仅 ASCII 空格转 ``-``，Tab/换行/控制字符删除；组合附加符号（Mn，含 VS16）保留；
    较新 Unicode 字母若落入上游删除区间也随之删除（以上游固定表为准，不按当前
    Unicode 类别重算）；重复 slug 追加 ``-1``、``-2`` 序号。
    """

    upstream_version = "0.0.3"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """遗忘全部历史 slug（与 github_slugger.GithubSlugger.reset 对齐）。"""
        self._occurrences: dict[str, int] = {}

    def slug(self, value: str, maintain_case: bool = False) -> str:
        """生成唯一 slug；重复输入自动追加序号（与 GithubSlugger.slug 行为一致）。"""
        if type(value) is not str:
            return ""
        if not maintain_case:
            value = value.lower()
        original = _replace(value)
        result = original
        count = self._occurrences.get(original, 0)
        while result in self._occurrences:
            count += 1
            result = f"{original}-{count}"
        self._occurrences[original] = count
        self._occurrences[result] = 0
        return result


def create_slugger() -> Slugger:
    """构造锚点生成器：github-slugger 可用则用包实现，否则回退等价实现。"""
    if _GithubSlugger is not None:
        return _GithubSlugger()
    return CompatGithubSlugger()


# ---------------------------------------------------------------------------
# 不可变视图数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadingInfo:
    """标题：可见文本、锚点 slug、级别（1-6）、起始行与标题块末行（1-based）。

    ``line`` 为标题可见文本起始行；``end_line`` 为标题块最后一行（ATX 标题与
    文本同行，Setext 标题含下划线行）。消费者据此切分章节正文，避免行级正则
    再次解析 Markdown 标题。
    """

    text: str
    slug: str
    level: int
    line: int
    end_line: int = 0


@dataclass(frozen=True)
class LinkInfo:
    """已解析链接：可见文本、目标、形态、所在行（1-based）。

    kind 为 "inline"（标准式 ``[t](u)``）或 "reference"（引用式 ``[t][label]``），
    由两轮 parse 对照法精确判定（见 parse_text），无行级正则启发式。
    """

    text: str
    target: str
    kind: str
    line: int


@dataclass(frozen=True)
class UnresolvedRef:
    """引用式死链：未定义引用的 label / 可见文本 / 所在行 / 形态。"""

    label: str
    text: str
    line: int
    form: str  # "full" 或 "collapsed"


@dataclass(frozen=True)
class HtmlIdInfo:
    """HTML 元素 id：标签名、来源 token 形态、所在行（1-based）。

    与标题锚点分开存储（可区分）；anchor_ids() 提供二者并集。
    """

    id: str
    tag: str
    source: str  # "inline" 或 "block"
    line: int


@dataclass(frozen=True)
class HtmlHrefInfo:
    """HTML 元素 href：目标、标签名、来源 token 形态、所在行（1-based）。

    HTML href 由标准库 HTMLParser 静态提取（只解析、不执行、不联网），与 Markdown
    链接分开存储，由消费者纳入统一本地 URL 死链检查；代码块/行内代码中的 HTML
    不是 html token，天然不进入本视图。块级 HTML 按标签实际行定位，行内 HTML 与
    Markdown 链接一致取所在段落起始行（inline token 不携带子 token 偏移）。
    """

    href: str
    tag: str
    source: str  # "inline" 或 "block"
    line: int


@dataclass(frozen=True)
class CodeBlockInfo:
    """代码块：围栏记号（``` / ~~~，缩进块为空串）、info 串、1-based 闭区间行号。"""

    fence: str
    info: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ReferenceDefInfo:
    """引用定义：normalize 后 label（大写折叠）、目标 URL、所在行（1-based）。"""

    label: str
    href: str
    line: int


@dataclass(frozen=True)
class CodeSpanInfo:
    """行内代码 span：原文文本、所在行（1-based）。

    行内代码内的假链接/伪标题由本视图承载（token 判定 code_inline，天然排除
    链接与标题语义），供消费者做「代码示例文本不冒正文」的负例判定。
    """

    text: str
    line: int


@dataclass(frozen=True)
class DocumentView:
    """一次解析形成的不可变文档视图（frozen dataclass，全部字段为 tuple）。"""

    source: tuple[str, ...]
    headings: tuple[HeadingInfo, ...]
    links: tuple[LinkInfo, ...]
    unresolved_references: tuple[UnresolvedRef, ...]
    html_ids: tuple[HtmlIdInfo, ...]
    code_blocks: tuple[CodeBlockInfo, ...]
    code_spans: tuple[CodeSpanInfo, ...]
    reference_definitions: tuple[ReferenceDefInfo, ...]
    raw_html_tokens: tuple[tuple[str, int], ...]  # (原 HTML 内容, 所在行)
    html_hrefs: tuple[HtmlHrefInfo, ...]

    def anchor_ids(self) -> frozenset[str]:
        """全部可用锚点 = 标题 slug ∪ HTML id。"""
        return frozenset({h.slug for h in self.headings}) | frozenset(i.id for i in self.html_ids)

    def has_anchor(self, anchor: str) -> bool:
        """锚点存在性显式判定：不存在返回 False（不伪造、不从全文取样代替）。"""
        return anchor in self.anchor_ids()

    def code_line_numbers(self) -> frozenset[int]:
        """代码块覆盖的全部行号（1-based，含围栏标记行与内容行，不含行内代码）。

        供行级消费者（日志表/完成判定正文）排除代码示例，避免围栏内伪标题、
        伪表格冒认正文结构。
        """
        lines: set[int] = set()
        for block in self.code_blocks:
            lines.update(range(block.start_line, block.end_line + 1))
        return frozenset(lines)

    def code_block_text(self, block: CodeBlockInfo) -> str:
        """代码块内容文本（不含围栏标记行；缩进块原样保留内容行）。"""
        if block.fence:
            content = self.source[block.start_line : block.end_line - 1]
        else:
            content = self.source[block.start_line - 1 : block.end_line]
        return "\n".join(content)


# ---------------------------------------------------------------------------
# 解析实现
# ---------------------------------------------------------------------------


class _HtmlAttrParser(HTMLParser):
    """标准库 HTMLParser：静态收集标签 id / href 属性（不执行任何内嵌 HTML 语义）。

    ``getpos()`` 返回相对本次 feed 文本的行号（1-based），调用方以 base_line 换算
    为文档绝对行号，使多行 html_block 能逐标签定位而非统一记块起始行。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[tuple[str, str, int]] = []
        self.hrefs: list[tuple[str, str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rel_line = self.getpos()[0]
        for key, value in attrs:
            if not value:
                continue
            lowered = key.lower()
            if lowered == "id":
                self.ids.append((tag, value, rel_line))
            elif lowered == "href":
                self.hrefs.append((tag, value, rel_line))


def _normalize_label(label: str) -> str:
    """CommonMark 引用 label normalize：折叠空白 + 大小写折叠（对齐 env 键）。"""
    return re.sub(r"\s+", " ", label).strip().upper()


def _token_line(token: object) -> int:
    """token.map 为 [start, end) 0-based 行区间，返回 1-based 起始行。"""
    mapping = getattr(token, "map", None)
    if not mapping:
        return 0
    return mapping[0] + 1


def _iter_links(tokens: list[Token]) -> list[tuple[int, str, str]]:
    """按出现顺序收集已解析链接 (line, text, href)；image 独立 token 不计入链接。"""
    links: list[tuple[int, str, str]] = []
    for token in tokens:
        if token.type != "inline":
            continue
        children = token.children or []
        index = 0
        while index < len(children):
            child = children[index]
            if child.type == "link_open":
                attrs = dict(child.attrs) if child.attrs else {}
                href = str(attrs.get("href", ""))
                parts: list[str] = []
                cursor = index + 1
                while cursor < len(children) and children[cursor].type != "link_close":
                    if children[cursor].type in ("text", "code_inline"):
                        parts.append(children[cursor].content)
                    cursor += 1
                links.append((_token_line(token), "".join(parts), href))
                index = cursor + 1
            else:
                index += 1
    return links


def _collect_headings(tokens: list[Token], slugger: Slugger) -> tuple[HeadingInfo, ...]:
    """标题：可见文本取 children 中 text/code_inline（跳过 HTML 等内联标记）。"""
    headings: list[HeadingInfo] = []
    pending_level: int | None = None
    pending_end = 0
    for token in tokens:
        if token.type == "heading_open" and token.tag.startswith("h"):
            pending_level = int(token.tag[1])
            pending_end = token.map[1] if token.map else 0
        elif token.type == "inline" and pending_level is not None:
            parts = [child.content for child in (token.children or []) if child.type in ("text", "code_inline")]
            text = "".join(parts)
            headings.append(
                HeadingInfo(
                    text=text,
                    slug=slugger.slug(text),
                    level=pending_level,
                    line=_token_line(token),
                    end_line=pending_end,
                )
            )
            pending_level = None
    return tuple(headings)


def _parse_html_attrs(content: str, source: str, base_line: int) -> tuple[list[HtmlIdInfo], list[HtmlHrefInfo]]:
    parser = _HtmlAttrParser()
    parser.feed(content)
    parser.close()
    ids = [
        HtmlIdInfo(id=value, tag=tag, source=source, line=base_line + rel_line - 1)
        for tag, value, rel_line in parser.ids
    ]
    hrefs = [
        HtmlHrefInfo(href=value, tag=tag, source=source, line=base_line + rel_line - 1)
        for tag, value, rel_line in parser.hrefs
    ]
    return ids, hrefs


def _collect_html(
    tokens: list[Token],
) -> tuple[list[HtmlIdInfo], list[HtmlHrefInfo], list[tuple[str, int]]]:
    """从 html_block / html_inline token 原文提取 id 与 href，并保留未核 HTML 原样。

    html_inline 位于 inline token 的 children 中，须下钻一层收集；
    html_block 在顶层 token（注意 CommonMark type 6 块仅由空行结束）。
    围栏/缩进代码块与行内代码不是 html token，其中的 HTML 文本不会进入本路径。
    """
    ids: list[HtmlIdInfo] = []
    hrefs: list[HtmlHrefInfo] = []
    raw: list[tuple[str, int]] = []
    for token in tokens:
        if token.type == "html_block":
            line = _token_line(token)
            raw.append((token.content, line))
            block_ids, block_hrefs = _parse_html_attrs(token.content, "block", line)
            ids.extend(block_ids)
            hrefs.extend(block_hrefs)
        elif token.type == "inline":
            line = _token_line(token)
            for child in token.children or []:
                if child.type == "html_inline":
                    raw.append((child.content, line))
                    inline_ids, inline_hrefs = _parse_html_attrs(child.content, "inline", line)
                    ids.extend(inline_ids)
                    hrefs.extend(inline_hrefs)
    return ids, hrefs, raw


def _collect_code_spans(tokens: list[Token]) -> tuple[CodeSpanInfo, ...]:
    """行内代码 span（code_inline）：文本与所在行（1-based，段落起始行粒度）。"""
    spans: list[CodeSpanInfo] = []
    for token in tokens:
        if token.type != "inline":
            continue
        line = _token_line(token)
        for child in token.children or []:
            if child.type == "code_inline":
                spans.append(CodeSpanInfo(text=child.content, line=line))
    return tuple(spans)


def _collect_code_blocks(tokens: list[Token]) -> tuple[CodeBlockInfo, ...]:
    """代码块：围栏（fence，含 markup ```/~~~）与缩进块（code_block）。"""
    blocks: list[CodeBlockInfo] = []
    for token in tokens:
        if token.type == "fence" and token.map:
            mapping = token.map
            blocks.append(
                CodeBlockInfo(
                    fence=token.markup,
                    info=token.info.strip(),
                    start_line=mapping[0] + 1,
                    end_line=mapping[1],
                )
            )
        elif token.type == "code_block" and token.map:
            mapping = token.map
            blocks.append(
                CodeBlockInfo(
                    fence="",
                    info="",
                    start_line=mapping[0] + 1,
                    end_line=mapping[1],
                )
            )
    return tuple(blocks)


def _strip_definition_lines(source_lines: tuple[str, ...], env: dict) -> tuple[str, tuple[int, ...]]:
    """按 env.references 的 map 行区间删除引用定义行，返回（新文本, 保留原行号）。"""
    removed: set[int] = set()
    for definition in env.get("references", {}).values():
        span = definition.get("map")
        if span:
            removed.update(range(span[0], span[1]))
    kept: list[int] = []
    lines: list[str] = []
    for index, line in enumerate(source_lines):
        if index not in removed:
            kept.append(index + 1)
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else ""), tuple(kept)


# 语法角色占位符：转义方括号 / 反引号与行内代码在「原始源语法流」中以 NUL 表示，
# 保证其不再参与引用形态匹配（NUL 不可能是文档正文的方括号字符）。
_INERT_SYNTAX = "\x00"


def _find_closing_backticks(raw: str, run: str, start: int) -> int | None:
    """定位与 ``run`` 等长的闭合反引号串（排除更长反引号串的一部分）。"""
    index = raw.find(run, start)
    while index != -1:
        after = index + len(run)
        before_ok = index == 0 or raw[index - 1] != "`"
        after_ok = after >= len(raw) or raw[after] != "`"
        if before_ok and after_ok:
            return index
        index = raw.find(run, index + 1)
    return None


def _syntax_stream(raw: str) -> str:
    """按原始 inline 源（未反转义）重建引用语法判定流。

    text token 的 ``content`` 已丢失转义来源（``\\[ok\\]`` 与 ``[ok]`` 同形），
    因此不能从显示文本倒猜语法。这里只做词法级还原：反斜杠转义按其字面字符
    还原，但转义方括号 / 反引号与行内代码以 ``_INERT_SYNTAX`` 占位，使其不再
    构成引用形态；不做第二次 Markdown 解析。
    """
    out: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == "\\" and index + 1 < length:
            escaped = raw[index + 1]
            out.append(_INERT_SYNTAX if escaped in "[]`\\" else escaped)
            index += 2
            continue
        if char == "`":
            run_end = index
            while run_end < length and raw[run_end] == "`":
                run_end += 1
            run = raw[index:run_end]
            close = _find_closing_backticks(raw, run, run_end)
            if close is not None:
                out.append(_INERT_SYNTAX)
                index = close + len(run)
                continue
            out.append(run)
            index = run_end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _source_reference_forms(raw: str) -> Counter[tuple[str, str]]:
    """原始 inline 源中真实语法角色的 full/collapsed 引用形态计数（含 text/label 原文）。"""
    forms: Counter[tuple[str, str]] = Counter()
    for match in _REFERENCE_RE.finditer(_syntax_stream(raw)):
        forms[(match.group(1), match.group(2))] += 1
    return forms


def _collect_unresolved(tokens: list[Token], defined_labels: set[str]) -> tuple[UnresolvedRef, ...]:
    """引用式死链：解析器判定未成链的 text token 中 full/collapsed 引用形态。

    shortcut 形态 ``[label]`` 不检（CommonMark 语义为纯文本，检之会对普通方括号
    文本误报）。行内代码/围栏内的假链接天然不会进入本路径（markdown-it 已将其
    判定为 code_inline / fence，见检视 §6.4 不误报负例）。

    D30：显示文本已反转义，无法区分 ``\\[ok\\]\\[undefined\\]``（转义文字）与真实
    未定义引用。因此候选仅在「原始 inline 源语法流」中同形出现时才成立：
    转义方括号、行内代码被占位，不会贡献语法形态；格式化文本（如
    ``[**x**][undefined]``）仍因强调拆成多个 text token 而不产生候选，避免扩大
    正则去拒绝 CommonMark 视为普通文字的输入。
    """
    unresolved: list[UnresolvedRef] = []
    for token in tokens:
        if token.type != "inline":
            continue
        line = _token_line(token)
        source_forms = _source_reference_forms(token.content or "")
        for child in token.children or []:
            if child.type != "text":
                continue
            for match in _REFERENCE_RE.finditer(child.content):
                text_part, label_part = match.group(1), match.group(2)
                form = "collapsed" if label_part == "" else "full"
                label = text_part if form == "collapsed" else label_part
                key = (text_part, label_part)
                if source_forms[key] <= 0:
                    # 显示文本候选来自转义 / 行内代码语境，非真实引用语法
                    continue
                source_forms[key] -= 1
                if _normalize_label(label) not in defined_labels:
                    unresolved.append(UnresolvedRef(label=label, text=text_part, line=line, form=form))
    return tuple(unresolved)


def parse_text(text: str) -> DocumentView:
    """解析 Markdown 文本，形成不可变文档视图（缓存限本次调用）。"""
    source_lines = tuple(text.splitlines())
    env: dict = {}
    tokens = _PARSER.parse(text, env)

    slugger = create_slugger()
    headings = _collect_headings(tokens, slugger)
    html_ids, html_hrefs, raw_html = _collect_html(tokens)
    code_blocks = _collect_code_blocks(tokens)
    code_spans = _collect_code_spans(tokens)
    defined_labels = {
        str(label)
        for label in env.get("references", {})  # 键已为大写折叠形态
    }
    unresolved = _collect_unresolved(tokens, defined_labels)

    definitions = tuple(
        ReferenceDefInfo(
            label=str(label),
            href=str(definition.get("href", "")),
            line=int(definition["map"][0]) + 1,
        )
        for label, definition in sorted(env.get("references", {}).items())
        if definition.get("map")
    )

    # kind 判定（两轮 parse 对照法，无行级正则启发式）：
    # 第一轮收集全部已解析链接；第二轮删除引用定义行后重新 parse ——
    # 依赖定义的引用式链接会退化为 text，而标准式内联链接不变。
    # 二轮仍出现的链接为 inline，消失者为 reference（按多重出现计数对齐）。
    round_one = _iter_links(tokens)
    stripped, kept_lines = _strip_definition_lines(source_lines, env)
    round_two_env: dict = {}
    round_two = _PARSER.parse(stripped, round_two_env)
    round_two_mapped = Counter(
        (kept_lines[line - 1], link_text, link_href)
        for line, link_text, link_href in _iter_links(round_two)
        if 0 < line <= len(kept_lines)
    )
    counts = Counter(round_one)
    links: list[LinkInfo] = []
    for line, link_text, link_href in round_one:
        kind = (
            "inline"
            if counts[(line, link_text, link_href)] <= round_two_mapped[(line, link_text, link_href)]
            else "reference"
        )
        links.append(LinkInfo(text=link_text, target=link_href, kind=kind, line=line))

    return DocumentView(
        source=source_lines,
        headings=headings,
        links=tuple(links),
        unresolved_references=unresolved,
        html_ids=tuple(html_ids),
        code_blocks=code_blocks,
        code_spans=code_spans,
        reference_definitions=definitions,
        raw_html_tokens=tuple(raw_html),
        html_hrefs=tuple(html_hrefs),
    )


def load_document_view(path: str | Path) -> DocumentView:
    """读取文档并解析为不可变视图；文件读取异常按直接传播边界原样上抛。"""
    content = Path(path).read_text(encoding="utf-8")
    return parse_text(content)
