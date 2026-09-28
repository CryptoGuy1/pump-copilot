// Response shapes the screens use (the OpenAPI schema types requests, not responses).
export type PresentationState =
  "normal" | "review_suggested" | "insufficient_evidence" | "data_unavailable";

export interface Provenance {
  synthetic: boolean;
  model_version: string[];
  assumptions: string[];
}

export interface SessionSummary {
  session_id: number;
  source_day: string;
  status: string;
  cursor_at: string | null;
  speed: number;
  synthetic: boolean;
  scenario: string | null;
}

export interface Pump extends Provenance {
  asset_id: string;
  days: string[];
  latest_session: SessionSummary | null;
  state: { state: PresentationState; reason: string | null; as_of?: string;
           signals: Record<string, PresentationState> };
  open_cases: { latest_session: number; all_sessions: { real: number; synthetic: number } };
  data_quality: { source_day: string; status: string; audit_issues: number | null;
                  gaps: number | null; flag_counts: Record<string, number> };
}

export interface AssetDay {
  asset_id: string;
  source_day: string;
  signals: string[];
  sessions: SessionSummary[];
  assumptions: string[];
}

export interface Segment { state: string; start_at: string; end_at: string }

export interface ScoreRow {
  signal_name: string;
  stretch: number;
  window_start: string;
  window_end: string;
  state: PresentationState;
  score: number | null;
  median: number | null;
  band_low: number | null;
  band_high: number | null;
  model_version: string;
  synthetic: boolean;
  abstention_reason: string | null;
}

export interface Band { center: number; low: number; high: number; unit: string }

export interface SignalProgress {
  status: "waiting_for_reading" | "settling" | "forming" | "formed" | "abstained";
  fraction: number;
  settled_at: string | null;
  first_reading: string | null;
  baseline_start: string | null;
  baseline_end: string | null;
  reason: string | null;
  band?: Band;
}

export interface BaselineProgress {
  cursor: string;
  runs: { run: number; start: string; end: string; closed: boolean;
          signals: Record<string, SignalProgress> }[];
}

export interface Session extends SessionSummary {
  asset_id: string;
  source_start: string;
  source_end: string;
  anchor_cursor: string | null;
  error: string | null;
}

export interface CaseState {
  case_id: number;
  session_id: number;
  synthetic: boolean;
  asset_id: string;
  source_day: string;
  stretch: number;
  status: "open" | "acknowledged" | "dispositioned" | "closed";
  evidence_start: string;
  evidence_end: string;
  evidence_windows: number;
  episodes: number;
  signals: string[];
  max_score: number | null;
  notes: number;
  disposition: string | null;
  disposition_reason: string | null;
  related_case_id: number | null;
}

export interface CaseList {
  cases: CaseState[];
  real_count: number;
  synthetic_count: number;
  model_version: string[];
  assumptions: string[];
}

export interface CaseEvent {
  event_id: number;
  event_type: string;
  actor: string;
  recorded_at: string;
  note: string | null;
  disposition: string | null;
  reason: string | null;
  related_case_id: number | null;
}

export interface CaseDetail extends Provenance {
  case: CaseState;
  timeline: CaseEvent[];
  signals: Record<string, {
    summary: { windows: number; episodes: number; first_window_start: string;
               last_window_end: string; max_score: number };
    band: Band | null;
    chart: { t: string[]; median: (number | null)[]; min: (number | null)[];
             max: (number | null)[]; review: boolean[] };
  }>;
  related: { related_case: CaseState | null; related_by: CaseState[] };
  actions: string[];
}

export interface EvidencePage extends Provenance {
  total: number;
  next_offset: number | null;
  items: ScoreRow[];
}

export interface Assumption {
  id: string;
  title: string;
  assumption: string;
  evidence: string;
  impact_if_wrong: string;
  how_to_revisit: string;
}
