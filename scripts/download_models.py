from pathlib import Path
import os
from huggingface_hub import snapshot_download

ROOT=Path(__file__).resolve().parents[1]/'apps/api/models'
MODELS=[
    ('ai4bharat/indictrans2-indic-indic-dist-320M',ROOT/'translation'/'indic-indic'),
    ('ai4bharat/indictrans2-en-indic-dist-200M',ROOT/'translation'/'en-indic'),
    ('ai4bharat/indictrans2-indic-en-dist-200M',ROOT/'translation'/'indic-en'),
    ('Systran/faster-whisper-small',ROOT/'stt'/'whisper-small'),
    ('ai4bharat/indic-parler-tts-pretrained',ROOT/'tts'/'indic-parler-tts'),
]
print('BhashaSaathi local model downloader')
print('Destination root:',ROOT)
for repo,dest in MODELS:
    dest.mkdir(parents=True,exist_ok=True)
    print(f'\n{repo}\n  -> {dest}')
    snapshot_download(repo_id=repo,local_dir=str(dest),local_dir_use_symlinks=False,token=os.getenv('HF_TOKEN') or None)
    print('  complete')
print('\nRun scripts/verify_models.py after download.')
