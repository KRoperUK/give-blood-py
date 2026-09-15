"""Tests for the PII/secret pre-commit guard.

An untested guard is worse than no guard: it produces confidence without
protection. These tests assert both halves — that real values are caught, and
that the reserved substitutes are not, since a scanner that cries wolf gets
disabled.

Every "bad" value below is **invented**, not copied from a real capture. That
matters: this file is the one place in the repository where PII-shaped strings are
allowed to exist, so it must not become the one place real PII hides.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_guard() -> ModuleType:
    """Import ``scripts/check_pii.py``, which isn't an installed module."""
    path = REPO_ROOT / "scripts" / "check_pii.py"
    spec = importlib.util.spec_from_file_location("check_pii", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pii"] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


class TestCatchesRealValues:
    """Anything that could identify a real person or authenticate as them."""

    @pytest.mark.parametrize(
        "line",
        [
            'email = "real.person@gmail.com"',
            "contact: someone@nhs.net",
            '"postcode": "AB12 3CD"',
            '"postcode": "EH991ZZ"',
            'phone = "07912345678"',
            'phone = "+447912345678"',
            '"donorID": "D1234567"',
            '"donationId": "G123456789012Z"',
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
            'password = "hunter2hunter2"',
            'API_KEY = "9f8e7d6c5b4a39281706fedcba098765"',
            'refresh_token: "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA=="',
        ],
    )
    def test_flags_the_line(self, line: str) -> None:
        assert guard.scan_line(line), f"guard missed: {line}"

    def test_flags_a_real_looking_jwt(self) -> None:
        token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJleHAiOjExMTExMTExMTF9.c2lnbmF0dXJlLWhlcmU"
        findings = guard.scan_line(f'accessToken = "{token}"')
        assert any("JWT" in finding for finding in findings)

    def test_reports_masked_values_not_the_originals(self) -> None:
        """The guard's own output must not become a second copy of the leak."""
        findings = guard.scan_line('email = "very.identifying.person@gmail.com"')
        assert findings
        assert not any("very.identifying.person@gmail.com" in finding for finding in findings)


class TestAllowsReservedSubstitutes:
    """False positives get scanners switched off, so these must stay quiet."""

    @pytest.mark.parametrize(
        "line",
        [
            'email = "donor@example.invalid"',
            'email = "someone@example.com"',
            'host = "test.invalid"',
            '"postcode": "SW1A 1AA"',
            '"postcode": "ZZ99 3WZ"',
            'phone = "07700900000"',
            '"donorID": "D0000000"',
            '"donationId": "G000000000000X"',
            'password = "REPLACE_ME"',
            'password = "your-password-here"',
            'api_key = "<your api key>"',
            "password: !secret nhs_give_blood_password",
            'access_token = "synthetic.access.token"',
            'refresh_token = "synthetic-refresh-token"',
            'token = "not-a-real-password"',
        ],
    )
    def test_stays_quiet(self, line: str) -> None:
        assert guard.scan_line(line) == [], f"false positive on: {line}"

    def test_allows_the_apps_public_client_key(self) -> None:
        """Extracted from a public APK; identifies the app, not a user."""
        line = 'APP_API_KEY = "b0046936-5a05-439e-8a89-5beab70829b7"'
        assert guard.scan_line(line) == []

    def test_honours_the_inline_allow_marker(self) -> None:
        assert guard.scan_line('email = "real@gmail.com"  # pii-allow') == []


class TestFileHandling:
    """Path filtering."""

    @pytest.mark.parametrize(
        "name",
        [".secrets.baseline", "poetry.lock", "logo.png", "app.apk", "libthing.so"],
    )
    def test_skips_paths_that_are_not_worth_scanning(self, name: str) -> None:
        assert guard.should_skip(Path(name))

    @pytest.mark.parametrize("part", [".git", ".venv", "__pycache__", "node_modules"])
    def test_skips_generated_directories(self, part: str) -> None:
        assert guard.should_skip(Path(part) / "thing.py")

    def test_scans_ordinary_source_files(self) -> None:
        assert not guard.should_skip(Path("nhs_give_blood/client.py"))

    def test_reports_line_numbers(self, tmp_path: Path) -> None:
        target = tmp_path / "leak.py"
        target.write_text('clean = 1\nemail = "person@gmail.com"\n')
        assert [number for number, _ in guard.scan_file(target)] == [2]

    def test_binary_content_does_not_crash_it(self, tmp_path: Path) -> None:
        target = tmp_path / "blob.txt"
        target.write_bytes(b"\xff\xfe\x00\x01not utf-8")
        assert guard.scan_file(target) == []


class TestExitCodes:
    """Pre-commit reads the exit code, so it has to be right."""

    def test_returns_zero_when_clean(self, tmp_path: Path) -> None:
        target = tmp_path / "ok.py"
        target.write_text('email = "donor@example.invalid"\n')
        assert guard.main([str(target)]) == 0

    def test_returns_one_when_findings_exist(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.py"
        target.write_text('email = "person@gmail.com"\n')
        assert guard.main([str(target)]) == 1

    def test_missing_paths_are_ignored(self) -> None:
        assert guard.main(["does/not/exist.py"]) == 0


class TestRepoIsClean:
    """The guard must pass over its own repository."""

    def test_no_findings_anywhere_in_tree(self) -> None:
        paths = [
            path
            for pattern in ("*.py", "*.md", "*.json", "*.toml", "*.yaml", "*.yml", "*.txt", "*.cfg")
            for path in REPO_ROOT.rglob(pattern)
            if not guard.should_skip(path.relative_to(REPO_ROOT))
        ]
        findings = {
            f"{path.relative_to(REPO_ROOT)}:{number}: {finding}"
            for path in paths
            for number, finding in guard.scan_file(path)
        }
        assert not findings, f"PII guard found issues in-tree: {sorted(findings)}"
