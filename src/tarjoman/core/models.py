"""Domain enums and the agent I/O contract (SCHEMAS.md sections 1 and 2)."""

from __future__ import annotations

import warnings
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# BookBrief.register is a spec field; ABCMeta.register is not a real attribute clash.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "register" in "BookBrief" shadows an attribute',
    category=UserWarning,
)

# --------------------------------------------------------------------------- enums


class ChapterKind(StrEnum):
    FRONT = "front"
    BODY = "body"
    BACK = "back"


class SegmentKind(StrEnum):
    PROSE = "prose"
    HEADING = "heading"
    VERSE = "verse"
    QUOTE = "quote"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    TABLE = "table"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    DRAFTED = "drafted"
    REVISED = "revised"
    DONE = "done"
    FLAGGED = "flagged"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChapterStatus(StrEnum):
    PENDING = "pending"
    ANALYZED = "analyzed"
    TRANSLATING = "translating"
    TRANSLATED = "translated"
    AUDITED = "audited"
    REVIEWED = "reviewed"
    FINAL = "final"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    PAUSED = "paused"


class PassKind(StrEnum):
    DRAFT = "draft"
    REVISION = "revision"
    REPAIR = "repair"
    HUMAN = "human"


class GlossaryKind(StrEnum):
    CHARACTER = "character"
    PLACE = "place"
    ORGANIZATION = "organization"
    TERM = "term"
    MOTIF = "motif"
    FORM_OF_ADDRESS = "form_of_address"
    TITLE = "title"
    UNIT = "unit"


class GlossaryStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class MqmType(StrEnum):
    OMISSION = "omission"
    ADDITION = "addition"
    MISTRANSLATION = "mistranslation"
    UNTRANSLATED = "untranslated"
    FLUENCY = "fluency"
    TERMINOLOGY = "terminology"
    LOCALE = "locale"
    STYLE = "style"


class RightsBasis(StrEnum):
    PUBLIC_DOMAIN = "public_domain"
    LICENSED = "licensed"
    USER_SUPPLIED = "user_supplied"


AgentSeverity = Literal["minor", "major", "critical"]

AGENT_SEVERITY_MAP: dict[str, Severity] = {
    "minor": Severity.WARN,
    "major": Severity.ERROR,
    "critical": Severity.CRITICAL,
}


def normalize_severity(value: str) -> Severity:
    """Map an agent-facing severity to the persisted four-value vocabulary."""
    try:
        return AGENT_SEVERITY_MAP[value]
    except KeyError as exc:
        raise ValueError(f"unknown agent severity: {value!r}") from exc


LINT_CODES: tuple[str, ...] = tuple(f"L{n:02d}" for n in range(1, 21))

# ------------------------------------------------------------------ agent contracts


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Motif(_Strict):
    motif: str
    why_it_matters: str


class BookBrief(_Strict):
    genre: str
    subgenre: str
    era_and_setting: str
    pov: str
    narrative_tense: str
    narrative_distance: str
    register: str
    register_target_fa: Literal[
        "literary-standard", "formal-classic", "colloquial-narrative", "mixed"
    ]
    address_policy: str
    sentence_rhythm: str
    dialogue_style: str
    figurative_language: str
    humor_irony: str
    cultural_references_policy: str
    name_policy: str
    measurement_policy: str
    foreign_text_policy: str
    taboo_policy: str
    footnote_policy: Literal["none", "sparing", "allowed"]
    style_references: list[str] = Field(default_factory=list, max_length=2)
    do_rules: list[str] = Field(min_length=5, max_length=10)
    dont_rules: list[str] = Field(min_length=5, max_length=10)
    key_motifs: list[Motif] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=5)


class ContinuityFacts(_Strict):
    characters: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    places_and_objects: list[str] = Field(default_factory=list)
    time: list[str] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)


class ChapterSummary(_Strict):
    summary_en: str = Field(min_length=200, max_length=2000)
    continuity: ContinuityFacts
    tone_notes: str
    translation_landmines: list[str] = Field(default_factory=list, max_length=3)


class Entity(_Strict):
    en: str
    kind: GlossaryKind
    surface_forms: list[str] = Field(default_factory=list)
    count: int = Field(ge=1)
    gender: Literal["male", "female", "unknown", "not_applicable"] = "unknown"
    role: str = ""
    context_quote: str = ""
    first_segment_id: str = ""
    needs_translation: bool = True
    notes: str = ""


