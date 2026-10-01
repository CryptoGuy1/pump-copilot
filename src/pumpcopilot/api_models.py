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

from .assistant import AssistantAnswer
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
    worker_id: str = Field(description="a short random id (worker-3f9a), no host name")
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


class DataQualityBrief(Model):
    source_day: dt.date
    status: Literal["ok", "issues", "not_audited"]
    audit_issues: int | None
    gaps: int | None
    flag_counts: dict[str, int]


class FleetSession(Provenance):
    """One replay session, as of its cursor."""
    session: SessionBrief
    state: PumpState = Field(description="computed on the server from the latest window of each"
                                          " signal at or before the cursor; as_of is the cursor")
    open_cases: int = Field(description="cases reached by the cursor and not closed")


class Pump(Provenance):
    asset_id: str
    days: list[dt.date]
    state: PumpState = Field(description="the pump's state: its latest real session's (a "
                                         "synthetic session never stands for the pump)")
    state_session_id: int | None = Field(description="the session the state is from")
    sessions: list[FleetSession] = Field(description="every session, synthetic ones included,"
                                                     " newest first")
    open_cases: RealSynthetic = Field(description="open cases over all sessions, each as of "
                                                  "its cursor")
    data_quality: DataQualityBrief


class Fleet(Model):
    pumps: list[Pump]


# --- asset-day ---------------------------------------------------------------------------

class SignalName(Model):
    display_name: str = Field(description="the name people see")
    short_name: str = Field(description="for tight spaces")
    unit: str


class SignalNames(Model):
    signals: dict[str, SignalName] = Field(description="by signal id; derived signals scored "
                                                       "relative to ambient included")


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


class KnownIssue(Model):
    title: str
    detail: str
    assumption: str = Field(description="the assumption (docs/ASSUMPTIONS.md) that handles it")


class DataQuality(Model):
    audit_ok: bool | None
    audit_issues: list[str] | None
    asset_days: list[QualityRow]
    known_issues: list[KnownIssue] = Field(
        [], description="known data issues, each tied to its assumption")


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
    as_of: dt.datetime | None = Field(description="the session cursor (source time) the case "
                                                  "is shown as of")


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
    as_of: dt.datetime | None = Field(description="the session cursor the export is as of")
    run_index: int = Field(description="the run within the day, from 0 (case.stretch)")
    run_number: int = Field(description="the run within the day as people count it, from 1")
    replay_status: SessionStatus
    complete: bool = Field(description="false while the replay has not finished: later "
                                       "evidence is not in this export")
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


class CalibrationGrade(Model):
    brier: float = Field(description="multi-class Brier score on the test part")
    ece: float = Field(description="top-label expected calibration error on the test part")
    grade: Literal["good", "fair", "poor", "very poor"]
    text: str = Field(description="the measurement in plain language")


class ZemaScore(Model):
    split: str
    model: str
    calibration: CalibrationGrade | None = Field(
        description="measured on the test part of this split, for this model")
    evidence: ScoredEvidence = Field(description="cycle_id is set and time_is_placeholder is "
                                                 "true: ZeMA cycles have no clock")


class ZemaBenchmark(Model):
    scope_note: str = Field(description="hydraulic test rig only; does not transfer")
    output_label: str
    status: Literal["not_tuned", "pre-registered, not yet evaluated", "evaluated"]
    preregistration_tag: str
    config: dict[str, Any] | None = Field(description="the frozen, pre-registered config")
    results: dict[str, Any] | None = Field(description="the test results, once evaluated")
    scores: list[ZemaScore] = Field(description="headline-model scores for the first test "
                                                "cycles of each split")
    report: str


class Evaluation(Model):
    modes: dict[str, ModeResult]
    cases_all_modes: dict[str, dict[str, dict[str, Any]]] | None
    report: str
    labels: str
    zema: ZemaBenchmark


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
    """Events are signals, not data: which session (or case) changed, so the page refetches.
    They carry no scores, states or case details."""
    session_id: int
    asset_id: str
    source_day: dt.date
    synthetic: bool
    created_at: dt.datetime = Field(description="when the event was written (wall time)")


class ReplayProgressEvent(EventBase):
    pass


class ScoreBatchEvent(EventBase):
    pass


class CaseEvent(EventBase):
    case_id: int
    event_type: Literal["opened", "evidence_added", "acknowledged", "note", "disposition",
                        "closed"]


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


# --- the copilot assistant ---------------------------------------------------------------

class AssistantQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    provider: Literal["auto", "template"] = Field(
        "auto", description="auto: the configured model, falling back to the evidence "
                            "summary; template: the evidence summary only")


class CheckOut(Model):
    passed: bool
    reasons: list[str]


class Rejected(Model):
    provider: str
    model: str | None
    reasons: list[str] = Field(description="why the checker rejected the provider's answer")


