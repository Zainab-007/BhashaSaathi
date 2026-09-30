from __future__ import annotations

import importlib
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'apps' / 'api'
MODEL_ROOT = API / 'models'

print('BhashaSaathi environment check')
print('Python:', sys.version.split()[0])
print('OS:', platform.platform())
print('Node:', end=' ')
node = shutil.which('node')
if node:
    import subprocess
    print(subprocess.check_output([node, '--version'], text=True).strip())
else:
    print('NOT FOUND')
print('npm:', end=' ')
npm = shutil.which('npm')
if npm:
    import subprocess
    print(subprocess.check_output([npm, '--version'], text=True).strip())
else:
    print('NOT FOUND')
print('Model root:', MODEL_ROOT)

packages = [
    'fastapi', 'sqlalchemy', 'alembic', 'jwt', 'argon2', 'torch',
    'transformers', 'huggingface_hub', 'parler_tts', 'IndicTransToolkit',
    'faster_whisper', 'soundfile', 'sentencepiece',
]
missing = []
for name in packages:
    try:
        module = importlib.import_module(name)
        print(f'OK   {name}: {getattr(module, "__version__", "installed")}')
    except Exception as exc:
        print(f'MISS {name}: {exc}')
        missing.append(name)

print('\nModel folders')
checks = {
    'IndicTrans2 indic-indic': MODEL_ROOT / 'translation' / 'indic-indic',
    'IndicTrans2 en-indic': MODEL_ROOT / 'translation' / 'en-indic',
    'IndicTrans2 indic-en': MODEL_ROOT / 'translation' / 'indic-en',
    'Whisper small': MODEL_ROOT / 'stt' / 'whisper-small',
    'Indic Parler-TTS': MODEL_ROOT / 'tts' / 'indic-parler-tts',
    'FLAN-T5 tokenizer support': MODEL_ROOT / 'tts' / 'flan-t5-large',
}
for label, path in checks.items():
    print(f'{"OK   " if path.is_dir() else "MISS "}{label}: {path}')

print('\nResult:', 'PASS' if not missing else 'CHECK MISSING PACKAGES')
raise SystemExit(0 if not missing else 1)
