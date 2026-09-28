"""Response models for every API endpoint (Step 5a-2: the contract).

Every model forbids undeclared fields, so a response that drifts from its model fails
FastAPI's response validation (a 500 in the one error format) instead of changing the
contract silently. api/openapi.json is generated from these, and the web client's types are
generated from it.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from .schema import ScoredEvidence

PresentationState = Literal["normal", "review_suggested", "insufficient_evidence",
                            "data_unavailable"]
SessionStatus = Literal["pending", "running", "paused", "completed", "failed"]
CaseStatus = Literal["open", "acknowledged", "dispositioned", "closed"]
CaseAction = Literal["acknowledge", "note", "disposition", "close", "export"]
BaselineStatus = Literal["waiting_for_reading", "settling", "forming", "formed", "abstained"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(Model):
    synthetic: bool = Field(description="true if the data comes from a synthetic scenario")
    model_version: list[str] = Field(description="the model versions that produced it")
    assumptions: list[str] = Field(description="IDs in docs/ASSUMPTIONS.md that apply")


# --- health ------------------------------------------------------------------------------

class DatabaseHealth(Model):
    ok: bool
    latency_ms: float | None = None
    pending_migrations: list[str] = []
    error: str | None = None


class WorkerHeartbeat(Model):
    worker_id: str
    host: str
    pid: int
    status: Literal["running", "stopped"]
    started_at: dt.datetime
    last_seen: dt.datetime
    sessions_stepped: int
    last_session_id: int | None
    age_s: float
    alive: bool


class WorkerHealth(Model):
    alive: bool
    stale_after_s: float | None = None
    workers: list[WorkerHeartbeat]


class Health(Model):
    status: Literal["ok", "degraded", "down"]
    database: DatabaseHealth
    worker: WorkerHealth


# --- fleet -------------------------------------------------------------------------------

class PumpState(Model):
    state: PresentationState
    reason: str | None
    as_of: dt.datetime | None = None
    signals: dict[str, PresentationState]


class SessionBrief(Model):
    session_id: int
    source_day: dt.date
    status: SessionStatus
    cursor_at: dt.datetime | None
    speed: int
    synthetic: bool
    scenario: str | None


class RealSynthetic(Model):
    real: int
    synthetic: int


class OpenCases(Model):
    latest_session: int
    all_sessions: RealSynthetic


class DataQualityBrief(Model):
    source_day: dt.date
    status: Literal["ok", "issues", "not_audited"]
    audit_issues: int | None
    gaps: int | None
    flag_counts: dict[str, int]


class Pump(Provenance):
    asset_id: str
    days: list[dt.date]
    latest_session: SessionBrief | None
    state: PumpState
    open_cases: OpenCases
    data_quality: DataQualityBrief


class Fleet(Model):
    pumps: list[Pump]


# --- asset-day ---------------------------------------------------------------------------

class AssetDayRow(Model):
    asset_id: str
    source_day: dt.date
    start_at: dt.datetime
    end_at: dt.datetime
    running_hours: float | None


class AssetDays(Model):
    asset_days: list[AssetDayRow]


class DaySession(Model):
    session_id: int
    status: SessionStatus
    speed: int
    synthetic: bool
    scenario: str | None
    cursor_at: dt.datetime | None


class AssetDaySummary(Model):
    asset_id: str
    source_day: dt.date
    signals: list[str]
    sessions: list[DaySession]
    assumptions: list[str]


class MinutePoint(Model):
    bucket: dt.datetime
    min: float | None
    max: float | None
    mean: float | None
    samples: int
    readings: int


class RawPoint(Model):
    t: dt.datetime
    value: float | None
    state: str
    flags: list[str]
    is_reading: bool


class Signals(Model):
    asset_id: str
    source_day: dt.date
    resolution: Literal["1m", "raw"]
    signals: dict[str, list[MinutePoint] | list[RawPoint]]
    assumptions: list[str]


class SegmentRow(Model):
    state: Literal["running", "off", "transition"]
    start_at: dt.datetime
    end_at: dt.datetime
    motor_unconfirmed: bool | None
    pressurized_while_stopped: bool | None
    rules_version: str


class Segments(Model):
    asset_id: str
    source_day: dt.date
    segments: list[SegmentRow]
    assumptions: list[str]


class ScoreRow(Model):
    signal_name: str
    stretch: int
    window_start: dt.datetime
    window_end: dt.datetime
    state: PresentationState
    score: float | None
    abstention_reason: str | None
    median: float | None
    band_low: float | None
    band_high: float | None
    model_id: str
    model_version: str
    synthetic: bool


class Scores(Provenance):
    asset_id: str
    source_day: dt.date
    session_id: int
    scores: list[ScoreRow]


class Band(Model):
    center: float
    low: float
    high: float
    unit: str


class SignalProgress(Model):
    status: BaselineStatus
    fraction: float
    settled_at: dt.datetime | None
    first_reading: dt.datetime | None
    baseline_start: dt.datetime | None
    baseline_end: dt.datetime | None
    reason: str | None
    band: Band | None = None


class RunProgress(Model):
    run: int
    start: dt.datetime
    end: dt.datetime
    closed: bool
    signals: dict[str, SignalProgress]


class BaselineProgress(Model):
    cursor: dt.datetime
    runs: list[RunProgress]


class Bands(Provenance):
    asset_id: str
    source_day: dt.date
    session_id: int
    bands: BaselineProgress | None


# --- data quality ------------------------------------------------------------------------

class CadenceSegment(Model):
    start: str
    end: str
    cadence_s: float
    steps: int


class AuditFile(Model):
    file: str
    rows: int | None
    blank_rows: int | None
    issues: list[str] | None
    span: list[str] | None
    cadence_segments: list[CadenceSegment] | None
    timestamp_assumption: str | None
    duplicate_timestamps: int | None
    non_monotonic_steps: int | None


class Gaps(Model):
    factor: float
    count: int
    site_level: int
    items: list[dict[str, Any]]


class AssetDayQuality(Model):
    asset_id: str
    source_day: dt.date
    audit: AuditFile | None
    gaps: Gaps | None
    site_gaps: list[dict[str, Any]]
    flag_counts: dict[str, dict[str, int]]
    assumptions: list[str]


class QualityRow(Model):
    asset_id: str
    source_day: dt.date
    audit_issues: int | None
    gaps: int | None
    flag_counts: dict[str, int]


class DataQuality(Model):
    audit_ok: bool | None
    audit_issues: list[str] | None
    asset_days: list[QualityRow]


# --- cases -------------------------------------------------------------------------------

class CaseState(Model):
    case_id: int
    session_id: int
    synthetic: bool
    asset_id: str
    source_day: dt.date
    stretch: int
    opened_at: dt.datetime
    evidence_start: dt.datetime | None
    evidence_end: dt.datetime | None
    evidence_windows: int
    episodes: int
    signals: list[str] | None
    max_score: float | None
    notes: int
    acknowledged: bool
    disposition: str | None
    disposition_reason: str | None
    closed: bool
    status: CaseStatus
    last_event_at: dt.datetime
    related_case_id: int | None


class CaseList(Model):
    cases: list[CaseState]
    real_count: int
    synthetic_count: int
    model_version: list[str]
    assumptions: list[str]


class TimelineEvent(Model):
    event_id: int
    event_type: Literal["opened", "acknowledged", "note", "disposition", "closed"]
    actor: str
    recorded_at: dt.datetime
    note: str | None
    disposition: str | None
    reason: str | None
    related_case_id: int | None


class SignalSummary(Model):
    windows: int
    episodes: int
    first_window_start: dt.datetime
    last_window_end: dt.datetime
    max_score: float | None


class BandChart(Model):
    t: list[dt.datetime]
    median: list[float | None]
    min: list[float | None]
    max: list[float | None]
    review: list[bool]


class CaseSignal(Model):
    summary: SignalSummary
    band: Band | None
    chart: BandChart


class Related(Model):
    related_case: CaseState | None
    related_by: list[CaseState]


class CaseDetail(Provenance):
    case: CaseState
    timeline: list[TimelineEvent]
    signals: dict[str, CaseSignal]
    max_points: int
    chart_margin_s: float
    evidence_url: str
    related: Related
    actions: list[CaseAction]


class EvidenceItem(Model):
    signal_name: str
    window_start: dt.datetime
    window_end: dt.datetime
    state: PresentationState
    score: float | None
    median: float | None
    band_low: float | None
    band_high: float | None
    model_version: str
    synthetic: bool
    episode_start: bool


class EvidencePage(Provenance):
    case_id: int
    total: int
    offset: int
    limit: int
    next_offset: int | None
    items: list[EvidenceItem]


class CaseActionResult(Provenance):
    case: CaseState
    actions: list[CaseAction]


class ExportEvent(Model):
    event_id: int
    case_id: int
    event_type: Literal["opened", "evidence_added", "acknowledged", "note", "disposition",
                        "closed"]
    session_id: int
    synthetic: bool
    asset_id: str
    source_day: dt.date
    stretch: int
    actor: str
    recorded_at: dt.datetime
    signal_name: str | None
    window_start: dt.datetime | None
    window_end: dt.datetime | None
    model_version: str | None
    score: float | None
    episode_start: bool | None
    note: str | None
    disposition: str | None
    reason: str | None
    related_case_id: int | None


class ExportEvidence(Model):
    signal_name: str
    window_start: dt.datetime
    window_end: dt.datetime
    presentation_state: PresentationState
    score: float | None
    median: float | None
    band_low: float | None
    band_high: float | None
    model_id: str
    model_version: str
    synthetic: bool
    scored_evidence: ScoredEvidence


class CaseExport(Provenance):
    case: CaseState
    events: list[ExportEvent]
    evidence: list[ExportEvidence]
    note: str


# --- replay ------------------------------------------------------------------------------

class Scenario(Model):
    name: str
    asset_id: str
    signal: str
    fault: Literal["step", "ramp", "drift", "stuck", "dropout"]
    size: float
    offset_s: float


class Scenarios(Model):
    scenarios: list[Scenario]
    note: str


class DayConstants(Model):
    intervals: dict[str, float]
    signals: list[str]
    max_window_s: int
    stale_limits: dict[str, float]
    source_end: dt.datetime


class Session(Model):
    session_id: int
    asset_id: str
    source_day: dt.date
    speed: int
    status: SessionStatus
    cursor_at: dt.datetime | None
    source_start: dt.datetime
    source_end: dt.datetime
    scenario: str | None
    synthetic: bool
    config_name: str
    config_sha256: str
    anchor_cursor: dt.datetime | None
    anchor_wall: dt.datetime | None
    claimed_by: str | None
    heartbeat_at: dt.datetime | None
    error: str | None
    created_at: dt.datetime
    day_constants: DayConstants


class SessionEnvelope(Model):
    session: Session


class SessionList(Model):
    sessions: list[Session]


class Baseline(Provenance):
    session_id: int
    status: SessionStatus
    cursor_at: dt.datetime | None
    baseline_progress: BaselineProgress | None


# --- evaluation --------------------------------------------------------------------------

class ModeResult(Model):
    label: str
    file: str
    results: dict[str, Any] | None = Field(description="the stored evaluation JSON, as scored")
    note: str | None


class Evaluation(Model):
    modes: dict[str, ModeResult]
    cases_all_modes: dict[str, dict[str, dict[str, Any]]] | None
    report: str
    labels: str


class AssumptionItem(Model):
    id: str
    title: str
    assumption: str
    evidence: str
    impact_if_wrong: str
    how_to_revisit: str


class Assumptions(Model):
    assumptions: list[AssumptionItem]
    source: str


# --- the live stream (server-sent events) ------------------------------------------------

class EventBase(Model):
    session_id: int
    asset_id: str
    source_day: dt.date
    synthetic: bool
    created_at: dt.datetime


class ReplayProgressEvent(EventBase):
    status: SessionStatus
    cursor_at: dt.datetime | None
    speed: int
    scenario: str | None
    baseline: dict[str, int] = Field(description="signals per baseline status")


class ScoreBatchEvent(EventBase):
    scenario: str | None
    count: int
    window_end_first: dt.datetime
    window_end_last: dt.datetime
    states: dict[str, int]
    model_version: list[str]


class CaseEventCounts(Model):
    opened: int
    evidence_added: int


class CaseEvent(EventBase):
    case_id: int
    actor: str
    event_type: Literal["opened", "evidence_added", "acknowledged", "note", "disposition",
                        "closed"]
    events: CaseEventCounts | None = Field(None, description="worker: events in this step")
    related_case_id: int | None = None
    status: CaseStatus | None = Field(None, description="person's action: status after it")
    disposition: str | None = None


class ReplayProgressMessage(Model):
    id: int
    event: Literal["replay.progress"]
    data: ReplayProgressEvent


class ScoreBatchMessage(Model):
    id: int
    event: Literal["score.batch"]
    data: ScoreBatchEvent


class CaseEventMessage(Model):
    id: int
    event: Literal["case.event"]
    data: CaseEvent


class StreamEvent(RootModel[Annotated[ReplayProgressMessage | ScoreBatchMessage |
                                       CaseEventMessage, Field(discriminator="event")]]):
    """One server-sent event: `id:` is the event id, `event:` the type, `data:` the JSON."""