class EvidenceRef(Model):
    id: str
    kind: Literal["case", "signal"]
    signal_name: str | None
    first_window: dt.datetime | None
    last_window: dt.datetime | None
    values: dict[str, float]
    unit: str | None = None
    times: list[str]


class EvidenceSummary(Provenance):
    """The evidence summary of a case: no model, not logged."""
    case_id: int
    label: Literal["Evidence summary"]
    answer: AssistantAnswer
    check: CheckOut
    evidence: list[EvidenceRef]
    calibration_status: str
    context_hash: str


class AssistantResponse(Provenance):
    case_id: int
    run_id: int
    served: Literal["assistant", "template"]
    label: Literal["Assistant, checked", "Evidence summary"]
    provider: str
    model: str | None
    answer: AssistantAnswer
    check: CheckOut = Field(description="the check of the answer that is shown")
    rejected: Rejected | None = Field(description="the provider's answer, if it was rejected")
    fallback_reason: str | None
    evidence: list[EvidenceRef]
    calibration_status: str
    context_hash: str
    latency_ms: float


# --- evaluation chapters, about, known issues (Step 5b stage 3) ------------------------------

class Prereg(Model):
    tag: str | None
    commit: str | None


class F1Interval(Model):
    model: str
    macro_f1: float
    lo: float | None
    hi: float | None
    n_blocks: int | None


class ZemaSplit(Model):
    split: str
    headline: F1Interval
    majority: F1Interval


class Confusion(Model):
    split: str
    model: str
    labels: list[str]
    matrix: list[list[int]] = Field(description="rows: true leakage state; columns: predicted")
    n: int


class Shortcut(Model):
    table: dict[str, dict[str, int]] | None = Field(description="stable flag -> leakage -> cycles")
    axes: str | None
    n_cycles: int | None
    mutual_information_bits: float | None
    share_of_leakage_entropy: float | None


class ZemaChapter(Model):
    how_to_read: str
    contrast: str | None = Field(description="the headline model's random against "
                                             "chronological macro-F1, in one line")
    scope_note: str
    splits: list[ZemaSplit]
    confusion: Confusion
    shortcut: Shortcut
    calibration: CalibrationGrade | None
    report: str
    preregistration: Prereg


class ModePump(Model):
    pump: str
    abstained: str | None
    cases: int | None
    cases_per_running_hour: float | None
    case_time_fraction: float | None


class CiraMode(Model):
    mode: str = Field(description="the internal label")
    name: str = Field(description="the plain name")
    pumps: list[ModePump]
    exploratory: list[ModePump] = Field(description="runs outside the protocol")


class DetectionCell(Model):
    fault: str
    size: float
    size_label: str
    injections: int
    detected: int
    detection_rate: float
    median_delay_s: float | None
    size_class: Literal["small", "medium", "large"] | None


class SyntheticDetection(Model):
    day: str
    cells: list[DetectionCell]
    starts_per_fault: int


class CiraChapter(Model):
    how_to_read: str
    modes: list[CiraMode]
    synthetic: SyntheticDetection
    key_finding: str | None
    exploratory_note: str
    report: str
    preregistration: Prereg


class QuestionSet(Model):
    set: str
    total: int
    served_checked: int


class AdversarialRates(Model):
    total: int
    raw_passed: int
    final_passed: int
    final_passed_corrected: int | None = None


class AssistantRevision(Model):
    revision: str
    checker_version: int
    questions: QuestionSet
    holdout_commit: str | None
    adversarial: AdversarialRates


class FlaggedAnswer(Model):
    run: str
    set: str
    id: str
    where: Literal["claim", "check", "note"]
    text: str
    rule_hit: str
    verdict: str | None = Field(description="a person's reading; unreviewed flags count")
    why: str | None
    reviewed_by: str | None


class AssistantChapter(Model):
    how_to_read: str
    revisions: list[AssistantRevision]
    instructions_served: int
    served_answers: int
    flagged: list[FlaggedAnswer]
    instructions_rule: str
    report: str


class EvaluationChapters(Model):
    zema: ZemaChapter | None
    cira: CiraChapter | None
    assistant: AssistantChapter | None


class Source(Model):
    id: str
    title: str
    authors: list[str] = Field(description="the dataset's creators, as its record lists them")
    citation: str = Field(description="the dataset, then its data descriptor where there is one")
    landing_page: str
    license: str
    license_url: str | None
    changes: str | None = Field(description="what this project changed (CC BY 4.0 asks for it)")
    role: str


class StackItem(Model):
    layer: str
    what: str


class ProfileLink(Model):
    label: str
    url: str


class Author(Model):
    name: str
    role: str | None
    links: list[ProfileLink]


class About(Model):
    sources: list[Source]
    repository: str | None = Field(description="null while the repository is private")
    author: Author
    stack: list[StackItem]
    ai_assistance: str
