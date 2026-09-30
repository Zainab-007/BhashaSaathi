from __future__ import annotations

import sys
import time
from pathlib import Path

# Setup paths
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / 'apps' / 'api'
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import torch

def test_compile():
    print("Testing torch.compile capability on Windows RTX 2050...")
    if not hasattr(torch, 'compile'):
        print("torch.compile is not available in this PyTorch build.")
        return
    print("PyTorch version:", torch.__version__)
    print("CUDA available:", torch.cuda.is_available())
    # PyTorch on Windows often lacks Triton compiler support for torch.compile
    try:
        @torch.compile
        def dummy_fn(x):
            return x * 2 + 1

        x = torch.randn(10, device='cuda' if torch.cuda.is_available() else 'cpu')
        out = dummy_fn(x)
        print("torch.compile small function test passed:", out.shape)
    except Exception as exc:
        print("torch.compile failed on this system:", exc)

if __name__ == '__main__':
    test_compile()
