"""ML-based donor reliability ranking using scikit-learn (Task 6.4).

This module extends the deterministic scoring baseline (``scoring.py``) with an
optional gradient-boosted ranking layer. It is designed to work entirely offline
and degrades gracefully when scikit-learn is not installed.

Self-supervised training
------------------------
Because PulseLink has no human-labelled "good donor" ground truth, training
labels are generated from the same dataset signals the deterministic scorer
already uses — a technique sometimes called *self-supervised* or
*pseudo-label* training:

    label = 0.50 * call_efficiency + 0.30 * volume_factor + 0.20 * recency

This is a lighter blend than the full reliability_score formula: it
deliberately omits the acceptance-ratio signal (which requires offer history
the seed data does not always have) and re-weights the remaining three factors
to emphasise call efficiency.  The gradient-boosted regressor then learns a
non-linear mapping from the five features to this label, potentially capturing
interaction effects that the linear baseline misses.

Features
--------
Each donor is represented by five normalised scalars (all in ``[0, 1]``):

1. ``call_efficiency``  — ``1 / max(1, calls_to_donations_ratio)``
2. ``volume_factor``    — ``min(1, donations_till_date / 10)``
3. ``recency``          — exponential-decay factor on days since last donation
4. ``blood_match``      — 1.0 (same group), 0.8 (compatible), 0.0 (incompatible)
5. ``dist_score``       — ``1 / (1 + distance_km)``

Fallback behaviour
------------------
* If scikit-learn is not installed, :class:`MLDonorRanker` falls back to the
  self-supervised label formula for ranking — no crash, no silent wrong result.
* If fewer than 5 donors are available for training, fitting is skipped and the
  fallback is used automatically.
* Any prediction-time exception (e.g. a mismatched scaler) also triggers the
  fallback transparently.

Requirements: 3.1, 3.2, 4.3
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from pulselink.common.enums import BloodGroup
from pulselink.reliability.scoring import DonorStats, reliability_score

# --------------------------------------------------------------------------- #
# Blood-match score
# --------------------------------------------------------------------------- #

def blood_match_score(donor_group: BloodGroup, patient_group: BloodGroup) -> float:
    """Return a normalised blood-compatibility score in ``{0.0, 0.8, 1.0}``.

    * ``1.0`` — donor and patient share the same blood group.
    * ``0.8`` — donor is compatible with the patient but groups differ (e.g.
      O- donating to A+).
    * ``0.0`` — donor is not compatible with the patient at all.

    The ``blood_compatible`` helper is imported lazily inside the function to
    avoid a circular import (``matching.match`` imports from
    ``reliability.scoring``; importing it at module level here would create a
    cycle).
    """
    if donor_group == patient_group:
        return 1.0

    # Lazy import to avoid circular dependency with pulselink.matching.match
    from pulselink.matching.match import blood_compatible  # noqa: PLC0415

    if blood_compatible(donor_group, patient_group):
        return 0.8

    return 0.0


# --------------------------------------------------------------------------- #
# Distance score
# --------------------------------------------------------------------------- #

def distance_score(distance_km: float) -> float:
    """Map a distance in km to a normalised score in ``(0, 1]``.

    Uses the reciprocal transform ``1 / (1 + distance_km)`` so that:

    * ``0 km``  -> ``1.0`` (same location, maximum score)
    * ``99 km`` -> ``~0.01`` (very far away, low score)

    Returns ``0.0`` for non-finite values (``math.inf``, ``math.nan``) or
    negative distances, which indicate missing or invalid coordinates.
    """
    if not math.isfinite(distance_km) or distance_km < 0:
        return 0.0
    return 1.0 / (1.0 + distance_km)


# --------------------------------------------------------------------------- #
# Feature container
# --------------------------------------------------------------------------- #

@dataclass
class DonorFeatures:
    """All ML features for a single donor candidate, each in ``[0, 1]``.

    Attributes
    ----------
    donor_id:
        Opaque string identifier — passed through for result correlation.
    call_efficiency:
        ``1 / max(1, calls_to_donations_ratio)`` from scoring.
    volume_factor:
        ``min(1, donations_till_date / 10)`` from scoring.
    recency:
        Exponential-decay recency factor from scoring.
    blood_match:
        Blood-group compatibility score — 1.0 / 0.8 / 0.0.
    dist_score:
        Proximity score ``1 / (1 + distance_km)``.
    """

    donor_id: str
    call_efficiency: float
    volume_factor: float
    recency: float
    blood_match: float
    dist_score: float

    def to_array(self) -> list[float]:
        """Return the five numeric features as an ordered list.

        Order: ``[call_efficiency, volume_factor, recency, blood_match,
        dist_score]``.  This fixed order must be preserved across
        :meth:`fit` and :meth:`predict` so the scaler and model see
        consistent column assignments.
        """
        return [
            self.call_efficiency,
            self.volume_factor,
            self.recency,
            self.blood_match,
            self.dist_score,
        ]


# --------------------------------------------------------------------------- #
# Feature builder
# --------------------------------------------------------------------------- #

def build_donor_features(
    donor_id: str,
    stats: DonorStats,
    donor_blood_group: BloodGroup,
    patient_blood_group: BloodGroup,
    dist_km: float,
    today: date,
) -> DonorFeatures:
    """Construct a :class:`DonorFeatures` instance for a single donor.

    Delegates all scoring arithmetic to :func:`~pulselink.reliability.scoring.reliability_score`
    so the same normalisation logic is reused without duplication.

    Parameters
    ----------
    donor_id:
        Opaque donor identifier threaded through for result correlation.
    stats:
        Historical donation signals (see :class:`~pulselink.reliability.scoring.DonorStats`).
    donor_blood_group:
        The donor's ABO/Rh blood group.
    patient_blood_group:
        The patient's ABO/Rh blood group for the slot being matched.
    dist_km:
        Great-circle distance from donor to patient in km.  Pass
        ``math.inf`` when coordinates are unknown.
    today:
        Reference date for recency decay computation.

    Returns
    -------
    DonorFeatures
        A fully populated feature container ready for :class:`MLDonorRanker`.
    """
    result = reliability_score(stats, today)
    components = result["components"]

    return DonorFeatures(
        donor_id=donor_id,
        call_efficiency=components["callEfficiency"],
        volume_factor=components["volumeFactor"],
        recency=components["recencyFactor"],
        blood_match=blood_match_score(donor_blood_group, patient_blood_group),
        dist_score=distance_score(dist_km),
    )


# --------------------------------------------------------------------------- #
# Self-supervised label
# --------------------------------------------------------------------------- #

def _self_supervised_label(f: DonorFeatures) -> float:
    """Generate a pseudo training label from donor features (no human labels).

    This function implements the self-supervised labelling strategy: the label
    is derived entirely from observable dataset signals, avoiding any need for
    manual annotation.

    Formula::

        label = 0.50 * call_efficiency + 0.30 * volume_factor + 0.20 * recency

    The acceptance-ratio signal is omitted intentionally — it requires offer
    history that is absent in the seed dataset for many donors. The three
    retained signals are re-weighted to emphasise call efficiency (the clearest
    behavioural proxy for reliability) while still rewarding donation volume and
    recency.
    """
    return (
        0.50 * f.call_efficiency
        + 0.30 * f.volume_factor
        + 0.20 * f.recency
    )


# --------------------------------------------------------------------------- #
# ML ranker
# --------------------------------------------------------------------------- #

class MLDonorRanker:
    """Gradient-boosted donor ranker with transparent scikit-learn fallback.

    Training uses self-supervised labels (see :func:`_self_supervised_label`).
    When sklearn is unavailable or the training set is too small the ranker
    falls back to the label formula for prediction, preserving a consistent
    interface in all deployment environments.

    Parameters
    ----------
    n_estimators:
        Number of boosting stages for :class:`~sklearn.ensemble.GradientBoostingRegressor`.
    max_depth:
        Maximum depth of each individual regression tree.
    """

    def __init__(self, n_estimators: int = 50, max_depth: int = 3) -> None:
        self._n_estimators = n_estimators
        self._max_depth = max_depth
        self._fitted: bool = False
        self._scaler: object = None
        self._model: object = None

    # ---------------------------------------------------------------------- #
    # Training
    # ---------------------------------------------------------------------- #

    def fit(self, features: list[DonorFeatures]) -> "MLDonorRanker":
        """Fit the scaler and gradient-boosted model on ``features``.

        If fewer than 5 donors are supplied (too small to produce a meaningful
        model) or if scikit-learn is not importable, ``_fitted`` is set to
        ``False`` and the instance will fall back to the label formula during
        :meth:`predict`.

        Parameters
        ----------
        features:
            A list of :class:`DonorFeatures` instances representing the donor
            population to train on.

        Returns
        -------
        self
            Returns ``self`` to allow chaining (``ranker.fit(...).predict(...)``).
        """
        if len(features) < 5:
            self._fitted = False
            return self

        try:
            from sklearn.ensemble import GradientBoostingRegressor  # noqa: PLC0415
            from sklearn.preprocessing import StandardScaler  # noqa: PLC0415
        except ImportError:
            self._fitted = False
            return self

        X = [f.to_array() for f in features]
        y = [_self_supervised_label(f) for f in features]

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        model = GradientBoostingRegressor(
            n_estimators=self._n_estimators,
            max_depth=self._max_depth,
            random_state=42,
            learning_rate=0.1,
        )
        model.fit(X_scaled, y)

        self._scaler = scaler
        self._model = model
        self._fitted = True
        return self

    # ---------------------------------------------------------------------- #
    # Prediction
    # ---------------------------------------------------------------------- #

    def predict(
        self, features: list[DonorFeatures]
    ) -> list[tuple[str, float]]:
        """Return ``(donor_id, score)`` pairs sorted by score descending.

        If the model is fitted it applies the scaler and gradient-boosted
        regressor. Any exception during prediction (e.g. a feature-count
        mismatch) triggers the self-supervised label fallback transparently so
        callers always receive a valid ranking.

        If the model is not fitted the fallback is used directly.

        Parameters
        ----------
        features:
            Donor feature vectors to score and rank.

        Returns
        -------
        list[tuple[str, float]]
            ``(donor_id, score)`` pairs in descending score order.
        """
        if not self._fitted:
            return _fallback_rank(features)

        try:
            X = [f.to_array() for f in features]
            X_scaled = self._scaler.transform(X)  # type: ignore[union-attr]
            scores: list[float] = self._model.predict(X_scaled).tolist()  # type: ignore[union-attr]
            ranked = sorted(
                zip((f.donor_id for f in features), scores),
                key=lambda t: t[1],
                reverse=True,
            )
            return list(ranked)
        except Exception:  # noqa: BLE001 — transparent fallback
            return _fallback_rank(features)


# --------------------------------------------------------------------------- #
# Fallback helper (module-private)
# --------------------------------------------------------------------------- #

def _fallback_rank(features: list[DonorFeatures]) -> list[tuple[str, float]]:
    """Rank donors using the self-supervised label formula (no sklearn needed)."""
    scored = [(f.donor_id, _self_supervised_label(f)) for f in features]
    return sorted(scored, key=lambda t: t[1], reverse=True)


# --------------------------------------------------------------------------- #
# Convenience function
# --------------------------------------------------------------------------- #

def rank_donors_ml(donors_with_features: list[DonorFeatures]) -> list[str]:
    """Fit an :class:`MLDonorRanker` and return donor IDs in ranked order.

    This is a single-call convenience wrapper for callers that do not need
    access to the ranker instance or per-donor scores.  It trains on the same
    population it ranks, which is appropriate for the self-supervised setting
    where no held-out labels exist.

    Parameters
    ----------
    donors_with_features:
        Feature vectors for all candidate donors (training **and** scoring set).

    Returns
    -------
    list[str]
        Donor IDs sorted from most to least reliable according to the ML model
        (or the fallback formula when the model cannot be fitted).
    """
    ranker = MLDonorRanker()
    ranker.fit(donors_with_features)
    ranked_pairs = ranker.predict(donors_with_features)
    return [donor_id for donor_id, _ in ranked_pairs]
