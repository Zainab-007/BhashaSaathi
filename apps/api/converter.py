"""
BhashaSaathi - single-file local speech translation + temporary local P2P phone system.

Expected directory layout (relative to this file):

models/
├── translation/
│   ├── en-indic/
│   ├── indic-en/
│   └── indic-indic/
├── stt/
│   └── whisper-small/
└── tts/
    ├── indic-parler-tts/
    └── flan-t5-large/

Environment already used by the diagnostic bench:
    transformers==4.46.1
    huggingface_hub==0.36.0
    parler-tts==0.2.2
    IndicTransToolkit==1.1.1
    faster-whisper (installed and working)

Additional runtime packages used by this single file:
    numpy
    scipy
    sounddevice
    soundfile

Example install command:
    pip install numpy scipy sounddevice soundfile

This program intentionally uses local_files_only=True for the Transformers-based
models. It will not silently download model weights during inference.

IMPORTANT:
- The supplied BhashaSaathi handoff says the current Whisper checkpoint is
  CTranslate2/faster-whisper style. Do NOT rename model.bin.
- The supplied handoff also says Parler-TTS is currently blocked until the
  local FLAN-T5 description tokenizer exists at models/tts/flan-t5-large
  (or PARLER_DESCRIPTION_TOKENIZER points to it).
- Mode 1 keeps the existing local laptop microphone pipeline.
- Mode 2 starts a local FastAPI/WebSocket server for temporary two-phone push-to-talk
  conversations. The laptop runs STT, translation and TTS; phones are browser UI clients.
"""

from __future__ import annotations

import argparse
import base64
import gc
import io
import json
import secrets
import socket
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# -----------------------------
# Optional imports with friendly errors
# -----------------------------

try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing numpy. Install with: pip install numpy") from exc

try:
    import scipy.signal
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing scipy. Install with: pip install scipy") from exc

try:
    import sounddevice as sd
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing sounddevice. Install with: pip install sounddevice") from exc

try:
    import soundfile as sf
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing soundfile. Install with: pip install soundfile") from exc

try:
    import soundcard as sc
except Exception:
    sc = None

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except Exception:
    FastAPI = None
    WebSocket = Any
    WebSocketDisconnect = Exception
    HTMLResponse = Any
    uvicorn = None
    FASTAPI_AVAILABLE = False

try:
    import torch
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing torch. Install the PyTorch build appropriate for your system.") from exc

try:
    from faster_whisper import WhisperModel
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing faster-whisper. Install with: pip install faster-whisper") from exc

try:
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing transformers. Install the pinned version from the BhashaSaathi handoff.") from exc

try:
    from IndicTransToolkit.processor import IndicProcessor
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing IndicTransToolkit. Install IndicTransToolkit==1.1.1") from exc

try:
    from parler_tts import ParlerTTSForConditionalGeneration
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing parler-tts. Install parler-tts==0.2.2") from exc

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except Exception as exc:  # pragma: no cover
    raise SystemExit("Tkinter is required. On Windows it is normally included with Python.") from exc


# -----------------------------
# Configuration
# -----------------------------

APP_NAME = "BhashaSaathi"
ROOT_DIR = Path(__file__).resolve().parent
MODEL_ROOT = ROOT_DIR / "models"

STT_DIR = MODEL_ROOT / "stt" / "whisper-small"
EN_INDIC_DIR = MODEL_ROOT / "translation" / "en-indic"
INDIC_EN_DIR = MODEL_ROOT / "translation" / "indic-en"
INDIC_INDIC_DIR = MODEL_ROOT / "translation" / "indic-indic"
TTS_DIR = MODEL_ROOT / "tts" / "indic-parler-tts"
DEFAULT_FLAN_DIR = MODEL_ROOT / "tts" / "flan-t5-large"

AUDIO_RATE = 16_000
BLOCK_MS = 30
MAX_INPUT_QUEUE = 128
MAX_STAGE_QUEUE = 24

# Speech segmentation / VAD-like energy parameters.
PRE_ROLL_MS = 180
START_HOLD_MS = 120
END_SILENCE_MS = 450
MIN_UTTERANCE_MS = 300
MAX_UTTERANCE_MS = 10_000
START_DB = -39.0
SPEECH_OFFSET_DB = 7.0
SILENCE_OFFSET_DB = 3.0
NOISE_EMA = 0.02

# Translation parameters. The official IndicTrans2 example uses 5 beams;
# 4 is a deliberate latency/quality compromise for an interactive demo.
NUM_BEAMS = 4
MAX_TRANSLATION_LENGTH = 256

# Parler description. The model's prompt text is the translated sentence;
# this description controls the speaking style.
TTS_DESCRIPTION = (
    "A clear natural Indian speaker delivers the sentence at a moderate pace "
    "with clear pronunciation, consistent volume, and very clear audio."
)

TTS_LANGUAGE_DESCRIPTIONS: dict[str, str] = {
    "English": "Thoma speaks clear Indian English at a moderate pace with natural pronunciation and consistent volume.",
    "Hindi": "Rohit speaks clear Hindi at a moderate pace with natural Indian pronunciation and consistent volume.",
    "Marathi": "Sanjay speaks clear Marathi at a moderate pace with natural pronunciation and consistent volume.",
    "Bengali": "Arjun speaks clear Bengali at a moderate pace with natural pronunciation and consistent volume.",
    "Gujarati": "Yash speaks clear Gujarati at a moderate pace with natural pronunciation and consistent volume.",
    "Kannada": "Suresh speaks clear Kannada at a moderate pace with natural pronunciation and consistent volume.",
    "Malayalam": "Anjali speaks clear Malayalam at a moderate pace with natural pronunciation and consistent volume.",
    "Punjabi": "A clear natural Punjabi speaker speaks at a moderate pace with natural pronunciation and consistent volume.",
    "Tamil": "Jaya speaks clear Tamil at a moderate pace with natural pronunciation and consistent volume.",
    "Telugu": "Prakash speaks clear Telugu at a moderate pace with natural pronunciation and consistent volume.",
    "Odia": "Manas speaks clear Odia at a moderate pace with natural pronunciation and consistent volume.",
    "Assamese": "A clear natural Assamese speaker speaks at a moderate pace with natural pronunciation and consistent volume.",
    "Nepali": "Amrita speaks clear Nepali at a moderate pace with natural pronunciation and consistent volume.",
    "Urdu": "A clear natural Urdu speaker speaks at a moderate pace with natural pronunciation and consistent volume.",
    "Santali (experimental)": "A clear natural Santali speaker speaks at a moderate pace with natural pronunciation and consistent volume.",
}

P2P_HOST = os.getenv("BHASHA_P2P_HOST", "0.0.0.0")
P2P_PORT = int(os.getenv("BHASHA_P2P_PORT", "8765"))
P2P_ROOM_TTL_S = int(os.getenv("BHASHA_P2P_ROOM_TTL_S", "1800"))
P2P_MAX_RECORDING_S = int(os.getenv("BHASHA_P2P_MAX_RECORDING_S", "15"))
P2P_HTTPS = os.getenv("BHASHA_P2P_HTTPS", "1").strip().lower() not in {"0", "false", "no", "off"}
P2P_TLS_DIR = ROOT_DIR / ".bhashasaathi_tls"
P2P_CERT_FILE = P2P_TLS_DIR / "bhashasaathi-local.crt"
P2P_KEY_FILE = P2P_TLS_DIR / "bhashasaathi-local.key"

# Optional environment controls.
# BHASHA_MODEL_DEVICE=cpu|cuda|auto
MODEL_DEVICE_OVERRIDE = os.getenv("BHASHA_MODEL_DEVICE", "auto").strip().lower()
PARLER_DESCRIPTION_TOKENIZER = Path(
    os.getenv("PARLER_DESCRIPTION_TOKENIZER", str(DEFAULT_FLAN_DIR))
).expanduser()
TTS_DEVICE_OVERRIDE = os.getenv("BHASHA_TTS_DEVICE", "auto").strip().lower()
LOCAL_CONVERSATION_MODES = ("Turn-by-turn (stable)", "Continuous (low-latency)")


# -----------------------------
# Language registry
# -----------------------------

@dataclass(frozen=True)
class Language:
    name: str
    indic_code: str
    whisper_code: Optional[str]
    tts_supported: bool = True


LANGUAGES: dict[str, Language] = {
    "English": Language("English", "eng_Latn", "en", True),
    "Hindi": Language("Hindi", "hin_Deva", "hi", True),
    "Marathi": Language("Marathi", "mar_Deva", "mr", True),
    "Bengali": Language("Bengali", "ben_Beng", "bn", True),
    "Gujarati": Language("Gujarati", "guj_Gujr", "gu", True),
    "Kannada": Language("Kannada", "kan_Knda", "kn", True),
    "Malayalam": Language("Malayalam", "mal_Mlym", "ml", True),
    "Punjabi": Language("Punjabi", "pan_Guru", "pa", True),
    "Tamil": Language("Tamil", "tam_Taml", "ta", True),
    "Telugu": Language("Telugu", "tel_Telu", "te", True),
    "Odia": Language("Odia", "ory_Orya", "or", True),
    "Assamese": Language("Assamese", "asm_Beng", "as", True),
    "Nepali": Language("Nepali", "npi_Deva", "ne", True),
    "Urdu": Language("Urdu", "urd_Arab", "ur", True),
    # Whisper language handling for Santali is intentionally left as auto until
    # the local checkpoint is tested with real Santali audio, matching the handoff.
    "Santali (experimental)": Language("Santali (experimental)", "sat_Olck", None, True),
}

LANGUAGE_NAMES = list(LANGUAGES.keys())


# -----------------------------
# Data objects
# -----------------------------

@dataclass
class SpeechSegment:
    sequence_id: int
    audio: np.ndarray
    sample_rate: int
    created_at: float
    ended_at: float
    duration_s: float


@dataclass
class TextJob:
    segment: SpeechSegment
    transcript: str
    detected_language: Optional[str] = None
    language_probability: float = 0.0
    stt_time_s: float = 0.0


@dataclass
class TranslationJob:
    segment: SpeechSegment
    transcript: str
    translated_text: str
    stt_time_s: float
    translation_time_s: float


@dataclass
class AudioJob:
    segment: SpeechSegment
    transcript: str
    translated_text: str
    audio: np.ndarray
    sample_rate: int
    stage_time_s: float
    stt_time_s: float
    translation_time_s: float
    tts_time_s: float
    ready_at: float


# -----------------------------
# Helpers
# -----------------------------


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    a = np.asarray(audio, dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(a)) + 1e-12))
    return 20.0 * math.log10(max(rms, 1e-7))


def resample_to_16k(audio: np.ndarray, source_rate: int) -> np.ndarray:
    if source_rate == AUDIO_RATE:
        return np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)
    g = math.gcd(int(source_rate), AUDIO_RATE)
    up = AUDIO_RATE // g
    down = int(source_rate) // g
    out = scipy.signal.resample_poly(np.asarray(audio, dtype=np.float32), up, down)
    return np.asarray(out, dtype=np.float32)


def resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    target_len = max(1, int(round(len(audio) * target_rate / source_rate)))
    return scipy.signal.resample(audio, target_len).astype(np.float32, copy=False)


def cleanup_torch() -> None:
    gc.collect()
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass


def choose_compute_device() -> str:
    if MODEL_DEVICE_OVERRIDE in {"cpu", "cuda"}:
        if MODEL_DEVICE_OVERRIDE == "cuda" and not torch.cuda.is_available():
            return "cpu"
        return MODEL_DEVICE_OVERRIDE

    if not torch.cuda.is_available():
        return "cpu"

    # A 4 GB-class GPU is deliberately not selected for both the 0.9B TTS and
    # translation model automatically. The diagnostic environment in the handoff
    # is CPU-only, and the local TTS checkpoint is large enough that conservative
    # auto-selection is safer.
    try:
        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024 ** 3)
        return "cuda" if vram_gb >= 8.0 else "cpu"
    except Exception:
        return "cpu"


def choose_whisper_device() -> tuple[str, str]:
    if torch.cuda.is_available():
        try:
            props = torch.cuda.get_device_properties(0)
            vram_gb = props.total_memory / (1024 ** 3)
            if vram_gb >= 4.0:
                return "cuda", "float16"
        except Exception:
            pass
    return "cpu", "int8"


