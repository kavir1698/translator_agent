"""A3 EntityExtractor plus the deterministic capitalized-token scan (plan 6.1.4)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from tarjoman.core.cost import TierProfile
from tarjoman.core.db import Database
from tarjoman.core.glossary import normalize_term_en
from tarjoman.core.models import Entity, EntityBatch, GlossaryKind
from tarjoman.core.text import split_sentences_en
from tarjoman.llm.cache import ResponseCache
from tarjoman.llm.promptlib import load as load_prompt
from tarjoman.llm.provider import Provider

from .base import run_agent

AGENT = "A3"
PROMPT_ID = "p03_entities"
BATCH_TOKENS = 4000
MAX_ENTITIES = 400

_TOKEN = re.compile(r"[A-Za-z][A-Za-z\u00C0-\u024F'’\-]*")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)

_COMMON_CAPITALIZED = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "but",
    "by",
    "chapter",
    "for",
    "from",
    "had",
    "has",
    "he",
    "her",
    "here",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "later",
    "my",
    "no",
    "not",
    "now",
    "of",
    "on",
    "one",
    "or",
    "our",
    "part",
    "she",
    "so",
    "some",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "two",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
    "your",
    "after",
    "before",
    "during",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}

_PROPER_KINDS = {
    GlossaryKind.CHARACTER,
    GlossaryKind.PLACE,
    GlossaryKind.ORGANIZATION,
    GlossaryKind.FORM_OF_ADDRESS,
    GlossaryKind.TITLE,
    GlossaryKind.MOTIF,
}

_KIND_PRIORITY = {
    GlossaryKind.CHARACTER: 8,
    GlossaryKind.PLACE: 7,
    GlossaryKind.ORGANIZATION: 6,
    GlossaryKind.FORM_OF_ADDRESS: 5,
    GlossaryKind.TITLE: 4,
    GlossaryKind.MOTIF: 3,
    GlossaryKind.TERM: 2,
    GlossaryKind.UNIT: 1,
}


def _display(surface: str) -> str:
    return _POSSESSIVE.sub("", surface).strip()


def _is_capitalized_word(token: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Z][A-Za-z\u00C0-\u024F]+(?:['’\-][A-Za-z\u00C0-\u024F]+)*", token
        )
    )


def _is_acronym(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2,}", token))


@dataclass
class _Occurrence:
    surface: str
    count: int = 0
    mid_sentence: int = 0
    first_segment_id: str = ""


def scan_proper_nouns(segments: Sequence[Any]) -> list[Entity]:
    """Deterministic English proper-noun list; no model, no network."""
    occurrences: dict[str, _Occurrence] = {}
    for segment in segments:
        segment_id = str(segment["id"])
        for sentence in split_sentences_en(str(segment["source_text"])):
            tokens = list(_TOKEN.finditer(sentence))
            for index, match in enumerate(tokens):
                token = match.group(0)
                if not (_is_capitalized_word(token) or _is_acronym(token)):
                    continue
                surface = _display(token)
                lowered = surface.casefold()
                if lowered in _COMMON_CAPITALIZED or len(surface) < 2:
                    continue
                occurrence = occurrences.setdefault(surface, _Occurrence(surface=surface))
                occurrence.count += 1
                if index > 0:
                    occurrence.mid_sentence += 1
                if not occurrence.first_segment_id:
                    occurrence.first_segment_id = segment_id
    entities = [
        Entity(
            en=item.surface,
            kind=GlossaryKind.TERM,
            surface_forms=[item.surface],
            count=item.count,
            first_segment_id=item.first_segment_id,
            needs_translation=True,
        )
        for item in occurrences.values()
        if item.mid_sentence >= 1 or item.count >= 2
    ]
    entities.sort(key=lambda entity: (-entity.count, entity.en.casefold()))
    return entities[:MAX_ENTITIES]


def _kind(priority: str) -> int:
    try:
        return _KIND_PRIORITY[GlossaryKind(priority)]
    except ValueError:
        return 0


@dataclass
class _Group:
    key: str
    en: str
    kind: str
    best_count: int
    count: int
    surfaces: set[str] = field(default_factory=set)
    gender: str = "unknown"
    role: str = ""
    context_quote: str = ""
    first_segment_id: str = ""
    notes: list[str] = field(default_factory=list)
    needs_translation: bool = True


def _new_group(entity: Entity) -> _Group:
    return _Group(
        key=normalize_term_en(entity.en),
        en=entity.en,
        kind=entity.kind.value if isinstance(entity.kind, GlossaryKind) else str(entity.kind),
        best_count=entity.count,
        count=entity.count,
        surfaces={entity.en, *entity.surface_forms},
        gender=entity.gender,
        role=entity.role,
        context_quote=entity.context_quote,
        first_segment_id=entity.first_segment_id,
        notes=[entity.notes] if entity.notes else [],
        needs_translation=entity.needs_translation,
    )


def _merge_one(group: _Group, entity: Entity) -> None:
    key = normalize_term_en(entity.en)
    if key == group.key:
        group.count = max(group.count, entity.count)
    else:
        group.count += entity.count
    if entity.count > group.best_count:
        group.best_count = entity.count
        group.en = entity.en
    kind = entity.kind.value if isinstance(entity.kind, GlossaryKind) else str(entity.kind)
    if _kind(kind) > _kind(group.kind):
        group.kind = kind
    group.surfaces.update({entity.en, *entity.surface_forms})
    if group.gender == "unknown" and entity.gender != "unknown":
        group.gender = entity.gender
    group.role = group.role or entity.role
    group.context_quote = group.context_quote or entity.context_quote
    if entity.first_segment_id and (
        not group.first_segment_id or entity.first_segment_id < group.first_segment_id
    ):
        group.first_segment_id = entity.first_segment_id
    if entity.notes and entity.notes not in group.notes:
        group.notes.append(entity.notes)
    group.needs_translation = group.needs_translation or entity.needs_translation


def merge_entities(scan: Sequence[Entity], extracted: Sequence[Entity]) -> list[Entity]:
    """Cluster by token-set similarity >= 90; keep the most frequent surface."""
    groups: list[_Group] = []
    for entity in sorted(
        [*scan, *extracted], key=lambda item: (-item.count, item.en.casefold())
    ):
        key = normalize_term_en(entity.en)
        if not key:
            continue
        target = next(
            (
                group
                for group in groups
                if fuzz.token_set_ratio(key, group.key) >= 90
            ),
            None,
        )
        if target is None:
            groups.append(_new_group(entity))
        else:
            _merge_one(target, entity)
    force_keep = {normalize_term_en(entity.en) for entity in scan}
    merged = [
        Entity(
            en=group.en,
            kind=GlossaryKind(group.kind),
            surface_forms=sorted(group.surfaces, key=str.casefold),
            count=max(1, group.count),
            gender=group.gender,  # type: ignore[arg-type]
            role=group.role,
            context_quote=group.context_quote,
            first_segment_id=group.first_segment_id,
            needs_translation=group.needs_translation,
            notes="; ".join(group.notes),
        )
        for group in groups
        if group.count >= 3
        or GlossaryKind(group.kind) in _PROPER_KINDS
        or group.key in force_keep
    ]
    merged.sort(key=lambda entity: (-entity.count, entity.en.casefold()))
    return merged[:MAX_ENTITIES]


def _batches(segments: Sequence[Any]) -> list[list[Any]]:
    batches: list[list[Any]] = []
    current: list[Any] = []
    running = 0
    for segment in segments:
        tokens = max(1, int(segment["tokens_est"]))
        if current and running + tokens > BATCH_TOKENS:
            batches.append(current)
            current = []
            running = 0
        current.append(segment)
        running += tokens
    if current:
        batches.append(current)
    return batches


class EntityExtractor:
    prompt_id = PROMPT_ID
    agent = AGENT

    def extract(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        segments: Sequence[Any],
        use_llm: bool,
    ) -> list[Entity]:
        scan = scan_proper_nouns(segments)
        if not use_llm:
            return scan
        prompt = load_prompt(self.prompt_id)
        known = sorted({entity.en for entity in scan}, key=str.casefold)
        extracted: list[Entity] = []
        for index, batch in enumerate(_batches(segments), start=1):
            source_text = "\n\n".join(
                f"### {segment['id']}\n{segment['source_text']}" for segment in batch
            )
            result = run_agent(
                provider=provider,
                prompt=prompt,
                schema_model=EntityBatch,
                variables={
                    "batch_index": index,
                    "source_text": source_text,
                    "known_entities": json.dumps(known, ensure_ascii=False),
                },
                tier=tier,
                db=db,
                run_id=run_id,
                cache=cache,
            )
            extracted.extend(result.value.entities)
            known = sorted(
                {*known, *(entity.en for entity in result.value.entities)}, key=str.casefold
            )
        return merge_entities(scan, extracted)
