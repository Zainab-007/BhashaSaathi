from pathlib import Path
from .model_manager import manager

def transcribe(path:Path, language="hi"):
    model=manager.stt_model()
    segments,info=model.transcribe(str(path),language=language,beam_size=5,vad_filter=True)
    text=" ".join(s.text.strip() for s in segments).strip()
    return {"text":text,"language":info.language,"probability":float(info.language_probability)}
