from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CollectionSeed:
    institution: str
    short_name: str
    handle: str
    target_labels: list[str] = field(default_factory=list)


@dataclass
class ThesisItem:
    item_url: str
    handle: str | None = None
    institution: str | None = None
    institution_short: str | None = None
    collection: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    advisors: list[str] = field(default_factory=list)
    degree: str | None = None
    department: str | None = None
    date_accepted: str | None = None
    abstract: str | None = None
    keywords: list[str] = field(default_factory=list)
    file_urls: list[str] = field(default_factory=list)
    language: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassificationResult:
    topic: str
    topic_confidence: float
    external_involvement_code: int
    external_actor_type: str | None
    external_actor_name: str | None
    evidence: str | None
    confidence: float
