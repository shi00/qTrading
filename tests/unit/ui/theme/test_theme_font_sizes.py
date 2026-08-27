"""AppStyles 字号基准契约守护 (UX-10 P2-07).

P2-07 字号基准提升: CAPTION/BODY_SM/BODY 11/12/13 → 12/13/14, 正文基准 14px,
辅助文字不低于 12px。本测试固化 token 值与语义不变量, 防止:

1. 后续误改 token 值破坏基准 (报告验收: 新字号生效)
2. BODY == LG 同值退阶被误拉开层级 (UX-10 决策: LG 保持 14, 报告仅点名 3 token)
"""

import pytest

from ui.theme import AppStyles

pytestmark = pytest.mark.unit


class TestFontSizesBaseline:
    """UX-10 (P2-07): 字号基准 12/13/14 + 语义不变量。"""

    def test_caption_is_12(self) -> None:
        """CAPTION 11→12: 辅助文字不低于 12px (验收)。"""
        assert AppStyles.FONT_SIZE_CAPTION == 12

    def test_body_sm_is_13(self) -> None:
        """BODY_SM 12→13: 小号正文 (表格/卡片)。"""
        assert AppStyles.FONT_SIZE_BODY_SM == 13

    def test_body_is_14(self) -> None:
        """BODY 13→14: 正文基准提升到 14px (验收)。"""
        assert AppStyles.FONT_SIZE_BODY == 14

    def test_monotonic_order(self) -> None:
        """语义层级不变量: CAPTION < BODY_SM < BODY。"""
        assert AppStyles.FONT_SIZE_CAPTION < AppStyles.FONT_SIZE_BODY_SM < AppStyles.FONT_SIZE_BODY

    def test_body_sm_at_least_12(self) -> None:
        """辅助文字不低于 12px (验收: 小号正文 ≥ 12)。"""
        assert AppStyles.FONT_SIZE_BODY_SM >= 12

    def test_body_equals_lg(self) -> None:
        """UX-10 决策固化: BODY == LG (同值退阶接受, 防未来误拉开层级)。"""
        assert AppStyles.FONT_SIZE_BODY == AppStyles.FONT_SIZE_LG
