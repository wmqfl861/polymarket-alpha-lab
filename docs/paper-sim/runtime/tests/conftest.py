"""Import the handoff scripts next to this suite."""

from pathlib import Path
import sys


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RUNTIME_ROOT / "scripts"), str(RUNTIME_ROOT / "legacy")]
