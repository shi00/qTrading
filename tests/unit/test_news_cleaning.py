"""语料文本清洗纯逻辑单测（N1-4，规格 §4.2）。

覆盖：HTML/实体清洗、``【栏目名】`` 与模板语剥离、快讯截断 120 字（标题不截断）、
例行条目判定与过滤、清洗后为空丢弃、字段透传与 ``text_field`` 覆盖。
"""

import pytest

from data.external.news_sources.cleaning import (
    CLEAN_REASON_EMPTY,
    CLEAN_REASON_ROUTINE,
    FLASH_MAX_CHARS,
    TEXT_KIND_FLASH,
    TEXT_KIND_TITLE,
    clean_document,
    clean_text,
    is_routine_title,
    strip_html_tags,
    strip_template_phrases,
    truncate,
)

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


# --------------------------------------------------------------------------- #
# strip_html_tags
# --------------------------------------------------------------------------- #
def test_strip_html_tags_removes_tags_and_em_highlight():
    assert strip_html_tags("<em>回购</em>公司股份") == "回购公司股份"


def test_strip_html_tags_unescapes_entities_and_normalizes_ws():
    assert strip_html_tags("A&nbsp;公司\u3000发布   <b>x</b>") == "A 公司 发布 x"


def test_strip_html_tags_none_returns_empty():
    assert strip_html_tags(None) == ""


# --------------------------------------------------------------------------- #
# strip_template_phrases
# --------------------------------------------------------------------------- #
def test_strip_template_phrases_removes_leading_column_marker():
    assert strip_template_phrases("【财经早报】今日要闻") == "今日要闻"
    assert strip_template_phrases("[公告] 某某事项") == "某某事项"


def test_strip_template_phrases_only_strips_one_leading_marker():
    assert strip_template_phrases("【A】【B】正文") == "【B】正文"


def test_strip_template_phrases_removes_source_signature():
    assert strip_template_phrases("新浪财经讯 某公司中标") == "某公司中标"
    assert strip_template_phrases("消息（新浪财经）") == "消息"


def test_strip_template_phrases_keeps_normal_text():
    assert strip_template_phrases("贵州茅台发布公告") == "贵州茅台发布公告"


# --------------------------------------------------------------------------- #
# is_routine_title
# --------------------------------------------------------------------------- #
def test_is_routine_title_matches_known_patterns():
    assert is_routine_title("H股公告-翌日披露报表") is True
    assert is_routine_title("股份发行人的证券变动月报表") is True


def test_is_routine_title_false_for_meaningful_title():
    assert is_routine_title("关于获得药品注册证书的公告") is False


def test_is_routine_title_none_text_is_false():
    assert is_routine_title(None) is False  # 容忍 None 入参的边界用例


# --------------------------------------------------------------------------- #
# truncate / clean_text
# --------------------------------------------------------------------------- #
def test_truncate_caps_length():
    assert truncate("字" * 200, FLASH_MAX_CHARS) == "字" * 120
    assert truncate("短文本", FLASH_MAX_CHARS) == "短文本"


def test_truncate_keeps_when_max_non_positive():
    assert truncate("字" * 200, 0) == "字" * 200


def test_clean_text_flash_truncates_to_120():
    assert len(clean_text("字" * 200, kind=TEXT_KIND_FLASH)) == 120


def test_clean_text_title_does_not_truncate():
    assert len(clean_text("字" * 200, kind=TEXT_KIND_TITLE)) == 200


def test_clean_text_applies_html_and_template_cleaning():
    assert clean_text("<em>【要闻】</em>新浪财经讯 内容", kind=TEXT_KIND_TITLE) == "内容"


# --------------------------------------------------------------------------- #
# clean_document
# --------------------------------------------------------------------------- #
def test_clean_document_keeps_and_passthroughs_other_fields():
    doc = {"source": "sina_7x24", "source_id": "1", "text": "【要闻】正文内容"}
    cleaned, reason = clean_document(doc, text_kind=TEXT_KIND_FLASH)
    assert reason is None
    assert cleaned is not None
    assert cleaned["text"] == "正文内容"
    assert cleaned["source"] == "sina_7x24"
    assert cleaned["source_id"] == "1"


def test_clean_document_drops_empty_after_clean():
    cleaned, reason = clean_document({"text": "<em></em>"}, text_kind=TEXT_KIND_TITLE)
    assert cleaned is None
    assert reason == CLEAN_REASON_EMPTY


def test_clean_document_drops_routine_when_enabled():
    doc = {"text": "H股公告-翌日披露报表"}
    cleaned, reason = clean_document(doc, text_kind=TEXT_KIND_TITLE, drop_routine=True)
    assert cleaned is None
    assert reason == CLEAN_REASON_ROUTINE


def test_clean_document_keeps_routine_when_disabled():
    doc = {"text": "H股公告-翌日披露报表"}
    cleaned, reason = clean_document(doc, text_kind=TEXT_KIND_TITLE, drop_routine=False)
    assert reason is None
    assert cleaned is not None
    assert cleaned["text"] == "H股公告-翌日披露报表"


def test_clean_document_respects_text_field():
    doc = {"title": "<em>标题</em>", "text": "正文"}
    cleaned, reason = clean_document(doc, text_kind=TEXT_KIND_TITLE, text_field="title")
    assert reason is None
    assert cleaned is not None
    assert cleaned["title"] == "标题"
    assert cleaned["text"] == "正文"
