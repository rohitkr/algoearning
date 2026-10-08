"""Envelope encryption for broker credentials (API key/secret, daily access tokens).

Every value gets its own random 256-bit data key (DEK). The value is encrypted with the DEK (AES-256-GCM), and the
DEK is encrypted ("wrapped") with the master key (KEK, APP_ENCRYPTION_KEY; later a KMS key). Stored blob:

    b"ae1" | key_version (1 byte) | wrap_nonce (12) | wrapped_dek (32 + 16 tag) | nonce (12) | ciphertext + tag

`context` (e.g. "broker_account:<id>:api_secret") is authenticated additional data for both layers: a blob copied
onto another row, another user's account or another field fails to decrypt instead of leaking a credential into
the wrong place. Rotating the master key = add a new version, re-wrap DEKs, keep old versions until done.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ae1"
_NONCE = 12
_WRAPPED = 32 + 16


class DecryptionError(Exception):
    """Wrong key, wrong context, or tampered data. Never includes the plaintext."""


def load_master_key(b64: str) -> bytes:
    try:
        key = base64.b64decode(b64, validate=True)
    except ValueError as exc:
        raise ValueError("APP_ENCRYPTION_KEY must be base64") from exc
    if len(key) != 32:
        raise ValueError("APP_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256)")
    return key


def new_master_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


class SecretBox:
    def __init__(self, keys: Mapping[int, bytes], current: int) -> None:
        if current not in keys:
            raise ValueError("current key version is not among the keys")
        if not all(0 < v < 256 and len(k) == 32 for v, k in keys.items()):
            raise ValueError("key versions must be 1..255 and keys 32 bytes")
        self._keys, self.current = dict(keys), current

    def encrypt(self, plaintext: str, context: str) -> bytes:
        aad = context.encode()
        dek = AESGCM.generate_key(bit_length=256)
        wrap_nonce, nonce = os.urandom(_NONCE), os.urandom(_NONCE)
        wrapped = AESGCM(self._keys[self.current]).encrypt(wrap_nonce, dek, aad)
        body = AESGCM(dek).encrypt(nonce, plaintext.encode(), aad)
        return MAGIC + bytes([self.current]) + wrap_nonce + wrapped + nonce + body

    def decrypt(self, blob: bytes, context: str) -> str:
        if len(blob) < 4 + _NONCE + _WRAPPED + _NONCE + 16 or not blob.startswith(MAGIC):
            raise DecryptionError("not an encrypted secret")
        version = blob[3]
        kek = self._keys.get(version)
        if kek is None:
            raise DecryptionError(f"unknown key version {version}")
        i = 4
        wrap_nonce, wrapped = blob[i : i + _NONCE], blob[i + _NONCE : i + _NONCE + _WRAPPED]
        i += _NONCE + _WRAPPED
        nonce, body = blob[i : i + _NONCE], blob[i + _NONCE :]
        aad = context.encode()
        try:
            dek = AESGCM(kek).decrypt(wrap_nonce, wrapped, aad)
            return AESGCM(dek).decrypt(nonce, body, aad).decode()
        except InvalidTag:
            raise DecryptionError("secret could not be decrypted (wrong key or context, or tampered)") from None

    @staticmethod
    def key_version(blob: bytes) -> int:
        return blob[3]


def mask(value: str, visible: int = 4) -> str:
    """For display: '••••' + last few characters (nothing at all for short values)."""
    return "••••" + value[-visible:] if len(value) > visible * 2 else "••••"


def mask_phone(phone: str) -> str:
    """For display: '+91 98•••••210' (country code, the first two and last three digits)."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 8:
        return "••••"
    cc_len = len(digits) - 10 if len(digits) > 10 else 0
    cc, rest = digits[:cc_len], digits[cc_len:]
    return f"{'+' + cc + ' ' if cc else ''}{rest[:2]}{'•' * (len(rest) - 5)}{rest[-3:]}"
