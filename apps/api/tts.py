#!/usr/bin/env python3
"""
BhashaSaathi MASTER TEST BENCH
==============================
A standalone diagnostic/QA runner for the local BhashaSaathi stack.

Goals:
- Prove the local environment and model files are sane.
- Isolate Parler-TTS from the product UI.
- Prove generated audio is non-empty, finite, correctly shaped, and writable.
- Optionally prove the selected Windows audio output actually plays sound.
- Prove IndicTrans2 EN->HI and HI->EN inference independently.
- Optionally record/test microphone + faster-whisper.
- Optionally launch the BhashaSaathi local P2P server and verify HTTP + WebSocket pairing.
- Produce a timestamped report folder that can be sent back for diagnosis.

Design choice:
Dangerous/long model tests can run in a child process with a hard timeout.
That way one hung TTS generation does not freeze the complete test bench.

Typical usage (PowerShell):
    python BhashaSaathi_MASTER_TEST.py
    python BhashaSaathi_MASTER_TEST.py --tts --playback
    python BhashaSaathi_MASTER_TEST.py --translation
    python BhashaSaathi_MASTER_TEST.py --mic 5
    python BhashaSaathi_MASTER_TEST.py --p2p
    python BhashaSaathi_MASTER_TEST.py --all --hardware

After running, send the entire report folder or at least:
    master_test.log
    report.json
    summary.txt
    tts_test.wav (when generated)
    mic_test.wav (when recorded)

The script intentionally does not alter production model files.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import gc
import hashlib
import io
import json
import math
import os
import platform
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import traceback
import urllib.error
import urllib.request
import importlib.util
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# -----------------------------
# Paths / expected versions
# -----------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_ROOT = Path(os.getenv("BHASHA_MODEL_ROOT", str(SCRIPT_DIR / "models"))).expanduser().resolve()
STT_DIR = MODEL_ROOT / "stt" / "whisper-small"
EN_INDIC_DIR = MODEL_ROOT / "translation" / "en-indic"
INDIC_EN_DIR = MODEL_ROOT / "translation" / "indic-en"
INDIC_INDIC_DIR = MODEL_ROOT / "translation" / "indic-indic"
TTS_DIR = MODEL_ROOT / "tts" / "indic-parler-tts"
FLAN_DIR = Path(
    os.getenv("PARLER_DESCRIPTION_TOKENIZER", str(MODEL_ROOT / "tts" / "flan-t5-large"))
).expanduser().resolve()

EXPECTED = {
    "transformers": "4.46.1",
    "huggingface_hub": "0.36.0",
    "parler_tts": "0.2.2",
    "IndicTransToolkit": "1.1.1",
}

TTS_TEXTS = {
    "English": "Hello. This is a real BhashaSaathi voice test.",
    "Hindi": "नमस्ते। यह भाषा साथी का असली आवाज़ परीक्षण है।",
    "Marathi": "नमस्कार। ही भाषा साथीची असली आवाज चाचणी आहे.",
}

TTS_DESCRIPTIONS = {
    "English": "Thoma speaks clear Indian English at a moderate pace with natural pronunciation and consistent volume.",
    "Hindi": "Rohit speaks clear Hindi at a moderate pace with natural Indian pronunciation and consistent volume.",
    "Marathi": "Sanjay speaks clear Marathi at a moderate pace with natural pronunciation and consistent volume.",
}


@dataclass
class Result:
    name: str
    status: str
    duration_s: float
    details: dict[str, Any]
    error: Optional[str] = None


class Logger:
    def __init__(self, path: Path, verbose: bool = True):
        self.path = path
        self.verbose = verbose
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, level: str, msg: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{stamp}] [{level}] {msg}"
        with self.lock:
            with self.path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        if self.verbose:
            print(line, flush=True)

    def info(self, msg: str) -> None:
        self.write("INFO", msg)

    def warn(self, msg: str) -> None:
        self.write("WARN", msg)

    def error(self, msg: str) -> None:
        self.write("ERROR", msg)


class Bench:
    def __init__(self, report_dir: Path, logger: Logger):
        self.report_dir = report_dir
        self.logger = logger
        self.results: list[Result] = []

    def run(self, name: str, fn) -> Result:
        self.logger.info("=" * 84)
        self.logger.info(f"TEST START: {name}")
        started = time.perf_counter()
        try:
            details = fn() or {}
            elapsed = time.perf_counter() - started
            result = Result(name, "PASS", elapsed, details)
            self.logger.info(f"TEST PASS: {name} ({elapsed:.3f}s)")
        except Exception as exc:
            elapsed = time.perf_counter() - started
            details = {"exception_type": type(exc).__name__, "traceback": traceback.format_exc()}
            result = Result(name, "FAIL", elapsed, details, str(exc))
            self.logger.error(f"TEST FAIL: {name} ({elapsed:.3f}s): {exc}")
            self.logger.error(traceback.format_exc().strip())
        self.results.append(result)
        return result

    def skip(self, name: str, reason: str) -> Result:
        result = Result(name, "SKIP", 0.0, {"reason": reason})
        self.results.append(result)
        self.logger.warn(f"TEST SKIP: {name}: {reason}")
        return result

    def write_report(self, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "script": str(Path(__file__).resolve()),
            "model_root": str(MODEL_ROOT),
            "results": [asdict(r) for r in self.results],
        }
        if extra:
            payload.update(extra)
        (self.report_dir / "report.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        counts = {s: sum(r.status == s for r in self.results) for s in ("PASS", "FAIL", "SKIP")}
        summary = [
            "BhashaSaathi MASTER TEST SUMMARY",
            "=" * 40,
            f"Generated: {payload['generated_at']}",
            f"Model root: {MODEL_ROOT}",
            f"PASS: {counts['PASS']}",
            f"FAIL: {counts['FAIL']}",
            f"SKIP: {counts['SKIP']}",
            "",
        ]
        for r in self.results:
            marker = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[r.status]
            line = f"{marker} {r.name}"
            if r.error:
                line += f" :: {r.error}"
            summary.append(line)
        summary += [
            "",
            "Send back: master_test.log + report.json + summary.txt.",
            "Also include tts_test.wav / mic_test.wav when present.",
        ]
        (self.report_dir / "summary.txt").write_text("\n".join(summary), encoding="utf-8")


# -----------------------------
# Utility helpers
# -----------------------------


def import_optional(name: str):
    return __import__(name)


def package_version(pkg: str) -> Optional[str]:
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def list_files(root: Path, limit: int = 100) -> list[str]:
    if not root.exists():
        return []
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(str(p.relative_to(root)))
            if len(files) >= limit:
                break
    return files


def parse_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() not in {"0", "false", "no", "off", ""}


def resample_audio(audio, source_rate: int, target_rate: int):
    import numpy as np
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    from scipy.signal import resample
    n = max(1, int(round(len(audio) * target_rate / source_rate)))
    return resample(np.asarray(audio, dtype=np.float32), n).astype(np.float32, copy=False)


def normalize_audio(audio):
    import numpy as np
    arr = np.asarray(audio, dtype=np.float32).squeeze()
    if arr.ndim > 1:
        arr = arr.mean(axis=0)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1.0:
        arr = arr / peak
    return np.clip(arr, -1.0, 1.0).astype(np.float32)


def tensor_to_audio(generation):
    import numpy as np
    import torch
    if isinstance(generation, torch.Tensor):
        arr = generation.detach().float().cpu().numpy()
    elif hasattr(generation, "sequences"):
        seq = generation.sequences
        try:
            if getattr(generation, "audios_length", None) is not None:
                length = int(generation.audios_length[0].item())
                seq = seq[0, :length]
            else:
                seq = seq[0]
        except Exception:
            pass
        arr = seq.detach().float().cpu().numpy() if isinstance(seq, torch.Tensor) else np.asarray(seq)
    else:
        arr = np.asarray(generation, dtype=np.float32)
    return normalize_audio(arr)


def open_playback_speaker_preflight(speaker, rates=(48000, 44100)):
    errors = []
    for rate in rates:
        try:
            cm = speaker.player(samplerate=rate, channels=2, blocksize=512, exclusive_mode=False)
            player = cm.__enter__()
            return cm, player, rate
        except Exception as exc:
            errors.append(f"{rate}: {exc}")
    raise RuntimeError("SoundCard could not open speaker. " + " | ".join(errors))


def play_with_soundcard(speaker, audio, sample_rate, logger: Logger, label="audio"):
    import numpy as np
    arr = normalize_audio(audio)
    opened = None
    errors = []
    for rate in (48000, 44100):
        try:
            out = resample_audio(arr, sample_rate, rate)
            out = np.repeat(out[:, None], 2, axis=1)
            with speaker.player(samplerate=rate, channels=2, blocksize=512, exclusive_mode=False) as player:
                player.play(out.astype(np.float32, copy=False))
                time.sleep(len(out) / rate + 0.30)
            opened = rate
            break
        except Exception as exc:
            errors.append(f"{rate} Hz: {exc}")
    if opened is None:
        raise RuntimeError(f"{label} playback failed: " + " | ".join(errors))
    logger.info(f"{label} playback submitted and drained at {opened} Hz")
    return opened


def make_tone(seconds=0.8, rate=48000, freq=440.0):
    import numpy as np
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    return (0.08 * np.sin(2 * np.pi * freq * t)).astype(np.float32), rate


# -----------------------------
# Tests
# -----------------------------


def test_environment(logger: Logger) -> dict[str, Any]:
    versions = {}
    import_names = [
        "numpy", "scipy", "sounddevice", "soundfile", "soundcard", "torch",
        "faster_whisper", "transformers", "huggingface_hub", "parler_tts",
        "IndicTransToolkit", "fastapi", "uvicorn"
    ]
    for name in import_names:
        try:
            mod = __import__(name)
            ver = getattr(mod, "__version__", None)
            versions[name] = ver or package_version(name)
        except Exception as exc:
            versions[name] = f"IMPORT_FAIL: {exc}"
    versions.update({"parler-tts": package_version("parler-tts"), "IndicTransToolkit-dist": package_version("IndicTransToolkit")})

    try:
        import torch
        cuda = bool(torch.cuda.is_available())
        gpu = torch.cuda.get_device_name(0) if cuda else None
        vram = int(torch.cuda.get_device_properties(0).total_memory) if cuda else None
    except Exception:
        cuda, gpu, vram = False, None, None

    info = {
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": platform.processor(),
        "cwd": str(Path.cwd()),
        "script_dir": str(SCRIPT_DIR),
        "model_root": str(MODEL_ROOT),
        "versions": versions,
        "cuda_available": cuda,
        "gpu": gpu,
        "vram_bytes": vram,
    }
    logger.info("Environment: " + json.dumps(info, ensure_ascii=False))
    return info


def test_model_inventory(logger: Logger) -> dict[str, Any]:
    roots = {
        "stt": STT_DIR,
        "en-indic": EN_INDIC_DIR,
        "indic-en": INDIC_EN_DIR,
        "indic-indic": INDIC_INDIC_DIR,
        "tts": TTS_DIR,
        "flan-description-tokenizer": FLAN_DIR,
    }
    report = {}
    missing = []
    for name, root in roots.items():
        exists = root.exists() and root.is_dir()
        files = list_files(root, 120)
        report[name] = {"path": str(root), "exists": exists, "file_count_sample": len(files), "files": files}
        logger.info(f"Model inventory {name}: exists={exists} path={root} sample_files={len(files)}")
        if not exists:
            missing.append(f"{name}: {root}")

    required_flan = ["config.json", "tokenizer_config.json", "special_tokens_map.json", "spiece.model"]
    if FLAN_DIR.exists():
        present = {x.name for x in FLAN_DIR.iterdir() if x.is_file()}
        required_map = {f: f in present for f in required_flan}
        report["flan-description-tokenizer"]["required_files"] = required_map
        missing_required = [f for f, ok in required_map.items() if not ok]
    else:
        missing_required = required_flan
    if missing or missing_required:
        raise RuntimeError(
            "Model inventory incomplete. Missing roots=" + repr(missing) +
            " missing FLAN tokenizer assets=" + repr(missing_required)
        )
    return {"roots": report, "missing_roots": missing, "missing_flan_assets": missing_required}


def test_audio_devices(logger: Logger) -> dict[str, Any]:
    import sounddevice as sd
    devices = sd.query_devices()
    inputs, outputs = [], []
    for i, d in enumerate(devices):
        rec = {
            "index": i,
            "name": d.get("name"),
            "max_input_channels": int(d.get("max_input_channels", 0)),
            "max_output_channels": int(d.get("max_output_channels", 0)),
            "default_samplerate": d.get("default_samplerate"),
        }
        if rec["max_input_channels"] > 0:
            inputs.append(rec)
        if rec["max_output_channels"] > 0:
            outputs.append(rec)
    report = {"default_device": sd.default.device, "inputs": inputs, "outputs": outputs}
    logger.info(f"Audio devices: {len(inputs)} inputs, {len(outputs)} outputs, default={sd.default.device}")
    for d in outputs:
        logger.info(f"OUTPUT {d['index']}: {d['name']} rate={d['default_samplerate']}")
    return report


def test_soundcard_output(logger: Logger, playback: bool) -> dict[str, Any]:
    import soundcard as sc
    speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("SoundCard default speaker is None")
    info = {"speaker": str(speaker), "name": getattr(speaker, "name", None)}
    # Open/close without submitting audio first. This isolates WASAPI endpoint opening.
    cm, player, rate = open_playback_speaker_preflight(speaker)
    cm.__exit__(None, None, None)
    info["open_rate"] = rate
    logger.info(f"SoundCard speaker open PASS: {speaker} at {rate} Hz")
    if playback:
        tone, tone_rate = make_tone()
        play_with_soundcard(speaker, tone, tone_rate, logger, label="440Hz tone")
        info["tone_played"] = True
    else:
        info["tone_played"] = False
    return info


def _load_tts_direct(logger: Logger, target_language: str):
    import torch
    from transformers import AutoTokenizer
    from parler_tts import ParlerTTSForConditionalGeneration

    override = os.getenv("BHASHA_TTS_DEVICE", "auto").strip().lower()
    if override == "cuda" and torch.cuda.is_available():
        device = "cuda"
    elif override == "cpu":
        device = "cpu"
    elif torch.cuda.is_available() and torch.cuda.get_device_properties(0).total_memory >= 6 * 1024**3:
        device = "cuda"
    else:
        device = "cpu"

    logger.info(f"TTS direct loader: device={device}")
    if not TTS_DIR.exists():
        raise FileNotFoundError(TTS_DIR)
    if not FLAN_DIR.exists():
        raise FileNotFoundError(
            f"Missing FLAN-T5 description tokenizer: {FLAN_DIR}. "
            "Provision config.json/tokenizer.json/tokenizer_config.json/special_tokens_map.json/spiece.model."
        )

    prompt_tok = AutoTokenizer.from_pretrained(str(TTS_DIR), local_files_only=True)
    desc_tok = AutoTokenizer.from_pretrained(str(FLAN_DIR), local_files_only=True)
    logger.info(f"TTS prompt tokenizer={type(prompt_tok).__name__} vocab={getattr(prompt_tok, 'vocab_size', None)}")
    logger.info(f"TTS description tokenizer={type(desc_tok).__name__} vocab={getattr(desc_tok, 'vocab_size', None)}")

    kwargs = {"local_files_only": True}
    if device == "cuda":
        kwargs["torch_dtype"] = torch.float16
    model = ParlerTTSForConditionalGeneration.from_pretrained(str(TTS_DIR), **kwargs).to(device)
    model.eval()
    logger.info("Parler model loaded successfully")
    return model, prompt_tok, desc_tok, device


def test_tts_generation(logger: Logger, report_dir: Path, language: str, timeout_s: int = 180) -> dict[str, Any]:
    # This function is called only inside a child process by run_isolated_tts.
    import numpy as np
    import torch

    model, prompt_tok, desc_tok, device = _load_tts_direct(logger, language)
    text = TTS_TEXTS.get(language, TTS_TEXTS["Hindi"])
    description = TTS_DESCRIPTIONS.get(language, TTS_DESCRIPTIONS["Hindi"])

    desc_inputs = desc_tok(description, return_tensors="pt", truncation=True, max_length=128)
    desc_inputs = {k: v.to(device) for k, v in desc_inputs.items()}
    prompt_inputs = prompt_tok(text, return_tensors="pt", truncation=True, max_length=256)
    prompt_inputs = {k: v.to(device) for k, v in prompt_inputs.items()}

    sampling_rate = None
    if getattr(getattr(model, "audio_encoder", None), "config", None) is not None:
        sampling_rate = getattr(model.audio_encoder.config, "sampling_rate", None)
    if sampling_rate is None:
        sampling_rate = getattr(model.config, "sampling_rate", None)
    sampling_rate = int(sampling_rate or 44100)

    logger.info(f"TTS generation input text={text!r}")
    logger.info(f"TTS description={description!r}")
    logger.info(f"TTS sampling_rate={sampling_rate}")

    started = time.perf_counter()
    with torch.inference_mode():
        generation = model.generate(
            input_ids=desc_inputs["input_ids"],
            attention_mask=desc_inputs.get("attention_mask"),
            prompt_input_ids=prompt_inputs["input_ids"],
            prompt_attention_mask=prompt_inputs.get("attention_mask"),
            do_sample=False,
            use_cache=True,
        )
    elapsed = time.perf_counter() - started
    audio = tensor_to_audio(generation)
    duration = len(audio) / sampling_rate if sampling_rate else 0.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    finite = bool(np.isfinite(audio).all()) if audio.size else True
    out = report_dir / f"tts_test_{language.lower()}.wav"
    import soundfile as sf
    sf.write(out, audio, sampling_rate, subtype="PCM_16")

    details = {
        "language": language,
        "text": text,
        "device": device,
        "sampling_rate": sampling_rate,
        "generation_time_s": elapsed,
        "audio_samples": int(audio.size),
        "audio_duration_s": duration,
        "peak": peak,
        "rms": rms,
        "finite": finite,
        "wav": str(out),
        "rtf": (elapsed / duration) if duration > 0 else None,
    }
    logger.info("TTS result: " + json.dumps(details, ensure_ascii=False))
    if audio.size == 0:
        raise RuntimeError("Parler generate() returned EMPTY audio")
    if not finite:
        raise RuntimeError("Generated audio contains NaN/Inf")
    if peak < 1e-5 or rms < 1e-6:
        raise RuntimeError(f"Generated waveform looks silent: peak={peak:.8f}, rms={rms:.8f}")
    return details


def run_child_with_timeout(args: list[str], report_dir: Path, logger: Logger, timeout_s: int) -> dict[str, Any]:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker"] + args + ["--report-dir", str(report_dir)]
    logger.info("Child command: " + " ".join(cmd))
    started = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines = []
    try:
        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                lines.append(line.rstrip())
                logger.info("[child] " + line.rstrip())
            if proc.poll() is not None:
                break
            if time.perf_counter() - started > timeout_s:
                proc.kill()
                proc.wait(timeout=5)
                raise TimeoutError(f"Child process exceeded timeout of {timeout_s}s")
        rc = proc.returncode
    finally:
        if proc.stdout:
            proc.stdout.close()
    # Child writes a machine-readable worker result. Read it even on failure so the
    # parent report preserves the exact model/tokenizer/generation exception.
    marker = report_dir / "worker_result.json"
    result = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    if rc != 0:
        detail = result.get("error") or f"Child process exited with code {rc}"
        tb = result.get("traceback")
        if tb:
            logger.error("Child traceback:" + "\n" + tb)
        raise RuntimeError(detail)
    logger.info("Worker result loaded")
    return result
    return {"stdout_tail": lines[-100:]}


def test_tts_isolated(logger: Logger, report_dir: Path, language: str, timeout_s: int) -> dict[str, Any]:
    try:
        return run_child_with_timeout(["--tts-generation", "--language", language], report_dir, logger, timeout_s)
    except Exception as exc:
        # Preserve timeout/hang diagnosis as a clear result.
        raise



def locate_app_script(explicit: str = "") -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        SCRIPT_DIR / "BhashaSaathi_FINAL_2MODE_V2.py",
        SCRIPT_DIR / "BhashaSaathi_FINAL_2MODE_AUDITED.py",
        SCRIPT_DIR / "main.py",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("Could not find the BhashaSaathi application script. Use --app-script <path>.")


class FakeUI:
    """Tiny UI bridge used to exercise the real application engines without Tk."""
    def __init__(self, logger: Logger):
        self.logger = logger

    def status(self, text: str):
        self.logger.info(f"[APP] STATUS: {text}")

    def warning(self, text: str):
        self.logger.warn(f"[APP] WARNING: {text}")

    def error(self, text: str):
        self.logger.error(f"[APP] ERROR: {text}")

    def __getattr__(self, name):
        def sink(*args, **kwargs):
            self.logger.info(f"[APP] EVENT {name}: args={args!r} kwargs={kwargs!r}")
        return sink


def import_app_module(app_path: Path):
    spec = importlib.util.spec_from_file_location("bhashasaathi_app_under_test", str(app_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to create import spec for {app_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_app_tts_integration(logger: Logger, report_dir: Path, language: str, app_script: str) -> dict[str, Any]:
    app_path = locate_app_script(app_script)
    logger.info(f"Application-under-test: {app_path}")
    app = import_app_module(app_path)
    ui = FakeUI(logger)
    engine = app.ParlerTTSEngine(ui, app.TTS_DIR, app.PARLER_DESCRIPTION_TOKENIZER)
    text = TTS_TEXTS.get(language, TTS_TEXTS["Hindi"])
    started = time.perf_counter()
    audio, rate, elapsed = engine.synthesize(text, language)
    import numpy as np
    import soundfile as sf
    arr = normalize_audio(audio)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
    wav = report_dir / f"app_tts_integration_{language.lower()}.wav"
    sf.write(wav, arr, int(rate), subtype="PCM_16")
    info = {
        "app_script": str(app_path),
        "language": language,
        "text": text,
        "rate": int(rate),
        "samples": int(arr.size),
        "duration_s": float(len(arr) / rate) if rate else 0.0,
        "peak": peak,
        "rms": rms,
        "engine_reported_elapsed_s": elapsed,
        "wall_elapsed_s": time.perf_counter() - started,
        "wav": str(wav),
    }
    logger.info("Application TTS integration result: " + json.dumps(info, ensure_ascii=False))
    if arr.size == 0 or peak < 1e-5 or rms < 1e-6:
        raise RuntimeError("Application TTS path generated empty/silent audio")
    return info


def load_pcm16_16k(wav_path: Path):
    import numpy as np
    import soundfile as sf
    audio, rate = sf.read(wav_path, dtype="float32")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    audio = normalize_audio(audio)
    if int(rate) != 16000:
        audio = resample_audio(audio, int(rate), 16000)
        rate = 16000
    pcm = np.clip(audio, -1, 1) * 32767.0
    return pcm.astype(np.int16).tobytes(), int(rate)


def ws_connect_client(uri: str):
    try:
        import websocket as ws_client  # websocket-client
        opts = {"timeout": 8}
        if uri.startswith("wss://"):
            opts["sslopt"] = {"cert_reqs": ssl.CERT_NONE}
        return "websocket-client", ws_client.create_connection(uri, **opts)
    except Exception:
        try:
            from websockets.sync.client import connect as ws_connect  # websockets >= 12
            kwargs = {"open_timeout": 8}
            if uri.startswith("wss://"):
                kwargs["ssl_context"] = ssl._create_unverified_context()
            return "websockets.sync", ws_connect(uri, **kwargs)
        except Exception as exc:
            raise RuntimeError("Install websocket-client or websockets for P2P tests") from exc


def ws_send(ws, payload: dict[str, Any]):
    ws.send(json.dumps(payload))


def ws_recv_json(ws, timeout: float = 10.0):
    if hasattr(ws, "settimeout"):
        ws.settimeout(timeout)
    raw = ws.recv(timeout=timeout) if "websockets.sync" in type(ws).__module__ else ws.recv()
    return json.loads(raw)


def p2p_connect_pair(base: str, logger: Logger):
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        raise ValueError("P2P URL must start with http:// or https://")
    create_url = base.rstrip("/") + "/api/rooms"
    req = urllib.request.Request(create_url, method="POST", data=b"", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8, context=ssl._create_unverified_context()) as resp:
        room = json.loads(resp.read().decode("utf-8"))
    code = str(room["code"])
    logger.info(f"P2P room created for pipeline test: {code}")
    mode1, ws1 = ws_connect_client(f"{ws_base}/ws/test-a-{os.getpid()}")
    mode2, ws2 = ws_connect_client(f"{ws_base}/ws/test-b-{os.getpid()}")
    ws_send(ws1, {"type": "join", "code": code, "source": "English", "target": "Hindi"})
    ws_send(ws2, {"type": "join", "code": code, "source": "Hindi", "target": "English"})
    # Each client should see joined plus peer/turn events. Consume until joined.
    joined1 = None; joined2 = None
    deadline = time.time() + 12
    while time.time() < deadline and (joined1 is None or joined2 is None):
        if joined1 is None:
            m = ws_recv_json(ws1, 3)
            if m.get("type") == "joined": joined1 = m
        if joined2 is None:
            m = ws_recv_json(ws2, 3)
            if m.get("type") == "joined": joined2 = m
    if not joined1 or not joined2:
        raise RuntimeError(f"Could not complete websocket join: joined1={joined1}, joined2={joined2}")
    logger.info(f"P2P pair joined successfully via {mode1}/{mode2}: {code}")
    return ws1, ws2, code, ws_base


def test_p2p_audio_pipeline(logger: Logger, report_dir: Path, base: str, wav_path: Path, timeout_s: int = 240) -> dict[str, Any]:
    raw, rate = load_pcm16_16k(wav_path)
    if rate != 16000:
        raise RuntimeError(f"Expected 16k PCM, got {rate}")
    ws1 = ws2 = None
    try:
        ws1, ws2, code, ws_base = p2p_connect_pair(base, logger)
        ws_send(ws1, {"type": "turn_start"})
        turn_granted = False
        phase_deadline = time.time() + 10
        while time.time() < phase_deadline:
            m = ws_recv_json(ws1, 5)
            logger.info("P2P sender event: " + json.dumps(m, ensure_ascii=False))
            if m.get("type") == "turn" and m.get("phase") == "recording":
                turn_granted = True
                break
        if not turn_granted:
            raise RuntimeError("P2P turn was not granted to sender")

        ws_send(ws1, {"type": "audio_begin"})
        chunk_bytes = 4096 * 2
        sent = 0
        for i in range(0, len(raw), chunk_bytes):
            ws1.send(raw[i:i+chunk_bytes], opcode=2) if hasattr(ws1, "send") else ws1.send(raw[i:i+chunk_bytes])
            sent += min(chunk_bytes, len(raw) - i)
        ws_send(ws1, {"type": "audio_end"})
        logger.info(f"P2P synthetic audio sent: {sent} bytes")

        events = []
        received_audio = None
        translated = None
        end = time.time() + timeout_s
        while time.time() < end:
            # Prefer the receiver because translated audio should arrive there.
            try:
                m = ws_recv_json(ws2, 5)
            except Exception as exc:
                logger.info(f"Receiver wait: {exc}")
                continue
            events.append(m)
            logger.info("P2P receiver event: " + json.dumps({k: v for k, v in m.items() if k != "data"}, ensure_ascii=False))
            if m.get("type") == "translation":
                translated = m.get("text")
            if m.get("type") == "audio":
                received_audio = base64.b64decode(m.get("data", ""))
                break
            if m.get("type") == "error":
                raise RuntimeError("P2P AI pipeline error: " + str(m.get("text")))

        info = {"room_code": code, "translated_text": translated, "events": events, "audio_bytes": len(received_audio or b"")}
        if received_audio:
            out = report_dir / "p2p_received_audio.wav"
            out.write_bytes(received_audio)
            info["received_audio_wav"] = str(out)
        if not translated:
            raise RuntimeError("P2P translation message never arrived")
        if not received_audio:
            raise RuntimeError("P2P translated audio never arrived")
        return info
    finally:
        for ws in (ws1, ws2):
            try:
                if ws:
                    ws.close()
            except Exception:
                pass


def test_translation(logger: Logger, language_pair: str = "en-hi") -> dict[str, Any]:
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    from IndicTransToolkit.processor import IndicProcessor

    pairs = {
        "en-hi": (EN_INDIC_DIR, "eng_Latn", "hin_Deva", "Welcome to our classroom."),
        "hi-en": (INDIC_EN_DIR, "hin_Deva", "eng_Latn", "हमारी कक्षा में आपका स्वागत है।"),
        "en-mr": (EN_INDIC_DIR, "eng_Latn", "mar_Deva", "Welcome to our classroom."),
    }
    if language_pair not in pairs:
        raise ValueError(f"Unknown translation pair: {language_pair}")
    model_dir, src_lang, tgt_lang, text = pairs[language_pair]
    started = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True, local_files_only=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(str(model_dir), trust_remote_code=True, local_files_only=True)
    model.eval()
    proc = IndicProcessor(inference=True)
    processed = proc.preprocess_batch([text], src_lang=src_lang, tgt_lang=tgt_lang)
    inputs = tok(processed, truncation=True, padding="longest", return_tensors="pt", return_attention_mask=True).to(next(model.parameters()).device)
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            use_cache=True,
            min_length=0,
            max_length=256,
            num_beams=4,
            num_return_sequences=1,
        )
    decoded = tok.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=True)
    result = proc.postprocess_batch(decoded, lang=tgt_lang)[0].strip()
    elapsed = time.perf_counter() - started
    if not result:
        raise RuntimeError("Translation returned empty text")
    info = {"pair": language_pair, "input": text, "output": result, "elapsed_s": elapsed}
    logger.info("Translation result: " + json.dumps(info, ensure_ascii=False))
    return info


def record_microphone(logger: Logger, path: Path, seconds: int, device: Optional[int] = None) -> dict[str, Any]:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    rate = 16000
    channels = 1
    logger.info(f"MIC recording {seconds}s @ {rate} Hz device={device}. Speak clearly after the start tone.")
    tone, tone_rate = make_tone(0.15, rate, 880)
    if device is not None:
        sd.play(tone, samplerate=tone_rate, device=device, blocking=True)
        audio = sd.rec(int(seconds * rate), samplerate=rate, channels=channels, dtype="float32", device=device, blocking=True)
    else:
        sd.play(tone, samplerate=tone_rate, blocking=True)
        audio = sd.rec(int(seconds * rate), samplerate=rate, channels=channels, dtype="float32", blocking=True)
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    sf.write(path, audio, rate, subtype="PCM_16")
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    info = {"wav": str(path), "rate": rate, "samples": int(audio.size), "peak": peak, "rms": rms}
    logger.info("MIC recording result: " + json.dumps(info))
    if rms < 1e-5:
        logger.warn("Microphone recording is near-silent")
    return info


def test_whisper_file(logger: Logger, wav_path: Path, language: str = "en") -> dict[str, Any]:
    from faster_whisper import WhisperModel
    import soundfile as sf
    audio, rate = sf.read(wav_path, dtype="float32")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    if rate != 16000:
        audio = resample_audio(audio, int(rate), 16000)
        rate = 16000
    started = time.perf_counter()
    model = WhisperModel(str(STT_DIR), device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio, language=language, beam_size=5, vad_filter=True, condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segments).strip()
    elapsed = time.perf_counter() - started
    out = {"language": language, "text": text, "elapsed_s": elapsed, "detected_language": getattr(info, "language", None), "probability": getattr(info, "language_probability", None)}
    logger.info("Whisper result: " + json.dumps(out, ensure_ascii=False))
    if not text:
        raise RuntimeError("Whisper returned empty transcription")
    return out


def get_local_ips() -> list[str]:
    ips = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
        for x in infos:
            ip = x[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def https_json(url: str, timeout: float = 5.0) -> tuple[int, Any]:
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, method="GET", headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
        return resp.status, json.loads(raw.decode("utf-8"))


def test_p2p_http(logger: Logger, url: str) -> dict[str, Any]:
    base = url.rstrip("/")
    code_url = base + "/api/rooms/000000"
    status, data = https_json(code_url)
    result = {"url": base, "probe_status": status, "probe_body": data}
    logger.info("P2P HTTP probe: " + json.dumps(result))
    if status != 200:
        raise RuntimeError(f"P2P room probe returned HTTP {status}")
    return result


def test_p2p_ws(logger: Logger, url: str) -> dict[str, Any]:
    """Real HTTP + websocket pairing check, when a WebSocket client library exists."""
    # Prefer websocket-client, then websockets sync API.
    ws_client = None
    mode = None
    try:
        import websocket as ws_client  # type: ignore
        mode = "websocket-client"
    except Exception:
        ws_client = None

    if ws_client is None:
        try:
            from websockets.sync.client import connect as ws_connect  # type: ignore
            mode = "websockets.sync"
        except Exception as exc:
            raise RuntimeError("Install websocket-client or websockets to run the real WebSocket P2P test") from exc

    base = url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        raise ValueError("P2P URL must start with http:// or https://")

    context = None
    if ws_base.startswith("wss://"):
        context = ssl._create_unverified_context()

    # Create room.
    create_url = base + "/api/rooms"
    req = urllib.request.Request(create_url, method="POST", data=b"", headers={"Content-Type": "application/json"})
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    code = str(body["code"])
    logger.info(f"P2P real WS test room created: {code}")

    c1 = f"test-{os.getpid()}-a"
    c2 = f"test-{os.getpid()}-b"
    uri1 = f"{ws_base}/ws/{c1}"
    uri2 = f"{ws_base}/ws/{c2}"

    if mode == "websocket-client":
        opts = {}
        if context is not None:
            opts["sslopt"] = {"cert_reqs": ssl.CERT_NONE}
        ws1 = ws_client.create_connection(uri1, timeout=5, **opts)
        ws2 = ws_client.create_connection(uri2, timeout=5, **opts)
        try:
            ws1.send(json.dumps({"type": "join", "code": code, "source": "English", "target": "Hindi"}))
            msg1 = json.loads(ws1.recv())
            ws2.send(json.dumps({"type": "join", "code": code, "source": "Hindi", "target": "English"}))
            msg2 = json.loads(ws2.recv())
            peer1 = json.loads(ws1.recv())
            peer2 = json.loads(ws2.recv())
        finally:
            ws1.close(); ws2.close()
    else:
        connect_kwargs = {"open_timeout": 5}
        if context is not None:
            connect_kwargs["ssl_context"] = context
        with ws_connect(uri1, **connect_kwargs) as ws1, ws_connect(uri2, **connect_kwargs) as ws2:
            ws1.send(json.dumps({"type": "join", "code": code, "source": "English", "target": "Hindi"}))
            msg1 = json.loads(ws1.recv())
            ws2.send(json.dumps({"type": "join", "code": code, "source": "Hindi", "target": "English"}))
            msg2 = json.loads(ws2.recv())
            peer1 = json.loads(ws1.recv())
            peer2 = json.loads(ws2.recv())

    result = {"mode": mode, "room_code": code, "client1": msg1, "client2": msg2, "peer1": peer1, "peer2": peer2}
    logger.info("P2P websocket result: " + json.dumps(result))
    if msg1.get("type") != "joined" or msg2.get("type") != "joined":
        raise RuntimeError(f"P2P WebSocket join failed: {result}")
    return result


# -----------------------------
# Child worker
# -----------------------------


def worker_main(args: argparse.Namespace) -> int:
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    logger = Logger(report_dir / "worker.log", verbose=True)
    try:
        if args.tts_generation:
            details = test_tts_generation(logger, report_dir, args.language)
        else:
            raise RuntimeError("Unknown worker operation")
        (report_dir / "worker_result.json").write_text(json.dumps(details, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:
        logger.error(str(exc))
        (report_dir / "worker_result.json").write_text(json.dumps({"status": "FAIL", "error": str(exc), "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False), encoding="utf-8")
        return 1


# -----------------------------
# Main runner
# -----------------------------


def build_report_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCRIPT_DIR / f"bhashasaathi_test_report_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BhashaSaathi professional master test bench")
    p.add_argument("--all", action="store_true", help="Run all safe tests; add --hardware for physical audio/mic")
    p.add_argument("--hardware", action="store_true", help="Run physical audio output tone/TTS playback")
    p.add_argument("--preflight", action="store_true", help="Environment + model inventory + devices")
    p.add_argument("--tts", action="store_true", help="Isolated TTS generation test")
    p.add_argument("--translation", action="store_true", help="Translation inference tests")
    p.add_argument("--mic", type=int, metavar="SECONDS", help="Record microphone for N seconds and run Whisper")
    p.add_argument("--p2p", action="store_true", help="Probe an already-running P2P server")
    p.add_argument("--p2p-url", default=os.getenv("BHASHA_P2P_TEST_URL", ""), help="P2P base URL, e.g. https://192.168.1.10:8765")
    p.add_argument("--p2p-file", default="", help="Optional 16k/any-rate WAV used for a real P2P AI pipeline test")
    p.add_argument("--start-p2p", action="store_true", help="Start the P2P server from the application-under-test in this process")
    p.add_argument("--app-script", default="", help="Application script under test, e.g. BhashaSaathi_FINAL_2MODE_V2.py")
    p.add_argument("--app-tts", action="store_true", help="Run the application's own ParlerTTSEngine.synthesize path")
    p.add_argument("--language", default="Hindi", choices=sorted(TTS_TEXTS), help="TTS language")
    p.add_argument("--tts-timeout", type=int, default=180, help="Max TTS child-process time")
    p.add_argument("--input-device", type=int, default=None, help="sounddevice input index for --mic")
    p.add_argument("--noninteractive", action="store_true", help="Never ask physical-hearing confirmation")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--tts-generation", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--report-dir", default="", help=argparse.SUPPRESS)
    return p.parse_args()


def confirm_heard(prompt: str, noninteractive: bool) -> str:
    if noninteractive:
        return "UNKNOWN_NONINTERACTIVE"
    try:
        ans = input(prompt + " [y/n]: ").strip().lower()
    except EOFError:
        return "UNKNOWN_EOF"
    return "YES" if ans in {"y", "yes"} else "NO"


def run_main(args: argparse.Namespace) -> int:
    report_dir = build_report_dir()
    logger = Logger(report_dir / "master_test.log", verbose=True)
    bench = Bench(report_dir, logger)

    logger.info("BhashaSaathi MASTER TEST START")
    logger.info(f"Report directory: {report_dir}")
    logger.info(f"Command line: {sys.argv}")

    # Always run preflight if no explicit test selection, or for --all.
    selected = any([args.all, args.preflight, args.tts, args.translation, args.mic, args.p2p, args.app_tts, args.start_p2p, bool(args.p2p_file)])
    if not selected:
        args.preflight = args.tts = args.translation = True

    if args.all or args.preflight:
        bench.run("01_environment", lambda: test_environment(logger))
        bench.run("02_model_inventory", lambda: test_model_inventory(logger))
        bench.run("03_audio_devices", lambda: test_audio_devices(logger))

    if args.all or args.translation:
        for pair in ("en-hi", "hi-en"):
            bench.run(f"translation_{pair}", lambda pair=pair: test_translation(logger, pair))

    if args.all or args.tts:
        r = bench.run("tts_isolated_generation", lambda: test_tts_isolated(logger, report_dir, args.language, args.tts_timeout))
        if r.status == "PASS" and args.hardware:
            import soundcard as sc
            speaker = sc.default_speaker()
            if speaker is None:
                bench.skip("tts_physical_playback", "No SoundCard default speaker available")
            else:
                wav_path = Path(r.details.get("wav", report_dir / f"tts_test_{args.language.lower()}.wav"))
                import soundfile as sf
                audio, rate = sf.read(wav_path, dtype="float32")
                if getattr(audio, "ndim", 1) > 1:
                    audio = audio.mean(axis=1)
                def physical_tts():
                    play_with_soundcard(speaker, audio, int(rate), logger, label=f"TTS {args.language}")
                    heard = confirm_heard("Did you physically hear the generated TTS sentence?", args.noninteractive)
                    return {"heard_confirmation": heard, "wav": str(wav_path)}
                bench.run("tts_physical_playback", physical_tts)
        elif args.hardware:
            bench.skip("tts_physical_playback", "Skipped because isolated TTS generation failed")

    if args.all and args.hardware:
        bench.run("physical_output_tone", lambda: test_soundcard_output(logger, playback=True))
    elif args.all and not args.hardware:
        bench.run("soundcard_output_open", lambda: test_soundcard_output(logger, playback=False))

    if args.app_tts or args.all:
        bench.run("app_tts_integration", lambda: test_app_tts_integration(logger, report_dir, args.language, args.app_script))

    if args.mic:
        mic_wav = report_dir / "mic_test.wav"
        bench.run("microphone_recording", lambda: record_microphone(logger, mic_wav, args.mic, args.input_device))
        bench.run("whisper_mic_file", lambda: test_whisper_file(logger, mic_wav, "en"))

    started_p2p = None
    app_mod = None
    if args.start_p2p:
        def _start_actual_p2p():
            nonlocal started_p2p, app_mod
            app_path = locate_app_script(args.app_script)
            app_mod = import_app_module(app_path)
            if not getattr(app_mod, "FASTAPI_AVAILABLE", False):
                raise RuntimeError("Application does not have FastAPI/Uvicorn available")
            ui = FakeUI(logger)
            started_p2p = app_mod.P2PServer(ui)
            started_p2p.start()
            time.sleep(1.2)
            return {"app_script": str(app_path), "url": started_p2p.url(), "https": getattr(app_mod, "P2P_HTTPS", None)}
        r = bench.run("p2p_server_start_actual_app", _start_actual_p2p)
        if r.status == "PASS":
            args.p2p_url = r.details.get("url", args.p2p_url)

    if args.p2p or args.start_p2p or args.p2p_file:
        if not args.p2p_url:
            bench.skip("p2p_http", "Provide --p2p-url or use --start-p2p")
            bench.skip("p2p_websocket", "Provide --p2p-url or use --start-p2p")
        else:
            bench.run("p2p_http", lambda: test_p2p_http(logger, args.p2p_url))
            bench.run("p2p_websocket", lambda: test_p2p_ws(logger, args.p2p_url))
            if args.p2p_file:
                wav = Path(args.p2p_file).resolve()
                bench.run("p2p_real_ai_pipeline", lambda: test_p2p_audio_pipeline(logger, report_dir, args.p2p_url, wav))

    if started_p2p is not None:
        try:
            started_p2p.stop()
            logger.info("Stopped P2P server started by master test")
        except Exception as exc:
            logger.warn(f"Could not stop test-started P2P server: {exc}")

    bench.write_report({
        "host": {
            "local_ips": get_local_ips(),
            "p2p_url_requested": args.p2p_url,
        },
        "cli": vars(args),
    })

    fails = [r for r in bench.results if r.status == "FAIL"]
    logger.info("=" * 84)
    logger.info(f"MASTER TEST COMPLETE: PASS={sum(r.status=='PASS' for r in bench.results)} FAIL={len(fails)} SKIP={sum(r.status=='SKIP' for r in bench.results)}")
    logger.info(f"Report: {report_dir}")
    if fails:
        logger.error("First failure: " + fails[0].name + " -> " + str(fails[0].error))
        return 2
    return 0


if __name__ == "__main__":
    args = parse_args()
    if args.worker:
        raise SystemExit(worker_main(args))
    raise SystemExit(run_main(args))
