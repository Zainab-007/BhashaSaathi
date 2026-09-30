from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import IntegrityError

from ..core.config import STORAGE_ROOT, settings
from ..db.session import SessionLocal
from ..models import Artifact, Lesson, LessonVersion, PreparationJob, PracticeQuestion, Translation
from ..services.artifacts import register_file, sha256_file
from ..services.content import ensure_practice
from ..services.model_manager import manager

logger = logging.getLogger('bhashasaathi.background')

# Audio is the long pole, so it gets its own ordered worker. Practice is separate
# so a slow TTS job never makes practice appear "stuck" behind it.
_tts_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bhashasaathi-tts')
_practice_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bhashasaathi-practice')
_submission_lock = threading.RLock()
_submitted_jobs: set[int] = set()


def _now():
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)



def _ensure_job(db, version_id: int, kind: str, language: str = '') -> PreparationJob:
    job = db.query(PreparationJob).filter_by(
        lesson_version_id=version_id, kind=kind, language=language,
    ).first()
    if job:
        return job
    job = PreparationJob(lesson_version_id=version_id, kind=kind, language=language, status='QUEUED')
    db.add(job)
    try:
        db.commit()
        db.refresh(job)
        return job
    except IntegrityError:
        db.rollback()
        job = db.query(PreparationJob).filter_by(lesson_version_id=version_id, kind=kind, language=language).first()
        if not job:
            raise
        return job


def _submit(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(PreparationJob, job_id)
        if not job or job.status != 'QUEUED':
            return
        executor = _tts_executor if job.kind == 'tts' else _practice_executor
    finally:
        db.close()
    with _submission_lock:
        if job_id in _submitted_jobs:
            return
        _submitted_jobs.add(job_id)
    try:
        executor.submit(_run_job, job_id)
    except Exception:
        with _submission_lock:
            _submitted_jobs.discard(job_id)
        raise


def enqueue_retry(version_id: int, kind: str, language: str = '') -> tuple[int, str]:
    """Idempotently queue one preparation job and return (job_id, status)."""
    db = SessionLocal()
    try:
        job = _ensure_job(db, version_id, kind, language)
        if kind == 'tts':
            existing_art = db.query(Artifact).filter_by(
                lesson_version_id=version_id, type='audio', language=language
            ).first()
            if existing_art and Path(existing_art.path).is_file() and Path(existing_art.path).stat().st_size > 0:
                if job.status != 'READY':
                    job.status = 'READY'
                    job.error = None
                    job.finished_at = _now()
                    db.commit()
                return job.id, 'READY'

        if job.status in {'QUEUED', 'RUNNING'}:
            job_id, status = job.id, job.status
        elif job.status == 'READY':
            job_id, status = job.id, job.status
        else:
            job.status = 'QUEUED'
            job.error = None
            job.started_at = None
            job.finished_at = None
            db.commit()
            job_id, status = job.id, job.status
    finally:
        db.close()
    if status == 'QUEUED':
        _submit(job_id)
    return job_id, status


def _set_status(db, job: PreparationJob, status: str, error: str | None = None):
    job.status = status
    job.error = error
    if status == 'RUNNING':
        job.started_at = _now()
    if status in {'READY', 'FAILED'}:
        job.finished_at = _now()
    db.commit()


def _run_tts(db: SessionLocal, job: PreparationJob, lesson: Lesson, version: LessonVersion) -> None:
    lang = job.language
    translation = None
    if lang == lesson.source_language:
        text = (version.clean_transcript or version.source_text).strip()
        if not text:
            raise RuntimeError('The lesson source is empty; audio generation cannot start.')
    else:
        translation = db.query(Translation).filter_by(
            lesson_version_id=version.id, target_language=lang,
        ).first()
        if not translation or not translation.translated_text.strip():
            raise RuntimeError('The selected translation is no longer available; audio generation must be retried.')
        usable = translation.verification_status == 'TEACHER_APPROVED' or (lang in {'sat_Olck', 'mar_Deva'} and translation.verification_status == 'TEMPORARY')
        if not usable:
            raise RuntimeError('The selected translation is not yet usable for audio generation.')
        text = translation.translated_text.strip()

    cache = STORAGE_ROOT / 'tts_cache'
    cache.mkdir(parents=True, exist_ok=True)
    speaker = 'classroom_teacher'
    key = hashlib.sha256((text + '|' + lang + '|' + speaker + '|v7').encode('utf-8')).hexdigest()
    path = cache / f'{key}.wav'

    if not path.exists() or path.stat().st_size == 0:
        manager.synthesize(text, path, lang, speaker)

    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f'TTS audio generation failed for {lang}: output file is missing or empty.')

    # The lesson may have changed while inference was running. Re-read the
    # authoritative source/translation before attaching the artifact.
    if lang == lesson.source_language:
        current_text = (version.clean_transcript or version.source_text).strip()
        if current_text != text:
            raise RuntimeError('Audio became stale because the lesson source changed. Retry audio generation.')
    else:
        current_translation = db.query(Translation).filter_by(
            lesson_version_id=version.id, target_language=lang,
        ).first()
        if not current_translation:
            raise RuntimeError('Audio became stale because the translation was removed. Retry audio generation.')
        current_usable = current_translation.verification_status == 'TEACHER_APPROVED' or (lang in {'sat_Olck', 'mar_Deva'} and current_translation.verification_status == 'TEMPORARY')
        if not current_usable or current_translation.translated_text.strip() != text:
            raise RuntimeError('Audio became stale because the usable text changed. Retry audio generation.')

    digest = sha256_file(path)
    existing = db.query(Artifact).filter_by(
        lesson_version_id=version.id, type='audio', language=lang,
    ).first()
    if existing and existing.sha256 == digest and Path(existing.path).is_file():
        return
    if existing:
        db.delete(existing)
        db.flush()
    register_file(db, version.id, 'audio', lang, path, 'audio/wav', {'cache_key': key, 'speaker': speaker})
    db.commit()


