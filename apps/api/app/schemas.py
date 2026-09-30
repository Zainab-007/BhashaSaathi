from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

LanguageCode = Literal['eng_Latn', 'hin_Deva', 'mar_Deva', 'sat_Olck']


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class GroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    grade: str = Field(default='Grade 2', min_length=1, max_length=50)
    subject: str = Field(default='Environmental Studies', min_length=1, max_length=100)


class JoinIn(BaseModel):
    join_code: str = Field(min_length=4, max_length=32)


class LessonCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    source_text: str = Field(default='', max_length=20000)
    source_language: LanguageCode = 'eng_Latn'
    grade: str = Field(default='Grade 2', min_length=1, max_length=50)
    subject: str = Field(default='Environmental Studies', min_length=1, max_length=100)
    topic: str = Field(default='', max_length=160)


class LessonPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    source_text: str | None = Field(default=None, max_length=20000)
    clean_transcript: str | None = Field(default=None, max_length=20000)
    topic: str | None = Field(default=None, max_length=160)
    learning_outcome: str | None = Field(default=None, max_length=1000)
    concepts: list[str] | None = Field(default=None, max_length=30)
    source_language: LanguageCode | None = None


class VersionCreateIn(BaseModel):
    source_text: str | None = Field(default=None, max_length=20000)
    clean_transcript: str | None = Field(default=None, max_length=20000)
    learning_outcome: str | None = Field(default=None, max_length=1000)
    concepts: list[str] | None = Field(default=None, max_length=30)


class TranslateIn(BaseModel):
    target_language: LanguageCode
    source_text: str | None = Field(default=None, max_length=20000)
    clean_transcript: str | None = Field(default=None, max_length=20000)
    source_language: LanguageCode | None = None
    force: bool = False


class VerifyIn(BaseModel):
    translation_id: int
    approved: bool
    edited_text: str | None = Field(default=None, max_length=20000)
    native_review_status: Literal['NOT_REVIEWED', 'PENDING', 'REJECTED'] | None = None


class PracticeGenerateIn(BaseModel):
    language: LanguageCode = 'eng_Latn'
    count: int = Field(default=4, ge=1, le=10)


class PracticeApprovalIn(BaseModel):
    approved: bool


class AttemptIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lesson_id: int
    lesson_version_id: int | None = None
    language: LanguageCode = 'eng_Latn'
    answers: dict[str, Any] = Field(default_factory=dict)
    offline_created_at: str | None = None


class TranslationFeedbackIn(BaseModel):
    status: Literal['CONFIRMED', 'REPORTED']
    comment: str = Field(default='', max_length=1000)


class SyncPushIn(BaseModel):
    device_id: str = Field(min_length=3, max_length=120)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class TTSIn(BaseModel):
    target_language: LanguageCode = 'hin_Deva'
    speaker: str = Field(default='classroom_teacher', min_length=1, max_length=100)



class LiveWarmIn(BaseModel):
    source_language: LanguageCode = 'eng_Latn'
    target_language: LanguageCode = 'hin_Deva'


class WarmTranslationIn(BaseModel):
    source_language: LanguageCode
    target_language: LanguageCode
