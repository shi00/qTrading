"""core.model_management_types F01 新增契约直接单测。

覆盖「外发目标与执行位置」小节（§4.3，F01 分派授权身份的规范来源）：
- :func:`normalize_egress_target` 规范化与拒绝分支（服务层测试只覆盖经
  dispatch 的正向路径，本文件补齐安全函数的拒绝分支直接验证）；
- :func:`adapter_kind_requires_egress` 执行位置判定。
"""

from typing import get_args, get_type_hints

import pytest

from core.model_management_types import (
    EGRESS_ADAPTER_KINDS,
    LOCAL_ADAPTER_KINDS,
    ResolvedConnection,
    adapter_kind_requires_egress,
    normalize_egress_target,
)

pytestmark = pytest.mark.unit


class TestNormalizeEgressTarget:
    @pytest.mark.parametrize(
        ("api_base", "expected"),
        [
            # 规范化：scheme / host 小写、显式默认端口、尾斜杠 path 归一
            ("https://API.Example.COM", "https://api.example.com:443"),
            ("HTTP://Host.Example.COM:8080/base/", "http://host.example.com:8080/base"),
            ("https://api.example.com:443/", "https://api.example.com:443"),
            # IDNA：非 ASCII host 转 punycode
            ("https://例子.example.com/v1", "https://xn--fsqu00a.example.com:443/v1"),
            # IPv6 字面量（T1）：方括号重组 + 跳过 IDNA（urlsplit hostname 已去括号并小写）
            ("http://[::1]:8080/v1", "http://[::1]:8080/v1"),
            ("https://[2001:DB8::1]", "https://[2001:db8::1]:443"),
            ("http://[::ffff:127.0.0.1]:8080/", "http://[::ffff:127.0.0.1]:8080"),
        ],
    )
    def test_canonical_forms(self, api_base: str, expected: str):
        assert normalize_egress_target(api_base) == expected

    @pytest.mark.parametrize(
        "api_base",
        [
            None,  # 非字符串（缺失）
            123,  # 非字符串（脏数据防御）
            "",  # 空串
            "   ",  # 纯空白
            "ftp://api.example.com",  # 非 http(s) scheme
            "api.example.com",  # 无 scheme
            "https://user@api.example.com",  # userinfo
            "https://@api.example.com",  # userinfo（空用户名，username == ""）
            "https://:@api.example.com",  # userinfo（空用户名 + 空密码）
            "https://user:pass@api.example.com",  # userinfo（含密码）
            "https://api.example.com/?x=1",  # query
            "https://api.example.com/v1#frag",  # fragment
            "https://api.example.com:0",  # 端口下界越界
            "https://api.example.com:70000",  # 端口上界越界
            "https://api.example.com:notaport",  # 端口非法（urlsplit .port ValueError）
            "https://a..example.com",  # 空 label（IDNA UnicodeError）
            "http://[foo]",  # bracketed 非 IP 字面量（urlsplit ValueError）
            "http://[::1]:notaport",  # IPv6 字面量 + 非法端口
        ],
    )
    def test_invalid_forms_return_none(self, api_base):
        """缺失 / 非法目标一律 None，不臆造（R21）。"""
        assert normalize_egress_target(api_base) is None


class TestAdapterKindRequiresEgress:
    @pytest.mark.parametrize(
        ("adapter_kind", "expected"),
        [
            ("litellm_chat", True),  # 已知网络出站
            ("gguf_chat", False),  # 已知设备内本地（显式豁免）
            ("gguf_sentiment", False),  # 已知设备内本地（显式豁免）
            ("unknown_kind", True),  # 未知类型 default-deny：要求 grant（fail-closed）
            ("litellm_chat_v2", True),  # 未知变体同样 default-deny
            ("", True),  # 空串按未知处理
        ],
    )
    def test_kind_table(self, adapter_kind: str, expected: bool):
        assert adapter_kind_requires_egress(adapter_kind) is expected

    def test_kind_lists_disjoint(self):
        """出站清单与本地豁免清单不得重叠（default-deny 三分支判定的前提）。"""
        assert not (set(EGRESS_ADAPTER_KINDS) & set(LOCAL_ADAPTER_KINDS))

    def test_known_kinds_registered(self):
        """Literal 白名单每个 kind 必须登记到出站或本地清单之一（防新增 kind 漏登记）。"""
        hints = get_type_hints(ResolvedConnection)
        for kind in get_args(hints["adapter_kind"]):
            assert kind in EGRESS_ADAPTER_KINDS or kind in LOCAL_ADAPTER_KINDS
