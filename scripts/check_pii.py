#!/usr/bin/env python3
"""Block commits that contain real credentials or personal data.

Runs as a pre-commit hook over the staged files. It complements — rather than
duplicates — the other scanners:

* **bandit** finds insecure code patterns, not personal data.
* **detect-secrets** finds high-entropy secrets, not UK postcodes or NHSBT donor
  identifiers.
* **this script** knows what *this* API's payloads look like: donor IDs, donation
  IDs, UK addresses and phone numbers, and the app's own token shapes.

Every synthetic substitute used by ``sanitise_capture.py`` is allowlisted, and
they are all drawn from officially reserved ranges (RFC 2606 ``example.*``, RFC
6761 ``.invalid``, Ofcom's 07700 900xxx drama range, and ``SW1A 1AA``) so a real
value can never be mistaken for one.

Add ``pii-allow`` to a line as a comment to accept a specific finding.

Exit codes: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

#: Inline marker that suppresses findings on a line.
ALLOW_MARKER = "pii-allow"

#: Paths never scanned. ``.secrets.baseline`` legitimately stores hashes, and
#: lock files are noise.
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
SKIP_NAMES = {
    ".secrets.baseline",
    "poetry.lock",
    "uv.lock",
    "package-lock.json",
    # This guard's own tests exist to hold synthetic values that must be
    # flagged, so scanning them would always fail. Everything in there is
    # invented; nothing is derived from a real account.
    "test_check_pii.py",
}

#: Extensions worth scanning. Binary assets are skipped outright.
BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".gz",
    ".apk",
    ".xapk",
    ".so",
    ".dex",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
}

# --- Reserved-by-standard values that are safe to commit ---------------------

SAFE_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "example.invalid",
    "test.invalid",
    "localhost",
)

#: File extensions that mean an "email-shaped" match is really a filename.
#: Home Assistant's retina asset naming (``icon@2x.png``) parses as an address
#: with local part ``icon`` and domain ``2x.png``, so without this every brand
#: asset reference is a false positive — and a scanner that cries wolf gets
#: switched off.
FILENAME_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".py",
    ".txt",
    ".zip",
)
#: ``SW1A 1AA`` is the sanitiser's substitute; the ``ZZ99`` outcode is reserved
#: by Royal Mail for "no fixed abode"/test use and can never be a real address,
#: which makes it the right thing for tests that need two distinct postcodes.
SAFE_POSTCODES = {"SW1A1AA"}
SAFE_POSTCODE_OUTCODE = "ZZ99"
SAFE_PHONE_PREFIXES = ("07700900", "+447700900")

# --- Detection patterns -----------------------------------------------------

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")
UK_PHONE = re.compile(r"(?:\+44|\b0)\d{9,10}\b")
#: A three-segment base64url string long enough to be a real JWT.
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE)
#: NHSBT donor id: D followed by 7 digits. Placeholder is all zeroes.
DONOR_ID = re.compile(r"\bD\d{7}\b")
#: NHSBT donation id: G + 13 alphanumerics.
DONATION_ID = re.compile(r"\bG\d{12}[A-Z0-9]\b")
#: An assignment of a credential-ish key to a non-placeholder literal.
CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(password|passwd|secret|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|client[_-]?secret)
    \b \s* [:=] \s* ["']([^"']{8,})["']
    """
)

#: Substrings that mark a credential-assignment value as a placeholder.
PLACEHOLDER_HINTS = (
    "example",
    "placeholder",
    "redact",
    "replace_me",
    "replaceme",
    "your_",
    "your-",
    "changeme",
    "change_me",
    "xxx",
    "dummy",
    "fake",
    "synthetic",
    "not-a-real",
    "not_a_real",
    "test",
    "sample",
    "<",
    "${",
    "{{",
    "!secret",
    "…",
)

#: The app's own static client key. Extracted from a public APK, identifies the
#: app rather than any user, and the library cannot function without it.
KNOWN_PUBLIC_VALUES = {"b0046936-5a05-439e-8a89-5beab70829b7"}


def _is_safe_email(value: str) -> bool:
    """True for reserved documentation/testing domains, or a filename."""
    lowered = value.lower()
    return lowered.endswith(SAFE_EMAIL_DOMAINS) or lowered.endswith(FILENAME_SUFFIXES)


def _is_safe_postcode(value: str) -> bool:
    """True for the sanitiser's substitute or the reserved ZZ99 outcode."""
    normalised = value.upper().replace(" ", "")
    return normalised in SAFE_POSTCODES or normalised.startswith(SAFE_POSTCODE_OUTCODE)


def _is_safe_phone(value: str) -> bool:
    """True for Ofcom's reserved drama range."""
    return value.startswith(SAFE_PHONE_PREFIXES)


def _is_placeholder(value: str) -> bool:
    """True when a credential-shaped literal is obviously not a real secret."""
    lowered = value.lower()
    return any(hint in lowered for hint in PLACEHOLDER_HINTS) or value in KNOWN_PUBLIC_VALUES


def _mask(value: str) -> str:
    """Show enough of a finding to locate it, without reprinting it in full."""
    if len(value) <= 6:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:3]}…{value[-2:]} ({len(value)} chars)"


def scan_line(line: str) -> list[str]:
    """Return findings for a single line."""
    if ALLOW_MARKER in line:
        return []

    findings: list[str] = []

    for match in EMAIL.findall(line):
        if not _is_safe_email(match):
            findings.append(f"email address {_mask(match)}")

    for match in UK_POSTCODE.findall(line):
        if not _is_safe_postcode(match):
            findings.append(f"UK postcode {_mask(match)}")

    for match in UK_PHONE.findall(line):
        if not _is_safe_phone(match):
            findings.append(f"UK phone number {_mask(match)}")

    if JWT.search(line):
        findings.append("JWT (a live access token)")

    if BEARER.search(line):
        findings.append("Bearer token")

    for match in DONOR_ID.findall(line):
        if match != "D0000000":
            findings.append(f"NHSBT donor id {_mask(match)}")

    for match in DONATION_ID.findall(line):
        if not match.startswith("G000000000000"):
            findings.append(f"NHSBT donation id {_mask(match)}")

    for key, value in CREDENTIAL_ASSIGNMENT.findall(line):
        if not _is_placeholder(value):
            findings.append(f"{key} assigned a non-placeholder value {_mask(value)}")

    return findings


def should_skip(path: Path) -> bool:
    """True for paths that are not worth scanning."""
    if path.name in SKIP_NAMES or path.suffix.lower() in BINARY_SUFFIXES:
        return True
    return bool(SKIP_PARTS.intersection(path.parts))


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Return ``(line_number, finding)`` pairs for a file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    results: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        results.extend((number, finding) for finding in scan_line(line))
    return results


def main(argv: Iterable[str]) -> int:
    """Scan the given paths; return 1 if anything was found."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(list(argv))

    total = 0
    for path in args.paths:
        if not path.is_file() or should_skip(path):
            continue
        for number, finding in scan_file(path):
            print(f"{path}:{number}: {finding}", file=sys.stderr)
            total += 1

    if total:
        print(
            f"\n{total} potential PII/secret finding(s). Nothing was committed.\n"
            "Fix by removing the value, replacing it with a reserved placeholder\n"
            "(donor@example.invalid, SW1A 1AA, 07700900000, D0000000), or running\n"
            "captures through scripts/sanitise_capture.py.\n"
            f"If a finding is genuinely safe, add a '{ALLOW_MARKER}' comment to that line.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
