"""近重复去重（SimHash）单测（N1-4，规格 §4.2.3）。

覆盖：归一化 / 特征提取、SimHash 确定性、Hamming 距离、分桶索引的去重判定与**召回保证**
（距离 ≤ threshold 必被检出）、参数校验。
"""

import pytest

from data.external.news_sources.dedup import (
    BANDS,
    DEFAULT_HAMMING_THRESHOLD,
    HASH_BITS,
    NearDuplicateIndex,
    features,
    hamming_distance,
    normalize_for_dedup,
    simhash,
)

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

_LONG_TEXT = "贵州茅台发布公告称公司拟以自有资金回购公司股份用于股权激励计划总金额不超过三十亿元"


# --------------------------------------------------------------------------- #
# normalize_for_dedup / features
# --------------------------------------------------------------------------- #
def test_normalize_removes_all_whitespace_and_lowercases():
    assert normalize_for_dedup(" AB c\tD\n") == "abcd"


def test_normalize_none_is_empty():
    assert normalize_for_dedup(None) == ""


def test_features_uses_character_trigrams():
    assert features("abcd", shingle_size=3) == ["abc", "bcd"]


def test_features_short_text_single_feature():
    assert features("ab", shingle_size=3) == ["ab"]


def test_features_empty_returns_empty():
    assert features("   ") == []


# --------------------------------------------------------------------------- #
# simhash / hamming_distance
# --------------------------------------------------------------------------- #
def test_simhash_is_deterministic():
    assert simhash(_LONG_TEXT) == simhash(_LONG_TEXT)


def test_simhash_ignores_whitespace_and_case():
    assert simhash("AB CD") == simhash("ab cd")


def test_simhash_different_texts_differ():
    assert simhash("贵州茅台回购股份") != simhash("中国平安减持股份")


def test_simhash_empty_is_zero():
    assert simhash("") == 0


def test_hamming_distance_basic():
    assert hamming_distance(0b1011, 0b1001) == 1
    assert hamming_distance(0, 0) == 0


# --------------------------------------------------------------------------- #
# NearDuplicateIndex
# --------------------------------------------------------------------------- #
def test_index_detects_exact_duplicate():
    index = NearDuplicateIndex()
    value = simhash(_LONG_TEXT)
    assert index.add_if_unique(value) is True
    assert index.find_duplicate(value) == value
    assert index.add_if_unique(value) is False
    assert index.size == 1


def test_index_detects_reprint_with_whitespace_difference():
    """多源转载常伴空白 / 换行差异；归一化后应落入阈值，判为重复。"""
    index = NearDuplicateIndex()
    index.add(simhash(_LONG_TEXT))
    reprint = " ".join(_LONG_TEXT)  # 逐字插入空格；normalize_for_dedup 后与原串一致
    assert index.find_duplicate(simhash(reprint)) == simhash(_LONG_TEXT)


def test_index_recall_guarantee_for_bit_flips():
    """鸽巢保证：与已收录指纹距离 ≤ threshold 的任意指纹必被检出（构造距离 1~threshold 的指纹）。"""
    index = NearDuplicateIndex(threshold=DEFAULT_HAMMING_THRESHOLD, bands=BANDS, hash_bits=HASH_BITS)
    base = 0b1011_0101_1100_0011_1010_0101_1111_0000_0001_0011_1100_1010_0101_1111_0000_1010
    index.add(base)
    for distance in range(1, DEFAULT_HAMMING_THRESHOLD + 1):
        flipped = base
        for step in range(distance):
            flipped ^= 1 << (step * 3)
        assert hamming_distance(base, flipped) == distance
        assert index.find_duplicate(flipped) == base


def test_index_unique_for_distant_value():
    index = NearDuplicateIndex()
    index.add(simhash(_LONG_TEXT))
    assert index.add_if_unique(simhash("中国平安发布减持公告完全不同的内容文本")) is True
    assert index.size == 2


def test_index_validation_rejects_invalid_params():
    with pytest.raises(ValueError, match="threshold"):
        NearDuplicateIndex(threshold=4, bands=4)
    with pytest.raises(ValueError, match="bands"):
        NearDuplicateIndex(threshold=0, bands=0)
    with pytest.raises(ValueError, match="hash_bits"):
        NearDuplicateIndex(threshold=0, bands=3, hash_bits=64)
    with pytest.raises(ValueError, match="threshold"):
        NearDuplicateIndex(threshold=-1)
