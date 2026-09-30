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

import numpy as np
import soundfile as sf
import torch

from app.adapters.tts import IndicParlerTTSAdapter, LANGUAGE_DESCRIPTIONS, LANGUAGE_SPEAKERS
from app.core.config import MODELS_ROOT

def run_test_a():
    print("=" * 60)
    print("TEST A: Direct Parler CLI / Script Benchmark")
    print("=" * 60)

    text = "Hello children, today we are learning about plants."
    language = "eng_Latn"
    speaker = "classroom_teacher"

    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"Device: {device_name}")
    print(f"Initial VRAM Allocated: {torch.cuda.memory_allocated() / (1024**2):.2f} MB")
    print(f"Initial VRAM Reserved: {torch.cuda.memory_reserved() / (1024**2):.2f} MB")

    adapter = IndicParlerTTSAdapter(MODELS_ROOT, cpu_only=False)
    
    # 1. Model loading
    print("\n--- 1. Loading Model ---")
    t0 = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    model, tokenizer, description_tokenizer, feature, device = adapter._load()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    load_time = time.perf_counter() - t0
    print(f"Model Load Time: {load_time:.2f} s")
    print(f"Post-Load VRAM Allocated: {torch.cuda.memory_allocated() / (1024**2):.2f} MB")
    print(f"Post-Load VRAM Reserved: {torch.cuda.memory_reserved() / (1024**2):.2f} MB")

    # 2. Text & Description Encoding
    print("\n--- 2. Text Encoding ---")
    t0 = time.perf_counter()
    description_ids, description_mask = adapter._description_inputs(language, speaker, device, description_tokenizer)
    prompt_inputs = tokenizer(text, return_tensors='pt').to(device)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    encoding_time = time.perf_counter() - t0
    print(f"Encoding Time: {encoding_time * 1000:.2f} ms")
    print(f"Description shape: {description_ids.shape}, mask shape: {description_mask.shape}")
    print(f"Prompt shape: {prompt_inputs.input_ids.shape}, mask shape: {prompt_inputs.attention_mask.shape}")

    # 3. Generation
    print("\n--- 3. Generation ---")
    frame_rate = int(getattr(model.audio_encoder.config, 'frame_rate', 86))
    max_tokens = adapter._estimate_max_new_tokens(text, frame_rate, adapter._max_output_seconds)
    print(f"Frame rate: {frame_rate}, max_new_tokens: {max_tokens}")
    
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    
    with torch.inference_mode():
        generation = model.generate(
            input_ids=description_ids,
            attention_mask=description_mask,
            prompt_input_ids=prompt_inputs.input_ids,
            prompt_attention_mask=prompt_inputs.attention_mask,
            do_sample=False,
            use_cache=True,
            max_new_tokens=max_tokens,
            return_dict_in_generate=True,
        )
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    generation_time = time.perf_counter() - t0
    print(f"Generation Time: {generation_time:.2f} s")
    print(f"Peak VRAM during generation: {torch.cuda.max_memory_allocated() / (1024**2):.2f} MB")

    # 4. Waveform conversion
    print("\n--- 4. Waveform Conversion ---")
    t0 = time.perf_counter()
    audio = generation.sequences[0]
    if hasattr(generation, 'audios_length'):
        audio = audio[: int(generation.audios_length[0])]
    array = audio.to(torch.float32).detach().cpu().numpy().reshape(-1)
    conversion_time = time.perf_counter() - t0
    sampling_rate = int(feature.sampling_rate)
    duration = float(len(array) / sampling_rate)
    rtf = generation_time / duration if duration > 0 else 0
    print(f"Conversion Time: {conversion_time * 1000:.2f} ms")
    print(f"Audio Samples: {len(array)}, Sampling Rate: {sampling_rate} Hz, Duration: {duration:.2f} s")
    print(f"Real-Time Factor (RTF): {rtf:.2f}x")

    # 5. WAV write & validation
    print("\n--- 5. File Write & Validation ---")
    out_dir = REPO_ROOT / 'data' / 'storage' / 'tts_test'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'test_a_direct.wav'
    t0 = time.perf_counter()
    sf.write(out_path, array, sampling_rate, format='WAV', subtype='PCM_16')
    write_time = time.perf_counter() - t0
    print(f"File Write Time: {write_time * 1000:.2f} ms")
    print(f"WAV Path: {out_path}")
    print(f"File Size: {out_path.stat().st_size} bytes")

    # Validation reopening file
    with sf.SoundFile(out_path) as f:
        print(f"Validation: Channels={f.channels}, Samplerate={f.samplerate}, Frames={f.frames}, Format={f.format}, Subtype={f.subtype}")
        assert f.frames > 0, "Empty audio frames"
        assert f.samplerate == sampling_rate, "Sample rate mismatch"
        assert f.channels == 1, "Expected mono"

    total_time = load_time + encoding_time + generation_time + conversion_time + write_time
    print(f"\nTotal Pipeline Time (Cold, with load): {total_time:.2f} s")
    print(f"Steady-State Synthesis Time (Warm): {encoding_time + generation_time + conversion_time + write_time:.2f} s")
    print("TEST A COMPLETED SUCCESSFULLY.")

if __name__ == '__main__':
    run_test_a()
