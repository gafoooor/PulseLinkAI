"""Unit tests for consent management and the revocation cascade (Task 13.1).

Covers, using the offline ``InMemoryConsentService``:
* grant then ``has_active_scope`` is True for a granted scope.
* revoke then ``has_active_scope`` is False for *every* scope and
  ``is_revoked`` is True — this is what excludes a revoked subject from future
  matching and suppresses future notifications (Requirement 7.2).
* revoke publishes a ``consent_revoked`` event carrying the ``subject_id`` so
  pending notifications can be purged (the "suppress pending" half of Req 7.2);
  a spy subscribed on the InMemoryEventBus asserts it fired.
* the consent-text ``version`` is recorded and advances on re-grant.

Offline only. Requirements: 7.2
"""

from __future__ import annotations

from pulselink.common.consent import (
    EVENT_CONSENT_REVOKED,
    ConsentStore,
    InMemoryConsentService,
)
from pulselink.common.enums import ConsentScope
from pulselink.common.event_bus import InMemoryEventBus

ALL_SCOPES = list(ConsentScope)


def test_grant_then_has_active_scope_true():
    """A granted scope is active immediately after grant()."""
    service = InMemoryConsentService()

    consent = service.grant(
        "donor-1", "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1"
    )

    assert consent.subject_id == "donor-1"
    assert service.has_active_scope("donor-1", ConsentScope.CONTACT_FOR_SLOTS) is True
    assert service.is_revoked("donor-1") is False


def test_has_active_scope_false_for_ungranted_scope():
    """Only the scopes that were granted are active."""
    service = InMemoryConsentService()
    service.grant("donor-1", "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1")

    assert (
        service.has_active_scope("donor-1", ConsentScope.USE_IN_FORECASTING) is False
    )


def test_has_active_scope_false_for_unknown_subject():
    """A subject with no consent on file holds no active scope."""
    service = InMemoryConsentService()

    assert service.has_active_scope("ghost", ConsentScope.CONTACT_FOR_SLOTS) is False
    assert service.is_revoked("ghost") is False
    assert service.get("ghost") is None


def test_revoke_disables_every_scope_and_sets_is_revoked():
    """After revoke, no scope is active and is_revoked is True (Req 7.2)."""
    service = InMemoryConsentService()
    service.grant("donor-1", "donor", ALL_SCOPES, version="v1")

    revoked = service.revoke("donor-1")

    assert revoked is not None
    assert revoked.revoked_at is not None
    assert service.is_revoked("donor-1") is True
    # Revocation excludes the subject from every consent-gated action.
    for scope in ALL_SCOPES:
        assert service.has_active_scope("donor-1", scope) is False


def test_revoke_publishes_consent_revoked_event_with_subject_id():
    """revoke() fires consent_revoked carrying the subject_id (Req 7.2)."""
    bus = InMemoryEventBus()
    received: list[dict] = []
    bus.subscribe(EVENT_CONSENT_REVOKED, received.append)

    service = InMemoryConsentService(event_bus=bus)
    service.grant("donor-7", "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1")

    service.revoke("donor-7")

    assert len(received) == 1
    assert received[0]["subject_id"] == "donor-7"
    assert received[0]["revoked_at"] is not None


def test_revoke_is_idempotent_and_does_not_republish():
    """Re-revoking an already-revoked subject is a no-op (publishes once)."""
    bus = InMemoryEventBus()
    received: list[dict] = []
    bus.subscribe(EVENT_CONSENT_REVOKED, received.append)

    service = InMemoryConsentService(event_bus=bus)
    service.grant("donor-7", "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1")

    service.revoke("donor-7")
    service.revoke("donor-7")

    assert len(received) == 1


def test_revoke_unknown_subject_returns_none_and_does_not_publish():
    """Revoking a subject with no consent on file does nothing."""
    bus = InMemoryEventBus()
    received: list[dict] = []
    bus.subscribe(EVENT_CONSENT_REVOKED, received.append)

    service = InMemoryConsentService(event_bus=bus)

    assert service.revoke("ghost") is None
    assert received == []


def test_version_is_recorded_and_advances_on_regrant():
    """The consent-text version is stored and a re-grant supersedes it."""
    service = InMemoryConsentService()

    v1 = service.grant("patient-1", "patient", [ConsentScope.STORE_CONTACT], version="v1")
    assert v1.version == "v1"
    assert service.get("patient-1").version == "v1"

    v2 = service.grant(
        "patient-1", "patient", [ConsentScope.STORE_CONTACT], version="v2"
    )
    assert v2.version == "v2"
    # The latest version supersedes the prior record.
    assert service.get("patient-1").version == "v2"


def test_regrant_after_revoke_reactivates_subject():
    """A fresh grant supersedes a revoked record and re-enables scopes."""
    service = InMemoryConsentService()
    service.grant("donor-1", "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1")
    service.revoke("donor-1")
    assert service.is_revoked("donor-1") is True

    service.grant("donor-1", "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v2")

    assert service.is_revoked("donor-1") is False
    assert service.has_active_scope("donor-1", ConsentScope.CONTACT_FOR_SLOTS) is True


def test_in_memory_service_satisfies_consent_store_protocol():
    """InMemoryConsentService structurally satisfies the ConsentStore seam."""
    assert isinstance(InMemoryConsentService(), ConsentStore)
