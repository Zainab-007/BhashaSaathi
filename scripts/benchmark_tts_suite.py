from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

# Setup paths
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / 'apps' / 'api'
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import soundfile as sf
import torch

from app.adapters.tts import IndicParlerTTSAdapter
from app.core.config import MODELS_ROOT, STORAGE_ROOT
from app.services.model_manager import manager

BENCHMARK_CASES = [
    {
        "id": "EN_SHORT",
        "name": "English Short Sentence",
        "lang": "eng_Latn",
        "speaker": "classroom_teacher",
        "text": "Hello children, today we are learning about plants."
    },
    {
        "id": "EN_LONG",
        "name": "English Classroom Paragraph",
        "lang": "eng_Latn",
        "speaker": "classroom_teacher",
        "text": "Plants need sunlight, water, and soil to grow into healthy trees. The leaves make food for the plant using sunlight and chlorophyll. Water travels from the roots through the stem all the way to the green leaves."
    },
    {
        "id": "HI_SHORT",
        "name": "Hindi Short Sentence",
        "lang": "hin_Deva",
        "speaker": "classroom_teacher",
        "text": "नमस्ते बच्चों, आज हम पौधों के बारे में सीख रहे हैं।"
    },
    {
        "id": "MR_SHORT",
        "name": "Marathi Short Sentence",
        "lang": "mar_Deva",
        "speaker": "classroom_teacher",
        "text": "नमस्कार मुलांनो, आज आपण वनस्पतींबद्दल शिकत आहोत."
    },
    {
        "id": "SAT_SHORT",
        "name": "Santali Short Sentence",
        "lang": "sat_Olck",
        "speaker": "classroom_teacher",
        "text": "ᱡᱚᱦᱟᱨ ᱜᱤᱫᱽᱨᱟᱹ, ᱛᱮᱦᱮᱧ ᱟᱵᱚ ᱫᱟᱨᱮ ᱵᱟᱵᱚᱛ ᱵᱚᱱ ᱪᱮᱫᱚᱜ-ᱟ ᱾"
    }
]

def run_suite():
    print("=" * 75)
    print("BHASHASAATHI TTS DEEP AUDIT & PERFORMANCE BENCHMARK SUITE")
    print("=" * 75)

    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"Device: {device_name}")
    print(f"PyTorch: {torch.__version__}, CUDA Available: {torch.cuda.is_available()}")
    print(f"Model Root: {MODELS_ROOT}")
    print(f"Initial VRAM Allocated: {torch.cuda.memory_allocated() / (1024**2):.2f} MB\n")

    out_dir = STORAGE_ROOT / 'benchmark_results'
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    # 1. Warm-up
    print("--- STEP 1: Model Warmup ---")
    t0 = time.perf_counter()
    manager.tts.warm()
    warmup_s = time.perf_counter() - t0
    print(f"Model Warmup Time: {warmup_s:.2f} s")
    print(f"Post-Warmup VRAM Allocated: {torch.cuda.memory_allocated() / (1024**2):.2f} MB\n")

    # 2. Run Test Cases
    print("--- STEP 2: Running Benchmark Matrix ---")
    for case in BENCHMARK_CASES:
        cid = case["id"]
        name = case["name"]
        lang = case["lang"]
        speaker = case["speaker"]
        text = case["text"]
        out_wav = out_dir / f"{cid}.wav"
        if out_wav.exists():
            out_wav.unlink()

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()

        t_start = time.perf_counter()
        synth_result = manager.synthesize(text, out_wav, lang, speaker)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        total_time = time.perf_counter() - t_start

        peak_vram = torch.cuda.max_memory_allocated() / (1024**2) if torch.cuda.is_available() else 0.0
        
        # Audio file validation
        with sf.SoundFile(out_wav) as f:
            dur = f.frames / f.samplerate
            valid = f.frames > 0 and f.samplerate == 44100 and f.channels == 1 and out_wav.stat().st_size > 0

        rtf = total_time / dur if dur > 0 else 0

        res = {
            "id": cid,
            "name": name,
            "lang": lang,
            "text_len": len(text),
            "audio_dur": dur,
            "gen_time": total_time,
            "rtf": rtf,
            "peak_vram_mb": peak_vram,
            "file_size": out_wav.stat().st_size,
            "valid": valid
        }
        results.append(res)
        print(f"[{cid}] {name}: Audio={dur:.2f}s | Gen={total_time:.2f}s | RTF={rtf:.2f}x | Peak VRAM={peak_vram:.1f} MB | Valid={valid}")

    # 3. Cache Test
    print("\n--- STEP 3: Testing Cache Hit Performance ---")
    cached_case = BENCHMARK_CASES[0]
    out_cached_wav = out_dir / "EN_SHORT_CACHED.wav"
    t_start = time.perf_counter()
    manager.synthesize(cached_case["text"], out_cached_wav, cached_case["lang"], cached_case["speaker"])
    cache_time_ms = (time.perf_counter() - t_start) * 1000
    print(f"Cache Hit Synthesis Time: {cache_time_ms:.2f} ms")

    # 4. Summary Table
    print("\n" + "=" * 75)
    print(f"{'ID':<10} | {'Language':<10} | {'Audio (s)':<10} | {'Gen (s)':<10} | {'RTF':<8} | {'Peak VRAM':<10} | {'Status'}")
    print("-" * 75)
    for r in results:
        status = "READY" if r["valid"] else "FAILED"
        print(f"{r['id']:<10} | {r['lang']:<10} | {r['audio_dur']:<10.2f} | {r['gen_time']:<10.2f} | {r['rtf']:<8.2f} | {r['peak_vram_mb']:<7.1f} MB | {status}")
    print("=" * 75)

if __name__ == '__main__':
    run_suite()
