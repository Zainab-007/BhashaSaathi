from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / 'apps' / 'api' / 'models'
TARGET = MODEL_ROOT / 'tts' / 'flan-t5-large'


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        print('huggingface_hub is unavailable:', exc)
        return 1

    print('Preparing FLAN-T5 description tokenizer support.')
    print('Target:', TARGET)
    print('Only tokenizer/config assets will be downloaded; no FLAN-T5 model weights.')
    try:
        path = snapshot_download(
            repo_id='google/flan-t5-large',
            local_dir=str(TARGET),
            token=os.getenv('HF_TOKEN') or None,
            allow_patterns=[
                'config.json',
                'tokenizer.json',
                'tokenizer_config.json',
                'special_tokens_map.json',
                'spiece.model',
            ],
        )
    except Exception as exc:
        print('\nTTS SUPPORT SETUP FAILED')
        print(type(exc).__name__ + ':', exc)
        print('Check internet access and rerun this command once. The main TTS model weights are not redownloaded.')
        return 1

    print('\nDownloaded support assets to:', path)
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(TARGET), local_files_only=True)
        print('FLAN-T5 tokenizer: OK')
        print('Tokenizer:', type(tokenizer).__name__)
        print('Vocab:', tokenizer.vocab_size)
    except Exception as exc:
        print('Tokenizer validation failed:', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
