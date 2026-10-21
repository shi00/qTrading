"""core.model_management_types F01 新增契约直接单测。

覆盖「外发目标与执行位置」小节（§4.3，F01 分派授权身份的规范来源）：
- :func:`normalize_egress_target` 规范化与拒绝分支（服务层测试只覆盖经
  dispatch 的正向路径，本文件补齐安全函数的拒绝分支直接验证）；
- :func:`adapter_kind_requires_egress` 执行位置判定。
"""

import pytest

from core.model_management_types import adapter_kind_requires_egress, normalize_egress_target

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
            "https://user:pass@api.example.com",  # userinfo（含密码）
            "https://api.example.com/?x=1",  # query
            "https://api.example.com/v1#frag",  # fragment
            "https://api.example.com:0",  # 端口下界越界
            "https://api.example.com:70000",  # 端口上界越界
            "https://api.example.com:notaport",  # 端口非法（urlsplit .port ValueError）
            "https://a..example.com",  # 空 label（IDNA UnicodeError）
        ],
    )
    def test_invalid_forms_return_none(self, api_base):
        """缺失 / 非法目标一律 None，不臆造（R21）。"""
        assert normalize_egress_target(api_base) is None


class TestAdapterKindRequiresEgress:
    @pytest.mark.parametrize(
        ("adapter_kind", "expected"),
        [
            ("litellm_chat", True),  # 网络出站
            ("gguf_chat", False),  # 设备内本地
            ("gguf_sentiment", False),  # 设备内本地
            ("unknown_kind", False),  # 未知类型防御：不要求 grant（executor 侧无适配器先拒）
        ],
    )
    def test_kind_table(self, adapter_kind: str, expected: bool):
        assert adapter_kind_requires_egress(adapter_kind) is expected
