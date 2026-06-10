import pytest

from services.news_subscription_service import NewsSubscriptionService


def _make_i18n_mock(overrides=None):
    overrides = overrides or {}

    def mock_get(key, *a, **kw):
        if key in overrides:
            val = overrides[key]
            if kw:
                try:
                    return val.format(**kw)
                except (KeyError, IndexError):
                    return val
            return val
        if "news_tag_format" in key:
            fmt = "【{emoji} {category}】"
            if kw:
                try:
                    return fmt.format(**kw)
                except (KeyError, IndexError):
                    return fmt
            return fmt
        return key

    return mock_get


@pytest.mark.asyncio
async def test_generate_tags_uses_translated_category_from_ai(monkeypatch):
    """AI 分类返回的 category 已由 _parse_news_result 翻译，直接使用。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _fake_classify(_content):
        return {"emoji": "📰", "category": "金融核心-贵金属"}

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_fake_classify)})()

    import services.news_subscription_service as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", _make_i18n_mock())

    tag = await svc._generate_tags("紫金矿业发现金矿")
    assert "金融核心-贵金属" in tag


@pytest.mark.asyncio
async def test_generate_tags_rule_based_policy(monkeypatch):
    """规则兜底：政策类新闻应显示本地化标签。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _raise_ai(_content):
        raise RuntimeError("ai unavailable")

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_raise_ai)})()

    import services.news_subscription_service as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", _make_i18n_mock({"tag_policy": "政策"}))

    tag = await svc._generate_tags("央行发布新政策")
    assert "政策" in tag


@pytest.mark.asyncio
async def test_generate_tags_rule_based_global(monkeypatch):
    """规则兜底：外盘类新闻应显示本地化标签。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _raise_ai(_content):
        raise RuntimeError("ai unavailable")

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_raise_ai)})()

    import services.news_subscription_service as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", _make_i18n_mock({"tag_global": "外盘"}))

    tag = await svc._generate_tags("美联储议息会议在即")
    assert "外盘" in tag


@pytest.mark.asyncio
async def test_generate_tags_rule_based_macro(monkeypatch):
    """规则兜底：宏观类新闻应显示本地化标签。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _raise_ai(_content):
        raise RuntimeError("ai unavailable")

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_raise_ai)})()

    import services.news_subscription_service as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", _make_i18n_mock({"tag_macro": "宏观"}))

    tag = await svc._generate_tags("CPI数据超预期")
    assert "宏观" in tag


@pytest.mark.asyncio
async def test_generate_tags_no_match_returns_empty(monkeypatch):
    """AI 不可用且规则不匹配时，应返回空标签。"""
    svc = NewsSubscriptionService.__new__(NewsSubscriptionService)

    async def _raise_ai(_content):
        raise RuntimeError("ai unavailable")

    svc.ai_client = type("AIClient", (), {"classify_news": staticmethod(_raise_ai)})()

    import services.news_subscription_service as ns_mod

    monkeypatch.setattr(ns_mod.I18n, "get", _make_i18n_mock())

    tag = await svc._generate_tags("今天天气不错")
    assert tag == ""
