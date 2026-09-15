#!/usr/bin/env python3
"""Turn a live API capture into a committable test fixture.

Live responses from this API are saturated with donor PII. This script replaces
every identifying value with a deterministic synthetic substitute while leaving
structure, types and enum-ish codes intact, so fixtures stay realistic without
carrying anyone's real data.

Usage::

    python scripts/sanitise_capture.py /tmp/capture/ep_account_details.json \
        tests/fixtures/account_details.json

Structure is preserved; values in :data:`REDACT_KEYS` are replaced, and free
text is scanned for anything that still looks like a postcode, email or phone
number. Review the output before committing it — this is a safety net, not a
guarantee.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

#: Wire field names whose values are replaced outright.
REDACT_KEYS: dict[str, Any] = {
    "donorID": "D0000000",
    "donorId": "D0000000",
    "donationId": "G000000000000X",
    "accessToken": "synthetic.access.token",
    "refreshToken": "synthetic-refresh-token",
    "surname": "Testerson",
    "firstForename": "Ada",
    "secondForename": "",
    "otherForenames": "",
    "title": "Ms",
    "dateOfBirth": "1990-01-01T00:00:00",
    "postcode": "SW1A 1AA",
    "number": "07700900000",
    "address": "donor@example.invalid",
    "emailAddress": "donor@example.invalid",
    "latitude": "51.5",
    "longitude": "-0.12",
    "lat": 51.5,
    "long": -0.12,
    "gridReference": "TQ000000",
    # Venue identity plus donation dates plus blood group can re-identify a
    # donor even with the name gone, so break the venue half of that chain.
    "venueId": "TSTV1",
    "venueID": "TSTV1",
    "venueName": "Testville, Example Donor Centre",
    "externalLocation": "Example Building",
    "internalLocation": "Example Room",
    "lastDonatedVenueId": "TSTV1",
    "sessionId": "CS0000",
    "sessionID": "CS0000",
    # Protected characteristics: neutralised rather than removed, so the field
    # still exercises the model.
    "gender": "Not specified",
    "genderIdentity": "Not specified",
    "sexAtBirth": "Not specified",
    "ethnicOrigin": "00",
    "ethnicOriginDescription": "Not specified",
}

#: Keys whose value is a list of free text that gets substituted wholesale
#: rather than scrubbed.
#:
#: ``notes`` is the important one. Venue notes are unstructured operator prose —
#: car parking arrangements, nearby car park names and postcodes, booking
#: telephone numbers — sometimes URL-encoded so pattern matching misses it.
#: Scrubbing free text like that is unreliable by nature, so it is replaced
#: outright.
LINE_KEYS = {"lines"}
NOTE_KEYS = {"notes"}

#: Substitutes for venue notes: same shape and length class, no real detail.
SYNTHETIC_NOTES = (
    "Parking is available on site. Please display a valid ticket.",
    "Step-free access via the main entrance.",
    "To book or change an appointment, please telephone 07700900000.",
    "Please bring photo identification to your first appointment.",
)

_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\b(?:0\d{9,10}|\+44\d{9,10})\b")


def _scrub_text(value: str) -> str:
    """Replace anything in free text that still looks identifying."""
    value = _EMAIL.sub("donor@example.invalid", value)
    value = _POSTCODE.sub("SW1A 1AA", value)
    return _PHONE.sub("07700900000", value)


def sanitise(node: Any, *, key: str | None = None) -> Any:
    """Recursively sanitise a decoded JSON document."""
    if key in REDACT_KEYS and not isinstance(node, (dict, list)):
        return REDACT_KEYS[key]
    if key in LINE_KEYS and isinstance(node, list):
        return [f"{index + 1} Example Street" for index in range(len(node))]
    if key in NOTE_KEYS and isinstance(node, list):
        return [SYNTHETIC_NOTES[index % len(SYNTHETIC_NOTES)] for index in range(len(node))]
    if isinstance(node, dict):
        return {child_key: sanitise(child, key=child_key) for child_key, child in node.items()}
    if isinstance(node, list):
        return [sanitise(item, key=key) for item in node]
    if isinstance(node, str):
        return _scrub_text(node)
    return node


def main(argv: list[str]) -> int:
    """Sanitise ``argv[0]`` into ``argv[1]``."""
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source, destination = Path(argv[0]), Path(argv[1])
    payload = json.loads(source.read_text())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(sanitise(payload), indent=2) + "\n")
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
