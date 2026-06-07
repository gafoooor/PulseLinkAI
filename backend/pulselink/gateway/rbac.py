"""Role-based access control (RBAC) seam for PulseLink (Task 13.3).

This module is the single place that decides *who may see what*. In production
it maps to **Amazon API Gateway + Lambda authorizers + IAM**: the authorizer
authenticates the caller and populates a request context with the caller's role
and identity, and IAM/role mappings enforce coarse access. For the offline demo
this is a **local stub** — the ``Principal`` is built from request headers
(``X-Role`` / ``X-Subject-Id`` / ``X-City-Id``) by the :func:`get_principal`
FastAPI dependency — but the *authorization rules* below are the real ones the
production authorizer would delegate to.

Access rules (Requirements 7.5, 10.2):

* **Patient** — sees only their own data: their own patient record and only the
  slots belonging to their own patient.
* **Donor** — sees only their own assigned slots (an assignment is theirs when
  ``assignment.donor_id == principal.subject_id``); donors never view patient
  records.
* **Coordinator** — sees patients and slots **only within their assigned city**
  (``principal.city_id == patient.city_id``); city scoping (Req 10.2) is kept
  central here. A coordinator may view a donor's contact only when a slot is
  active **and** the donor's ``contact_for_slots`` consent is active (the consent
  gate itself lives in tasks 13.1 / 9.3).

Denied access raises :class:`AccessDenied`, a ``PermissionError`` subclass that
also exposes an HTTP 403 status so callers can surface it as a forbidden
response.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from pulselink.common.models import Patient, SlotAssignment


# --------------------------------------------------------------------------- #
# Role + Principal (authenticated caller / AuthContext)
# --------------------------------------------------------------------------- #
class Role(str, Enum):
    """The three PulseLink caller roles enforced at the gateway."""

    PATIENT = "patient"
    DONOR = "donor"
    COORDINATOR = "coordinator"


class Principal(BaseModel):
    """The authenticated caller (a.k.a. AuthContext).

    In production this is populated by the API Gateway Lambda authorizer from
    the verified token claims; in the demo it is built from request headers by
    :func:`get_principal`.

    * ``role``       — the caller's role.
    * ``subject_id`` — the caller's identity (``patient_id`` for a patient,
      ``donor_id`` for a donor, a staff user id for a coordinator).
    * ``city_id``    — the coordinator's assigned city; required for coordinators
      so that access is always city-scoped (Req 10.2). Patients/donors may leave
      it unset.
    """

    role: Role
    subject_id: str = Field(min_length=1)
    city_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class AccessDenied(PermissionError):
    """Raised when a principal is not authorized for an action/resource.

    Subclasses ``PermissionError`` (so plain ``except PermissionError`` works)
    and carries ``status_code = 403`` so a FastAPI handler can translate it into
    an HTTP 403 Forbidden response.
    """

    status_code: int = 403

    def __init__(self, message: str = "Access denied") -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------- #
# Authorization checks
# --------------------------------------------------------------------------- #
def _coordinator_in_city(principal: Principal, city_id: Optional[str]) -> bool:
    """Central city-scoping rule (Req 10.2): a coordinator is bound to one city.

    Returns ``True`` only when the principal is a coordinator with an assigned
    city that matches the resource's city. A coordinator with no assigned city
    is never granted city-scoped access.
    """

    return (
        principal.role is Role.COORDINATOR
        and principal.city_id is not None
        and principal.city_id == city_id
    )


def can_view_patient(principal: Principal, patient: Patient) -> bool:
    """Whether ``principal`` may view ``patient`` (Req 7.5, 10.2).

    * Patient: only their own record (``subject_id == patient.patient_id``).
    * Coordinator: only patients within their assigned city.
    * Donor: never.
    """

    if principal.role is Role.PATIENT:
        return principal.subject_id == patient.patient_id
    if principal.role is Role.COORDINATOR:
        return _coordinator_in_city(principal, patient.city_id)
    # Donors do not view patient records.
    return False


def can_view_slot_assignment(
    principal: Principal,
    assignment: SlotAssignment,
    patient: Optional[Patient] = None,
) -> bool:
    """Whether ``principal`` may view a slot ``assignment`` (Req 7.5, 10.2).

    * Donor: only their own assigned slots (``assignment.donor_id == subject_id``).
    * Patient: only assignments on their own patient's slots — requires the
      owning ``patient`` so ownership can be checked.
    * Coordinator: only within their assigned city — requires the owning
      ``patient`` so the city can be checked.

    When the owning ``patient`` is needed (patient/coordinator) but not provided,
    access is denied rather than assumed.
    """

    if principal.role is Role.DONOR:
        return assignment.donor_id == principal.subject_id
    if principal.role is Role.PATIENT:
        return patient is not None and principal.subject_id == patient.patient_id
    if principal.role is Role.COORDINATOR:
        return patient is not None and _coordinator_in_city(principal, patient.city_id)
    return False


def can_view_donor_contact(
    principal: Principal,
    *,
    slot_active: bool,
    has_active_consent: bool,
) -> bool:
    """Whether ``principal`` may view a donor's contact details (Req 7.5).

    Only a coordinator may ever view donor contact, and only when the relevant
    slot is active **and** the donor holds an active ``contact_for_slots``
    consent. The consent record/lookup itself lives in tasks 13.1 / 9.3; this
    function takes the already-resolved facts as flags and applies the access
    rule.
    """

    return principal.role is Role.COORDINATOR and slot_active and has_active_consent


def require(allowed: bool, message: str = "Access denied") -> None:
    """Raise :class:`AccessDenied` when ``allowed`` is falsy.

    Lets call sites read as a guard, e.g.::

        require(can_view_patient(principal, patient), "not your patient")
    """

    if not allowed:
        raise AccessDenied(message)


# --------------------------------------------------------------------------- #
# FastAPI dependency (demo stub for the API Gateway Lambda authorizer)
# --------------------------------------------------------------------------- #
def get_principal(
    x_role: Optional[str] = None,
    x_subject_id: Optional[str] = None,
    x_city_id: Optional[str] = None,
) -> Principal:
    """Build the :class:`Principal` from request headers (demo stub).

    Wire it into a FastAPI route with ``Header`` aliases, e.g.::

        from fastapi import Depends, Header
        from pulselink.gateway.rbac import Principal, get_principal

        def principal_dep(
            x_role: str | None = Header(default=None),
            x_subject_id: str | None = Header(default=None),
            x_city_id: str | None = Header(default=None),
        ) -> Principal:
            return get_principal(x_role, x_subject_id, x_city_id)

        @app.get("/patients/{patient_id}")
        def read_patient(principal: Principal = Depends(principal_dep)):
            ...

    PRODUCTION: this header stub is replaced by an **API Gateway Lambda
    authorizer** that verifies the caller's token and injects the role/identity
    into the request context; the rules in this module stay unchanged.

    Raises :class:`AccessDenied` (HTTP 403) when the role/subject are missing or
    the role is not one of the known PulseLink roles.
    """

    if not x_role or not x_subject_id:
        raise AccessDenied("Missing authentication context (role/subject)")

    try:
        role = Role(x_role.strip().lower())
    except ValueError as exc:
        valid = ", ".join(r.value for r in Role)
        raise AccessDenied(f"Unknown role {x_role!r}; expected one of: {valid}") from exc

    return Principal(role=role, subject_id=x_subject_id, city_id=x_city_id)
