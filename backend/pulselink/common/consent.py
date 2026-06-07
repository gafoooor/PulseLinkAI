"""Consent management and the revocation cascade (Task 13.1).

PulseLink treats consent as a **versioned**, scoped grant for a patient or
donor, and makes revocation the authoritative gate that other services consult
before contacting or matching a subject. This module is the decoupled seam the
rest of the system calls; it deliberately edits no other service's files. The
matching flow (Task 7.x) and the messaging / voice flows (Tasks 9.x / 10.x)
wire the consent check in from their own tasks — this module only provides the
service, the event, and the single check they call.

Requirement 7.2 (the revocation cascade):

    "WHEN a subject revokes consent, THE PulseLink SHALL suppress pending and
     future notifications to that subject and exclude that subject from future
     matching."

How that requirement is honoured here, decoupled:

* **One authoritative check.** :meth:`ConsentStore.has_active_scope` is the
  single predicate matching / messaging / voice consult. ``active`` means the
  subject's consent is **not revoked AND grants the requested scope**. A
  revoked subject therefore returns ``False`` for *every* scope, which is what
  excludes them from future matching and suppresses future notifications — the
  callers simply skip any subject for whom ``has_active_scope`` is ``False``.
  (Matching uses the ``contact_for_slots`` scope per Requirement 4.2; the
  messaging and voice channels check the same scope per Requirements 7.1 /
  13.4.)

* **A revocation event for pending work.** ``has_active_scope`` covers *future*
  matching and *future* sends, but work already queued needs to be purged.
  :meth:`revoke` publishes a ``consent_revoked`` event on the event bus so any
  component holding pending notifications (a queue, an in-flight offer) can
  react and drop them. The event payload carries the ``subject_id`` so handlers
  know exactly whose pending work to suppress. Publishing is *exactly once* per
  state-changing revocation: re-revoking an already-revoked subject is a no-op
  and does not re-publish.

* **Versioned grants.** Each :meth:`grant` records the consent-text
  ``version``. Re-granting for the same subject supersedes the prior record
  (the latest version wins) so consent history advances rather than silently
  mutating in place.

Both an in-memory implementation (for the offline demo / tests) and an
optional DB-backed implementation (using the ``consent`` ORM table) are
provided. The DB-backed variant is optional and is not required to run the
tests.

Requirements: 7.2 (cross-checks: 4.2, 7.1, 13.4)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from pulselink.common.enums import ConsentScope
from pulselink.common.event_bus import EventBus
from pulselink.common.models import Consent, SubjectType

# --------------------------------------------------------------------------- #
# Canonical event type
# --------------------------------------------------------------------------- #
# Published on the event bus when a subject revokes consent. Components holding
# pending notifications subscribe to this to purge queued work for the subject
# (the "suppress pending notifications" half of Requirement 7.2). The "exclude
# from future matching / suppress future sends" half is handled by callers
# consulting ``has_active_scope`` at match / send time.
EVENT_CONSENT_REVOKED = "consent_revoked"


# --------------------------------------------------------------------------- #
# The consent-store seam
# --------------------------------------------------------------------------- #
@runtime_checkable
class ConsentStore(Protocol):
    """Consent management seam (Requirement 7.2).

    The matching, messaging, and voice flows depend only on this surface —
    primarily :meth:`has_active_scope`, the one check that gates contact and
    matching for a subject.
    """

    def grant(
        self,
        subject_id: str,
        subject_type: SubjectType,
        scopes: list[ConsentScope],
        version: str,
    ) -> Consent:
        """Record a versioned, scoped consent for a subject."""
        ...

    def revoke(self, subject_id: str) -> Optional[Consent]:
        """Revoke a subject's consent (the revocation-cascade entry point)."""
        ...

    def has_active_scope(self, subject_id: str, scope: ConsentScope) -> bool:
        """True when the subject's consent is active and grants ``scope``."""
        ...

    def is_revoked(self, subject_id: str) -> bool:
        """True when the subject's consent has been revoked."""
        ...

    def get(self, subject_id: str) -> Optional[Consent]:
        """Return the subject's current consent record, if any."""
        ...


def _new_consent(
    subject_id: str,
    subject_type: SubjectType,
    scopes: list[ConsentScope],
    version: str,
) -> Consent:
    """Build an active :class:`Consent` with a service-set UTC ``granted_at``.

    Centralised so every implementation stamps ``granted_at`` identically and
    mints a stable ``consent_id``.
    """
    return Consent(
        consent_id=f"consent-{uuid.uuid4().hex}",
        subject_id=subject_id,
        subject_type=subject_type,
        scopes=list(scopes),
        granted_at=datetime.now(timezone.utc),
        revoked_at=None,
        version=version,
    )


