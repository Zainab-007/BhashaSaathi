from .models import Concept

# Curated classroom glossary for the Marathi prototype.
GLOSSARY = {
    'water': {'eng_Latn':'water','hin_Deva':'पानी','mar_Deva':'पाणी'},
    'plants': {'eng_Latn':'plants','hin_Deva':'पौधे','mar_Deva':'झाडे'},
    'growth': {'eng_Latn':'growth','hin_Deva':'विकास','mar_Deva':'वाढ'},
    'need': {'eng_Latn':'need','hin_Deva':'आवश्यकता','mar_Deva':'गरज'},
    'sunlight': {'eng_Latn':'sunlight','hin_Deva':'सूरज की रोशनी','mar_Deva':'सूर्यप्रकाश'},
}

def preferred_term(concept_id: str, language: str) -> str:
    return GLOSSARY.get(concept_id, {}).get(language, '')

def glossary_matches(concepts: list[Concept], target_text: str, target_language: str) -> list[str]:
    lower = target_text.lower()
    missing = []
    for c in concepts:
        term = preferred_term(c.concept_id, target_language)
        if term and term.lower() not in lower:
            missing.append(c.concept_id)
    return missing
