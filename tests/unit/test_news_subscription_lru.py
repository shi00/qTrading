from collections import OrderedDict


def test_seen_hashes_initialized_as_ordered_dict():
    from services.news_subscription_service import NewsSubscriptionService

    NewsSubscriptionService._instance = None
    svc = NewsSubscriptionService()
    assert isinstance(svc._seen_hashes, OrderedDict), (
        f"H-5: _seen_hashes must be OrderedDict, got {type(svc._seen_hashes)}"
    )


def test_lru_keeps_most_recent_hashes():
    kept_count = 5
    seen_hashes: OrderedDict[str, None] = OrderedDict()
    all_hashes = [f"h{i:03d}" for i in range(20)]

    for hash_value in all_hashes:
        if hash_value not in seen_hashes:
            seen_hashes[hash_value] = None
            if len(seen_hashes) > kept_count:
                seen_hashes.popitem(last=False)

    assert list(seen_hashes.keys()) == all_hashes[-kept_count:], (
        f"H-5: LRU should keep the most recent hashes, got {list(seen_hashes.keys())}"
    )
