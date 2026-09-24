from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pumpcopilot.schema import (
    CalibrationStatus,
    EvidenceItem,
    FeatureWindow,
    PresentationState,
    QualityFlag,
    ScoredEvidence,
    SourceDataset,
    TelemetryEvent,
)

T0 = datetime(2024, 4, 10, 8, tzinfo=UTC)
WIN = FeatureWindow(start=T0, end=T0 + timedelta(minutes=5))


def _evidence(**kw):
    base = dict(
        asset_id="cira-pump-A", source_dataset=SourceDataset.CIRA, model_id="robust-z",
        model_version="0.1.0", model_domain=SourceDataset.CIRA, feature_window=WIN,
        confidence_calibration_status=CalibrationStatus.NOT_APPLICABLE,
        presentation_state=PresentationState.NORMAL,
    )
    return ScoredEvidence(**{**base, **kw})


def test_zema_model_cannot_score_cira():
    with pytest.raises(ValidationError, match="may not score"):
        _evidence(model_domain=SourceDataset.ZEMA)


def test_abstention_requires_reason():
    with pytest.raises(ValidationError, match="abstention_reason"):
        _evidence(presentation_state=PresentationState.INSUFFICIENT_EVIDENCE)
    ok = _evidence(presentation_state=PresentationState.DATA_UNAVAILABLE,
                   abstention_reason="no samples in window")
    assert ok.score is None


def test_review_requires_evidence():
    with pytest.raises(ValidationError, match="evidence"):
        _evidence(presentation_state=PresentationState.REVIEW_SUGGESTED)
    ev = EvidenceItem(ref="E1", signal_name="pump_vibration",
                      statement="above asset baseline band for 6 consecutive windows")
    assert _evidence(presentation_state=PresentationState.REVIEW_SUGGESTED, evidence=(ev,))


def test_no_alarm_state_exists():
    assert "alarm" not in {s.value for s in PresentationState}


def test_zema_output_keeps_bench_label():
    kw = dict(asset_id="zema-test-rig", source_dataset=SourceDataset.ZEMA,
              model_domain=SourceDataset.ZEMA)
    with pytest.raises(ValidationError, match="bench label"):
        _evidence(**kw, output_label="severe leakage")
    assert _evidence(**kw, output_label="hydraulic test rig pump leakage state: 2")


def test_event_rules():
    base = dict(asset_id="a", source_dataset="cira", source_file="A_2024-04-10.csv",
                sample_id="x", signal_name="s", unit="u", provenance_hash="0" * 64)
    with pytest.raises(ValidationError, match="timezone"):
        TelemetryEvent(**base, observed_at=datetime(2024, 1, 1), value=1.0)
    with pytest.raises(ValidationError, match="missing"):
        TelemetryEvent(**base, observed_at=T0, value=None)
    TelemetryEvent(**base, observed_at=T0, value=None, quality_flags=(QualityFlag.MISSING,))
