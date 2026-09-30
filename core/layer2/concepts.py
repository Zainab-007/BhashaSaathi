import re
from .models import Concept

RULES = [
    ('water', ('water','पानी','जल','watered')),
    ('plants', ('plant','plants','पौध','पौधे','पेड़')),
    ('growth', ('grow','growth','बढ़','विकास')),
    ('need', ('need','needs','आवश्यक','जरूरत')),
    ('sunlight', ('sun','sunlight','सूरज','धूप')),
]

def extract_concepts(text: str) -> list[Concept]:
    concepts: list[Concept] = []
    for slug, words in RULES:
        hit = next((w for w in words if w.lower() in text.lower()), None)
        if hit:
            concepts.append(Concept(concept_id=slug, label=slug.replace('_',' ').title(), source_span=hit, keywords=tuple(words)))
    if not concepts and text.strip():
        first = re.split(r'[.!?\n]', text.strip())[0][:100]
        concepts.append(Concept(concept_id='main_idea', label='Main idea', source_span=first, keywords=(first,)))
    return concepts
