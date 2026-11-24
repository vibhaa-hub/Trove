"""Encoder-only ("System 1") models used instead of a generative LLM.

These models map text to vectors; they never generate text, so they are fast,
cheap (run on CPU, no per-call fees), deterministic and cannot be prompt-
injected into producing instructions. Trove uses them for:

* RAG retrieval over skills and knowledge files
* semantic skill routing
* zero-shot classification of statement/SMS lines into asset categories
* entity resolution in the knowledge graph (institution / person variants)

Backends (TROVE_ENCODER):
  fastembed  BAAI/bge-small-en-v1.5 via ONNX Runtime (384-d, ~67 MB, local)
  hashing    dependency-free character n-gram hashing (tests / offline)
  auto       fastembed if installed and the model can load, else hashing
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
from typing import Iterable, Protocol, Sequence

log = logging.getLogger("trove.encoders")
DIM_HASH = 384


class Encoder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


class HashingEncoder:
    """Character 3-5 gram feature hashing. Weak semantics, zero dependencies."""

    name = "hashing-ngram-384"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vector = [0.0] * DIM_HASH
            clean = " " + re.sub(r"\s+", " ", text.casefold()) + " "
            for n in (3, 4, 5):
                for i in range(len(clean) - n + 1):
                    digest = hashlib.blake2b(clean[i:i + n].encode(), digest_size=4).digest()
                    bucket = int.from_bytes(digest[:3], "little") % DIM_HASH
                    vector[bucket] += 1.0 if digest[3] & 1 else -1.0
            out.append(_normalize(vector))
        return out


class FastEmbedEncoder:
    def __init__(self, model: str, cache_dir: str) -> None:
        from fastembed import TextEmbedding

        self.name = model
        self._model = TextEmbedding(model, cache_dir=cache_dir)
        self._lock = threading.Lock()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            vectors = list(self._model.embed(list(texts), batch_size=64))
        return [_normalize([float(x) for x in v]) for v in vectors]


_encoder: Encoder | None = None
_encoder_lock = threading.Lock()


def get_encoder() -> Encoder:
    global _encoder
    with _encoder_lock:
        if _encoder is not None:
            return _encoder
        choice = os.getenv("TROVE_ENCODER", "auto").casefold()
        if choice in ("auto", "fastembed"):
            try:
                _encoder = FastEmbedEncoder(
                    os.getenv("TROVE_ENCODER_MODEL", "BAAI/bge-small-en-v1.5"),
                    os.getenv("TROVE_MODEL_DIR", ".models"))
                log.info("encoder loaded", extra={"encoder": _encoder.name})
                return _encoder
            except Exception as exc:
                if choice == "fastembed":
                    raise
                log.warning("fastembed unavailable, using hashing encoder: %s", exc)
        _encoder = HashingEncoder()
        return _encoder


def set_encoder(encoder: Encoder | None) -> None:
    global _encoder
    with _encoder_lock:
        _encoder = encoder


# ------------------------------------------------- zero-shot classification
CATEGORY_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "insurance": ("life insurance premium payment", "policy premium auto debit to insurer",
                  "health insurance renewal premium"),
    "investment": ("mutual fund SIP instalment", "stock broker trade settlement",
                   "dividend credited from company shares"),
    "pension": ("provident fund contribution", "pension credited", "salary credited by employer"),
    "liability": ("loan EMI debited", "credit card bill payment", "home loan instalment"),
    "property": ("property tax payment to municipal corporation", "housing society maintenance",
                 "rent received from tenant"),
    "crypto": ("crypto exchange deposit", "bitcoin purchase", "transfer to crypto wallet"),
    "bank_account": ("fixed deposit interest credited", "transfer to own savings account",
                     "recurring deposit instalment"),
    "none": ("grocery store purchase", "restaurant bill", "fuel station payment",
             "mobile recharge", "ATM cash withdrawal", "online shopping order"),
}
_prototype_cache: dict[str, tuple[str, list[list[float]]]] = {}


def classify(texts: Sequence[str], threshold: float = 0.55, margin: float = 0.03
             ) -> list[tuple[str, float] | None]:
    """Nearest-prototype classification. Returns (category, score) or None."""
    if not texts:
        return []
    encoder = get_encoder()
    labels, vectors = [], []
    for label, examples in CATEGORY_PROTOTYPES.items():
        key = f"{encoder.name}:{label}"
        if key not in _prototype_cache:
            _prototype_cache[key] = (label, encoder.embed(list(examples)))
        for vector in _prototype_cache[key][1]:
            labels.append(label)
            vectors.append(vector)
    results: list[tuple[str, float] | None] = []
    for vector in encoder.embed(list(texts)):
        best: dict[str, float] = {}
        for label, proto in zip(labels, vectors):
            best[label] = max(best.get(label, -1.0), cosine(vector, proto))
        ranked = sorted(best.items(), key=lambda item: -item[1])
        (top, score), (_, second) = ranked[0], ranked[1]
        if top == "none" or score < threshold or score - second < margin:
            results.append(None)
        else:
            results.append((top, round(score, 3)))
    return results


def batched(items: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
