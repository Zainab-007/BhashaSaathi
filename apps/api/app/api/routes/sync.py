import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ...db import get_db
from ...models import SyncRecord, Attempt, Lesson, LessonVersion, Membership, PracticeQuestion
from ...schemas import SyncPushIn
from ...core.security import current_user
from ...services.content import evaluate_attempt

router = APIRouter(prefix='/sync', tags=['sync'])


def process_attempt(db: Session, user, payload: dict) -> tuple[bool, str | None]:
    lesson_id = int(payload.get('lessonId') or payload.get('lesson_id') or 0)
    version_id = int(payload.get('lessonVersionId') or payload.get('lesson_version_id') or 0)
    language = str(payload.get('language') or 'hin_Deva')
    answers = payload.get('answers') or {}
    lesson = db.get(Lesson, lesson_id)
    version = db.get(LessonVersion, version_id)
    membership = lesson and db.query(Membership).filter_by(group_id=lesson.group_id, user_id=user.id).first()
    if not lesson or lesson.status != 'PUBLISHED' or not version or version.lesson_id != lesson.id or not membership:
        return False, 'ATTEMPT_ACCESS_DENIED'
    questions = db.query(PracticeQuestion).filter_by(lesson_version_id=version.id, language=language, approved=True).all()
    if not questions:
        return False, 'PRACTICE_NOT_READY'
    clean_answers = {str(k): v for k, v in answers.items()}
    score, results = evaluate_attempt(version, clean_answers, questions)
    stamp = datetime.now(timezone.utc)
    attempt = Attempt(
        lesson_id=lesson.id,
        lesson_version_id=version.id,
        student_id=user.id,
        answers_json=json.dumps(clean_answers, ensure_ascii=False),
        score=score,
        concept_results_json=json.dumps(results, ensure_ascii=False),
        created_at=stamp,
        synced_at=stamp,
    )
    db.add(attempt)
    return True, None


@router.post('/push')
def push(x: SyncPushIn, db: Session = Depends(get_db), user=Depends(current_user)):
    accepted = 0
    processed = 0
    failures = []
    for op in x.operations:
        operation = str(op.get('operation', 'UNKNOWN'))
        entity_id = str(op.get('entity_id', ''))
        existing = db.query(SyncRecord).filter_by(
            user_id=user.id,
            device_id=x.device_id,
            operation=operation,
            entity_id=entity_id,
        ).first()
        if existing and existing.processed_at:
            accepted += 1
            processed += 1
            continue
        row = existing or SyncRecord(
            user_id=user.id,
            device_id=x.device_id,
            operation=operation,
            entity_type=str(op.get('entity_type', 'unknown')),
            entity_id=entity_id,
            payload_json=json.dumps(op.get('payload') or {}, ensure_ascii=False),
            created_at=datetime.now(timezone.utc),
            attempt_count=0,
        )
        if not existing:
            db.add(row)
        accepted += 1
        if operation == 'ATTEMPT_UPSERT':
            try:
                with db.begin_nested():
                    ok, reason = process_attempt(db, user, op.get('payload') or {})
                    if not ok:
                        raise ValueError(reason or 'SYNC_REJECTED')
                row.processed_at = datetime.now(timezone.utc)
                row.last_error = None
                processed += 1
            except ValueError as exc:
                row.last_error = str(exc)
                row.attempt_count = (row.attempt_count or 0) + 1
                failures.append({'entity_id': entity_id, 'code': str(exc)})
            except Exception as exc:
                row.last_error = str(exc)[:500]
                row.attempt_count = (row.attempt_count or 0) + 1
                failures.append({'entity_id': entity_id, 'code': 'SYNC_PROCESSING_FAILED'})
        else:
            row.processed_at = datetime.now(timezone.utc)
            processed += 1
    db.commit()
    return {'accepted': accepted, 'processed': processed, 'failed': len(failures), 'failures': failures, 'status': 'ok'}


@router.post('/pull')
def pull(db: Session = Depends(get_db), user=Depends(current_user)):
    rows = (
        db.query(SyncRecord)
        .filter(SyncRecord.user_id == user.id, SyncRecord.processed_at.is_(None))
        .order_by(SyncRecord.id.asc())
        .limit(100)
        .all()
    )
    return {
        'changes': [
            {
                'id': row.id,
                'operation': row.operation,
                'entity_type': row.entity_type,
                'entity_id': row.entity_id,
                'payload': json.loads(row.payload_json or '{}'),
            }
            for row in rows
        ]
    }
