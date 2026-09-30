import re
from .glossary import glossary_matches
from .models import Concept, TranslationValidation
from .language_codes import LANGUAGES


def _numbers(text: str) -> list[str]:
    return re.findall(r'\b\d+(?:[.,]\d+)?\b', text)

def _script_ok(text: str, language: str) -> bool:
    if language == 'eng_Latn':
        return any('a' <= ch.lower() <= 'z' for ch in text) or not text.strip()
    if language == 'hin_Deva':
        return any('\u0900' <= ch <= '\u097f' for ch in text) or not text.strip()
    if language == 'mar_Deva':
        return any('\u0900' <= ch <= '\u097f' for ch in text) or not text.strip()
    if language == 'sat_Olck':
        # Ol Chiki Unicode block: U+1C50–U+1C7F. Allow punctuation/digits/space around it.
        return any('\u1c50' <= ch <= '\u1c7f' for ch in text) or not text.strip()
    return True

def validate_translation(source: str, target: str, source_language: str, target_language: str, concepts: list[Concept]) -> TranslationValidation:
    flags: list[str] = []
    if not target.strip(): flags.append('EMPTY_OUTPUT')
    source_nums, target_nums = _numbers(source), _numbers(target)
    number_mismatch = source_nums != target_nums and bool(source_nums or target_nums)
    if number_mismatch: flags.append('NUMBER_MISMATCH')
    script_anomaly = not _script_ok(target, target_language)
    if script_anomaly: flags.append('TARGET_SCRIPT_ANOMALY')
    source_words = {x.lower() for x in re.findall(r'[A-Za-z]{3,}', source)}
    target_words = {x.lower() for x in re.findall(r'[A-Za-z]{3,}', target)}
    source_leakage = bool(source_words & target_words) and source_language != target_language and len(source_words) > 1
    if source_leakage and target_language != 'eng_Latn': flags.append('SOURCE_LANGUAGE_LEAKAGE')
    missing = glossary_matches(concepts, target, target_language)
    if missing: flags.append('GLOSSARY_TERM_MISSING')
    length_ratio = len(target.split()) / max(1, len(source.split()))
    if length_ratio < 0.25 or length_ratio > 3.5: flags.append('LENGTH_ANOMALY')
    confidence = max(0.20, min(0.96, 0.96 - 0.10*len(flags)))
    publish_blocked = any(f in flags for f in ['EMPTY_OUTPUT','TARGET_SCRIPT_ANOMALY','NUMBER_MISMATCH','SOURCE_LANGUAGE_LEAKAGE'])
    return TranslationValidation(confidence, flags, missing, [], missing, script_anomaly, number_mismatch, source_leakage, publish_blocked)
