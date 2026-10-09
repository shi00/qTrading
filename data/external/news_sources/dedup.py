"""近重复去重纯逻辑（SimHash，N1-4，规格 §4.2.3）。

同一事件多源转载很常见（新浪 7x24 ↔ 新浪个股、巨潮公告多家转载稿），需按内容指纹去重，
避免同一事件在语料中过度重复导致训练/评估指标虚高（规格 §4.6 同因）。

实现
----
- 指纹：对归一化文本取**字符 3-gram** 特征，逐特征 64 位哈希后按位加权求号得 SimHash；
- 去重：Hamming 距离 ≤ :data:`DEFAULT_HAMMING_THRESHOLD` 视为近重复；
- 索引：把 64 位切成 :data:`BANDS` 段建桶（鸽巢原理：距离 ≤ ``BANDS - 1`` 时至少有一段完全
  相同），新指纹只需与同段桶内候选比较，避免 O(n²)。

仅标准库（``hashlib``），位于 ``data/`` 层。
"""

import hashlib

# NOTE(lazy): 采用字符 3-gram SimHash + 固定 Hamming 阈值，仅保证「近逐字重复」（空白 / 排版差异）去重.
# ceiling: 短文本（标题 / 快讯）对内容级改写（换词、改数字）不敏感，Hamming 距离常显著超阈值而漏判.
# upgrade: 语料库实测同一事件多源改写漏判率高时，改用更鲁棒指纹（如 word 级 shingle + 自适应阈值）或叠加语义向量去重.
HASH_BITS = 64
SHINGLE_SIZE = 3
DEFAULT_HAMMING_THRESHOLD = 3
BANDS = 4
_BAND_BITS = HASH_BITS // BANDS  # 每段位数（16）
_BAND_MASK = (1 << _BAND_BITS) - 1


def normalize_for_dedup(text: object) -> str:
    """去全部空白并小写：同一文本的不同排版视为同一特征集合。"""
    return "".join(str(text or "").split()).lower()


def features(text: object, *, shingle_size: int = SHINGLE_SIZE) -> list[str]:
    """字符 ``shingle_size``-gram 特征；文本短于 shingle 时整串作单一特征（可能为空）。"""
    normalized = normalize_for_dedup(text)
    if not normalized:
        return []
    if len(normalized) <= shingle_size:
        return [normalized]
    return [normalized[i : i + shingle_size] for i in range(len(normalized) - shingle_size + 1)]


def _feature_hash(feature: str) -> int:
    """特征 → 64 位整数（blake2b，确定性跨进程一致）。"""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash(text: object, *, shingle_size: int = SHINGLE_SIZE, hash_bits: int = HASH_BITS) -> int:
    """计算 SimHash（64 位）。无特征（空文本）返回 0。"""
    vector = [0] * hash_bits
    for feature in features(text, shingle_size=shingle_size):
        feature_hash = _feature_hash(feature)
        for bit in range(hash_bits):
            vector[bit] += 1 if (feature_hash >> bit) & 1 else -1
    result = 0
    for bit in range(hash_bits):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def hamming_distance(a: int, b: int) -> int:
    """两枚 64 位指纹的 Hamming 距离。"""
    return (a ^ b).bit_count()


class NearDuplicateIndex:
    """近重复索引：分段分桶 + Hamming 距离判定。

    ``threshold`` 必须 < ``bands``（否则鸽巢召回保证失效）；默认 ``bands=4`` / ``threshold=3``。
    """

    def __init__(
        self,
        *,
        threshold: int = DEFAULT_HAMMING_THRESHOLD,
        bands: int = BANDS,
        hash_bits: int = HASH_BITS,
    ) -> None:
        if threshold < 0:
            raise ValueError("threshold 不能为负")
        if bands < 1:
            raise ValueError("bands 必须 ≥ 1")
        if hash_bits % bands != 0:
            raise ValueError("hash_bits 必须能被 bands 整除")
        if threshold >= bands:
            raise ValueError(f"threshold({threshold}) 必须 < bands({bands})，否则分桶召回保证失效")
        self._threshold = threshold
        self._bands = bands
        self._band_bits = hash_bits // bands
        self._band_mask = (1 << self._band_bits) - 1
        self._buckets: list[dict[int, list[int]]] = [{} for _ in range(bands)]
        self._size = 0

    @property
    def size(self) -> int:
        """已收录指纹数。"""
        return self._size

    def _band_key(self, value: int, band: int) -> int:
        return (value >> (band * self._band_bits)) & self._band_mask

    def find_duplicate(self, value: int) -> int | None:
        """返回任一 Hamming 距离 ≤ threshold 的已收录指纹；无则 ``None``。"""
        seen: set[int] = set()
        for band in range(self._bands):
            for candidate in self._buckets[band].get(self._band_key(value, band), ()):
                if candidate in seen:
                    continue
                seen.add(candidate)
                if hamming_distance(value, candidate) <= self._threshold:
                    return candidate
        return None

    def add(self, value: int) -> None:
        """收录一枚指纹（不做重复判定）。"""
        for band in range(self._bands):
            self._buckets[band].setdefault(self._band_key(value, band), []).append(value)
        self._size += 1

    def add_if_unique(self, value: int) -> bool:
        """若与已收录指纹不近重复则收录并返回 ``True``；否则返回 ``False``（不收录）。"""
        if self.find_duplicate(value) is not None:
            return False
        self.add(value)
        return True
