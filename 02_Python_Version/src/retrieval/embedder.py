"""离线可复现的中文字符 n-gram Hashing Embedding。"""

from __future__ import annotations

import hashlib
import math
import re


def tokenize(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    chinese = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    tokens = chinese[:]
    tokens.extend("".join(chinese[index : index + 2]) for index in range(max(0, len(chinese) - 1)))
    tokens.extend(re.findall(r"[a-z0-9_-]+", normalized))
    return tokens


class HashingEmbedder:
    name = "LocalHashingEmbedding-v2"

    def __init__(self, dimension: int = 512):
        if dimension < 32:
            raise ValueError("向量维度不应小于 32。")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vector[value % self.dimension] += -1.0 if value & 1 else 1.0
        norm = math.sqrt(sum(component * component for component in vector))
        return [component / norm for component in vector] if norm else vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("向量维度不一致。")
    return sum(a * b for a, b in zip(left, right))
