"""Unit + property tests for Contact_Point encryption at rest (Task 13.2).

Covers Requirements 7.3 (encrypted at rest, separate from matching data) and
7.4 (never written in plaintext to logs / traces / audit):

* encrypt -> decrypt round-trips back to the original value;
* the stored token is not equal to and does not contain the plaintext;
* :func:`store_contact` produces a ContactPoint whose ``value_encrypted``
  decrypts back to the raw value (and stores no plaintext);
* :func:`reveal` returns the original value on the authorized path;
* :func:`redact` masks all but the last few characters for safe logging;
* the demo fallback cipher still round-trips (exercised offline when the
  optional ``cryptography`` library is not installed).

Validates: Requirements 7.3, 7.4
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.encryption import (
    ContactCipher,
    DemoXorCipher,
    build_cipher,
    redact,
    reveal,
    store_contact,
)

KEY = "change-me-demo-only-32byte-key!!"

# Contact-like values plus arbitrary unicode text to stress the round-trip.
PHONE_LIKE = st.text(alphabet="0123456789+ -()", min_size=1, max_size=20)
ANY_TEXT = st.text(min_size=1, max_size=200)
# For the "token never contains the plaintext" guarantee we use realistic
# contact lengths. The base64 token alphabet ([A-Za-z0-9_-]) overlaps with
# digits, so a *trivially short* value (e.g. the single digit "5") can appear
# in the encoded ciphertext by coincidence without being a real leak; a real
# phone/WhatsApp number is many characters long, where coincidental
# containment is effectively impossible.
REALISTIC_PHONE = st.text(alphabet="0123456789", min_size=8, max_size=15)


def _cipher() -> ContactCipher:
    return ContactCipher(KEY)


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #
def test_encrypt_decrypt_round_trip_example():
    cipher = _cipher()
    token = cipher.encrypt("+91 98765 43210")
    assert cipher.decrypt(token) == "+91 98765 43210"


def test_token_not_equal_and_does_not_contain_plaintext():
    cipher = _cipher()
    plaintext = "9876543210"
    token = cipher.encrypt(plaintext)
    assert token != plaintext
    assert plaintext not in token


def test_same_plaintext_encrypts_to_different_tokens():
    # A per-value nonce (demo) / Fernet IV means tokens are not deterministic.
    cipher = _cipher()
    a = cipher.encrypt("9876543210")
    b = cipher.encrypt("9876543210")
    assert a != b
    assert cipher.decrypt(a) == cipher.decrypt(b) == "9876543210"


@given(value=ANY_TEXT)
def test_round_trip_property(value: str):
    cipher = _cipher()
    assert cipher.decrypt(cipher.encrypt(value)) == value


@given(value=REALISTIC_PHONE)
def test_token_never_contains_plaintext_property(value: str):
    cipher = _cipher()
    token = cipher.encrypt(value)
    assert token != value
    assert value not in token


# --------------------------------------------------------------------------- #
# Demo fallback cipher (always available, no dependency)
# --------------------------------------------------------------------------- #
def test_demo_cipher_round_trips():
    demo = DemoXorCipher(KEY)
    token = demo.encrypt("whatsapp:+919876543210")
    assert token.startswith("demo:")
    assert "919876543210" not in token
    assert demo.decrypt(token) == "whatsapp:+919876543210"


def test_prefer_demo_backend_round_trips():
    cipher = ContactCipher(KEY, prefer_demo=True)
    assert cipher.backend == "demo"
    token = cipher.encrypt("9998887776")
    assert token.startswith("demo:")
    assert cipher.decrypt(token) == "9998887776"


# --------------------------------------------------------------------------- #
# store_contact / reveal
# --------------------------------------------------------------------------- #
def test_store_contact_encrypts_and_decrypts_back():
    cipher = _cipher()
    cp = store_contact(
        subject_id="donor-1",
        type="phone",
        raw_value="9876543210",
        preferred_lang="te",
        cipher=cipher,
    )
    # Only the encrypted form is stored; plaintext is absent.
    assert cp.value_encrypted != "9876543210"
    assert "9876543210" not in cp.value_encrypted
    assert cp.subject_id == "donor-1"
    assert cp.type == "phone"
    assert cp.preferred_lang == "te"
    assert cp.contact_id
    # The narrow authorized path recovers the original value.
    assert reveal(cp, cipher=cipher) == "9876543210"
    assert cipher.decrypt(cp.value_encrypted) == "9876543210"


def test_store_contact_generates_unique_contact_ids():
    cipher = _cipher()
    a = store_contact("d1", "phone", "111", cipher=cipher)
    b = store_contact("d1", "phone", "111", cipher=cipher)
    assert a.contact_id != b.contact_id


def test_build_cipher_from_explicit_key_round_trips():
    cipher = build_cipher(key=KEY)
    assert reveal(
        store_contact("d2", "whatsapp", "12345", cipher=cipher), cipher=cipher
    ) == "12345"


# --------------------------------------------------------------------------- #
# redact
# --------------------------------------------------------------------------- #
def test_redact_masks_all_but_last_two():
    assert redact("9876543210") == "********10"


def test_redact_short_values_fully_masked():
    assert redact("12") == "**"
    assert redact("7") == "*"
    assert redact("") == ""
    assert redact(None) == ""


@given(value=PHONE_LIKE)
def test_redact_never_reveals_more_than_last_two(value: str):
    out = redact(value)
    assert len(out) == len(value)
    if len(value) > 2:
        # Everything but the final two characters is masked.
        assert out[:-2] == "*" * (len(value) - 2)
        assert out[-2:] == value[-2:]
