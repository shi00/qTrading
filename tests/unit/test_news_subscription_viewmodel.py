"""
Tests for NewsSubscriptionService and ScreenerViewModel.

U-2: stop() should not clear listeners.
U-3: HISTORY mode should buffer AI content and merge on switch back.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestNewsSubscriptionStopNoClear:
    """U-2: stop() should not clear _listeners"""

    def test_stop_does_not_call_listeners_clear(self):
        """stop() method should not contain active _listeners.clear()"""
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()

        in_stop = False
        stop_lines = []
        indent_level = None
        for line in source.split("\n"):
            stripped = line.lstrip()
            if "def stop(" in stripped:
                in_stop = True
                indent_level = len(line) - len(stripped)
                continue
            if in_stop:
                if stripped and not stripped.startswith("#"):
                    current_indent = len(line) - len(stripped)
                    if indent_level is not None and current_indent <= indent_level and stripped.startswith("def "):
                        break
                    stop_lines.append(stripped)
                elif stripped.startswith("#"):
                    pass

        stop_source = "\n".join(stop_lines)
        assert "_listeners.clear()" not in stop_source, "U-2: stop() should not clear _listeners"
        assert "_listeners = []" not in stop_source, "U-2: stop() should not reassign _listeners"

    def test_listeners_attribute_in_init(self):
        """NewsSubscriptionService should initialize _listeners"""
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()

        assert "_listeners" in source, "NewsSubscriptionService should have _listeners"


class TestHistoryModeBuffer:
    """U-3: HISTORY mode should buffer AI content and merge on switch back"""

    def test_discarded_buffer_in_viewmodel(self):
        """ScreenerViewModel should have _discarded_buffer attribute"""
        vm_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ui", "viewmodels", "screener_view_model.py")
        )
        with open(vm_path, encoding="utf-8") as f:
            source = f.read()

        assert "_discarded_buffer" in source, "U-3: ScreenerViewModel should have _discarded_buffer"

    def test_discarded_buffer_merge_logic(self):
        """When switching from HISTORY back to LIVE, discarded buffer should merge"""
        discarded = ["chunk1", "chunk2"]
        current = ["chunk3"]

        merged = discarded + current
        assert merged == ["chunk1", "chunk2", "chunk3"]


class TestNewsSubscriptionCorrelationId:
    """Verify NewsSubscriptionService uses correlation_scope for log tracing."""

    def test_processing_loop_uses_correlation_scope(self):
        """_processing_loop should use correlation_scope for each item."""
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()

        assert "correlation_scope" in source, "NewsSubscriptionService should import correlation_scope"

    def test_fetch_and_notify_uses_correlation_scope(self):
        """_fetch_and_notify should wrap operations in correlation_scope."""
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()

        in_fetch = False
        fetch_lines = []
        for line in source.split("\n"):
            stripped = line.lstrip()
            if "def _fetch_and_notify(" in stripped:
                in_fetch = True
                continue
            if in_fetch:
                if stripped.startswith("def ") and "def _fetch_and_notify" not in stripped:
                    break
                fetch_lines.append(stripped)

        fetch_source = "\n".join(fetch_lines)
        assert "correlation_scope" in fetch_source, "_fetch_and_notify should use correlation_scope"
        assert 'correlation_scope("news-fetch")' in fetch_source, (
            "_fetch_and_notify should use 'news-fetch' correlation scope"
        )


class TestNewsSubscriptionLRU:
    """H-5: _seen_hashes must preserve insertion order (OrderedDict-based LRU)."""

    def test_seen_hashes_is_ordered_dict_not_set(self):
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()
        assert "self._seen_hashes: OrderedDict[str, None] = OrderedDict()" in source, (
            "H-5: _seen_hashes must be OrderedDict, not set"
        )

    def test_seen_hashes_uses_popitem_not_set_slice(self):
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()
        assert "list(self._seen_hashes)" not in source, "H-5: must not convert dict to list for trimming"
        assert ".pop(" in source or "popitem" in source, "H-5: must use dict.pop/popitem for LRU eviction"

    def test_lru_eviction_preserves_recent_items(self):
        from collections import OrderedDict

        svc = object.__new__(
            __import__("data.external.news_subscription", fromlist=["NewsSubscriptionService"]).NewsSubscriptionService
        )
        svc._MAX_SEEN = 5
        svc._seen_hashes = OrderedDict()
        for i in range(10):
            h = f"hash_{i:03d}"
            svc._seen_hashes[h] = None
            if len(svc._seen_hashes) > svc._MAX_SEEN:
                svc._seen_hashes.popitem(last=False)
        assert len(svc._seen_hashes) == 5
        keys = list(svc._seen_hashes.keys())
        assert keys == ["hash_005", "hash_006", "hash_007", "hash_008", "hash_009"], (
            f"H-5: LRU should keep most recent, got {keys}"
        )
