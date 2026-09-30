from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Setup paths
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / 'apps' / 'api'
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import soundfile as sf
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import User, Group, Lesson, LessonVersion, PreparationJob, Artifact
from app.services.background_prep import enqueue_retry, _tts_executor, preparation_views
from app.core.config import STORAGE_ROOT

def run_test_b():
    print("=" * 60)
    print("TEST B: Backend TTS Service & Preparation Lifecycle Test")
    print("=" * 60)

    db: Session = SessionLocal()
    try:
        # 1. Setup / find a test user, group, and lesson
        user = db.query(User).first()
        if not user:
            user = User(username='test_teacher', email='teacher@test.com', password_hash='dummy', role='TEACHER')
            db.add(user)
            db.commit()
            db.refresh(user)
        
        group = db.query(Group).filter_by(owner_id=user.id).first()
        if not group:
            group = Group(name='Test Classroom', grade='Grade 5', section='A', owner_id=user.id)
            db.add(group)
            db.commit()
            db.refresh(group)

        text = "Hello children, today we are learning about plants."
        
        lesson = Lesson(
            group_id=group.id,
            title='Test Plant Lesson',
            grade='Grade 5',
            subject='Science',
            topic='Plants',
            source_language='eng_Latn',
            status='DRAFT',
            created_by=user.id
        )
        db.add(lesson)
        db.flush()

        version = LessonVersion(
            lesson_id=lesson.id,
            version_number=1,
            source_text=text,
            clean_transcript=text,
            created_by=user.id
        )
        db.add(version)
        db.flush()
        lesson.current_version_id = version.id
        db.commit()
        db.refresh(lesson)
        db.refresh(version)

        print(f"Created Lesson ID={lesson.id}, Version ID={version.id}")
        print(f"Source text: '{text}' ({lesson.source_language})")

        # 2. Enqueue TTS Preparation
        print("\n--- 2. Enqueueing TTS Preparation Job ---")
        job_id, status = enqueue_retry(version.id, 'tts', 'eng_Latn')
        print(f"Job ID: {job_id}, Initial Status: {status}")

        # 3. Poll status until READY or FAILED
        print("\n--- 3. Monitoring Worker Execution ---")
        t0 = time.perf_counter()
        final_job = None
        for i in range(120): # up to 120 seconds
            time.sleep(1.0)
            db.expire_all()
            job = db.get(PreparationJob, job_id)
            elapsed = time.perf_counter() - t0
            print(f"[{elapsed:.1f}s] Job status: {job.status}, error: {job.error}")
            if job.status in {'READY', 'FAILED'}:
                final_job = job
                break

        assert final_job is not None, "Job timed out in test loop"
        print(f"\nFinal Job Status: {final_job.status}")
        assert final_job.status == 'READY', f"Job failed: {final_job.error}"

        # 4. Verify DB Artifact
        print("\n--- 4. Verifying DB Artifact ---")
        artifact = db.query(Artifact).filter_by(
            lesson_version_id=version.id, type='audio', language='eng_Latn'
        ).first()
        assert artifact is not None, "Artifact was not registered in DB!"
        print(f"Artifact ID: {artifact.id}")
        print(f"Artifact Path: {artifact.path}")
        print(f"Artifact MIME: {artifact.mime_type}")
        print(f"Artifact Size: {artifact.size_bytes} bytes")
        print(f"Artifact SHA256: {artifact.sha256}")

        # 5. Verify physical WAV file on disk
        wav_path = Path(artifact.path)
        assert wav_path.is_file(), f"File does not exist: {wav_path}"
        assert wav_path.stat().st_size > 0, "File size is 0"
        
        with sf.SoundFile(wav_path) as f:
            print(f"WAV Audio Validation: Samplerate={f.samplerate}, Channels={f.channels}, Frames={f.frames}, Duration={f.frames/f.samplerate:.2f}s")
            assert f.frames > 0, "Audio frames is 0"
            assert f.samplerate == 44100, f"Unexpected sample rate {f.samplerate}"

        print("\nTEST B PASSED PERFECTLY.")

    finally:
        db.close()

if __name__ == '__main__':
    run_test_b()
