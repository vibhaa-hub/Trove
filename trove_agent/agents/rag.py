"""Retrieval-augmented knowledge base over skills and knowledge files.

Sources: every SKILL.md body, every skill reference file, and markdown files
in ``trove_agent/knowledge/``. Text is split into heading-aware chunks,
embedded with the local encoder, and cached on disk keyed by a content hash so
restarts are instant. Retrieval is exact cosine search (the corpus is small;
swap in pgvector when it grows).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from trove_agent.agents.encoders import cosine, get_encoder
from trove_agent.agents.skill_registry import SKILLS_DIR, SkillRegistry, registry

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
CHUNK_CHARS = 700


@dataclass(frozen=True)
class Chunk:
    source: str      # e.g. "skill:legacy-release" or "knowledge:claims-by-asset.md"
    skill: str | None
    heading: str
    text: str


def _chunks(source: str, skill: str | None, text: str) -> list[Chunk]:
    out: list[Chunk] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            for start in range(0, len(body), CHUNK_CHARS):
                out.append(Chunk(source, skill, heading, body[start:start + CHUNK_CHARS]))
        buffer.clear()

    for line in text.splitlines():
        if re.match(r"#{1,3} ", line):
            flush()
            heading = line.lstrip("# ").strip()
        else:
            buffer.append(line)
    flush()
    return out


class KnowledgeIndex:
    def __init__(self, skills: SkillRegistry | None = None,
                 cache_dir: str | None = None) -> None:
        self.skills = skills or registry()
        self.cache_dir = Path(cache_dir or os.getenv("TROVE_MODEL_DIR", ".models"))
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []
        self._lock = threading.Lock()
        self._built = False

    def _collect(self) -> list[Chunk]:
        chunks: list[Chunk] = []
        for card in self.skills.cards.values():
            if card.internal:
                continue
            chunks += _chunks(f"skill:{card.name}", card.name, self.skills.body(card.name))
            for resource in card.resources:
                chunks += _chunks(f"skill:{card.name}/{resource}", card.name,
                                  self.skills.resource(card.name, resource))
        if KNOWLEDGE_DIR.is_dir():
            for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
                chunks += _chunks(f"knowledge:{path.name}", None, path.read_text(encoding="utf-8"))
        return chunks

    def build(self) -> None:
        with self._lock:
            if self._built:
                return
            encoder = get_encoder()
            chunks = self._collect()
            digest = hashlib.sha256(
                (encoder.name + "".join(c.source + c.heading + c.text for c in chunks)).encode()
            ).hexdigest()[:16]
            cache = self.cache_dir / f"rag-{digest}.json"
            vectors = None
            if cache.exists():
                try:
                    vectors = json.loads(cache.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    vectors = None
            if vectors is None or len(vectors) != len(chunks):
                vectors = encoder.embed([f"{c.heading}\n{c.text}" for c in chunks])
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(vectors), encoding="utf-8")
                except OSError:
                    pass
            self.chunks, self.vectors, self._built = chunks, vectors, True

    def search(self, query: str, k: int = 4, skill: str | None = None,
               min_score: float = 0.0) -> list[dict]:
        self.build()
        if not self.chunks:
            return []
        query_vector = get_encoder().embed([query])[0]
        scored = []
        for chunk, vector in zip(self.chunks, self.vectors):
            if skill and chunk.skill != skill:
                continue
            score = cosine(query_vector, vector)
            if score >= min_score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: -item[0])
        return [{"source": c.source, "heading": c.heading, "text": c.text, "score": round(s, 3)}
                for s, c in scored[:k]]

    def route_skills(self, question: str, limit: int = 2) -> list[str]:
        """Semantic routing: skills whose chunks best match the question."""
        best: dict[str, float] = {}
        for hit in self.search(question, k=12):
            skill = hit["source"].split(":", 1)[1].split("/")[0] if hit["source"].startswith("skill:") else None
            if skill:
                best[skill] = max(best.get(skill, 0.0), hit["score"])
        return [name for name, _ in sorted(best.items(), key=lambda item: -item[1])[:limit]]


_index: KnowledgeIndex | None = None


def index() -> KnowledgeIndex:
    global _index
    if _index is None:
        _index = KnowledgeIndex()
    return _index


__all__ = ["KnowledgeIndex", "index", "SKILLS_DIR"]
