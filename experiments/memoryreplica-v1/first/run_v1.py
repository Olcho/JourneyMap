"""Portable launcher; also works with Python -I -B without PYTHONPATH."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memoryreplica_v1.cli import main

if __name__ == "__main__":
    main()
