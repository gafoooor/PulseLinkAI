"""Unit tests for the gateway RBAC seam (Task 13.3).

Covers the role-scoped access rules (Requirements 7.5, 10.2):
* a patient can view only their own patient record (denied for others),
* a donor can view only their own assigned slot (denied for others),
* a coordinator can view patients/slots only within their assigned city
  (denied cross-city),
* ``can_view_donor_contact`` is allowed only for a coordinator, and only when
  the slot is active and consent is active,
* the ``require(...)`` guard raises ``AccessDenied`` (HTTP 403) on the deny path,
* the ``get_principal`` header stub builds a principal and rejects bad input.

Requirements: 7.5, 10.2
"""

from __future__ import annotations

import pytest

from pulselink.common.enums import AssignmentStatus, BloodGroup
from pulselink.common.models import Patient, SlotAssignment
from pulselink.gateway.rbac import (
    AccessDenied,
    Principal,
    Role,
    can_view_donor_contact,
    can_view_patient,
    can_view_slot_assignment,
    get_principal,
    require,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def make_patient(patient_id: str = "p1", city_id: str = "hyd") -> Patient:
    return Patient(
        patient_id=patient_id,
        city_id=city_id,
        blood_group=BloodGroup.O_POSITIVE,
        quantity_required=1,
        cadence_days=21,
        consent_id="c1",
    )


def make_assignment(donor_id: str = "d1", slot_id: str = "s1") -> SlotAssignment:
    return SlotAssignment(
        assignment_id="a1",
        slot_id=slot_id,
        donor_id=donor_id,
        rank=0,
        status=AssignmentStatus.ACTIVE,
    )


# --------------------------------------------------------------------------- #
# Patient viewing their own record
# --------------------------------------------------------------------------- #
def test_patient_can_view_own_record():
    patient = make_patient(patient_id="p1")
    principal = Principal(role=Role.PATIENT, subject_id="p1")
    assert can_view_patient(principal, patient) is True


def test_patient_cannot_view_other_patient_record():
    patient = make_patient(patient_id="p2")
    principal = Principal(role=Role.PATIENT, subject_id="p1")
    assert can_view_patient(principal, patient) is False


def test_donor_cannot_view_patient_record():
    patient = make_patient(patient_id="p1")
    principal = Principal(role=Role.DONOR, subject_id="d1")
    assert can_view_patient(principal, patient) is False


# --------------------------------------------------------------------------- #
# Donor viewing their own assigned slot
# --------------------------------------------------------------------------- #
def test_donor_can_view_own_assignment():
    assignment = make_assignment(donor_id="d1")
    principal = Principal(role=Role.DONOR, subject_id="d1")
    assert can_view_slot_assignment(principal, assignment) is True


def test_donor_cannot_view_other_donor_assignment():
    assignment = make_assignment(donor_id="d2")
    principal = Principal(role=Role.DONOR, subject_id="d1")
    assert can_view_slot_assignment(principal, assignment) is False


def test_patient_can_view_own_slot_assignment_but_not_others():
    patient = make_patient(patient_id="p1")
    assignment = make_assignment(donor_id="d9")
    own = Principal(role=Role.PATIENT, subject_id="p1")
    other = Principal(role=Role.PATIENT, subject_id="p2")
    assert can_view_slot_assignment(own, assignment, patient=patient) is True
    assert can_view_slot_assignment(other, assignment, patient=patient) is False


def test_patient_assignment_denied_without_patient_context():
    assignment = make_assignment(donor_id="d9")
    principal = Principal(role=Role.PATIENT, subject_id="p1")
    assert can_view_slot_assignment(principal, assignment) is False


# --------------------------------------------------------------------------- #
# Coordinator city scoping (Req 10.2)
# --------------------------------------------------------------------------- #
def test_coordinator_can_view_patient_in_their_city():
    patient = make_patient(city_id="hyd")
    principal = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id="hyd")
    assert can_view_patient(principal, patient) is True


def test_coordinator_cannot_view_patient_in_other_city():
    patient = make_patient(city_id="blr")
    principal = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id="hyd")
    assert can_view_patient(principal, patient) is False


def test_coordinator_can_view_slot_in_their_city_but_not_cross_city():
    same_city = make_patient(patient_id="p1", city_id="hyd")
    other_city = make_patient(patient_id="p2", city_id="blr")
    assignment = make_assignment(donor_id="d1")
    principal = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id="hyd")
    assert can_view_slot_assignment(principal, assignment, patient=same_city) is True
    assert can_view_slot_assignment(principal, assignment, patient=other_city) is False


def test_coordinator_without_assigned_city_is_denied():
    patient = make_patient(city_id="hyd")
    principal = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id=None)
    assert can_view_patient(principal, patient) is False


# --------------------------------------------------------------------------- #
# Donor contact visibility (coordinator-only + active + consented)
# --------------------------------------------------------------------------- #
def test_can_view_donor_contact_only_for_coordinator_when_active_and_consented():
    coordinator = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id="hyd")
    assert (
        can_view_donor_contact(coordinator, slot_active=True, has_active_consent=True)
        is True
    )


def test_can_view_donor_contact_denied_when_not_active_or_not_consented():
    coordinator = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id="hyd")
    assert (
        can_view_donor_contact(coordinator, slot_active=False, has_active_consent=True)
        is False
    )
    assert (
        can_view_donor_contact(coordinator, slot_active=True, has_active_consent=False)
        is False
    )


def test_can_view_donor_contact_denied_for_non_coordinators():
    patient = Principal(role=Role.PATIENT, subject_id="p1")
    donor = Principal(role=Role.DONOR, subject_id="d1")
    assert (
        can_view_donor_contact(patient, slot_active=True, has_active_consent=True)
        is False
    )
    assert (
        can_view_donor_contact(donor, slot_active=True, has_active_consent=True)
        is False
    )


# --------------------------------------------------------------------------- #
# require(...) guard raises on the deny path
# --------------------------------------------------------------------------- #
def test_require_raises_access_denied_on_deny():
    patient = make_patient(patient_id="p2")
    principal = Principal(role=Role.PATIENT, subject_id="p1")
    with pytest.raises(AccessDenied) as exc_info:
        require(can_view_patient(principal, patient), "not your patient")
    assert exc_info.value.status_code == 403
    assert isinstance(exc_info.value, PermissionError)


def test_require_passes_silently_when_allowed():
    patient = make_patient(patient_id="p1")
    principal = Principal(role=Role.PATIENT, subject_id="p1")
    # Should not raise.
    require(can_view_patient(principal, patient))


# --------------------------------------------------------------------------- #
# get_principal header stub
# --------------------------------------------------------------------------- #
def test_get_principal_builds_principal_from_headers():
    principal = get_principal(x_role="coordinator", x_subject_id="staff1", x_city_id="hyd")
    assert principal.role is Role.COORDINATOR
    assert principal.subject_id == "staff1"
    assert principal.city_id == "hyd"


def test_get_principal_rejects_missing_context():
    with pytest.raises(AccessDenied):
        get_principal(x_role=None, x_subject_id="p1")
    with pytest.raises(AccessDenied):
        get_principal(x_role="patient", x_subject_id=None)


def test_get_principal_rejects_unknown_role():
    with pytest.raises(AccessDenied):
        get_principal(x_role="admin", x_subject_id="x1")