class EntityBatch(_Strict):
    entities: list[Entity]
    uncertain: list[dict[str, str]] = Field(default_factory=list)


class GlossaryProposal(_Strict):
    en: str
    kind: GlossaryKind
    fa: str
    alternatives: list[str] = Field(default_factory=list, max_length=2)
    transliteration: str = ""
    gender: Literal["male", "female", "unknown", "not_applicable"] = "unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    aliases_fa: list[str] = Field(default_factory=list)
    notes: str = ""


class GlossaryProposals(_Strict):
    proposals: list[GlossaryProposal]
    refuse: list[dict[str, str]] = Field(default_factory=list)


class TranslatedSegment(_Strict):
    id: str
    fa_markup: str = Field(min_length=1)
    notes: str = ""


class GlossarySuggestion(_Strict):
    en: str
    fa: str
    kind: GlossaryKind
    reason: str
    segment_id: str


class WindowTranslation(_Strict):
    segments: list[TranslatedSegment]
    glossary_suggestions: list[GlossarySuggestion] = Field(default_factory=list)
    window_notes: str = ""
    applied: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class TranslatorOutput(_Strict):
    segments: list[TranslatedSegment]
    glossary_suggestions: list[GlossarySuggestion] = Field(default_factory=list)
    window_notes: str = ""


class CritiqueIssue(_Strict):
    segment_id: str
    type: str
    severity: AgentSeverity
    quote_source: str = ""
    quote_target: str = ""
    problem: str
    suggested_fix: str = ""


class Critique(_Strict):
    issues: list[CritiqueIssue] = Field(default_factory=list)
    verdict: Literal["clean", "minor_issues", "major_issues", "needs_redraft"]
    segment_verdicts: dict[str, str] = Field(default_factory=dict)


class LedgerCharacter(_Strict):
    en: str
    fa: str
    role: str = ""
    gender: Literal["male", "female", "unknown"] = "unknown"
    address_forms: dict[str, Literal["تو", "شما"]] = Field(default_factory=dict)
    notes: str = ""


class LedgerUpdate(_Strict):
    characters: list[LedgerCharacter] = Field(default_factory=list)
    terms: list[dict[str, str]] = Field(default_factory=list)
    decisions: list[dict[str, str]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list, max_length=5)
    corrections: list[dict[str, str]] = Field(default_factory=list)


class AuditFinding(_Strict):
    kind: Literal[
        "character", "address", "terminology", "pronoun", "timeline", "physical", "voice"
    ]
    severity: AgentSeverity
    segment_id: str
    evidence: str
    conflicts_with: str = ""
    explanation: str
    suggested_resolution: str = ""


class AuditFindings(_Strict):
    findings: list[AuditFinding] = Field(default_factory=list)
    ledger_corrections: list[dict[str, str]] = Field(default_factory=list)
    verdict: Literal["consistent", "minor_drift", "inconsistent"]


class ReadThroughIssue(_Strict):
    passage: str
    problem: Literal[
        "rhythm", "translationese", "register", "dialogue", "clarity", "imagery", "voice_break"
    ]
    why_it_hurts: str
    suggested_rewrite: str
    severity: AgentSeverity


class ReadThroughFindings(_Strict):
    issues: list[ReadThroughIssue] = Field(max_length=5)
    voice_verdict: Literal["consistent", "drifting", "broken"]
    chapter_impression: str


class JudgeError(_Strict):
    type: MqmType
    severity: Literal["minor", "major", "critical"]
    quote_source: str = ""
    quote_target: str = ""
    comment: str = ""


JUDGE_DIMENSIONS = frozenset(
    {"fidelity", "naturalness", "style_fidelity", "terminology", "cultural_adaptation"}
)


class JudgeVerdict(_Strict):
    scores: dict[str, int]
    errors: list[JudgeError] = Field(default_factory=list)
    verdict: Literal["pass", "minor_issues", "major_issues", "reject"]
    rationale: str

    @model_validator(mode="after")
    def _check_scores(self) -> JudgeVerdict:
        if set(self.scores) != JUDGE_DIMENSIONS:
            raise ValueError(f"scores keys must be exactly {sorted(JUDGE_DIMENSIONS)}")
        for key, value in self.scores.items():
            if not 1 <= value <= 5:
                raise ValueError(f"score {key}={value} outside 1..5")
        return self