def device_dtype(device: str) -> torch.dtype:
    return torch.float16 if device == "cuda" else torch.float32


def tensor_to_numpy_audio(tensor: Any) -> np.ndarray:
    if isinstance(tensor, torch.Tensor):
        arr = tensor.detach().float().cpu().numpy()
    else:
        arr = np.asarray(tensor, dtype=np.float32)
    arr = np.asarray(arr, dtype=np.float32).squeeze()
    if arr.ndim > 1:
        arr = np.mean(arr, axis=0)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1.0:
        arr = arr / peak
    return np.clip(arr, -1.0, 1.0).astype(np.float32)


def pretty_device_name(index: int, device: dict[str, Any]) -> str:
    kind = []
    if int(device.get("max_input_channels", 0)) > 0:
        kind.append("IN")
    if int(device.get("max_output_channels", 0)) > 0:
        kind.append("OUT")
    tag = "/".join(kind)
    return f"{index}: {device['name']} [{tag}]"


def list_audio_devices() -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    inputs: list[tuple[int, str]] = []
    outputs: list[tuple[int, str]] = []
    for idx, d in enumerate(sd.query_devices()):
        label = pretty_device_name(idx, d)
        if int(d.get("max_input_channels", 0)) > 0:
            inputs.append((idx, label))
        if int(d.get("max_output_channels", 0)) > 0:
            outputs.append((idx, label))
    return inputs, outputs


def find_device_index(label: str) -> Optional[int]:
    if not label:
        return None
    m = re.match(r"\s*(\d+)\s*:", label)
    return int(m.group(1)) if m else None


def soundcard_speaker_for_portaudio_index(index: int) -> Any:
    if sc is None:
        return None
    try:
        name = str(sd.query_devices(index).get("name", "")).strip().lower()
        speakers = list(sc.all_speakers())
        for speaker in speakers:
            sp_name = str(getattr(speaker, "name", "")).strip().lower()
            if sp_name == name or name in sp_name or sp_name in name:
                return speaker
        # If the selected endpoint cannot be matched, prefer the Windows default
        # speaker rather than silently opening a random PortAudio endpoint.
        return sc.default_speaker()
    except Exception:
        return None


def split_sentences(text: str, max_chars: int = 320) -> list[str]:
    """Keep TTS prompts reasonably sized without breaking normal punctuation."""
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?।॥])\s+", text)
    chunks: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            chunks.append(part)
            continue
        words = part.split()
        current = []
        current_len = 0
        for word in words:
            add = len(word) + (1 if current else 0)
            if current and current_len + add > max_chars:
                chunks.append(" ".join(current))
                current = [word]
                current_len = len(word)
            else:
                current.append(word)
                current_len += add
        if current:
            chunks.append(" ".join(current))
    return chunks or [text]


# -----------------------------
# Model wrappers
# -----------------------------

class WhisperEngine:
    def __init__(self, model_dir: Path, ui: "UIBridge") -> None:
        self.model_dir = model_dir
        self.ui = ui
        self.model: Optional[WhisperModel] = None
        self.device, self.compute_type = choose_whisper_device()
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self.model is not None:
                return
            if not self.model_dir.exists():
                raise FileNotFoundError(f"Whisper model directory not found: {self.model_dir}")
            self.ui.status(f"Loading Whisper on {self.device} ({self.compute_type})...")
            kwargs: dict[str, Any] = {
                "device": self.device,
                "compute_type": self.compute_type,
            }
            if self.device == "cpu":
                kwargs["cpu_threads"] = max(4, (os.cpu_count() or 8) // 2)
            self.model = WhisperModel(str(self.model_dir), **kwargs)
            self.ui.status("Whisper ready")

    def transcribe(self, audio: np.ndarray, language: Optional[str]) -> tuple[str, str, float, float]:
        self.load()
        assert self.model is not None
        started = time.perf_counter()
        segments, info = self.model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        text_parts: list[str] = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if txt:
                text_parts.append(txt)
        text = " ".join(text_parts).strip()
        elapsed = time.perf_counter() - started
        detected = getattr(info, "language", "") or ""
        probability = safe_float(getattr(info, "language_probability", 0.0))
        return text, detected, probability, max(elapsed, 0.0)


class IndicTranslationEngine:
    def __init__(self, ui: "UIBridge", source: Language, target: Language) -> None:
        self.ui = ui
        self.source = source
        self.target = target
        self.device = choose_compute_device()
        self.dtype = device_dtype(self.device)
        self.processor = IndicProcessor(inference=True)
        self.model: Optional[Any] = None
        self.tokenizer: Optional[Any] = None
        self.model_dir: Optional[Path] = None
        self._lock = threading.Lock()

    def _route(self) -> Optional[Path]:
        src_en = self.source.name == "English"
        tgt_en = self.target.name == "English"
        if src_en and tgt_en:
            return None
        if src_en:
            return EN_INDIC_DIR
        if tgt_en:
            return INDIC_EN_DIR
        return INDIC_INDIC_DIR

    def load(self) -> None:
        route_dir = self._route()
        if route_dir is None:
            return
        with self._lock:
            if self.model is not None:
                return
            if not route_dir.exists():
                raise FileNotFoundError(f"Translation model directory not found: {route_dir}")
            self.ui.status(f"Loading IndicTrans2: {route_dir.name} on {self.device}...")
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(route_dir),
                trust_remote_code=True,
                local_files_only=True,
            )
            kwargs: dict[str, Any] = {
                "trust_remote_code": True,
                "local_files_only": True,
            }
            if self.device == "cuda":
                kwargs["torch_dtype"] = self.dtype
            self.model = AutoModelForSeq2SeqLM.from_pretrained(str(route_dir), **kwargs)
            self.model = self.model.to(self.device)
            self.model.eval()
            self.model_dir = route_dir
            self.ui.status(f"Translation model ready: {route_dir.name}")

    def translate(self, text: str) -> tuple[str, float]:
        if self.source.name == self.target.name:
            return text, 0.0
        self.load()
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Translation model did not initialize")

        started = time.perf_counter()
        processed = self.processor.preprocess_batch(
            [text],
            src_lang=self.source.indic_code,
            tgt_lang=self.target.indic_code,
        )
        inputs = self.tokenizer(
            processed,
            truncation=True,
            padding="longest",
            return_tensors="pt",
            return_attention_mask=True,
        ).to(self.device)

        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                use_cache=True,
                min_length=0,
                max_length=MAX_TRANSLATION_LENGTH,
                num_beams=NUM_BEAMS,
                num_return_sequences=1,
            )

        decoded = self.tokenizer.batch_decode(
            generated,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )
        result = self.processor.postprocess_batch(
            decoded,
            lang=self.target.indic_code,
        )[0]
        elapsed = time.perf_counter() - started
        return result.strip(), max(elapsed, 0.0)


class ParlerTTSEngine:
    def __init__(self, ui: "UIBridge", model_dir: Path, description_tokenizer_dir: Path) -> None:
        self.ui = ui
        self.model_dir = model_dir
        self.description_tokenizer_dir = description_tokenizer_dir
        self.device = self._choose_tts_device()
        self.dtype = device_dtype(self.device)
        self.model: Optional[Any] = None
        self.prompt_tokenizer: Optional[Any] = None
        self.description_tokenizer: Optional[Any] = None
        self._description_cache: dict[str, dict[str, torch.Tensor]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _choose_tts_device() -> str:
        if TTS_DEVICE_OVERRIDE == "cpu":
            return "cpu"
        if TTS_DEVICE_OVERRIDE == "cuda":
            if torch.cuda.is_available():
                return "cuda"
            return "cpu"
        # Auto mode stays conservative for 4 GB-class GPUs; manually set
        # BHASHA_TTS_DEVICE=cuda when the machine has enough VRAM and the user
        # wants to benchmark the GPU path. Translation can remain on CPU.
        if torch.cuda.is_available():
            try:
                props = torch.cuda.get_device_properties(0)
                if props.total_memory / (1024 ** 3) >= 6.0:
                    return "cuda"
            except Exception:
                pass
        return "cpu"

    def load(self) -> None:
        with self._lock:
            if self.model is not None:
                return
            if not self.model_dir.exists():
                raise FileNotFoundError(f"Parler-TTS model directory not found: {self.model_dir}")
            if not self.description_tokenizer_dir.exists():
                raise FileNotFoundError(
                    "Parler-TTS is blocked because the local FLAN-T5 description tokenizer "
                    f"was not found at: {self.description_tokenizer_dir}\n"
                    "Set PARLER_DESCRIPTION_TOKENIZER to the directory containing "
                    "config.json, tokenizer.json/tokenizer_config.json and spiece.model."
                )

            self.ui.status(f"Loading Parler-TTS on {self.device}...")
            self.prompt_tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_dir),
                local_files_only=True,
            )
            self.description_tokenizer = AutoTokenizer.from_pretrained(
                str(self.description_tokenizer_dir),
                local_files_only=True,
            )

            kwargs: dict[str, Any] = {
                "local_files_only": True,
            }
            if self.device == "cuda":
                kwargs["torch_dtype"] = self.dtype

            self.model = ParlerTTSForConditionalGeneration.from_pretrained(
                str(self.model_dir),
                **kwargs,
            )
            self.model = self.model.to(self.device)
            self.model.eval()
            self.ui.status("Parler-TTS ready")


    def synthesize(self, text: str, target_language: Optional[str] = None) -> tuple[np.ndarray, int, float]:
        """Generate the complete translated utterance, in FIFO order.

        This stable path deliberately generates one complete utterance before
        playback.  It is slower to first audio than a streamer, but it is much
        more reliable on Windows laptops and guarantees every queued utterance
        becomes one complete AudioJob.
        """
        self.load()
        if self.model is None or self.prompt_tokenizer is None or self.description_tokenizer is None:
            raise RuntimeError("Parler-TTS did not initialize")

        started = time.perf_counter()
        sentences = split_sentences(text, max_chars=320)
        if not sentences:
            return np.zeros(0, dtype=np.float32), 44100, 0.0

        sampling_rate = int(
            getattr(
                getattr(self.model, "audio_encoder", None),
                "config",
                getattr(self.model, "config", None),
            ).sampling_rate
            if getattr(getattr(self.model, "audio_encoder", None), "config", None) is not None
            else getattr(self.model.config, "sampling_rate", 44100)
        )

        description = TTS_LANGUAGE_DESCRIPTIONS.get(target_language or "", TTS_DESCRIPTION)
        cached = self._description_cache.get(target_language or "")
        if cached is None:
            desc_inputs = self.description_tokenizer(
                description,
                return_tensors="pt",
                truncation=True,
            )
            cached = {k: v.to(self.device) for k, v in desc_inputs.items()}
            self._description_cache[target_language or ""] = cached

        audio_parts: list[np.ndarray] = []
        generation_times: list[float] = []
        for idx, part in enumerate(sentences, start=1):
            prompt_inputs = self.prompt_tokenizer(
                part,
                return_tensors="pt",
                truncation=True,
                max_length=256,
            )
            prompt_inputs = {k: v.to(self.device) for k, v in prompt_inputs.items()}

            generate_kwargs = {
                "input_ids": cached["input_ids"],
                "prompt_input_ids": prompt_inputs["input_ids"],
                "attention_mask": cached.get("attention_mask"),
                "prompt_attention_mask": prompt_inputs.get("attention_mask"),
                "do_sample": False,
                "use_cache": True,
            }
            generate_kwargs = {k: v for k, v in generate_kwargs.items() if v is not None}

            piece_started = time.perf_counter()
            try:
                with torch.inference_mode():
                    generation = self.model.generate(**generate_kwargs)
            except TypeError:
                fallback_kwargs = {
                    "input_ids": cached["input_ids"],
                    "prompt_input_ids": prompt_inputs["input_ids"],
                    "do_sample": False,
                    "use_cache": True,
                }
                with torch.inference_mode():
                    generation = self.model.generate(**fallback_kwargs)

            piece_audio = tensor_to_numpy_audio(generation)
            audio_parts.append(piece_audio)
            piece_elapsed = max(time.perf_counter() - piece_started, 0.0)
            generation_times.append(piece_elapsed)
            piece_seconds = len(piece_audio) / float(sampling_rate) if sampling_rate else 0.0
            self.ui.status(
                f"TTS #{idx}/{len(sentences)} generated • {piece_elapsed:.2f}s compute → {piece_seconds:.2f}s audio"
            )
            del generation, prompt_inputs

        gap = np.zeros(int(sampling_rate * 0.02), dtype=np.float32)
        assembled: list[np.ndarray] = []
        for i, piece in enumerate(audio_parts):
            assembled.append(piece)
            if i != len(audio_parts) - 1:
                assembled.append(gap)
        audio = np.concatenate(assembled).astype(np.float32) if assembled else np.zeros(0, dtype=np.float32)

        elapsed = max(time.perf_counter() - started, 0.0)
        audio_seconds = len(audio) / float(sampling_rate) if sampling_rate else 0.0
        rtf = elapsed / audio_seconds if audio_seconds > 0 else 0.0
        if rtf > 1.0:
            self.ui.warning(
                f"TTS generation is slower than playback: {elapsed:.2f}s compute for {audio_seconds:.2f}s audio (RTF {rtf:.2f}×, device={self.device})"
            )
        else:
            self.ui.status(
                f"TTS ready • {audio_seconds:.2f}s audio generated in {elapsed:.2f}s (RTF {rtf:.2f}×, device={self.device})"
            )
        return audio, sampling_rate, elapsed


