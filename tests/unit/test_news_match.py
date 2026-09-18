# B4：新闻-股票直接关联 + 确定性去重（data/news_match.py 纯逻辑单测）。
# 覆盖 §15.1 的「直接关联：代码、公司全称、合法简称、歧义简称和行业词误匹配」
# 与「去重：URL、标题、内容哈希及相似但不同进展」。

import datetime
import pytest

from data.news_match import (
    dedupe_documents,
    match_news_to_stock,
    _is_excluded_number,
    _normalize_title,
)

pytestmark = pytest.mark.unit


# ---------- 内部辅助：排除段 ----------
class TestIsExcludedNumber:
    def test_year_prefix_excluded(self):
        """19xx/20xx 开头视为年份/日期段，不当作股票代码。"""
        assert _is_excluded_number("202403") is True
        assert _is_excluded_number("199900") is True

    def test_repeated_digits_excluded(self):
        """连续重复数字（如 888888）排除。"""
        assert _is_excluded_number("888888") is True

    def test_sequential_excluded(self):
        """连续递增/递减排除。"""
        assert _is_excluded_number("123456") is True
        assert _is_excluded_number("987654") is True

    def test_normal_code_kept(self):
        assert _is_excluded_number("600519") is False
        assert _is_excluded_number("000001") is False


# ---------- 直接关联 ----------
class TestMatchNewsToStock:
    def _candidates(self):
        # 600519 贵州茅台（含简称"茅台"）、601318 中国平安、000651 格力电器
        return [
            {"ts_code": "600519.SH", "name": "贵州茅台"},
            {"ts_code": "601318.SH", "name": "中国平安"},
            {"ts_code": "000651.SZ", "name": "格力电器"},
        ]

    def test_empty_text_returns_empty(self):
        assert match_news_to_stock(None, self._candidates()) == []
        assert match_news_to_stock("  ", self._candidates()) == []

    def test_empty_candidates_returns_empty(self):
        assert match_news_to_stock("贵州茅台提价", []) == []

    def test_full_code_suffix_form(self):
        """完整代码 ``600519.SH`` 直接命中。"""
        assert match_news_to_stock("600519.SH 发布业绩预告", self._candidates()) == ["600519.SH"]

    def test_full_code_prefix_form_case_insensitive(self):
        """``SH600519`` / ``sh600519`` 大小写不敏感命中。"""
        assert match_news_to_stock("SH600519 发布公告", self._candidates()) == ["600519.SH"]
        assert match_news_to_stock("sh600519 公告", self._candidates()) == ["600519.SH"]

    def test_bare_6digit_suffix_in_pool(self):
        """裸 6 位落在候选池直接命中。"""
        assert match_news_to_stock("公司宣布 600519 相关事项", self._candidates()) == ["600519.SH"]

    def test_bare_6digit_not_in_pool_ignored(self):
        """裸 6 位不在候选池则不入。"""
        assert match_news_to_stock("提到 999999 数字", self._candidates()) == []

    def test_bare_excluded_number_ignored(self):
        """裸 6 位为年份（202403）等排除段则不算代码。"""
        assert match_news_to_stock("202403 数据发布", self._candidates()) == []

    def test_bare_6digit_overscan_matched_prefix(self):
        """裸 6 位被其他数字拼入（前紧邻数字）不匹配：1202519 不匹配 600519。"""
        # 构造文本里出现被拼接的 600519，前接"1"。裸数字 regex 前后不得紧邻字母数字。
        assert match_news_to_stock("总股本1600519", [{"ts_code": "600519.SH", "name": "贵州茅台"}]) == []

    def test_full_company_name_matches(self):
        """公司全称（>=4 汉字）直接出现在文本命中。"""
        assert match_news_to_stock("贵州茅台宣布分红方案", self._candidates()) == ["600519.SH"]

    def test_unique_short_name_matches(self):
        """唯一个股简称（>=3 汉字）命中。"""
        assert match_news_to_stock("格力电器新推产品线", self._candidates()) == ["000651.SZ"]
        # "茅台" 2 汉字 < 3，不按简称命中（全称已覆盖场景单独测）
        assert match_news_to_stock("茅台披露销售", [{"ts_code": "600519.SH", "name": "贵州茅台"}]) == []

    def test_ambiguous_short_name_not_matched(self):
        """歧义简称（候选池出现多次/更短）不作为直接关联依据。"""
        # 若两只股票简称都是 2 汉字（<3），不命中——可靠性优先，宁可漏报。
        cands = [
            {"ts_code": "600519.SH", "name": "贵州茅台"},
            {"ts_code": "000858.SZ", "name": "五粮液"},
        ]
        assert match_news_to_stock("茅台集团合作", cands) == []

    def test_industry_word_no_false_match(self):
        """行业词误匹配：不含代码/全称/唯一简称时不命中（保持只报确定，不报推测）。"""
        assert match_news_to_stock("白酒行业景气度回升", self._candidates()) == []

    def test_shortname_under_3_chars_no_match(self):
        """<3 汉字的简称（如 2 字）不直接匹配。"""
        cands = [{"ts_code": "600519.SH", "name": "茅台"}]
        assert match_news_to_stock("茅台发布", cands) == []

    def test_shortname_unique_3chars_matches(self):
        """恰好 3 汉字且候选池唯一的简称命中。"""
        cands = [{"ts_code": "600332.SH", "name": "白云山"}]
        assert match_news_to_stock("白云山发布公告", cands) == ["600332.SH"]

    def test_shortname_3chars_duplicated_in_pool_not_matched(self):
        """候选池存在两个同名裸简称时（歧义）不作为直接关联依据。"""
        cands = [
            {"ts_code": "600332.SH", "name": "白云山"},
            {"ts_code": "000261.SZ", "name": "白云山"},
        ]
        assert match_news_to_stock("白云山发布公告", cands) == []

    def test_no_duplicate_ts_codes_in_result(self):
        """同一条新闻命中同一股票多次去重，仅返回一次。"""
        text = "600519.SH 今日大涨，贵州茅台再创新高"
        assert match_news_to_stock(text, self._candidates()) == ["600519.SH"]


