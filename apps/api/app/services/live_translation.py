from __future__ import annotations

import asyncio
import io
import json
import math
import queue
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from ..core.config import settings
from .model_manager import manager

LIVE_LANGUAGES = {
    'eng_Latn': {'name': 'English', 'native': 'English', 'whisper': 'en', 'stt_engine': 'whisper'},
    'hin_Deva': {'name': 'Hindi', 'native': 'हिन्दी', 'whisper': 'hi', 'stt_engine': 'whisper'},
    'mar_Deva': {'name': 'Marathi', 'native': 'मराठी', 'whisper': 'mr', 'stt_engine': 'whisper'},
    'sat_Olck': {'name': 'Santali', 'native': 'ᱥᱟᱱᱛᱟᱲᱤ', 'whisper': None, 'stt_engine': 'indic-conformer'},
}

LIVE_RATE = 16_000
BLOCK_MS = 30
BLOCK_SAMPLES = int(LIVE_RATE * BLOCK_MS / 1000)
PRE_ROLL_MS = 180
START_HOLD_MS = 120
MIN_UTTERANCE_MS = 250
START_DB = -39.0
SPEECH_OFFSET_DB = 7.0
SILENCE_OFFSET_DB = 3.0
NOISE_EMA = 0.02


@dataclass(slots=True)
class SpeechChunk:
    sequence: int
    audio: np.ndarray
    created_at: float


class LiveAudioCache:
    """Process-memory cache only. Live microphone audio is never persisted."""

    def __init__(self, limit: int = 64) -> None:
        self.limit = limit
        self._items: OrderedDict[tuple[str, str, str], tuple[bytes, int, float]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: tuple[str, str, str]):
        with self._lock:
            item = self._items.get(key)
            if item:
                self._items.move_to_end(key)
            return item

    def put(self, key: tuple[str, str, str], value: tuple[bytes, int, float]) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)


LIVE_AUDIO_CACHE = LiveAudioCache()


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    values = np.asarray(audio, dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(values)) + 1e-12))
    return 20.0 * math.log10(max(rms, 1e-7))


