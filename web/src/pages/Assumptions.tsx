import { useEffect } from "react";
import { usePageTitle } from "../hooks/usePageTitle";
import { useLocation } from "react-router-dom";
import { Loading, useAssumptions } from "../components/ui";

/** The assumptions register (docs/ASSUMPTIONS.md, served by the API): what each assumption
 * says, its evidence, the impact if it is wrong and how to revisit it. An assumption chip
 * anywhere in the app opens its entry here. */
export function Assumptions() {
  usePageTitle("Assumptions register");
  const q = useAssumptions();
  const { hash } = useLocation();
  const open = decodeURIComponent(hash.replace(/^#/, ""));
  useEffect(() => {
    if (open && q.data) document.getElementById(open)?.scrollIntoView?.({ block: "start" });
  }, [open, q.data]);
  return (
    <section>
      <p className="eyebrow">docs/ASSUMPTIONS.md</p>
      <h1>Assumptions register</h1>
      <p className="muted">Every score, case and chart names the assumptions it rests on.</p>
      <Loading q={q} lines={8}>
        <div className="register">
          {q.data?.assumptions.map((a) => (
            <details key={a.id} id={a.id} open={a.id === open}>
              <summary><span className="chip">{a.id}</span> {a.title}</summary>
              <dl>
                <div><dt>Assumption</dt><dd>{a.assumption}</dd></div>
                {a.evidence && <div><dt>Evidence</dt><dd>{a.evidence}</dd></div>}
                {a.impact_if_wrong && <div><dt>Impact if wrong</dt><dd>{a.impact_if_wrong}</dd>
                </div>}
                {a.how_to_revisit && <div><dt>How to revisit</dt><dd>{a.how_to_revisit}</dd>
                </div>}
              </dl>
            </details>))}
        </div>
      </Loading>
    </section>
  );
}
