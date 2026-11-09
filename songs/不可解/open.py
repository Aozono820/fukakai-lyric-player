"""Open the 不可解 lyric window directly."""

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import run_song


def main():
    parser = argparse.ArgumentParser(description="Open 不可解")
    parser.add_argument(
        "--reannotate",
        action="store_true",
        help="rebuild the annotation cache from the SRT file",
    )
    args = parser.parse_args()
    run_song(Path(__file__).resolve().parent, reannotate=args.reannotate)


if __name__ == "__main__":
    main()
