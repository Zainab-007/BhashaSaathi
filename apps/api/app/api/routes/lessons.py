import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...core.config import STORAGE_ROOT, settings
from ...core.security import current_user
from ...db import get_db
from ...layer2.service import Layer2Engine
from ...models import Artifact, Group, Lesson, LessonVersion, Membership, PracticeQuestion, Translation, TranslationFeedback, PreparationJob
from ...schemas import (
    LessonCreateIn,
    LessonPatchIn,
    PracticeApprovalIn,
    PracticeGenerateIn,
    TTSIn,
    TranslateIn,
    VerifyIn,
    VersionCreateIn,
)
from ...services.artifacts import register_file, sha256_file
from ...services.content import analyze_source, ensure_practice, concepts_as_json
from ...services.model_manager import manager
from ...services.publish import publish
from ...services.worksheet import make_worksheet
from ...services.background_prep import enqueue_retry, preparation_views
from ...layer2.concepts import extract_concepts
from ...layer2.normalizer import normalize_text

router = APIRouter(tags=['lessons'])
logger = logging.getLogger('bhashasaathi.lessons')


def db_lesson(db: Session, lesson_id: int) -> Lesson:
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(404, detail={'code': 'LESSON_NOT_FOUND', 'message': 'Lesson not found.'})
    return lesson


def current_version(db: Session, lesson: Lesson) -> LessonVersion:
    if not lesson.current_version_id:
        raise HTTPException(400, detail={'code': 'NO_VERSION', 'message': 'This lesson has no active version yet.'})
    version = db.get(LessonVersion, lesson.current_version_id)
    if not version:
        raise HTTPException(500, detail={'code': 'VERSION_MISSING', 'message': 'The active lesson version is missing.'})
    return version


def require_group_owner(db: Session, lesson: Lesson, user) -> Group:
    group = db.get(Group, lesson.group_id)
    if not group or group.owner_id != user.id:
        raise HTTPException(403, detail={'code': 'LESSON_EDIT_DENIED', 'message': 'You do not have permission to edit this lesson.'})
    return group


def can_read_lesson(db: Session, lesson: Lesson, user) -> bool:
    group = db.get(Group, lesson.group_id)
    if not group:
        return False
    if group.owner_id == user.id:
        return True
    return bool(db.query(Membership).filter_by(group_id=lesson.group_id, user_id=user.id).first()) and lesson.status == 'PUBLISHED'


def lesson_summary(db: Session, lesson: Lesson) -> dict:
    version = db.get(LessonVersion, lesson.current_version_id) if lesson.current_version_id else None
    return {
        'id': lesson.id,
        'group_id': lesson.group_id,
        'title': lesson.title,
        'grade': lesson.grade,
        'subject': lesson.subject,
        'topic': lesson.topic,
        'source_language': lesson.source_language,
        'status': lesson.status,
        'current_version_id': lesson.current_version_id,
        'version_number': version.version_number if version else None,
        'updated_at': lesson.updated_at.isoformat() if lesson.updated_at else None,
    }


def version_view(version: LessonVersion) -> dict:
    return {
        'id': version.id,
        'version_number': version.version_number,
        'source_text': version.source_text,
        'clean_transcript': version.clean_transcript,
        'learning_objectives': json.loads(version.learning_objectives_json or '[]'),
        'concepts': json.loads(version.concepts_json or '[]'),
        'ai_explanation': json.loads(version.ai_explanation_json or '{}'),
        'activities': json.loads(version.activities_json or '[]'),
        'approved_at': version.approved_at.isoformat() if version.approved_at else None,
        'published_at': version.published_at.isoformat() if version.published_at else None,
    }


def _practice_view(question: PracticeQuestion) -> dict:
    """Full practice view for teacher-side lesson management (includes correct_answer)."""
    return {
        'id': question.id,
        'concept_id': question.concept_id,
        'prompt': question.prompt,
        'options': json.loads(question.options_json or '[]'),
        'correct_answer': question.correct_answer,
        'explanation': question.explanation,
        'language': question.language,
        'question_type': question.question_type,
        'approved': bool(question.approved),
    }


def _practice_student_view(question: PracticeQuestion) -> dict:
    """Student-safe practice view. Never includes correct_answer or explanation.

    Evaluation is always performed server-side via POST /student/attempts.
    """
    return {
        'id': question.id,
        'concept_id': question.concept_id,
        'prompt': question.prompt,
        'options': json.loads(question.options_json or '[]'),
        'language': question.language,
        'question_type': question.question_type,
        'approved': bool(question.approved),
    }


