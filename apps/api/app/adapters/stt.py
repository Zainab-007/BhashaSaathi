from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import torch
import soundfile as sf

LANGUAGE_TO_WHISPER = {
    'hin_Deva': 'hi',
    'eng_Latn': 'en',
    'mar_Deva': 'mr',
}

LANGUAGE_TO_INDIC_CONFORMER = {
    'sat_Olck': 'sat',
}


class FasterWhisperAdapter:
    """Persistent faster-whisper adapter for English/Hindi/Marathi."""

    def __init__(self, model_root: Path, cpu_only: bool = False) -> None:
        self.path = Path(model_root) / 'stt' / 'whisper-small'
        self.cpu_only = cpu_only
        self._model = None
        self._lock = threading.RLock()

    def health(self) -> bool:
        return self.path.is_dir() and (self.path / 'model.bin').is_file()

    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            if not self.path.is_dir():
                raise FileNotFoundError(f'Missing Whisper model folder: {self.path}')
            if not (self.path / 'model.bin').is_file():
                raise RuntimeError('Whisper checkpoint is not a faster-whisper/CTranslate2 model: model.bin is missing.')
            from faster_whisper import WhisperModel
            device = 'cpu' if self.cpu_only or not torch.cuda.is_available() else 'cuda'
            compute = 'int8' if device == 'cpu' else 'float16'
            kwargs = {'device': device, 'compute_type': compute}
            if device == 'cpu':
                kwargs['cpu_threads'] = max(4, (torch.get_num_threads() or 4))
                kwargs['num_workers'] = 1
            self._model = WhisperModel(str(self.path), **kwargs)
            return self._model

    @staticmethod
    def _language(language: str | None) -> str | None:
        if language in {'en', 'hi', 'mr'}:
            return language
        return LANGUAGE_TO_WHISPER.get(language or '')

    def transcribe_audio(self, audio: np.ndarray, language: str | None = None) -> dict:
        model = self._load()
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return {'text': '', 'language': self._language(language) or '', 'probability': 0.0, 'segments': []}
        segments, info = model.transcribe(
            audio,
            language=self._language(language),
            task='transcribe',
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        collected: list[str] = []
        views: list[dict[str, object]] = []
        for segment in segments:
            text = (segment.text or '').strip()
            if text:
                collected.append(text)
            views.append({'start': float(segment.start), 'end': float(segment.end), 'text': text})
        return {
            'text': ' '.join(collected).strip(),
            'language': getattr(info, 'language', '') or '',
            'probability': float(getattr(info, 'language_probability', 0.0) or 0.0),
            'segments': views,
        }

    def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        model = self._load()
        segments, info = model.transcribe(
            str(audio_path),
            language=self._language(language),
            task='transcribe',
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=True,
            word_timestamps=False,
        )
        collected: list[str] = []
        views: list[dict[str, object]] = []
        for segment in segments:
            text = (segment.text or '').strip()
            if text:
                collected.append(text)
            views.append({'start': float(segment.start), 'end': float(segment.end), 'text': text})
        return {
            'text': ' '.join(collected).strip(),
            'language': getattr(info, 'language', '') or '',
            'probability': float(getattr(info, 'language_probability', 0.0) or 0.0),
            'segments': views,
        }

    def warm(self) -> None:
        self._load()

    def unload(self) -> None:
        with self._lock:
            self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


class IndicConformerAdapter:
    """Optional IndicConformer-600M ASR for Santali.

    The model is kept on CPU by default so the RTX 2050 remains available to
    Parler-TTS. AI4Bharat documents the multilingual checkpoint as covering all
    22 scheduled languages, including Santali (`sat`).
    """

    def __init__(self, model_path: Path, cpu_only: bool = True) -> None:
        self.path = Path(model_path)
        self.cpu_only = cpu_only
        self._model = None
        self._lock = threading.RLock()

    def health(self) -> bool:
        return self.path.is_dir() and (self.path / 'config.json').is_file()

    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            if not self.health():
                raise FileNotFoundError(
                    f'Missing Santali IndicConformer model: {self.path}. '
                    'Run scripts\\download_santali_stt.py after accepting the model terms.'
                )
            from transformers import AutoModel
            device = torch.device('cpu' if self.cpu_only or not torch.cuda.is_available() else 'cuda')
            model = AutoModel.from_pretrained(
                str(self.path),
                trust_remote_code=True,
                local_files_only=True,
            )
            model.to(device).eval()
            self._model = (model, device)
            return self._model

    @staticmethod
    def _prepare_audio(audio: np.ndarray, sample_rate: int = 16000) -> torch.Tensor:
        values = np.asarray(audio, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return torch.zeros((1, 0), dtype=torch.float32)
        # Browser live audio is already normalized to 16 kHz before this adapter.
        return torch.from_numpy(values).unsqueeze(0)

    def transcribe_audio(self, audio: np.ndarray) -> dict:
        model, device = self._load()
        wav = self._prepare_audio(audio).to(device)
        if wav.shape[-1] == 0:
            return {'text': '', 'language': 'sat', 'probability': 0.0, 'segments': []}
        with torch.inference_mode():
            text = model(wav, 'sat', 'ctc')
        text = str(text or '').strip()
        return {'text': text, 'language': 'sat', 'probability': 1.0 if text else 0.0, 'segments': []}

    def transcribe(self, audio_path: Path) -> dict:
        wav, sr = sf.read(str(audio_path), dtype='float32', always_2d=False)
        if np.ndim(wav) > 1:
            wav = np.mean(wav, axis=1)
        if int(sr) != 16000:
            try:
                from scipy.signal import resample_poly
                import math
                g = math.gcd(int(sr), 16000)
                wav = resample_poly(np.asarray(wav), 16000 // g, int(sr) // g).astype(np.float32)
            except Exception as exc:
                raise RuntimeError(f'Could not resample Santali audio to 16 kHz: {exc}') from exc
        return self.transcribe_audio(wav)

    def warm(self) -> None:
        self._load()

    def unload(self) -> None:
        with self._lock:
            self._model = None
