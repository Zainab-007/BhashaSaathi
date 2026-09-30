from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Run from repository root or from apps/api; make the API package importable.
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[1]
API_ROOT = REPO_ROOT / 'apps' / 'api'
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.model_manager import manager  # noqa: E402

LANGUAGES = {'eng_Latn', 'hin_Deva', 'mar_Deva', 'sat_Olck'}

parser = argparse.ArgumentParser(description='Pre-generate BhashaSaathi lesson WAV audio locally.')
parser.add_argument('--text-file', required=True, help='UTF-8 text file containing the approved text.')
parser.add_argument('--language', required=True, choices=sorted(LANGUAGES))
parser.add_argument('--output', required=True, help='Output WAV path.')
parser.add_argument('--speaker', default='classroom_teacher')
args = parser.parse_args()

text = Path(args.text_file).read_text(encoding='utf-8').strip()
if not text:
    raise SystemExit('Text file is empty.')

out = Path(args.output).resolve()
print('BhashaSaathi offline audio generator')
print(f'Language: {args.language}')
print(f'Output:   {out}')
print('Generating with the configured TTS device...')
manager.synthesize(text, out, args.language, args.speaker)
print(f'READY: {out}')
