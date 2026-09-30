from pathlib import Path
import sys

# Make the monorepo root importable so Layer 2 remains a separate pure package.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
