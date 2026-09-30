from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent.parent
MODELS_ROOT = ROOT / "models"
STORAGE_ROOT = ROOT / "storage"
DATA_ROOT = PROJECT_ROOT / "data"

class Settings(BaseSettings):
    app_name: str = "BhashaSaathi"
    database_url: str = f"sqlite:///{DATA_ROOT / 'bhashasaathi.db'}"
    jwt_secret: str = "change-this"
    jwt_expire_minutes: int = 720
    demo_mode: bool = False
    translation_enabled: bool = True
    stt_enabled: bool = True
    tts_enabled: bool = True
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

settings = Settings()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

def origins():
    return [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
