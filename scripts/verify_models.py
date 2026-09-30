from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'apps' / 'api' / 'models'


def candidate_dirs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    result = [folder]
    result.extend(path for path in folder.iterdir() if path.is_dir())
    return result


def files_ok(folder: Path, names: tuple[str, ...]) -> bool:
    return any(
        path.is_dir() and any((path / name).is_file() for name in names)
        for path in candidate_dirs(folder)
    )


def main() -> int:
    checks = [
        ('IndicTrans2 Indic-Indic', ROOT / 'translation' / 'indic-indic', ('config.json', 'model.safetensors', 'pytorch_model.bin', 'model.bin')),
        ('IndicTrans2 En-Indic', ROOT / 'translation' / 'en-indic', ('config.json', 'model.safetensors', 'pytorch_model.bin', 'model.bin')),
        ('IndicTrans2 Indic-En', ROOT / 'translation' / 'indic-en', ('config.json', 'model.safetensors', 'pytorch_model.bin', 'model.bin')),
        ('Whisper Small', ROOT / 'stt' / 'whisper-small', ('model.bin',)),
        ('Indic Parler-TTS', ROOT / 'tts' / 'indic-parler-tts', ('config.json', 'model.safetensors', 'pytorch_model.bin')),
    ]
    ok = True
    for name, folder, expected in checks:
        present = files_ok(folder, expected)
        print(('OK   ' if present else 'MISS ') + f'{name} -> {folder}')
        if not present:
            print('      Expected at least one of:', ', '.join(expected))
            ok = False

    optional_santali = ROOT / 'stt' / 'indic-conformer-600m-multilingual'
    santali_present = files_ok(optional_santali, ('config.json',))
    print(('OK   ' if santali_present else 'INFO ') + f'Optional Santali STT -> {optional_santali}')
    if not santali_present:
        print('      Santali text translation/TTS still work. Install the gated ASR only for Santali speech input.')

    tts_support = ROOT / 'tts' / 'flan-t5-large'
    tokenizer_ok = tts_support.is_dir() and (tts_support / 'tokenizer_config.json').is_file() and (
        (tts_support / 'tokenizer.json').is_file() or (tts_support / 'spiece.model').is_file()
    )
    print(('OK   ' if tokenizer_ok else 'WARN ') + f'FLAN-T5 description tokenizer -> {tts_support}')
    if not tokenizer_ok:
        print('      Run: python scripts\\download_tts_support.py')

    print('\nCore model folders:', 'PASS' if ok else 'FAIL')
    print('TTS runtime support:', 'READY' if tokenizer_ok else 'NOT READY')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