def resample_to_16k(audio: np.ndarray, source_rate: int) -> np.ndarray:
    if source_rate == LIVE_RATE:
        return np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)
    source_rate = int(source_rate)
    gcd = math.gcd(source_rate, LIVE_RATE)
    return np.asarray(
        resample_poly(np.asarray(audio, dtype=np.float32), LIVE_RATE // gcd, source_rate // gcd),
        dtype=np.float32,
    )


class LiveTranslationSession:
    def __init__(self, source: str, target: str, loop: asyncio.AbstractEventLoop) -> None:
        if source not in LIVE_LANGUAGES or target not in LIVE_LANGUAGES:
            raise ValueError('Choose two supported languages: English, Hindi, Marathi, or Santali.')
        if source == target:
            raise ValueError('Choose two different languages for Live Translate.')
        self.source = source
        self.target = target
        self.loop = loop
        self.stop_event = threading.Event()
        size = max(1, int(settings.live_queue_size))
        self.segment_queue: queue.Queue[SpeechChunk] = queue.Queue(maxsize=size)
        self.translation_queue: queue.Queue[tuple[SpeechChunk, str, float]] = queue.Queue(maxsize=size)
        self.tts_queue: queue.Queue[tuple[SpeechChunk, str, str, float, float]] = queue.Queue(maxsize=size)
        self.outgoing: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=512)
        self.threads: list[threading.Thread] = []
        self.sequence = 0
        self._audio_buffer = np.zeros(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, PRE_ROLL_MS // BLOCK_MS))
        self._speech_chunks: list[np.ndarray] = []
        self._speech_active = False
        self._start_hold_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._speech_start_at = 0.0
        self._noise_floor = START_DB - 10.0

    async def initialize(self) -> None:
        self.emit_json({'type': 'status', 'status': 'warming', 'message': 'Preparing local speech models…'})
        await asyncio.to_thread(manager.warm_live, self.source, self.target)
        self.emit_json({'type': 'status', 'status': 'ready', 'message': 'Microphone ready. Speak naturally.'})
        self.threads = [
            threading.Thread(target=self._stt_worker, name='live-stt', daemon=True),
            threading.Thread(target=self._translation_worker, name='live-translation', daemon=True),
            threading.Thread(target=self._tts_worker, name='live-tts', daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def emit_json(self, payload: dict[str, Any]) -> None:
        try:
            self.loop.call_soon_threadsafe(self._put_outgoing, ('json', payload))
        except RuntimeError:
            pass

    def emit_audio(self, payload: bytes) -> None:
        try:
            self.loop.call_soon_threadsafe(self._put_outgoing, ('bytes', payload))
        except RuntimeError:
            pass

    def emit_pcm(self, audio: np.ndarray) -> None:
        values = np.asarray(audio, dtype='<f4').reshape(-1)
        if values.size:
            self.emit_audio(values.tobytes())

    def _put_outgoing(self, item: tuple[str, Any]) -> None:
        if self.outgoing.full():
            try:
                self.outgoing.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.outgoing.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _enqueue_latest(self, q: queue.Queue, item: Any) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def push_audio(self, raw: bytes, sample_rate: int) -> None:
        if self.stop_event.is_set() or not raw:
            return
        pcm = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
        if pcm.size == 0:
            return
        pcm = resample_to_16k(pcm, int(sample_rate))
        self._audio_buffer = np.concatenate((self._audio_buffer, pcm))
        while self._audio_buffer.size >= BLOCK_SAMPLES:
            block = self._audio_buffer[:BLOCK_SAMPLES]
            self._audio_buffer = self._audio_buffer[BLOCK_SAMPLES:]
            self._feed_block(block)

    def flush(self) -> None:
        if self._audio_buffer.size:
            padded = np.pad(self._audio_buffer, (0, max(0, BLOCK_SAMPLES - self._audio_buffer.size)))
            self._audio_buffer = np.zeros(0, dtype=np.float32)
            self._feed_block(padded[:BLOCK_SAMPLES])
        if self._speech_active and self._speech_chunks:
            self._emit_segment(self._speech_chunks, self._speech_start_at or time.perf_counter())
            self._speech_chunks = []
            self._speech_active = False

    def _feed_block(self, chunk: np.ndarray) -> None:
        self._preroll.append(chunk)
        db = rms_db(chunk)
        threshold_start = max(START_DB, self._noise_floor + SPEECH_OFFSET_DB)
        threshold_continue = max(START_DB - 4.0, self._noise_floor + SILENCE_OFFSET_DB)

        if not self._speech_active:
            self._noise_floor = (1.0 - NOISE_EMA) * self._noise_floor + NOISE_EMA * min(db, START_DB)
            if db >= threshold_start:
                self._start_hold_ms += BLOCK_MS
            else:
                self._start_hold_ms = 0.0
            if self._start_hold_ms >= START_HOLD_MS:
                self._speech_active = True
                self._silence_ms = 0.0
                self._utterance_ms = sum(len(x) for x in self._preroll) / LIVE_RATE * 1000.0
                self._speech_start_at = time.perf_counter() - self._utterance_ms / 1000.0
                self._speech_chunks = list(self._preroll)
                self.emit_json({'type': 'status', 'status': 'listening', 'message': 'Speech detected…'})
            return

        self._speech_chunks.append(chunk)
        self._utterance_ms += BLOCK_MS
        if db >= threshold_continue:
            self._silence_ms = 0.0
        else:
            self._silence_ms += BLOCK_MS

        if self._silence_ms >= settings.live_end_silence_ms:
            self._emit_segment(self._speech_chunks, self._speech_start_at or time.perf_counter())
            self._reset_segmenter()
            return

        max_segment_ms = settings.live_max_segment_ms_santali if self.source == 'sat_Olck' else settings.max_live_segment_ms
        if self._utterance_ms >= max_segment_ms:
            combined = np.concatenate(self._speech_chunks).astype(np.float32)
            cut = self._live_boundary(combined)
            self._emit_audio_segment(combined[:cut], self._speech_start_at or time.perf_counter())
            remainder = combined[cut:]
            self._speech_chunks = [remainder] if remainder.size else []
            self._utterance_ms = len(remainder) / LIVE_RATE * 1000.0
            self._speech_start_at = time.perf_counter() - self._utterance_ms / 1000.0
            self._silence_ms = 0.0
            self.emit_json({'type': 'status', 'status': 'listening', 'message': 'Live chunk sent…'})

    def _live_boundary(self, audio: np.ndarray) -> int:
        min_len = int(LIVE_RATE * 1.2)
        if len(audio) <= min_len:
            return len(audio)
        search_len = min(len(audio), int(LIVE_RATE * 0.55))
        start = len(audio) - search_len
        window = int(LIVE_RATE * 0.06)
        step = max(1, int(LIVE_RATE * 0.03))
        best_idx = len(audio)
        best_db = 1e9
        for index in range(start, len(audio) - window + 1, step):
            db = rms_db(audio[index:index + window])
            if db < best_db:
                best_db = db
                best_idx = index + window
        return best_idx if best_db <= START_DB + 4.0 else len(audio)

    def _emit_segment(self, chunks: list[np.ndarray], created_at: float) -> None:
        if chunks:
            self._emit_audio_segment(np.concatenate(chunks).astype(np.float32), created_at)

    def _emit_audio_segment(self, audio: np.ndarray, created_at: float) -> None:
        if audio.size / LIVE_RATE * 1000.0 < MIN_UTTERANCE_MS:
            return
        self.sequence += 1
        # created_at is the moment the utterance becomes available to inference.
        # This makes end-to-end processing latency measurable without counting the
        # time the user was still speaking.
        chunk = SpeechChunk(self.sequence, audio, time.perf_counter())
        self._enqueue_latest(self.segment_queue, chunk)
        self.emit_json({'type': 'segment', 'sequence': chunk.sequence, 'duration_ms': round(len(audio) / LIVE_RATE * 1000.0)})

    def _reset_segmenter(self) -> None:
        self._speech_chunks = []
        self._speech_active = False
        self._start_hold_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._speech_start_at = 0.0
        self._preroll.clear()
        self.emit_json({'type': 'status', 'status': 'ready', 'message': 'Listening for speech…'})

    def _stt_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                chunk = self.segment_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                started = time.perf_counter()
                queue_ms = (started - chunk.created_at) * 1000.0
                result = manager.transcribe_audio(chunk.audio, self.source)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                text = (result.get('text') or '').strip()
                if not text:
                    continue
                self.emit_json({'type': 'transcript', 'sequence': chunk.sequence, 'text': text, 'stt_ms': round(elapsed_ms), 'queue_ms': round(queue_ms)})
                self._enqueue_latest(self.translation_queue, (chunk, text, elapsed_ms))
            except Exception as exc:
                self.emit_json({'type': 'error', 'sequence': chunk.sequence, 'stage': 'stt', 'message': str(exc)[:500]})

    def _translation_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                chunk, text, stt_ms = self.translation_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                started = time.perf_counter()
                translated = manager.translate(text, self.source, self.target, fast=True).strip()
                translation_ms = (time.perf_counter() - started) * 1000.0
                if not translated:
                    continue
                self.emit_json({
                    'type': 'translation',
                    'sequence': chunk.sequence,
                    'text': translated,
                    'translation_ms': round(translation_ms),
                    'pipeline_ms': round(stt_ms + translation_ms),
                })
                self._enqueue_latest(self.tts_queue, (chunk, text, translated, stt_ms, translation_ms))
            except Exception as exc:
                self.emit_json({'type': 'error', 'sequence': chunk.sequence, 'stage': 'translation', 'message': str(exc)[:500]})

    @staticmethod
    def _wav_bytes(audio_chunks: list[np.ndarray], sample_rate: int) -> tuple[bytes, float]:
        if not audio_chunks:
            raise RuntimeError('Parler-TTS produced no audio chunks.')
        waveform = np.concatenate(audio_chunks).astype(np.float32, copy=False)
        buffer = io.BytesIO()
        sf.write(buffer, waveform, int(sample_rate), format='WAV', subtype='PCM_16')
        return buffer.getvalue(), float(len(waveform) / sample_rate)

    def _tts_worker(self) -> None:
        speaker = manager.tts.speaker_for(self.target)
        while not self.stop_event.is_set():
            try:
                chunk, transcript, translated, stt_ms, translation_ms = self.tts_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                cache_key = (translated, self.target, speaker)
                started = time.perf_counter()
                cached = LIVE_AUDIO_CACHE.get(cache_key)
                was_cached = cached is not None
                if cached is not None:
                    wav_bytes, sample_rate, duration_s = cached
                    self.emit_json({
                        'type': 'audio_start',
                        'sequence': chunk.sequence,
                        'mode': 'wav',
                        'sample_rate': sample_rate,
                        'duration_ms': round(duration_s * 1000.0),
                        'tts_ms': round((time.perf_counter() - started) * 1000.0),
                        'total_ms': round((time.perf_counter() - chunk.created_at) * 1000.0),
                        'cached': True,
                    })
                    self.emit_audio(wav_bytes)
                    tts_ms = (time.perf_counter() - started) * 1000.0
                else:
                    audio_chunks: list[np.ndarray] = []
                    sample_rate = 0
                    first_audio_sent = False
                    first_audio_ms = 0.0
                    stream_error: BaseException | None = None
                    try:
                        pieces = manager.tts.stream_audio(
                            translated,
                            self.target,
                            speaker,
                            stop_event=self.stop_event,
                            stream_seconds=settings.live_tts_stream_seconds,
                            timeout=settings.live_tts_stream_timeout,
                        )
                        for piece, rate in pieces:
                            if self.stop_event.is_set():
                                break
                            array = np.asarray(piece, dtype=np.float32).reshape(-1)
                            if array.size == 0:
                                continue
                            sample_rate = int(rate)
                            audio_chunks.append(array.copy())
                            if not first_audio_sent:
                                first_audio_sent = True
                                first_audio_ms = (time.perf_counter() - started) * 1000.0
                                self.emit_json({
                                    'type': 'audio_start',
                                    'sequence': chunk.sequence,
                                    'mode': 'pcm',
                                    'sample_rate': sample_rate,
                                    'time_to_first_audio_ms': round(first_audio_ms),
                                    'tts_ms': round(first_audio_ms),
                                    'total_ms': round((time.perf_counter() - chunk.created_at) * 1000.0),
                                    'cached': False,
                                })
                            self.emit_pcm(array)
                    except BaseException as exc:
                        stream_error = exc

                    if stream_error is not None:
                        # Do not silently switch streams after audio has started; the
                        # client could receive an overlapping second voice. Surface the
                        # real model failure so the UI can stop/retry cleanly.
                        raise stream_error
                    if not audio_chunks:
                        raise RuntimeError('Parler-TTS produced no audio.')
                    wav_bytes, duration_s = self._wav_bytes(audio_chunks, sample_rate)
                    LIVE_AUDIO_CACHE.put(cache_key, (wav_bytes, sample_rate, duration_s))
                    tts_ms = (time.perf_counter() - started) * 1000.0
                    if not first_audio_sent:
                        # Defensive fallback for a model implementation that yields
                        # only after generation completes.
                        self.emit_json({
                            'type': 'audio_start',
                            'sequence': chunk.sequence,
                            'mode': 'pcm',
                            'sample_rate': sample_rate,
                            'time_to_first_audio_ms': round(tts_ms),
                            'tts_ms': round(tts_ms),
                            'total_ms': round((time.perf_counter() - chunk.created_at) * 1000.0),
                            'cached': False,
                        })
                total_ms = (time.perf_counter() - chunk.created_at) * 1000.0
                self.emit_json({
                    'type': 'audio_ready',
                    'sequence': chunk.sequence,
                    'has_audio': True,
                })
                self.emit_json({
                    'type': 'audio_end',
                    'sequence': chunk.sequence,
                    'transcript': transcript,
                    'translation': translated,
                    'tts_ms': round(tts_ms),
                    'total_ms': round(total_ms),
                    'cached': was_cached,
                })
            except Exception as exc:
                self.emit_json({'type': 'error', 'sequence': chunk.sequence, 'stage': 'tts', 'message': str(exc)[:500]})

    def close(self) -> None:
        if self.stop_event.is_set():
            return
        # Flush the last spoken phrase before stopping workers so a short final
        # sentence is not silently discarded when the browser clicks Stop.
        self.flush()
        deadline = time.perf_counter() + 1.5
        while time.perf_counter() < deadline and any(not q.empty() for q in (self.segment_queue, self.translation_queue, self.tts_queue)):
            time.sleep(0.02)
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=1.0)


async def warm_live_models(source: str, target: str) -> dict[str, Any]:
    if source not in LIVE_LANGUAGES or target not in LIVE_LANGUAGES:
        raise ValueError('Choose two supported languages: English, Hindi, Marathi, or Santali.')
    if source == target:
        raise ValueError('Choose two different languages.')
    started = time.perf_counter()
    result = await asyncio.to_thread(manager.warm_live, source, target)
    result['warm_ms'] = round((time.perf_counter() - started) * 1000.0)
    return result
