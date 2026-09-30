from pathlib import Path
from .model_manager import manager

def synthesize(text: str, out: Path, language: str, speaker: str = 'classroom_teacher') -> dict:
    return manager.synthesize(text, out, language, speaker)

