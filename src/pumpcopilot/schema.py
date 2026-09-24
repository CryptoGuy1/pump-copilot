"""Canonical data contract.

Two record shapes, deliberately:

* ``TelemetryEvent`` - narrow, one value per row. Used for CIRA (low-rate plant telemetry),
  and for anything the replay worker streams.
* ``CycleRecord`` - one row per ZeMA 60 s cycle. The 100 Hz arrays live in Parquet/NPZ keyed
  by ``cycle_id``; exploding ZeMA into narrow rows would be ~96M rows for no benefit.

Scope guardrails are enforced here as validation errors so they cannot be forgotten later:
a model trained on one dataset cannot score another, abstention requires a reason, and
anomaly scores can never become a safety alarm (there is no alarm state to choose).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceDataset(StrEnum):
    ZEMA = "zema"
    CIRA = "cira"


class OperatingState(StrEnum):
    RUNNING = "running"
    OFF = "off"
    TRANSITION = "transition"  # start or stop
    UNKNOWN = "unknown"


class QualityFlag(StrEnum):
    MISSING = "missing"
    OUT_OF_RANGE = "out_of_range"
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    NON_MONOTONIC = "non_monotonic"
    GAP_BEFORE = "gap_before"
    PLACEHOLDER_SUSPECTED = "placeholder_suspected"
    UNIT_UNVERIFIED = "unit_unverified"


class PresentationState(StrEnum):
    """The only four states the UI may show. There is intentionally no 'alarm'."""

    NORMAL = "normal"
    REVIEW_SUGGESTED = "review_suggested"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DATA_UNAVAILABLE = "data_unavailable"


class CalibrationStatus(StrEnum):
    CALIBRATED = "calibrated"  # probability calibration measured on held-out data
    UNCALIBRATED = "uncalibrated"
    NOT_APPLICABLE = "not_applicable"  # e.g. robust z-scores, no probability claim


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_tz(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return v


class TelemetryEvent(_Frozen):
    asset_id: str
    source_dataset: SourceDataset
    source_file: str
    source_day: str | None = None  # CIRA: separate days are never concatenated
    observed_at: datetime
    replayed_at: datetime | None = None
    sample_id: str
    signal_name: str
    value: float | None
    unit: str
    operating_state: OperatingState = OperatingState.UNKNOWN
    quality_flags: tuple[QualityFlag, ...] = ()
    provenance_hash: str = Field(min_length=16)

    _tz = field_validator("observed_at", "replayed_at")(_require_tz)

    @model_validator(mode="after")
    def _missing_needs_flag(self) -> TelemetryEvent:
        if self.value is None and QualityFlag.MISSING not in self.quality_flags:
            raise ValueError("value is None but 'missing' quality flag is not set")
        return self


class ChannelMeta(_Frozen):
    sample_hz: float
    n_samples: int
    unit: str
    virtual: bool = False  # CE, CP, SE are computed, not measured


class CycleLabels(_Frozen):
    """ZeMA profile.txt, all five columns preserved. Only pump_leakage is a target."""

    cooler_pct: Literal[3, 20, 100]
    valve_pct: Literal[100, 90, 80, 73]
    pump_leakage: Literal[0, 1, 2]
    accumulator_bar: Literal[130, 115, 100, 90]
    stable_flag: Literal[0, 1]


class CycleRecord(_Frozen):
    source_dataset: Literal[SourceDataset.ZEMA] = SourceDataset.ZEMA
    asset_id: str = "zema-test-rig"
    cycle_id: int = Field(ge=0)
    channels: dict[str, ChannelMeta]
    labels: CycleLabels
    provenance_hash: str = Field(min_length=16)


class EvidenceItem(_Frozen):
    ref: str  # e.g. "E1"; the assistant must cite these ids
    signal_name: str
    statement: str  # a measured observation, never a diagnosis
    value: float | None = None
    baseline_low: float | None = None
    baseline_high: float | None = None
    unit: str | None = None


class FeatureWindow(_Frozen):
    start: datetime
    end: datetime

    _tz = field_validator("start", "end")(_require_tz)

    @model_validator(mode="after")
    def _ordered(self) -> FeatureWindow:
        if self.end <= self.start:
            raise ValueError("feature window end must be after start")
        return self


class ScoredEvidence(_Frozen):
    asset_id: str
    source_dataset: SourceDataset
    model_id: str
    model_version: str
    model_domain: SourceDataset  # dataset the model was fit on
    feature_window: FeatureWindow
    evidence: tuple[EvidenceItem, ...] = ()
    score: float | None = None
    confidence_calibration_status: CalibrationStatus
    presentation_state: PresentationState
    abstention_reason: str | None = None
    output_label: str | None = None  # e.g. "hydraulic test rig pump leakage state: 2"

    @model_validator(mode="after")
    def _guardrails(self) -> ScoredEvidence:
        if self.model_domain != self.source_dataset:
            raise ValueError(
                f"model fit on {self.model_domain} may not score {self.source_dataset} data"
            )
        abstaining = self.presentation_state in (
            PresentationState.INSUFFICIENT_EVIDENCE,
            PresentationState.DATA_UNAVAILABLE,
        )
        if abstaining and not self.abstention_reason:
            raise ValueError("abstaining states require an abstention_reason")
        if self.presentation_state == PresentationState.REVIEW_SUGGESTED and not self.evidence:
            raise ValueError("review_suggested requires at least one evidence item")
        if (
            self.source_dataset == SourceDataset.ZEMA
            and self.output_label is not None
            and not self.output_label.startswith("hydraulic test rig pump leakage state")
        ):
            raise ValueError("ZeMA outputs must keep the bench label")
        return self
