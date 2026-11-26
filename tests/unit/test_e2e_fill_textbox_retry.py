"""``FletPage.fill_textbox`` 回读校验行为单测（CI run 45654373321 回归）。

背景：CanvasKit a11y 下逐键 ``keyboard.type`` 与受控 TextField 重渲染竞争，
导致输入被污染（端口 ``5432`` → ``435432``、用户名重复），向导停在 DB 步骤。
``fill_textbox`` 改为「全选 + 单次原子插入 + 回读校验」，本文件锁定其行为：
1. 回读不一致时重试，直至与实际值一致；
2. 密码类掩码值（``***``）跳过校验；
3. 无法回读（无匹配 input）时跳过校验；
4. 重试耗尽仍不一致时 fail-fast（AssertionError 携带 actual/expected）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.e2e.helpers.flet_page import FletPage

pytestmark = pytest.mark.unit


def _make_page(read_values: list[str] | None, input_count: int = 1) -> MagicMock:
    """构造最小 Playwright Page 替身。

    Args:
        read_values: 每次回读依次返回的值；None 表示 input 数量为 0（无法回读）。
        input_count: ``page.locator(...).count()`` 返回值。
    """
    page = MagicMock()
    page.on = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.keyboard.type = AsyncMock()

    el = AsyncMock()
    el.click = AsyncMock()
    el.wait_for = AsyncMock()
    el.fill = AsyncMock()

    combined = MagicMock()
    combined.first = el
    role_locator = MagicMock()
    role_locator.or_ = MagicMock(return_value=combined)
    page.get_by_role = MagicMock(return_value=role_locator)

    value_locator = MagicMock()
    value_locator.count = AsyncMock(return_value=input_count)
    if read_values is not None:
        value_locator.first = MagicMock()
        value_locator.first.evaluate = AsyncMock(side_effect=read_values)

    def _locator(selector: str) -> MagicMock:
        # 主路径的 loc2 选择器含 role="textbox"，与回读选择器区分。
        return MagicMock() if '[role="textbox"]' in selector else value_locator

    page.locator = MagicMock(side_effect=_locator)
    return page


async def test_fill_textbox_retries_until_value_matches() -> None:
    """首次回读被污染（重渲染吞字）→ 重试后一致即返回。"""
    page = _make_page(read_values=["435432", "5432"])
    fp = FletPage(page)

    await fp.fill_textbox("端口", "5432")

    assert page.keyboard.press.await_count == 2
    assert page.keyboard.insert_text.await_count == 1
    assert page.keyboard.type.await_count == 1


async def test_fill_textbox_skips_verification_for_masked_value() -> None:
    """密码类掩码字段回读为 ``***``：不重试、不报错。"""
    page = _make_page(read_values=["***"])
    fp = FletPage(page)

    await fp.fill_textbox("密码", "ci_test_postgres_2026!")

    assert page.keyboard.press.await_count == 1


async def test_fill_textbox_skips_verification_when_unreadable() -> None:
    """无匹配 input（无法回读）时跳过校验，保持既有兼容行为。"""
    page = _make_page(read_values=None, input_count=0)
    fp = FletPage(page)

    await fp.fill_textbox("主机", "localhost")

    assert page.keyboard.press.await_count == 1


async def test_fill_textbox_fails_fast_on_persistent_mismatch() -> None:
    """重试耗尽仍不一致 → AssertionError，错误信息含 actual/expected。"""
    page = _make_page(read_values=["bad-1", "bad-2", "bad-3"])
    fp = FletPage(page)

    with pytest.raises(AssertionError) as exc_info:
        await fp.fill_textbox("用户名", "postgres")

    message = str(exc_info.value)
    assert "postgres" in message
    assert "bad-3" in message
    assert page.keyboard.press.await_count == 3