def _run_practice(db: SessionLocal, job: PreparationJob, lesson: Lesson, version: LessonVersion) -> None:
    lang = job.language
    ensure_practice(
        db=db,
        version=version,
        language=lang,
        source_language=lesson.source_language,
        count=4,
        approved=False,
        translate_fn=manager.translate,
        translate_many_fn=manager.translate_many,
    )


def _run_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(PreparationJob, job_id)
        if not job or job.status == 'READY':
            return
        lesson_version = db.get(LessonVersion, job.lesson_version_id)
        lesson = db.get(Lesson, lesson_version.lesson_id) if lesson_version else None
        if not lesson_version or not lesson:
            if job:
                _set_status(db, job, 'FAILED', 'Lesson version no longer exists.')
            return
        _set_status(db, job, 'RUNNING')
        if job.kind == 'tts':
            _run_tts(db, job, lesson, lesson_version)
        elif job.kind == 'practice':
            _run_practice(db, job, lesson, lesson_version)
        else:
            raise ValueError(f'Unsupported preparation job: {job.kind}')
        # Source edits can invalidate a running job while model inference is in
        # progress. Never convert an externally invalidated job back to READY.
        db.expire_all()
        current_job = db.get(PreparationJob, job_id)
        if not current_job or current_job.status != 'RUNNING':
            return
        _set_status(db, current_job, 'READY')
    except Exception as exc:
        logger.exception('Background preparation failed job_id=%s', job_id)
        try:
            job = db.get(PreparationJob, job_id)
            if job:
                _set_status(db, job, 'FAILED', str(exc)[:1000])
        except Exception:
            logger.exception('Could not persist background failure job_id=%s', job_id)
    finally:
        db.close()
        with _submission_lock:
            _submitted_jobs.discard(job_id)


def _expire_stale_jobs(db) -> None:
    """Convert orphaned RUNNING jobs into FAILED so the UI can never wait forever."""
    now = _now()
    rows = db.query(PreparationJob).filter(PreparationJob.status == 'RUNNING').all()
    changed = False
    for row in rows:
        started = _to_utc(row.started_at)
        if not started:
            continue
        limit = int(settings.preparation_tts_timeout_s if row.kind == 'tts' else settings.preparation_practice_timeout_s)
        if (now - started) > timedelta(seconds=limit):
            row.status = 'FAILED'
            row.error = f'{row.kind.upper()} job exceeded the {limit}s safety timeout. Press Retry to run it again.'
            row.finished_at = now
            changed = True
    if changed:
        db.commit()


def preparation_views(db, version_id: int) -> list[dict]:
    _expire_stale_jobs(db)
    rows = db.query(PreparationJob).filter_by(lesson_version_id=version_id).order_by(PreparationJob.id.asc()).all()
    return [
        {
            'id': row.id,
            'kind': row.kind,
            'language': row.language,
            'status': row.status,
            'error': row.error,
            'created_at': _to_utc(row.created_at).isoformat() if row.created_at else None,
            'started_at': _to_utc(row.started_at).isoformat() if row.started_at else None,
            'finished_at': _to_utc(row.finished_at).isoformat() if row.finished_at else None,
        }
        for row in rows
    ]



def resume_pending_preparations() -> None:
    db = SessionLocal()
    try:
        rows = db.query(PreparationJob).filter(PreparationJob.status.in_(['QUEUED', 'RUNNING'])).all()
        for row in rows:
            if row.kind == 'tts':
                art = db.query(Artifact).filter_by(
                    lesson_version_id=row.lesson_version_id, type='audio', language=row.language
                ).first()
                if art and Path(art.path).is_file() and Path(art.path).stat().st_size > 0:
                    row.status = 'READY'
                    row.error = None
                    row.finished_at = _now()
                    continue
            row.status = 'FAILED'
            row.error = f'{row.kind.upper()} generation was interrupted by process restart. Press Generate or Retry to run it.'
            row.started_at = None
            row.finished_at = _now()
        db.commit()
    finally:
        db.close()

