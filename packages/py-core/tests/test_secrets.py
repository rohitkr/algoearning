import os

import pytest
from ae_core.secrets import DecryptionError, SecretBox, load_master_key, mask, new_master_key

K1, K2 = os.urandom(32), os.urandom(32)


def test_roundtrip_and_no_plaintext_in_blob() -> None:
    box = SecretBox({1: K1}, 1)
    blob = box.encrypt("kite-secret-123", "broker_account:a:api_secret")
    assert b"kite-secret-123" not in blob and blob.startswith(b"ae1")
    assert box.decrypt(blob, "broker_account:a:api_secret") == "kite-secret-123"
    assert box.encrypt("x", "c") != box.encrypt("x", "c")  # fresh DEK + nonces every time


def test_blob_is_bound_to_its_context() -> None:
    box = SecretBox({1: K1}, 1)
    blob = box.encrypt("s", "broker_account:a:api_secret")
    for other in ("broker_account:b:api_secret", "broker_account:a:api_key"):
        with pytest.raises(DecryptionError):
            box.decrypt(blob, other)


def test_tampering_and_wrong_keys_fail() -> None:
    blob = SecretBox({1: K1}, 1).encrypt("s", "c")
    flipped = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(DecryptionError):
        SecretBox({1: K1}, 1).decrypt(flipped, "c")
    with pytest.raises(DecryptionError):
        SecretBox({1: K2}, 1).decrypt(blob, "c")
    with pytest.raises(DecryptionError):
        SecretBox({1: K1}, 1).decrypt(b"plaintext-not-encrypted", "c")


def test_key_rotation_keeps_old_blobs_readable() -> None:
    old = SecretBox({1: K1}, 1).encrypt("s", "c")
    box = SecretBox({1: K1, 2: K2}, 2)
    new = box.encrypt("t", "c")
    assert SecretBox.key_version(old) == 1 and SecretBox.key_version(new) == 2
    assert box.decrypt(old, "c") == "s" and box.decrypt(new, "c") == "t"


def test_master_key_loading_and_masking() -> None:
    assert len(load_master_key(new_master_key())) == 32
    for bad in ("not base64!!", "c2hvcnQ="):
        with pytest.raises(ValueError):
            load_master_key(bad)
    assert mask("abcd1234wxyz") == "••••wxyz" and mask("short") == "••••"
