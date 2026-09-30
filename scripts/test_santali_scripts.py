import sys
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
    abs_wav = np.abs(wav)
    peak = float(np.max(abs_wav))
    rms = float(np.sqrt(np.mean(wav**2)))
    near_zero_pct = float(np.mean(abs_wav < 1e-4) * 100.0)
    return {
        "duration_seconds": round(float(len(wav) / sr), 3),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "near_zero_pct": round(near_zero_pct, 2),
        "is_audible": bool(peak > 0.05 and rms > 0.005 and near_zero_pct < 90.0),
    }

def main():
    adapter = IndicParlerTTSAdapter(model_root=API_ROOT / "models")
    adapter.warm()
    model, tokenizer, description_tokenizer, feature, device = adapter._load()
    sampling_rate = int(feature.sampling_rate)
    frame_rate = int(getattr(model.audio_encoder.config, "frame_rate", 86))

    # Test Santali in Ol Chiki, Bengali script, and Roman transliteration
    # Sentence: "Plants grow with water and sunlight"
    # Ol Chiki: ᱫᱟᱜ ᱟᱨ ᱥᱤᱛᱩᱝ ᱛᱮ ᱫᱟᱨᱮ ᱦᱟᱨᱟᱜᱼᱟ ᱾
    # Bengali script transliteration of Santali: দাগ আর সিতুং তে দারে হারাগা।
    # Roman: Dag ar situng te dare haraga.
    
    santali_cases = [
        # Ol Chiki
        ("Ol Chiki", "ᱥᱟᱱᱛᱟᱲᱤ ᱛᱮ ᱯᱟᱲᱦᱟᱣ ᱢᱮ ᱾"),
        ("Ol Chiki", "ᱦᱟᱹᱨᱭᱟᱹᱲ ᱥᱟᱠᱟᱢ ᱡᱚᱢᱟᱜ ᱮ ᱛᱮᱭᱟᱨᱟ ᱾"),
        ("Ol Chiki", "ᱫᱟᱨᱮ ᱫᱟᱜ ᱧᱟᱢᱟ ᱾"),
        ("Ol Chiki", "ᱪᱮᱬᱮ ᱠᱚ ᱥᱮᱨᱮᱧᱟ ᱾"),
        ("Ol Chiki", "ᱟᱞᱮ ᱟᱹᱛᱩ ᱨᱮ ᱟᱹᱰᱤ ᱱᱟᱯᱟᱭ ᱫᱟᱨᱮ ᱢᱮᱱᱟᱜᱼᱟ ᱾"),
        # Bengali script transliteration
        ("Bengali Script", "দাগ আর সিতুং তে দারে হারাগা।"),
        ("Bengali Script", "সানতালি তে পাড়হাও মে।"),
        # Roman transliteration
        ("Roman", "Dag ar situng te dare haraga."),
        ("Roman", "Santali te padhao me."),
        ("Roman", "Hariyad sakam jomag e teyara."),
    ]

    speakers = [
        ("Arjun", "Arjun speaks clearly with a close recording."),
        ("Aditi", "Aditi speaks with a clear voice in a close environment with excellent recording quality."),
        ("Sunita", "Sunita speaks clearly in a close-sounding environment with high quality."),
        ("Rani", "Rani speaks with a high-pitched, clear voice in a close recording."),
    ]

    print("=" * 80)
    print("SANTALI MULTI-SCRIPT & MULTI-SPEAKER AUDIT")
    print("=" * 80)

    for script, text in santali_cases:
        print(f"\n[{script}] Text: '{text}'")
        for spk, desc in speakers:
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
            print(f"  Spk: {spk:<6} | Peak: {stats['peak']:<8} | RMS: {stats['rms']:<8} | Audible: {stats['is_audible']}")

if __name__ == "__main__":
    main()
