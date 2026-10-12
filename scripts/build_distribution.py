#!/usr/bin/env python3
"""Build locked frontend assets, then an allowlisted sdist and bundled wheel."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument(
        "--skip-install", action="store_true", help="Reuse existing web/node_modules"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = args.output.resolve()
    for tool in ("npm", "uv"):
        if not shutil.which(tool):
            parser.error(
                f"{tool} is required to build a distribution from the checkout"
            )
    if not args.skip_install:
        subprocess.run(["npm", "ci"], cwd=root / "web", check=True)
    subprocess.run(["npm", "run", "build"], cwd=root / "web", check=True)
    subprocess.run(
        ["uv", "build", "--sdist", "--wheel", "--out-dir", str(output)],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    main()
