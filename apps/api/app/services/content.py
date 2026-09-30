from __future__ import annotations

import json
from datetime import datetime, timezone
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..layer2.concepts import extract_concepts
from ..models import LessonVersion, PracticeQuestion


def now():
    return datetime.now(timezone.utc)


def concepts_as_json(concepts):
    return json.dumps([
        {
            'concept_id': c.concept_id,
            'label': c.label,
            'source_span': c.source_span,
            'definition': c.definition,
            'keywords': list(c.keywords),
            'importance': c.importance,
        }
        for c in concepts
    ], ensure_ascii=False)


def analyze_source(text: str, grade: str, subject: str, topic: str = '') -> dict:
    concepts = extract_concepts(text)
    inferred_topic = topic or next((c.label for c in concepts if c.concept_id != 'main_idea'), 'New Lesson')
    low = text.lower()
    outcome = f'Learner can explain the main idea of {inferred_topic.lower()} using the key concepts.'
    if ('water' in low or 'पानी' in text) and ('plant' in low or 'पौध' in text):
        outcome = 'Learner can explain that plants need water to live and grow.'
    explanation = (
        f'For {grade} {subject}: explain the teacher lesson using the approved source '
        f'and these concepts: {", ".join(c.label for c in concepts)}.'
    )
    activities = [
        'Ask the learner to explain the main idea in their own words.',
        'Ask for one real-world example from the learner’s surroundings.',
    ]
    return {
        'topic': inferred_topic,
        'concepts': [c.__dict__ for c in concepts],
        'learning_objective': outcome,
        'explanation': explanation,
        'activities': activities,
    }


def _english_question(concept_label: str) -> list[str]:
    return [
        f'Why is “{concept_label}” important in this lesson?',
        concept_label,
        'It is unrelated to the lesson',
        'I am not sure',
        f'This question checks understanding of the concept “{concept_label}”.',
    ]


def ensure_practice(
    db: Session,
    version: LessonVersion,
    language: str = 'hin_Deva',
    source_language: str = 'eng_Latn',
    count: int = 4,
    approved: bool = False,
    translate_fn: Callable[[str, str, str], str] | None = None,
    translate_many_fn: Callable[[list[str], str, str], list[str]] | None = None,
):
    concepts = json.loads(version.concepts_json or '[]')
    existing = db.query(PracticeQuestion).filter_by(lesson_version_id=version.id, language=language).order_by(PracticeQuestion.id.asc()).all()
    if existing:
        return existing[:count]

    concepts = concepts[: max(1, min(count, 10))]
    canonical: list[str] = []
    concept_ids: list[str] = []
    for concept in concepts:
        label = concept.get('label') or concept.get('concept_id') or 'main idea'
        concept_ids.append(concept.get('concept_id', label))
        canonical.extend(_english_question(label))

    if language == 'eng_Latn':
        rendered = canonical
    else:
        if translate_many_fn is not None:
            rendered = translate_many_fn(canonical, 'eng_Latn', language)
        elif translate_fn is not None:
            rendered = [translate_fn(text, 'eng_Latn', language) for text in canonical]
        else:
            raise RuntimeError('A local translation function is required to draft non-English practice.')

    rows: list[PracticeQuestion] = []
    for index, concept_id in enumerate(concept_ids):
        values = rendered[index * 5:index * 5 + 5]
        if len(values) != 5:
            continue
        rows.append(PracticeQuestion(
            lesson_version_id=version.id,
            concept_id=concept_id,
            prompt=values[0],
            options_json=json.dumps(values[1:4], ensure_ascii=False),
            correct_answer=values[1],
            explanation=values[4],
            language=language,
            question_type='mcq',
            approved=approved,
        ))

    if rows:
        db.add_all(rows)
        db.commit()
        for row in rows:
            db.refresh(row)
    return rows


def evaluate_attempt(version: LessonVersion, answers: dict, questions):
    results = {}
    hits = 0
    total = max(1, len(questions))
    for question in questions:
        answer = str(answers.get(str(question.id), '')).strip().lower()
        expected = question.correct_answer.strip().lower()
        ok = answer == expected
        results[question.concept_id] = {
            'understood': ok,
            'answer': answers.get(str(question.id)),
        }
        hits += 1 if ok else 0
    return round(hits * 100 / total, 1), results
