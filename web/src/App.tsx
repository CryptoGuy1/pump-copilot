import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { type StreamEvent, useStream } from "./hooks/useStream";
import { PageProvenanceBar, PageProvenanceProvider } from "./pageProvenance";
import { AssetDay } from "./pages/AssetDay";
import { Assumptions } from "./pages/Assumptions";
import { CaseDetail } from "./pages/CaseDetail";
import { Cases } from "./pages/Cases";
import { DataQuality } from "./pages/DataQuality";
import { Design } from "./pages/Design";
import { Evaluation } from "./pages/Evaluation";
import { Fleet } from "./pages/Fleet";
import { Replay } from "./pages/Replay";
import { THEMES, type Theme, ThemeContext, applyTheme, readTheme } from "./theme";

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

const PAGES = [["/", "Fleet"], ["/cases", "Cases"], ["/replay", "Replay"],
               ["/evaluation", "Evaluation"], ["/data-quality", "Data quality"],
               ["/assumptions", "Assumptions"]] as const;

/** The design direction: ?theme= or the one chosen last in this browser. */
function ThemeSwitcher({ theme }: { theme: Theme }) {
  const loc = useLocation();
  return (
    <div className="theme-switch" role="group" aria-label="Theme">
      {THEMES.map((t) => (
        <Link key={t} to={`${loc.pathname}?theme=${t}`} aria-current={t === theme || undefined}
              className={t === theme ? "on" : undefined}>{t}</Link>
      ))}
    </div>
  );
}

export function App() {
  const live = useLiveInvalidation();
  const loc = useLocation();
  const theme = readTheme(loc.search);
  useLayoutEffect(() => applyTheme(theme), [theme]);
  const [menu, setMenu] = useState(false);
  useLayoutEffect(() => setMenu(false), [loc.pathname]);
  return (
    <ThemeContext.Provider value={theme}>
      <div className="backdrop" aria-hidden="true" />
      <a className="skip-link" href="#main">Skip to content</a>
      <PageProvenanceProvider>{(p) => <>
        <header>
          <nav className="shell-nav" aria-label="Main">
            <Link to="/" className="brand" aria-label="pump-copilot, fleet overview">
              <span className="brand-mark" aria-hidden="true" />pump-copilot</Link>
            <button type="button" className="menu-button" aria-expanded={menu}
                    aria-controls="nav-collapsible" onClick={() => setMenu((m) => !m)}>
              {menu ? "Close menu" : "Menu"}</button>
            <div id="nav-collapsible" className={`nav-collapsible${menu ? " open" : ""}`}>
              <ul className="nav-links">
                {PAGES.map(([to, label]) => (
                  <li key={to}><NavLink to={to} end={to === "/"}>{label}</NavLink></li>))}
              </ul>
              <div className="nav-tools">
                <span className={`live live-${live.status}`} data-testid="live-status">
                  live: {live.status}{live.lastEventId != null && ` (#${live.lastEventId})`}
                </span>
                <ThemeSwitcher theme={theme} />
              </div>
            </div>
          </nav>
          <PageProvenanceBar p={p} />
        </header>
        <main id="main" className="shell-main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<Fleet />} />
            <Route path="/assets/:asset/:day" element={<AssetDay />} />
            <Route path="/cases" element={<Cases />} />
            <Route path="/cases/:id" element={<CaseDetail />} />
            <Route path="/replay" element={<Replay />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="/data-quality" element={<DataQuality />} />
            <Route path="/assumptions" element={<Assumptions />} />
            <Route path="/design" element={<Design />} />
          </Routes>
        </main>
      </>}</PageProvenanceProvider>
      <footer className="shell-footer">
        <p>Read-only decision support on public CIRA data. There are no alarm states and no
          control actions; escalation is export-only.</p>
        <p><Link to="/design">Design system</Link></p>
      </footer>
    </ThemeContext.Provider>
  );
}
