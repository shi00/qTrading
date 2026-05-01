import pytest

from data.external.news_subscription import NewsSubscriptionService


@pytest.mark.asyncio
async def test_generate_tags_uses_localized_i18n_category(monkeypatch):
    """O-2: AI 分类命中 i18n key 时应显示本地化文案。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _fake_ai_result():
        return {"emoji": "📰", "category": "policy"}

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(lambda _c: _fake_ai_result())})()

    from data.external import news_subscription as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", lambda key: "政策" if key == "tag_policy" else key)

    tag = await svc._generate_tags("政策消息")
    assert tag == "【📰 政策】"


@pytest.mark.asyncio
async def test_generate_tags_falls_back_to_original_category_when_i18n_missing(monkeypatch):
    """O-2: i18n 缺失时（返回 key 本身）应退回 AI 原始分类名。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _fake_ai_result(_content):
        return {"emoji": "📰", "category": "Breaking"}

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_fake_ai_result)})()

    from data.external import news_subscription as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", lambda key: key)

    tag = await svc._generate_tags("突发新闻")
    assert tag == "【📰 Breaking】"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "localized,expected",
    [
        ("外盘", "【🌍 外盘】"),
        ("Global", "【🌍 Global】"),
    ],
)
async def test_rule_based_global_tag_supports_different_locales(monkeypatch, localized, expected):
    """O-2: 规则兜底标签在不同 locale 下应读取对应 i18n 文案。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _raise_ai(_content):
        raise RuntimeError("ai unavailable")

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_raise_ai)})()

    from data.external import news_subscription as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", lambda key: localized if key == "tag_global" else key)

    tag = await svc._generate_tags("美联储议息会议在即")
    assert tag == expected
