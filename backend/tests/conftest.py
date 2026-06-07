"""Pytest + Hypothesis wiring — the single property-test toolchain for PulseLink.

Registers Hypothesis settings profiles used across all services' property
tests (added in later tasks). ``ci`` runs more examples; ``dev`` is the fast
default.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

settings.register_profile("dev", max_examples=50)
settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Default to the fast development profile.
settings.load_profile("dev")
