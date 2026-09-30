from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Boolean, Float, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from ..db.session import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    preferred_language: Mapped[str] = mapped_column(String(30), default='eng_Latn')
    account_role: Mapped[str] = mapped_column(String(20), default='user', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Group(Base):
    __tablename__ = 'groups'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    join_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    grade: Mapped[str] = mapped_column(String(50), default='Grade 2')
    subject: Mapped[str] = mapped_column(String(100), default='Environmental Studies')
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = 'memberships'
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    role: Mapped[str] = mapped_column(String(30), default='student')
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('group_id', 'user_id', name='uq_membership_group_user'),)


class Lesson(Base):
    __tablename__ = 'lessons'
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    grade: Mapped[str] = mapped_column(String(50), default='Grade 2')
    subject: Mapped[str] = mapped_column(String(100), default='Environmental Studies')
    topic: Mapped[str] = mapped_column(String(160), default='')
    source_language: Mapped[str] = mapped_column(String(30), default='eng_Latn')
    status: Mapped[str] = mapped_column(String(40), default='DRAFT', index=True)
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey('lesson_versions.id'), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class LessonVersion(Base):
    __tablename__ = 'lesson_versions'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey('lessons.id'), index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    source_text: Mapped[str] = mapped_column(Text, default='')
    clean_transcript: Mapped[str] = mapped_column(Text, default='')
    learning_objectives_json: Mapped[str] = mapped_column(Text, default='[]')
    concepts_json: Mapped[str] = mapped_column(Text, default='[]')
    ai_explanation_json: Mapped[str] = mapped_column(Text, default='{}')
    activities_json: Mapped[str] = mapped_column(Text, default='[]')
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Translation(Base):
    __tablename__ = 'translations'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_version_id: Mapped[int] = mapped_column(ForeignKey('lesson_versions.id'), index=True)
    source_language: Mapped[str] = mapped_column(String(30))
    target_language: Mapped[str] = mapped_column(String(30))
    translated_text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    flags_json: Mapped[str] = mapped_column(Text, default='[]')
    validation_json: Mapped[str] = mapped_column(Text, default='{}')
    verification_status: Mapped[str] = mapped_column(String(40), default='UNVERIFIED')
    teacher_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    native_review_status: Mapped[str] = mapped_column(String(30), default='NOT_REVIEWED')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint('lesson_version_id', 'target_language', name='uq_translation_version_language'),)


class Artifact(Base):
    __tablename__ = 'artifacts'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_version_id: Mapped[int] = mapped_column(ForeignKey('lesson_versions.id'), index=True)
    type: Mapped[str] = mapped_column(String(50))
    language: Mapped[str] = mapped_column(String(30), default='')
    path: Mapped[str] = mapped_column(String(800))
    mime_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PracticeQuestion(Base):
    __tablename__ = 'practice_questions'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_version_id: Mapped[int] = mapped_column(ForeignKey('lesson_versions.id'), index=True)
    concept_id: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text, default='[]')
    correct_answer: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default='')
    language: Mapped[str] = mapped_column(String(30), default='eng_Latn')
    question_type: Mapped[str] = mapped_column(String(30), default='mcq')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)


class Attempt(Base):
    __tablename__ = 'attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey('lessons.id'), index=True)
    lesson_version_id: Mapped[int] = mapped_column(ForeignKey('lesson_versions.id'))
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    answers_json: Mapped[str] = mapped_column(Text, default='{}')
    score: Mapped[float] = mapped_column(Float, default=0)
    concept_results_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PreparationJob(Base):
    __tablename__ = 'preparation_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_version_id: Mapped[int] = mapped_column(ForeignKey('lesson_versions.id'), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(30), default='')
    status: Mapped[str] = mapped_column(String(20), default='QUEUED', index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint('lesson_version_id', 'kind', 'language', name='uq_preparation_job_version_kind_language'),)


class TranslationFeedback(Base):
    __tablename__ = 'translation_feedback'
    id: Mapped[int] = mapped_column(primary_key=True)
    translation_id: Mapped[int] = mapped_column(ForeignKey('translations.id'), index=True)
    lesson_version_id: Mapped[int] = mapped_column(ForeignKey('lesson_versions.id'), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    status: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint('translation_id', 'student_id', name='uq_translation_feedback_student'),)


class SyncRecord(Base):
    __tablename__ = 'sync_records'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    device_id: Mapped[str] = mapped_column(String(120), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint('user_id', 'device_id', 'operation', 'entity_id', name='uq_sync_user_device_operation_entity'),)


Index('ix_lessons_group_status', Lesson.group_id, Lesson.status)
Index('ix_versions_lesson_number', LessonVersion.lesson_id, LessonVersion.version_number)
Index('ix_sync_user_processed', SyncRecord.user_id, SyncRecord.processed_at)
