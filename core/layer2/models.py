from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class Concept:
    concept_id: str
    label: str
    source_span: str = ''
    definition: str = ''
    keywords: tuple[str, ...] = field(default_factory=tuple)
    importance: str = 'core'

@dataclass(frozen=True)
class TranslationValidation:
    confidence: float
    flags: list[str]
    concepts_missing: list[str]
    concepts_extra: list[str]
    glossary_violations: list[str]
    script_anomaly: bool
    number_mismatch: bool
    source_leakage: bool
    publish_blocked: bool
