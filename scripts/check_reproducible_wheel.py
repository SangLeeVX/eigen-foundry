"""Prove that two clean, offline builds produce byte-identical wheels."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATE_EPOCH = "1704067200"
IGNORED_NAMES = (
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "*.egg-info",
    "*.pyc",
)


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _build(source: Path, destination: Path) -> Path:
    destination.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            "TZ": "UTC",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            ".",
            "--wheel-dir",
            str(destination),
        ],
        cwd=source,
        env=environment,
        check=True,
    )
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {len(wheels)}")
    return wheels[0]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="foundry-reproducible-") as temporary:
        workspace = Path(temporary)
        source_one = workspace / "source-one"
        source_two = workspace / "source-two"
        ignore = shutil.ignore_patterns(*IGNORED_NAMES)
        shutil.copytree(ROOT, source_one, ignore=ignore)
        shutil.copytree(ROOT, source_two, ignore=ignore)

        wheel_one = _build(source_one, workspace / "wheel-one")
        wheel_two = _build(source_two, workspace / "wheel-two")
        digest_one = _digest(wheel_one)
        digest_two = _digest(wheel_two)

        if wheel_one.name != wheel_two.name or digest_one != digest_two:
            print(
                "wheel reproducibility check failed: "
                f"{wheel_one.name} sha256:{digest_one} != "
                f"{wheel_two.name} sha256:{digest_two}",
                file=sys.stderr,
            )
            return 1

        print(f"reproducible wheel: {wheel_one.name} sha256:{digest_one}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
