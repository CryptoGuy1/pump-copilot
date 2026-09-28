import { useQueryClient } from "@tanstack/react-query";
import { NavLink, Route, Routes } from "react-router-dom";
import { type StreamEvent, useStream } from "./hooks/useStream";
import { AssetDay } from "./pages/AssetDay";
import { CaseDetail } from "./pages/CaseDetail";
import { Cases } from "./pages/Cases";
import { DataQuality } from "./pages/DataQuality";
import { Evaluation } from "./pages/Evaluation";
import { Fleet } from "./pages/Fleet";
import { Replay } from "./pages/Replay";

/** Live updates: each stream event marks the queries it affects as stale. */
function useLiveInvalidation() {
  const qc = useQueryClient();
  return useStream({
    onEvent: (e: StreamEvent) => {
      const inv = (key: unknown[]) => qc.invalidateQueries({ queryKey: key });
      inv(["fleet"]);
      if (e.type === "replay.progress") {
        inv(["sessions"]);
        inv(["baseline", e.data.session_id]);
      } else if (e.type === "score.batch") {
        inv(["scores"]);
      } else {
        inv(["cases"]);
        inv(["case", e.data.case_id]);
      }
    },
  });
}

export function App() {
  const live = useLiveInvalidation();
  return (
    <>
      <nav>
        <NavLink to="/">Fleet</NavLink>
        <NavLink to="/cases">Cases</NavLink>
        <NavLink to="/replay">Replay</NavLink>
        <NavLink to="/evaluation">Evaluation</NavLink>
        <NavLink to="/data-quality">Data quality</NavLink>
        <span className={`live live-${live.status}`} data-testid="live-status">
          live: {live.status}{live.lastEventId != null && ` (#${live.lastEventId})`}</span>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Fleet />} />
          <Route path="/assets/:asset/:day" element={<AssetDay />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:id" element={<CaseDetail />} />
          <Route path="/replay" element={<Replay />} />
          <Route path="/evaluation" element={<Evaluation />} />
          <Route path="/data-quality" element={<DataQuality />} />
        </Routes>
      </main>
      <footer className="muted">Read-only decision support on public CIRA data. There are no
        alarm states and no control actions; escalation is export-only.</footer>
    </>
  );
}
