"""Contact_Point encryption-at-rest seam (Task 13.2).

This module is the single, narrow place where a subject's contact value
(phone / WhatsApp number) is turned into the ``value_encrypted`` token stored
in the ``contact_point`` table, and the only authorized place it is turned
back into plaintext (e.g. immediately before a consented send).

Design intent (Requirements 7.3, 7.4):

* **Encrypted at rest.** ``ContactPoint.value_encrypted`` is the *only* stored
  form of a contact value; the plaintext is never persisted.
* **Separate from operational matching data.** ``contact_point`` already lives
  in its own table with no ``city_id`` (see ``db_models.py``); this module adds
  no coupling to the matching tables.
* **No plaintext in logs / traces / audit.** Nothing here logs a raw value, the
  helpers return tokens (not plaintext), and :func:`redact` is provided so any
  human-readable log line shows a masked value only.

Provider seam (maps to AWS KMS in production):

* If the optional ``cryptography`` library is installed, :class:`ContactCipher`
  uses **Fernet** (authenticated symmetric encryption). This is the preferred,
  production-leaning path; in real deployments the key is a KMS-managed data
  key rather than a config string.
* Otherwise it falls back to a clearly-labelled **demo cipher**
  (:class:`DemoXorCipher`) — a documented, reversible keyed-XOR scheme that is
  ``NOT FOR PRODUCTION``. It exists only so the offline hackathon demo runs with
  zero extra dependencies. It provides obfuscation-at-rest, not real security.

The key is read from ``settings.contact_encryption_key``. In production this
maps to an AWS KMS-managed key (``contact_encryption_key`` would be a key id /
the decrypted data key), never a literal string in config.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from typing import Optional, Protocol, runtime_checkable

from pulselink.common.config import Settings, get_settings
from pulselink.common.models import ContactPoint, ContactType

# Detect the optional, preferred backend without making it a hard dependency.
try:  # pragma: no cover - import side effect depends on the environment
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore

    _HAS_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - exercised in the offline/demo profile
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment, misc]
    _HAS_CRYPTOGRAPHY = False


# Scheme prefixes let :meth:`ContactCipher.decrypt` route a token to the right
# backend even if a value was written under a different backend earlier.
_FERNET_PREFIX = "fernet:"
_DEMO_PREFIX = "demo:"


@runtime_checkable
class Encryptor(Protocol):
    """Minimal symmetric-encryption contract: round-trippable token <-> text."""

    def encrypt(self, plaintext: str) -> str:
        """Return an opaque token for ``plaintext``."""
        ...

    def decrypt(self, token: str) -> str:
        """Return the original plaintext for a token produced by ``encrypt``."""
        ...


def _key_bytes(key: str) -> bytes:
    """Normalise an arbitrary key string to raw bytes."""
    return key.encode("utf-8")


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class FernetCipher:
    """Authenticated symmetric encryption via ``cryptography`` Fernet.

    The configured key string is hashed to a stable 32-byte value and
    url-safe-base64-encoded to form a valid Fernet key, so any key string works
    (in production the underlying key is KMS-managed).
    """

    def __init__(self, key: str) -> None:
        if not _HAS_CRYPTOGRAPHY:  # pragma: no cover - guarded by factory
            raise RuntimeError("cryptography is not available")
        digest = hashlib.sha256(_key_bytes(key)).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return _FERNET_PREFIX + token

    def decrypt(self, token: str) -> str:
        raw = token[len(_FERNET_PREFIX):] if token.startswith(_FERNET_PREFIX) else token
        return self._fernet.decrypt(raw.encode("ascii")).decode("utf-8")


class DemoXorCipher:
    """Reversible keyed-XOR demo cipher. NOT FOR PRODUCTION.

    A random per-value nonce is mixed with the key to derive a keystream, so the
    same plaintext does not encrypt to the same token. The nonce is carried in
    the token so :meth:`decrypt` can reconstruct the keystream. This provides
    obfuscation-at-rest with zero dependencies for the offline demo only; it is
    NOT a substitute for real encryption (no authentication, weak KDF). In
    production the Fernet backend (or AWS KMS) is used instead.
    """

    _NONCE_LEN = 8

    def __init__(self, key: str) -> None:
        self._key = _key_bytes(key)

    @staticmethod
    def _keystream(seed: bytes, length: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            out += hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
            counter += 1
        return bytes(out[:length])

    def encrypt(self, plaintext: str) -> str:
        data = plaintext.encode("utf-8")
        nonce = os.urandom(self._NONCE_LEN)
        keystream = self._keystream(self._key + nonce, len(data))
        cipher = bytes(b ^ k for b, k in zip(data, keystream))
        blob = base64.urlsafe_b64encode(nonce + cipher).decode("ascii")
        return _DEMO_PREFIX + blob

    def decrypt(self, token: str) -> str:
        raw = token[len(_DEMO_PREFIX):] if token.startswith(_DEMO_PREFIX) else token
        blob = base64.urlsafe_b64decode(raw.encode("ascii"))
        nonce, cipher = blob[: self._NONCE_LEN], blob[self._NONCE_LEN:]
        keystream = self._keystream(self._key + nonce, len(cipher))
        data = bytes(b ^ k for b, k in zip(cipher, keystream))
        return data.decode("utf-8")


# --------------------------------------------------------------------------- #
# Public cipher facade
# --------------------------------------------------------------------------- #
class ContactCipher:
    """Selects the best available backend and exposes encrypt / decrypt.

    Prefers Fernet when ``cryptography`` is installed; otherwise uses the
    clearly-labelled demo XOR cipher. ``decrypt`` routes by the token's scheme
    prefix, so tokens remain readable across backend availability changes.
    """

    def __init__(self, key: str, *, prefer_demo: bool = False) -> None:
        self.key = key
        self._fernet: Optional[FernetCipher] = None
        self._demo = DemoXorCipher(key)
        if _HAS_CRYPTOGRAPHY and not prefer_demo:
            self._fernet = FernetCipher(key)
        self.backend = "fernet" if self._fernet is not None else "demo"

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is not None:
            return self._fernet.encrypt(plaintext)
        return self._demo.encrypt(plaintext)

    def decrypt(self, token: str) -> str:
        if token.startswith(_FERNET_PREFIX):
            if self._fernet is None:  # pragma: no cover - defensive
                raise RuntimeError(
                    "token requires the Fernet backend but cryptography is unavailable"
                )
            return self._fernet.decrypt(token)
        if token.startswith(_DEMO_PREFIX):
            return self._demo.decrypt(token)
        # Unprefixed legacy token: fall back to the active backend.
        if self._fernet is not None:
            return self._fernet.decrypt(token)
        return self._demo.decrypt(token)


def build_cipher(
    settings: Optional[Settings] = None, *, key: Optional[str] = None
) -> ContactCipher:
    """Build a :class:`ContactCipher` from settings (or an explicit key)."""
    if key is None:
        settings = settings or get_settings()
        key = settings.contact_encryption_key
    return ContactCipher(key)


# --------------------------------------------------------------------------- #
# ContactPoint helpers (the narrow, authorized encrypt / decrypt path)
# --------------------------------------------------------------------------- #
def store_contact(
    subject_id: str,
    type: ContactType,
    raw_value: str,
    preferred_lang: Optional[str] = None,
    *,
    contact_id: Optional[str] = None,
    cipher: Optional[ContactCipher] = None,
) -> ContactPoint:
    """Build a protected :class:`ContactPoint` from a plaintext contact value.

    The plaintext ``raw_value`` is encrypted immediately and only the token is
    stored in ``value_encrypted``; the raw value is never persisted or logged.
    """
    cipher = cipher or build_cipher()
    return ContactPoint(
        contact_id=contact_id or f"contact-{uuid.uuid4().hex}",
        subject_id=subject_id,
        type=type,
        value_encrypted=cipher.encrypt(raw_value),
        preferred_lang=preferred_lang,
    )


def reveal(contact_point: ContactPoint, *, cipher: Optional[ContactCipher] = None) -> str:
    """Decrypt a Contact_Point's value for a narrow, authorized use.

    Call this only on the authorized path (e.g. immediately before a consented
    send). The returned plaintext MUST NOT be logged, traced, or written to an
    audit entry (Requirement 7.4) — use :func:`redact` for any log line.
    """
    cipher = cipher or build_cipher()
    return cipher.decrypt(contact_point.value_encrypted)


def redact(value: Optional[str], *, visible: int = 2, mask: str = "*") -> str:
    """Mask a contact value for safe display in logs / traces.

    Keeps only the last ``visible`` characters and masks the rest, e.g.
    ``redact("9876543210") -> "********10"``. Short or empty values are fully
    masked so no meaningful digits leak.
    """
    if not value:
        return ""
    if len(value) <= visible:
        return mask * len(value)
    return mask * (len(value) - visible) + value[-visible:]
