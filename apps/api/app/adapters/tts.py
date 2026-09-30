from __future__ import annotations

import io
import logging
import os
import queue
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch

logger = logging.getLogger('bhashasaathi.tts')

LANGUAGE_DESCRIPTIONS = {
    'eng_Latn': 'English',
    'hin_Deva': 'Hindi',
    'mar_Deva': 'Marathi',
    'sat_Olck': 'Santali',
}

LANGUAGE_SPEAKERS = {
    'eng_Latn': 'Thoma',
    'hin_Deva': 'Rani',
    'mar_Deva': 'Sunita',
    'sat_Olck': 'Rani',
}

SPEAKER_DESCRIPTIONS = {
    'Thoma': "Thoma's voice is clear, with a close-sounding recording and excellent quality.",
    'Mary': "Mary speaks clearly at a moderate pace in a high quality recording.",
    'Rani': "Rani speaks with a high-pitched, clear voice in a close recording.",
    'Aman': "Aman speaks in a clear voice with a close recording and high quality.",
    'Sunita': "Sunita speaks clearly in a close-sounding environment with high quality.",
    'Isha': "Isha speaks clearly in a high quality recording.",
    'Arjun': "Arjun speaks clearly with a close recording.",
}



class IndicParlerTTSAdapter:
    """Persistent local Indic Parler-TTS adapter.

    The first synthesis can be expensive. Subsequent identical text is served by
    the caller's disk cache without touching the model. The model itself remains
    resident so every new utterance does not pay model-load time again.
    """

    def __init__(self, model_root: Path, cpu_only: bool = False, description_tokenizer: str = ''):
        self.model_root = Path(model_root)
        self.model_path = self.model_root / 'tts' / 'indic-parler-tts'
        self.cpu_only = cpu_only
        configured = description_tokenizer.strip() or os.getenv('PARLER_DESCRIPTION_TOKENIZER', '').strip()
        self.description_tokenizer_path = self._resolve_description_path(configured)
        self._bundle: tuple[Any, Any, Any, Any, torch.device] | None = None
        self._lock = threading.RLock()
        self._inference_lock = threading.RLock()
        self._audio_cache: OrderedDict[tuple[str, str, str], tuple[bytes, int, float]] = OrderedDict()
        self._audio_cache_limit = 32
        self._description_cache: dict[tuple[str, str], tuple[Any, Any]] = {}
        self._compile_enabled = os.getenv('PARLER_COMPILE', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
        self._max_output_seconds = max(4.0, float(os.getenv('PARLER_MAX_OUTPUT_SECONDS', '30')))
        self._live_max_output_seconds = max(3.0, float(os.getenv('PARLER_LIVE_MAX_OUTPUT_SECONDS', '10')))

    def _resolve_description_path(self, configured: str) -> Path:
        candidates: list[Path] = []
        if configured:
            p = Path(configured)
            candidates.append(p if p.is_absolute() else self.model_root / p)
        candidates.extend([
            self.model_root / 'tts' / 'flan-t5-large',
            self.model_root / 'tts' / 'description-tokenizer',
        ])
        for path in candidates:
            if path.is_dir() and (path / 'tokenizer_config.json').is_file() and (
                (path / 'tokenizer.json').is_file() or (path / 'spiece.model').is_file()
            ):
                return path.resolve()
        return (candidates[0] if candidates else self.model_root / 'tts' / 'flan-t5-large').resolve()

    def health(self) -> dict[str, bool | str]:
        model_ready = self.model_path.is_dir() and (self.model_path / 'config.json').is_file() and (
            any(self.model_path.glob('*.safetensors'))
            or any((self.model_path / name).is_file() for name in ('pytorch_model.bin', 'model.bin'))
        )
        tokenizer_ready = self.description_tokenizer_path.is_dir() and (
            (self.description_tokenizer_path / 'tokenizer.json').is_file()
            or (self.description_tokenizer_path / 'spiece.model').is_file()
        ) and (self.description_tokenizer_path / 'tokenizer_config.json').is_file()
        prompt_tokenizer_ready = (self.model_path / 'tokenizer.json').is_file() or (self.model_path / 'tokenizer.model').is_file()
        feature_ready = (self.model_path / 'preprocessor_config.json').is_file()
        return {
            'model': bool(model_ready),
            'prompt_tokenizer': bool(prompt_tokenizer_ready),
            'description_tokenizer': bool(tokenizer_ready),
            'feature_extractor': bool(feature_ready),
            'runtime_ready': bool(model_ready and tokenizer_ready and prompt_tokenizer_ready and feature_ready),
            'loaded': self._bundle is not None,
            'description_tokenizer_path': str(self.description_tokenizer_path),
        }

    def _load(self):
        with self._lock:
            if self._bundle is not None:
                return self._bundle
            health = self.health()
            if not health['model']:
                raise FileNotFoundError(f'Indic Parler-TTS model is incomplete: {self.model_path}')
            if not health['description_tokenizer']:
                raise RuntimeError(
                    'Indic Parler-TTS needs the local FLAN-T5 description tokenizer. '
                    f'Expected it at: {self.description_tokenizer_path}. '
                    'Run scripts\\download_tts_support.py once.'
                )
            if not health['prompt_tokenizer'] or not health['feature_extractor']:
                raise RuntimeError(f'Indic Parler-TTS model folder is missing tokenizer or preprocessor files: {self.model_path}')
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoFeatureExtractor, AutoTokenizer
            device = torch.device('cpu' if self.cpu_only or not torch.cuda.is_available() else 'cuda')
            dtype = torch.float32 if device.type == 'cpu' else torch.float16
            if device.type == 'cuda':
                # Safe performance flags for NVIDIA laptop GPUs. These do not
                # change model semantics and avoid some allocator fragmentation
                # on 4 GB cards.
                try:
                    torch.backends.cuda.matmul.allow_tf32 = True
                except Exception:
                    pass
                try:
                    torch.backends.cudnn.allow_tf32 = True
                except Exception:
                    pass
            load_kwargs = {
                'local_files_only': True,
                'torch_dtype': dtype,
            }
            # Transformers 4.46.1 + the FLAN-T5 encoder in this local Parler
            # stack does not support SDPA cleanly. Use eager by default to avoid
            # a failed first model construction on every API startup. Advanced
            # users can opt into another implementation with PARLER_ATTENTION.
            attention_impl = os.getenv('PARLER_ATTENTION', 'eager').strip() or 'eager'
            load_kwargs['attn_implementation'] = attention_impl
            try:
                model = ParlerTTSForConditionalGeneration.from_pretrained(
                    str(self.model_path),
                    **load_kwargs,
                )
            except (TypeError, ValueError) as exc:
                if attention_impl == 'eager':
                    raise
                load_kwargs['attn_implementation'] = 'eager'
                model = ParlerTTSForConditionalGeneration.from_pretrained(
                    str(self.model_path),
                    **load_kwargs,
                )
            model = model.to(device)
            model.eval()
            if device.type == 'cuda' and self._compile_enabled:
                model = self._maybe_compile(model)
            tokenizer = AutoTokenizer.from_pretrained(str(self.model_path), local_files_only=True)
            description_tokenizer = AutoTokenizer.from_pretrained(str(self.description_tokenizer_path), local_files_only=True)
            feature = AutoFeatureExtractor.from_pretrained(str(self.model_path), local_files_only=True)
            self._bundle = (model, tokenizer, description_tokenizer, feature, device)
            return self._bundle

    @staticmethod
    def _estimate_max_new_tokens(text: str, frame_rate: int, max_seconds: float) -> int:
        words = max(1, len((text or '').split()))
        # Approximate natural classroom delivery. The explicit cap prevents Parler
        # from running away to long 20–30 s generations for short prompts.
        estimated_seconds = min(max_seconds, max(2.5, words * 0.40 + 1.2))
        return max(256, int(estimated_seconds * max(1, frame_rate)))

    def _maybe_compile(self, model):
        if not self._compile_enabled or not hasattr(torch, 'compile'):
            return model
        try:
            return torch.compile(model, mode='reduce-overhead', dynamic=False)
        except Exception:
            return model

    @classmethod
    def speaker_for(cls, language: str) -> str:
        return LANGUAGE_SPEAKERS.get(language, 'Rani')

    @classmethod
    def _description(cls, language: str, speaker: str) -> str:
        chosen_speaker = speaker if speaker and speaker != 'classroom_teacher' else cls.speaker_for(language)
        if chosen_speaker in SPEAKER_DESCRIPTIONS:
            return SPEAKER_DESCRIPTIONS[chosen_speaker]
        language_name = LANGUAGE_DESCRIPTIONS.get(language, 'English')
        return (
            f'{chosen_speaker} speaks {language_name} clearly at a natural pace. '
            'The recording is very clear, close and without background noise.'
        )

    @staticmethod
    def _split_prompt_text(text: str, max_chars: int = 480) -> list[str]:
        import re
        normalized = re.sub(r'\s+', ' ', (text or '').strip())
        if not normalized:
            return []
        if len(normalized) <= max_chars:
            return [normalized]
        sentences = re.split(r'(?<=[.!?।॥])\s+', normalized)
        chunks: list[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            while len(sentence) > max_chars:
                cut = sentence.rfind(' ', 0, max_chars + 1)
                if cut < max_chars // 2:
                    cut = max_chars
                piece = sentence[:cut].strip()
                if piece:
                    chunks.append(piece)
                sentence = sentence[cut:].strip()
            if sentence:
                chunks.append(sentence)
        return chunks or [normalized]

    @staticmethod
    def _token_count(text: str, tokenizer: Any) -> int:
        encoded = tokenizer(text, padding=False, truncation=False, return_tensors='pt')
        return int(encoded.input_ids.shape[1])

    def _token_safe_prompt_chunks(self, text: str, tokenizer: Any, budget: int = 220) -> list[str]:
        """Split long TTS prompts before tokenizer truncation can occur."""
        import re
        normalized = re.sub(r'\s+', ' ', (text or '').strip())
        if not normalized:
            return []
        try:
            if self._token_count(normalized, tokenizer) <= budget:
                return [normalized]
        except Exception:
            return self._split_prompt_text(normalized, max_chars=480)

        sentences = re.split(r'(?<=[.!?।॥])\s+', normalized)
        chunks: list[str] = []
        current = ''
        for sentence in (part.strip() for part in sentences):
            if not sentence:
                continue
            candidate = sentence if not current else f'{current} {sentence}'
            try:
                count = self._token_count(candidate, tokenizer)
            except Exception:
                count = budget + 1
            if count <= budget:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ''
            words = sentence.split()
            word_chunk: list[str] = []
            for word in words:
                candidate = ' '.join(word_chunk + [word])
                try:
                    count = self._token_count(candidate, tokenizer)
                except Exception:
                    count = budget + 1
                if word_chunk and count > budget:
                    chunks.append(' '.join(word_chunk).strip())
                    word_chunk = [word]
                else:
                    word_chunk.append(word)
            if word_chunk:
                chunks.append(' '.join(word_chunk).strip())
        if current:
            chunks.append(current)
        return [item for item in chunks if item]

    def _description_inputs(self, language: str, speaker: str, device: torch.device, tokenizer: Any):
        cache_key = (language, speaker or 'classroom_teacher')
        with self._lock:
            cached = self._description_cache.get(cache_key)
        if cached is not None:
            ids, mask = cached
            return ids.to(device), mask.to(device)
        encoded = tokenizer(
            self._description(language, speaker),
            return_tensors='pt',
            truncation=True,
            max_length=128,
        )
        with self._lock:
            self._description_cache[cache_key] = (encoded.input_ids.cpu(), encoded.attention_mask.cpu())
        return encoded.input_ids.to(device), encoded.attention_mask.to(device)

    def _generate_waveform(self, text: str, language: str, speaker: str) -> tuple[np.ndarray, int]:
        model, tokenizer, description_tokenizer, feature, device = self._load()
        description_ids, description_mask = self._description_inputs(language, speaker, device, description_tokenizer)
        # Token-safe chunks prevent silent prompt truncation on long classroom
        # lessons while still grouping sentences to avoid excessive Parler calls.
        chunks = self._token_safe_prompt_chunks(text, tokenizer, budget=220)
        audio_chunks: list[np.ndarray] = []
        sampling_rate = int(feature.sampling_rate)
        frame_rate = int(getattr(model.audio_encoder.config, 'frame_rate', 86))
        with torch.inference_mode():
            for chunk in chunks:
                prompt_inputs = tokenizer(
                    chunk,
                    return_tensors='pt',
                    truncation=False,
                ).to(device)
                decoder_attention_mask = torch.ones(
                    (1, 1),
                    device=device,
                    dtype=prompt_inputs.attention_mask.dtype,
                )
                generation = model.generate(
                    input_ids=description_ids,
                    attention_mask=description_mask,
                    prompt_input_ids=prompt_inputs.input_ids,
                    prompt_attention_mask=prompt_inputs.attention_mask,
                    decoder_attention_mask=decoder_attention_mask,
                    do_sample=False,
                    use_cache=True,
                    max_new_tokens=self._estimate_max_new_tokens(chunk, frame_rate, self._max_output_seconds),
                    return_dict_in_generate=True,
                )
                if not hasattr(generation, 'sequences'):
                    raise RuntimeError('Indic Parler-TTS returned no audio sequence.')
                audio = generation.sequences[0]
                if hasattr(generation, 'audios_length'):
                    audio = audio[: int(generation.audios_length[0])]
                array = audio.to(torch.float32).detach().cpu().numpy().reshape(-1)
                if array.size:
                    audio_chunks.append(array)
                del generation, prompt_inputs, decoder_attention_mask
        if not audio_chunks:
            raise RuntimeError('Indic Parler-TTS produced empty audio.')
        full_waveform = np.concatenate(audio_chunks).astype(np.float32)
        # Robust silence and waveform energy validation
        abs_wav = np.abs(full_waveform)
        peak = float(np.max(abs_wav)) if abs_wav.size else 0.0
        rms = float(np.sqrt(np.mean(full_waveform**2))) if abs_wav.size else 0.0
        near_zero_pct = float(np.mean(abs_wav < 1e-4) * 100.0) if abs_wav.size else 100.0
        if peak < 0.03 or rms < 0.002 or near_zero_pct > 95.0:
            logger.error(
                '[TTS_SILENT_AUDIO] lang=%s speaker=%s peak=%.5f rms=%.5f near_zero=%.1f%%',
                language, speaker, peak, rms, near_zero_pct,
            )
            raise RuntimeError(
                f'TTS_SILENT_AUDIO: Generated waveform for {language} contained no usable speech (peak={peak:.4f}, rms={rms:.5f})'
            )
        return full_waveform, sampling_rate

    def synthesize_array(self, text: str, language: str, speaker: str = 'classroom_teacher') -> tuple[np.ndarray, int]:
        text = (text or '').strip()
        if not text:
            raise ValueError('Cannot synthesize empty text.')
        with self._inference_lock:
            return self._generate_waveform(text, language, speaker)

    def streaming_supported(self) -> bool:
        try:
            from parler_tts import ParlerTTSStreamer  # type: ignore
            return ParlerTTSStreamer is not None
        except Exception:
            return False

    def stream_audio(
        self,
        text: str,
        language: str,
        speaker: str = 'classroom_teacher',
        *,
        stop_event: threading.Event | None = None,
        stream_seconds: float = 0.5,
        timeout: float = 15.0,
    ):
        """Yield native Parler audio chunks as soon as the streamer produces them.

        This is the web equivalent of the proven desktop live converter's
        ParlerTTSStreamer path. The caller owns playback; this adapter only yields
        mono float32 PCM chunks and never touches a physical speaker device.
        """
        text = (text or '').strip()
        if not text:
            raise ValueError('Cannot synthesize empty text.')
        stop_event = stop_event or threading.Event()

        try:
            from parler_tts import ParlerTTSStreamer  # type: ignore
        except Exception:
            ParlerTTSStreamer = None

        with self._inference_lock:
            model, tokenizer, description_tokenizer, feature, device = self._load()
            sampling_rate = int(feature.sampling_rate)
            if ParlerTTSStreamer is None:
                waveform, rate = self._generate_waveform(text, language, speaker)
                if waveform.size:
                    yield waveform, rate
                return

            description_ids, description_mask = self._description_inputs(language, speaker, device, description_tokenizer)
            words = text.split()
            prompt_text = ' '.join(words[:256]).strip()
            prompt_inputs = tokenizer(
                prompt_text,
                return_tensors='pt',
                truncation=True,
                max_length=256,
            ).to(device)
            frame_rate = int(getattr(model.audio_encoder.config, 'frame_rate', 86))
            play_steps = max(8, int(round(frame_rate * max(0.25, float(stream_seconds)))))
            streamer = ParlerTTSStreamer(
                model,
                device=device,
                play_steps=play_steps,
                timeout=float(timeout),
            )
            generation_error: list[BaseException] = []
            generation_finished = threading.Event()

            generation_kwargs = {
                'input_ids': description_ids,
                'attention_mask': description_mask,
                'prompt_input_ids': prompt_inputs.input_ids,
                'prompt_attention_mask': prompt_inputs.attention_mask,
                'decoder_attention_mask': torch.ones(
                    (1, 1),
                    device=device,
                    dtype=prompt_inputs.attention_mask.dtype,
                ),
                'streamer': streamer,
                'do_sample': False,
                'use_cache': True,
                'max_new_tokens': self._estimate_max_new_tokens(prompt_text, frame_rate, self._live_max_output_seconds),
                'min_new_tokens': 10,
            }

            def generate_target() -> None:
                try:
                    with torch.inference_mode():
                        model.generate(**generation_kwargs)
                except BaseException as exc:  # pragma: no cover - runtime/model dependent
                    generation_error.append(exc)
                finally:
                    generation_finished.set()

            worker = threading.Thread(target=generate_target, name='parler-stream-generate', daemon=True)
            worker.start()
            try:
                while not stop_event.is_set():
                    try:
                        piece = next(streamer)
                    except queue.Empty:
                        if generation_error:
                            raise generation_error[0]
                        if generation_finished.is_set():
                            break
                        continue
                    except StopIteration:
                        break
                    array = piece.detach().to(torch.float32).cpu().numpy().reshape(-1) if torch.is_tensor(piece) else np.asarray(piece, dtype=np.float32).reshape(-1)
                    if array.size:
                        yield np.asarray(array, dtype=np.float32), sampling_rate
                if generation_error:
                    raise generation_error[0]
            finally:
                worker.join(timeout=5.0)

    def _cache_key(self, text: str, language: str, speaker: str) -> tuple[str, str, str]:
        return (text.strip(), language, speaker or 'classroom_teacher')

    def _cache_get(self, key: tuple[str, str, str]) -> tuple[bytes, int, float] | None:
        with self._lock:
            value = self._audio_cache.get(key)
            if value is not None:
                self._audio_cache.move_to_end(key)
            return value

    def _cache_put(self, key: tuple[str, str, str], value: tuple[bytes, int, float]) -> None:
        with self._lock:
            self._audio_cache[key] = value
            self._audio_cache.move_to_end(key)
            while len(self._audio_cache) > self._audio_cache_limit:
                self._audio_cache.popitem(last=False)

    def synthesize_bytes(self, text: str, language: str, speaker: str = 'classroom_teacher') -> tuple[bytes, int, float]:
        text = (text or '').strip()
        if not text:
            raise ValueError('Cannot synthesize empty text.')
        normalized_speaker = speaker or 'classroom_teacher'
        key = self._cache_key(text, language, normalized_speaker)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        waveform, rate = self.synthesize_array(text, language, normalized_speaker)
        buffer = io.BytesIO()
        sf.write(buffer, waveform, rate, format='WAV', subtype='PCM_16')
        value = (buffer.getvalue(), rate, float(len(waveform) / rate))
        self._cache_put(key, value)
        return value

    def synthesize(self, text: str, out_path: Path, language: str, speaker: str = 'classroom_teacher') -> dict:
        t0 = time.perf_counter()
        data, sampling_rate, duration = self.synthesize_bytes(text, language, speaker)
        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

        # Artifact validation
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise RuntimeError(f'TTS synthesis failed to write audio file: {out_path}')
        with sf.SoundFile(out_path) as f:
            if f.frames == 0 or f.samplerate != sampling_rate or f.channels != 1:
                out_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f'Invalid WAV produced: frames={f.frames}, samplerate={f.samplerate}, channels={f.channels}'
                )

        total_s = time.perf_counter() - t0
        rtf = total_s / duration if duration > 0 else 0
        vram_mb = torch.cuda.memory_allocated() / (1024**2) if torch.cuda.is_available() else 0
        logger.info(
            '[TTS] lang=%s speaker=%s dur=%.2fs total=%.2fs RTF=%.2fx VRAM=%.1fMB path=%s',
            language, speaker, duration, total_s, rtf, vram_mb, out_path.name,
        )
        return {
            'path': str(out_path),
            'sample_rate': sampling_rate,
            'duration_seconds': round(duration, 2),
            'rtf': round(rtf, 2),
            'generation_time': round(total_s, 2),
            'size_bytes': out_path.stat().st_size,
        }

    def warm(self) -> None:
        self._load()

    def unload(self) -> None:
        with self._lock:
            self._bundle = None
            self._audio_cache.clear()
            self._description_cache.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
