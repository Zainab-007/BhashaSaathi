from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

# Keep CPU inference predictable on laptops. Too many OpenMP/MKL threads can
# make concurrent FastAPI requests slower because the translation/TTS models
# fight for the same cores. The user can override this with BHASHA_TORCH_THREADS.
_thread_setting = (os.getenv('TORCH_THREADS') or os.getenv('BHASHA_TORCH_THREADS') or '').strip()
if not _thread_setting:
    cores = os.cpu_count() or 8
    _thread_setting = str(max(2, min(8, cores // 2 if cores > 4 else cores)))
    os.environ.setdefault('OMP_NUM_THREADS', _thread_setting)
    os.environ.setdefault('MKL_NUM_THREADS', _thread_setting)

import torch

from ..adapters.stt import FasterWhisperAdapter, IndicConformerAdapter
from ..adapters.translation import IndicTrans2Adapter
from ..adapters.tts import IndicParlerTTSAdapter
from ..core.config import MODELS_ROOT, settings


class ModelManager:
    """Shared resident model registry.

    Previous builds unloaded the other two heavy models before every request.
    That guaranteed slow classroom actions because translation/TTS repeatedly paid
    model construction time. This version keeps loaded models warm and lets the
    independent pipeline stages use their own adapter locks.
    """

    def __init__(self) -> None:
        thread_setting = (os.getenv('TORCH_THREADS') or os.getenv('BHASHA_TORCH_THREADS') or '').strip() or _thread_setting
        if thread_setting.isdigit():
            try:
                torch.set_num_threads(max(1, int(thread_setting)))
            except Exception:
                pass
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            # PyTorch only allows this before parallel work has started.
            pass
        try:
            torch.set_float32_matmul_precision('high')
        except Exception:
            pass
        self._registry_lock = threading.RLock()
        self.translation_cpu_only = settings.cpu_only or settings.translation_cpu_only
        self.stt_cpu_only = settings.cpu_only or settings.stt_cpu_only
        self.tts_cpu_only = settings.cpu_only or settings.tts_cpu_only
        self.santali_stt_cpu_only = settings.cpu_only or settings.santali_stt_cpu_only
        self.translation = IndicTrans2Adapter(
            MODELS_ROOT,
            self.translation_cpu_only,
            settings.max_translation_chars,
            text_beams=settings.text_translation_beams,
            route_cache_limit=settings.translation_route_cache_limit,
            max_new_tokens=settings.translation_max_new_tokens,
            live_max_new_tokens=settings.live_translation_max_new_tokens,
            input_token_budget=settings.translation_input_token_budget,
            batch_size=settings.translation_batch_size,
            inference_lock_enabled=settings.translation_inference_lock,
        )
        self.stt = FasterWhisperAdapter(MODELS_ROOT, self.stt_cpu_only)
        self.santali_stt = IndicConformerAdapter(
            Path(settings.santali_stt_model_dir) if Path(settings.santali_stt_model_dir).is_absolute() else MODELS_ROOT.parent / settings.santali_stt_model_dir.lstrip('./'),
            self.santali_stt_cpu_only,
        )
        self.tts = IndicParlerTTSAdapter(MODELS_ROOT, self.tts_cpu_only, settings.parler_description_tokenizer)
        self.last_error: dict[str, str] | None = None

    def status(self) -> dict[str, Any]:
        return {
            'demo_mode': settings.demo_mode,
            'enabled': {
                'translation': settings.translation_enabled,
                'stt': settings.stt_enabled,
                'tts': settings.tts_enabled,
                'llm': settings.llm_enabled,
            },
            'device': 'cpu' if settings.cpu_only else ('cuda' if torch.cuda.is_available() else 'cpu'),
            'cuda_available': bool(torch.cuda.is_available()),
            'model_root': str(MODELS_ROOT),
            'translation': self.translation.health(),
            'translation_loaded': self.translation.loaded(),
            'stt': self.stt.health(),
            'santali_stt': self.santali_stt.health() if settings.santali_stt_enabled else False,
            'stt_loaded': self.stt.loaded(),
            'santali_stt_loaded': self.santali_stt.loaded(),
            'tts': self.tts.health(),
            'last_error': self.last_error,
            'performance': {
                'keep_models_loaded': settings.keep_models_loaded,
                'translation_beams': settings.text_translation_beams,
                'live_translation_beams': settings.live_translation_beams,
                'translation_max_new_tokens': settings.translation_max_new_tokens,
                'translation_input_token_budget': settings.translation_input_token_budget,
                'translation_batch_size': settings.translation_batch_size,
                'live_translation_max_new_tokens': settings.live_translation_max_new_tokens,
                'torch_threads': torch.get_num_threads(),
                'translation_device_policy': 'cpu' if self.translation_cpu_only else 'auto',
                'stt_device_policy': 'cpu' if self.stt_cpu_only else 'auto',
                'tts_device_policy': 'cpu' if self.tts_cpu_only else 'auto',
                'santali_stt_device_policy': 'cpu' if self.santali_stt_cpu_only else 'auto',
                'live_segment_ms': settings.max_live_segment_ms,
                'live_queue_size': settings.live_queue_size,
                'live_tts_stream_seconds': settings.live_tts_stream_seconds,
                'live_tts_stream_timeout': settings.live_tts_stream_timeout,
            },
        }

    def _record_error(self, stage: str, exc: Exception) -> None:
        self.last_error = {'stage': stage, 'type': type(exc).__name__, 'message': str(exc)[:1000]}

    def translate(self, text: str, src: str, tgt: str, *, fast: bool = False) -> str:
        try:
            return self.translation.translate(text, src, tgt, beams=settings.live_translation_beams if fast else settings.text_translation_beams, max_new_tokens=settings.live_translation_max_new_tokens if fast else settings.translation_max_new_tokens)
        except Exception as exc:
            self._record_error('translation', exc)
            raise

    def translate_many(self, texts: list[str], src: str, tgt: str, *, fast: bool = False) -> list[str]:
        try:
            return self.translation.translate_many(texts, src, tgt, beams=settings.live_translation_beams if fast else settings.text_translation_beams, max_new_tokens=settings.live_translation_max_new_tokens if fast else settings.translation_max_new_tokens)
        except Exception as exc:
            self._record_error('translation', exc)
            raise

    def transcribe(self, audio_path: Path, language: str | None = 'hi') -> dict:
        try:
            if language == 'sat_Olck' and settings.santali_stt_enabled:
                return self.santali_stt.transcribe(audio_path)
            return self.stt.transcribe(audio_path, language)
        except Exception as exc:
            self._record_error('stt', exc)
            raise

    def transcribe_audio(self, audio, language: str | None = None) -> dict:
        try:
            if language == 'sat_Olck' and settings.santali_stt_enabled:
                return self.santali_stt.transcribe_audio(audio)
            return self.stt.transcribe_audio(audio, language)
        except Exception as exc:
            self._record_error('stt', exc)
            raise

    def synthesize(self, text: str, out_path: Path, language: str, speaker: str) -> dict:
        try:
            return self.tts.synthesize(text, out_path, language, speaker)
        except Exception as exc:
            self._record_error('tts', exc)
            raise

    def synthesize_bytes(self, text: str, language: str, speaker: str) -> tuple[bytes, int, float]:
        try:
            return self.tts.synthesize_bytes(text, language, speaker)
        except Exception as exc:
            self._record_error('tts', exc)
            raise

    def warm_translation(self, src: str, tgt: str, *, fast: bool = False) -> dict[str, Any]:
        return self.translation.warm(src, tgt, beams=settings.live_translation_beams if fast else settings.text_translation_beams, max_new_tokens=settings.live_translation_max_new_tokens if fast else settings.translation_max_new_tokens)

    def warm_translation_routes(self, src: str, targets: list[str]) -> None:
        """Warm the exact classroom routes in the background, one at a time."""
        for tgt in targets:
            if tgt == src:
                continue
            try:
                self.translation.warm(src, tgt, beams=settings.text_translation_beams, max_new_tokens=settings.translation_max_new_tokens)
            except Exception as exc:
                self._record_error('translation_warm', exc)

    def warm_live(self, src: str, tgt: str) -> dict[str, Any]:
        """Load only what the live pipeline needs; loading is idempotent."""
        results: dict[str, Any] = {'source': src, 'target': tgt}
        if src == 'sat_Olck' and settings.santali_stt_enabled:
            self.santali_stt.warm()
        else:
            self.stt.warm()
        results['stt'] = True
        if src != tgt:
            results['translation'] = self.translation.warm(src, tgt, beams=settings.live_translation_beams, max_new_tokens=settings.live_translation_max_new_tokens)
        else:
            results['translation'] = {'route': 'bypass', 'loaded': True}
        self.tts.warm()
        results['tts'] = True
        return results

    def unload_heavy(self) -> None:
        with self._registry_lock:
            self.translation.unload()
            self.stt.unload()
            self.santali_stt.unload()
            self.tts.unload()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


manager = ModelManager()
