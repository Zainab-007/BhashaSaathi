from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'api'))

from app.services import live_translation as live  # noqa: E402


class FakeTTS:
    @staticmethod
    def speaker_for(language: str) -> str:
        return {'eng_Latn': 'Thoma', 'hin_Deva': 'Rohit', 'mar_Deva': 'Sanjay', 'sat_Olck': 'Arjun'}[language]

    @staticmethod
    def stream_audio(text: str, language: str, speaker: str, *, stop_event, stream_seconds: float, timeout: float):
        rate = 16000
        t = np.arange(int(rate * 0.08), dtype=np.float32) / rate
        piece = (0.08 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        yield piece, rate
        yield piece, rate


class FakeManager:
    tts = FakeTTS()

    @staticmethod
    def warm_live(source: str, target: str):
        return {'source': source, 'target': target, 'tts': True, 'stt': True, 'translation': True}

    @staticmethod
    def transcribe_audio(audio, language):
        return {'text': 'hello classroom', 'language': language, 'probability': 0.99, 'segments': []}

    @staticmethod
    def translate(text, src, tgt, *, fast=False):
        return 'नमस्कार वर्ग'


async def main() -> None:
    original = live.manager
    live.manager = FakeManager()
    try:
        session = live.LiveTranslationSession('eng_Latn', 'hin_Deva', asyncio.get_running_loop())
        await session.initialize()
        # 0.75s of speech followed by 0.36s of silence; 16 kHz means the backend
        # should NOT resample these chunks again.
        speech = (0.2 * np.sin(2 * np.pi * 220 * (np.arange(12000) / 16000))).astype(np.float32)
        silence = np.zeros(6000, dtype=np.float32)
        session.push_audio((speech * 32767).astype('<i2').tobytes(), 16000)
        session.push_audio((silence * 32767).astype('<i2').tobytes(), 16000)

        events: list[tuple[str, object]] = []
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            while True:
                try:
                    events.append(session.outgoing.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if any(kind == 'json' and isinstance(payload, dict) and payload.get('type') == 'audio_end' for kind, payload in events):
                break
            await asyncio.sleep(0.03)

        session.close()
        json_events = [payload for kind, payload in events if kind == 'json']
        types = [payload.get('type') for payload in json_events if isinstance(payload, dict)]
        assert 'transcript' in types, types
        assert 'translation' in types, types
        assert 'audio_start' in types, types
        assert 'audio_end' in types, types
        audio_start = next(payload for payload in json_events if payload.get('type') == 'audio_start')
        assert audio_start.get('mode') == 'pcm', audio_start
        assert any(kind == 'bytes' for kind, _ in events), 'No streamed PCM bytes were emitted.'
        print('Live pipeline protocol test: PASS')

        # Same protocol contract for Santali as a source language. The real
        # runtime swaps in IndicConformer; this test proves the language routing
        # and streamed-audio contract do not special-case English/Hindi/Marathi.
        session = live.LiveTranslationSession('sat_Olck', 'hin_Deva', asyncio.get_running_loop())
        await session.initialize()
        session.push_audio((speech * 32767).astype('<i2').tobytes(), 16000)
        session.push_audio((silence * 32767).astype('<i2').tobytes(), 16000)
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            if any(kind == 'json' and isinstance(payload, dict) and payload.get('type') == 'audio_end' for kind, payload in list(session.outgoing._queue)):
                break
            await asyncio.sleep(0.03)
        events = []
        while True:
            try:
                events.append(session.outgoing.get_nowait())
            except asyncio.QueueEmpty:
                break
        session.close()
        json_events = [payload for kind, payload in events if kind == 'json']
        types = [payload.get('type') for payload in json_events if isinstance(payload, dict)]
        assert 'transcript' in types and 'translation' in types and 'audio_end' in types, types
        print('Santali live routing protocol test: PASS')
    finally:
        live.manager = original


if __name__ == '__main__':
    asyncio.run(main())
