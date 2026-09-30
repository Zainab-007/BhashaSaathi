from __future__ import annotations

import io
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
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import SessionLocal
from app.models import User, Group, Lesson, LessonVersion, PreparationJob, Artifact
from app.core.security import create_access_token

def run_test_c():
    print("=" * 75)
    print("TEST C: Full FastAPI Endpoints & Client Integration Test")
    print("=" * 75)

    client = TestClient(app)
    db: Session = SessionLocal()

    try:
        # 1. Setup / find teacher user
        user = db.query(User).first()
        if not user:
            user = User(name='Test Teacher', email='teacher_api@test.com', password_hash='dummy', account_role='user')
            db.add(user)
            db.commit()
            db.refresh(user)
        
        group = db.query(Group).filter_by(owner_id=user.id).first()
        if not group:
            group = Group(name='Class 5B', join_code='C5B123', grade='Grade 5', subject='Science', owner_id=user.id)
            db.add(group)
            db.commit()
            db.refresh(group)

        token = create_access_token(user)
        headers = {"Authorization": f"Bearer {token}"}


        # 2. Create lesson via API
        print("\n--- 1. Creating Lesson via API ---")
        res = client.post(
            f"/api/v1/groups/{group.id}/lessons",
            headers=headers,
            json={
                "title": "Water Cycle Lesson",
                "grade": "Grade 5",
                "subject": "Science",
                "topic": "Water",
                "source_language": "eng_Latn",
                "source_text": "Water evaporates from lakes and forms white clouds in the sky."
            }
        )
        assert res.status_code == 200, f"Failed to create lesson: {res.text}"
        lesson_data = res.json()
        lesson_id = lesson_data["id"]
        print(f"Created Lesson ID: {lesson_id}")

        # 3. Queue English Audio via API
        print("\n--- 2. Queueing English Audio (POST /lessons/{id}/preparations/tts/eng_Latn) ---")
        res = client.post(f"/api/v1/lessons/{lesson_id}/preparations/tts/eng_Latn", headers=headers)
        assert res.status_code == 200, f"Failed to queue TTS: {res.text}"
        prep_data = res.json()
        job_id = prep_data["job_id"]
        print(f"Job enqueued: ID={job_id}, status={prep_data['status']}")

        # 4. Test Deduplication: Click Generate 5 times rapidly
        print("\n--- 3. Testing Deduplication (5 rapid clicks) ---")
        for i in range(5):
            res_dup = client.post(f"/api/v1/lessons/{lesson_id}/preparations/tts/eng_Latn", headers=headers)
            assert res_dup.status_code == 200
            assert res_dup.json()["job_id"] == job_id, "Deduplication failed to return identical job_id"
        print("Deduplication test passed: all requests mapped to single job.")

        # 5. Poll preparation status until READY
        print("\n--- 4. Polling Preparation Status ---")
        t0 = time.perf_counter()
        ready = False
        for _ in range(60):
            time.sleep(1.0)
            res_poll = client.get(f"/api/v1/lessons/{lesson_id}/preparations", headers=headers)
            assert res_poll.status_code == 200
            jobs = res_poll.json()
            tts_job = next((j for j in jobs if j["id"] == job_id), None)
            if tts_job:
                elapsed = time.perf_counter() - t0
                print(f"[{elapsed:.1f}s] Job {job_id} Status: {tts_job['status']}")
                if tts_job["status"] == "READY":
                    ready = True
                    break
                elif tts_job["status"] == "FAILED":
                    raise AssertionError(f"Job failed: {tts_job['error']}")

        assert ready, "Job did not complete within timeout"

        # 6. Fetch Lesson Pack to get Artifact URL
        print("\n--- 5. Verifying Lesson Pack & Artifact Metadata ---")
        res_pack = client.get(f"/api/v1/lessons/{lesson_id}", headers=headers)
        assert res_pack.status_code == 200
        pack = res_pack.json()
        audio_artifacts = [a for a in pack["artifacts"] if a["type"] == "audio" and a["language"] == "eng_Latn"]
        assert len(audio_artifacts) == 1, f"Expected 1 audio artifact, found: {len(audio_artifacts)}"
        artifact = audio_artifacts[0]
        artifact_id = artifact["id"]
        artifact_url = artifact["url"]
        print(f"Artifact ID: {artifact_id}, URL: {artifact_url}, MIME: {artifact['mime_type']}, Size: {artifact['size_bytes']} bytes")

        # 7. Fetch Audio Artifact via HTTP GET /api/v1/artifacts/{id}
        print("\n--- 6. Fetching Audio Stream (GET /api/v1/artifacts/{id}) ---")
        res_audio = client.get(artifact_url, headers=headers)
        assert res_audio.status_code == 200, f"Failed to fetch artifact: {res_audio.status_code}"
        assert res_audio.headers.get("content-type") == "audio/wav", f"Unexpected content-type: {res_audio.headers.get('content-type')}"
        audio_bytes = res_audio.content
        assert len(audio_bytes) == artifact["size_bytes"], "Size mismatch between metadata and payload"

        # 8. Validate Audio Payload
        buffer = io.BytesIO(audio_bytes)
        with sf.SoundFile(buffer) as f:
            print(f"Browser-bound WAV verified: Samplerate={f.samplerate}, Channels={f.channels}, Frames={f.frames}, Duration={f.frames/f.samplerate:.2f}s")
            assert f.frames > 0
            assert f.samplerate == 44100
            assert f.channels == 1

        print("\n" + "=" * 75)
        print("TEST C PASSED: END-TO-END FASTAPI HTTP TTS PIPELINE FULLY VERIFIED")
        print("=" * 75)

    finally:
        db.close()

if __name__ == '__main__':
    run_test_c()
