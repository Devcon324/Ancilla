"""Console-script entrypoint for `uv run jetson-assistant`."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import run


def main() -> None:
    run()
