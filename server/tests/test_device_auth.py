from app.security.device_auth import generate_device_token, token_digest, verify_device_token
from app.config import Settings
import pytest


def test_generated_token_has_256_bits_and_verifies_for_one_identity():
    token = generate_device_token()
    device = "593a0d29-c053-4c40-9c3c-8d7d26102f7a"
    assert len(token) == 64
    assert verify_device_token(token, {device: token_digest(token)}) == device
    assert verify_device_token(token, {}) is None
    assert verify_device_token(None, {device: token_digest(token)}) is None
    assert verify_device_token("bad", {device: token_digest(token)}) is None


def test_revoked_or_rotated_token_does_not_authenticate():
    token = generate_device_token()
    device = "593a0d29-c053-4c40-9c3c-8d7d26102f7a"
    assert verify_device_token(token, {}) is None
    assert verify_device_token(token, {device: token_digest(generate_device_token())}) is None


def test_configuration_rejects_one_verifier_bound_to_multiple_devices():
    digest = token_digest(generate_device_token())
    with pytest.raises(ValueError):
        Settings(_env_file=None, device_token_hashes={
            "593a0d29-c053-4c40-9c3c-8d7d26102f7a": digest,
            "b0e4f026-663c-4739-9e88-e7a7f8b7ce16": digest,
        })
