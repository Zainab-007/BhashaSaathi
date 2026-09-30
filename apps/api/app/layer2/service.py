from dataclasses import asdict
from collections.abc import Callable
from .normalizer import normalize_text
from .concepts import extract_concepts
from .models import Concept
from .verifier import validate_translation
from .language_codes import validate_language

class Layer2Engine:
    """Pure text intelligence. It has no database, HTTP, audio, or UI dependencies."""
    def __init__(self, translate_fn: Callable[[str,str,str],str]):
        self._translate_fn = translate_fn

    def concepts(self, source_text: str) -> list[Concept]:
        return extract_concepts(normalize_text(source_text))

    def translate_text(self, source_text: str, source_language: str, target_language: str, context: dict | None = None) -> str:
        validate_language(source_language); validate_language(target_language)
        text = normalize_text(source_text)
        if not text: return ''
        return normalize_text(self._translate_fn(text, source_language, target_language))

    def validate_translation(self, source_text: str, translated_text: str, source_language: str, target_language: str, context: dict | None = None) -> dict:
        validate_language(source_language); validate_language(target_language)
        concepts = context.get('concepts', []) if context else []
        concept_objs = [Concept(c.get('concept_id',c.get('id',c.get('label','main_idea'))), c.get('label',c.get('concept_id','Main idea')), c.get('source_span',''), c.get('definition',''), tuple(c.get('keywords',[])), c.get('importance','core')) for c in concepts]
        report = validate_translation(normalize_text(source_text), normalize_text(translated_text), source_language, target_language, concept_objs)
        return asdict(report)
