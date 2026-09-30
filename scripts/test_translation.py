from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as: python scripts/test_translation.py from the repository root.
ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
sys.path.insert(0, str(API))

from app.core.config import MODELS_ROOT, settings  # noqa: E402
from app.adapters.translation import IndicTrans2Adapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct local IndicTrans2 smoke test")
    parser.add_argument("--text", default="पौधों को जीवित रहने और बढ़ने के लिए पानी की आवश्यकता होती है।")
    parser.add_argument("--src", default="hin_Deva")
    parser.add_argument("--tgt", default="mar_Deva")
    args = parser.parse_args()

    adapter = IndicTrans2Adapter(MODELS_ROOT, settings.cpu_only)
    print("Model root:", MODELS_ROOT)
    print("Health:", adapter.health())
    print(f"Translating {args.src} -> {args.tgt} ...")
    print("Source:", args.text)
    try:
        result = adapter.translate(args.text, args.src, args.tgt)
    except Exception as exc:
        print("\nTRANSLATION FAILED")
        print(type(exc).__name__ + ":", exc)
        return 1
    print("Target:", result)
    print("\nTRANSLATION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
