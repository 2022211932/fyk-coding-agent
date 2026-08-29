from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a clean FYK Coding Agent demo workspace")
    parser.add_argument("destination", nargs="?", default="demo-workspace")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "examples" / "slugify-bug"
    destination = (project_root / args.destination).resolve()
    destination.relative_to(project_root)
    if destination.exists():
        raise SystemExit(
            f"Destination already exists: {destination}\n"
            "Remove it deliberately before preparing another clean demo."
        )
    shutil.copytree(source, destination)
    print(f"Demo workspace prepared: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

