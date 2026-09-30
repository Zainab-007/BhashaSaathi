from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
from app.models import User, Group, Lesson, LessonVersion, Translation, Artifact, PracticeQuestion, Membership
from app.core.security import create_access_token

def run_full_regression():
    print("=" * 80)
    print("BHASHASAATHI COMPLETE APPLICATION & TTS REGRESSION AUDIT")
    print("=" * 80)

    client = TestClient(app)
    db: Session = SessionLocal()

    try:
        # 1. Health check
        print("\n[1] Health Check")
        res = client.get("/api/v1/health")
        assert res.status_code == 200
        print("GET /api/v1/health -> 200 OK")

        # 2. Model Health
        print("\n[2] Model Health Check")
        res = client.get("/api/v1/health/models")
        assert res.status_code == 200
        health = res.json()
        print(f"Device: {health['device']}, CUDA: {health['cuda_available']}, TTS Health: {health['tts']}")

        # 3. Teacher Authentication
        print("\n[3] Authentication")
        teacher = db.query(User).filter_by(account_role='user').first()
        if not teacher:
            teacher = User(name='Teacher Maya', email='maya@test.com', password_hash='dummy', account_role='user')
            db.add(teacher)
            db.commit()
            db.refresh(teacher)

        teacher_token = create_access_token(teacher)
        t_headers = {"Authorization": f"Bearer {teacher_token}"}

        # 4. Classroom / Group
        group = db.query(Group).filter_by(owner_id=teacher.id).first()
        if not group:
            group = Group(name='Grade 5 Science', join_code='G5SCI1', grade='Grade 5', subject='Science', owner_id=teacher.id)
            db.add(group)
            db.commit()
            db.refresh(group)

        # 5. Lesson Creation
        print("\n[4] Lesson Creation (Source=English)")
        res = client.post(
            f"/api/v1/groups/{group.id}/lessons",
            headers=t_headers,
            json={
                "title": "Photosynthesis and Plant Food",
                "grade": "Grade 5",
                "subject": "Science",
                "topic": "Plants",
                "source_language": "eng_Latn",
                "source_text": "Green leaves use sunlight and water to make food for the plant."
            }
        )
        assert res.status_code == 200
        lesson_data = res.json()
        lesson_id = lesson_data["id"]
        print(f"Lesson created: ID={lesson_id}, Title='{lesson_data['title']}'")

        # 6. Concept Extraction
        print("\n[5] Concept Extraction")
        res = client.post(f"/api/v1/lessons/{lesson_id}/prepare-concepts", headers=t_headers)
        assert res.status_code == 200
        concepts = res.json()["concepts"]
        print(f"Concepts extracted: {[c['label'] for c in concepts]}")

        # 7. Translation: Hindi
        print("\n[6] Translation to Hindi (Optional Target)")
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/translate",
            headers=t_headers,
            json={
                "target_language": "hin_Deva",
                "source_text": "Green leaves use sunlight and water to make food for the plant.",
                "clean_transcript": "Green leaves use sunlight and water to make food for the plant.",
                "source_language": "eng_Latn",
                "force": False
            }
        )
        assert res.status_code == 200
        hi_trans = res.json()
        print(f"Hindi text: '{hi_trans['text']}', confidence: {hi_trans['confidence']}")

        # 8. Approval: Hindi
        print("\n[7] Translation Approval (Hindi)")
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/verify-translation",
            headers=t_headers,
            json={
                "translation_id": hi_trans["translation_id"],
                "approved": True,
                "edited_text": hi_trans["text"],
                "native_review_status": "NOT_REVIEWED"
            }
        )
        assert res.status_code == 200
        print(f"Hindi verification status: {res.json()['verification_status']}")

        # 9. Translation: Marathi
        print("\n[8] Translation to Marathi (Optional Target)")
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/translate",
            headers=t_headers,
            json={
                "target_language": "mar_Deva",
                "source_text": "Green leaves use sunlight and water to make food for the plant.",
                "clean_transcript": "Green leaves use sunlight and water to make food for the plant.",
                "source_language": "eng_Latn",
                "force": False
            }
        )
        assert res.status_code == 200
        mr_trans = res.json()
        print(f"Marathi text: '{mr_trans['text']}', confidence: {mr_trans['confidence']}")

        # 10. Approval: Marathi
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/verify-translation",
            headers=t_headers,
            json={
                "translation_id": mr_trans["translation_id"],
                "approved": True,
                "edited_text": mr_trans["text"],
                "native_review_status": "PENDING"
            }
        )
        assert res.status_code == 200
        print(f"Marathi verification status: {res.json()['verification_status']}")

        # 11. Translation: Santali (Temporary)
        print("\n[9] Translation to Santali (Temporary Optional Target)")
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/translate",
            headers=t_headers,
            json={
                "target_language": "sat_Olck",
                "source_text": "Green leaves use sunlight and water to make food for the plant.",
                "clean_transcript": "Green leaves use sunlight and water to make food for the plant.",
                "source_language": "eng_Latn",
                "force": False
            }
        )
        assert res.status_code == 200
        sat_trans = res.json()
        print(f"Santali text: '{sat_trans['text']}', status: {sat_trans['verification_status']}")

        # 12. Save Temporary Santali
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/translation/{sat_trans['translation_id']}/temporary",
            headers=t_headers,
            json={"edited_text": sat_trans["text"]}
        )
        assert res.status_code == 200
        print("Saved Temporary Santali without approval gate.")

        # 13. Practice Generation & Approval
        print("\n[10] Practice Question Generation")
        res = client.post(
            f"/api/v1/lessons/{lesson_id}/generate-practice",
            headers=t_headers,
            json={"language": "eng_Latn", "count": 2}
        )
        assert res.status_code == 200
        questions = res.json()
        print(f"Generated {len(questions)} questions")
        if questions:
            res = client.post(
                f"/api/v1/lessons/{lesson_id}/practice/{questions[0]['id']}/approve",
                headers=t_headers,
                json={"approved": True}
            )
            assert res.status_code == 200
            print(f"Approved Question ID: {questions[0]['id']}")

        # 14. Worksheet Generation
        print("\n[11] Worksheet Preview Generation")
        res = client.post(f"/api/v1/lessons/{lesson_id}/generate-worksheet", headers=t_headers)
        assert res.status_code == 200
        print("Worksheet HTML generated successfully.")

        # 15. Audio Generation: English & Hindi
        print("\n[12] Audio Generation (English & Hindi)")
        res_en = client.post(f"/api/v1/lessons/{lesson_id}/preparations/tts/eng_Latn", headers=t_headers)
        assert res_en.status_code == 200
        job_en = res_en.json()["job_id"]

        res_hi = client.post(f"/api/v1/lessons/{lesson_id}/preparations/tts/hin_Deva", headers=t_headers)
        assert res_hi.status_code == 200
        job_hi = res_hi.json()["job_id"]

        print(f"Enqueued TTS jobs: English={job_en}, Hindi={job_hi}")

        # Poll preparations until both are READY
        t0 = time.perf_counter()
        for _ in range(240):
            time.sleep(1.0)
            res_poll = client.get(f"/api/v1/lessons/{lesson_id}/preparations", headers=t_headers)
            assert res_poll.status_code == 200
            jobs = res_poll.json()
            ready_jobs = [j for j in jobs if j["status"] == "READY"]
            failed_jobs = [j for j in jobs if j["status"] == "FAILED"]
            if failed_jobs:
                raise AssertionError(f"Preparation job failed: {failed_jobs}")
            if len(ready_jobs) >= 2:
                print(f"All audio jobs READY in {time.perf_counter() - t0:.1f}s")
                break
        else:
            raise AssertionError(f"Timed out waiting for audio preparations. Current jobs: {jobs}")

        # 16. Publish Lesson
        print("\n[13] Publish Lesson")
        res = client.post(f"/api/v1/lessons/{lesson_id}/publish", headers=t_headers)
        assert res.status_code == 200
        pack = res.json()
        assert pack["lesson"]["status"] == "PUBLISHED"
        print("Lesson PUBLISHED successfully!")

        # 17. Student Access Test
        print("\n[14] Student Access & Audio Playback")
        student = db.query(User).filter(User.id != teacher.id).first()
        if not student:
            student = User(name='Student Rahul', email='rahul@test.com', password_hash='dummy', account_role='user')
            db.add(student)
            db.commit()
            db.refresh(student)

        # Ensure membership
        mem = db.query(Membership).filter_by(group_id=group.id, user_id=student.id).first()
        if not mem:
            mem = Membership(group_id=group.id, user_id=student.id, role='student')
            db.add(mem)
            db.commit()

        student_token = create_access_token(student)
        s_headers = {"Authorization": f"Bearer {student_token}"}

        # Student fetches lesson
        res_stu = client.get(f"/api/v1/lessons/{lesson_id}", headers=s_headers)
        assert res_stu.status_code == 200
        stu_pack = res_stu.json()
        assert len(stu_pack["artifacts"]) >= 2
        print(f"Student retrieved published pack with {len(stu_pack['artifacts'])} audio artifacts.")

        # Student downloads audio artifact
        audio_art = stu_pack["artifacts"][0]
        res_audio = client.get(audio_art["url"], headers=s_headers)
        assert res_audio.status_code == 200
        assert res_audio.headers.get("content-type") == "audio/wav"
        
        with sf.SoundFile(io.BytesIO(res_audio.content)) as f:
            print(f"Student received playable WAV: {f.frames/f.samplerate:.2f}s, {f.samplerate}Hz, channels={f.channels}")
            assert f.frames > 0

        print("\n" + "=" * 80)
        print("ALL REGRESSION TESTS PASSED! FULL SYSTEM VERIFIED END-TO-END.")
        print("=" * 80)

    finally:
        db.close()

if __name__ == '__main__':
    run_full_regression()
