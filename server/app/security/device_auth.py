"""Per-device bearer-token verification. Only SHA-256 verifiers are configured."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import UUID


def generate_device_token() -> str:
    """Generate a 256-bit random token (store only its digest on the server)."""

    return secrets.token_hex(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_device_token(token: str | None, token_hashes: dict[str, str]) -> str | None:
    """Return the canonical provisioned device UUID for a valid token."""

    if token is None or len(token) != 64:
        return None
    try:
        bytes.fromhex(token)
    except ValueError:
        return None
    candidate = token_digest(token)
    matched: str | None = None
    ambiguous = False
    for device_id, expected in token_hashes.items():
        if hmac.compare_digest(candidate, expected):
            if matched is not None:
                ambiguous = True
            else:
                matched = str(UUID(device_id))
    return None if ambiguous else matched


def parse_bearer_header(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, credential = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not credential or " " in credential:
        return None
    return credential
