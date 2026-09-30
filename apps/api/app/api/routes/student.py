import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ...db import get_db
from ...models import Lesson, LessonVersion, Membership, PracticeQuestion, Attempt, Translation, TranslationFeedback
from ...schemas import AttemptIn, TranslationFeedbackIn
from ...core.security import current_user
from ...services.content import evaluate_attempt
from .lessons import pack

router = APIRouter(prefix='/student', tags=['student'])


@router.get('/lessons')
def student_lessons(db: Session = Depends(get_db), user=Depends(current_user)):
    ids = [m.group_id for m in db.query(Membership).filter_by(user_id=user.id).all()]
    rows = db.query(Lesson).filter(Lesson.group_id.in_(ids or [-1]), Lesson.status == 'PUBLISHED').order_by(Lesson.updated_at.desc()).all()
    return [pack(db, lesson, viewer_id=user.id) for lesson in rows]


@router.get('/lessons/{lesson_id}/package')
def package(lesson_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db.get(Lesson, lesson_id)
    if not lesson or lesson.status != 'PUBLISHED':
        raise HTTPException(404, detail={'code': 'PUBLISHED_LESSON_NOT_FOUND', 'message': 'This lesson is not published.'})
    allowed = bool(db.query(Membership).filter_by(group_id=lesson.group_id, user_id=user.id).first())
    if not allowed:
        raise HTTPException(403, detail={'code': 'LESSON_ACCESS_DENIED', 'message': 'Join the classroom before opening this lesson.'})
    return pack(db, lesson, viewer_id=user.id, include_worksheet=True)


@router.post('/attempts')
def attempt(x: AttemptIn, db: Session = Depends(get_db), user=Depends(current_user)):
    lesson = db.get(Lesson, x.lesson_id)
    if not lesson or lesson.status != 'PUBLISHED':
        raise HTTPException(404, detail={'code': 'LESSON_NOT_FOUND', 'message': 'Published lesson not found.'})
    membership = db.query(Membership).filter_by(group_id=lesson.group_id, user_id=user.id).first()
    if not membership:
        raise HTTPException(403, detail={'code': 'GROUP_ACCESS_DENIED', 'message': 'You are not a member of this classroom.'})
    version_id = x.lesson_version_id or lesson.current_version_id
    version = db.get(LessonVersion, version_id) if version_id else None
    if not version or version.lesson_id != lesson.id:
        raise HTTPException(404, detail={'code': 'VERSION_NOT_FOUND', 'message': 'Lesson version not found.'})
    questions = db.query(PracticeQuestion).filter_by(lesson_version_id=version.id, language=x.language, approved=True).all()
    if not questions:
        raise HTTPException(400, detail={'code': 'PRACTICE_NOT_READY', 'message': f'Approved practice is not available in {x.language} yet.'})
    score, results = evaluate_attempt(version, x.answers, questions)
    stamp = datetime.now(timezone.utc)
    attempt_row = Attempt(
        lesson_id=lesson.id,
        lesson_version_id=version.id,
        student_id=user.id,
        answers_json=json.dumps(x.answers, ensure_ascii=False),
        score=score,
        concept_results_json=json.dumps(results, ensure_ascii=False),
        created_at=stamp,
        synced_at=stamp,
    )
    db.add(attempt_row)
    db.commit()
    db.refresh(attempt_row)
    return {'attempt_id': attempt_row.id, 'score': score, 'concept_results': results, 'synced': True}


@router.get('/progress')
def progress(db: Session = Depends(get_db), user=Depends(current_user)):
    attempts = db.query(Attempt).filter_by(student_id=user.id).order_by(Attempt.created_at.desc()).all()
    avg = round(sum(item.score for item in attempts) / len(attempts), 1) if attempts else 0
    return {
        'lessons_completed': len(attempts),
        'overall_understanding': avg,
        'needs_practice': sum(1 for item in attempts if item.score < 70),
        'recent_attempts': [
            {'id': item.id, 'lesson_id': item.lesson_id, 'score': item.score, 'created_at': item.created_at.isoformat()}
            for item in attempts[:8]
        ],
    }


@router.post('/translations/{translation_id}/feedback')
def translation_feedback(translation_id: int, x: TranslationFeedbackIn, db: Session = Depends(get_db), user=Depends(current_user)):
    translation = db.get(Translation, translation_id)
    if not translation:
        raise HTTPException(404, detail={'code': 'TRANSLATION_NOT_FOUND', 'message': 'Translation not found.'})
    version = db.get(LessonVersion, translation.lesson_version_id)
    lesson = db.get(Lesson, version.lesson_id) if version else None
    if not lesson or lesson.status != 'PUBLISHED':
        raise HTTPException(404, detail={'code': 'PUBLISHED_LESSON_NOT_FOUND', 'message': 'This lesson is not published.'})
    membership = db.query(Membership).filter_by(group_id=lesson.group_id, user_id=user.id).first()
    if not membership:
        raise HTTPException(403, detail={'code': 'GROUP_ACCESS_DENIED', 'message': 'Join the classroom before reviewing translations.'})
    row = db.query(TranslationFeedback).filter_by(translation_id=translation.id, student_id=user.id).first()
    if not row:
        row = TranslationFeedback(
            translation_id=translation.id,
            lesson_version_id=version.id,
            student_id=user.id,
            status=x.status,
            comment=x.comment.strip(),
        )
        db.add(row)
    else:
        row.status = x.status
        row.comment = x.comment.strip()
    db.commit()
    return {'translation_id': translation.id, 'status': row.status, 'comment': row.comment}
