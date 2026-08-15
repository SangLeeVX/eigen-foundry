#!/usr/bin/env python3
"""Scan every blob reachable from Git refs without printing matched values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import BinaryIO


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.check_secrets import (  # noqa: E402
    ASSIGNMENT,
    MAX_BYTES,
    PATTERNS,
    is_placeholder,
)


DEFAULT_ALLOWLIST = Path(__file__).with_name("history_secret_allowlist.json")
OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$")
ALLOWED_ENTRY_KEYS = {"blob_sha", "reason", "rule"}


def _git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {message}")
    return result.stdout


def _load_allowlist(path: Path) -> set[tuple[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if set(raw) != {"version", "allow"} or raw["version"] != 1:
        raise ValueError("allowlist must contain only version=1 and allow")
    if not isinstance(raw["allow"], list):
        raise ValueError("allowlist allow value must be a list")

    allowed: set[tuple[str, str]] = set()
    for entry in raw["allow"]:
        if not isinstance(entry, dict) or set(entry) != ALLOWED_ENTRY_KEYS:
            raise ValueError("allowlist entries require exactly blob_sha, rule, and reason")
        blob_sha = entry["blob_sha"]
        rule = entry["rule"]
        reason = entry["reason"]
        if not isinstance(blob_sha, str) or not OBJECT_ID.fullmatch(blob_sha):
            raise ValueError("allowlist blob_sha must be a full Git object ID")
        if not isinstance(rule, str) or not rule:
            raise ValueError("allowlist rule must be non-empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("allowlist reason must be non-empty")
        allowed.add((blob_sha, rule))
    return allowed


def _reachable_objects(repository: Path) -> tuple[list[str], dict[str, str]]:
    output = _git(repository, "rev-list", "--objects", "--all")
    object_ids: list[str] = []
    paths: dict[str, str] = {}
    for raw_line in output.splitlines():
        object_id, separator, raw_path = raw_line.partition(b" ")
        decoded_id = object_id.decode("ascii")
        object_ids.append(decoded_id)
        if separator:
            paths.setdefault(decoded_id, raw_path.decode("utf-8", errors="replace"))
    return object_ids, paths


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise RuntimeError("unexpected end of git cat-file output")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _blobs(repository: Path, object_ids: list[str]):
    process = subprocess.Popen(
        ["git", "-C", str(repository), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdin.write("".join(f"{item}\n" for item in object_ids).encode("ascii"))
    process.stdin.close()

    for expected_id in object_ids:
        header = process.stdout.readline().decode("ascii").strip().split()
        if len(header) != 3 or header[0] != expected_id:
            raise RuntimeError("unexpected git cat-file header")
        object_type, size = header[1], int(header[2])
        content = _read_exact(process.stdout, size)
        if process.stdout.read(1) != b"\n":
            raise RuntimeError("invalid git cat-file record terminator")
        if object_type == "blob":
            yield expected_id, content

    stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"git cat-file --batch failed: {stderr}")


def _scan_blob(content: bytes) -> list[tuple[int, str]]:
    if len(content) > MAX_BYTES or b"\0" in content:
        return []
    text = content.decode("utf-8", errors="replace")
    findings: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((number, name))
        assignment = ASSIGNMENT.search(line)
        if assignment and not is_placeholder(assignment.group(1)):
            findings.append((number, "credential assignment"))
    return findings


def scan_history(repository: Path, allowlist: Path) -> list[tuple[str, str, int, str]]:
    allowed = _load_allowlist(allowlist)
    object_ids, paths = _reachable_objects(repository)
    findings: list[tuple[str, str, int, str]] = []
    for blob_sha, content in _blobs(repository, object_ids):
        for line, rule in _scan_blob(content):
            if (blob_sha, rule) not in allowed:
                findings.append((blob_sha, paths.get(blob_sha, "<unknown>"), line, rule))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    arguments = parser.parse_args()

    try:
        findings = scan_history(arguments.repository.resolve(), arguments.allowlist.resolve())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"History secret scan could not complete: {error}", file=sys.stderr)
        return 2

    if findings:
        print("Potential secrets found in reachable Git history; matched values are suppressed:")
        for blob_sha, path, line, rule in findings:
            print(f"  blob {blob_sha} path {json.dumps(path)} line {line}: {rule}")
        return 1

    print("History secret scan passed: all reachable Git blobs were checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
