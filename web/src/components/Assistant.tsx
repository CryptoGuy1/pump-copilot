import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, type Schema, call, client } from "../api/client";

type Response = Schema<"AssistantResponse">;

/** Ask about the case. The badge says which answer this is: the checked assistant answer or
 * the evidence summary it falls back to. A draft note is saved only by "Approve and save",
 * through the normal notes endpoint. */
export interface Highlight { id: string; signal: string | null; from: string | null;
                            to: string | null }

export function AssistantPanel({ caseId, actor, canNote, onHighlight }: {
  caseId: number; actor: string; canNote: boolean; onHighlight: (h: Highlight | null) => void;
}) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const ask = useMutation({
    mutationFn: () => call(client.POST("/api/cases/{case_id}/assistant",
      { params: { path: { case_id: caseId } }, body: { question, provider: "auto" } })),
    onSuccess: (r: Response) => { setDraft(r.answer.draft_note ?? null); setSaved(false); },
  });
  const save = useMutation({
    mutationFn: (text: string) => call(client.POST("/api/cases/{case_id}/notes",
      { params: { path: { case_id: caseId } }, body: { actor, text } })),
    onSuccess: () => {
      setSaved(true);
      qc.invalidateQueries({ queryKey: ["case", caseId] });
    },
  });
  const r = ask.data;
  const err = (ask.error ?? save.error) as ApiError | null;
  return (
    <fieldset className="assistant" data-testid="assistant">
      <legend>Ask about this case</legend>
      <textarea aria-label="question" rows={2} cols={70} value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="What does the evidence show?" />
      <div><button disabled={!question.trim() || ask.isPending}
                   onClick={() => ask.mutate()}>{ask.isPending ? "Asking…" : "Ask"}</button></div>
      {err && <p className="error" role="alert">{err.status} {err.code}: {err.message}</p>}
      {r && <div className="answer" data-testid="assistant-answer">
        <p>
          <span className={`badge badge-${r.served}`} data-testid="assistant-badge">
            {r.label}</span>{" "}
          <span className="muted">{r.served === "assistant"
            ? `${r.provider}${r.model ? ` (${r.model})` : ""}` : "built from the evidence"}
            {" · "}check {r.check.passed ? "passed" : "failed"}</span>
        </p>
        {r.fallback_reason && <p className="muted" data-testid="assistant-fallback">
          Showing the evidence summary: {r.fallback_reason}.
          {r.rejected && <> The {r.rejected.provider} answer was rejected:{" "}
            {r.rejected.reasons.join("; ")}</>}</p>}
        <ul>{r.answer.claims.map((c, i) => (
          <li key={i} className={`claim claim-${c.kind}`}>
            <span className="kind">{c.kind}</span> {c.text}{" "}
            {c.evidence_refs.map((ref) => (
              <button key={ref} className="ref" data-testid={`ref-${ref}`}
                      onClick={() => onHighlight(target(r, ref))}>{ref}</button>))}
          </li>))}</ul>
        {r.answer.suggested_checks.length > 0 && <>
          <p><strong>Read-only checks you could make</strong></p>
          <ul>{r.answer.suggested_checks.map((c) => <li key={c}>{c}</li>)}</ul></>}
        {draft != null && <div className="draft">
          <p><strong>Draft note</strong> (not saved until you approve it)</p>
          <textarea aria-label="draft note" rows={3} cols={70} value={draft}
                    onChange={(e) => setDraft(e.target.value)} />
          <div><button disabled={!canNote || saved || !draft.trim() || save.isPending}
                       onClick={() => save.mutate(draft)}>Approve and save</button>
            {saved && <span data-testid="draft-saved"> saved as a note by {actor}</span>}
            {!canNote && <span className="muted"> notes cannot be added to a closed case</span>}
          </div>
        </div>}
        <p className="muted">calibration: {r.calibration_status} · assumptions{" "}
          {r.assumptions.join(", ")} · run #{r.run_id}</p>
      </div>}
    </fieldset>
  );
}

/** Where an evidence ID points: a signal's chart and its evidence window, or (E1) the case. */
function target(r: Response, id: string): Highlight | null {
  const e = r.evidence.find((x) => x.id === id);
  return e ? { id, signal: e.signal_name, from: e.first_window, to: e.last_window } : null;
}
