from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Lesson, LessonVersion


def now():
    return datetime.now(timezone.utc)


def can_publish(db: Session, lesson: Lesson, version: LessonVersion) -> tuple[bool, list[str]]:
    """Publication is source-first. Optional translations/audio/practice never block."""
    problems: list[str] = []
    if not version.source_text.strip() and not version.clean_transcript.strip():
        problems.append('SOURCE_TEXT_MISSING')
    return not problems, problems


def publish(db: Session, lesson: Lesson, version: LessonVersion):
    ok, problems = can_publish(db, lesson, version)
    if not ok:
        return False, problems
    stamp = now()
    version.approved_at = version.approved_at or stamp
    version.published_at = stamp
    lesson.status = 'PUBLISHED'
    db.commit()
    return True, []
