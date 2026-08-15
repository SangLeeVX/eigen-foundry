from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "check_git_history.py"


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class GitHistorySecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="foundry-history-test-")
        self.repository = Path(self.temporary.name)
        git(self.repository, "init", "--quiet")
        git(self.repository, "config", "user.email", "test@example.invalid")
        git(self.repository, "config", "user.name", "Foundry Test")
        self.allowlist = self.repository / "allowlist.json"
        self.allowlist.write_text('{"version": 1, "allow": []}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run_scanner(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCANNER),
                "--repository",
                str(self.repository),
                "--allowlist",
                str(self.allowlist),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_deleted_historical_secret_is_detected_without_echoing_value(self) -> None:
        synthetic_secret = "sk-" + "A" * 24
        assignment_name = "API_" + "KEY"
        historical = self.repository / "historical.env"
        historical.write_text(f"{assignment_name}={synthetic_secret}\n", encoding="utf-8")
        git(self.repository, "add", "historical.env")
        git(self.repository, "commit", "--quiet", "-m", "add synthetic historical fixture")
        historical.unlink()
        git(self.repository, "add", "-u")
        git(self.repository, "commit", "--quiet", "-m", "delete synthetic historical fixture")

        result = self._run_scanner()

        self.assertEqual(result.returncode, 1)
        self.assertIn("provider secret key", result.stdout)
        self.assertNotIn(synthetic_secret, result.stdout)
        self.assertNotIn(synthetic_secret, result.stderr)

    def test_exact_blob_and_rule_allowlist_is_reviewable(self) -> None:
        synthetic_secret = "sk-" + "B" * 24
        historical = self.repository / "false-positive.txt"
        historical.write_text(synthetic_secret + "\n", encoding="utf-8")
        git(self.repository, "add", historical.name)
        git(self.repository, "commit", "--quiet", "-m", "add synthetic false positive")
        blob_sha = git(self.repository, "rev-parse", "HEAD:false-positive.txt")
        self.allowlist.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allow": [
                        {
                            "blob_sha": blob_sha,
                            "rule": "provider secret key",
                            "reason": "Synthetic regression fixture only.",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = self._run_scanner()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
