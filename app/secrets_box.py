"""Encryption for the one secret we hold on a user's behalf: their API key.

Being honest about what this buys. The server has to decrypt a key in order to
call the model with it, so this protects against a leaked *copy* of data/ -- a
backup, a synced folder, someone poking around the disk -- and not against
someone who has the whole machine. The key itself lives in .env, outside data/,
so the ciphertext and the key do not travel together by accident.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _cipher() -> Fernet:
    # Derive a fixed Fernet key from the configured secret so the value stored in
    # .env does not have to be in Fernet's exact format.
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Return the plaintext, or "" when it cannot be decrypted.

    A failed decrypt usually means SECRET_KEY was changed. Returning empty lets
    the caller show "please re-enter your key" instead of crashing.
    """
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