def pack(db: Session, lesson: Lesson, *, include_pending: bool = False, viewer_id: int | None = None, include_worksheet: bool = False) -> dict:
    version = current_version(db, lesson)
    translations = db.query(Translation).filter_by(lesson_version_id=version.id).order_by(Translation.target_language).all()
    artifacts = db.query(Artifact).filter_by(lesson_version_id=version.id).order_by(Artifact.id.desc()).all()
    all_questions = db.query(PracticeQuestion).filter_by(lesson_version_id=version.id).order_by(PracticeQuestion.id.asc()).all()
    visible_questions = all_questions if include_pending else [q for q in all_questions if q.approved]

    worksheet = make_worksheet(lesson, version, translations) if include_worksheet else None

    feedback_rows = db.query(TranslationFeedback).filter_by(lesson_version_id=version.id).all()
    feedback_counts: dict[int, dict[str, int]] = {}
    viewer_feedback: dict[int, str] = {}
    for feedback in feedback_rows:
        item = feedback_counts.setdefault(feedback.translation_id, {'confirmed': 0, 'reported': 0})
        if feedback.status == 'CONFIRMED':
            item['confirmed'] += 1
        elif feedback.status == 'REPORTED':
            item['reported'] += 1
        if viewer_id is not None and feedback.student_id == viewer_id:
            viewer_feedback[feedback.translation_id] = feedback.status

    translations_view = [
        {
            'id': item.id,
            'language': item.target_language,
            'text': item.translated_text,
            'confidence': item.confidence,
            'flags': json.loads(item.flags_json or '[]'),
            'validation': json.loads(item.validation_json or '{}'),
            'verification_status': item.verification_status,
            'teacher_edited': item.teacher_edited,
            'native_review_status': item.native_review_status,
            'student_feedback': feedback_counts.get(item.id, {'confirmed': 0, 'reported': 0}),
            'my_student_feedback': viewer_feedback.get(item.id),
        }
        for item in translations
    ]
    # Use student-safe practice views whenever a specific viewer is identified
    # (i.e. a student is requesting the lesson). The student view never includes
    # correct_answer or explanation — evaluation happens server-side.
    practice_view_fn = _practice_student_view if viewer_id is not None else _practice_view
    return {
        'lesson': lesson_summary(db, lesson),
        'version': version_view(version),
        'worksheet_html': worksheet['html'] if worksheet else '',
        'worksheet_meta': ({k: worksheet[k] for k in ('learning_outcome', 'concepts', 'language_a', 'language_b')} if worksheet else {}),
        'translations': translations_view,
        'artifacts': [
            {
                'id': artifact.id,
                'type': artifact.type,
                'language': artifact.language,
                'mime_type': artifact.mime_type,
                'size_bytes': artifact.size_bytes,
                'sha256': artifact.sha256,
                'url': f'/api/v1/artifacts/{artifact.id}',
            }
            for artifact in artifacts
        ],
        'practice': [practice_view_fn(q) for q in visible_questions if q.approved],
        'practice_all': [_practice_view(q) for q in all_questions] if include_pending else [],
        'preparations': preparation_views(db, version.id),
    }

def concept_rows(values: list[str]) -> list[dict]:
    result = []
    for value in values:
        label = value.strip()
        if not label:
            continue
        result.append({'concept_id': label.lower().replace(' ', '_'), 'label': label, 'keywords': [label], 'importance': 'core'})
    return result


