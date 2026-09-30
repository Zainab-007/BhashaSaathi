from pathlib import Path
import os
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1] / 'apps' / 'api' / 'models' / 'stt' / 'indic-conformer-600m-multilingual'
REPO = 'ai4bharat/indic-conformer-600m-multilingual'

print('Santali STT downloader')
print('Model:', REPO)
print('Destination:', ROOT)
print('This is an optional gated AI4Bharat checkpoint; accept its HF terms and set HF_TOKEN if required.')
ROOT.mkdir(parents=True, exist_ok=True)
snapshot_download(repo_id=REPO, local_dir=str(ROOT), local_dir_use_symlinks=False, token=os.getenv('HF_TOKEN') or None)
print('Santali STT download complete.')