# --------------------------------------------------------------------------- #
# In-memory implementation (offline tests / demo)
# --------------------------------------------------------------------------- #
class InMemoryConsentService:
    """Offline ``ConsentStore`` keyed by ``subject_id`` in process memory.

    A single current consent record is held per subject; re-granting supersedes
    the prior version. Revocation flips ``revoked_at`` and publishes a
    ``consent_revoked`` event (exactly once per state-changing revoke) on the
    optional :class:`~pulselink.common.event_bus.EventBus` so any pending-work
    holder can purge queued notifications for the subject.
    """

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self._event_bus = event_bus
        self._by_subject: dict[str, Consent] = {}

    def grant(
        self,
        subject_id: str,
        subject_type: SubjectType,
        scopes: list[ConsentScope],
        version: str,
    ) -> Consent:
        """Record a versioned, scoped consent; supersedes any prior record."""
        consent = _new_consent(subject_id, subject_type, scopes, version)
        self._by_subject[subject_id] = consent
        return consent

    def revoke(self, subject_id: str) -> Optional[Consent]:
        """Revoke the subject's consent and fire the cascade.

        Sets ``revoked_at`` and publishes ``consent_revoked`` carrying the
        ``subject_id`` so pending notifications can be suppressed. Returns the
        revoked record, or ``None`` if the subject has no consent on file.
        Re-revoking an already-revoked subject is a no-op and does not
        re-publish the event.
        """
        current = self._by_subject.get(subject_id)
        if current is None or current.revoked_at is not None:
            return current

        revoked = current.model_copy(
            update={"revoked_at": datetime.now(timezone.utc)}
        )
        self._by_subject[subject_id] = revoked
        self._publish_revoked(revoked)
        return revoked

    def has_active_scope(self, subject_id: str, scope: ConsentScope) -> bool:
        """The single check matching / messaging / voice consult (Req 7.2)."""
        current = self._by_subject.get(subject_id)
        return current is not None and current.has_active_scope(scope)

    def is_revoked(self, subject_id: str) -> bool:
        """True when the subject's consent exists and has been revoked."""
        current = self._by_subject.get(subject_id)
        return current is not None and current.revoked_at is not None

    def get(self, subject_id: str) -> Optional[Consent]:
        """Return the subject's current consent record, if any."""
        return self._by_subject.get(subject_id)

    def _publish_revoked(self, consent: Consent) -> None:
        """Emit the ``consent_revoked`` event (no-op when no bus is wired)."""
        if self._event_bus is None:
            return
        self._event_bus.publish(
            EVENT_CONSENT_REVOKED,
            {
                "subject_id": consent.subject_id,
                "subject_type": consent.subject_type,
                "consent_id": consent.consent_id,
                "revoked_at": consent.revoked_at,
            },
        )


# --------------------------------------------------------------------------- #
# DB-backed implementation (optional; uses the ``consent`` ORM table)
# --------------------------------------------------------------------------- #
class DbConsentService:
    """``ConsentStore`` backed by the ``consent`` table (optional).

    Mirrors :class:`InMemoryConsentService` over the ORM in
    :mod:`pulselink.common.db_models`, following the session-injection pattern
    used elsewhere (e.g. :class:`pulselink.common.audit.DbAuditLog`). A caller
    supplies a SQLAlchemy ``Session``; transaction commit remains the caller's
    responsibility. This variant is optional and is not exercised by the
    offline test suite.
    """

    def __init__(self, session, *, event_bus: Optional[EventBus] = None, flush: bool = True) -> None:
        if session is None:
            raise ValueError("DbConsentService requires a SQLAlchemy session")
        self._session = session
        self._event_bus = event_bus
        self._flush = flush

    def grant(
        self,
        subject_id: str,
        subject_type: SubjectType,
        scopes: list[ConsentScope],
        version: str,
    ) -> Consent:
        """Insert a versioned consent row and return the domain record."""
        from pulselink.common.db_models import Consent as ConsentORM

        consent = _new_consent(subject_id, subject_type, scopes, version)
        self._session.add(
            ConsentORM(
                consent_id=consent.consent_id,
                subject_id=consent.subject_id,
                subject_type=consent.subject_type,
                scopes=[s.value for s in consent.scopes],
                version=consent.version,
                granted_at=consent.granted_at,
                revoked_at=consent.revoked_at,
            )
        )
        if self._flush:
            self._session.flush()
        return consent

    def revoke(self, subject_id: str) -> Optional[Consent]:
        """Stamp ``revoked_at`` on the subject's latest consent and cascade."""
        row = self._latest_row(subject_id)
        if row is None or row.revoked_at is not None:
            return self._to_domain(row) if row is not None else None

        row.revoked_at = datetime.now(timezone.utc)
        if self._flush:
            self._session.flush()
        consent = self._to_domain(row)
        self._publish_revoked(consent)
        return consent

    def has_active_scope(self, subject_id: str, scope: ConsentScope) -> bool:
        """The single check matching / messaging / voice consult (Req 7.2)."""
        consent = self.get(subject_id)
        return consent is not None and consent.has_active_scope(scope)

    def is_revoked(self, subject_id: str) -> bool:
        """True when the subject's latest consent has been revoked."""
        consent = self.get(subject_id)
        return consent is not None and consent.revoked_at is not None

    def get(self, subject_id: str) -> Optional[Consent]:
        """Return the subject's latest consent record, if any."""
        row = self._latest_row(subject_id)
        return self._to_domain(row) if row is not None else None

    def _latest_row(self, subject_id: str):
        from pulselink.common.db_models import Consent as ConsentORM

        return (
            self._session.query(ConsentORM)
            .filter(ConsentORM.subject_id == subject_id)
            .order_by(ConsentORM.granted_at.desc())
            .first()
        )

    @staticmethod
    def _to_domain(row) -> Consent:
        return Consent(
            consent_id=row.consent_id,
            subject_id=row.subject_id,
            subject_type=row.subject_type,
            scopes=[ConsentScope(s) for s in (row.scopes or [])],
            granted_at=row.granted_at,
            revoked_at=row.revoked_at,
            version=row.version,
        )

    def _publish_revoked(self, consent: Consent) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            EVENT_CONSENT_REVOKED,
            {
                "subject_id": consent.subject_id,
                "subject_type": consent.subject_type,
                "consent_id": consent.consent_id,
                "revoked_at": consent.revoked_at,
            },
        )
