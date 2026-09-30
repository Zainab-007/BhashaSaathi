from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = 'development'
    app_version: str = '25.0.0'
    secret_key: str = 'change-this-local-secret-key-32-bytes-minimum-demo-value'
    database_url: str = 'sqlite:///./data/bhashasaathi.db'
    storage_dir: str = './data/storage'
    model_dir: str = './models'
    hf_token: str | None = None
    cors_origins: str = 'http://localhost:5173,http://127.0.0.1:5173'
    api_base_url: str = 'http://127.0.0.1:8000/api/v1'
    demo_mode: bool = False
    translation_enabled: bool = True
    stt_enabled: bool = True
    tts_enabled: bool = True
    llm_enabled: bool = False
    cpu_only: bool = False
    translation_cpu_only: bool = True
    stt_cpu_only: bool = True
    tts_cpu_only: bool = False
    santali_stt_enabled: bool = True
    santali_stt_cpu_only: bool = True
    santali_stt_model_dir: str = './models/stt/indic-conformer-600m-multilingual'
    keep_models_loaded: bool = True
    log_level: str = 'INFO'
    max_audio_mb: int = 30
    max_translation_chars: int = 12000
    max_live_segment_ms: int = 2200
    live_end_silence_ms: int = 280
    live_queue_size: int = 2
    live_translation_beams: int = 1
    translation_route_cache_limit: int = 2
    live_tts_stream_seconds: float = 0.5
    live_tts_stream_timeout: float = 12.0
    live_max_segment_ms_santali: int = 2800
    text_translation_beams: int = 1
    parler_description_tokenizer: str = ''
    live_history_ttl_hours: int = 24
    torch_threads: int = 0
    translation_request_timeout_s: float = 90.0
    preparation_tts_timeout_s: float = 600.0
    preparation_practice_timeout_s: float = 180.0
    translation_max_new_tokens: int = 512
    live_translation_max_new_tokens: int = 96
    translation_inference_lock: bool = True
    translation_input_token_budget: int = 220
    translation_batch_size: int = 4
    defer_tts_warmup_until_approval: bool = True

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )


settings = Settings()


def _resolve_local_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (API_ROOT / path).resolve()


STORAGE_ROOT = _resolve_local_path(settings.storage_dir)
MODELS_ROOT = _resolve_local_path(settings.model_dir)
DATA_ROOT = API_ROOT / 'data'

if settings.database_url.startswith('sqlite:///'):
    sqlite_path = Path(settings.database_url.removeprefix('sqlite:///'))
    if not sqlite_path.is_absolute():
        settings.database_url = f'sqlite:///{(API_ROOT / sqlite_path).resolve().as_posix()}'

STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
MODELS_ROOT.mkdir(parents=True, exist_ok=True)
DATA_ROOT.mkdir(parents=True, exist_ok=True)


def origins() -> list[str]:
    return [item.strip() for item in settings.cors_origins.split(',') if item.strip()]
