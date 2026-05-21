from __future__ import annotations

import re
from dataclasses import asdict

import pandas as pd
from rapidfuzz import fuzz

from .models import ClassificationResult


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(map(str, value))
    return str(value)


def joined_evidence_text(row: pd.Series) -> str:
    fields = [
        row.get("title"),
        row.get("abstract"),
        row.get("keywords"),
        row.get("pdf_text_snippet"),
    ]
    return "\n".join(_as_text(v) for v in fields if _as_text(v)).strip()


def classify_topic(text: str, topic_keywords: dict[str, list[str]]) -> tuple[str, float]:
    text_lower = text.lower()
    best_topic = "unknown"
    best_score = 0

    for topic, keywords in topic_keywords.items():
        score = 0
        for keyword in keywords:
            kw = keyword.lower()
            if kw in text_lower:
                score = max(score, 100)
            else:
                score = max(score, fuzz.partial_ratio(kw, text_lower[:5000]))
        if score > best_score:
            best_topic = topic
            best_score = score

    confidence = min(1.0, best_score / 100)
    if confidence < 0.65:
        return "unknown", confidence
    return best_topic, confidence


def find_evidence_sentence(text: str, patterns: list[str]) -> tuple[str | None, str | None]:
    sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    lowered_patterns = [p.lower() for p in patterns]
    for sent in sentences:
        low = sent.lower()
        if any(p in low for p in lowered_patterns):
            return sent[:500], next((p for p in patterns if p.lower() in low), None)
    return None, None


def classify_external_involvement(
        text: str,
        external_patterns: dict[str, list[str]],
) -> tuple[int, str | None, str | None, str | None, float]:
    all_patterns = [p for group in external_patterns.values() for p in group]
    evidence, matched = find_evidence_sentence(text, all_patterns)

    if not evidence:
        return 0, None, None, None, 0.6

    lowered_evidence = evidence.lower()

    for group in ("companies", "public_sector"):
        for pattern in external_patterns.get(group, []):
            if pattern.lower() in lowered_evidence:
                actor_type = "company" if group == "companies" else "public institution"
                return 3, actor_type, pattern, evidence, 0.85

    for pattern in external_patterns.get("data_from", []):
        if pattern.lower() in lowered_evidence:
            return 2, "external data source", None, evidence, 0.75

    for group in ("collaboration", "done_for"):
        for pattern in external_patterns.get(group, []):
            if pattern.lower() in lowered_evidence:
                return 3, "external organisation", None, evidence, 0.8

    return 1, "external actor/context", matched, evidence, 0.65


def classify_dataframe(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    records: list[dict] = []
    topic_keywords = config.get("topic_keywords", {})
    external_patterns = config.get("external_involvement_patterns", {})

    for _, row in df.iterrows():
        text = joined_evidence_text(row)
        topic, topic_conf = classify_topic(text, topic_keywords)
        code, actor_type, actor_name, evidence, ext_conf = classify_external_involvement(
            text, external_patterns
        )

        records.append(
            asdict(
                ClassificationResult(
                    topic=topic,
                    topic_confidence=topic_conf,
                    external_involvement_code=code,
                    external_actor_type=actor_type,
                    external_actor_name=actor_name,
                    evidence=evidence,
                    confidence=ext_conf,
                )
            )
        )

    out = df.copy()
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(records)], axis=1)
