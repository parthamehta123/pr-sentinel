"""Embeddings, with an offline default.

`hash` is a real feature-hashing embedder over character 4-grams and identifier
tokens: deterministic, dimension-configurable, no network, no key. Recall is
clearly worse than a trained model — it captures lexical overlap, not meaning —
but it makes the whole system runnable and testable without a second vendor, and
it keeps the dimension contract honest in CI.

`local` is a trained static embedding model that runs on CPU with no key and no
network after the first download. It exists because "hybrid vector + FTS" was, as
deployed here, two *lexical* signals fused together — reciprocal-rank fusion over
two correlated signals buys much less than over independent ones, and measuring
retrieval at recall@12 0.337 is what made that visible.

Switch to `openai` when a hosted model is wanted instead.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from abc import ABC, abstractmethod

from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")


class Embedder(ABC):
    name: str = "abstract"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class HashingEmbedder(Embedder):
    name = "hash"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall(text)
        # camelCase / snake_case pieces too, so `getUserById` meets `user_id`.
        expanded: list[str] = []
        for tok in tokens:
            expanded.append(tok.lower())
            expanded.extend(p.lower() for p in re.findall(r"[A-Z]?[a-z]+|\d+", tok) if len(p) > 2)
        for gram in _bigrams(expanded):
            idx, sign = _slot(gram, self.dim)
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


def _bigrams(tokens: list[str]) -> list[str]:
    out = list(tokens)
    out.extend(f"{a}␟{b}" for a, b in itertools.pairwise(tokens))
    return out


def _slot(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(digest, "big")
    return n % dim, 1.0 if (n >> 63) & 1 else -1.0


class OpenAIEmbedder(Embedder):
    name = "openai"

    def __init__(self, dim: int, model: str, api_key: str) -> None:
        super().__init__(dim)
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key or None)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 96):  # keep request bodies sane
            batch = texts[i : i + 96]
            resp = await self._client.embeddings.create(model=self.model, input=batch, dimensions=self.dim)
            out.extend(d.embedding for d in resp.data)
        return out


class LocalEmbedder(Embedder):
    """A trained static embedding model, on CPU, with no vendor.

    Static embeddings are a distilled sentence transformer: one vector per token,
    pooled, with no forward pass at query time. That makes them a few hundred
    times faster than the model they come from and meaningfully worse at nuance —
    and still categorically different from feature hashing, which has no notion
    that two spellings of the same idea are related at all.

    Dimension. The model's width is fixed, and the `code_chunks.embedding` column
    is `vector(EMBEDDING_DIM)`. Short vectors are zero-padded to that width, which
    is exact rather than approximate: appending zeros changes neither a dot
    product nor a norm, so cosine ranking is identical to ranking on the
    unpadded vectors. It wastes storage in proportion to the gap, which a
    migration narrowing the column would recover. Padding is allowed; truncating
    is not, because dropping components does change the ranking.
    """

    name = "local"

    def __init__(self, dim: int, model: str) -> None:
        super().__init__(dim)
        try:
            from model2vec import StaticModel
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise RuntimeError(
                "EMBEDDING_PROVIDER=local needs the optional dependency: "
                "pip install 'pr-sentinel[local-embeddings]'"
            ) from exc

        self._model = StaticModel.from_pretrained(model)
        self._native = int(self._model.dim)
        if self._native > dim:
            raise ValueError(
                f"{model} produces {self._native}-dimensional vectors but EMBEDDING_DIM "
                f"is {dim}. Truncating would change the ranking, so this is refused. "
                f"Set EMBEDDING_DIM={self._native} and re-run the migrations."
            )
        log.info("embeddings.local", model=model, native_dim=self._native, padded_to=dim)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts)
        pad = [0.0] * (self.dim - self._native)
        return [[float(x) for x in v] + pad for v in vectors]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        if s.embedding_provider == "openai":
            _embedder = OpenAIEmbedder(s.embedding_dim, s.embedding_model, s.openai_api_key)
        elif s.embedding_provider == "local":
            _embedder = LocalEmbedder(s.embedding_dim, s.local_embedding_model)
        else:
            _embedder = HashingEmbedder(s.embedding_dim)
        log.info("embeddings.provider", provider=_embedder.name, dim=_embedder.dim)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    global _embedder
    _embedder = embedder