# -----------------------------
# Audio capture / segmentation
# -----------------------------

class MicrophoneSegmenter:
    def __init__(
        self,
        input_device: int,
        callback_queue: queue.Queue,
        segment_queue: queue.Queue,
        ui: "UIBridge",
        stop_event: threading.Event,
        pause_capture: threading.Event,
        turn_by_turn: bool,
    ) -> None:
        self.input_device = input_device
        self.callback_queue = callback_queue
        self.segment_queue = segment_queue
        self.ui = ui
        self.stop_event = stop_event
        self.pause_capture = pause_capture
        self.turn_by_turn = turn_by_turn
        self.manual_finalize = threading.Event()
        self.thread = threading.Thread(target=self._run, name="segmenter", daemon=True)
        self.stream: Optional[sd.InputStream] = None
        self.sequence = 0
        self.input_rate = self._query_input_rate()
        self.block_frames = max(1, int(round(self.input_rate * BLOCK_MS / 1000.0)))

    def _query_input_rate(self) -> int:
        info = sd.query_devices(self.input_device, "input")
        rate = int(round(float(info["default_samplerate"])))
        return rate if rate > 0 else AUDIO_RATE

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            self.ui.warning(f"Audio input status: {status}")
        try:
            self.callback_queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.ui.warning("Microphone input queue is full; dropping an audio block")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stop_event.set()
        self.thread.join(timeout=1.5)

    def request_finalize(self) -> None:
        """Immediately finish the current utterance in turn-by-turn mode.

        The microphone stream itself stays alive, but capture is paused as soon as
        the current sentence is emitted. This gives the user an explicit "Done"
        button while retaining automatic silence detection as a fallback.
        """
        if self.turn_by_turn:
            self.manual_finalize.set()

    def _emit_segment(self, chunks: list[np.ndarray], start_at: float) -> None:
        if not chunks:
            return
        audio = np.concatenate(chunks).astype(np.float32)
        duration_s = len(audio) / AUDIO_RATE
        if duration_s * 1000.0 < MIN_UTTERANCE_MS:
            return
        self.sequence += 1
        ended = time.perf_counter()
        seg = SpeechSegment(
            sequence_id=self.sequence,
            audio=audio,
            sample_rate=AUDIO_RATE,
            created_at=start_at,
            ended_at=ended,
            duration_s=duration_s,
        )
        try:
            if self.turn_by_turn:
                # Lock the microphone immediately after the utterance is finalized.
                # The rest of the pipeline can now STT → translate → TTS → playback
                # without another utterance entering the queue.
                self.pause_capture.set()
            self.segment_queue.put(seg, timeout=0.5)
            self.ui.segment_created(seg)
        except queue.Full:
            self.ui.warning("Speech queue is full. The system is behind real-time processing.")
            if self.turn_by_turn:
                self.pause_capture.clear()

    def _run(self) -> None:
        preroll_blocks = max(1, int(round(PRE_ROLL_MS / BLOCK_MS)))
        preroll: deque[np.ndarray] = deque(maxlen=preroll_blocks)
        speech_chunks: list[np.ndarray] = []
        speech_active = False
        start_hold = 0.0
        silence_ms = 0.0
        utterance_ms = 0.0
        speech_start_at = 0.0
        noise_floor = START_DB - 10.0

        self.ui.status(f"Starting microphone stream at {self.input_rate} Hz...")
        try:
            self.stream = sd.InputStream(
                samplerate=self.input_rate,
                blocksize=self.block_frames,
                device=self.input_device,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            with self.stream:
                self.ui.status("Listening... speak naturally")
                while not self.stop_event.is_set():
                    try:
                        raw = self.callback_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    chunk = resample_to_16k(raw, self.input_rate)
                    if chunk.size == 0:
                        continue
                    # In stable turn-by-turn mode we keep draining the audio callback
                    # queue but do not create new utterances while translated TTS is
                    # being generated/played. This prevents feedback and TTS backlog.
                    if self.pause_capture.is_set():
                        speech_active = False
                        start_hold = 0.0
                        silence_ms = 0.0
                        utterance_ms = 0.0
                        speech_chunks = []
                        preroll.clear()
                        continue
                    preroll.append(chunk)

                    db = rms_db(chunk)
                    threshold_start = max(START_DB, noise_floor + SPEECH_OFFSET_DB)
                    threshold_continue = max(START_DB - 4.0, noise_floor + SILENCE_OFFSET_DB)

                    if not speech_active:
                        noise_floor = (1.0 - NOISE_EMA) * noise_floor + NOISE_EMA * min(db, START_DB)
                        if db >= threshold_start:
                            start_hold += BLOCK_MS
                        else:
                            start_hold = 0.0

                        if start_hold >= START_HOLD_MS:
                            speech_active = True
                            silence_ms = 0.0
                            utterance_ms = 0.0
                            speech_start_at = time.perf_counter() - max(0.0, start_hold / 1000.0)
                            speech_chunks = list(preroll)
                            utterance_ms = sum(len(x) for x in speech_chunks) / AUDIO_RATE * 1000.0
                            self.ui.status("Speaking detected...")
                    else:
                        speech_chunks.append(chunk)
                        utterance_ms += BLOCK_MS
                        if db >= threshold_continue:
                            silence_ms = 0.0
                        else:
                            silence_ms += BLOCK_MS

                        if silence_ms >= END_SILENCE_MS or utterance_ms >= MAX_UTTERANCE_MS or self.manual_finalize.is_set():
                            forced = self.manual_finalize.is_set()
                            self.manual_finalize.clear()
                            self._emit_segment(speech_chunks, speech_start_at or time.perf_counter())
                            speech_chunks = []
                            speech_active = False
                            start_hold = 0.0
                            silence_ms = 0.0
                            utterance_ms = 0.0
                            speech_start_at = 0.0
                            preroll.clear()
                            self.ui.status("Turn sent — processing..." if forced else "Listening...")

                    # If the user pressed the explicit Finish Turn button before VAD
                    # had actually entered speech, just consume the request. Do not
                    # pause the microphone forever with an empty segment.
                    if self.manual_finalize.is_set() and not speech_active:
                        self.manual_finalize.clear()

                # Flush a final active utterance on shutdown.
                if speech_active and speech_chunks:
                    self._emit_segment(speech_chunks, speech_start_at or time.perf_counter())
        except Exception as exc:
            self.ui.error(f"Microphone error: {exc}")
        finally:
            self.stream = None


# -----------------------------
# Pipeline controller
# -----------------------------

class Pipeline:
    def __init__(
        self,
        ui: "UIBridge",
        source: Language,
        target: Language,
        input_device: int,
        output_device: int,
        turn_by_turn: bool = True,
    ) -> None:
        self.ui = ui
        self.source = source
        self.target = target
        self.input_device = input_device
        self.output_device = output_device
        self.turn_by_turn = bool(turn_by_turn)

        self.stop_event = threading.Event()
        self.pause_capture = threading.Event()
        self.input_blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=MAX_INPUT_QUEUE)
        self.segment_queue: queue.Queue[SpeechSegment] = queue.Queue(maxsize=MAX_STAGE_QUEUE)
        self.stt_queue: queue.Queue[TextJob] = queue.Queue(maxsize=MAX_STAGE_QUEUE)
        self.translation_queue: queue.Queue[TranslationJob] = queue.Queue(maxsize=MAX_STAGE_QUEUE)
        self.playback_queue: queue.Queue[AudioJob] = queue.Queue(maxsize=MAX_STAGE_QUEUE)

        self.ui_thread = threading.Thread(target=self._ui_stats_loop, name="stats", daemon=True)
        self.threads: list[threading.Thread] = []

        self.whisper = WhisperEngine(STT_DIR, ui)
        self.translation = IndicTranslationEngine(ui, source, target)
        self.tts = ParlerTTSEngine(ui, TTS_DIR, PARLER_DESCRIPTION_TOKENIZER)
        self.segmenter: Optional[MicrophoneSegmenter] = None

        self.ready = False
        self.starting = False
        self.started_at: Optional[float] = None

        self.metrics_lock = threading.Lock()
        self.last_latency: Optional[float] = None
        self.last_stt: Optional[float] = None
        self.last_translation: Optional[float] = None
        self.last_tts: Optional[float] = None

    def validate(self) -> None:
        missing = [
            p for p in [STT_DIR, EN_INDIC_DIR, INDIC_EN_DIR, INDIC_INDIC_DIR, TTS_DIR]
            if not p.exists()
        ]
        if missing:
            joined = "\n".join(str(p) for p in missing)
            raise FileNotFoundError(f"Required model directories are missing:\n{joined}")
        if not PARLER_DESCRIPTION_TOKENIZER.exists():
            raise FileNotFoundError(
                "Missing Parler FLAN-T5 description tokenizer: "
                f"{PARLER_DESCRIPTION_TOKENIZER}\n"
                "Provision it locally as described in the BhashaSaathi handoff, or "
                "set PARLER_DESCRIPTION_TOKENIZER."
            )

        if self.source.name == "Santali (experimental)" and self.source.whisper_code is None:
            self.ui.warning(
                "Santali STT is marked experimental: Whisper language is left on auto-detect. "
                "This matches the current local-model handoff and should be validated with real audio."
            )
        if self.target.name == self.source.name:
            self.ui.warning("Source and target are the same language: translation will be bypassed.")

    def prepare_models(self) -> None:
        self.starting = True
        self.ui.status("Preparing local models. First startup can take time...")
        try:
            self.validate()
            # Sequential loading is deliberate: it reduces peak RAM/VRAM spikes on a
            # laptop and is safer than loading all 3 large models concurrently.
            self.whisper.load()
            self.translation.load()
            self.tts.load()
            self.ready = True
            self.ui.status("Models ready. Starting microphone...")
        except Exception as exc:
            self.ready = False
            self.stop_event.set()
            self.ui.error(self.format_exception(exc))
            self.ui.stopped()
        finally:
            self.starting = False

    def _release_turn_capture(self) -> None:
        if self.turn_by_turn:
            self.pause_capture.clear()

    def finish_turn(self) -> None:
        """Request immediate sentence finalization in turn-by-turn mode."""
        if not self.turn_by_turn:
            return
        if self.segmenter is None:
            self.ui.warning("Microphone is not ready yet.")
            return
        self.ui.status("Finishing your turn...")
        self.segmenter.request_finalize()

    def start(self) -> None:
        if self.starting or self.ready:
            return
        thread = threading.Thread(target=self._start_async, name="bootstrap", daemon=True)
        thread.start()

    def _start_async(self) -> None:
        self.prepare_models()
        if not self.ready or self.stop_event.is_set():
            return

        self.started_at = time.perf_counter()
        self.threads = [
            threading.Thread(target=self._stt_worker, name="stt-worker", daemon=True),
            threading.Thread(target=self._translation_worker, name="translation-worker", daemon=True),
            threading.Thread(target=self._tts_worker, name="tts-worker", daemon=True),
            threading.Thread(target=self._playback_worker, name="playback-worker", daemon=True),
        ]
        for t in self.threads:
            t.start()

        self.segmenter = MicrophoneSegmenter(
            input_device=self.input_device,
            callback_queue=self.input_blocks,
            segment_queue=self.segment_queue,
            ui=self.ui,
            stop_event=self.stop_event,
            pause_capture=self.pause_capture,
            turn_by_turn=self.turn_by_turn,
        )
        self.segmenter.start()
        self.ui.started()
        self.ui_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_capture.clear()
        if self.segmenter is not None:
            self.segmenter.stop()
        try:
            sd.stop()
        except Exception:
            pass
        self._drain(self.input_blocks)
        self._drain(self.segment_queue)
        self._drain(self.stt_queue)
        self._drain(self.translation_queue)
        self._drain(self.playback_queue)
        for t in self.threads:
            t.join(timeout=1.0)
        if self.ui_thread.is_alive():
            self.ui_thread.join(timeout=0.5)
        self.ui.stopped()

    @staticmethod
    def _drain(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _stt_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                segment = self.segment_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                lang = self.source.whisper_code
                transcript, detected, probability, elapsed = self.whisper.transcribe(segment.audio, lang)
                if not transcript.strip():
                    self.ui.warning(f"Segment {segment.sequence_id}: speech was detected but STT returned empty text")
                    self._release_turn_capture()
                    continue
                self.ui.transcript(segment.sequence_id, transcript, detected, probability)
                self.stt_queue.put(
                    TextJob(
                        segment=segment,
                        transcript=transcript,
                        detected_language=detected,
                        language_probability=probability,
                        stt_time_s=elapsed,
                    ),
                    timeout=1.0,
                )
            except Exception as exc:
                self.ui.error(f"STT failed for segment {getattr(segment, 'sequence_id', '?')}: {exc}")
                self._release_turn_capture()

    def _translation_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.stt_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                translated, elapsed = self.translation.translate(job.transcript)
                self.ui.translation(job.segment.sequence_id, translated)
                self.translation_queue.put(
                    TranslationJob(
                        segment=job.segment,
                        transcript=job.transcript,
                        translated_text=translated,
                        stt_time_s=job.stt_time_s,
                        translation_time_s=elapsed,
                    ),
                    timeout=1.0,
                )
            except Exception as exc:
                self.ui.error(
                    f"Translation failed for segment {getattr(job.segment, 'sequence_id', '?')}: {exc}"
                )
                self._release_turn_capture()

    def _tts_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.translation_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                audio, sample_rate, elapsed = self.tts.synthesize(job.translated_text, self.target.name)
                if audio.size == 0:
                    self.ui.warning(f"TTS returned empty audio for segment {job.segment.sequence_id}")
                    self._release_turn_capture()
                    continue
                now = time.perf_counter()
                ready_latency = now - job.segment.ended_at
                with self.metrics_lock:
                    self.last_latency = ready_latency
                    self.last_stt = job.stt_time_s
                    self.last_translation = job.translation_time_s
                    self.last_tts = elapsed
                self.playback_queue.put(
                    AudioJob(
                        segment=job.segment,
                        transcript=job.transcript,
                        translated_text=job.translated_text,
                        audio=audio,
                        sample_rate=sample_rate,
                        stage_time_s=ready_latency,
                        stt_time_s=job.stt_time_s,
                        translation_time_s=job.translation_time_s,
                        tts_time_s=elapsed,
                        ready_at=now,
                    ),
                    timeout=2.0,
                )
                self.ui.ready_for_playback(job.segment.sequence_id, ready_latency)
            except Exception as exc:
                self.ui.error(f"TTS failed for segment {getattr(job.segment, 'sequence_id', '?')}: {exc}")
                self._release_turn_capture()

    def _playback_worker(self) -> None:
        # Single FIFO playback worker intentionally preserves utterance order.
        while not self.stop_event.is_set():
            try:
                job = self.playback_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.ui.status(f"Playing translated segment {job.segment.sequence_id}...")
                if sc is not None:
                    speaker = soundcard_speaker_for_portaudio_index(self.output_device)
                    if speaker is None:
                        raise RuntimeError("Selected SoundCard speaker could not be resolved")
                    played = False
                    errors = []
                    for rate in (48000, 44100):
                        try:
                            arr = resample_audio(job.audio, job.sample_rate, rate)
                            channels = getattr(speaker, "channels", 2)
                            channels = channels if isinstance(channels, int) else 2
                            arr = np.repeat(arr[:, None], 2, axis=1) if channels >= 2 else arr[:, None]
                            with speaker.player(samplerate=rate, channels=min(channels, 2), blocksize=512, exclusive_mode=False) as player:
                                player.play(arr.astype(np.float32, copy=False))
                                deadline = time.perf_counter() + len(arr) / rate + 0.25
                                while time.perf_counter() < deadline and not self.stop_event.is_set():
                                    time.sleep(0.02)
                            played = True
                            break
                        except Exception as exc:
                            errors.append(str(exc))
                    if not played:
                        raise RuntimeError("SoundCard playback failed: " + " | ".join(errors))
                else:
                    sd.play(job.audio, samplerate=job.sample_rate, device=self.output_device, blocking=True)
                self.ui.played(job.segment.sequence_id)
            except Exception as exc:
                self.ui.error(f"Audio playback failed: {exc}")
            finally:
                self._release_turn_capture()
                if self.turn_by_turn:
                    self.ui.status("Listening... your turn")
                else:
                    self.ui.status("Listening... speak naturally")

    def _ui_stats_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.metrics_lock:
                latency = self.last_latency
            self.ui.metrics(
                segment_q=self.segment_queue.qsize(),
                stt_q=self.stt_queue.qsize(),
                translation_q=self.translation_queue.qsize(),
                tts_q=self.playback_queue.qsize(),
                latency=latency,
            )
            time.sleep(0.2)

    @staticmethod
    def format_exception(exc: BaseException) -> str:
        return "\n".join(
            [
                f"{type(exc).__name__}: {exc}",
                "",
                "Traceback (most recent call last):",
                traceback.format_exc().strip(),
            ]
        )



# -----------------------------
# P2P local phone server
# -----------------------------

P2P_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#063f36">
<title>BhashaSaathi • Conversation</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#153a35;background:#f5f3eb;--green:#073f36;--green2:#0b5a4d;--mint:#e6f1eb;--line:#d9dfd7;--cream:#f5f3eb;--ink:#153a35;--muted:#71817b;--gold:#b98b32;--danger:#a33d45}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#f7f5ee 0%,#f2f1e9 100%);min-height:100vh;color:var(--ink)}
.app{max-width:720px;margin:auto;min-height:100vh;padding-bottom:28px}.top{background:var(--green);color:#fff;padding:18px 18px 20px;border-radius:0 0 28px 28px;box-shadow:0 10px 28px #073f3622}.brand{display:flex;align-items:center;gap:12px}.logo{width:44px;height:44px;border-radius:13px;background:#f4ead6;color:var(--green);display:grid;place-items:center;font-weight:900;font-size:20px}.brand h1{margin:0;font-size:21px;letter-spacing:-.4px}.brand p{margin:2px 0 0;color:#c8ddd7;font-size:12px}.pill{margin-left:auto;border:1px solid #ffffff2c;background:#ffffff12;padding:7px 10px;border-radius:999px;font-size:11px;color:#dbe9e5}.content{padding:16px}.card{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px;margin:12px 0;box-shadow:0 7px 22px #163d3510}.eyebrow{font-size:11px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#73837d}.title{font-size:24px;font-weight:850;margin:5px 0 4px;letter-spacing:-.7px}.muted{color:var(--muted)}.small{font-size:12px;line-height:1.45}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field label{display:block;font-size:12px;font-weight:750;margin:0 0 7px}.field select,.field input{width:100%;border:1px solid var(--line);background:#fbfbf8;color:var(--ink);border-radius:13px;padding:13px;font-size:15px;outline:none}.field input:focus,.field select:focus{border-color:#6da99b;box-shadow:0 0 0 3px #b8ddd533}button{border:0;border-radius:13px;padding:13px 15px;font-size:15px;font-weight:800;cursor:pointer;touch-action:manipulation}button.primary{background:var(--green);color:#fff}button.secondary{background:var(--mint);color:var(--green)}button.ghost{background:transparent;border:1px solid var(--line);color:var(--green)}button.danger{background:#f7e7e7;color:var(--danger)}button:disabled{opacity:.45;cursor:not-allowed}.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.codeInput{margin-top:10px}.codeInput input{text-align:center;font-size:22px;font-weight:850;letter-spacing:5px}.hint{display:flex;gap:8px;align-items:flex-start;margin-top:12px;padding:11px 12px;background:#f7f8f4;border-radius:12px;color:#687872}.hint b{color:var(--ink)}.hidden{display:none!important}.roomHead{display:flex;justify-content:space-between;gap:12px;align-items:center}.online{display:inline-flex;align-items:center;gap:7px;background:#e7f4ed;color:#28654f;border-radius:999px;padding:7px 10px;font-size:11px;font-weight:800}.dot{width:7px;height:7px;border-radius:50%;background:#45a86f}.roomCode{font-size:31px;font-weight:900;letter-spacing:6px;margin-top:3px}.peer{font-size:13px;color:var(--muted);margin-top:5px}.langbar{display:grid;grid-template-columns:1fr auto 1fr;gap:9px;align-items:center}.langbox{padding:12px;border:1px solid var(--line);border-radius:14px;background:#fbfbf8}.langbox span{display:block;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:800}.langbox b{display:block;margin-top:3px}.arrow{color:#6c817a;font-weight:900}.bubble{min-height:64px;border-radius:15px;background:#f7f8f4;border:1px solid #e5e9e2;padding:13px;font-size:18px;line-height:1.45;margin-top:7px}.bubble+ .label{margin-top:13px}.label{font-size:10px;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted);font-weight:850}.status{background:var(--mint);color:var(--green);border-radius:13px;padding:12px;text-align:center;font-size:13px;font-weight:750}.ptt{display:block;width:176px;height:176px;margin:17px auto 13px;border-radius:50%;background:var(--green);color:#fff;border:9px solid #d5e8e1;box-shadow:0 12px 28px #073f3630;font-size:19px;line-height:1.2}.ptt:active,.ptt.recording{background:#a6404a;border-color:#f1d6d8;transform:scale(.985)}.footerNote{text-align:center;color:#89958f;font-size:11px;margin-top:14px}.spinner{display:inline-block;width:14px;height:14px;border:2px solid #ffffff66;border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:480px){.content{padding:12px}.row{grid-template-columns:1fr}.actions{grid-template-columns:1fr}.title{font-size:22px}.ptt{width:162px;height:162px}.top{border-radius:0 0 22px 22px}}
</style>
</head>
<body>
<div class="app">
<header class="top"><div class="brand"><div class="logo">भा</div><div><h1>BhashaSaathi</h1><p>Translate the classroom, not just the words.</p></div><div class="pill">LOCAL • P2P</div></div></header>
<main class="content">
<section id="setup" class="card">
  <div class="eyebrow">Conversation</div><div class="title">Connect two devices</div>
  <div class="muted small">Your laptop runs the AI. Phones only handle the microphone, conversation controls and audio.</div>
  <div class="row" style="margin-top:15px"><div class="field"><label>Your language</label><select id="source"></select></div><div class="field"><label>I want to hear</label><select id="target"></select></div></div>
  <div class="actions"><button id="create" class="primary">Create conversation</button><button id="join" class="secondary">Join conversation</button></div>
  <div class="field codeInput"><label>6-digit conversation code</label><input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000 000"></div>
  <div class="hint"><span>●</span><span><b>How it works:</b> Device 1 creates a temporary room. Device 2 enters the code. Then only the device holding the mic button can speak.</span></div>
  <div id="setupStatus" class="footerNote">Same Wi-Fi or laptop hotspot required.</div>
</section>
<section id="room" class="hidden">
  <div class="card"><div class="roomHead"><div><div class="eyebrow">Conversation room</div><div id="roomCode" class="roomCode">000000</div><div id="peer" class="peer">Waiting for the other device…</div></div><div id="online" class="online"><span class="dot"></span><span id="onlineText">Connecting</span></div></div></div>
  <div class="card"><div class="langbar"><div class="langbox"><span>You speak</span><b id="sourceBadge">English</b></div><div class="arrow">→</div><div class="langbox"><span>Other person hears</span><b id="targetBadge">Hindi</b></div></div></div>
  <div class="card"><div class="label">You said</div><div id="heard" class="bubble">—</div><div class="label">Other person hears</div><div id="translated" class="bubble">—</div></div>
  <div class="card center"><div id="state" class="status">Connecting…</div><button id="ptt" class="ptt">🎤<br>HOLD TO SPEAK</button><div class="muted small">Release to send. Your microphone is completely off when you are not holding the button.</div></div>
  <div class="card"><button id="leave" class="danger" style="width:100%">Leave conversation</button></div>
</section>
<div class="footerNote">BhashaSaathi • temporary local session • no cloud required</div>
</main></div>
<script>
const langs=%LANGS%;
const $=id=>document.getElementById(id); const source=$("source"),target=$("target");
langs.forEach(x=>{source.add(new Option(x,x));target.add(new Option(x,x));}); source.value="English";target.value="Hindi";
let ws=null,roomCode=null,clientId=null,recording=false,turnRequested=false,processor=null,audioCtx=null,playCtx=null,sourceNode=null,muteNode=null,pcm=[],peerCount=0,phase="waiting",connecting=false,connectTimer=null;
function setState(s){$("state").textContent=s}
function setOnline(text,ok=true){$("onlineText").textContent=text;$("online").style.background=ok?"#e7f4ed":"#f7e7e7";$("online").style.color=ok?"#28654f":"#8f3940";$("online").querySelector(".dot").style.background=ok?"#45a86f":"#b85a60"}
function setButtons(on){$("ptt").disabled=!on}
function makeClientId(){try{if(window.crypto&&typeof window.crypto.randomUUID==="function")return window.crypto.randomUUID()}catch(e){} return "c_"+Date.now().toString(36)+"_"+Math.random().toString(36).slice(2,10)}
function showSetupStatus(text){$("setupStatus").textContent=text}
function stopConnectTimer(){if(connectTimer){clearTimeout(connectTimer);connectTimer=null}}
function setPairButtons(busy){$("create").disabled=busy;$("join").disabled=busy}
if(!window.isSecureContext){showSetupStatus("⚠ Open the HTTPS address shown by the laptop. Phone microphone access requires a secure context.")}
async function connect(code){
 if(connecting)return; connecting=true; setPairButtons(true); showSetupStatus("Connecting to laptop…");
 clientId=makeClientId(); roomCode=code; setOnline("Connecting",true);
 try{
  const info=await fetch(`/api/rooms/${encodeURIComponent(code)}`,{cache:"no-store",headers:{"Cache-Control":"no-cache"}});
  if(!info.ok)throw new Error("Laptop server did not answer the room check. Check the Wi-Fi address.");
  const room=await info.json();
  if(!room.exists)throw new Error("Room not found or expired. Create a new room on Device 1.");
  if(room.clients>=2)throw new Error("This room already has two devices.");
 }catch(err){connecting=false;setPairButtons(false);setOnline("Error",false);showSetupStatus(err.message);alert(err.message);return}
 try{
  const proto=location.protocol==="https:"?"wss":"ws";
  ws=new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(clientId)}`); ws.binaryType="arraybuffer";
  connectTimer=setTimeout(()=>{if(connecting){try{ws&&ws.close()}catch(e){} connecting=false;setPairButtons(false);setOnline("Timeout",false);showSetupStatus("WebSocket join timed out. Check Windows Firewall and confirm both devices use the same Wi-Fi/hotspot.")}},8000);
  ws.onopen=()=>{stopConnectTimer();setOnline("Socket connected",true);showSetupStatus("Connected to laptop — joining room…");ws.send(JSON.stringify({type:"join",code,source:source.value,target:target.value}));};
  ws.onmessage=async e=>{if(typeof e.data!=="string")return;let m;try{m=JSON.parse(e.data)}catch(err){console.error("Bad server message",e.data);return}
   if(m.type==="joined"){
    stopConnectTimer();connecting=false;setPairButtons(false);$("setup").classList.add("hidden");$("room").classList.remove("hidden");$("roomCode").textContent=m.code;$("sourceBadge").textContent=source.value;$("targetBadge").textContent=target.value;setOnline((m.peer_count||1)>=2?"Paired":"Connected",true);setState((m.peer_count||1)>=2?"Ready — hold to speak":"Waiting for the other device…");
   } else if(m.type==="peer"){
    peerCount=m.count;$("peer").textContent=m.count>=2?"2 devices connected":"Waiting for the other device…";$("onlineText").textContent=m.count>=2?"Paired":"Waiting";
   } else if(m.type==="status"){setState(m.text)}
   else if(m.type==="heard"){$("heard").textContent=m.text}
   else if(m.type==="translation"){$("translated").textContent=m.text}
   else if(m.type==="audio"){
    try{if(!playCtx)playCtx=new(window.AudioContext||window.webkitAudioContext)();await playCtx.resume();const b=Uint8Array.from(atob(m.data),c=>c.charCodeAt(0));const decoded=await playCtx.decodeAudioData(b.buffer.slice(0));const node=playCtx.createBufferSource();node.buffer=decoded;node.connect(playCtx.destination);node.onended=()=>{phase="idle";ws&&ws.send(JSON.stringify({type:"playback_finished"}));};node.start();phase="speaking";setButtons(false);setState("Speaking…")}catch(err){phase="idle";setState("Audio playback failed: "+err.message);ws&&ws.send(JSON.stringify({type:"playback_finished"}))}
   }
   else if(m.type==="turn"){
    phase=m.phase||"idle";const mine=peerCount>=2&&m.client_id===clientId&&phase==="recording";setButtons(mine);
    if(peerCount<2)setState("Waiting for the other device…");
    else if(phase==="processing")setState("Translating and preparing voice…");
    else if(phase==="speaking")setState(m.client_id===clientId?"Translation is playing on the other device…":"Playing translated voice…");
    else setState(mine?"Your turn — recording…":"Ready — hold to speak");
    if(mine && turnRequested && !recording){begin();}
   }
   else if(m.type==="error"){stopConnectTimer();connecting=false;setPairButtons(false);setOnline("Error",false);setState("Error: "+m.text);showSetupStatus(m.text);alert(m.text)}
  };
  ws.onerror=()=>{stopConnectTimer();connecting=false;setPairButtons(false);setOnline("Connection error",false);showSetupStatus("WebSocket failed. Check that both devices use the exact HTTPS address shown by the laptop and allow Python/Uvicorn through Windows Private-network firewall.");};
  ws.onclose=()=>{stopConnectTimer();connecting=false;setPairButtons(false);setOnline("Disconnected",false);setState("Disconnected");setButtons(false)};
 }catch(err){stopConnectTimer();connecting=false;setPairButtons(false);setOnline("Connection error",false);showSetupStatus(err.message);alert(err.message)}
}
$("create").onclick=async()=>{try{$("create").disabled=true;showSetupStatus("Creating temporary room…");const r=await fetch('/api/rooms',{method:'POST',cache:'no-store'});if(!r.ok)throw new Error("Could not create a room on the laptop.");const j=await r.json();$("code").value=j.code;await connect(j.code)}catch(err){showSetupStatus(err.message);alert(err.message)}finally{$("create").disabled=false}};
$("join").onclick=async()=>{const c=$("code").value.replace(/\D/g,'');if(c.length!==6){showSetupStatus("Enter the 6-digit conversation code.");alert('Enter the 6-digit code');return}await connect(c)};
$("code").addEventListener("input",e=>{e.target.value=e.target.value.replace(/\D/g,'').slice(0,6)});
function requestTurn(){
 if(!ws||ws.readyState!==1||peerCount<2||phase!=="idle"||turnRequested||recording)return;
 turnRequested=true;setState("Requesting the turn…");ws.send(JSON.stringify({type:"turn_start"}));
}
async function begin(){if(!ws||ws.readyState!==1||peerCount<2||phase!=="recording"||!turnRequested||recording)return;
 if(!window.isSecureContext||!navigator.mediaDevices||typeof navigator.mediaDevices.getUserMedia!=="function"){recording=false;turnRequested=false;setState("Microphone blocked — open the HTTPS BhashaSaathi address on the phone.");showSetupStatus("Phone microphone needs HTTPS. Use the HTTPS URL displayed by the laptop, then allow microphone permission.");ws.send(JSON.stringify({type:"turn_cancel"}));return;}
 recording=true;pcm=[];setState("Recording… release to send");
 try{if(!playCtx)playCtx=new(window.AudioContext||window.webkitAudioContext)();await playCtx.resume();audioCtx=new(window.AudioContext||window.webkitAudioContext)();await audioCtx.resume();const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});sourceNode=audioCtx.createMediaStreamSource(stream);processor=audioCtx.createScriptProcessor(4096,1,1);processor.onaudioprocess=e=>{if(!recording)return;const input=e.inputBuffer.getChannelData(0),ratio=audioCtx.sampleRate/16000,n=Math.floor(input.length/ratio),out=new Int16Array(n);for(let i=0;i<n;i++){let x=input[Math.floor(i*ratio)];x=Math.max(-1,Math.min(1,x));out[i]=x<0?x*32768:x*32767}pcm.push(out.buffer)};muteNode=audioCtx.createGain();muteNode.gain.value=0;sourceNode.connect(processor);processor.connect(muteNode);muteNode.connect(audioCtx.destination);window._stream=stream;
 }catch(err){recording=false;turnRequested=false;setState("Microphone error: "+err.message);showSetupStatus("Microphone access failed: "+(err&&err.message?err.message:"unknown browser error"));if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:"turn_cancel"}))}}
async function end(){
 if(!recording){if(turnRequested&&ws&&ws.readyState===1){turnRequested=false;ws.send(JSON.stringify({type:"turn_cancel"}));setState("Turn cancelled")}return;}
 recording=false;turnRequested=false;if(processor){processor.disconnect();processor=null}if(sourceNode){sourceNode.disconnect();sourceNode=null}if(muteNode){muteNode.disconnect();muteNode=null}if(window._stream){window._stream.getTracks().forEach(t=>t.stop());window._stream=null}if(audioCtx){await audioCtx.close();audioCtx=null}setState("Processing…");ws.send(JSON.stringify({type:"audio_begin",sample_rate:16000}));for(const b of pcm)ws.send(b);ws.send(JSON.stringify({type:"audio_end"}));pcm=[]}
const ptt=$("ptt");ptt.onpointerdown=e=>{e.preventDefault();try{ptt.setPointerCapture(e.pointerId)}catch(_e){}if(!ptt.disabled){ptt.classList.add("recording");requestTurn()}};ptt.onpointerup=e=>{e.preventDefault();try{ptt.releasePointerCapture(e.pointerId)}catch(_e){}ptt.classList.remove("recording");end()};ptt.onpointercancel=e=>{try{ptt.releasePointerCapture(e.pointerId)}catch(_e){}ptt.classList.remove("recording");end()};
$("leave").onclick=()=>{try{ws&&ws.close()}catch(e){}try{playCtx&&playCtx.close()}catch(e){}location.reload()};setButtons(false);
</script></body></html>
"""


def ensure_p2p_tls_certificate(host_ip: str) -> tuple[Path, Path]:
    """Create a small self-signed certificate for the laptop LAN IP.

    Phone microphone access from getUserMedia() requires a secure context. A local
    HTTPS certificate keeps the demo entirely on the LAN. The browser may show a
    one-time certificate warning because the certificate is self-signed.
    """
    if not P2P_HTTPS:
        raise RuntimeError("P2P HTTPS is disabled by BHASHA_P2P_HTTPS=0.")
    if P2P_CERT_FILE.exists() and P2P_KEY_FILE.exists():
        return P2P_CERT_FILE, P2P_KEY_FILE
    try:
        from datetime import datetime, timedelta, timezone
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception as exc:
        raise RuntimeError(
            "P2P phone microphone mode needs local HTTPS. Install the certificate helper with:\n"
            "pip install cryptography"
        ) from exc

    P2P_TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BhashaSaathi Local"),
        x509.NameAttribute(NameOID.COMMON_NAME, host_ip),
    ])
    san_values = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address(host_ip))]
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName(san_values), critical=False)
        .sign(key, hashes.SHA256())
    )
    P2P_KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    P2P_CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return P2P_CERT_FILE, P2P_KEY_FILE


class P2PRoom:
    def __init__(self, code: str) -> None:
        self.code = code
        self.created_at = time.time()
        self.last_activity = time.time()
        self.clients: dict[str, Any] = {}
        self.meta: dict[str, dict[str, str]] = {}
        self.current_speaker: Optional[str] = None
        self.phase: str = "idle"
        self.lock = threading.RLock()

    def touch(self) -> None:
        self.last_activity = time.time()


class P2PServer:
    """Local-only conversation broker.

    Phones are lightweight browser clients. The laptop owns the temporary room,
    turn lock, STT, IndicTrans2 and Parler-TTS. Only translated text/audio is
    routed to the other client; raw microphone audio is never broadcast.
    """
    def __init__(self, ui: "UIBridge") -> None:
        self.ui = ui
        self.rooms: dict[str, P2PRoom] = {}
        self.client_rooms: dict[str, str] = {}
        self.lock = threading.RLock()
        self.app = FastAPI(title="BhashaSaathi Local P2P") if FASTAPI_AVAILABLE else None
        self.thread: Optional[threading.Thread] = None
        self.server = None
        self.started = False
        self.host_ip = self._detect_lan_ip()
        self.whisper = WhisperEngine(STT_DIR, ui)
        self.tts = ParlerTTSEngine(ui, TTS_DIR, PARLER_DESCRIPTION_TOKENIZER)
        self.translation_models: dict[str, tuple[Any, Any]] = {}
        self.translation_lock = threading.Lock()
        self.ai_lock = threading.Lock()
        if self.app is not None:
            self._routes()

    @staticmethod
    def _detect_lan_ip() -> str:
        # Prefer a real private LAN address. This also works when the laptop
        # hotspot has no internet route, where the usual UDP-to-8.8.8.8 trick
        # can otherwise return 127.0.0.1 or fail.
        candidates: list[str] = []
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
            candidates.extend(addr[4][0] for addr in infos)
        except Exception:
            pass
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            candidates.insert(0, sock.getsockname()[0])
            sock.close()
        except Exception:
            pass
        def is_private(ip: str) -> bool:
            try:
                first, second = [int(x) for x in ip.split(".")[:2]]
                return first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168)
            except Exception:
                return False
        for ip in candidates:
            if is_private(ip):
                return ip
        return next((ip for ip in candidates if ip and not ip.startswith("127.")), "127.0.0.1")

    def url(self) -> str:
        scheme = "https" if P2P_HTTPS else "http"
        return f"{scheme}://{self.host_ip}:{P2P_PORT}"

    def _routes(self) -> None:
        app = self.app
        assert app is not None

        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return HTMLResponse(P2P_HTML.replace("%LANGS%", json.dumps(LANGUAGE_NAMES)))

        @app.post("/api/rooms")
        async def create_room() -> dict[str, Any]:
            with self.lock:
                self._cleanup_rooms()
                for _ in range(20):
                    code = f"{secrets.randbelow(1_000_000):06d}"
                    if code not in self.rooms:
                        self.rooms[code] = P2PRoom(code)
                        break
                else:
                    raise RuntimeError("Could not allocate a temporary room code")
            self.ui.status(f"P2P room created: {code}")
            return {"code": code, "expires_in": P2P_ROOM_TTL_S, "url": self.url(), "secure": P2P_HTTPS}

        @app.get("/api/rooms/{code}")
        async def room_info(code: str) -> dict[str, Any]:
            with self.lock:
                room = self.rooms.get(code)
                if room is None:
                    return {"exists": False}
                return {
                    "exists": True,
                    "clients": len(room.clients),
                    "phase": room.phase,
                    "capacity": 2,
                    "secure": P2P_HTTPS,
                }

        @app.websocket("/ws/{client_id}")
        async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
            await websocket.accept()
            import asyncio
            websocket._bhasha_loop = asyncio.get_running_loop()
            try:
                first = await websocket.receive_text()
                hello = json.loads(first)
                if hello.get("type") != "join":
                    await websocket.send_json({"type": "error", "text": "First message must join a room."})
                    await websocket.close()
                    return
                code = str(hello.get("code", "")).strip()
                source = str(hello.get("source", "English"))
                target = str(hello.get("target", "Hindi"))
                if source not in LANGUAGES or target not in LANGUAGES:
                    await websocket.send_json({"type": "error", "text": "Invalid language selection."})
                    await websocket.close()
                    return
                with self.lock:
                    room = self.rooms.get(code)
                    if room is None:
                        await websocket.send_json({"type": "error", "text": "Room not found or expired. Create a new room on Device 1."})
                        await websocket.close()
                        return
                    if client_id in self.client_rooms:
                        await websocket.send_json({"type": "error", "text": "This browser session is already connected. Refresh the page and try again."})
                        await websocket.close()
                        return
                    if len(room.clients) >= 2:
                        await websocket.send_json({"type": "error", "text": "This room already has two connected devices."})
                        await websocket.close()
                        return
                    room.clients[client_id] = websocket
                    room.meta[client_id] = {"source": source, "target": target}
                    room.touch()
                    self.client_rooms[client_id] = code
                self.ui.status(f"P2P client joined room {code[:6]} ({len(room.clients)}/2)")
                await websocket.send_json({"type": "joined", "code": code, "client_id": client_id, "peer_count": len(room.clients)})
                await self._broadcast_peer(room)
                await self._broadcast_turn(room)
                recording_parts: list[bytes] = []
                recording = False
                while True:
                    msg = await websocket.receive()
                    if "text" in msg and msg["text"] is not None:
                        data = json.loads(msg["text"])
                        typ = data.get("type")
                        room.touch()
                        if typ == "turn_start":
                            granted = False
                            busy = False
                            with room.lock:
                                if room.current_speaker is None and room.phase == "idle":
                                    room.current_speaker = client_id
                                    room.phase = "recording"
                                    granted = True
                                elif room.current_speaker != client_id:
                                    busy = True
                            if granted:
                                await self._broadcast_turn(room)
                            elif busy:
                                await websocket.send_json({"type": "status", "text": "Other device is speaking."})
                        elif typ == "turn_cancel":
                            changed = False
                            with room.lock:
                                if room.current_speaker == client_id:
                                    room.current_speaker = None
                                    room.phase = "idle"
                                    changed = True
                            if changed:
                                await self._broadcast_turn(room)
                        elif typ == "audio_begin":
                            recording_parts = []
                            recording = True
                        elif typ == "audio_end":
                            if recording:
                                recording = False
                                raw = b"".join(recording_parts)
                                recording_parts = []
                                with room.lock:
                                    room.phase = "processing"
                                await self._broadcast_turn(room)
                                threading.Thread(
                                    target=self._process_audio,
                                    args=(room, client_id, raw),
                                    name=f"p2p-ai-{client_id[:6]}",
                                    daemon=True,
                                ).start()
                        elif typ == "playback_finished":
                            changed = False
                            with room.lock:
                                if room.current_speaker is not None and room.phase == "speaking":
                                    room.current_speaker = None
                                    room.phase = "idle"
                                    changed = True
                            if changed:
                                await self._broadcast_turn(room)
                    elif "bytes" in msg and msg["bytes"] is not None and recording:
                        chunk = msg["bytes"]
                        if len(b"".join(recording_parts)) < AUDIO_RATE * 2 * P2P_MAX_RECORDING_S:
                            recording_parts.append(chunk)
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                self.ui.warning(f"P2P client error: {exc}")
            finally:
                with self.lock:
                    code = self.client_rooms.pop(client_id, None)
                    room = self.rooms.get(code) if code else None
                    if room is not None:
                        room.clients.pop(client_id, None)
                        room.meta.pop(client_id, None)
                        if room.current_speaker == client_id:
                            room.current_speaker = None
                            room.phase = "idle"
                        room.touch()
                        if not room.clients:
                            self.rooms.pop(room.code, None)
                if room is not None:
                    try:
                        await self._broadcast_peer(room)
                        await self._broadcast_turn(room)
                    except Exception:
                        pass

    def _cleanup_rooms(self) -> None:
        now = time.time()
        dead = [c for c, r in self.rooms.items() if now - r.last_activity > P2P_ROOM_TTL_S and not r.clients]
        for code in dead:
            self.rooms.pop(code, None)

    async def _broadcast_peer(self, room: P2PRoom) -> None:
        payload = {"type": "peer", "count": len(room.clients)}
        for ws in list(room.clients.values()):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def _broadcast_turn(self, room: P2PRoom) -> None:
        payload = {"type": "turn", "client_id": room.current_speaker or "", "phase": room.phase}
        for ws in list(room.clients.values()):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    def _send_threadsafe(self, ws: Any, payload: dict[str, Any]) -> None:
        # FastAPI websocket objects are tied to the event-loop task, so AI work
        # sends are scheduled back onto the loop through the socket's send call
        # by using a dedicated async bridge created at connection time.
        # In practice the process worker below uses the client's running loop
        # captured in the connection metadata.
        loop = getattr(ws, "_bhasha_loop", None)
        if loop is None:
            return
        import asyncio
        asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)

    def _process_audio(self, room: P2PRoom, sender_id: str, raw: bytes) -> None:
        receiver_id = None
        with room.lock:
            if room.current_speaker != sender_id:
                return
            others = [cid for cid in room.clients if cid != sender_id]
            receiver_id = others[0] if others else None
            if receiver_id is None:
                self._release_turn(room)
                return
            sender_meta = room.meta[sender_id]
            receiver_meta = room.meta[receiver_id]
            receiver_ws = room.clients.get(receiver_id)
            sender_ws = room.clients.get(sender_id)
        if receiver_ws is None:
            self._release_turn(room)
            return
        self._send_threadsafe(sender_ws, {"type": "status", "text": "Processing speech…"})
        self._send_threadsafe(receiver_ws, {"type": "status", "text": "Receiving translation…"})
        try:
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size < int(AUDIO_RATE * 0.15):
                raise ValueError("Recording was too short")
            src = LANGUAGES[sender_meta["source"]]
            tgt = LANGUAGES[receiver_meta["target"]]
            with self.ai_lock:
                self._send_threadsafe(sender_ws, {"type": "status", "text": "Transcribing…"})
                transcript, detected, prob, stt_time = self.whisper.transcribe(audio, src.whisper_code)
                if not transcript.strip():
                    raise ValueError("Speech was detected but no text was transcribed")
                self._send_threadsafe(sender_ws, {"type": "heard", "text": transcript})
                self._send_threadsafe(sender_ws, {"type": "status", "text": "Translating…"})
                translated, trans_time = self._translate_dynamic(transcript, src, tgt)
                self._send_threadsafe(receiver_ws, {"type": "translation", "text": translated})
                self._send_threadsafe(receiver_ws, {"type": "status", "text": "Generating voice…"})
                audio_out, rate, tts_time = self.tts.synthesize(translated, tgt.name)
            if audio_out.size == 0:
                raise ValueError("TTS returned empty audio")
            buf = io.BytesIO()
            sf.write(buf, audio_out, rate, format="WAV", subtype="PCM_16")
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            with room.lock:
                room.phase = "speaking"
            self._release_broadcast_turn(room)
            self._send_threadsafe(receiver_ws, {"type": "audio", "data": encoded})
            self._send_threadsafe(sender_ws, {"type": "status", "text": f"Done • STT {stt_time:.2f}s • TXT {trans_time:.2f}s • TTS {tts_time:.2f}s"})
            self._send_threadsafe(receiver_ws, {"type": "status", "text": "Speaking…"})
        except Exception as exc:
            self._send_threadsafe(sender_ws, {"type": "error", "text": f"Pipeline failed: {exc}"})
            self._send_threadsafe(receiver_ws, {"type": "error", "text": f"Pipeline failed: {exc}"})
            self._release_turn(room)

    def _translate_dynamic(self, text: str, source: Language, target: Language) -> tuple[str, float]:
        if source.name == target.name:
            return text, 0.0
        route = "en-indic" if source.name == "English" else "indic-en" if target.name == "English" else "indic-indic"
        with self.translation_lock:
            if route not in self.translation_models:
                route_dir = {"en-indic": EN_INDIC_DIR, "indic-en": INDIC_EN_DIR, "indic-indic": INDIC_INDIC_DIR}[route]
                self.ui.status(f"P2P loading translation route: {route}")
                tok = AutoTokenizer.from_pretrained(str(route_dir), trust_remote_code=True, local_files_only=True)
                kwargs: dict[str, Any] = {"trust_remote_code": True, "local_files_only": True}
                if choose_compute_device() == "cuda":
                    kwargs["torch_dtype"] = device_dtype("cuda")
                model = AutoModelForSeq2SeqLM.from_pretrained(str(route_dir), **kwargs).to(choose_compute_device())
                model.eval()
                self.translation_models[route] = (tok, model)
            tokenizer, model = self.translation_models[route]
        started = time.perf_counter()
        processor = IndicProcessor(inference=True)
        processed = processor.preprocess_batch([text], src_lang=source.indic_code, tgt_lang=target.indic_code)
        inputs = tokenizer(processed, truncation=True, padding="longest", return_tensors="pt", return_attention_mask=True).to(next(model.parameters()).device)
        with torch.inference_mode():
            generated = model.generate(**inputs, use_cache=True, min_length=0, max_length=MAX_TRANSLATION_LENGTH, num_beams=NUM_BEAMS, num_return_sequences=1)
        decoded = tokenizer.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=True)
        result = processor.postprocess_batch(decoded, lang=target.indic_code)[0].strip()
        return result, max(time.perf_counter() - started, 0.0)

    def _release_broadcast_turn(self, room: P2PRoom) -> None:
        import asyncio
        payload = {"type": "turn", "client_id": room.current_speaker or "", "phase": room.phase}
        for ws in list(room.clients.values()):
            loop = getattr(ws, "_bhasha_loop", None)
            if loop:
                asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)

    def _release_turn(self, room: P2PRoom) -> None:
        with room.lock:
            room.current_speaker = None
            room.phase = "idle"
        import asyncio
        for ws in list(room.clients.values()):
            loop = getattr(ws, "_bhasha_loop", None)
            if loop:
                asyncio.run_coroutine_threadsafe(self._broadcast_turn(room), loop)

    def start(self) -> None:
        if self.started:
            return
        if not FASTAPI_AVAILABLE:
            raise RuntimeError("P2P mode requires fastapi and uvicorn. Install with: pip install fastapi uvicorn")
        if P2P_HTTPS:
            ensure_p2p_tls_certificate(self.host_ip)
        self.started = True
        def runner() -> None:
            kwargs: dict[str, Any] = {
                "app": self.app,
                "host": P2P_HOST,
                "port": P2P_PORT,
                "log_level": "info",
            }
            if P2P_HTTPS:
                kwargs.update(
                    ssl_keyfile=str(P2P_KEY_FILE),
                    ssl_certfile=str(P2P_CERT_FILE),
                )
            config = uvicorn.Config(**kwargs)
            self.server = uvicorn.Server(config)
            self.server.run()
        self.thread = threading.Thread(target=runner, name="bhasha-p2p-server", daemon=True)
        self.thread.start()
        time.sleep(0.8)
        self.ui.status(f"P2P server ready: {self.url()}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        self.started = False


# -----------------------------
# UI bridge
# -----------------------------

class UIBridge:
    """Thread-safe bridge from background workers to Tkinter."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._pipeline: Optional[Pipeline] = None
        self.p2p_server: Optional[P2PServer] = None
        self.running = False

        self.source_var = tk.StringVar(value="Hindi")
        self.target_var = tk.StringVar(value="English")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.latency_var = tk.StringVar(value="—")
        self.queue_var = tk.StringVar(value="STT 0  •  TXT 0  •  TTS 0  •  OUT 0")
        self.sequence_var = tk.StringVar(value="—")
        self.device_var = tk.StringVar(value="CPU")
        self.conversation_mode_var = tk.StringVar(value=LOCAL_CONVERSATION_MODES[0])

        self.transcript_text = tk.StringVar(value="Waiting for speech...")
        self.translation_text = tk.StringVar(value="Waiting for translation...")
        self._history_limit = 100

        self._build()
        self.root.after(100, self._poll_events)

    def _build(self) -> None:
        self.root.title(f"{APP_NAME} — Real-Time Local Translator")
        self.root.geometry("1100x760")
        self.root.minsize(980, 700)
        self.root.configure(bg="#0b1020")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background="#0b1020")
        style.configure("Card.TFrame", background="#11182d")
        style.configure("TLabel", background="#0b1020", foreground="#e9eefb", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"), foreground="#ffffff", background="#0b1020")
        style.configure("Muted.TLabel", font=("Segoe UI", 9), foreground="#9aa7c0", background="#0b1020")
        style.configure("CardTitle.TLabel", font=("Segoe UI", 11, "bold"), foreground="#ffffff", background="#11182d")
        style.configure("Big.TLabel", font=("Segoe UI", 17, "bold"), foreground="#ffffff", background="#11182d")
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=10)
        style.configure("Green.TButton", font=("Segoe UI", 11, "bold"), padding=12)
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=10)
        style.configure("TCombobox", padding=6)

        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, padx=20, pady=18)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="BhashaSaathi", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="Local • Offline-first • Real-time voice translation", style="Muted.TLabel").pack(
            side="left", padx=18, pady=(8, 0)
        )
        ttk.Label(top, textvariable=self.device_var, style="Muted.TLabel").pack(side="right", pady=(8, 0))

        controls = ttk.Frame(outer, style="Card.TFrame", padding=18)
        controls.pack(fill="x", pady=(18, 14))

        row1 = ttk.Frame(controls, style="Card.TFrame")
        row1.pack(fill="x")
        self._field(row1, "SPEAKING LANGUAGE", self.source_var, 0)
        self._field(row1, "LISTENER LANGUAGE", self.target_var, 1)

        swap_btn = ttk.Button(row1, text="⇄  Swap", command=self.swap_languages)
        swap_btn.grid(row=0, column=2, rowspan=2, padx=12, pady=18, sticky="nsew")

        row2 = ttk.Frame(controls, style="Card.TFrame")
        row2.pack(fill="x", pady=(12, 0))
        self._device_field(row2, "MICROPHONE INPUT", self.input_var, 0, input_mode=True)
        self._device_field(row2, "EARPHONES OUTPUT", self.output_var, 1, input_mode=False)

        hint = ttk.Label(
            controls,
            text=(
                "Recommended demo wiring: laptop/external mic as INPUT + wired/Bluetooth earphones as OUTPUT. "
                "Do not use the earbud microphone. Mute laptop speakers to avoid feedback. "
                "P2P Phone Mode hosts a temporary local website on this laptop."
            ),
            style="Muted.TLabel",
            wraplength=980,
        )
        hint.pack(fill="x", pady=(12, 0))

        timing = ttk.Frame(controls, style="Card.TFrame")
        timing.pack(fill="x", pady=(10, 0))
        ttk.Label(timing, text="SPEECH MODE", style="Muted.TLabel").pack(side="left")
        self.conversation_mode_combo = ttk.Combobox(
            timing,
            textvariable=self.conversation_mode_var,
            values=list(LOCAL_CONVERSATION_MODES),
            state="readonly",
            width=29,
        )
        self.conversation_mode_combo.pack(side="left", padx=(12, 0))
        ttk.Label(
            timing,
            text="Turn-by-turn: press Finish Turn or wait for silence, then STT → translation → TTS. "
                 "Continuous: keep listening and queue utterances automatically.",
            style="Muted.TLabel",
        ).pack(side="left", padx=12)

        action = ttk.Frame(outer)
        action.pack(fill="x", pady=(0, 14))
        self.start_btn = ttk.Button(action, text="●  START TRANSLATION", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(action, text="■  STOP", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=10)
        self.finish_turn_btn = ttk.Button(
            action,
            text="✓  FINISH TURN • TRANSLATE NOW",
            command=self.finish_turn,
            state="disabled",
        )
        self.finish_turn_btn.pack(side="left", padx=10)
        ttk.Button(action, text="↻  Refresh Devices", command=self.refresh_devices).pack(side="left")
        ttk.Button(action, text="Test Output", command=self.test_output).pack(side="right")
        ttk.Button(action, text="📱  START P2P PHONE MODE", command=self.start_p2p).pack(side="right", padx=10)

        status_bar = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12))
        status_bar.pack(fill="x", pady=(0, 14))
        ttk.Label(status_bar, text="STATUS", style="Muted.TLabel").pack(side="left")
        ttk.Label(status_bar, textvariable=self.status_var, style="CardTitle.TLabel").pack(side="left", padx=14)
        ttk.Label(status_bar, text="Latency", style="Muted.TLabel").pack(side="right", padx=(20, 5))
        ttk.Label(status_bar, textvariable=self.latency_var, style="CardTitle.TLabel").pack(side="right")
        ttk.Label(status_bar, text="Queues", style="Muted.TLabel").pack(side="right", padx=(20, 5))
        ttk.Label(status_bar, textvariable=self.queue_var, style="CardTitle.TLabel").pack(side="right")

        center = ttk.Frame(outer)
        center.pack(fill="both", expand=True)
        center.columnconfigure(0, weight=1)
        center.columnconfigure(1, weight=1)
        center.rowconfigure(0, weight=1)

        self._history_card(
            center,
            "WHAT WAS HEARD",
            "Every utterance from the beginning • newest stays visible at the bottom",
            self.transcript_text,
            0,
            "transcript_history",
        )
        self._history_card(
            center,
            "WHAT THE OTHER PERSON HEARS",
            "Every translation from the beginning • each queued utterance is spoken in order",
            self.translation_text,
            1,
            "translation_history",
        )

        bottom = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12))
        bottom.pack(fill="x", pady=(14, 0))
        ttk.Label(bottom, text="Pipeline", style="CardTitle.TLabel").pack(side="left")
        ttk.Label(
            bottom,
            text="Mic → VAD → FIFO STT → FIFO Translation → FIFO TTS → Ordered Playback",
            style="Muted.TLabel",
        ).pack(side="left", padx=15)
        ttk.Label(bottom, textvariable=self.sequence_var, style="Muted.TLabel").pack(side="right")

        self.refresh_devices(initial=True)
        self.device_var.set(f"STT/TXT: {choose_compute_device().upper()}  •  TTS: {ParlerTTSEngine._choose_tts_device().upper()}")

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, col: int) -> None:
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=0, column=col, padx=(0 if col == 0 else 12, 0), sticky="ew")
        parent.columnconfigure(col, weight=1)
        ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
        combo = ttk.Combobox(box, textvariable=variable, values=LANGUAGE_NAMES, state="readonly", width=28)
        combo.pack(fill="x", pady=(5, 0))
        if variable.get() in LANGUAGE_NAMES:
            combo.set(variable.get())

    def _device_field(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        col: int,
        input_mode: bool,
    ) -> None:
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=0, column=col, padx=(0 if col == 0 else 12, 0), sticky="ew")
        parent.columnconfigure(col, weight=1)
        ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
        combo = ttk.Combobox(box, textvariable=variable, state="readonly", width=50)
        combo.pack(fill="x", pady=(5, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: None)
        if input_mode:
            self.input_combo = combo
        else:
            self.output_combo = combo

    def _history_card(
        self,
        parent: ttk.Frame,
        title: str,
        subtitle: str,
        latest_var: tk.StringVar,
        col: int,
        history_attr: str,
    ) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=col, padx=(0, 7) if col == 0 else (7, 0), sticky="nsew")
        parent.rowconfigure(0, weight=1)
        ttk.Label(card, text=title, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(card, text=subtitle, style="Muted.TLabel", wraplength=480).pack(anchor="w", pady=(4, 9))

        latest_frame = ttk.Frame(card, style="Card.TFrame")
        latest_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(latest_frame, text="LATEST", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(
            latest_frame,
            textvariable=latest_var,
            style="Big.TLabel",
            wraplength=500,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(4, 0))

        history_frame = tk.Frame(card, bg="#0d1427", highlightthickness=1, highlightbackground="#26314d")
        history_frame.pack(fill="both", expand=True)
        history = tk.Text(
            history_frame,
            bg="#0d1427",
            fg="#eef3ff",
            insertbackground="#eef3ff",
            relief="flat",
            borderwidth=0,
            wrap="word",
            font=("Segoe UI", 11),
            padx=12,
            pady=10,
            state="disabled",
            cursor="arrow",
        )
        scrollbar = ttk.Scrollbar(history_frame, orient="vertical", command=history.yview)
        history.configure(yscrollcommand=scrollbar.set)
        history.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        setattr(self, history_attr, history)

    def _append_history(self, widget: tk.Text, entry: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", entry.rstrip() + "\n\n")
        # Keep the widget bounded while retaining a useful full conversation history.
        lines = int(widget.index("end-1c").split(".")[0])
        max_lines = self._history_limit * 5
        if lines > max_lines:
            widget.delete("1.0", f"{lines - max_lines}.0")
        widget.configure(state="disabled")
        widget.see("end")

    # ---- UI event methods ----

    def status(self, text: str) -> None:
        self.events.put(("status", text))

    def warning(self, text: str) -> None:
        self.events.put(("warning", text))

    def error(self, text: str) -> None:
        self.events.put(("error", text))

    def started(self) -> None:
        self.events.put(("started", None))

    def stopped(self) -> None:
        self.events.put(("stopped", None))

    def segment_created(self, seg: SpeechSegment) -> None:
        self.events.put(("segment", seg.sequence_id))

    def transcript(self, seq: int, text: str, detected: str, probability: float) -> None:
        self.events.put(("transcript", (seq, text, detected, probability)))

    def translation(self, seq: int, text: str) -> None:
        self.events.put(("translation", (seq, text)))

    def ready_for_playback(self, seq: int, latency: float) -> None:
        self.events.put(("ready", (seq, latency)))

    def played(self, seq: int) -> None:
        self.events.put(("played", seq))

    def metrics(self, **values: Any) -> None:
        self.events.put(("metrics", values))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                if event == "status":
                    self.status_var.set(str(payload))
                elif event == "warning":
                    self.status_var.set(f"⚠ {payload}")
                elif event == "error":
                    self.status_var.set("ERROR")
                    self._show_error(str(payload))
                elif event == "started":
                    self.running = True
                    self.start_btn.configure(state="disabled")
                    self.stop_btn.configure(state="normal")
                    self.conversation_mode_combo.configure(state="disabled")
                    turn_mode = self._pipeline is not None and self._pipeline.turn_by_turn
                    self.finish_turn_btn.configure(state="normal" if turn_mode else "disabled")
                elif event == "stopped":
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.conversation_mode_combo.configure(state="readonly")
                    self.finish_turn_btn.configure(state="disabled")
                    self.status_var.set("Stopped")
                elif event == "segment":
                    self.sequence_var.set(f"Segment #{payload}")
                elif event == "transcript":
                    seq, text, detected, prob = payload
                    detail = f"# {seq}   •   Whisper {detected or 'auto'} ({prob:.0%})\n{text}"
                    self.transcript_text.set(text)
                    self._append_history(self.transcript_history, detail)
                    self.sequence_var.set(f"Segment #{seq}")
                elif event == "translation":
                    seq, text = payload
                    detail = f"# {seq}\n{text}"
                    self.translation_text.set(text)
                    self._append_history(self.translation_history, detail)
                    self.sequence_var.set(f"Segment #{seq}")
                elif event == "ready":
                    seq, latency = payload
                    self.latency_var.set(f"{latency:.2f}s")
                    self.sequence_var.set(f"Segment #{seq} ready → FIFO playback")
                elif event == "played":
                    self.sequence_var.set(f"Segment #{payload} played")
                elif event == "metrics":
                    v = payload
                    self.queue_var.set(
                        f"STT {v['segment_q']} • TXT {v['stt_q']} • "
                        f"TTS {v['translation_q']} • OUT {v['tts_q']}"
                    )
                    if v.get("latency") is not None:
                        self.latency_var.set(f"{float(v['latency']):.2f}s")
            except Exception:
                pass
        self.root.after(100, self._poll_events)

    def start(self) -> None:
        if self.running:
            return
        source_name = self.source_var.get().strip()
        target_name = self.target_var.get().strip()
        input_label = self.input_var.get().strip()
        output_label = self.output_var.get().strip()

        if source_name not in LANGUAGES or target_name not in LANGUAGES:
            messagebox.showerror(APP_NAME, "Select valid source and target languages.")
            return
        input_index = find_device_index(input_label)
        output_index = find_device_index(output_label)
        if input_index is None or output_index is None:
            messagebox.showerror(APP_NAME, "Select both a microphone input and earphones output.")
            return

        if input_index == output_index:
            answer = messagebox.askyesno(
                APP_NAME,
                "Input and output are the same physical device. This can cause feedback. Continue?",
            )
            if not answer:
                return

        source = LANGUAGES[source_name]
        target = LANGUAGES[target_name]
        self.transcript_text.set("Listening for speech...")
        self.translation_text.set("Translation will appear here...")
        for history in (self.transcript_history, self.translation_history):
            history.configure(state="normal")
            history.delete("1.0", "end")
            history.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.conversation_mode_combo.configure(state="disabled")
        self.finish_turn_btn.configure(state="disabled")
        self.status_var.set("Starting...")

        self._pipeline = Pipeline(
            ui=self,
            source=source,
            target=target,
            input_device=input_index,
            output_device=output_index,
            turn_by_turn=self.conversation_mode_var.get().startswith("Turn-by-turn"),
        )
        self._pipeline.start()

    def start_p2p(self) -> None:
        if self.p2p_server is None:
            self.p2p_server = P2PServer(self)
        try:
            self.p2p_server.start()
            url = self.p2p_server.url()
            self.status_var.set(f"P2P server: {url}")
            security_note = (
                "\n\nPhone microphone note: this demo uses local HTTPS so mobile browsers can request mic permission. "
                "The first visit may show a self-signed certificate warning; choose Advanced/Proceed for this local demo."
                if P2P_HTTPS else
                "\n\nWarning: HTTP is not suitable for phone microphone access in most modern browsers. Set BHASHA_P2P_HTTPS=1 (default)."
            )
            messagebox.showinfo(
                APP_NAME,
                "P2P Phone Mode is running.\n\n"
                f"On Phone 1 and Phone 2, connect to the same Wi-Fi/hotspot and open:\n\n{url}\n\n"
                "Phone 1: Create Conversation.\n"
                "Phone 2: enter the 6-digit code and Join.\n\n"
                "Hold the microphone button to speak; release it to translate and play the other language."
                + security_note,
            )
        except Exception as exc:
            self._show_error(str(exc))

    def stop(self) -> None:
        if self._pipeline is not None:
            pipeline = self._pipeline
            self._pipeline = None
            threading.Thread(target=pipeline.stop, name="pipeline-stop", daemon=True).start()
        else:
            self.stopped()

    def finish_turn(self) -> None:
        if not self.running or self._pipeline is None:
            return
        if not self._pipeline.turn_by_turn:
            return
        self.finish_turn_btn.configure(state="disabled")
        try:
            self._pipeline.finish_turn()
        finally:
            # Re-enable shortly after the segmenter has received the request; the
            # playback lock is handled by the pipeline itself.
            self.root.after(250, self._refresh_finish_turn_button)

    def _refresh_finish_turn_button(self) -> None:
        if self.running and self._pipeline is not None and self._pipeline.turn_by_turn:
            self.finish_turn_btn.configure(state="normal")

    def swap_languages(self) -> None:
        if self.running or (self._pipeline is not None and self._pipeline.starting):
            messagebox.showinfo(APP_NAME, "Stop the current translation before swapping languages.")
            return
        a = self.source_var.get()
        b = self.target_var.get()
        self.source_var.set(b)
        self.target_var.set(a)

    def refresh_devices(self, initial: bool = False) -> None:
        try:
            inputs, outputs = list_audio_devices()
        except Exception as exc:
            self.status_var.set(f"Audio devices unavailable: {exc}")
            return

        in_values = [label for _, label in inputs]
        out_values = [label for _, label in outputs]
        self.input_combo["values"] = in_values
        self.output_combo["values"] = out_values

        def prefer(values: list[str], keywords: list[str]) -> str:
            for v in values:
                low = v.lower()
                if any(k in low for k in keywords):
                    return v
            return values[0] if values else ""

        if initial:
            self.input_var.set(prefer(in_values, ["microphone", "mic", "realtek"]))
            self.output_var.set(prefer(out_values, ["headphone", "earphone", "buds", "headset"]))
        else:
            if self.input_var.get() not in in_values:
                self.input_var.set(in_values[0] if in_values else "")
            if self.output_var.get() not in out_values:
                self.output_var.set(out_values[0] if out_values else "")
        self.status_var.set("Ready")

    def test_output(self) -> None:
        idx = find_device_index(self.output_var.get())
        if idx is None:
            messagebox.showwarning(APP_NAME, "Select an output device first.")
            return
        fs = 44100
        duration = 0.5
        t = np.arange(int(fs * duration), dtype=np.float32) / fs
        tone = 0.08 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
        try:
            if sc is not None:
                speaker = soundcard_speaker_for_portaudio_index(idx)
                if speaker is None:
                    raise RuntimeError("Could not resolve selected Windows speaker")
                with speaker.player(samplerate=48000, channels=2, blocksize=512, exclusive_mode=False) as player:
                    arr = np.repeat((tone * 0.8)[:, None], 2, axis=1)
                    player.play(arr.astype(np.float32, copy=False))
                    time.sleep(duration + 0.15)
            else:
                sd.play(tone, samplerate=fs, device=idx, blocking=True)
            self.status_var.set("Output test: 440 Hz tone")
        except Exception as exc:
            self._show_error(f"Output test failed: {exc}")

    def _show_error(self, text: str) -> None:
        # Keep very large tracebacks out of the modal message box title area.
        messagebox.showerror(APP_NAME, text[:6000])

    def _on_close(self) -> None:
        if self.p2p_server is not None:
            try:
                self.p2p_server.stop()
            except Exception:
                pass
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                pass
        self.root.destroy()


# -----------------------------
# Diagnostics / CLI
# -----------------------------


def print_environment() -> None:
    print("=== BhashaSaathi environment ===")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Transformers: __check at runtime__")
    try:
        import transformers
        print(f"transformers: {transformers.__version__}")
    except Exception:
        pass
    print(f"Torch: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        try:
            p = torch.cuda.get_device_properties(0)
            print(f"GPU: {p.name} / VRAM {p.total_memory / (1024**3):.1f} GB")
        except Exception:
            pass
    print(f"Model compute choice: {choose_compute_device()}")
    print(f"Whisper device/compute: {choose_whisper_device()}")
    print(f"Parler-TTS device: {ParlerTTSEngine._choose_tts_device()}")
    print(f"Model root: {MODEL_ROOT}")
    for p in [STT_DIR, EN_INDIC_DIR, INDIC_EN_DIR, INDIC_INDIC_DIR, TTS_DIR, PARLER_DESCRIPTION_TOKENIZER]:
        print(f"{'OK ' if p.exists() else 'MISS'} {p}")


def list_devices_cli() -> None:
    print("=== Audio devices ===")
    for idx, d in enumerate(sd.query_devices()):
        print(f"{idx}: {d['name']} | IN={d['max_input_channels']} OUT={d['max_output_channels']} SR={d['default_samplerate']}")


def launch_gui() -> None:
    root = tk.Tk()
    UIBridge(root)
    root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BhashaSaathi local real-time voice translator")
    parser.add_argument("--devices", action="store_true", help="List PortAudio input/output devices and exit")
    parser.add_argument("--check", action="store_true", help="Print environment and model-path diagnostics and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.devices:
        list_devices_cli()
        return
    if args.check:
        print_environment()
        return
    launch_gui()


if __name__ == "__main__":
    main()
