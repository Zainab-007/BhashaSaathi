import io
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import numpy as np
import soundfile as sf
import torch
from app.adapters.tts import IndicParlerTTSAdapter

def analyze_waveform(wav: np.ndarray, sr: int) -> dict:
    if wav.size == 0:
        return {"error": "empty waveform"}
    abs_wav = np.abs(wav)
    peak = float(np.max(abs_wav))
    rms = float(np.sqrt(np.mean(wav**2)))
    mean_abs = float(np.mean(abs_wav))
    near_zero_pct = float(np.mean(abs_wav < 1e-4) * 100.0)
    duration = float(len(wav) / sr)
    return {
        "duration_seconds": round(duration, 3),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "near_zero_pct": round(near_zero_pct, 2),
        "is_audible": bool(peak > 0.05 and rms > 0.005 and near_zero_pct < 90.0),
    }

def main():
    print("=" * 80)
    print("HINDI / MARATHI / SANTALI DETAILED SPEAKER & PROMPT GRID SEARCH")
    print("=" * 80)

    adapter = IndicParlerTTSAdapter(model_root=API_ROOT / "models")
    adapter.warm()
    model, tokenizer, description_tokenizer, feature, device = adapter._load()
    sampling_rate = int(feature.sampling_rate)
    frame_rate = int(getattr(model.audio_encoder.config, "frame_rate", 86))

    out_dir = REPO_ROOT / "scratch" / "tts_grid"
    out_dir.mkdir(parents=True, exist_ok=True)

    hindi_text = "बच्चों, आज हम पौधों के बारे में सीख रहे हैं।"
    
    # Grid of Hindi descriptions and speakers
    hindi_candidates = [
        # Official recommended speakers & descriptions from README
        ("Divya", "Divya's voice is monotone yet slightly fast in delivery, with a very close recording that almost has no background noise."),
        ("Divya", "Divya speaks with a clear, high-quality voice in a close environment."),
        ("Divya", "Divya speaks clearly in a high quality recording."),
        ("Rohit", "Rohit's voice is clear with a close-sounding recording and excellent quality."),
        ("Rohit", "Rohit speaks at a moderate pace with clear pronunciation and no background noise."),
        ("Aman", "Aman speaks in a clear voice with a close recording and high quality."),
        ("Rani", "Rani speaks with a high-pitched, clear voice in a close recording."),
        # General descriptions
        ("female", "A female speaker delivers a slightly expressive speech with a moderate speed and pitch. The recording is of very high quality, with the speaker's voice sounding clear and very close up."),
        ("male", "A male speaker delivers a clear and natural speech with very high quality."),
        # Cross-language clear speakers (e.g. Sita, Sunita, Mary, Aditi)
        ("Sita", "Sita speaks at a fast pace with a slightly low-pitched voice, captured clearly in a close-sounding environment with excellent recording quality."),
        ("Sunita", "Sunita speaks with a high pitch in a close environment. Her voice is clear, with slight dynamic changes, and the recording is of excellent quality."),
        ("Aditi", "Aditi speaks with a slightly higher pitch in a close-sounding environment. Her voice is clear, with subtle emotional depth and a normal pace, all captured in high-quality recording."),
        ("Karan", "Karan's high-pitched, engaging voice is captured in a clear, close-sounding recording."),
    ]

    print("\n--- TESTING HINDI PROMPT: " + hindi_text + " ---")
    for spk, desc in hindi_candidates:
        desc_inputs = description_tokenizer(desc, return_tensors="pt").to(device)
        prompt_inputs = tokenizer(hindi_text, return_tensors="pt").to(device)
        decoder_mask = torch.ones((1, 1), device=device, dtype=prompt_inputs.attention_mask.dtype)
        
        t0 = time.perf_counter()
        with torch.inference_mode():
            gen = model.generate(
                input_ids=desc_inputs.input_ids,
                attention_mask=desc_inputs.attention_mask,
                prompt_input_ids=prompt_inputs.input_ids,
                prompt_attention_mask=prompt_inputs.attention_mask,
                decoder_attention_mask=decoder_mask,
                do_sample=False,
                use_cache=True,
                max_new_tokens=adapter._estimate_max_new_tokens(hindi_text, frame_rate, 30.0),
                return_dict_in_generate=True,
            )
            audio = gen.sequences[0]
            if hasattr(gen, "audios_length"):
                audio = audio[: int(gen.audios_length[0])]
            arr = audio.to(torch.float32).detach().cpu().numpy().reshape(-1)
        dur_s = time.perf_counter() - t0
        stats = analyze_waveform(arr, sampling_rate)
        print(f"Speaker: {spk:<8} | Peak: {stats['peak']:<8} | RMS: {stats['rms']:<8} | Audible: {stats['is_audible']} | Desc: {desc[:50]}...")
        if stats['is_audible']:
            sf.write(str(out_dir / f"hindi_audible_{spk}.wav"), arr, sampling_rate, format="WAV", subtype="PCM_16")

    # Now let's test Marathi with Sunita, Radha, Varun, Isha, Sanjay
    marathi_text = "मुलांनो, आज आपण वनस्पतींबद्दल शिकत आहोत."
    marathi_candidates = [
        ("Sunita", "Sunita speaks with a high pitch in a close environment. Her voice is clear, with slight dynamic changes, and the recording is of excellent quality."),
        ("Sunita", "Sunita speaks clearly in a close-sounding environment with high quality."),
        ("Radha", "Radha speaks clearly in a close environment with excellent recording quality."),
        ("Isha", "Isha speaks clearly in a high quality recording."),
        ("Varun", "Varun speaks in a clear voice with high quality."),
        ("Sanjay", "Sanjay speaks at a moderate pace with a close recording and clear audio."),
    ]
    print("\n--- TESTING MARATHI PROMPT: " + marathi_text + " ---")
    for spk, desc in marathi_candidates:
        desc_inputs = description_tokenizer(desc, return_tensors="pt").to(device)
        prompt_inputs = tokenizer(marathi_text, return_tensors="pt").to(device)
        decoder_mask = torch.ones((1, 1), device=device, dtype=prompt_inputs.attention_mask.dtype)
        
        with torch.inference_mode():
            gen = model.generate(
                input_ids=desc_inputs.input_ids,
                attention_mask=desc_inputs.attention_mask,
                prompt_input_ids=prompt_inputs.input_ids,
                prompt_attention_mask=prompt_inputs.attention_mask,
                decoder_attention_mask=decoder_mask,
                do_sample=False,
                use_cache=True,
                max_new_tokens=adapter._estimate_max_new_tokens(marathi_text, frame_rate, 30.0),
                return_dict_in_generate=True,
            )
            audio = gen.sequences[0]
            if hasattr(gen, "audios_length"):
                audio = audio[: int(gen.audios_length[0])]
            arr = audio.to(torch.float32).detach().cpu().numpy().reshape(-1)
        stats = analyze_waveform(arr, sampling_rate)
        print(f"Speaker: {spk:<8} | Peak: {stats['peak']:<8} | RMS: {stats['rms']:<8} | Audible: {stats['is_audible']} | Desc: {desc[:50]}...")
        if stats['is_audible']:
            sf.write(str(out_dir / f"marathi_audible_{spk}.wav"), arr, sampling_rate, format="WAV", subtype="PCM_16")

    # Now let's test Santali with various Ol Chiki sentences and speakers (Arjun, Aditi, Rashmi, Tapan)
    # Let's also check known repository Santali sentences
    santali_sentences = [
        "ᱥᱟᱱᱛᱟᱲᱤ ᱛᱮ ᱯᱟᱲᱦᱟᱣ ᱢᱮ ᱾",
        "ᱫᱟᱜ ᱟᱨ ᱥᱤᱛᱩᱝ ᱛᱮ ᱫᱟᱨᱮ ᱦᱟᱨᱟᱜᱼᱟ ᱾",  # "Plants grow with water and sunlight"
        "ᱦᱟᱹᱨᱭᱟᱹᱲ ᱥᱟᱠᱟᱢ ᱡᱚᱢᱟᱜ ᱮ ᱛᱮᱭᱟᱨᱟ ᱾",   # "Green leaves make food"
    ]
    santali_speakers = [
        ("Arjun", "Arjun speaks clearly with a close recording."),
        ("Arjun", "Arjun speaks in a clear voice with excellent quality and no background noise."),
        ("Aditi", "Aditi speaks with a clear voice in a close environment with excellent recording quality."),
        ("Rashmi", "Rashmi speaks clearly in a high quality recording."),
    ]
    print("\n--- TESTING SANTALI OL CHIKI SENTENCES ---")
    for text in santali_sentences:
        print(f"\nSantali Text: {text}")
        for spk, desc in santali_speakers:
            desc_inputs = description_tokenizer(desc, return_tensors="pt").to(device)
            prompt_inputs = tokenizer(text, return_tensors="pt").to(device)
            decoder_mask = torch.ones((1, 1), device=device, dtype=prompt_inputs.attention_mask.dtype)
            
            with torch.inference_mode():
                gen = model.generate(
                    input_ids=desc_inputs.input_ids,
                    attention_mask=desc_inputs.attention_mask,
                    prompt_input_ids=prompt_inputs.input_ids,
                    prompt_attention_mask=prompt_inputs.attention_mask,
                    decoder_attention_mask=decoder_mask,
                    do_sample=False,
                    use_cache=True,
                    max_new_tokens=adapter._estimate_max_new_tokens(text, frame_rate, 30.0),
                    return_dict_in_generate=True,
                )
                audio = gen.sequences[0]
                if hasattr(gen, "audios_length"):
                    audio = audio[: int(gen.audios_length[0])]
                arr = audio.to(torch.float32).detach().cpu().numpy().reshape(-1)
            stats = analyze_waveform(arr, sampling_rate)
            print(f"  Speaker: {spk:<8} | Peak: {stats['peak']:<8} | RMS: {stats['rms']:<8} | Audible: {stats['is_audible']} | Desc: {desc[:40]}...")
            if stats['is_audible']:
                sf.write(str(out_dir / f"santali_audible_{spk}_{abs(hash(text)) % 1000}.wav"), arr, sampling_rate, format="WAV", subtype="PCM_16")

if __name__ == "__main__":
    main()
