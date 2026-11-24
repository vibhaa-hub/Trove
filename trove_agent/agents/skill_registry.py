"""Skill registry with progressive disclosure.

Level 1  catalog      name + description of every skill (always in context)
Level 2  instructions SKILL.md body, read from disk only when a skill is loaded
Level 3  resources    files under <skill>/reference/, read only on request

Skills replace a zoo of single-purpose LLM "agents": one assistant runtime
loads the instructions and tools it needs for the question in front of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
_RESOURCE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,60}\.md")


@dataclass(frozen=True)
class SkillCard:
    """Level-1 metadata only. The body is not held in memory."""

    name: str
    description: str
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    path: Path
    internal: bool = False
    resources: tuple[str, ...] = field(default_factory=tuple)


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    _, header, body = text.split("---", 2)
    meta = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body.lstrip("\n")


def _split(value: str) -> tuple[str, ...]:
    return tuple(v.strip().casefold() for v in value.split(",") if v.strip())


class SkillRegistry:
    def __init__(self, root: Path = SKILLS_DIR) -> None:
        self.root = root
        self.cards: dict[str, SkillCard] = {}
        for skill_file in sorted(root.glob("*/SKILL.md")):
            meta, _ = _front_matter(skill_file.read_text(encoding="utf-8"))
            name = meta.get("name") or skill_file.parent.name
            reference = skill_file.parent / "reference"
            resources = tuple(sorted(
                p.name for p in reference.glob("*.md") if _RESOURCE_NAME.fullmatch(p.name)
            )) if reference.is_dir() else ()
            self.cards[name] = SkillCard(
                name=name,
                description=meta.get("description", ""),
                triggers=_split(meta.get("triggers", "")),
                tools=_split(meta.get("tools", "")),
                path=skill_file,
                internal=meta.get("internal", "").casefold() == "true",
                resources=resources,
            )

    # ---------------------------------------------------------- level 1
    def public(self) -> list[SkillCard]:
        return [card for card in self.cards.values() if not card.internal]

    def catalog_text(self) -> str:
        return "\n".join(f"- {c.name}: {c.description}" for c in self.public())

    # ---------------------------------------------------------- level 2
    def body(self, name: str) -> str:
        card = self.cards.get(name)
        if card is None:
            raise KeyError(f"Unknown skill: {name}")
        _, body = _front_matter(card.path.read_text(encoding="utf-8"))
        return body

    def offline_summary(self, name: str) -> str:
        match = re.search(r"## Offline summary\s*\n(.+?)(?:\n## |\Z)", self.body(name), re.S)
        return " ".join(match.group(1).split()) if match else ""

    # ---------------------------------------------------------- level 3
    def resource(self, name: str, file_name: str) -> str:
        card = self.cards.get(name)
        if card is None:
            raise KeyError(f"Unknown skill: {name}")
        if file_name.startswith("reference/"):
            file_name = file_name[len("reference/"):]
        if file_name not in card.resources:
            raise KeyError(f"{name} has no resource {file_name!r}; available: {list(card.resources)}")
        path = (card.path.parent / "reference" / file_name).resolve()
        if path.parent != (card.path.parent / "reference").resolve():
            raise KeyError("Resource path is outside the skill")
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------ local router
    def route(self, question: str, limit: int = 2) -> list[str]:
        words = set(re.findall(r"[a-z][a-z-]+", question.casefold()))
        scored = []
        for card in self.public():
            score = sum(1 for t in card.triggers if t in words)
            if score:
                scored.append((score, card.name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in scored[:limit]]


_registry: SkillRegistry | None = None


def registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
