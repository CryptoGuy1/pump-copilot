import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, type Schema, call, client } from "../api/client";
import { Button, Card } from "./ui";

type Response = Schema<"AssistantResponse">;
type Summary = Schema<"EvidenceSummary">;
/** What the panel shows: the checked answer, or the evidence summary. */
type Shown = Pick<Response, "label" | "answer" | "check" | "evidence" | "calibration_status"
                  | "assumptions"> & { served: "assistant" | "template"; provider?: string;
                  model?: string | null; rejected?: Response["rejected"];
                  fallback_reason?: string | null; run_id?: number };

const QUICK = ["Summarize this case", "Which signal drove it?", "What should I check next?"];

/** Where an evidence ID points on the charts. */
export interface Highlight { id: string; signal: string | null; from: string | null;
                            to: string | null }

/** Ask about the case. One assistant request per question: the evidence summary (built from
 * the evidence, no model, not logged; it does not depend on the question) is fetched ahead
 * and appears at once; the checked answer replaces it if it passes the checker. The badge
 * always says which one is shown, and the check result can be expanded. A draft note is
 * saved only by "Approve and save", through the normal notes endpoint. */
export function AssistantPanel({ caseId, actor, canNote, onHighlight }: {
  caseId: number; actor: string; canNote: boolean; onHighlight: (h: Highlight | null) => void;
}) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const summary = useQuery({ queryKey: ["evidence-summary", caseId], staleTime: 60_000,
    queryFn: () => call(client.GET("/api/cases/{case_id}/evidence-summary",
                                   { params: { path: { case_id: caseId } } })) });
  const checked = useMutation({
    mutationFn: (q: string) => call(client.POST("/api/cases/{case_id}/assistant",
      { params: { path: { case_id: caseId } }, body: { question: q, provider: "auto" } })),
    onSuccess: (r) => setDraft(r.answer.draft_note ?? null),
  });
  const ask = (q: string) => {
    if (!q.trim()) return;
    setQuestion(q); setAsked(true); setDraft(null); setSaved(false);
    checked.mutate(q);
  };
  const save = useMutation({
    mutationFn: (text: string) => call(client.POST("/api/cases/{case_id}/notes",
      { params: { path: { case_id: caseId } }, body: { actor, text } })),
    onSuccess: () => { setSaved(true); qc.invalidateQueries({ queryKey: ["case", caseId] }); },
  });
  const fromSummary = (x: Summary): Shown => ({ ...x, served: "template", provider: "template" });
  // the checked answer once it is there, else the evidence summary
  const r: Shown | undefined = !asked ? undefined
    : checked.data ?? (summary.data ? fromSummary(summary.data) : undefined);
  const working = checked.isPending;
  useEffect(() => {  // the summary's draft note until the answer arrives
    if (asked && working && draft == null && summary.data?.answer.draft_note)
      setDraft(summary.data.answer.draft_note);
  }, [asked, working, draft, summary.data]);
  const err = (checked.error ?? save.error) as ApiError | null;
  return (
    <Card title="Ask about this case" accent={false} as="section">
      <div className="assistant" data-testid="assistant">
        <textarea aria-label="question" rows={2} value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="What does the evidence show?" />
        <div className="quick" role="group" aria-label="Suggested questions">
          {QUICK.map((q) => <Button key={q} size="sm" variant="ghost" disabled={working}
                                    onClick={() => ask(q)}>{q}</Button>)}
        </div>
        <div className="btn-row"><Button variant="primary"
          disabled={!question.trim() || working} onClick={() => ask(question)}>
          {working ? "Asking…" : "Ask"}</Button></div>
        {err && <p className="error" role="alert">{err.status} {err.code}: {err.message}</p>}
        {r && <div className="answer" data-testid="assistant-answer" aria-live="polite">
          <p className="answer-head">
            <span className={`badge badge-${r.served}`} data-testid="assistant-badge">
              {r.label}</span>{" "}
            <span className="muted">{r.served === "assistant"
              ? `${r.provider}${r.model ? ` (${r.model})` : ""}` : "built from the evidence"}</span>
          </p>
          {working && <div className="working" data-testid="assistant-working">
            <span>Assistant is working…</span>
            <div className="progress" role="progressbar" aria-label="Assistant is working" />
          </div>}
          {!working && r.fallback_reason && <p className="muted" data-testid="assistant-fallback">
            {plainReason(r.fallback_reason)}
            {r.rejected && <> The {r.rejected.provider} answer was rejected.</>}</p>}
          <details className="check" data-testid="assistant-check">
            <summary>Check {r.check.passed ? "passed" : "failed"}
              {r.rejected ? `; the model's answer failed (${r.rejected.reasons.length})` : ""}</summary>
            <ul>{r.check.reasons.map((x) => <li key={x}>{x}</li>)}
              {r.rejected?.reasons.map((x) => <li key={x}>Rejected answer: {x}</li>)}
              {!r.check.reasons.length && !r.rejected && <li>No problems found.</li>}</ul>
          </details>
          <ul className="claims">{r.answer.claims.map((c, i) => (
            <li key={i} className={`claim claim-${c.kind}`}>
              <span className="kind">{c.kind}</span> {c.text}{" "}
              {c.evidence_refs.map((ref) => (
                <button key={ref} className="ref" data-testid={`ref-${ref}`}
                        onClick={() => onHighlight(target(r, ref))}>{ref}</button>))}
            </li>))}</ul>
          {(r.answer.suggested_checks ?? []).length > 0 && <>
            <p><strong>Read-only checks you could make</strong></p>
            <ul>{(r.answer.suggested_checks ?? []).map((c) => <li key={c}>{c}</li>)}</ul></>}
          {draft != null && !working && <div className="draft">
            <p><strong>Draft note</strong> (not saved until you approve it)</p>
            <textarea aria-label="draft note" rows={3} value={draft}
                      onChange={(e) => setDraft(e.target.value)} />
            <div className="btn-row"><Button disabled={!canNote || saved || !draft.trim()
                                                         || save.isPending}
                         onClick={() => save.mutate(draft)}>Approve and save</Button>
              {saved && <span data-testid="draft-saved"> saved as a note by {actor}</span>}
              {!canNote && <span className="muted"> notes cannot be added to a closed case</span>}
            </div>
          </div>}
          <p className="muted" data-testid="stored-units">Assistant text uses stored units
            (vibration in m/s; the charts show mm/s).</p>
          <p className="muted">{plainCalibration(r.calibration_status)} Assumptions{" "}
            {r.assumptions.join(", ")}.{r.run_id != null && ` Run #${r.run_id}.`}</p>
        </div>}
      </div>
    </Card>
  );
}

/** Why the evidence summary is shown, in plain words. */
function plainReason(reason: string): string {
  if (/no ANTHROPIC_API_KEY/.test(reason))
    return "The assistant is not set up here (no API key), so this is the evidence summary.";
  if (/timeout/.test(reason)) return "The assistant took too long, so this is the evidence summary.";
  if (/rejected|malformed/.test(reason))
    return "The assistant's answer did not pass the check, so this is the evidence summary.";
  if (/provider error/.test(reason))
    return "The assistant could not be reached, so this is the evidence summary.";
  return `Showing the evidence summary (${reason}).`;
}

function plainCalibration(status: string): string {
  return status === "not_applicable"
    ? "Scores are band exceedances, not calibrated probabilities."
    : `Calibration: ${status.replace(/_/g, " ")}.`;
}

/** Where an evidence ID points: a signal's chart and its evidence window, or (E1) the case. */
function target(r: Pick<Response, "evidence">, id: string): Highlight | null {
  const e = r.evidence.find((x) => x.id === id);
  return e ? { id, signal: e.signal_name, from: e.first_window, to: e.last_window } : null;
}
