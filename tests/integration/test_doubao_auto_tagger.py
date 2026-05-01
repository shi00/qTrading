from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts.doubao_auto_tagger import DoubaoTagger


class _FakeLocator:
    def __init__(self, *, visible: bool, enabled: bool = True, editable: bool = True, timeout_log=None, key=""):
        self.visible = visible
        self.enabled = enabled
        self.editable = editable
        self.first = self
        self.fill = AsyncMock()
        self._timeout_log = timeout_log
        self._key = key

    async def wait_for(self, state="visible", timeout=0):
        if self._timeout_log is not None:
            self._timeout_log.append((self._key, timeout))
        if state != "visible" or not self.visible:
            raise TimeoutError(f"locator not visible within {timeout}ms")

    async def is_enabled(self):
        return self.enabled

    async def is_editable(self):
        return self.editable

    async def is_visible(self, timeout=0):
        return self.visible

    async def click(self):
        return None


class _FakePage:
    def __init__(
        self,
        *,
        css_configs=None,
        role_config=None,
        url="https://www.doubao.com/chat/",
        title="Doubao",
        content_html="<html></html>",
    ):
        self._css_configs = css_configs or {}
        self._role_config = role_config or {"visible": False, "enabled": True, "editable": True}
        self.url = url
        self._title = title
        self._content_html = content_html
        self.timeout_log: list[tuple[str, int]] = []
        self.goto = AsyncMock()
        self.screenshot = AsyncMock()
        self.keyboard = SimpleNamespace(press=AsyncMock())
        self._new_chat_btn = _FakeLocator(visible=False)

    def locator(self, selector):
        config = self._css_configs.get(selector, {})
        return _FakeLocator(
            visible=config.get("visible", False),
            enabled=config.get("enabled", True),
            editable=config.get("editable", True),
            timeout_log=self.timeout_log,
            key=selector,
        )

    def get_by_role(self, name):
        assert name == "textbox"
        return _FakeLocator(
            visible=self._role_config.get("visible", False),
            enabled=self._role_config.get("enabled", True),
            editable=self._role_config.get("editable", True),
            timeout_log=self.timeout_log,
            key="role:textbox",
        )

    def get_by_text(self, _text, exact=True):
        assert exact is True
        return self._new_chat_btn

    async def title(self):
        return self._title

    async def content(self):
        return self._content_html


@pytest.mark.asyncio
async def test_find_chat_input_uses_first_matching_fallback():
    tagger = DoubaoTagger()
    page = _FakePage(
        css_configs={
            'textarea[data-testid="chat_input_input"]': {"visible": False},
            "textarea": {"visible": True},
        }
    )

    locator, matched = await tagger._find_chat_input(page, timeout_ms=100)

    assert matched == "css:textarea"
    assert locator is not None


@pytest.mark.asyncio
async def test_find_chat_input_falls_back_to_role_textbox():
    tagger = DoubaoTagger()
    page = _FakePage(css_configs={}, role_config={"visible": True})

    locator, matched = await tagger._find_chat_input(page, timeout_ms=100)

    assert matched == "role:textbox"
    assert locator is not None


@pytest.mark.asyncio
async def test_find_chat_input_reports_page_context_when_all_selectors_fail():
    tagger = DoubaoTagger()
    page = _FakePage(url="https://www.doubao.com/login", title="Login")

    with pytest.raises(TimeoutError, match="url=https://www.doubao.com/login, title=Login"):
        await tagger._find_chat_input(page, timeout_ms=50)
    page.screenshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_chat_input_uses_total_timeout_budget_instead_of_full_timeout_per_selector():
    import asyncio

    tagger = DoubaoTagger()
    page = _FakePage(url="https://www.doubao.com/chat/", title="Doubao")

    fake_time_values = [0.0, 0.0, 0.030, 0.060]
    call_count = [0]

    def fake_time():
        idx = min(call_count[0], len(fake_time_values) - 1)
        val = fake_time_values[idx]
        call_count[0] += 1
        return val

    with patch.object(asyncio.get_running_loop(), "time", fake_time):
        with pytest.raises(TimeoutError):
            await tagger._find_chat_input(page, timeout_ms=90)

    attempted_timeouts = [timeout for _, timeout in page.timeout_log]
    assert len(attempted_timeouts) == 3
    assert sum(attempted_timeouts) <= 90


@pytest.mark.asyncio
async def test_find_chat_input_skips_visible_but_not_editable_textarea():
    tagger = DoubaoTagger()
    page = _FakePage(
        css_configs={
            'textarea[data-testid="chat_input_input"]': {"visible": False},
            "textarea": {"visible": True, "editable": False},
        },
        role_config={"visible": True, "editable": True},
    )

    locator, matched = await tagger._find_chat_input(page, timeout_ms=120)

    assert matched == "role:textbox"
    assert locator is not None


@pytest.mark.asyncio
async def test_process_batch_returns_false_when_input_preparation_fails(monkeypatch):
    tagger = DoubaoTagger()
    tagger._find_chat_input = AsyncMock(side_effect=TimeoutError("broken selector chain"))  # type: ignore[method-assign]
    tagger._dump_debug_artifacts = AsyncMock()  # type: ignore[method-assign]
    page = _FakePage()

    success = await tagger.process_batch(page, [("000001.SZ", "平安银行")])

    assert success is False
    tagger._dump_debug_artifacts.assert_awaited_once_with(page, "prompt_submission_failed")


@pytest.mark.asyncio
async def test_run_doubao_tagging_does_not_clear_concepts_before_tagger_run(monkeypatch):
    from data.data_processor import DataProcessor

    dao = SimpleNamespace(
        clear_all_doubao_concepts=AsyncMock(),
    )
    fake_processor = SimpleNamespace(cache=SimpleNamespace(stock_dao=dao))
    run_mock = AsyncMock()

    class _FakeTagger:
        def __init__(self):
            self.dao = None
            self.cancel_event = None

        async def run(self, limit=0):
            await run_mock(limit=limit, dao=self.dao, cancel_event=self.cancel_event)

    monkeypatch.setattr("scripts.doubao_auto_tagger.DoubaoTagger", _FakeTagger)

    await DataProcessor.run_doubao_tagging(fake_processor, task_id="t1", cancel_event="cancel-token")

    dao.clear_all_doubao_concepts.assert_not_awaited()
    run_mock.assert_awaited_once()
