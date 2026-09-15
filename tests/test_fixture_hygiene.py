"""Repo-hygiene tests: no real credentials or PII may enter version control.

These are cheap and they run on every commit, which is the point — the automated
scanners (bandit, detect-secrets, and ``scripts/check_pii.py`` in pre-commit)
catch the general case, and these catch the case specific to this API's payload
shapes.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).parent / "fixtures"

FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))

#: Patterns that indicate real-world identifying data.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)
_UK_PHONE = re.compile(r"\b(?:0\d{9,10}|\+44\d{9,10})\b")

#: Synthetic substitutes the sanitiser produces, which are allowed to appear.
ALLOWED = {
    "donor@example.invalid",
    "SW1A 1AA",
    "07700900000",
    "example.invalid",
}

#: Ofcom reserves 07700 900000-900999 for drama/testing, and RFC 6761 reserves
#: .invalid, so both substitutes are guaranteed never to reach a real person.
ALLOWED_PHONE_PREFIX = "07700900"


def test_fixtures_exist() -> None:
    """Guard against a silently empty fixture directory."""
    assert FIXTURES, "no fixtures found — did sanitise_capture.py run?"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_no_real_email_addresses(path: Path) -> None:
    """Only the reserved .invalid substitute may appear."""
    found = {match for match in _EMAIL.findall(path.read_text()) if match not in ALLOWED}
    assert not found, f"{path.name} contains non-synthetic email address(es)"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_no_real_postcodes(path: Path) -> None:
    """Only the synthetic postcode may appear."""
    found = {
        match.upper().replace(" ", "")
        for match in _UK_POSTCODE.findall(path.read_text())
        if match.upper().replace(" ", "") != "SW1A1AA"
    }
    assert not found, f"{path.name} contains non-synthetic postcode(s)"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_no_real_phone_numbers(path: Path) -> None:
    """Only Ofcom's reserved test range may appear."""
    found = {m for m in _UK_PHONE.findall(path.read_text()) if not m.startswith(ALLOWED_PHONE_PREFIX)}
    assert not found, f"{path.name} contains non-synthetic phone number(s)"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_donor_and_donation_ids_are_synthetic(path: Path) -> None:
    """Donor and donation identifiers must be the placeholder values."""
    payload = json.loads(path.read_text())
    found: list[str] = []

    def walk(node: object, key: str | None = None) -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, child_key)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif (
            isinstance(node, str)
            and key in {"donorID", "donorId", "donationId"}
            and node
            and not re.fullmatch(r"[DG]0+X?|G0{9}0{3}X|D0{7}", node)
        ):
            found.append(f"{key}={node}")

    walk(payload)
    assert not found, f"{path.name} has non-synthetic identifier(s): {found}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_tokens_are_not_real_jwts(path: Path) -> None:
    """A captured access token is a live credential and must never be committed."""
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        return
    for key in ("accessToken", "refreshToken"):
        value = payload.get(key)
        if value is None:
            continue
        assert isinstance(value, str)
        assert value.startswith("synthetic"), f"{path.name}:{key} does not look like a placeholder"


def test_dotenv_is_not_tracked() -> None:
    """An .env in the repo root must never be committable."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file()
    patterns = {line.strip() for line in gitignore.read_text().splitlines()}
    assert ".env" in patterns or "*.env" in patterns


def test_env_file_is_not_tracked_by_git() -> None:
    """A local .env is the documented workflow — it just must never be tracked."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git working tree")
    tracked = {Path(name) for name in result.stdout.split("\0") if name}
    offenders = {path for path in tracked if path.name == ".env" or path.name.endswith(".env")}
    offenders -= {path for path in offenders if path.name == ".env.example"}
    assert not offenders, f"credential file(s) tracked by git: {sorted(map(str, offenders))}"
