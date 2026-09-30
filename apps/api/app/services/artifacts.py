from pathlib import Path
import hashlib, json, secrets
from sqlalchemy.orm import Session
from ..core.config import STORAGE_ROOT
from ..models import Artifact

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def store_bytes(db: Session, lesson_version_id: int, artifact_type: str, language: str, data: bytes, extension: str, mime_type: str, metadata: dict | None=None) -> Artifact:
    folder = STORAGE_ROOT / 'lesson_versions' / str(lesson_version_id) / artifact_type
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{secrets.token_hex(10)}{extension}'
    path = folder / name
    path.write_bytes(data)
    artifact = Artifact(lesson_version_id=lesson_version_id,type=artifact_type,language=language,path=str(path),mime_type=mime_type,sha256=sha256_file(path),size_bytes=path.stat().st_size,metadata_json=json.dumps(metadata or {},ensure_ascii=False))
    db.add(artifact); db.commit(); db.refresh(artifact)
    return artifact

def register_file(db: Session, lesson_version_id: int, artifact_type: str, language: str, path: Path, mime_type: str, metadata: dict | None=None) -> Artifact:
    path = path.resolve()
    artifact = Artifact(lesson_version_id=lesson_version_id,type=artifact_type,language=language,path=str(path),mime_type=mime_type,sha256=sha256_file(path),size_bytes=path.stat().st_size,metadata_json=json.dumps(metadata or {},ensure_ascii=False))
    db.add(artifact); db.commit(); db.refresh(artifact)
    return artifact