@router.post('/groups/{group_id}/lessons')
def create_lesson(group_id: int, x: LessonCreateIn, db: Session = Depends(get_db), user=Depends(current_user)):
    group = db.get(Group, group_id)
    if not group:
        raise HTTPException(404, detail={'code': 'GROUP_NOT_FOUND', 'message': 'Classroom not found.'})
    if group.owner_id != user.id:
        raise HTTPException(403, detail={'code': 'GROUP_EDIT_DENIED', 'message': 'You do not own this classroom.'})
    title = x.title.strip()
    source = x.source_text.strip()
    lesson = Lesson(
        group_id=group_id,
        title=title,
        grade=x.grade.strip(),
        subject=x.subject.strip(),
        topic=x.topic.strip(),
        source_language=x.source_language,
        status='DRAFT',
        created_by=user.id,
    )
    db.add(lesson)
    db.flush()
    version = LessonVersion(
        lesson_id=lesson.id,
        version_number=1,
        source_text=source,
        clean_transcript=source,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    lesson.current_version_id = version.id
    db.commit()
    db.refresh(lesson)
    return lesson_summary(db, lesson)


@router.get('/groups/{group_id}/lessons')
def list_lessons(group_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    group = db.get(Group, group_id)
    if not group:
        raise HTTPException(404, detail={'code': 'GROUP_NOT_FOUND', 'message': 'Classroom not found.'})
    is_owner = group.owner_id == user.id
    is_member = bool(db.query(Membership).filter_by(group_id=group_id, user_id=user.id).first())
    if not (is_owner or is_member):
        raise HTTPException(403, detail={'code': 'GROUP_ACCESS_DENIED', 'message': 'You are not a member of this classroom.'})
    query = db.query(Lesson).filter(Lesson.group_id == group_id)
    # Classroom drafts are private to the classroom owner. Membership—regardless
    # of the member's global account role—only grants access to published lessons.
    if not is_owner:
        query = query.filter(Lesson.status == 'PUBLISHED')
    return [lesson_summary(db, lesson) for lesson in query.order_by(Lesson.updated_at.desc()).all()]


@router.get('/lessons/{lesson_id}')
def get_lesson(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    if not can_read_lesson(db, lesson, user):
        raise HTTPException(403, detail={'code': 'LESSON_ACCESS_DENIED', 'message': 'This lesson is not available to your account.'})
    include_pending = bool(db.query(Group).filter_by(id=lesson.group_id, owner_id=user.id).first())
    return pack(db, lesson, include_pending=include_pending, viewer_id=user.id)


@router.patch('/lessons/{lesson_id}')
def patch_lesson(lesson_id: int, x: LessonPatchIn, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    changes_requested = any(value is not None for value in (x.source_text, x.clean_transcript, x.learning_outcome, x.concepts, x.source_language)) or x.title is not None or x.topic is not None

    if lesson.status == 'PUBLISHED' and changes_requested:
        outcome = [x.learning_outcome] if x.learning_outcome is not None else json.loads(version.learning_objectives_json or '[]')
        concepts = concept_rows(x.concepts) if x.concepts is not None else json.loads(version.concepts_json or '[]')
        new_version = LessonVersion(
            lesson_id=lesson.id,
            version_number=version.version_number + 1,
            source_text=x.source_text if x.source_text is not None else version.source_text,
            clean_transcript=x.clean_transcript if x.clean_transcript is not None else version.clean_transcript,
            learning_objectives_json=json.dumps(outcome, ensure_ascii=False),
            concepts_json=json.dumps(concepts, ensure_ascii=False),
            created_by=user.id,
        )
        db.add(new_version)
        db.flush()
        lesson.current_version_id = new_version.id
        lesson.status = 'DRAFT'
        if x.title is not None:
            lesson.title = x.title.strip()
        if x.topic is not None:
            lesson.topic = x.topic.strip()
        if x.source_language is not None:
            lesson.source_language = x.source_language
        db.commit()
        return pack(db, lesson, include_pending=True)

    existing_outcome = json.loads(version.learning_objectives_json or '[]')
    existing_concepts = json.loads(version.concepts_json or '[]')
    incoming_concepts = concept_rows(x.concepts) if x.concepts is not None else existing_concepts
    incoming_outcome = [x.learning_outcome.strip()] if x.learning_outcome is not None and x.learning_outcome.strip() else ([] if x.learning_outcome is not None else existing_outcome)
    source_changed = (
        (x.source_text is not None and x.source_text != version.source_text)
        or (x.clean_transcript is not None and x.clean_transcript != version.clean_transcript)
        or (x.source_language is not None and x.source_language != lesson.source_language)
        or (x.learning_outcome is not None and incoming_outcome != existing_outcome)
        or (x.concepts is not None and incoming_concepts != existing_concepts)
    )
    if source_changed:
        # Generated translations, practice, audio and preparation jobs belong to
        # the exact source/version. Invalidate all downstream work before saving
        # the new source so old workers cannot publish stale derivative assets.
        db.query(Translation).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(Artifact).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(PracticeQuestion).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        jobs = db.query(PreparationJob).filter_by(lesson_version_id=version.id).all()
        for job in jobs:
            job.status = 'FAILED'
            job.error = 'Preparation invalidated because the lesson source changed.'
            job.finished_at = datetime.now(timezone.utc)

    if x.title is not None:
        lesson.title = x.title.strip()
    if x.topic is not None:
        lesson.topic = x.topic.strip()
    if x.source_language is not None:
        lesson.source_language = x.source_language
    if x.source_text is not None:
        version.source_text = x.source_text
    if x.clean_transcript is not None:
        version.clean_transcript = x.clean_transcript
    if x.learning_outcome is not None:
        version.learning_objectives_json = json.dumps([x.learning_outcome.strip()] if x.learning_outcome.strip() else [], ensure_ascii=False)
    if x.concepts is not None:
        version.concepts_json = json.dumps(concept_rows(x.concepts), ensure_ascii=False)
    if lesson.status not in {'PUBLISHED'}:
        lesson.status = 'DRAFT'
    db.commit()
    return pack(db, lesson, include_pending=True)


@router.delete('/lessons/{lesson_id}')
def delete_lesson(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    versions = db.query(LessonVersion).filter_by(lesson_id=lesson.id).all()
    lesson.current_version_id = None
    db.flush()
    for version in versions:
        db.query(Translation).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(Artifact).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(PracticeQuestion).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.delete(version)
    db.delete(lesson)
    db.commit()
    return {'ok': True}


@router.post('/lessons/{lesson_id}/versions')
def new_version(lesson_id: int, x: VersionCreateIn, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    previous = current_version(db, lesson)
    outcome = [x.learning_outcome] if x.learning_outcome else json.loads(previous.learning_objectives_json or '[]')
    concepts = concept_rows(x.concepts) if x.concepts is not None else json.loads(previous.concepts_json or '[]')
    version = LessonVersion(
        lesson_id=lesson.id,
        version_number=previous.version_number + 1,
        source_text=x.source_text if x.source_text is not None else previous.source_text,
        clean_transcript=x.clean_transcript if x.clean_transcript is not None else previous.clean_transcript,
        learning_objectives_json=json.dumps(outcome, ensure_ascii=False),
        concepts_json=json.dumps(concepts, ensure_ascii=False),
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    lesson.current_version_id = version.id
    lesson.status = 'DRAFT'
    db.commit()
    return pack(db, lesson, include_pending=True)


@router.get('/lessons/{lesson_id}/versions')
def versions(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    rows = db.query(LessonVersion).filter_by(lesson_id=lesson.id).order_by(LessonVersion.version_number.desc()).all()
    return [
        {
            'id': version.id,
            'version_number': version.version_number,
            'created_at': version.created_at.isoformat(),
            'published_at': version.published_at.isoformat() if version.published_at else None,
            'approved_at': version.approved_at.isoformat() if version.approved_at else None,
        }
        for version in rows
    ]


@router.post('/lessons/{lesson_id}/rollback')
def rollback(lesson_id: int, version_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = db.get(LessonVersion, version_id)
    if not version or version.lesson_id != lesson.id or not version.published_at:
        raise HTTPException(400, detail={'code': 'VERSION_NOT_PUBLISHED', 'message': 'Only a published version of this lesson can be restored.'})
    lesson.current_version_id = version.id
    lesson.status = 'PUBLISHED'
    db.commit()
    return pack(db, lesson, include_pending=True)


@router.post('/lessons/{lesson_id}/prepare-concepts')
def prepare_concepts(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    source = normalize_text(version.clean_transcript or version.source_text)
    if not source:
        raise HTTPException(400, detail={'code': 'SOURCE_REQUIRED', 'message': 'Add lesson text before preparing concepts.'})
    concepts = extract_concepts(source)
    version.concepts_json = concepts_as_json(concepts)
    db.commit()
    return {'concepts': [c.__dict__ for c in concepts]}


@router.post('/lessons/{lesson_id}/analyze')
def analyze(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    text = (version.clean_transcript or version.source_text).strip()
    if not text:
        raise HTTPException(400, detail={'code': 'SOURCE_REQUIRED', 'message': 'Add or record the lesson source before analysis.'})
    result = analyze_source(text, lesson.grade, lesson.subject, lesson.topic)
    version.concepts_json = json.dumps(result['concepts'], ensure_ascii=False)
    version.learning_objectives_json = json.dumps([result['learning_objective']], ensure_ascii=False)
    version.ai_explanation_json = json.dumps({'explanation': result['explanation']}, ensure_ascii=False)
    version.activities_json = json.dumps(result['activities'], ensure_ascii=False)
    lesson.topic = result['topic']
    lesson.status = 'AI_SUGGESTED'
    db.commit()
    return result


def _apply_fast_translate_input(db: Session, lesson: Lesson, version: LessonVersion, x: TranslateIn) -> bool:
    """Apply source edits in the same request as translation.

    This removes the old UI round-trip: autosave/patch -> translate. If the source
    actually changed, generated downstream content is invalidated exactly once.
    """
    source_changed = False
    if x.source_language is not None and x.source_language != lesson.source_language:
        lesson.source_language = x.source_language
        source_changed = True
    if x.source_text is not None and x.source_text != version.source_text:
        version.source_text = x.source_text
        source_changed = True
    if x.clean_transcript is not None and x.clean_transcript != version.clean_transcript:
        version.clean_transcript = x.clean_transcript
        source_changed = True
    if source_changed:
        db.query(Translation).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(Artifact).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(PracticeQuestion).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(PreparationJob).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        db.query(TranslationFeedback).filter_by(lesson_version_id=version.id).delete(synchronize_session=False)
        lesson.status = 'DRAFT'
    return source_changed


def _translation_payload(row: Translation, *, translation_ms: float, total_ms: float, source_changed: bool = False) -> dict:
    return {
        'translation_id': row.id,
        'text': row.translated_text,
        'confidence': row.confidence,
        'flags': json.loads(row.flags_json or '[]'),
        'validation': json.loads(row.validation_json or '{}'),
        'verification_status': row.verification_status,
        'native_review_status': row.native_review_status,
        'translation_ms': round(translation_ms),
        'total_ms': round(total_ms),
        'source_changed': source_changed,
    }


@router.post('/lessons/{lesson_id}/translate')
def translate(lesson_id: int, x: TranslateIn, db: Session = Depends(get_db), user=Depends(current_user)):
    request_started = time.perf_counter()
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    if not settings.translation_enabled:
        raise HTTPException(503, detail={'code': 'TRANSLATION_DISABLED', 'message': 'Translation is disabled in this local profile.'})

    source_changed = _apply_fast_translate_input(db, lesson, version, x)
    # Never hold a SQLite write transaction open while a transformer is running.
    # The old path flushed the edit and then kept the write transaction alive for
    # model inference, which made concurrent target-language requests wait on the
    # database before they even reached IndicTrans2.
    if source_changed:
        db.commit()
        version = current_version(db, lesson)

    source = (version.clean_transcript or version.source_text).strip()
    if not source:
        raise HTTPException(400, detail={'code': 'SOURCE_REQUIRED', 'message': 'Add lesson text before translation.'})
    if x.target_language == lesson.source_language:
        raise HTTPException(400, detail={'code': 'SAME_LANGUAGE', 'message': 'Choose a language different from the source language.'})

    concepts = json.loads(version.concepts_json or '[]')
    context = {'concepts': concepts, 'grade': lesson.grade, 'subject': lesson.subject, 'topic': lesson.topic}

    # Database-level cache: once a translation exists for this exact lesson
    # version, do not rerun the model unless the caller explicitly asks for force.
    # This makes repeated language clicks instant even after process restarts.
    cached_row = db.query(Translation).filter_by(lesson_version_id=version.id, target_language=x.target_language).first()
    if cached_row is not None and cached_row.translated_text.strip() and not x.force:
        return _translation_payload(cached_row, translation_ms=0.0, total_ms=(time.perf_counter() - request_started) * 1000.0, source_changed=source_changed)

    try:
        model_started = time.perf_counter()
        # Fast interactive mode: greedy decoding + bounded output length. The
        # Translation Guard remains enabled immediately after inference.
        text = manager.translate(source, lesson.source_language, x.target_language, fast=True).strip()
        translation_ms = (time.perf_counter() - model_started) * 1000.0
        engine = Layer2Engine(lambda value, src, tgt: text)
        validation = engine.validate_translation(source, text, lesson.source_language, x.target_language, context)
    except Exception as exc:
        logger.exception('Local IndicTrans2 translation failed lesson_id=%s src=%s tgt=%s', lesson_id, lesson.source_language, x.target_language)
        db.rollback()
        raise HTTPException(
            503,
            detail={
                'code': 'TRANSLATION_FAILED',
                'message': 'The local translation model could not prepare this translation. Your original lesson is safe.',
                'details': {'runtime_error': str(exc)[:600]},
                'retryable': True,
            },
        ) from exc

    row = db.query(Translation).filter_by(lesson_version_id=version.id, target_language=x.target_language).first()
    previous_text = row.translated_text if row else ''
    if not row:
        row = Translation(
            lesson_version_id=version.id,
            source_language=lesson.source_language,
            target_language=x.target_language,
            translated_text=text,
        )
        db.add(row)
    if previous_text.strip() != text.strip():
        db.query(Artifact).filter_by(lesson_version_id=version.id, type='audio', language=x.target_language).delete(synchronize_session=False)
    row.source_language = lesson.source_language
    row.translated_text = text
    row.confidence = validation['confidence']
    row.flags_json = json.dumps(validation['flags'], ensure_ascii=False)
    row.validation_json = json.dumps(validation, ensure_ascii=False)
    row.verification_status = 'TEMPORARY' if x.target_language in {'sat_Olck', 'mar_Deva'} else 'UNVERIFIED'
    row.teacher_edited = False
    row.native_review_status = 'NOT_REQUIRED' if x.target_language in {'sat_Olck', 'mar_Deva'} else 'NOT_REVIEWED'
    lesson.status = 'FLAGGED' if validation['flags'] else 'TRANSLATED'
    db.commit()
    db.refresh(row)
    return _translation_payload(row, translation_ms=translation_ms, total_ms=(time.perf_counter() - request_started) * 1000.0, source_changed=source_changed)


@router.post('/lessons/{lesson_id}/verify-translation')
def verify_translation(lesson_id: int, x: VerifyIn, db: Session = Depends(get_db), user=Depends(current_user)):
    """Approve only the text. Everything downstream is prepared asynchronously."""
    started = time.perf_counter()
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    translation = db.get(Translation, x.translation_id)
    if not translation or translation.lesson_version_id != version.id:
        raise HTTPException(404, detail={'code': 'TRANSLATION_NOT_FOUND', 'message': 'Translation not found.'})
    if x.native_review_status == 'VERIFIED':
        raise HTTPException(400, detail={'code': 'NATIVE_REVIEW_REQUIRES_REVIEWER', 'message': 'Native-language verification is reserved for a qualified language reviewer.'})
    if x.edited_text is not None:
        text = x.edited_text.strip()
        if not text:
            raise HTTPException(400, detail={'code': 'TRANSLATION_EMPTY', 'message': 'Approved translation cannot be empty.'})
        changed_text = translation.translated_text.strip() != text
        if changed_text:
            db.query(Artifact).filter_by(lesson_version_id=version.id, type='audio', language=translation.target_language).delete(synchronize_session=False)
        translation.translated_text = text
        translation.teacher_edited = True
        engine = Layer2Engine(manager.translate)
        validation = engine.validate_translation(
            version.clean_transcript or version.source_text,
            text,
            translation.source_language,
            translation.target_language,
            {'concepts': json.loads(version.concepts_json or '[]')},
        )
        translation.confidence = validation['confidence']
        translation.flags_json = json.dumps(validation['flags'], ensure_ascii=False)
        translation.validation_json = json.dumps(validation, ensure_ascii=False)
    if x.native_review_status is not None:
        translation.native_review_status = x.native_review_status

    translation.verification_status = ('TEMPORARY' if translation.target_language in {'sat_Olck', 'mar_Deva'} and x.approved else ('TEACHER_APPROVED' if x.approved else 'REVIEW_REQUIRED'))
    lesson.status = 'TEACHER_APPROVED' if x.approved else 'FLAGGED'
    db.commit()
    return {
        'translation_id': translation.id,
        'language': translation.target_language,
        'text': translation.translated_text,
        'verification_status': translation.verification_status,
        'native_review_status': translation.native_review_status,
        'prep_queued': bool(x.approved),
        'response_ms': round((time.perf_counter() - started) * 1000),
    }



@router.post('/lessons/{lesson_id}/translation/{translation_id}/temporary')
def save_temporary_translation(lesson_id: int, translation_id: int, payload: dict, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    translation = db.get(Translation, translation_id)
    if not translation or translation.lesson_version_id != version.id:
        raise HTTPException(404, detail={'code': 'TRANSLATION_NOT_FOUND', 'message': 'Translation not found.'})
    if translation.target_language not in {'sat_Olck', 'mar_Deva'}:
        raise HTTPException(400, detail={'code': 'TEMPORARY_LANGUAGE_REQUIRED', 'message': 'Temporary save is only available for Santali and Marathi.'})
    text = str(payload.get('edited_text') or translation.translated_text or '').strip()
    if not text:
        raise HTTPException(400, detail={'code': 'TRANSLATION_EMPTY', 'message': 'Temporary translation cannot be empty.'})
    if text != translation.translated_text.strip():
        validation = Layer2Engine(manager.translate).validate_translation(
            version.clean_transcript or version.source_text,
            text,
            translation.source_language,
            translation.target_language,
            {'concepts': json.loads(version.concepts_json or '[]')},
        )
        translation.confidence = validation['confidence']
        translation.flags_json = json.dumps(validation['flags'], ensure_ascii=False)
        translation.validation_json = json.dumps(validation, ensure_ascii=False)
        db.query(Artifact).filter_by(lesson_version_id=version.id, type='audio', language='sat_Olck').delete(synchronize_session=False)
    translation.translated_text = text
    translation.teacher_edited = True
    translation.verification_status = 'TEMPORARY'
    translation.native_review_status = 'NOT_REQUIRED'
    db.commit()
    return {'translation_id': translation.id, 'language': translation.target_language, 'text': translation.translated_text, 'verification_status': translation.verification_status, 'native_review_status': translation.native_review_status}

@router.post('/lessons/{lesson_id}/translation/{translation_id}/back-translate')
def back_translate(lesson_id: int, translation_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    """On-demand semantic review for a teacher who does not know the target language."""
    started = time.perf_counter()
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    translation = db.get(Translation, translation_id)
    if not translation or translation.lesson_version_id != version.id:
        raise HTTPException(404, detail={'code': 'TRANSLATION_NOT_FOUND', 'message': 'Translation not found.'})
    source = (version.clean_transcript or version.source_text).strip()
    if not source or not translation.translated_text.strip():
        raise HTTPException(400, detail={'code': 'BACK_TRANSLATION_SOURCE_MISSING', 'message': 'Source and target text are required.'})
    context = json.loads(translation.validation_json or '{}')
    cached = context.get('back_translation')
    if isinstance(cached, str) and cached.strip():
        return {'translation_id': translation.id, 'back_translation': cached, 'cached': True, 'back_translation_ms': 0}
    model_started = time.perf_counter()
    try:
        reverse = manager.translate(translation.translated_text, translation.target_language, translation.source_language, fast=True).strip()
    except Exception as exc:
        logger.exception('Back-translation failed lesson_id=%s translation_id=%s', lesson_id, translation_id)
        raise HTTPException(503, detail={'code': 'BACK_TRANSLATION_FAILED', 'message': 'Meaning check could not be prepared right now.', 'retryable': True, 'details': {'runtime_error': str(exc)[:500]}}) from exc
    context['back_translation'] = reverse
    context['back_translation_ms'] = round((time.perf_counter() - model_started) * 1000)
    translation.validation_json = json.dumps(context, ensure_ascii=False)
    db.commit()
    return {'translation_id': translation.id, 'back_translation': reverse, 'cached': False, 'back_translation_ms': round((time.perf_counter() - model_started) * 1000), 'total_ms': round((time.perf_counter() - started) * 1000)}


@router.get('/lessons/{lesson_id}/preparations')
def preparation_status(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    return preparation_views(db, version.id)


@router.post('/lessons/{lesson_id}/preparations/tts/{language}')
def queue_tts_preparation(lesson_id: int, language: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """Explicitly queue heavyweight lesson audio. It never starts automatically on approval."""
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    allowed = {'eng_Latn', 'hin_Deva', 'mar_Deva', 'sat_Olck'}
    if language not in allowed:
        raise HTTPException(400, detail={'code': 'LANGUAGE_UNSUPPORTED', 'message': 'Unsupported lesson language.'})
    if language == lesson.source_language:
        text = (version.clean_transcript or version.source_text).strip()
        if not text:
            raise HTTPException(400, detail={'code': 'TTS_SOURCE_MISSING', 'message': 'There is no source text for audio generation.'})
    else:
        translation = db.query(Translation).filter_by(lesson_version_id=version.id, target_language=language).first()
        if not translation or not translation.translated_text.strip():
            raise HTTPException(400, detail={'code': 'TTS_SOURCE_MISSING', 'message': 'Prepare the language translation first.'})
        if translation.verification_status != 'TEACHER_APPROVED' and not (language in {'sat_Olck', 'mar_Deva'} and translation.verification_status == 'TEMPORARY'):
            raise HTTPException(400, detail={'code': 'TTS_REVIEW_REQUIRED', 'message': 'Approve the translation before generating student-facing audio.'})
    job_id, status = enqueue_retry(version.id, 'tts', language)
    return {'job_id': job_id, 'status': status, 'kind': 'tts', 'language': language}


@router.post('/lessons/{lesson_id}/preparations/{job_id}/retry')
def retry_preparation(lesson_id: int, job_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    from ...models import PreparationJob
    job = db.get(PreparationJob, job_id)
    if not job or job.lesson_version_id != version.id:
        raise HTTPException(404, detail={'code': 'PREPARATION_NOT_FOUND', 'message': 'Preparation job not found.'})
    new_id, status = enqueue_retry(version.id, job.kind, job.language)
    return {'job_id': new_id, 'status': status, 'kind': job.kind, 'language': job.language}


@router.post('/lessons/{lesson_id}/generate-practice')
def generate_practice(lesson_id: int, x: PracticeGenerateIn, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    rows = ensure_practice(
        db=db, version=version, language=x.language, source_language=lesson.source_language,
        count=x.count, approved=False, translate_fn=manager.translate, translate_many_fn=manager.translate_many,
    )
    return [_practice_view(q) for q in rows]


@router.post('/lessons/{lesson_id}/practice/{question_id}/approve')
def approve_practice(lesson_id: int, question_id: int, x: PracticeApprovalIn, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    question = db.get(PracticeQuestion, question_id)
    if not question or question.lesson_version_id != version.id:
        raise HTTPException(404, detail={'code': 'PRACTICE_NOT_FOUND', 'message': 'Practice question not found.'})
    question.approved = x.approved
    db.commit()
    return _practice_view(question)


@router.post('/lessons/{lesson_id}/generate-worksheet')
def generate_worksheet(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    translations = db.query(Translation).filter_by(lesson_version_id=version.id).all()
    return make_worksheet(lesson, version, translations)


@router.post('/lessons/{lesson_id}/transcribe')
async def transcribe(lesson_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    if not settings.stt_enabled:
        raise HTTPException(503, detail={'code': 'STT_DISABLED', 'message': 'Speech-to-text is disabled in this local profile.'})
    suffix = Path(file.filename or 'audio.webm').suffix.lower() or '.webm'
    allowed = {'.webm', '.wav', '.mp3', '.m4a', '.mp4', '.ogg', '.flac'}
    if suffix not in allowed:
        raise HTTPException(400, detail={'code': 'AUDIO_FORMAT_UNSUPPORTED', 'message': 'Use WAV, MP3, M4A, OGG, FLAC, or WebM audio.'})
    data = await file.read()
    if len(data) > settings.max_audio_mb * 1024 * 1024:
        raise HTTPException(413, detail={'code': 'AUDIO_TOO_LARGE', 'message': f'Audio must be under {settings.max_audio_mb} MB.'})
    folder = STORAGE_ROOT / 'uploads'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (secrets.token_hex(12) + suffix)
    path.write_bytes(data)
    try:
        # Pass the canonical app language code so the ModelManager can route
        # Santali to IndicConformer instead of accidentally sending it to Whisper.
        result = manager.transcribe(path, lesson.source_language)
    except Exception as exc:
        logger.exception('STT failed lesson_id=%s', lesson_id)
        raise HTTPException(503, detail={'code': 'STT_FAILED', 'message': 'Speech recognition failed. You can edit the transcript manually or retry.', 'details': {'runtime_error': str(exc)[:600]}, 'retryable': True}) from exc
    version = current_version(db, lesson)
    version.clean_transcript = result['text']
    db.commit()
    return result


@router.post('/lessons/{lesson_id}/tts')
def tts(lesson_id: int, x: TTSIn, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    if not settings.tts_enabled:
        raise HTTPException(503, detail={'code': 'TTS_DISABLED', 'message': 'Text-to-speech is disabled in this local profile.'})
    version = current_version(db, lesson)
    translation = db.query(Translation).filter_by(lesson_version_id=version.id, target_language=x.target_language).first()
    if x.target_language == lesson.source_language:
        text = version.clean_transcript or version.source_text
    else:
        if not translation:
            raise HTTPException(400, detail={'code': 'TTS_SOURCE_MISSING', 'message': 'Prepare this language translation before generating audio.'})
        if translation.verification_status != 'TEACHER_APPROVED' and not (x.target_language in {'sat_Olck', 'mar_Deva'} and translation.verification_status == 'TEMPORARY'):
            raise HTTPException(400, detail={'code': 'TTS_REVIEW_REQUIRED', 'message': 'Approve the translation before generating student-facing audio.'})
        text = translation.translated_text
    if not text.strip():
        raise HTTPException(400, detail={'code': 'TTS_SOURCE_MISSING', 'message': 'There is no text available for audio generation.'})

    cache = STORAGE_ROOT / 'tts_cache'
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((text + '|' + x.target_language + '|' + x.speaker + '|v2').encode('utf-8')).hexdigest()
    path = cache / f'{key}.wav'
    created = False
    tts_started = time.perf_counter()
    if not path.exists():
        try:
            manager.synthesize(text, path, x.target_language, x.speaker)
            created = True
        except Exception as exc:
            logger.exception('TTS failed lesson_id=%s language=%s', lesson_id, x.target_language)
            raise HTTPException(503, detail={'code': 'TTS_FAILED', 'message': 'Audio generation failed. The lesson text is still safe and available.', 'details': {'runtime_error': str(exc)[:600]}, 'retryable': True}) from exc
    digest = sha256_file(path)
    artifact = db.query(Artifact).filter_by(lesson_version_id=version.id, type='audio', language=x.target_language, sha256=digest).first()
    if not artifact:
        artifact = register_file(db, version.id, 'audio', x.target_language, path, 'audio/wav', {'cache_key': key, 'speaker': x.speaker})
    return {
        'artifact_id': artifact.id,
        'url': f'/api/v1/artifacts/{artifact.id}',
        'language': x.target_language,
        'cached': not created,
        'tts_ms': round((time.perf_counter() - tts_started) * 1000.0),
        'artifact': {
            'id': artifact.id,
            'type': artifact.type,
            'language': artifact.language,
            'mime_type': artifact.mime_type,
            'size_bytes': artifact.size_bytes,
            'sha256': artifact.sha256,
            'url': f'/api/v1/artifacts/{artifact.id}',
        },
    }


@router.post('/lessons/{lesson_id}/publish')
def do_publish(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db_lesson(db, lesson_id)
    require_group_owner(db, lesson, user)
    version = current_version(db, lesson)
    ok, problems = publish(db, lesson, version)
    if not ok:
        raise HTTPException(400, detail={'code': 'PUBLISH_BLOCKED', 'message': 'Publish is blocked until the lesson has valid source content.', 'details': {'problems': problems}})
    return pack(db, lesson, include_pending=True)


@router.get('/artifacts/{artifact_id}')
def artifact(artifact_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    item = db.get(Artifact, artifact_id)
    if not item or not Path(item.path).is_file():
        raise HTTPException(404, detail={'code': 'ARTIFACT_NOT_FOUND', 'message': 'Artifact not found.'})
    version = db.get(LessonVersion, item.lesson_version_id)
    lesson = db.get(Lesson, version.lesson_id) if version else None
    if not lesson or not can_read_lesson(db, lesson, user):
        raise HTTPException(403, detail={'code': 'ARTIFACT_ACCESS_DENIED', 'message': 'You do not have access to this lesson artifact.'})
    return FileResponse(item.path, media_type=item.mime_type, filename=Path(item.path).name)