# ---------- 标题标准化 ----------
class TestNormalizeTitle:
    def test_lowercase_and_collapse_whitespace(self):
        assert _normalize_title("  Hello   World  ") == "hello world"

    def test_none_returns_empty(self):
        assert _normalize_title(None) == ""
        assert _normalize_title("   ") == ""


# ---------- 去重 ----------
class TestDedupeDocuments:
    def _doc(self, **kw):
        base = {
            "source_kind": "news",
            "title": "某标题",
            "url": None,
            "content_hash": None,
            "publish_time": datetime.datetime(2026, 9, 1, 10, 0, 0),
        }
        base.update(kw)
        return base

    def test_no_duplicates_all_kept(self):
        docs = [self._doc(title="a"), self._doc(title="b")]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 2
        assert dropped == []

    def test_url_duplicate_dropped(self):
        docs = [
            self._doc(url="http://x.com/1"),
            self._doc(url="http://x.com/1", title="不同标题"),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "url_same"

    def test_content_hash_duplicate_dropped(self):
        docs = [
            self._doc(content_hash="abc"),
            self._doc(content_hash="abc", url="http://y.com/2"),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "content_hash_same"

    def test_title_time_similar_news_10min_dropped(self):
        """news 同源标题 10 分钟内视为重复。"""
        t0 = datetime.datetime(2026, 9, 1, 10, 0, 0)
        docs = [
            self._doc(source_kind="news", title="茅台提价", publish_time=t0),
            self._doc(source_kind="news", title="茅台提价", publish_time=t0 + datetime.timedelta(minutes=5)),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "title_time_similar"

    def test_title_same_but_beyond_window_kept(self):
        """news 同标题但超过 10 分钟不视为重复（相似但不同进展）。"""
        t0 = datetime.datetime(2026, 9, 1, 10, 0, 0)
        docs = [
            self._doc(source_kind="news", title="茅台提价", publish_time=t0),
            self._doc(source_kind="news", title="茅台提价", publish_time=t0 + datetime.timedelta(minutes=30)),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 2  # 同标题但时间不接近 → 保留

    def test_announcement_same_day_title_dropped(self):
        """announcement 同日同标题视为重复。"""
        a = datetime.datetime(2026, 9, 1, 8, 0, 0)
        b = datetime.datetime(2026, 9, 1, 23, 0, 0)
        docs = [
            self._doc(source_kind="announcement", title="业绩预增", publish_time=a),
            self._doc(source_kind="announcement", title="业绩预增", publish_time=b),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "title_time_similar"

    def test_announcement_cross_day_title_kept(self):
        """announcement 标题相同但跨日不视为重复。"""
        a = datetime.datetime(2026, 9, 1, 23, 0, 0)
        b = datetime.datetime(2026, 9, 2, 1, 0, 0)
        docs = [
            self._doc(source_kind="announcement", title="业绩预增", publish_time=a),
            self._doc(source_kind="announcement", title="业绩预增", publish_time=b),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 2

    def test_cross_source_kind_not_deduped(self):
        """跨 source_kind 不互去重：即使内容完全一致也保留。"""
        docs = [
            self._doc(source_kind="news", content_hash="same"),
            self._doc(source_kind="announcement", content_hash="same"),
        ]
        kept, dropped = dedupe_documents(docs)
        assert len(kept) == 2
        assert dropped == []

    def test_missing_time_title_only_hash_when_available(self):
        """缺时间但有不同 content_hash/url 时不误判。"""
        docs = [
            self._doc(source_kind="news", title="相同标题", publish_time=None, content_hash="h1"),
            self._doc(source_kind="news", title="相同标题", publish_time=None, content_hash="h2"),
        ]
        kept, dropped = dedupe_documents(docs)
        # 无时间 → 标题时间规则不触发；hash 不同 → 不重复
        assert len(kept) == 2

    def test_dropped_keeps_original_fields_plus_reason(self):
        """dropped 文档保留原字段并附加 dedup_reason。"""
        docs = [
            self._doc(url="http://x.com/1", title="原标题"),
            self._doc(url="http://x.com/1", title="副本"),
        ]
        kept, dropped = dedupe_documents(docs)
        assert dropped[0]["title"] == "副本"
        assert dropped[0]["dedup_reason"] == "url_same"
