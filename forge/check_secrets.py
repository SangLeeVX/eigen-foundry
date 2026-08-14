#!/usr/bin/env python3
"""Fail when likely credentials are present in repository text files.

This is a dependency-free baseline scanner, not a replacement for provider-side
rotation or a full repository-history scan.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".venv",
    ".forge-run",
    "__pycache__",
    "build",
    "dist",
}
MAX_BYTES = 1_000_000

PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("live Stripe key", re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b")),
    ("provider secret key", re.compile(r"\bsk-[0-9A-Za-z_-]{20,}\b")),
)

ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|secret)\b\s*[:=]\s*[\"']?([^\s\"'#]{12,})"
)

PLACEHOLDER_MARKERS = (
    "changeme",
    "dummy",
    "example",
    "fake",
    "placeholder",
    "redacted",
    "replace",
    "test-only",
    "tbd",
    "${",
    "{{",
    "<",
)


def candidate_files() -> list[Path]:
    """Return tracked files when possible, otherwise bounded workspace files."""

    if (ROOT / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
        return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]

    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in SKIP_PARTS for part in path.parts)
    ]


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def scan(path: Path) -> list[tuple[int, str]]:
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        raw = path.read_bytes()
    except (OSError, ValueError):
        return []
    if b"\0" in raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    findings: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((number, name))
        assignment = ASSIGNMENT.search(line)
        if assignment and not is_placeholder(assignment.group(1)):
            findings.append((number, "credential assignment"))
    return findings


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in candidate_files():
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        for line, rule in scan(path):
            findings.append((path.relative_to(ROOT), line, rule))

    if findings:
        print("Potential secrets found; remove them and rotate any real credential:")
        for path, line, rule in findings:
            print(f"  {path}:{line}: {rule}")
        return 1

    print("Secret scan passed: no likely credential found in repository files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
